"""How the generation CLI is invoked.

No network and no subprocess: these assert the shape of argv, which is where
the damage happens. A backend that is merely misconfigured still exits 0 and
still writes a generation record, so the failure looks like a bad agent rather
than a bad flag -- the class of bug scripts/launchd/README.md warns about.
"""
from __future__ import annotations

import pytest

from avo.core.backends import _PARKING_TOOLS, claude_backend


@pytest.fixture
def argv(monkeypatch):
    monkeypatch.setattr("avo.core.backends.resolve_cli", lambda _: "/fake/claude")
    monkeypatch.setattr("avo.core.backends._version", lambda _: "test")
    return claude_backend


@pytest.mark.parametrize("model", ["sonnet", None])
def test_the_prompt_cannot_be_eaten_by_the_disallowed_tools_list(argv, model):
    """--disallowedTools is variadic and SubprocessBackend appends the prompt as
    the final positional argument. With the flag last, the CLI parses the
    prompt as a list of tool names and then exits complaining it was given no
    input -- verified against the real CLI on 2026-09-22:

        Permission deny rule "Reply" matches no known tool -- check for typos.
        Error: Input must be provided either through stdin or as a prompt

    A comma-joined value does not save it; the variadic still takes the next
    positional. So something flag-shaped must follow the tool list.
    """
    a = list(argv(model).argv)
    i = a.index("--disallowedTools")
    after = a[i + 1 + len(_PARKING_TOOLS):]
    assert after, "nothing follows the tool list; the prompt would be consumed"
    assert after[0].startswith("-"), (
        f"the tool list must be terminated by a flag, not {after[0]!r}")


@pytest.mark.parametrize("model", ["sonnet", None])
def test_the_parking_tools_are_denied(argv, model):
    """A generation is one invocation with a hard timeout and no later turn, so
    a tool that defers work to one is a way to finish having written nothing.
    Three of four slots on 2026-09-20 and 2026-09-22 died exactly there."""
    a = argv(model).argv
    assert "--disallowedTools" in a
    for t in ("ScheduleWakeup", "Monitor", "Agent"):
        assert t in a, t


def test_the_model_is_still_passed_through(argv):
    """INVARIANT #7: one backend per run, recorded in RunState.backend. The
    flag reordering must not drop it -- an unpinned run falls back to the CLI
    default, which is a user setting, and exhausted default-model credits
    killed a whole generation silently on 2026-09-11."""
    a = list(argv("sonnet").argv)
    assert a[a.index("--model") + 1] == "sonnet"
    assert "sonnet" in argv("sonnet").name


def test_acceptedits_survives_the_reorder(argv):
    """There is no human at the keyboard during a generation."""
    a = list(argv("sonnet").argv)
    assert a[a.index("--permission-mode") + 1] == "acceptEdits"
    assert "-p" in a

"""INVARIANT #3: core/ must never import from experiments/.

This test is the enforcement mechanism. If it fails, the adapter boundary is
wrong — widen the protocol in core/interfaces.py rather than deleting this test.
"""
import ast
import pathlib

CORE = pathlib.Path(__file__).resolve().parents[1] / "src" / "avo" / "core"


def _imports(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_core_does_not_import_experiments():
    offenders = [
        (p.name, mod)
        for p in CORE.rglob("*.py")
        for mod in _imports(p)
        if mod.startswith("experiments")
    ]
    assert not offenders, f"core imports experiments: {offenders}"


def test_core_has_no_experiment_name_branches():
    for p in CORE.rglob("*.py"):
        text = p.read_text()
        for slug in ("kalshi_quant", "kalshi_research"):
            assert f'== "{slug}"' not in text, f"{p.name} branches on experiment name"

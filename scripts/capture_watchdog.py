"""Alarm when capture has gone quiet. Read-only except for one state file.

The parse alarm in capture.py only fires when a pass RUNS and fails. It cannot
fire when a pass never finishes (2026-10-21: a hung full pass, no full snapshot
for 13h, every job still "exit 0"). This runs on its own schedule and checks
the files, which is the only proof capture works.

    uv run python scripts/capture_watchdog.py

Prints `!!! ALARM`, raises a desktop notification (at most hourly) and exits 1.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "data" / "kalshi_quant" / "snapshots"
STATE = ROOT / "data" / ".capture_watchdog_notified"

# kind -> (max age in s, min plausible size in bytes). Healthy: full ~2.5-2.9 MB
# hourly, near ~45-85 KB every 15 min. The parse failure of 2026-10-01 wrote
# 7.8 KB files, hence the size floors.
LIMITS = {"full": (90 * 60, 1_000_000), "near": (30 * 60, 20_000)}
RENOTIFY_SECONDS = 60 * 60


def newest(kind: str) -> Path | None:
    files = [f for f in SNAPSHOTS.glob("date=*/*.parquet") if f.stem.endswith("-near") == (kind == "near")]
    return max(files, key=lambda f: f.stat().st_mtime, default=None)


def problems(now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    out: list[str] = []
    for kind, (max_age, min_size) in LIMITS.items():
        f = newest(kind)
        if f is None:
            out.append(f"no {kind} snapshot exists")
            continue
        age = now - f.stat().st_mtime
        if age > max_age:
            out.append(f"newest {kind} snapshot is {age / 60:.0f} min old (limit {max_age // 60})")
        elif f.stat().st_size < min_size:
            out.append(f"newest {kind} snapshot is only {f.stat().st_size} bytes (floor {min_size})")
    return out


def main() -> int:
    found = problems()
    if not found:
        return 0
    msg = "; ".join(found)
    print(f"!!! ALARM capture quiet: {msg}")
    last = STATE.stat().st_mtime if STATE.exists() else 0.0
    if time.time() - last > RENOTIFY_SECONDS:
        sys.path.insert(0, str(ROOT))
        from experiments.kalshi_quant.capture import _notify_desktop
        _notify_desktop(msg)
        STATE.write_text(msg)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

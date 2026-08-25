# Scheduling on macOS

**cron does not work here. Use these.**

A syntactically correct crontab line was installed on 2026-08-24 (macOS 26.5)
and never executed once: nothing in `data/capture.log`, no lock file, no
process, no error anywhere. `/usr/sbin/cron` is subject to TCC and needs Full
Disk Access to touch a home directory, and its failure mail is discarded when no
MTA is configured — so the job fails **completely silently**. `crontab -l`
showing the right line proves nothing.

A LaunchAgent runs in the user session and needs no such grant.
`../cron.example` is kept for portability to non-macOS hosts.

## Install

Paths inside the plists are absolute — edit them (`WorkingDirectory`, the `uv`
path, and both log paths) before loading. Find `uv` with `command -v uv`.

    cp com.avoforecast.kalshi-*.plist ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.avoforecast.kalshi-snapshot.plist
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.avoforecast.kalshi-settle.plist

## Verify

Never infer success from the plist being loaded — check that data lands.

    launchctl list | grep avoforecast        # 2nd column is last exit status
    ls data/kalshi_quant/snapshots/date=*/   # a new file every 15 min
    tail -f data/capture.log

## Reload after editing

    launchctl bootout gui/$(id -u)/com.avoforecast.kalshi-snapshot
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.avoforecast.kalshi-snapshot.plist

Do this only when no sweep is in flight (`pgrep -f "capture.*snapshot"`);
`bootout` kills a running job, wasting the ~7 minutes of API calls it has spent.

## Why these schedules

`StartCalendarInterval`, not `StartInterval`. The latter is a relative timer and
macOS coalesces it: measured gaps were 21m28s against a nominal 900s, i.e. ~68
snapshots/day instead of 96.

- **snapshot** — `:00 :15 :30 :45`. A sweep takes ~6.5-9.5 min (~3.16 pages/s
  against a 1.2-1.3M market universe), so it uses under half its slot.
  `capture.py` takes a lock, so an overrun skips the next tick rather than
  doubling the request rate into Kalshi's limiter.
- **settle** — `:12`, inside the gap between sweeps. Cheap (145 requests / 35s
  on a first run, 40 / 10s once caught up) and idempotent, so a missed run
  costs nothing.

Snapshots are the only calendar-bound part of the system: top-of-book at a given
instant is gone forever if nothing captured it. Settlements stay queryable and
are backfillable.

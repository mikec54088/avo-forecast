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

- **snapshot** — `:00 :20 :40`. Was every 15 min until 2026-08-25, when the
  open universe grew 1.32M -> 2.08M markets in a day (essentially all MVE
  parlay combos) and sweep time went 412s -> 925s, past the 900s slot. launchd
  will not start a second copy while one runs, so an overrun silently becomes a
  dropped tick; one 30-minute gap was observed before this change.

  **Watch this.** Sweep time tracks universe size almost linearly, and the
  universe is volatile. 20 min plus `MIN_REQUEST_INTERVAL` at 0.15 holds to
  roughly 3M markets. Past that, bound the sweep by close time — `max_close_ts`
  does work on open markets (a +24h window measured 9 pages / 5s against 2,082
  pages / 925s, and is only 3.5% MVE), at the cost of long-horizon price
  history. There is no MVE filter; every plausible parameter is silently
  ignored, and filtering after the fetch saves nothing because the cost is
  walking pages.

      grep -oE 'in [0-9]+s' data/capture.log | tail -20   # duration trend
- **settle** — `:12`, inside the gap between sweeps. Cheap (145 requests / 35s
  on a first run, 40 / 10s once caught up) and idempotent, so a missed run
  costs nothing.

Snapshots are the only calendar-bound part of the system: top-of-book at a given
instant is gone forever if nothing captured it. Settlements stay queryable and
are backfillable.

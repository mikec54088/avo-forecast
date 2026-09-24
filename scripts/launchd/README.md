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

A launchd job also gets `PATH=/usr/bin:/bin:/usr/sbin:/sbin` and nothing else,
so anything the job shells out to must be found by absolute path or via an
`EnvironmentVariables` PATH. The research runner needs both — see its plist.
This failure is silent in the usual way: the job runs, exits 0, and simply
never does the work.

    cp com.avoforecast.kalshi-*.plist ~/Library/LaunchAgents/
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.avoforecast.kalshi-snapshot.plist
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.avoforecast.kalshi-settle.plist
    # kalshi-research is INTENTIONALLY PAUSED as of 2026-09-22. Do not bootstrap
    # its old Sonnet runner; see docs/PLAN-2026-09-22-RESEARCH-REVAMP.md.
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.avoforecast.kalshi-evolve.plist

## Verify

Never infer success from the plist being loaded — check that data lands.

    launchctl list | grep avoforecast        # 2nd column is last exit status
    ls data/kalshi_quant/snapshots/date=*/   # a new file every 15 min
    ls data/kalshi_research/forecasts/date=*/  # research runner: a file per pass that forecast anything
    tail data/research.log
    tail data/evolve.log
    tail -f data/capture.log

## The evolve agent

Daily at 03:35. Unlike the capture jobs this one is allowed to do nothing, and
usually will: `pool_changed()` requires a new candidate or a moved verdict
before it spends a generation, so a run that prints

    generation N skipped: no candidate is new and no verdict has moved

is the job working, not failing. It exits 0 either way. Scheduling only became
safe once that gate existed -- before it, a nightly run would have spent a full
agentic session re-deriving identical parents, which is what generations 3 and
4 did by hand.

Two things about this job are load-bearing and easy to get wrong:

- **`--run-id` must stay pinned.** Unpinned, every night creates a fresh run
  directory, `pool_changed()` finds no prior generation and reports "first
  generation", and the gate never fires. The job would run, exit 0, and
  generate unconditionally. If the run is restarted, edit the plist; `ls runs/`
  is the check.
- **It takes a lock** (`runs/.evolve.lock`, stale after 6h). A nightly run
  firing while you have one going by hand would breed twice from one quota
  window and overwrite the run record. The second one exits with a message
  rather than colliding.

A skipped run still costs ~25 min of local CPU, because the pool cannot be
compared without recomputing the scores. It costs no API quota, which is the
resource that actually runs out.

## The entries table is on the critical path

Capture writes snapshots and resolutions; **nothing scores until
`avo entries build` folds them into the entries table**, and until 2026-09-22
that was a manual command with no schedule behind it.

It had last run on 2026-09-20T01:49Z. Three days later the cost was:

- every candidate written after that timestamp scored **zero** observations and
  always would -- INVARIANT #1 admits only ground truth resolving after
  `created_at`, and none had been ingested. `unclimbed_tight`, `unclimbed_far`
  and `control_news_fade_band` were all in that state;
- `gen006`, `gen007` and `gen008` each read exactly **157,108** observations
  with identical fill counts, so three "no candidate is new and no verdict has
  moved" skips were arithmetic, not evidence about the market. The rebuild
  added 24,870 entries in one pass.

Two things now stop it recurring. `kalshi-entries` runs it at 02:00 daily, and
`scoring_inputs()` REFUSES a table more than a day behind the newest
resolutions partition (`experiment.MAX_ENTRIES_STALENESS_DAYS`) rather than
returning a confident ranking of frozen numbers. One day of tolerance because
the newest partition is still being written during the day; two means a
rebuild was missed.

02:00 is 95 minutes ahead of evolve at 03:35 -- deliberately, because a build
still running at 03:35 would now take the nightly generation down with it.

Verify it the way everything here is verified -- that data lands, not that a
job is loaded:

    uv run python -m avo.cli entries status
    ls data/kalshi_quant/entries/entries/ | tail -3    # should show today

## The research agent is paused

`com.avoforecast.kalshi-research-run` was booted out on 2026-09-22 while idle.
Do not reload the old plist. The replacement must pass the shadow and canary
gates in `docs/PLAN-2026-09-22-RESEARCH-REVAMP.md`, use a new candidate/backend
identity, and receive explicit human approval before scheduled research resumes.
Capture and settlement remain active during this pause.

## Reload after editing

    launchctl bootout gui/$(id -u)/com.avoforecast.kalshi-snapshot
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.avoforecast.kalshi-snapshot.plist

Do this only when no sweep is in flight (`pgrep -f "capture.*snapshot"`);
`bootout` kills a running job, wasting the ~7 minutes of API calls it has spent.

## Why these schedules

`StartCalendarInterval`, not `StartInterval`. The latter is a relative timer and
macOS coalesces it: measured gaps were 21m28s against a nominal 900s, i.e. ~68
snapshots/day instead of 96.

| agent | when | cost |
|---|---|---|
| `kalshi-snapshot-near` | `:00 :15 :30 :45` | 9 pages, ~3s |
| `kalshi-snapshot` | `:07` hourly | ~2,100 pages, ~940s |
| `kalshi-settle` | `:52` hourly | ~90 requests, ~40s |
| `kalshi-entries` | `02:00` daily | appends the day's resolutions, ~2 min |

**The near pass carries the scoring load.** It sweeps only markets closing
within 24h (`--max-close-hours 24`). Every market passes through that window
before it closes, so this alone captures an entry price for everything that
becomes scoreable — at finer resolution than the full sweep ever managed, for
0.3% of the requests. It is also 0% MVE, because the cross-category parlays
reference events days out.

**The full pass is for long-horizon history and as a backstop.** It was every
20 min until 2026-08-25, when the universe hit 2.12M markets and a sweep took
941s against a 1200s slot. Hourly moves it from 78% of its slot to ~26% and
raises the ceiling from 2.7M to 8.1M markets. Daily request volume drops from
~152k pages to ~52k.

**Pacing is not a lever.** Page cost is `max(pace, latency)` and latency is
~0.444s, so the sweep is latency-bound at ~2.25 req/s regardless of
`MIN_REQUEST_INTERVAL` — two sweeps at 0.22 and 0.15 came out at 0.44428 and
0.44429 s/page. The only lever is fewer pages. Measured safety: 3.5 req/s over
69 min drew zero 429s; 8 req/s drew 1,964 in 13 minutes.

**There is no MVE filter.** `exclude_mve`, `mve`, `is_mve`,
`exclude_multivariate`, `multivariate`, `market_type` are all silently ignored
— they return identical MVE-heavy results rather than an error. `max_close_ts`
*does* work on open markets but is silently ignored on `status=settled`. An
ignored parameter is indistinguishable from a working one without checking the
response, so never trust a filter you have not verified against output.

Because of that, the near pass aborts past `BOUNDED_MAX_PAGES` (200) and says
so loudly: if `max_close_ts` ever stops working, a bounded run would silently
become a full sweep every 15 minutes.

### Watch these

    grep -oE '[0-9]+ rate-limited' data/capture.log | tail -20   # should be 0
    grep -c 'may be being ignored' data/capture.log              # should be 0
    grep -oE 'in [0-9]+s' data/capture.log | tail -20            # duration trend

Snapshots are the only calendar-bound part of the system: top-of-book at a given
instant is gone forever if nothing captured it. Settlements stay queryable and
are backfillable.

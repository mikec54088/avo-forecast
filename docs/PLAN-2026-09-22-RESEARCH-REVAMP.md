# Plan of record — research-track retrieval/local-inference revamp

Decided by the human on 2026-09-22. This supersedes the operational parts of
Track B in `PLAN-2026-09-09.md`; that file remains the history of how the first
research arm was built and what it learned.

## Current state: intentionally paused

`kalshi_research` is intentionally paused pending this implementation.

- Paused: **2026-09-22 08:37 America/Los_Angeles**.
- `com.avoforecast.kalshi-research-run` was booted out while it was idle. Its
  last completed pass landed at 2026-09-22 07:51 PDT.
- `experiments/kalshi_research/experiment.toml` records `status = "paused"`.
- Existing forecast and evidence history is immutable. Do not delete, rewrite,
  backdate, or merge it with forecasts from the replacement researcher.
- Snapshot capture, settlement, paper trading, and `kalshi_quant` evolution are
  NOT paused. They continue to collect the data the replacement arm will need.
- Do not reload the research LaunchAgent until the resume gate below is met and
  the human explicitly authorizes resumption.

## Amendments 2026-09-24 (review; approved by the human)

The architecture below is sound and stands. The review found that it fixes
COST and AUDITABILITY, while the thing that actually stalled the track is
RATE: 512 research calls in 11 days yielded ~20 opinions and 13 scoreable
decisions, ~5 months to `PNL_GATE_MIN_FILLS = 200`. A perfect local pipeline
driving the same fixed -0.04 one-direction fade inherits that rate. Five
changes, in order:

**A1. Read the no-research control first.** `control_news_fade_band` makes the
same fade with no research, ~32 fills/day, verdict in about a week from
2026-09-24. If the fade alone loses, research must add real edge to be worth
building; if it wins, research may not be needed at all. Its verdict sets how
much to invest below.

**A2. R0.5 -- redesign for rate before building retrieval.** The "do not
optimize the fade" non-goal is lifted to this extent: before R1, write down a
candidate design that could plausibly produce >= 10 scoreable decisions/day
(both directions, magnitude from evidence strength, a wider qualifying set),
because what it needs determines which markets and queries retrieval must
serve. Design on paper only; any new candidate still gets a fresh created_at.

**A3. Pilot before the build: the answer-key replay (P0/P1 below).** The old
arm left 703 stored Sonnet research outputs (`research_text` in
`data/kalshi_research/forecasts/`), 41 of them positive verdicts on 41 distinct
events, 2026-09-10..22. That is a free labelled set. Validate the local path
against it in days, before building R1-R4 in full.

**A4. Measure retrieval, not just interpretation.** R3 as written feeds both
models the SAME bundle, so it cannot see news that fixed query templates never
retrieve -- both models would agree on NONE. Add retrieval recall: for the
positives Sonnet found with open search, does the deterministic retrieval
surface the same report?

**A5. Stop R3 on positives, not markets.** At ~4-8% positives, 200 markets
gives ~8-16 positives, too few to measure 90% recall. R3 ends when >= 30
adjudicated positives exist, whatever the market count.

Also noted: the host's Ollama currently has `qwen3-vl:4b` installed, not the
`qwen3.5:9b` target.

### P0 -- reading test (no search, hours)

Give the local model each stored Sonnet EVIDENCE paragraph plus the market
title and ask for the schema output. Tests side mapping, negation and date
discipline only -- the known failure modes -- in isolation from retrieval.
Cheap, but circular for recall: Sonnet already chose the evidence.

### P1 -- answer-key replay (the real pilot, ~1-2 days)

Sample: all 41 positives + 60 random NONEs from the stored log. For each, run
the deterministic query templates with search restricted to sources published
before that row's `forecast_at`, fetch, and ask the local model. Nothing is
logged as a forecast and nothing is scored -- these markets resolved, so this
measures agreement, never P&L. Report:

- retrieval recall: positives where retrieval surfaced Sonnet's report;
- verdict agreement on positives and on NONEs, with intervals;
- every disagreement hand-adjudicated;
- latency and parse-failure rate.

Leakage caveat: post-game pages can survive the date filter. Tolerable here
because the question is "did it find the pre-game news", not "did it predict
the result", but any page describing the outcome is excluded and counted.

Needs: `qwen3.5:9b` pulled locally. Search provider decided for the pilot
(2026-09-24): **Google News RSS**, free, no key, `after:`/`before:` operators.
Tested on the Celta Vigo row: a Spanish query ("bajas") returned the exact El
Desmarque report Sonnet cited; the English query ("injury") did not. Findings
that shape P1:

- Query templates need local-language terms (bajas, lesión, 欠場...), not
  English only.
- `before:` is day-granular and leaky (before:09-13 returned 09-13 items).
  Re-filter every item's pubDate against the row's exact `forecast_at`.
- Article links are Google-encoded; they decode via the documented
  batchexecute call. Some publishers 403 a plain scripted fetch. Do not spoof
  a browser to get past a block: record the fetch as `FETCH_BLOCKED`, never
  as no evidence.
- The RSS `<description>` is only the headline and publisher again -- there
  is no separate snippet.

**Headlines vs full text (asked for by the human).** For each market the local
model judges twice on the same retrieval: (a) headlines + publisher + date
only, (b) the same plus the full article text of every item that fetched.
Report on the subset where at least one article fetched: how often (b)'s
verdict differs from (a)'s, which one agrees with Sonnet and the
adjudication, and the fetch success rate. If full text rarely changes a
verdict, production skips fetching -- cheaper, faster, and no dependence on
publishers allowing scripted access.

Gate: if P1 retrieval recall on positives is poor, fix retrieval before any
R1-R4 work; if interpretation disagrees often, the 9B target is wrong before
any engineering is spent on it.

## Decision

Replace the current autonomous Claude web-research session with a staged,
auditable pipeline:

    deterministic Python query construction
        -> search and fetch
        -> append-only timestamped evidence bundle
        -> one local structured-output inference
        -> optional, narrowly gated Sonnet escalation
        -> append-only forecast log

The local model does not browse, call tools, or read files itself. Python gives
it a bounded evidence bundle in one prompt. Retrieval failure, no relevant
evidence, and a valid positive finding are three different states and must
never collapse into one another.

Initial local-model target: **Qwen 3.5 9B, 4-bit**, served by Ollama on the M5
Pro / 24 GB host. This is a target to validate, not a foregone promotion.

## Goals

1. Cut cloud-model usage by at least 80% relative to the widened research arm.
2. Preserve the forward-only timestamp and source audit trail.
3. Make retrieval deterministic and independently testable.
4. Make every model verdict schema-valid and traceable to stored source IDs.
5. Keep the old Sonnet arm and the new local arm experimentally distinct.
6. Fail closed: missing search, fetch, model, or parse capability defers the
   market; it never becomes `NONE` and is never scored.

## Non-goals

- Do not change `kalshi_quant`'s candidate contract or fitness definition.
- Do not replay historical markets against today's web.
- Do not silently replace the researcher behind `roster_news_favourite`.
- Do not let the local model choose an unbounded sequence of searches.
- Do not optimize the four-point fade before the retrieval/model arm itself is
  measured; candidate logic and researcher quality are separate questions.

## Architecture

### 1. Deterministic retrieval

Add a retrieval module owned by `kalshi_research`, with interfaces for search
and fetch providers. For a qualifying market it should:

- derive the event date and named sides from captured market metadata;
- emit a small, versioned set of query templates (initially 2-3);
- search concurrently with hard result, time, and byte caps;
- fetch the underlying source where possible rather than trusting snippets;
- normalize URLs, deduplicate sources, and reject results outside the relevant
  date window;
- retain source title, URL, publication time when available, retrieval time,
  relevant text, provider, and a content hash;
- treat pages as untrusted data: strip active content, cap text, and tell the
  model that instructions found in sources are not instructions to follow.

The search-provider choice is deliberately behind an interface. The first
provider may be a commercial search API, a sports/news API, RSS/official feeds,
or a self-hosted metasearch service. Direct scraping of consumer search-result
HTML is not an acceptable production dependency.

### 2. Append-only evidence store

Write one immutable evidence bundle per retrieval attempt under the run-data
root. Each bundle needs a stable ID and at least:

- ticker, event, candidate/researcher arm, query-template version;
- started/completed/retrieved timestamps;
- queries and provider response metadata;
- normalized sources and content hashes;
- explicit state: `SEARCH_FAILED`, `NO_RELEVANT_RESULTS`, or
  `EVIDENCE_AVAILABLE`;
- structured error details that are logged but never shown to a candidate as
  evidence.

Forecast rows reference the evidence-bundle ID. Scoring consumes the forecast
made at that time; it never reruns retrieval.

### 3. One-shot local interpretation

Add a local researcher adapter that calls Ollama with:

- a pinned model and quantization recorded in the researcher name;
- temperature 0 (or the closest supported deterministic setting);
- no tools and no autonomous loop;
- a bounded context containing only the query, market identity, date rules,
  and stored evidence;
- a JSON schema with `state`, `verdict`, `harmed_side`, `evidence_source_ids`,
  and a short factual explanation.

Only cited source IDs may support a positive verdict. Schema failure, an
unknown source ID, a missing date, or a side that cannot be mapped to the
market is a research failure and defers the market.

### 4. Optional Sonnet escalation

Sonnet is not the default researcher. Escalate only when a deterministic rule
fires, initially one of:

- sources conflict about availability or lineup status;
- local output identifies possible relevant evidence but cannot map the side;
- a positive verdict needs verification;
- the local output is invalid after one retry.

The escalation sees the SAME stored evidence bundle. It does not start a new
open-ended web search during the first implementation. This isolates model
interpretation from retrieval quality and bounds cloud usage. Record local and
escalated outputs separately.

### 5. Separate experimental arm

Do not append local-research forecasts under the existing
`roster_news_favourite` identity. Create a separately named candidate/researcher
arm with a fresh `created_at`. The old arm remains historical. The new arm may
reuse the forecast mapping only if that fact is explicit in its manifest.

## Implementation sequence

### R0 — pause and preserve [DONE 2026-09-22]

- Boot out the research LaunchAgent while idle.
- Record the experiment as paused and document the reason.
- Leave all other schedulers running.
- Preserve existing research forecasts unchanged.

### R1 — retrieval contracts and fixtures

- Define typed `QueryPlan`, `SourceDocument`, and `EvidenceBundle` records.
- Define search/fetch provider protocols and explicit failure categories.
- Add fixtures covering positive news, no news, old news, negation, duplicate
  syndication, conflicting reports, missing dates, fetch failure, and prompt
  injection inside a source.
- Choose and implement one search provider after its credentials/cost are
  approved. Provider secrets must remain outside the repository and logs.

Done when the same market and fixed provider fixture produce the same canonical
bundle, with every failure distinguishable from no evidence.

### R2 — evidence store and local adapter

- Implement append/read of immutable evidence bundles.
- Add the Ollama adapter for pinned `qwen3.5:9b` (exact installed tag recorded).
- Enforce JSON-schema output and source-ID validation.
- Add time, context, and output caps plus health reporting.
- Keep the existing `ResearchContext.research(query) -> str` boundary if
  possible; widen the research experiment's own types only if the evidence ID
  cannot be carried honestly through the current log.

Done when all fixtures produce deterministic, auditable outcomes and injected
source instructions cannot alter the requested schema or policy.

### R3 — shadow comparison, no forecasts

Run local and Sonnet interpretation on the same live evidence bundles without
letting either output affect a forecast. Start with at least 200 qualifying
markets and manually adjudicate:

- every positive from either model;
- every disagreement;
- a random sample of at least 50 `NONE` agreements.

Report schema-valid rate, retrieval-success rate, latency, `NONE` agreement,
positive precision/recall against adjudication, side-mapping errors, citation
validity, negation errors, and projected cloud-call reduction.

Minimum promotion criteria:

- 100% parseable output after at most one local retry;
- zero silent conversion of failures to `NONE`;
- zero unsupported positive verdicts in the adjudicated set;
- no negation failures in the fixture suite;
- at least 90% recall of adjudicated relevant findings (report the interval;
  do not hide a small denominator);
- median local interpretation under 30 seconds on this host;
- projected Sonnet escalation rate at or below 20%.

If the positive sample is too small to measure recall, continue shadowing. Do
not promote merely because both models usually say `NONE`.

### R4 — canary arm

- Create a new candidate/researcher identity and fresh forward clock.
- Run manually at no more than 5 research attempts per pass for 48 hours.
- Verify evidence/forecast joins, timestamps, source IDs, failures, resource
  use, and that no old candidate ID receives new local-model rows.
- Compare canary outputs with the shadow report; investigate drift before
  increasing volume.

### R5 — controlled resume

Resume scheduling only after R1-R4 pass, tests are green, the pause record is
updated with the exact backend and evidence schema versions, and the human
explicitly approves the resumption.

Start at 5 attempts/pass, then 10, then at most 25. Each increase requires 48
hours without silent failures. Automatically stop research attempts when the
search failure rate, local parse failure rate, or escalation rate breaches a
documented threshold; controls that do no research may still log only if that
behavior is explicitly desired.

## Resume and rollback

Resume is a deliberate state transition, not merely loading the old plist.
The replacement plist must name the new researcher backend and candidate arm.
The old Sonnet plist is retained as history and must not be reloaded unchanged.

Rollback means booting out the replacement LaunchAgent. Existing evidence and
forecasts remain append-only. A rollback never switches the new candidate ID
back to Sonnet or reuses its clock with another backend.

## Open decisions requiring human approval

1. Search/fetch provider and any recurring external cost.
2. Whether full fetched text may be retained, or only bounded excerpts plus
   hashes and URLs.
3. The acceptable Sonnet escalation ceiling below the 20% promotion maximum.
4. Whether a second local model should adjudicate positives before Sonnet.

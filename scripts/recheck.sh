#!/bin/bash
# Re-run the Phase 2 diagnostics and save a dated report.
#
#     scripts/recheck.sh
#
# Answers the two questions deferred on 2026-08-24, both of which needed more
# than one afternoon of data:
#   1. does the market midpoint's shading toward 0.5 survive? (market_prob, G2)
#   2. how do the four controls score on a larger, more diverse sample?
#
# Writes data/reports/recheck-<utc-timestamp>.txt (gitignored) and fires a
# desktop notification. Safe to run by hand at any time.
set -uo pipefail

REPO="/Users/hermesagent/development/avo-forecast"
UV="/Users/hermesagent/miniconda3/bin/uv"
cd "$REPO" || exit 1

OUT_DIR="$REPO/data/reports"
mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/recheck-$(date -u +%Y%m%dT%H%M%SZ).txt"

{
  echo "avo-forecast Phase 2 re-check"
  echo "generated $(date -u '+%Y-%m-%d %H:%M:%S UTC')  ($(date '+%Z %H:%M'))"
  echo
  echo "snapshots:   $(find "$REPO/data/kalshi_quant/snapshots"   -name '*.parquet' 2>/dev/null | wc -l | tr -d ' ') files"
  echo "resolutions: $(find "$REPO/data/kalshi_quant/resolutions" -name '*.parquet' 2>/dev/null | wc -l | tr -d ' ') files"
  echo
  echo "================ scripts/score_baselines.py ================"
  "$UV" run python scripts/score_baselines.py 2>&1
  echo
  echo "================ scripts/check_calibration.py =============="
  "$UV" run python scripts/check_calibration.py 2>&1
  echo
  echo "================ open decisions ============================"
  echo "G2  market_prob : raw midpoint, spread-aware, or fillable-only?"
  echo "G2  ENTRY_POLICY: last-before-resolution is the most generous choice"
  echo "                  available (see experiments/kalshi_quant/observations.py)"
  echo "G2  bootstrap_ci: i.i.d. resampling on series-clustered observations"
  echo "                  understates every interval printed above"
  echo "G4  Phase 3 gate: do not start hand-writing candidates until the above"
  echo "                  are settled -- ROADMAP calls Phase 3 highest-value"
} 2>&1 | tee "$OUT"

# Headline for the notification: the tripwire's verdict.
LINE=$(grep -E '^baseline_sharpened' "$OUT" | head -1 | tr -s ' ')
osascript -e "display notification \"${LINE:-see report}\" with title \"avo-forecast re-check ready\" subtitle \"$(basename "$OUT")\"" 2>/dev/null

echo
echo "report: $OUT"

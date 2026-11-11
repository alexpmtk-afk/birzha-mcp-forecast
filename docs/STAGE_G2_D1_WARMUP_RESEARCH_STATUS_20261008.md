# Stage G2 — D1 reconstructed research / warmup evidence (2026-10-08)

## Scope and status

The Stage G2 research work is **not a production classifier** and is **not a strict historical causal replay**.

| Work package | Evidence | Status |
| --- | --- | --- |
| Sequence Quality D1 / H1 / M15 | PR #125; historical archive D1 pass; H1/M15 unverified | Audited; PR open |
| Active-only D1 research eligibility | PR #126, 21-bar calendar/SECID filter | Implemented, tests passed; PR open |
| Immutable D1 exact warmup session evidence | PR #128, `CAPTURED_AT_SYNC` generation keys | Implemented, CI passed; PR open |
| Opt-in preactive same-SECID D1 eligibility | PR #129, candidate T + stored verified exact dates + tests | Implemented in draft PR; awaiting validation |
| Reconstructed backfill for legacy marker-only warmup | Not implemented | BLOCKED / separate work |
| Actual BR / Si / GOLD reconstructed 21-bar dry run | Requires exact-date evidence; old archive is marker-only | NOT DONE |
| TREND/BALANCE calibration and OOS validation | Thresholds not chosen; untouched historical holdout unproven | NOT STARTED |

## Why

Active-only 21-bar D1 eligibility accepted only 94 of 1,656 BR candidate sessions in the old archive. Monthly rollover leaves most active contracts with fewer than 21 active sessions. Previous candles of the **same exact SECID** exist, but the old `CONTRACT_WARMUP_V2_ACTIVITY` markers saved a verified range without the exact expected-session list. Those markers cannot independently prove a missing internal date.

PR #128 saves exact warmup session dates under immutable generation keys in existing `historical_sessions`; PR #129 adds a separate opt-in evaluator that accepts a preactive portion only when its full exact SECID date list and range certificate are proven under the same generation key. The original conservative evaluator remains unchanged. Futures prices are never stitched across SECID rollover, and `SECID=GOLD` equity is not a GOLD futures contract.

## Evidence caveats

- `CAPTURED_AT_SYNC` means captured in the **current** sync, not necessarily captured before a forecast's historical T0.
- The archived `birzha_history.duckdb` is reconstructed historical data without as-known-at-T0 versions of old candles. It cannot prove strict historical replay.
- The old archive has no full exact warmup-date generations. Historical backfill must use an explicit `RECONSTRUCTED_MOEX` origin, keep original legacy markers unchanged and be a separately reviewed read-only/nonproduction task.
- Original historical archive must never be mutated. No merge, HOME deploy, secrets access or production DB writes are part of this stage.

## Next acceptance gates

1. CI + code review for PR #129 and reconcile dependencies PR #126/#128 without accidental merge.
2. Design safe nonproduction generation/backfill of reconstructed expected warmup session lists (not automatic promotion of marker-only ranges).
3. Run real six-market D1 dataset eligibility on a copied archive, report BR coverage and missing/unknown evidence.
4. Only after reproducible dataset quality: Research Development feature distributions; no classifier/threshold/OOS claim until accepted.

# RI 2026: blank-only field repair (PR #300)

This extends the existing Google-first archive and research engine; it is not
another collector. HOME is not updated by this PR. No production apply has run.

## Scope and interface

- archive.model3_data_readiness explicitly scopes both source timestamps and
  indicator source keys to Moscow year 2026. Older data may supply warmup to the
  repair engine, but is never a write target.
- archive.model3_repair_fields(timeframe, from_date, till_date, ...) defaults to
  dry_run=true. A call covers at most 31 civil days of 2026, 200 existing rows
  (default 50) and 200 cells (default 50). The pure offline planner supports
  5000 rows for an inventory; that is not the MCP write budget.
- fields is a strict allowlist of existing research features. It excludes
  identities, hashes, formula/version/provenance fields, availability, calendar
  assertions, signal eligibility, and arbitrary columns.
- The exact original 95-column order is required. No append, insertDimension,
  row shifting, resizing, deletion, source write or new worksheet is used.
- Empty means None or "". Zero, False, formulas and every populated value are
  occupied. Existing discrepancies are reported/prevent updates; they are not
  permission to correct filled values.

Each gap contains exact SECID, date, timeframe, source key, existing row/column,
field, reason, source, dependency/required counts, and whether calculation is
permitted. Accepted gaps also contain the proposed literal. Monthly private
JSONL inventories are execution evidence, never public candle archives.

## Reasons, not manufactured zeros

MISSING_CALCULATION means an existing versioned OHLCV calculation is available
from complete, hash-pinned dependencies of the SAME contract and correct window.
INSUFFICIENT_WARMUP means insufficient prior observations of that contract/slot.
SOURCE_UNRELIABLE covers incomplete/unpinned target or dependencies, a reset
caused by an incomplete source, unknown existing formula version, or a dependency
not closed at the target. SOURCE_UNAVAILABLE means an input needed by this
calculation was not supplied/proved (for example unknown volume or indexed M1
evidence); this does not assert that Drive contains no such source.

NOT_APPLICABLE is separate: a pivot/event was not observed, a ratio is undefined
(for example zero spread), or the existing M1 proxy specification does not cover
D1. Those blanks are not missing numeric zeros. No false completion is claimed.

For recursive pivots/waves, the gate conservatively requires the entire saved
prior contract state to be complete and pinned. This may block an otherwise
possible calculation; relaxing it requires a separate dependency proof, not
silently treating incomplete bars as complete. ATR uses its contiguous source
chain and prior seed; RVOL uses 20 prior same-contract observations at the same
civil clock. Same-clock RVOL remains approximate, not a proved exchange session
alignment.

No minute source is downloaded by this new repair path. Existing H1/M15 minute
proxies remain untouched. True aggressor delta and trade-level volume-at-price
are not inferred from candle volume. This PR does not attest historical PIT,
calendar completeness or forecast fitness.

## Applying a plan later

No current owner authorization to apply is implied by code or this document.
The caller must separately establish sole external Google ownership, obtain
owner approval and review the concrete small plan/range.

Apply requires sole_writer, owner_confirmed,
external_writer_exclusion_confirmed, and expected_plan_sha256 from the accepted
preview. These explicit attestations do NOT technically exclude another Google
client. The fingerprint includes the full old indicator table, sources, daily
context, options, repair version AND exact proposed cell literals.

The existing per-workbook OS guard spans recovery, reads, audit and mutation.
The existing QUALITY_AUDIT records STARTED/PREPARED/COMMITTED or ERROR; source
and full existing-row snapshot are re-read after PREPARED. model3_write_cells is
called with existing_only=true: header cells and writes outside the current
95-column grid fail closed. Only userEnteredValue is changed; formatting,
notes, validation, row addresses and unrelated cells are preserved.

Google Sheets does not offer a compare-and-swap for this multi-request sequence.
The final read is optimistic, not distributed locking. Owner exclusion of
external editors/jobs is a mandatory production prerequisite.

After one bounded native batchUpdate, ALL old table rows plus edits are read
back and compared. Status goes to existing SYNC_STATUS. A status failure after
confirmed write is logged and never resubmits the patch. An unknown write or
receipt response leaves PREPARED unresolved: no automatic retry, timeout release,
rollback of user data, or overwrite from an older backup. Existing recovery
recognizes the exact after hash without another indicator write; before hash
requires explicit settlement; any other hash is a conflict.

Pagination: follow next_key only when remaining_rows exists and the selected
row window has no remaining_fillable_cells. If cells were capped, re-preview
the SAME window after readback; do not skip its remaining blanks. Every apply
requires a newly accepted current fingerprint. No automatic mass loop or
scheduler is introduced.

## Acceptance boundaries

Tests cover mixed years, exact contract/window isolation, ATR/RVOL warmup and
source resets, unknown inputs, occupied zero/False/formula, fixed 95-column
order/grid, source/row concurrency, receipts, late/unknown responses, no-op
repeat, full readback, and causal D1 context. The engine now also rejects a
previous-calendar-day D1 whose bar end is later than the H1 target end; that is
a causality correction, not a historical availability assertion.

Local/CI success does not mean HOME installation, external writer exclusion or
live Google apply. Merge, archive-only deploy/restart, pilot writes and schedule
remain separate owner decisions. Forecast18082, DuckDB and M1 ZIP stay untouched.

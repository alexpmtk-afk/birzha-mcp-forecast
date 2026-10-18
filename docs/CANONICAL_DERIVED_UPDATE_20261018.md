# Controlled canonical derived update — 18.10.2026

## Scope and default

The existing `mirror.sync` and `mirror.sync_core` accept optional `include_derived=false`.
Nothing is activated automatically. No new collector, scheduler, database connection,
credentials path, paid subscription, Forecast invocation or source-history write is added.
The existing candle store and existing flow store are injected into the same writer.
The same whole-workbook ownership declaration and process lock apply to all sheets.

## Prepared update order

1. Existing history acquisition accepts D1 only after its session, H1 after its interval,
   M15 after its existing minute-completeness rule. This change does not turn archived
   `is_complete` into independent minute or calendar proof.
2. Existing `history.flow_sync` / `history.flow_capture_public_trades` follow actual
   publication and capture availability. Do not add a second collector or retry paid
   history without the owner's source approval. Missing old trades cannot be recovered
   from the fact that a new public capture succeeded.
3. Existing mirror reads already stored candles, current canonical candles and selected
   stored flow through the existing store locks. It makes no source network request.
4. Preflight all candle, service and requested derived sheets before the first write.
   INDICATORS is computed over the whole retained canonical history plus planned new
   candles, grouped by exact contract and timeframe, never today's root substitution.
5. Native append and complete readback; final digest comparison across all candle and
   derived sheets. Overall success is published only after both comparisons.
6. Repeat the same request: zero additional candle and derived rows. A lost write
   response is not retried blindly. The next request reads every existing key and
   reconstructs its remaining additions from that committed prefix.

Example after separate installation and activation approval:

```json
{"tool":"mirror.sync","arguments":{"symbol":"SBER","from_date":"2026-10-15","till_date":"2026-10-16","dry_run":true,"include_derived":true}}
```

The existing scheduled caller must explicitly pass `include_derived=true` only after
acceptance. Repeat with `dry_run=false` requires exclusive ownership and production
approval. Existing callers retain their candle-only default until that decision.

## Schemas and immutable formulas

INDICATORS has the same 34 columns as the verified SBER pilot. FLOW has 28 columns,
POSITIONS 22. All three must already exist with exact headers; no implicit creation,
replacement, clear, rename or deletion is implemented by the runtime writer.

Frozen pilot formula V1 and its records are retained. New V2 identity includes symbol,
exact contract, timeframe, original source key, full source-row hash, ordered window
hash and formula version. Formula version includes the existing feature-engine file
hash and V2 window/revision policy. A change in the source/window gets a new record;
an existing record cannot silently acquire different values. The original successful
calculation timestamp is retained on repeats. The full formula version is registered
in SYNC_STATUS under its short identifier. No formula in the Forecast engine changes.

Window limits and missing-data rules equal the accepted research calculation: each
exact contract has its own history, incomplete bars reset it, insufficient history
remains missing, unknown volume is not zero, index own-volume features are inapplicable.
Explicit provider-clock conversion is used for timezone-aware and naive candle times.
The policy does not bridge an unverified calendar gap by inventing bars.

## Stored flow and positions

The read-only exporter allows only requested exact contract-family TRADESTATS and
PUBLIC_TRADES_RAW plus root-scoped FUTOI for Si/BR/GD. GOLD equity rows cannot enter
GD futures. Dates are bounded to 367 calendar dates and 100000 selected source rows;
a larger read fails and requires a narrower interval. No public-trades checkpoint,
raw server payload, secrets or unrelated dataset is transferred to cloud.

Only selected normalized fields and local source references/hashes are published.
FUTOI ticker GD remains an all-contracts underlying, not GDZ6 contract OI. Signed long
plus signed short is checked against net where all three exist. Contract OI is emitted
only when actually captured on futures trades; OI changes are left missing because
this route does not establish continuous session coverage. Equity/index OI is not
invented. Participant counts absent from this fixed destination schema are retained
in the source, not silently mapped into OI columns.

M5 buy minus sell is a partial-source Delta. Its ratio explicitly uses classified
buy+sell volume; unknown volume is unavailable. No full-session Cumulative Delta or
reset is inferred. The separate historical captured-subset profiles/VWAP and archived
Si logical-time reconstructions remain in the audited local research dataset; this
first runtime extension does not publish them automatically or claim full coverage.

available_at/confidence and observed_at are preserved only where stored. The writer
does not invent first_seen_at, known units, a trading-session date or historical
forecast admission. Future source subscription provenance is not certified by this
normalization. All derived results remain reconstructed/partial research context.

## Failure, continuation and last successful state

Before mutation: any missing sheet, header, key conflict, source-family mismatch,
nonliteral/nonfinite value, row limit or oversized transfer blocks the whole request.
Transfer row encoding is measured before writing; each native body remains below
1 MB with at most 250 rows. A failed mutation marks the overall operation failed and
retains previous successful timestamps/digests. Accepted old rows are not rolled back,
removed or rewritten. Fresh readback reconstructs the exact continuation point by
record identities. Success stores source version, formula version, requested range,
all derived sheet digests and real finalization clock. Failure does not grant a pass.

## Release gate

Requires the owner's separate decision under attachment section 15 and AGENTS.md:
merge #262 then compatible #263, install the reviewed code in HOME, restart only the
Birzha program, use existing approved authorization, declare one writer, and migrate
the six named canonical books with fresh complete backups and full readback. Do not
reuse an older RTSI-only restart consent. Do not reboot the computer or close others.

Before cloud migration, refresh source and destination snapshots and recompute the
plan: the local 265076-row research union is evidence, not a blind overwrite payload.
Append only nonconflicting missing candles; preserve all old values, empty fields,
archive tabs and raw index zeroes. Expand candle headers losslessly to 40 fields,
add the new logical sheets in the established order, then exercise dry/real/repeat.
Only the selected normalized market fields are proposed for cloud transfer; bulk
server-file export previously rejected by automatic approval is not requested again.

No HOME acceptance or regular-update acceptance is claimed by local tests. Full
calendar, historical first receipt, units, source completeness and model readiness
remain separately limited. Production DATA_UPDATE requires an actual accepted cycle.
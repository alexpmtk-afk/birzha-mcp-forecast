# Q-110–Q-113 — original native D1 and prospective first-receipt restart audit

Based on Draft #222 exact SHA 3f3f4c53244740c146f6405eed37d0e730937b62.
All operations are opt-in GitHub research code and disposable CI fixtures.

## Q-110: ambiguous native ISS D1 table is NOT an opaque fixture

Previously replay_native_d1_if_present returned UNREPLAYED=False if
a well-formed JSON document omitted exact top-level 'candles' key.
A malformed or mistyped native candles table such as 'Candles' or
'candles.cursor' without the matching canonical data could therefore
be admitted as a synthetic opaque byte blob. Refuse key names
indicating a native candle table, including case-variants and
metadata-only 'candles.cursor'. Clearly nonnative archived synthetic
fixtures remain UNREPLAYED and are never official proof.

## Q-111: impossible >24h original native D1 must fail

Native D1 replay checked source date and claimed end time but had no
bound on original candle length; a caller could coordinate a future
end time claim with the raw body and treat a two-day span as a single
completed daily candle. The full D1 raw replay now requires candle
end within 24 hours of its source begin and Moscow end day no
later than next Moscow calendar day. This is a content consistency
guard only, not an official market schedule.

## Q-112: pilot first-capture receipt causal cold audit

Previously ProspectivePilotLedger.audit recomputed receipt SHA but
checked only forecast SHA and source SHA. A receipt's source observed
time, T0, captured_at, snapshot or source label could be modified
and rehashed and still pass cold audit. Now it rebinds receipt IDs,
snapshot contract, symbol and SECID to the original Forecast payload
and checks exactly completed_event_end <= source_observed <= T0 <=
captured_at, matching immutable captured_at DB column with both
300-second source and issuance freshness windows. Rehashed bad
receipt, arbitrary source certificate label and altered DB column
fail closed, including after restart. It cannot protect against an
attacker who controls all originals plus external independent trust.

## Q-113: DuckDB first-receipt causal and canonical-column cold audit

SingleDuckDBProspectiveStagingJournal._audit rechecks full first
receipt and frozen Forecast time/identity after restart, using the
same source <= observed <= T0 <= captured sequence/freshness,
and compares denormalized Forecast columns (symbol, secid, issue
time, engine) against frozen canonical Forecast JSON. Previously
rehashed timestamp/identity changes or changed denormalized columns
were not explicitly rejected. Tests prove corruption detection on
reopen. This does NOT itself attest independent MOEX first arrival.

## Boundaries and required tests

Four independent adversarial test groups. Full exact-head pinned
no-push crossline CI + pytest + compile + Public Repository Security
+ Repository Preflight are required before technical PASS. No
independently signed trusted first receipt, official complete MOEX
session calendar, retrospective reconstructed ground truth, real
5/10/20 prospective outcomes, approved Protocol08 scoring or
protected HOME read-only run is manufactured here.

All 12 canonical release blockers remain OPEN. No main merge,
HOME/REMOTE calls, production DB, protected holdout, Forecast V1
or automated trading changes.

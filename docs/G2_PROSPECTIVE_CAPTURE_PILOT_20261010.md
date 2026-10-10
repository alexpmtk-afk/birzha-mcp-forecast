# G2: Prospective Capture and Outcome Pilot — 10.10.2026

## Goal / owners / boundaries

Owner: ChatGPT, independent of Codex-colleague G2-TREND-BALANCE-RESEARCH. This stacked non-production PR adds an isolated atomic forecast receipt → later outcome pilot, tests and a synthetic demo. PR #137 (accepted reconstructed dataset) → PR #139 (ChatGPT's Development forecast baseline) → this PR. The pilot does NOT change ForecastService, DuckDBForecastJournal, DuckDBOutcomeJournal, MCP, HOME, scheduler, network requests, secrets, or 2023–24 holdout. No merge/deploy.

Module src/birzha/application/prospective_capture.py uses only the Python standard library. Staging SQLite is explicitly not production storage; canonical DuckDB forecast/outcome journals remain the longer-term integration targets.

## Capture gate and immutable receipt

Input: a ForecastRecord.to_dict()-compatible object with FORECAST_RECORD_V1_PROTOCOL_08, MARKET_SNAPSHOT_V2 snapshot identity, exact market/SECID, finite positive reference price, and exactly 5/10/20-session horizon records. CaptureEvidence contains original source bytes, source-observed timestamp, most recent completed bar end and LIVE_CAPTURED_PAYLOAD origin.

The pilot atomically stores original input bytes and SHA256, immutable forecast JSON and SHA256, and a bound receipt with snapshot ID, decision T0, source-observed/last-completed times, captured-at and explicit research-only status.

Refuse reconstruction instead of actually acquired input, unavailable evidence, unversioned/unanchored snapshot, future bars or source timestamps after T0, future T0 after capture, stale evidence or T0 older than 300 seconds, bad clock zone, missing/invalid reference price, wrong horizon set. Identical repeat returns original receipt, not a new timestamp; same forecast ID with changed input/forecast fails closed.

SQLite update/delete triggers protect captured forecasts and outcomes against ordinary SQL mutation; audit rechecks JSON and source-byte hashes after reopen. This is corruption detection, not cryptographic third-party timestamping.

## Post-maturity future outcome

CompletedSessions includes the original future source bytes, source observation timestamp, exact market/SECID, horizon-count session dates and independently supplied verified expected dates, completed-bar end timestamps and closes. Only completed and observed price data after capture may be used. The session calendar must match exactly (no gap compression), the same SECID must hold for each horizon, and all price/times must be well-formed. The contract uses exchange-local Europe/Moscow dates, avoiding UTC-midnight misclassification.

One outcome per forecast+horizon (5/10/20) is appended; subsequent exact retries are idempotent. Conflicting later values or a different source/calendar are rejected instead of overwriting history. Outcomes remain separate from original immutable predictions.

**Critical caveat:** the pilot does not authenticate the caller's source credentials, raw response, market calendar or wallclock; a caller can provide a misleading origin string. Consequently it is neither a deployed prospective capture nor proof of strict ex-ante causality. A privileged actor can also bypass SQLite trigger protections. Do not claim real-market capture or statistical performance from a synthetic test.

## Reproduction and actual evidence

Local Python 3.13.5 with pytest and no DuckDB:

    PYTHONPATH=src pytest -q tests/test_prospective_capture.py
    # 17 passed

    PYTHONPATH=src python -m scripts.g2_prospective_pilot_demo --output-dir /new/scratch-only/pilot

The demo was executed on entirely fabricated inputs, labelled SYNTHETIC_ONLY, without MOEX calls. Result after close/reopen milestones: 1 immutable forecast receipt and 3 later synthetic outcome records for 5/10/20 sessions, audit PASS. The generated synthetic direction hits are not actual model predictions and do not establish quality.

17 tests include idempotent persistence, unchanged capture time on retry, forecast/data collision, bad source origin, future/late data, missing snapshot or wrong version, invalid prices and timezone, no forecast/no horizon, exact calendar and exact SECID, uncompleted future D1, duplicates/conflicting outcomes, SQLite UPDATE/DELETE refusal, byte tampering, Moscow local midnight.

Synthetic demo SHA256 (not stable across all SQLite storage engines): DB 3af41e6fc2d30c68caccf4f9fefeb05b29347e2a4c7b144d12d536c916978e70; JSON report 066744b10bb1869db2225065c186be94676836ab13f9e4fb1467b88807596de7.

## Gates before using actual prospective data

1. Add a trustworthy real collector adapter to preserve provider raw payload, provider identity, ingestion/observed_at, bar finality, actual event_time and exact SECID/calendar provenance. Simply setting an origin to LIVE_CAPTURED_PAYLOAD is not proof.
2. Tie snapshot_id to complete verifiable input provenance, an external time anchor and a durable receipt whose hash binds forecast/source; handle atomicity/restart/collision when attaching to canonical DuckDB Forecast and Outcome Journals, rather than creating two parallel production ledgers.
3. Require post-capture authenticated market sessions and exact SECID, verified calendar, finality and append-only Outcome Contract. Keep missing or ambiguous horizons PENDING; never quietly compress gaps or roll futures.
4. On disposable staging verify concurrent writers, crash-in-transaction, backup/restore, clock skew, missing/late revisions, transport trust boundaries and bounded integration with existing ForecastService.
5. Independent review of stacked PRs, full CI, backup/rollback and separate user consent for merge, deploy, scheduling, secrets, production schema/data.

Next: ChatGPT implements a bounded staging adapter to canonical journals after checking their contracts and retains ownership of forecast quality/prospective pipeline; Codex-colleague independently works on TREND/BALANCE. No holdout 2023–24 usage.

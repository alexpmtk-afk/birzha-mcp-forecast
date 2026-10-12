# Q-088 — source/receipt/session-candidate dry-run contract

A new opt-in, pure offline preflight builds a deterministic SHA-256-bound
research commitment from Q-087's replayed ORIGINAL M1 bytes, exact instrument,
candidate M15 and operator-supplied official-schedule document bytes.

Key distinction: **content binding is not external certification**.
Official host URL claims, document SHA, caller-supplied request/response
timestamps, session ID and source body may all be forged together. The
preflight's local evaluation clock is NOT original first-receipt time.
The existing g2_official_schedule_witness.py verifies a different
future-session cut-off contract and is NOT reused as if it provided a
verified historical live M15 session calendar.

Preflight checks notice bytes/hash, official HTTPS host claim, exact
engine/market/board/SECID, declared session interval containing the
replayed completed M15, operator notice observation before source request,
and source receipt recency relative to injected local evaluation clock.
It rejects user-self-promoted EXACT/DOCUMENTED candle availability,
observed-at evidence, altered OHLCV, future/stale time and notice drift.

Returns canonical UTF-8 manifest bytes, SHA-256 and explicit unresolved
external-gate reason codes for future **independent** TSA commitment. Its
source/replay/session content tests are offline only. This is not a real
external TSA or signed exchange-calendar verification. All externally
controlled claims and journal/forecast/production authority remain false.

No network, sqlite, HOME, live provider, forecast, trading, merge or deploy.
Future step needs captured authentic transport evidence, verified MOEX
session witness and separately signed first-receipt commitment on actual
new source bytes, all before any LevelEventPilot write.

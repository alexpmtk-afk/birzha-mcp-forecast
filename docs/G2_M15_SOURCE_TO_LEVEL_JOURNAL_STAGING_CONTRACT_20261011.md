# Q-086: M15 original-source to Level Journal admission contract

Status: offline design contract, NOT a live MOEX connection or release permission.

The current optional LevelEventPilot accepts caller-supplied Instrument, Candle,
raw bytes, and research ATR; it does not independently establish that the bytes
contain the candle or that an authenticated HTTPS connection really reached MOEX.

## Evidence required for each next received M15

1. Preserve original HTTP response bytes BEFORE any parsing, their SHA-256,
   request start and end/receive UTC, HTTP response status and content type.
2. Record the real HTTPS hostname iss.moex.com and exact ISS path containing
   the original engine, market, board and SECID. For M15 derived from native
   M1, query interval=1 and retain full bounded pagination/offset history.
   URL asserted by a caller is NOT independent transport authentication.
3. Independently reparse the retained native M1 candles.columns/data payload.
   Exactly 15 consecutive completed one-minute source rows must make one M15,
   with no missing/duplicate/overlapping rows, future row, unknown volume or
   invented zero-volume filler. Determine M15 open/close, high/low and
   sum(volume/value) solely from these rows.
4. Source SHA, original page offsets, precise instrument identity, all
   constituent M1 end times and derived M15 fields must be immutable and
   tied to ONE journal first-receipt identifier and its commit UTC.
5. Provider-observed timestamp, journal-owned first_received_at and external
   independent time proof are THREE DIFFERENT FACTS. SHA-256 and HTTP Date
   alone DO NOT certify original MOEX transport or independent UTC time.
6. Before giving source available_at_confidence=EXACT, require a separate
   verified receipt/clock contract. A caller flag must never upgrade
   assumed availability. Use ABSTAIN until evidence is verified.
7. MOEX session calendar/witness must establish the session grouping and
   transition; do not infer weekend/calendar continuity. Account for
   gaps, contract rolls and futures active-root changes explicitly.
8. Existing request governor is mandatory for any future opt-in provider
   call; isolate new writes under a user-authorized staging root, never
   production, HOME, historical forecast journals or protected OOS.
9. Repeated exact receipts are idempotent; changed bytes for an existing
   candle, stale observations, mismatched board/SECID, missing rows or
   partial candles require fail-closed refusal.

## Acceptance

Prove with mocked original ISS source pages and negative fixtures: wrong
SECID/path/board/market; missing/duplicated M1; high/low, volume/value
mismatch; truncated pagination; clock mismatch; altered source payload;
stale/replayed M15; restart/crash/idempotence; independent release-gate checks.

The existing MOEX native M1 and M15 aggregation is in
src/birzha/providers/moex_iss.py. Existing staging archival capture in
scripts/g2_six_market_live_source_capture.py retains original HTTP bytes,
but is not an attested prospective M15 issuance adapter.

PR #207 hardens INTERNAL integrity of the journal and does not implement
this source adapter, authenticate exchange provenance, request new live
market data, authorize forecasting, approve Alignment weights or close the
12 release blockers. NO_MAIN, NO_HOME, NO_PRODUCTION and NO_TRADE.

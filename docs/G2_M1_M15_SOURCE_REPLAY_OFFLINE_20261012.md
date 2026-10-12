# Q-087: Offline original MOEX M1 -> one completed M15 replay

This opt-in research validator reads ONLY caller-provided original raw bytes
from bounded MOEX ISS M1 pages. It makes no network request, opens no database,
uses no existing Forecast/M15 adapter and does not modify HOME/production.

The candidate MOEX source endpoint must be HTTPS iss.moex.com, exact
engine/market/board/SECID, requested interval=1 with consecutive pagination
start offsets and a consistent date query. Source-body SHA256, HTTP status,
content type, request UTC and response UTC claims are checked. Original
candles.columns/data are parsed from **unchanged bytes**, and exactly 15
consecutive one-minute exchange-local candles with end at :59 are used for a
single aligned completed M15 OHLCV. Extra or missing minute, duplicate,
partial, reordered, unobserved/future, invalid prices, unknown volume/value,
truncated cursor pagination and mutated expected M15 all fail closed.

The result contains reconstructed M15, original page SHA references and
diagnostics, with ALL downstream authority explicitly FALSE:
source_transport_independently_authenticated=false,
receipt_clock_independently_attested=false,
session_calendar_independently_verified=false,
level_journal_admission_authorized=false,
forecast_or_trade_authorized=false, production_authorized=false.

The metadata is supplied by the caller. A matching HTTPS URL field and hash
do NOT prove authentic MOEX transport, earliest market availability, or
independent RFC3161/TSA commitment. This work deliberately does NOT call
LevelEventPilot.append or set available_at_confidence=EXACT.

Future opt-in source-to-journal gate needs: provider request governor and
authentic transport metadata bound to raw bytes; externally verifiable
first-receipt time and retained original source pages; a calendar session
witness preventing overnight continuity claims; separate approved staging
root and failure recovery. Only *then* consider prospective fresh M15
capture in isolation, never protected OOS or real trades.

Do not merge/deploy/main/HOME/production without explicit authorizations.
All 12 release-gate blockers remain open and Alignment weights unapproved.

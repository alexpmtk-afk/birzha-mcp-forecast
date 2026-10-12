# Q-091 — governed mock-only original response callback

Standalone optional helper (not imported by existing production paths)
uses an actual MoexIssClient and its existing request governor. Each
attempt runs inside governor.execute: stamp request start, call the
original provider one_attempt, capture raw response bytes on completion,
validate JSON shape, then atomically append to Q-090 isolated SQLite
before returning the HTTP result. Retry, pacing, budgets and 503 handling
remain controlled by the existing governor. A failed SQLite write
propagates without uncontrolled new HTTP attempts.

Default real httpx MOEX transport is REJECTED even if opt-in is true.
Only test fake in-memory openers were used; no live MOEX, TSA or external
provider requests. A custom opener alone is not externally authenticated.
The URL constructed for the requested exact SECID is a local intention,
not independent TLS server identity proof. Local completion timestamp is
AFTER body download in the callback, not earliest network arrival.

Negative tests cover missing bytes, wrong Content-Type, bad source
JSON/table, non-200 and retryable statuses, abort on failed journal
persistence, corrupt previous journal, stale unrelated new receipts,
date/offset sanity and refusal of real provider by default.

Q-087 M1 replay, Q-088 schedule candidate, Q-089 RFC3161 verifier
and LevelEventPilot still do not authorize a live input. No automatic
EXACT source certification, production forecast issuance or trading.

Future independently authorized implementation must establish trusted
MOEX transport origin, actual first response and signed time plus real
exchange session witness. Release blocked: NO_MAIN/NO_HOME/NO_PRODUCTION.

# Q-090: isolated first-LOCAL-intake M1 receipt journal (research only)

New opt-in OriginalM1LocalIntake keeps original response body bytes and
caller-reported HTTP page metadata in an isolated SQLite file under an
explicit disposable staging_root. The process reads its own offset-aware
clock immediately on each append invocation and stores a first LOCAL
intake timestamp atomically with the raw bytes, source SHA256, exact
instrument/capture/page ID, immutable trigger protection and hash chain.

Identical receipt retry after restart preserves its original timestamp;
different bytes or metadata for the same capture/page cause Collision.
New incoming pages with future/stale claims, missing bytes, different
market identity and invalid sequence are refused. A separate read-only
handoff re-reads/audits the journal, reruns offline M1→M15 and Q-088
session-candidate preflight, and produces canonical UTF8 bytes committing
to original retained per-page hashes AND locally committed receipt IDs/
times, together with the prior Q-088 hash.

CRITICAL: This is NOT a real collector integration. The caller passes
OriginalM1Page as an object; original response_received_utc, official URL,
HTTP status and content type are OPERATOR CLAIMS. Storing its bytes when
append() is invoked does not prove their earliest network arrival, origin
at MOEX, independent clock accuracy, authentic calendar or an externally
signed RFC3161 timestamp. Caller may have held the bytes prior to append.
The handoff's own sha is NOT TSA-signed here. No upstream requests, no
TSA requests, no scheduler, no LEVEL_EVENT_PILOT append, no forecast.

The test plan covers durable restart/idempotence, source collisions,
clock anomalies, hostile raw-byte modifications, multiple pages,
corrupted indexed timestamps, missing append-only trigger, failed
calendar/source handoff, and no writes from handoff.

Future engineering boundary: authorized MOEX request-governor callback
that atomically retains actual transport response bytes at reception,
trusted timestamp commitment of the sealed first LOCAL receipt,
independently approved TSA trust root, official versioned calendar
session evidence, crash recovery and real forward-only M15 captures.
No retrospective source-data reconstruction is allowed to stand in
for actual first network receipt. MAIN/HOME/production/Forecast V1,
protected OOS, live signals and trading remain untouched.

All 12 G2 release-gate blockers stay open.

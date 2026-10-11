# G2: RFC3161 trust candidates and exact MOEX session cutoff — research and offline preflight (2026-10-11)

## Official sources verified 2026-10-11 (read-only public research)

1. IETF **RFC3161** / RFC5816: TSA response must be cryptographically checked against its original request hash and optional nonce, trusted signing chain, TSU certificate critical timeStamping-only extendedKeyUsage, acceptable policy OID, signing certificate revocation status and accuracy. `genTime ± accuracy` is the relevant confidence interval; a correctly signed token does **not** show MOEX input genuineness nor independently measure the local claimed Forecast T0.
   - https://www.rfc-editor.org/rfc/rfc3161.html
   - https://www.rfc-editor.org/rfc/rfc5816.html
2. **DigiCert** publishes an RFC3161 endpoint `http://timestamp.digicert.com` and documented certificates; on **2026-09-03 it replaced three timestamping responder certificates**. This is a potential RFC3161 TSQ/TSR-compatible service to investigate, **not** an approved or activated service. HTTP transport, commercial terms, trust chain policy, direct compatible nonce handling and actual production fitness require separate approval, with certificate-rotation/revocation procedures. **No queries sent.**
   - https://knowledge.digicert.com/general-information/rfc3161-compliant-time-stamp-authority-server
3. **GlobalSign DSS** has documented `GET /v2/timestamp/:digest` accepting a SHA-256 hex digest and authenticated `Authorization: Bearer`, returning a base64 DER timestamp *token*. This endpoint does not accept an original random-nonce TSQ parameter in the documented API, so **cannot be treated as a drop-in for the project's strict original nonce-bearing TSQ + signed TSR verifier** without a separately documented compatible protocol or redesigned, separately reviewed proof contract. Auth and mTLS/API credentials are outside scope; no connection attempted.
   - https://api.docs.globalsign.com/docs/dss/timestamp-digest
   - https://api.docs.globalsign.com/docs/dss/login
4. **MOEX official schedule** includes distinct morning/main/evening and selected weekend sessions for equities. SBER/TQBR exact trading eligibility, altered session close, holidays, suspensions and earliest *future observable event* must be proven by a versioned official schedule/notice and exact-SECID route, **not inferred from UTC midnight, a weekday formula or latest D1 bar alone**. A SHA of a locally provided schedule proves bytes, not authenticity of the schedule.
   - https://www.moex.com/a1684

## Non-network code added

`src/birzha/application/g2_tsa_policy_cutoff_review.py` does an **offline, conditional** review of **already archived original signed TSQ/TSR/CA/chain bytes** in the disposable same-DuckDB forecast journal:

- Replays the RFC3161 OpenSSL cryptographic nonce, imprint and signer chain checks against original immutable commitment bytes, after journal/source manifest SHA audit.
- Requires caller-provided proposed CA bundle hash to match archived original certificate bundle, enforces explicitly allowed TSA policy OID and verifies `accuracy` from the signed token text (decimal or hexadecimal units, rounded *up*, rejects unknown/zero/invalid accuracy).
- Requires exact original Forecast SECID/engine/market/board match against a separately supplied proposed first future event cutoff, its explicit session identifier and SHA of the externally obtained official schedule snapshot; rejects wrong market, missing TZ, overlapping signed genTime+accuracy bound and unsupported event types.
- Returns `CANDIDATE_CRYPTO_AND_TIME_ORDER_CONSISTENT` **only as a structural conditional review**. Every output permanently says `independent_tsa_authority_approved=false`, `exchange_session_cutoff_independently_certified=false`, `future_outcome_cutoff_independently_approved=false`, `local_forecast_t0_independently_measured=false`, `release_gate_can_close=false` and `production_authorized=false`.
- A caller-supplied fingerprint and a schedule document SHA **cannot independently approve TSA/legal identity, revocation or exchange timetable authenticity**. No unattended trust escalation even after locally consistent crypto checks. No new external network action and no outbound hash transmission.

## What would have to happen next, separately

Independent selection/approval of the TSA provider and trust/revocation policy (especially rotating 2026 responder certs); authenticated official instrument-specific MOEX future event/close schedule with exceptions; explicit user authorization for any genuine external TSA request and controlled new prospective Forecast; independently inspect actual signed TSR and event-time upper bounds. The archive/clock and original first-receipt calendar evidence still require external sign-off.

**Not done:** selecting a provider, creating an account/API token, downloading signer cert files, posting any Forecast imprint externally, issuing timestamps, changing HOME or production, main merge, opening OOS, executing MOEX trades or removing any of the 12 G2 release blockers. All tests use synthetic TSA runner or prior offline local synthetic self-signed fixture.

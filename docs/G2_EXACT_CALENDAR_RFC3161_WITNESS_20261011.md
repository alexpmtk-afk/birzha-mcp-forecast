# G2 — Exact SECID calendar cross-check and RFC3161 signed T0 anchor readiness (2026-10-11)

## What we independently audited

The existing `src/birzha/providers/moex_calendar.py` gets actual trading dates for a specific `engine/market/board/security` from the MOEX /iss/history route, excluding zero-activity placeholders. **But it contains a backward-compatibility fallback that interprets date-only legacy test rows as sessions.** That provider by itself does not preserve original raw pages or prove the exact close time of the most recent trading session.

MOEX itself documents `/iss` (live/delayed source) and `/iss/history` (historical results) as different namespaces; historical access may be subscription-dependent, and a 15-minute delay can apply to free market data. **The second MOEX endpoint is independent of the candle endpoint, not independent of MOEX as a provider.** It cannot become a certified external calendar merely through a successful cross-check.

The current G2 Forecast T0 is a local runtime timestamp recorded after HTTP receipt; SHA-256 protects local bytes but is not external time authentication. RFC3161 time-stamping can prove an immutable commitment existed by a trusted TSA generation time, but **does not prove a claimed local T0 is UTC-accurate or that MOEX inputs themselves are authentic**. An independently certified exchange calendar/event cutoff remains a separate requirement.

## Implemented, opt-in and OFFLINE

- `src/birzha/application/g2_calendar_witness.py` checks original separately supplied MOEX /history raw bytes against their SHA-256 and per-page receipt UTC, exact engine/market/board/SECID path, positive activity columns, full pagination, no duplicate trade dates and recent D1 session concordance. The `concordance_from_atomic_staging` entry derives original D1 pages from the *already audited* single-DuckDB raw source bundle, never caller-provided candles. Strictly fail-closed on missing or malformed history. Reports `MOEX_SECOND_ENDPOINT_ACTIVITY_CONCORDANCE`, **not** a certified exchange schedule or verified close time.
- `src/birzha/application/g2_time_anchor.py` creates a deterministic UTF-8 statement binding the frozen exact `forecast_payload_sha256`, original `source_blob_sha256`, exact `manifest_sha256`, SECID and claimed issue T0. The source must come from the audited canonical single DuckDB, including previously persisted original full manifest (PR #189). It can verify an optional external RFC3161 TSA response with an externally supplied/approved trusted CA and saved nonce-bearing original request. Verification includes **both** original query nonce/signature and original statement data imprint/signature, plus signed generation time within 5 minutes of claimed T0.
- No TSA URL, CA, credentials, keys or trust anchor bundled; no timestamp network request or MOEX history request is launched by CI. There is deliberately **no** implicit fallback to GitHub runner UTC, HTTP Date, source SHA alone, local unsigned JSON, weekend-based calendar or a fabricated test CA.
- Every result explicitly says `independent_exchange_calendar_verified=false`, `forecast_issued_at_t0_independently_measured=false`, `production_authorized=false`. A cryptographically valid token under a **configured CA** is only proof of existence at the signed TSA time; a person must independently approve the actual TSA trust policy, chain, revocation and relation of signed time to future forecast outcome cutoff. Do NOT count the gate as PASS before that.

## RFC3161 issuance protocol for a future approved isolated environment

1. From the audited disposable DuckDB, obtain `commitment_from_atomic_staging(journal, forecast_id)`; retain exact bytes unchanged in a `forecast-commitment.json` file. Forecasts from legacy journal rows that lack original page manifests fail closed.
2. Locally create a nonce-bearing RFC3161 request, using OpenSSL, without contacting a third party:

```bash
openssl ts -query -data forecast-commitment.json -sha256 -cert -out original-request.tsq
```

3. Have an **independently approved** RFC3161 TSA issue a DER `response.tsr` corresponding to the preserved original query *before any future outcome/event being claimed*. Retain the original query, original complete response, separate trusted CA chain/policy evidence and immutable commitment. The server/CA is **not** specified or selected automatically here. Review TSA accuracy, nonce, cert chain, purpose/EKU, revocation and policy separately; an unapproved self-issued test CA never authorizes release.
4. Call `verify_rfc3161(commitment, issued_at_t0=..., query_path=..., response_path=..., trust_ca_path=...)` and audit the returned binding/time. The verifier delegates cryptographic trust-chain and nonce/imprint checks to OpenSSL rather than interpreting strings as signatures.
5. Only then decide if the **signed** TSA time actually preceded the *independently established* first future evaluation cutoff. Without certified cutoff/evidence, the gate remains BLOCKED even with a valid token.

## Known limitations and remaining blocker status

- No real independent TSA request/response has been obtained in this stage. All cryptographic positive tests use **mocked runners**, so the passing offline tests cannot be presented as a real signed timestamp.
- /history needs separate governed capture/immutable persistence and might be inaccessible for certain instruments without authorized access; not fetched here. Future work must capture original history pages within source request budgets and preserve them transactionally as a separate authenticated witness. If history is unavailable, remain BLOCKED, do not infer weekdays or falsify exchange sessions.
- A separate activity-bearing /history row proves some trading activity, **not** that the session was formally finished by a specific exchange time. Official independently authenticated session close schedule and clock uncertainty are still missing.
- No change to model, journal production writer, frozen Forecast/Outcome, HOME, protected 2023–24 holdout, main, secrets, deployment or 12 release blockers.
- `G2_RELEASE_ALLOWED=false` is expected from CI; only individual code-contract readiness can PASS in this PR.

# G2 / RFC 3161 external time witness — durable evidence contract, 2026-10-11

## Direct result of Draft PR #194

A forecast already immutably issued in a **disposable, single-file DuckDB STAGING** can acquire an external RFC 3161 receipt as **a distinct append-only second transaction**. The Forecast/source/original manifest never changes. The external response necessarily arrives *after* issuance and therefore cannot be part of the original Forecast transaction.

`append_signed_time_evidence(journal, forecast_id, query_path=..., response_path=..., ca_path=..., untrusted_chain_path=...)` snapshots the exact original byte content in a temporary private directory, cryptographically verifies those snapshots via OpenSSL, and commits the same bytes to `g2_atomic_time_anchors` in the **same DuckDB file** as Forecast. Bound material includes original issued Forecast JSON, source bundle, full per-page original manifest, original nonce-bearing TSQ bytes, signed DER TSR bytes, CA public chain bytes and optional signer intermediates, individual SHA-256s and signed TSA generation time. Subsequent `audit()` compares all retained SHA hashes and regenerates the commitment against canonical Forecast/source bytes, including after restart. A conflicting second receipt is rejected rather than replacing the original. Fault after insert rolls back the receipt but preserves Forecast.

**A separately supplied trust CA is *not* independently approved solely by passing `openssl ts -verify`.** Every entry is deliberately stamped with `external_ca_independently_approved=false`, `forecast_t0_independently_measured=false`, `before_future_outcome_cutoff_certified=false` and `production_authorized=false`.

## What a REAL independent accepted timestamp would still require

1. Before requesting any external timestamp, a responsible operator independently approves a genuine RFC 3161 Timestamp Authority's service, legal/data-sharing conditions, independent CA trust anchor, chain/policy OIDs, expected `timeStamping` EKU, TSA signer status and revocation/OCSP/CRL procedure. Pin the approved root/chain fingerprints and policy before contacting the TSA; **do not treat a user-supplied or test self-signed CA as verified third-party identity**.
2. Issue a genuine immutable source-bound Forecast on actual authorized fresh source bytes. Before future outcomes, create `commitment_from_atomic_staging` from the audited canonical single DuckDB; persist exactly that commitment. Generate a client-random nonce-bearing RFC 3161 TSQ referencing **SHA-256 of this commitment**. The TSA request must convey only the hash (not full source or Forecast bytes).
3. Within a permitted environment, send that TSQ to the preapproved TSA and durably capture the original TSQ, signed DER TSR, CA chain/certificates, provider policy evidence, signed genTime and (when available) independently validated clock uncertainty and actual trading-session/event cutoff.
4. Verify mandatory TSQ nonce present (not unspecified); signed query/nonce matches response; full digest matches exact saved immutable commitment; signer chain, expected TSA EKU/policy, current trust/revocation and generation time window pass. A generic TLS HTTP `Date`, ordinary GitHub runner clock, SHA without a real signed TSR, or mocked local self-signed test CA is **not** external proof.
5. Independently confirm the **signed TSA genTime occurred before the independently established future event/outcome cutoff** and within a preapproved issuance lag, allowing for TSA accuracy and time uncertainty. It proves bytes existed **by genTime**, not that claimed local T0 exactly equals that real time; separate first-receipt/provider calendar evidence is still necessary. No historical or old Forecast can be made prospectively authenticated retroactively.
6. Independently reverify archived TSQ/TSR with current approved pinned trust anchors and revocation information during acceptance, not only by replaying a stored locally asserted `status=VERIFIED`.

## Not done or authorized

No public real TSA was chosen or contacted. No root CA/fingerprint was independently approved. No real trusted TSR was issued for a future event. No real MOEX source/history HTTP request was made as part of #194. No production/HOME/main merge, DB changes outside disposable staging, holdout peeking or trading. Production release gate stays **12 BLOCKERS, RELEASE_ALLOWED=false**. Passing mock/ephemeral self-signed crypto tests validates implementation only, not time attestation as a factual result.

Next independent step is trust-provider approval (external to code), then a *separately authorized and bounded* genuine TSA proof acquisition for a newly created prospective Forecast with independently checked future event cutoff.

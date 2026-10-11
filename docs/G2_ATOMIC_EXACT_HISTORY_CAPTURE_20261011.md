# G2 — One bounded source+calendar capture / one immutable DuckDB (2026-10-11)

## Exact capability added

**Draft PR #192** builds on Draft #191 (second-endpoint exact-SECID history concordance) and #189 (canonical source-manifest single-DuckDB retention). This is an explicit **nonproduction** staging mode, never a released data source.

Run only after explicitly approving a limited actual network test on an isolated disposable environment:

```bash
# Documentation; this command has NOT been run against public MOEX in CI.
python -m scripts.g2_capture_and_issue_atomic_staging \
  --market SBER \
  --capture-dir /tmp/new-original-source-only \
  --staging-root /tmp/previously-created-disposable-staging \
  --require-history-corroboration
```

- The current governed MOEX client first resolves **exact engine/market/board/SECID** and receives original D1/H1/M1 page bytes, with per-page SHA and local begin/end receipt timestamps.
- **Only with the new explicit flag**, the same captured resolved instrument is used for a bounded `/history/engines/.../boards/.../securities/<SECID>.json` read. Requested historical dates match the *already captured D1 timeframe request*, preventing a caller from substituting a convenient period. No new re-resolution (or extra unrecorded root lookup) is permitted.
- Both source collection and historical receipt share **one existing per-market 120-physical-attempt / 240-monotonic-second budget**, including network retries and `Retry-After`. The additional history cap is 20 pages × 100 rows. All bytes are retained in memory while the single Forecast is being prepared.
- Before any staging DB file is opened, the history witness must pass strict SHA, exact route, positive trading activity, complete unchanged cursor pagination and last active exact-SECID session concordance against original D1 source. A partial/mismatched/unavailable /history endpoint refuses issuance **without creating a Forecast**.
- Upon successful admission, the exact original /history HTTP bodies are stored base64 with each local receipt's start/end, SHA and HTTP Date, along with the original D1/H1/M1 manifest/raw bytes and immutable Forecast **inside the same DuckDB `g2_atomic_captures` transaction**. The existing journal `_audit` repeats source vs. history comparison on every read and catches tampering even if the receipt's own SHA is recomputed. A different calendar on duplicate issuance is an immutable collision.
- Existing historical/legacy records without this optional calendar are not rewritten or granted retrospective authenticity. Ordinary `forecast.create` remains guarded from past unverified LIVE T0; enabled flag does not authorize production.

## Genuine evidence vs test claims

Synthetic offline regressions cover exact original byte retention, bad/zero-volume historical sessions, matching D1 latest dates, stale history receipt, forged SECID/board, manifest replay collision, transaction rollback, restart/readback, and corrupted base64 including recomputed journal hashes. CI also runs SHA-pinned #168/#179/#147 no-push integration and full pytest, compilation, Security and Preflight.

**External and production facts NOT established:** no actual MOEX /history HTTP request was performed in this PR, no exchange independently certified its trading calendar/session-close time, no independent TSA signature was obtained, local host UTC first receipt is not trusted signed provider time, history and candles come from the **same MOEX provider**, future +5/+10/+20 outcomes and OOS skill have not matured/proved, and HOME/production remain outside permitted scope. Even a same-provider history cross-check PASS does not qualify the full `independent_exchange_calendar_and_clock` release gate. Production release still has **12 outstanding blockers**, including separate user authorizations.

## Next independent evidence step

A future controlled real one-market smoke **may require an authorized historical-data subscription** for /history. If provider denies access, record the sanitized reason and classify as provider/permission **BLOCKED**, not a reason to guess weekdays, bypass quotas, use a synthetic calendar, issue a data-less Forecast, or claim a stronger AI model would solve access.

For external T0 proof, issue a commitment to the immutable Forecast/source/manifest and request a nonce-based RFC3161 timestamp from a separately approved trusted TSA before the future evaluation cutoff. Archive the genuine original request, DER response, trust chain/policy and proof bytes. Draft #191 only tests the validator with synthetic self-signed certificates; no default trusted TSA endpoint, network call or secret has been selected.

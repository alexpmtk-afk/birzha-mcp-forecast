# G2 — original MOEX schedule pages and versioned exception chain (2026-10-11)

## Source-of-truth distinctions

The official MOEX pages show that exchange sessions are **not** a simple weekday calendar. Its announcement for the 2026 calendar (https://www.moex.com/n94172) was followed by specific late-2025 holiday/weekend notices (https://www.moex.com/n95564?nt=111 and https://www.moex.com/n96571?nt=106). More granular stock-market session rules include distinct morning, main, evening and weekend sessions (https://www.moex.com/a1684), and the official trading-calendar overview is https://www.moex.com/ru/tradingcalendar?year=2026. Public announcements say weekend additional sessions can be assigned to **the following trading day**. The weekend notice expressly permits later schedule updates.

**These links are navigation/context, not retrieved immutable authenticated bytes.** A public URL or locally calculated SHA does not prove TLS delivery, page identity, content authorship, exact SECID eligibility, an unaltered effective session, exchange-certified close or independent time.

## New opt-in nonproduction chain

`src/birzha/application/g2_official_schedule_witness.py` accepts an OFFLINE versioned envelope of **original captured bytes supplied by a controlled capture process**, together with a conservatively interpreted exact-instrument session *candidate*:

- one exact frozen Forecast ID + resolved `SECID/engine/market/board`, one session identifier and Moscow calendar date;
- original HTTPS `moex.com` / `www.moex.com` page bytes (raw HTML/PDF/text) encoded losslessly, SHA-256, MIME, local observed UTC, exact URL (bounded inventory);
- ordered notice revisions, every revision bound to the specific original page SHA, original published-at claim, immutable parent revision SHA and the claimed version of the session (regular/weekend/holiday/special notice/cancelled);
- explicit local session date, assigned *trading day*, actual first candidate event in UTC and candidate session close, exact SECID eligibility assertion. **No weekend/holiday inference or extrapolation from a weekday pattern**;
- all original source receipts must claim observed before the immutable Forecast T0; a notice discovered later is not retroactively admitted. The last revision wins, missing originals, time reversals, gaps, unknown eligibility, amended identifiers and cancellation fail closed.

`src/birzha/storage/g2_schedule_evidence.py` append-only stores original envelope bytes, SHA and the derived conservative candidate in the **same disposable single-DuckDB file as Forecast** via a separate atomic transaction, like RFC3161 proof retention. Reopens/audits compare every original document SHA, revision parent chain, candidate and exact original stored Forecast/source SECID. Duplicate identical evidence is idempotent; different replay or tampering (even after changing DB hash fields) is refused. Fault after insert rolls back schedule evidence without changing the immutable Forecast. Legacy sources with no original source pages cannot be upgraded into authenticated schedules.

The derived candidate is compatible with the conditional `G2_EXACT_SECID_FUTURE_EVENT_CUTOFF_CANDIDATE_V1` contract used by PR #195; `derived_candidate_from_staging` derives its cutoff from **retained original bytes** rather than caller-entered arbitrary SHA. It is not an API to certify exchange schedule truth.

## Hard boundary of what PASS means

Even a structurally valid document chain **does not authenticate that a supplied page was genuinely received from MOEX**, that the operator transcribed its timetable correctly, or that an instrument was eligible for a particular supplementary session. It does not detect omissions of uncollected later notices. The candidate consistently returns:

- `source_identity_cryptographically_authenticated=false`
- `instrument_eligibility_independently_certified=false`
- `session_event_time_independently_certified=false`
- `exchange_calendar_release_approved=false`.

This PR does not call MOEX, TSA, or post Forecast hashes. No provider/timestamp independent third-party trust sign-off, no genuine official raw page with independent receipt proof, no future session cutoff independently established, no protected holdout, HOME, main, production, merge, trading, or release-gate change. **All 12 outstanding G2 production release requirements remain.**

## Real acceptance still required

An independently authorized bounded HTTPS capture needs original response bytes, transport/authority proof, precise local retrieval times and a comprehensive source-notice change inventory **as of T0**. Separate formal review must confirm correct instrument-specific interpretation, exceptions and assigned trading dates. Only an approved timestamp authority can independently certify the time of the SHA-bound Forecast commitment; its signed `genTime + uncertainty` must precede an independently authenticated **first future evaluation event**. Manual document digests alone cannot close this gate.

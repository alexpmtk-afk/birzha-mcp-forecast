# Q-092 — Cross-layer causal audit of Q-085 to Q-091

Scope: Draft PRs #205–#212 on the research-only G2 chain, based on #212
commit 3289c1dc2948712d1cc116bca91a94ffc0226e19.
This is a direct code audit, not independent external expert sign-off.

## Confirmed defects addressed

1. **P0 — future rows in original M1 pages.** Q-087 verified that the 15
   selected M1 bars had completed by the last source receipt, but other
   bars within the same original page could end AFTER that page's claimed
   receipt and still enter its committed raw-source hash. Q-092 checks
   completion of EVERY M1 row against EACH original page's receipt time.
   Negative fixtures exercise future selected and unselected minutes.
2. **P0 — last local receipt after evaluation T0.** Q-090 checked only the
   FIRST locally committed page before making a M15 handoff. The last
   page might actually be committed after the declared evaluation, while
   all its caller-reported HTTP times appear earlier. Q-092 compares
   ALL original local commit times against the requested T0 using
   timezone-aware parsed timestamps. Tests refuse the earlier T0 and
   accept a legitimate later evaluation.
3. **P1 — insufficient native M1 validation in Q-091 callback.** It
   checked source JSON table shape but not native OHLCV consistency,
   duplicate/out-of-order native minutes or future native M1 end times.
   Q-092 uses the exact Q-087 minute parser at the callback BEFORE a
   successful local SQLite append and rejects all three classes with
   additional negative tests.

## Rechecked trust and error boundaries

- Q-085 first-observed level journal is research-only; internal SQLite
  SHA chains and source-reported observation fields are not independent
  market source/clock attestations. Q-086 strengthens metadata/index
  consistency but does not prevent hostile coordinated DB rewriting.
- Q-087 retained original source bytes and M1→M15 replay do not prove
  genuine MOEX TLS origin; URL and receipt time can be supplied by caller.
- Q-088 MOEX official-session candidate remains UNVERIFIED until the
  actual official market calendar witness is independently authenticated.
- Q-089 RFC3161 checks provided query/response/CA offline. Mock CI
  signing fixtures do not establish a real TSA signature, approved trust
  root or first real market reception.
- Q-090 first-LOCAL-intake time is not socket first-arrival time. All
  original source page receipts must now precede the declared handoff T0.
- Q-091 only uses existing MOEX request governor and test custom opener.
  A custom opener cannot independently certify its own origin or claim
  that zero real traffic occurred outside these mock tests.
- No module gains authority to set EXACT, append to production LevelEvent,
  issue Forecast V1, trade, or modify HOME/main/protected OOS.

## Acceptance and open boundaries

Run exact-final-HEAD focused/full pinned no-push crossline CI, compile,
Repository Preflight and Public Repository Security, with all twelve
G2 release blockers remaining open. All corrections and tests must be
included in the final tested commit.

Real MOEX transport authentication, verified official session calendar,
independent original network receipt, external TSA trust roots and
expert review of Alignment weights remain uncompleted. These cannot be
closed by synthetic source pages or passing unit tests.

NO_MAIN_MERGE; NO_HOME; NO_PRODUCTION; NO_TRADE.

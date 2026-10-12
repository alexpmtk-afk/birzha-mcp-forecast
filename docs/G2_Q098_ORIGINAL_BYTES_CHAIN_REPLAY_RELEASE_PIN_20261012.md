# Q-098 — proof against internally rehashed multi-layer staging evidence

Base: Q-095–097 Draft #216 exact commit
3bd5cbbdd44d2ce321a1f15c40a89aff5ad5e815.

## Confirmed gap in previous engineering acceptance

Q-097 checked raw M1 SHA/request route in a *self-reported* Q-088
manifest and compared the SHA pointers in a self-reported Q-096
session verdict. It did not reopen the actual Q-090 local-original
SQLite record and regenerate M1→M15; it did not regenerate session
acceptance from the original normalized schedule bytes. A coordinated
rewrite of Q-088 M15 OHLCV, canonical JSON bytes, its SHA and Q-096
parent SHA could therefore pass content-pointer checks without
matching the original stored source. A modified first-local receipt
ID/time could also be overlooked.

## Fixed acceptance contract

- Require exact typed Q-090 OriginalM1LocalIntake, instrument, M15
  candidate, original notice, capture identity and evaluation time.
- Within ONE SQLite read snapshot, rerun local source audit, original
  M1→M15 replay and Q-088 preflight; demand exact byte/manifest equality
  with the caller's submitted Q-088.
- Re-run Q-096 normalized intraday session segment guard from the exact
  Q-088 recalculation and original notice; compare its entire result.
- Verify Q-090 captured local receipt identity, raw source hash, first
  local commit timestamp and claimed original response-complete time.
  Reject caller-invented external attestation or success status.
- Read the frozen canonical release manifest from the checked-out
  project docs and refuse a caller-provided modification (including
  forged evidence text even if the blocker count remains 12).

Adversarial tests edit the staged M15 value and correctly recompute both
staged hashes, alter a rehashed accepted segment index, forge first
local receipt IDs/timestamps and fake attestation, change arbitrary
release evidence and mutate the underlying SQLite raw bytes.

## Unresolved limits

Even this replay confirms internal raw-source consistency in a local
checkout, NOT that the bytes were authenticated by MOEX transport,
that an official exchange signed a session schedule, that a trusted
TSA signed original receipt at network delivery, or that the local
checkout itself cannot be maliciously rewritten. The original Q-090
receipt is local timestamp evidence, not independently clock-anchored.

This work does not authorize Forecast V1, trading, level journal writes,
main/HOME deployment, prospective protected OOS use or close any of the
12 canonical release requirements. Independent source and clock
attestation, semantic expert review, maturated prospective outcomes and
separate approvals still govern live readiness.

Acceptance: exact final HEAD full and focused GitHub Actions tests,
compile, Public Repository Security and Repository Preflight PASS,
research output still RELEASE_BLOCKED, 12 blockers. No push/merge to
main, no HOME, no production.

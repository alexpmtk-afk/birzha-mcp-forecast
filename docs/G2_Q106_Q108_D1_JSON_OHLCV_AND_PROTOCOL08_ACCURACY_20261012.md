# Q-106–Q-108 — audited source integrity and truthful Protocol 08 status

Base: Q-102–Q-105 Draft PR #221, exact HEAD
9b5ca8d59a9308de93bc96367643e6ee47d635ac.

## Q-106 — malformed JSON cannot silently become opaque synthetic source

The native D1 inspector previously returned False (UNREPLAYED) on any
malformed JSON, unless the literal bytes contained the precise
substring `"candles"`. This substring test did not cover escaped
property names, leading JSON-like syntax or truncated original JSON.
A malformed intended ISS response could be accepted into development
storage as an opaque fixture (still NOT external proof). A JSON-shaped
malformed body now fails closed; existing clearly synthetic non-JSON
fixture bytes remain UNREPLAYED for non-production compatibility.
The status never upgrades to independently authentic MOEX.

## Q-107 — complete OHLCV, not only D1 close/clock

Native source rows previously required only begin/end/close, so a
malformed truncated pseudo-ISS row could reproduce the three claimed
arrays. Native content validation now also requires columns open,
high, low, volume, and value, positive finite OHLC, nonnegative finite
volume/value, and low <= open/close <= high. Missing fields, negative
volumes, fabricated high/low and Boolean prices are rejected. This is
SOURCE CONTENT consistency only; official MOEX TLS transport and
session calendar remain unverified. No historical bytes rewritten.

## Q-108 — reconcile existing Protocol08 code vs release-grade status

The generic Protocol08 audit returned NOT_IMPLEMENTED for independent
Control/Holding, level/obstacle Route and Market State despite the
existence of research-only implementations in:
- `control_holding.py` — M15 reaction + holding research
- `level_route.py` — typed historical level and obstacle candidates
- `market_state_classifier.py` — provisional six-regime candidate
- `g2_alignment15_proposal.py` — 0..15 research weights, still UNAPPROVED

Status now distinguishes RESEARCH_PRESENT_UNVERIFIED,
RESEARCH_PARTIAL_UNVERIFIED and RESEARCH_PROPOSAL_UNAPPROVED from
missing functionality. This does NOT mark the frozen ForecastRecord
as fully Protocol08 compliant, certify any claimed source/time, approve
Alignment factor allocation, or allow execution. Source evidence,
reviewed semantics and prospective OOS outcomes must independently pass.

## Crossline conflict and security boundaries

Full exact-head pinned crossline ephemeral no-push merge, full pytest,
focused pytest, compile, Public Repository Security, Repository
Preflight and unchanged 12 release blockers are mandatory.
Separately Q-109 is a private, manual-only HOME native-cmd design
proposal in gpt-powershell-bridge, NOT a HOME execution.

No main merge, HOME/REMOTE commands, production write, protected OOS
unlock, Forecast V1 score change or automated order.

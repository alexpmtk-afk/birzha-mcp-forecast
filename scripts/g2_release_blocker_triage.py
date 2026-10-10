"""Explain why G2 release is blocked, without changing the release decision.

This inventory classifies evidence gaps; it NEVER turns a gate to PASS or
authorizes a merge, HOME deployment, migration, or trading.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.g2_release_gate import REQUIRED, validate

# Advisory categorization, deliberately independent of evidence status.
# Every mandatory gate and both explicit approvals MUST have an entry.
NEXT = {
    "code_three_line_compatibility": (
        "CODE_REGRESSION", "Re-run exact current-head integration CI after upstream SHA changes."),
    "individual_pr_semantic_review": (
        "REVIEW", "Independently inspect code and negative cases; record written reviewer acceptance."),
    "historical_first_receipt_attestation": (
        "LEGACY_EVIDENCE_NOT_RECOVERABLE",
        "Do not backfill old first receipts. Preserve historical DEVELOPMENT-only label; decide a separate prospective-only release scope before changing the policy."),
    "future_outcomes_5_10_20": (
        "FUTURE_NOT_YET_MATURE",
        "After each actual 5/10/20 exchange-session maturity, append source-bound outcomes without rewriting forecasts."),
    "independent_exchange_calendar_and_clock": (
        "EXTERNAL_ATTESTATION",
        "Verify a complete exact-SECID session calendar and trustworthy source/receipt clock provenance."),
    "forecast_skill_and_oos_significance": (
        "VALIDATION_DATA_MISSING",
        "Wait for genuine prospective outcomes, then pre-register OOS/metrics and independent statistics; do not reopen reserved history implicitly."),
    "forecast_protocol08_complete": (
        "PRODUCT_SCOPE_NOT_IMPLEMENTED",
        "Implement and accept the remaining Control, Route, scenario and quality contracts; baseline facts are not the full Protocol 08."),
    "durable_single_canonical_storage": (
        "STAGING_NOT_PRODUCTION",
        "Review canonical storage migration and rollback; demonstrate recovery on authorized disposable fixtures before any production change."),
    "home_windows_readonly_acceptance": (
        "EXECUTION_PATH_BLOCKED",
        "Repair approved read-only Windows execution path without bypassing execution policy, then measure actual HOME git/runtime state."),
    "production_backup_rollback_and_authorization": (
        "RELEASE_OPERATIONS",
        "Verify backup and restore in a permitted test environment, document rollback and request separate deployment authorization."),
    "live_forecast_issuance_after_source_receipt": (
        "LIVE_ADAPTER_NOT_ATTESTED",
        "Trace every real LIVE forecast producer to an issued-at-after-receipt guard; verify true UTC receipt, source bundle SHA and negative T0 tests."),
    "user_approved_main_merge": (
        "USER_PERMISSION", "Obtain explicit user authorization for merging the exact reviewed commit; never infer consent from CI."),
    "user_approved_home_deploy": (
        "USER_PERMISSION", "Obtain separate explicit user authorization for HOME installation after readiness and rollback checks."),
}


def triage(manifest: dict) -> dict:
    checked = validate(manifest)
    if set(NEXT) != REQUIRED | {"user_approved_main_merge", "user_approved_home_deploy"}:
        raise ValueError("triage inventory is not exhaustive")
    blocking = checked["blocking_requirements"]
    ids = {item["gate"] for item in blocking}
    rows = []
    for item in blocking:
        gate = item["gate"]
        category, next_action = NEXT[gate]
        rows.append({
            "gate": gate,
            "status": item["status"],
            "category": category,
            "evidence": item["evidence"],
            "next_action": next_action,
        })
    return {
        "release_allowed": checked["release_allowed"],
        "release_state": checked["release_state"],
        "blocking_count": len(rows),
        "blocking_technical_and_evidence": len(ids & REQUIRED),
        "blocking_user_permissions": len(ids - REQUIRED),
        "historical_receipt_missing_is_irrecoverable": (
            "historical_first_receipt_attestation" in ids
        ),
        "no_policy_change": True,
        "blocking": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args(argv)
    result = triage(json.loads(args.manifest.read_text(encoding="utf-8")))
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(f"G2_RELEASE_ALLOWED={str(result['release_allowed']).lower()}")
        print(f"G2_BLOCKERS={result['blocking_count']}; TECHNICAL={result['blocking_technical_and_evidence']}; USER_PERMISSIONS={result['blocking_user_permissions']}")
        for idx, item in enumerate(result["blocking"], 1):
            print(f"{idx:02d}. {item['gate']} [{item['status']} / {item['category']}]")
            print(f"    Evidence: {item['evidence']}")
            print(f"    Next: {item['next_action']}")
    return result


if __name__ == "__main__":
    main()

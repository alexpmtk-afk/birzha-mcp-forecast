"""Fail-closed G2 release-decision auditor. Read-only; no GitHub/Home mutation.

A passing CI is necessary but cannot silently authorize production. The
pinned JSON is a *record of observed gates*, not a deployment instruction.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SCHEMA = "G2_RELEASE_DECISION_V1"
STATUS = frozenset({"PASS", "OPEN", "BLOCKED", "PENDING"})
REQUIRED = frozenset({
    "code_three_line_compatibility",
    "individual_pr_semantic_review",
    "historical_first_receipt_attestation",
    "future_outcomes_5_10_20",
    "independent_exchange_calendar_and_clock",
    "forecast_skill_and_oos_significance",
    "forecast_protocol08_complete",
    "durable_single_canonical_storage",
    "home_windows_readonly_acceptance",
    "live_forecast_issuance_after_source_receipt",
    "production_backup_rollback_and_authorization",
})
RESEARCH = (132, 133, 134, 135, 136, 137, 138, 140, 142, 144, 147)
FORECAST = (132, 133, 134, 135, 136, 137, 139, 141, 143, 145, 146, 148)
PRODUCTION = (132, 133, 134, 135, 136, 137, 139, 141, 143, 145, 146, 148, 149, 151, 154, 155)
REVISIONS = (132, 133, 134, 135, 136, 137, 139, 141, 143, 145, 146, 148, 152, 153, 156)
EXPECTED_LEAVES = {
    156: "5f091403510a7af0a4c036404db7c04af959120d",
    155: "0a55a9607642cd1428a396c9d69ff404c7d0578f",
    147: "82af27e1f9d79f02f8062c5756047fe1fb35ef20",
}


def validate(manifest: dict) -> dict:
    if manifest.get("schema") != SCHEMA:
        raise ValueError("unrecognized release gate schema")
    if manifest.get("repository") != "alexpmtk-afk/birzha-mcp-forecast":
        raise ValueError("unexpected repository")
    if manifest.get("joint_research_ancestor") != {
        "pr": 137, "head": "36ac2f9a6c7d28838e3e863b613a574be37d8fa6"
    }:
        raise ValueError("G2 common ancestor changed without re-audit")
    raw_edges = manifest.get("pr_dependencies")
    if not isinstance(raw_edges, dict):
        raise ValueError("missing PR dependency DAG")
    edges = {int(n): parent for n, parent in raw_edges.items()}
    for node in set(RESEARCH + FORECAST + PRODUCTION + REVISIONS):
        if node not in edges:
            raise ValueError("missing required PR dependency")
    if edges[132] is not None:
        raise ValueError("only PR132 may be based on main")
    for node, parent in edges.items():
        if node != 132 and (not isinstance(parent, int) or parent not in edges):
            raise ValueError("missing or invalid PR parent")
    for path in (RESEARCH, FORECAST, PRODUCTION, REVISIONS):
        for parent, child in zip(path, path[1:]):
            if edges[child] != parent:
                raise ValueError("release dependency chain was reordered")
    for node in edges:
        seen: set[int] = set()
        cur: int | None = node
        while cur is not None:
            if cur in seen:
                raise ValueError("cycle in PR dependency graph")
            seen.add(cur)
            cur = edges[cur]
    leaves = manifest.get("release_leaves")
    if not isinstance(leaves, list) or len(leaves) != 3:
        raise ValueError("expected exactly three pinned release leaves")
    leaf_pins = {p["pr"]: p["head"] for p in leaves}
    if leaf_pins != EXPECTED_LEAVES:
        raise ValueError("release line heads moved without independent re-audit")
    if {p["parent_pr"] for p in leaves} != {144, 153, 154}:
        raise ValueError("release line dependency not pinned")

    gates = manifest.get("release_gates")
    if not isinstance(gates, dict) or set(gates) != REQUIRED:
        raise ValueError("missing mandatory release gates")
    for key, value in gates.items():
        if value.get("status") not in STATUS:
            raise ValueError(f"{key}: invalid gate status")
        if not isinstance(value.get("evidence"), str) or not value["evidence"].strip():
            raise ValueError(f"{key}: missing evidence or blocker rationale")
    approvals = {
        "main_merge": manifest.get("user_approved_main_merge") is True,
        "home_deploy": manifest.get("user_approved_home_deploy") is True,
        "quality_review": all(v["status"] == "PASS" for v in gates.values()),
    }
    allowed = all(approvals.values())
    if manifest.get("release_authorized") is not allowed:
        raise ValueError("release_authorized contradicts gates and user approvals")
    if manifest.get("state") != ("RELEASE_READY" if allowed else "RELEASE_BLOCKED"):
        raise ValueError("release state contradicts gate evidence")
    if manifest.get("production_data_modified") is not False:
        raise ValueError("production data must remain unchanged in this audit")
    if manifest.get("holdout_2023_2024_opened") is not False:
        raise ValueError("protected OOS holdout must not be accessed by this audit")
    blockers = [
        {"gate": key, "status": value["status"], "evidence": value["evidence"]}
        for key, value in sorted(gates.items())
        if value["status"] != "PASS"
    ]
    if not approvals["main_merge"]:
        blockers.append({"gate": "user_approved_main_merge", "status": "BLOCKED",
                         "evidence": "Explicit user approval not recorded"})
    if not approvals["home_deploy"]:
        blockers.append({"gate": "user_approved_home_deploy", "status": "BLOCKED",
                         "evidence": "Explicit user approval not recorded"})
    return {
        "schema": SCHEMA,
        "release_state": manifest["state"],
        "release_allowed": allowed,
        "three_pinned_heads": leaf_pins,
        "gates_passed": sum(v["status"] == "PASS" for v in gates.values()),
        "gates_total": len(REQUIRED),
        "blocking_requirements": blockers,
        "disclaimer": "Green CI/ephemeral merge are not release or trading authorization.",
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--summary", action="store_true")
    p.add_argument("--require-release", action="store_true")
    args = p.parse_args(argv)
    outcome = validate(json.loads(args.manifest.read_text(encoding="utf-8")))
    if args.summary:
        print(f"G2_RELEASE_ALLOWED={str(outcome['release_allowed']).lower()}")
        print(f"G2_BLOCKING_REQUIREMENTS={len(outcome['blocking_requirements'])}")
        for row in outcome["blocking_requirements"]:
            print(f"BLOCKER {row['gate']}: {row['status']} — {row['evidence']}")
    else:
        print(json.dumps(outcome, ensure_ascii=False, sort_keys=True, indent=2))
    if args.require_release and not outcome["release_allowed"]:
        raise SystemExit(3)
    return outcome


if __name__ == "__main__":
    main()

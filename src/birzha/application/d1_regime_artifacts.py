"""Exclusive publication for descriptive research simulations, not forecast journals."""
from __future__ import annotations

import os
from pathlib import Path

from birzha.application.d1_research_dataset_adapter import _decode, canonical_json, sha256
from birzha.application.d1_regime_experiment import prepare_quantile_candidates, evaluate_frozen_candidates

ARTIFACT_VERSION = "G2_REGIME_ARTIFACTS_V1"
_INPUT_NAMES = ("manifest.json", "accepted.jsonl", "exclusions.jsonl", "frozen.json")


def _json_bytes(value: dict) -> bytes:
    return (canonical_json(value) + "\n").encode()


def _read(path: Path) -> bytes:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError("artifact links are not permitted")
    return path.read_bytes()


def _leaf(directory: Path) -> None:
    if directory.is_symlink() or (hasattr(directory, "is_junction") and directory.is_junction()):
        raise ValueError("artifact directory links are not permitted")


def _write(path: Path, content: bytes) -> None:
    # Never overwrite; receipt is published only after every byte is read back.
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    if _read(path) != content:
        raise OSError("artifact readback mismatch")


def _publish(directory: Path, payloads: dict[str, bytes], *, stage: str, parent: str | None) -> bytes:
    for name, content in payloads.items():
        _write(directory / name, content)
    receipt = _json_bytes({
        "schema": ARTIFACT_VERSION, "stage": stage,
        "scope": "DESCRIPTIVE_PROPOSAL_SIMULATION_ONLY",
        "forecast_admission": False, "parent_receipt_sha256": parent,
        "files": {name: sha256(content) for name, content in payloads.items()},
    })
    _write(directory / "receipt.json", receipt)
    return receipt


def publish_preparation(
    directory: Path, manifest_bytes: bytes, accepted_bytes: bytes, exclusion_bytes: bytes, *,
    trusted_manifest_sha256: str, experiment_id: str, procedure_sha256: str,
    max_disagreement_percent: int,
) -> bytes:
    """Validate and freeze in memory, then exclusively create a new directory.

    A failed write leaves an incomplete directory. It is never reused automatically.
    The returned receipt hash must be anchored externally before later evaluation.
    """
    directory = Path(directory)
    frozen = prepare_quantile_candidates(
        manifest_bytes, accepted_bytes, exclusion_bytes,
        trusted_manifest_sha256=trusted_manifest_sha256, experiment_id=experiment_id,
        procedure_sha256=procedure_sha256, max_disagreement_percent=max_disagreement_percent,
    )
    directory.mkdir()  # atomic reservation; parents must already exist
    return _publish(directory, dict(zip(_INPUT_NAMES, (
        manifest_bytes, accepted_bytes, exclusion_bytes, frozen,
    ))), stage="PREPARATION_PUBLISHED", parent=None)


def read_preparation(directory: Path, *, trusted_receipt_sha256: str) -> tuple[bytes, ...]:
    """Refuse absent receipts, wrong anchors, changed files, or unexpected entries."""
    directory = Path(directory)
    _leaf(directory)
    receipt_bytes = _read(directory / "receipt.json")
    if (not isinstance(trusted_receipt_sha256, str) or len(trusted_receipt_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in trusted_receipt_sha256)
            or sha256(receipt_bytes) != trusted_receipt_sha256):
        raise ValueError("trusted preparation receipt SHA mismatch")
    receipt = _decode(receipt_bytes, "preparation receipt")
    if (not isinstance(receipt, dict) or receipt.get("schema") != ARTIFACT_VERSION
            or receipt.get("stage") != "PREPARATION_PUBLISHED"
            or receipt.get("scope") != "DESCRIPTIVE_PROPOSAL_SIMULATION_ONLY"
            or receipt.get("forecast_admission") is not False
            or receipt.get("parent_receipt_sha256") is not None
            or not isinstance(receipt.get("files"), dict)
            or set(receipt["files"]) != set(_INPUT_NAMES)):
        raise ValueError("invalid preparation receipt")
    if {p.name for p in directory.iterdir()} != {*_INPUT_NAMES, "receipt.json"}:
        raise ValueError("unexpected preparation directory entries")
    payloads = tuple(_read(directory / name) for name in _INPUT_NAMES)
    for name, content in zip(_INPUT_NAMES, payloads):
        if receipt["files"][name] != sha256(content):
            raise ValueError("preparation file SHA mismatch: " + name)
    return payloads


def publish_evaluation(
    preparation_directory: Path, output_directory: Path, *,
    trusted_receipt_sha256: str, trusted_manifest_sha256: str,
    max_disagreement_percent: int,
) -> bytes:
    """Read an anchored preparation; reserve one output; write report then receipt.

    A receipt proves complete publication, not successful classification or quality.
    ABORTED reports remain ABORTED. Missing receipt means incomplete publication.
    """
    payloads = read_preparation(preparation_directory, trusted_receipt_sha256=trusted_receipt_sha256)
    output_directory = Path(output_directory)
    output_directory.mkdir()
    report = evaluate_frozen_candidates(
        *payloads, trusted_manifest_sha256=trusted_manifest_sha256,
        trusted_frozen_sha256=sha256(payloads[-1]),
        max_disagreement_percent=max_disagreement_percent,
    )
    return _publish(output_directory, {"report.json": report},
                    stage="REPORT_PUBLISHED", parent=trusted_receipt_sha256)

def read_evaluation(directory: Path, *, trusted_receipt_sha256: str,
                    trusted_preparation_receipt_sha256: str) -> bytes:
    """Verify published report bytes and their anchored preparation lineage."""
    directory = Path(directory)
    _leaf(directory)
    receipt_bytes = _read(directory / "receipt.json")
    if sha256(receipt_bytes) != trusted_receipt_sha256:
        raise ValueError("trusted report receipt SHA mismatch")
    receipt = _decode(receipt_bytes, "report receipt")
    if (not isinstance(receipt, dict) or receipt.get("schema") != ARTIFACT_VERSION
            or receipt.get("stage") != "REPORT_PUBLISHED"
            or receipt.get("scope") != "DESCRIPTIVE_PROPOSAL_SIMULATION_ONLY"
            or receipt.get("forecast_admission") is not False
            or receipt.get("parent_receipt_sha256") != trusted_preparation_receipt_sha256
            or not isinstance(receipt.get("files"), dict)
            or set(receipt["files"]) != {"report.json"}):
        raise ValueError("invalid report receipt or preparation lineage")
    if {p.name for p in directory.iterdir()} != {"report.json", "receipt.json"}:
        raise ValueError("unexpected report directory entries")
    report = _read(directory / "report.json")
    if sha256(report) != receipt["files"]["report.json"]:
        raise ValueError("report SHA mismatch")
    return report

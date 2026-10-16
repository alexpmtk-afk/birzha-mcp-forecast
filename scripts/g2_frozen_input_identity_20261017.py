"""Externally pinned admission identity for ONE frozen 13 Oct 2026 G2 study.

This repository-tracked manifest is NOT loaded from the user-supplied ZIP.
It is immutable *for this research experiment*: new studies get new manifest
versions, never silently replace these expected digests.
"""
from __future__ import annotations

import hashlib

IDENTITY_VERSION = "G2_FROZEN_INPUT_IDENTITY_V1_20261017"
EXPECTED_ARCHIVE_SHA256 = "a80b2762d4a7af155af6863790dd50e220b1e49a61db9c330015fec654b13858"
EXPECTED_INPUT_SHA256 = "dabb1eca56ae69267829b81f9b13318cfb6c142806c3b2229eafd24d684b0c03"
EXPECTED_CASE_COUNT = 30
EXPECTED_DURATION_FALSE = 933


def verify_fixed_research_identity(
    archive_bytes: bytes, input_bytes: bytes, receipt: dict
) -> dict[str, object]:
    """Fail closed: independent pinned identity THEN original receipt integrity.

    Receipt agreement alone is insufficient; an attacker can replace receipt
    and input together. Caller must run this before parsing or calculating.
    """
    archive_hash = hashlib.sha256(archive_bytes).hexdigest()
    if archive_hash != EXPECTED_ARCHIVE_SHA256:
        raise ValueError("FROZEN_ARCHIVE_IDENTITY_MISMATCH")
    input_hash = hashlib.sha256(input_bytes).hexdigest()
    if input_hash != EXPECTED_INPUT_SHA256:
        raise ValueError("FROZEN_INPUT_IDENTITY_MISMATCH")
    if not isinstance(receipt, dict) or receipt.get("input_sha256") != input_hash:
        raise ValueError("ARCHIVE_RECEIPT_INTEGRITY_MISMATCH")
    return {
        "identity_version": IDENTITY_VERSION,
        "archive_sha256": archive_hash,
        "inputs_sha256": input_hash,
        "receipt_integrity_pass": True,
        "frozen_identity_pass": True,
    }

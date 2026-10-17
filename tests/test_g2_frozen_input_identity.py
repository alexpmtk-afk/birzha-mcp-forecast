"""Independent frozen identity is NOT trust-on-first-use from ZIP receipt."""
from copy import deepcopy
from pathlib import Path
import io
import zipfile
import json

import pytest

from scripts.g2_frozen_input_identity_20261017 import (
    IDENTITY_VERSION, EXPECTED_ARCHIVE_SHA256, EXPECTED_INPUT_SHA256,
    verify_fixed_research_identity,
)
from scripts.g2_market_structure_archive_direct import run


def test_frozen_hashes_are_exact_and_pinned_outside_archive():
    assert IDENTITY_VERSION == "G2_FROZEN_INPUT_IDENTITY_V1_20261017"
    assert EXPECTED_ARCHIVE_SHA256 == "a80b2762d4a7af155af6863790dd50e220b1e49a61db9c330015fec654b13858"
    assert EXPECTED_INPUT_SHA256 == "dabb1eca56ae69267829b81f9b13318cfb6c142806c3b2229eafd24d684b0c03"


def test_joint_forgery_of_input_and_receipt_cannot_bypass_outer_hash():
    fake = b'{"cases":[],"schema":"FAKE"}'
    import hashlib
    forged = {"input_sha256": hashlib.sha256(fake).hexdigest()}
    with pytest.raises(ValueError, match="FROZEN_ARCHIVE_IDENTITY_MISMATCH"):
        verify_fixed_research_identity(fake, fake, forged)


def test_real_input_hash_cannot_bypass_wrong_archive():
    # Outer archive proof takes precedence over self-consistent digest claims.
    with pytest.raises(ValueError, match="FROZEN_ARCHIVE_IDENTITY_MISMATCH"):
        verify_fixed_research_identity(b"wrong zip", b"any json", {"input_sha256": "fake"})


def test_cli_refuses_repackaged_zip_before_case_evaluation(tmp_path):
    trial=tmp_path/"tampered.zip"
    with zipfile.ZipFile(trial,"w") as z:
        z.writestr("allowed-real-run-v2/inputs.json", '{"cases":[]}')
        z.writestr("allowed-real-run-v2/execution-receipt.json", '{"input_sha256":"fake"}')
    with pytest.raises(ValueError, match="FROZEN_ARCHIVE_IDENTITY_MISMATCH"):
        run(trial)


def test_manifest_not_loaded_from_external_zip():
    import inspect
    from scripts import g2_market_structure_archive_direct as direct
    from scripts import g2_frozen_input_identity_20261017 as identity
    assert "verify_fixed_research_identity(archive_bytes, input_bytes, receipt)" in inspect.getsource(direct.run)
    assert "EXPECTED_ARCHIVE_SHA256" in inspect.getsource(identity.verify_fixed_research_identity)
    assert "EXPECTED_INPUT_SHA256" in inspect.getsource(identity.verify_fixed_research_identity)

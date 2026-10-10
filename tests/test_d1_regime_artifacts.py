"""File publication tests use only invented research inputs."""
import json
from pathlib import Path

import pytest

from test_d1_regime_experiment import synthetic_bundle
from birzha.application import d1_regime_artifacts as artifacts
from birzha.application.d1_research_dataset_adapter import sha256


def prepared(tmp_path):
    payload = synthetic_bundle()
    path = tmp_path / "preparation"
    receipt = artifacts.publish_preparation(
        path, *payload, trusted_manifest_sha256=sha256(payload[0]),
        experiment_id="synthetic-publication", procedure_sha256="a" * 64,
        max_disagreement_percent=20,
    )
    return path, payload, receipt


def test_round_trip_and_lineage(tmp_path):
    path, payload, receipt = prepared(tmp_path)
    frozen = artifacts.read_preparation(path, trusted_receipt_sha256=sha256(receipt))
    assert frozen[:3] == payload
    output = tmp_path / "evaluation"
    result = artifacts.publish_evaluation(
        path, output, trusted_receipt_sha256=sha256(receipt),
        trusted_manifest_sha256=sha256(payload[0]), max_disagreement_percent=20,
    )
    report = artifacts.read_evaluation(
        output, trusted_receipt_sha256=sha256(result),
        trusted_preparation_receipt_sha256=sha256(receipt),
    )
    decoded = json.loads(report)
    assert decoded["forecast_admission"] is False
    assert len(decoded["attempts"]) == 11
    with pytest.raises(ValueError, match="lineage"):
        artifacts.read_evaluation(output, trusted_receipt_sha256=sha256(result),
                                  trusted_preparation_receipt_sha256="f" * 64)


def test_existing_preparation_never_overwritten(tmp_path):
    path, payload, receipt = prepared(tmp_path)
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    with pytest.raises(FileExistsError):
        artifacts.publish_preparation(
            path, *payload, trusted_manifest_sha256=sha256(payload[0]),
            experiment_id="second", procedure_sha256="a" * 64, max_disagreement_percent=20,
        )
    assert before == {p.name: p.read_bytes() for p in path.iterdir()}


@pytest.mark.parametrize("name", ["manifest.json", "accepted.jsonl", "exclusions.jsonl", "frozen.json"])
def test_changed_input_rejected_before_output_reservation(tmp_path, name):
    path, payload, receipt = prepared(tmp_path)
    with (path / name).open("ab") as stream:
        stream.write(b" ")
    output = tmp_path / "evaluation"
    with pytest.raises(ValueError, match="SHA mismatch"):
        artifacts.publish_evaluation(
            path, output, trusted_receipt_sha256=sha256(receipt),
            trusted_manifest_sha256=sha256(payload[0]), max_disagreement_percent=20,
        )
    assert not output.exists()


def test_wrong_anchor_and_extra_entry(tmp_path):
    path, _, receipt = prepared(tmp_path)
    with pytest.raises(ValueError, match="receipt SHA"):
        artifacts.read_preparation(path, trusted_receipt_sha256="0" * 64)
    (path / "extra").write_bytes(b"untracked")
    with pytest.raises(ValueError, match="unexpected"):
        artifacts.read_preparation(path, trusted_receipt_sha256=sha256(receipt))


@pytest.mark.parametrize("failure_at", [1, 2, 3, 4, 5])
def test_interrupted_preparation_never_reads_as_complete(tmp_path, monkeypatch, failure_at):
    original = artifacts._write
    calls = 0
    def fail(path, content):
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError("simulated interrupted write")
        original(path, content)
    monkeypatch.setattr(artifacts, "_write", fail)
    payload = synthetic_bundle()
    path = tmp_path / "interrupted"
    with pytest.raises(OSError):
        artifacts.publish_preparation(
            path, *payload, trusted_manifest_sha256=sha256(payload[0]),
            experiment_id="interrupted", procedure_sha256="a" * 64, max_disagreement_percent=20,
        )
    assert path.exists()
    assert not (path / "receipt.json").exists()
    with pytest.raises(FileNotFoundError):
        artifacts.read_preparation(path, trusted_receipt_sha256="0" * 64)


def test_invalid_input_does_not_create_preparation(tmp_path):
    payload = synthetic_bundle()
    path = tmp_path / "invalid"
    with pytest.raises(ValueError):
        artifacts.publish_preparation(
            path, *payload, trusted_manifest_sha256="0" * 64,
            experiment_id="invalid", procedure_sha256="a" * 64, max_disagreement_percent=20,
        )
    assert not path.exists()


def test_evaluation_failure_leaves_unreceipted_reservation(tmp_path):
    path, payload, receipt = prepared(tmp_path)
    output = tmp_path / "failed"
    with pytest.raises(ValueError):
        artifacts.publish_evaluation(
            path, output, trusted_receipt_sha256=sha256(receipt),
            trusted_manifest_sha256=sha256(payload[0]), max_disagreement_percent=19,
        )
    assert output.exists()
    assert not (output / "receipt.json").exists()
    with pytest.raises(FileExistsError):
        artifacts.publish_evaluation(
            path, output, trusted_receipt_sha256=sha256(receipt),
            trusted_manifest_sha256=sha256(payload[0]), max_disagreement_percent=20,
        )


def test_published_aborted_report_is_not_relabelled(tmp_path, monkeypatch):
    path, payload, receipt = prepared(tmp_path)
    aborted = b'{"completion":"ABORTED","forecast_admission":false}\n'
    monkeypatch.setattr(artifacts, "evaluate_frozen_candidates", lambda *a, **k: aborted)
    output = tmp_path / "aborted"
    result = artifacts.publish_evaluation(
        path, output, trusted_receipt_sha256=sha256(receipt),
        trusted_manifest_sha256=sha256(payload[0]), max_disagreement_percent=20,
    )
    assert artifacts.read_evaluation(output, trusted_receipt_sha256=sha256(result),
        trusted_preparation_receipt_sha256=sha256(receipt)) == aborted


def test_report_tampering_and_duplicate_evaluation(tmp_path):
    path, payload, receipt = prepared(tmp_path)
    output = tmp_path / "report"
    kwargs = dict(trusted_receipt_sha256=sha256(receipt),
                  trusted_manifest_sha256=sha256(payload[0]), max_disagreement_percent=20)
    result = artifacts.publish_evaluation(path, output, **kwargs)
    before = (output / "report.json").read_bytes()
    with pytest.raises(FileExistsError):
        artifacts.publish_evaluation(path, output, **kwargs)
    assert (output / "report.json").read_bytes() == before
    (output / "report.json").write_bytes(before + b" ")
    with pytest.raises(ValueError, match="report SHA"):
        artifacts.read_evaluation(output, trusted_receipt_sha256=sha256(result),
            trusted_preparation_receipt_sha256=sha256(receipt))


def test_linked_artifact_rejected(tmp_path, monkeypatch):
    path, _, receipt = prepared(tmp_path)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink",
                        lambda p: p.name == "frozen.json" or original(p))
    with pytest.raises(ValueError, match="links"):
        artifacts.read_preparation(path, trusted_receipt_sha256=sha256(receipt))

@pytest.mark.parametrize("failure_at", [1, 2])
def test_interrupted_report_has_no_receipt(tmp_path, monkeypatch, failure_at):
    path, payload, receipt = prepared(tmp_path)
    original = artifacts._write
    calls = 0
    def fail(target, content):
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError("interrupted report publication")
        original(target, content)
    monkeypatch.setattr(artifacts, "_write", fail)
    output = tmp_path / "interrupted-report"
    with pytest.raises(OSError):
        artifacts.publish_evaluation(
            path, output, trusted_receipt_sha256=sha256(receipt),
            trusted_manifest_sha256=sha256(payload[0]), max_disagreement_percent=20,
        )
    assert not (output / "receipt.json").exists()


def test_receipt_is_last_and_readback_failure_blocks_it(tmp_path, monkeypatch):
    payload = synthetic_bundle()
    original = artifacts._read
    def corrupted(path):
        content = original(path)
        return content + b"changed" if path.name == "accepted.jsonl" else content
    monkeypatch.setattr(artifacts, "_read", corrupted)
    path = tmp_path / "readback-failure"
    with pytest.raises(OSError, match="readback"):
        artifacts.publish_preparation(
            path, *payload, trusted_manifest_sha256=sha256(payload[0]),
            experiment_id="readback-failure", procedure_sha256="a" * 64,
            max_disagreement_percent=20,
        )
    assert not (path / "receipt.json").exists()
    assert not (path / "frozen.json").exists()

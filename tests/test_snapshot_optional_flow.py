from birzha.application.snapshot import MarketSnapshotService


def test_default_snapshot_does_not_request_subscriber_flow_without_token(monkeypatch) -> None:
    monkeypatch.delenv("MOEX_ALGOPACK_BEARER_TOKEN", raising=False)
    service = MarketSnapshotService.default()
    assert service.flow is None


def test_default_snapshot_enables_flow_when_subscriber_token_exists(monkeypatch) -> None:
    monkeypatch.setenv("MOEX_ALGOPACK_BEARER_TOKEN", "test-token")
    service = MarketSnapshotService.default()
    assert service.flow is not None
    assert service.flow.analytics.authenticated is True

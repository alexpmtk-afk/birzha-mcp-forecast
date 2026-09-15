"""Deterministic action plans for autonomous BIRZHA workflows.

Persistent market-price history is D1-only. Model validation may later stage
H1/M15 on demand, but durable history preparation must never persist intraday
bars. The daily archive refresh is deliberately separate from model validation.
"""

from __future__ import annotations

from datetime import date, timedelta

from birzha.application.history_policy import PERSISTENT_PRICE_TIMEFRAMES
from birzha.domain.orchestration import WorkflowAction, WorkflowStage


CORE_SYMBOLS = ("SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI")
CORE_TIMEFRAMES = PERSISTENT_PRICE_TIMEFRAMES
HISTORY_CHUNK_DAYS = 92
D1_ARCHIVE_CHUNK_DAYS = 366


def build_d1_archive_refresh_actions(
    workflow_id: str,
    archive_start: str,
    archive_end: str,
    source_sha: str,
) -> tuple[WorkflowAction, ...]:
    """Build bounded D1 archive work and one full mirror commit per market.

    A whole 2021+ history is deliberately not fetched in one serverless action.
    Each market is prepared in bounded D1 chunks, then a finalizer verifies all
    chunks, marks the full range and publishes one complete Google mirror. This
    keeps retries small without ever replacing the Google archive with a partial
    chunk.
    """
    start = date.fromisoformat(archive_start[:10])
    finish = date.fromisoformat(archive_end[:10])
    if finish < start:
        raise ValueError("archive_end must not be before archive_start")
    if not source_sha.strip():
        raise ValueError("source_sha must be non-empty")

    chunks = _date_chunks(start.isoformat(), finish.isoformat(), D1_ARCHIVE_CHUNK_DAYS)
    specs: list[tuple[WorkflowStage, str, dict[str, object]]] = []
    for symbol in CORE_SYMBOLS:
        for left, right in chunks:
            specs.append(
                (
                    WorkflowStage.HISTORY_PREPARATION,
                    "HISTORY_SYNC_CHUNK",
                    {
                        "symbol": symbol,
                        "timeframe": "D1",
                        "from_date": left,
                        "till_date": right,
                        "source_sha": source_sha,
                    },
                )
            )
        specs.append(
            (
                WorkflowStage.HISTORY_PREPARATION,
                "HISTORY_FINALIZE_RANGE",
                {
                    "symbol": symbol,
                    "timeframe": "D1",
                    "from_date": start.isoformat(),
                    "till_date": finish.isoformat(),
                    "chunks": [[left, right] for left, right in chunks],
                    "source_sha": source_sha,
                },
            )
        )

    return tuple(
        WorkflowAction(
            action_id=f"{workflow_id}:{index:04d}:{stage.value}",
            workflow_id=workflow_id,
            stage=stage,
            kind=kind,
            sequence=index,
            payload=payload,
        )
        for index, (stage, kind, payload) in enumerate(specs, start=1)
    )


def build_core_validation_actions(
    workflow_id: str,
    development_start: str,
    split_date: str,
    holdout_end: str,
    source_sha: str,
) -> tuple[WorkflowAction, ...]:
    """Build the sealed model-validation workflow.

    Only D1 is prepared durably here. H1/M15 belong to the on-demand validation
    layer and are intentionally absent from durable historical actions.
    """
    chunks = _date_chunks(development_start, holdout_end, HISTORY_CHUNK_DAYS)
    specs: list[tuple[WorkflowStage, str, dict[str, object]]] = []

    for symbol in CORE_SYMBOLS:
        for timeframe in CORE_TIMEFRAMES:
            for left, right in chunks:
                specs.append(
                    (
                        WorkflowStage.HISTORY_PREPARATION,
                        "HISTORY_SYNC_CHUNK",
                        {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "from_date": left,
                            "till_date": right,
                            "source_sha": source_sha,
                        },
                    )
                )
            specs.append(
                (
                    WorkflowStage.HISTORY_PREPARATION,
                    "HISTORY_FINALIZE_RANGE",
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "from_date": development_start,
                        "till_date": holdout_end,
                        "chunks": [[left, right] for left, right in chunks],
                        "source_sha": source_sha,
                    },
                )
            )

    specs.extend(
        [
            (
                WorkflowStage.READINESS_AUDIT,
                "READINESS_AUDIT",
                {
                    "validation_start": development_start,
                    "validation_end": holdout_end,
                    "required_markets": list(CORE_SYMBOLS),
                    "required_timeframes": list(CORE_TIMEFRAMES),
                },
            ),
            (
                WorkflowStage.SEALED_DEVELOPMENT,
                "SEALED_DEVELOPMENT",
                {
                    "development_start": development_start,
                    "split_date": split_date,
                    "holdout_end": holdout_end,
                    "holdout_open": False,
                    "source_sha": source_sha,
                },
            ),
            (
                WorkflowStage.HOLDOUT_EVALUATION,
                "ONE_SHOT_HOLDOUT",
                {
                    "requires_fingerprints": True,
                    "single_use": True,
                    "source_sha": source_sha,
                },
            ),
            (
                WorkflowStage.PROMOTION,
                "PROMOTE_ACCEPTED_SOURCE",
                {
                    "immutable_source_required": True,
                    "requires_green_checks": True,
                    "source_sha": source_sha,
                },
            ),
            (
                WorkflowStage.TEST_DEPLOYMENT,
                "DEPLOY_ACCEPTED_SOURCE_TO_TEST",
                {"immutable_source_required": True, "source_sha": source_sha},
            ),
            (
                WorkflowStage.E2E_VERIFICATION,
                "AUTHENTICATED_E2E",
                {"fail_closed": True, "source_sha": source_sha},
            ),
        ]
    )

    return tuple(
        WorkflowAction(
            action_id=f"{workflow_id}:{index:04d}:{stage.value}",
            workflow_id=workflow_id,
            stage=stage,
            kind=kind,
            sequence=index,
            payload=payload,
        )
        for index, (stage, kind, payload) in enumerate(specs, start=1)
    )


def _date_chunks(start_text: str, end_text: str, chunk_days: int) -> tuple[tuple[str, str], ...]:
    if chunk_days <= 0:
        raise ValueError("chunk_days must be positive")
    start = date.fromisoformat(start_text[:10])
    end = date.fromisoformat(end_text[:10])
    if end < start:
        raise ValueError("end must not be before start")
    chunks: list[tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        right = min(end, cursor + timedelta(days=chunk_days - 1))
        chunks.append((cursor.isoformat(), right.isoformat()))
        cursor = right + timedelta(days=1)
    return tuple(chunks)

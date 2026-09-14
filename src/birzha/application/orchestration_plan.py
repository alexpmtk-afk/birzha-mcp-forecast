"""Deterministic action plan for autonomous core validation.

Persistent historical preparation is deliberately D1-only. H1/M15 remain
on-demand analysis inputs and must never be scheduled as durable YDB history
writes. D1 work is split into bounded calendar chunks so a worker can checkpoint
progress after every successful unit.
"""

from __future__ import annotations

from datetime import date, timedelta

from birzha.application.history_policy import PERSISTENT_PRICE_TIMEFRAMES
from birzha.domain.orchestration import WorkflowAction, WorkflowStage


CORE_SYMBOLS = ("SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI")
CORE_PERSISTENT_TIMEFRAMES = tuple(PERSISTENT_PRICE_TIMEFRAMES)
HISTORY_CHUNK_DAYS = 92


def build_core_validation_actions(
    workflow_id: str,
    development_start: str,
    split_date: str,
    holdout_end: str,
    source_sha: str,
) -> tuple[WorkflowAction, ...]:
    chunks = _date_chunks(development_start, holdout_end, HISTORY_CHUNK_DAYS)
    specs: list[tuple[WorkflowStage, str, dict[str, object]]] = []

    for symbol in CORE_SYMBOLS:
        for timeframe in CORE_PERSISTENT_TIMEFRAMES:
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
                    "persistent_timeframes": list(CORE_PERSISTENT_TIMEFRAMES),
                    "intraday_mode": "H1/M15_ON_DEMAND_NOT_PERSISTED",
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

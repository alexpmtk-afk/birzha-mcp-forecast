"""Safe MCP controls for durable autonomous workflows."""

from __future__ import annotations

from typing import Any

from birzha.application.orchestrator import WorkflowOrchestrator
from birzha.config import Settings
from birzha.domain.orchestration import WorkflowStage
from birzha.storage.orchestration_store import MemoryOrchestrationStore
from birzha.storage.ydb_runtime_orchestration_store import YdbRuntimeOrchestrationStore
from birzha.storage.ydb_state import YdbRuntime


DEVELOPMENT_START = "2021-01-01"
SPLIT_DATE = "2022-12-31"
HOLDOUT_END = "2024-12-31"
CONFIRM_HOLDOUT = "OPEN_FINAL_HOLDOUT"
CONFIRM_PROMOTION = "PROMOTE_ACCEPTED_SOURCE"

_STAGE_RU = {
    "HISTORY_PREPARATION": "Собираю и проверяю исторические данные",
    "READINESS_AUDIT": "Проверяю полноту данных",
    "SEALED_DEVELOPMENT": "Проверяю модель на обучающем периоде",
    "HOLDOUT_APPROVAL": "Жду разрешения на финальную независимую проверку",
    "HOLDOUT_EVALUATION": "Выполняю финальную независимую проверку",
    "PROMOTION_APPROVAL": "Жду разрешения принять проверенную версию",
    "PROMOTION": "Принимаю проверенную версию",
    "TEST_DEPLOYMENT": "Разворачиваю проверенную версию в TEST",
    "E2E_VERIFICATION": "Проверяю всю систему от начала до конца",
    "COMPLETE": "Работа завершена",
}

_STATUS_RU = {
    "RUNNING": "Выполняется",
    "WAITING_APPROVAL": "Нужно решение пользователя",
    "FAILED": "Ошибка — дальнейшее движение остановлено",
    "COMPLETE": "Готово",
}


def install_orchestration_tools(
    mcp: Any,
    *,
    settings: Settings,
    ydb_runtime: YdbRuntime | None,
) -> WorkflowOrchestrator:
    if settings.state_backend == "ydb":
        if ydb_runtime is None:
            raise RuntimeError("YDB runtime is required for durable orchestration")
        store = YdbRuntimeOrchestrationStore(ydb_runtime.pool)
    else:
        store = MemoryOrchestrationStore()
    orchestrator = WorkflowOrchestrator(store)

    @mcp.tool(
        name="workflow.start_core_validation",
        description=(
            "Start the canonical durable six-market validation workflow. "
            "The final holdout and promotion remain protected and require explicit user approval."
        ),
    )
    def workflow_start_core_validation() -> dict[str, object]:
        if settings.source_sha is None:
            return {
                "status": "ERROR",
                "reason": "SOURCE_IDENTITY_MISSING",
                "message": "Сервер не знает точную версию своего кода; запуск остановлен.",
            }
        active = store.list_workflows(active_only=True, limit=100)
        same = [item for item in active if item.kind == "CORE_VALIDATION_V1"]
        if same:
            return {
                "status": "ALREADY_ACTIVE",
                "message": "Основная проверка уже запущена; второй параллельный процесс не создан.",
                "workflow": _present(orchestrator.status(same[0].workflow_id)),
            }
        run, created = orchestrator.start_core_validation(
            development_start=DEVELOPMENT_START,
            split_date=SPLIT_DATE,
            holdout_end=HOLDOUT_END,
            source_sha=settings.source_sha,
        )
        if not created:
            return {
                "status": "ALREADY_EXISTS",
                "message": "Для этой точной версии кода уже существует долговечная цепочка; дубликат не создан.",
                "workflow": _present(orchestrator.status(run.workflow_id)),
            }
        return {
            "status": "STARTED",
            "message": "Автономная цепочка проверки запущена и сохранена в постоянном хранилище.",
            "workflow": _present(orchestrator.status(run.workflow_id)),
        }

    @mcp.tool(
        name="workflow.status",
        description="Return a plain-language durable progress report for one workflow.",
    )
    def workflow_status(workflow_id: str) -> dict[str, object]:
        try:
            return {"status": "PASS", "workflow": _present(orchestrator.status(workflow_id))}
        except KeyError:
            return {
                "status": "NOT_FOUND",
                "workflow_id": workflow_id,
                "message": "Такой процесс не найден в постоянном хранилище.",
            }

    @mcp.tool(
        name="workflow.list",
        description="List durable workflows and their plain-language progress.",
    )
    def workflow_list(active_only: bool = True, limit: int = 20) -> dict[str, object]:
        payload = orchestrator.list(active_only=active_only, limit=limit)
        return {
            "status": "PASS",
            "count": payload["count"],
            "workflows": [_present(item) for item in payload["workflows"]],
        }

    @mcp.tool(
        name="workflow.approve",
        description=(
            "Explicitly approve a protected workflow gate. "
            "Use only after the user has explicitly authorized the exact gate."
        ),
    )
    def workflow_approve(
        workflow_id: str,
        gate: str,
        confirmation: str,
        approval_id: str,
    ) -> dict[str, object]:
        expected = {
            WorkflowStage.HOLDOUT_APPROVAL.value: CONFIRM_HOLDOUT,
            WorkflowStage.PROMOTION_APPROVAL.value: CONFIRM_PROMOTION,
        }.get(gate)
        if expected is None:
            return {
                "status": "ERROR",
                "reason": "UNKNOWN_PROTECTED_GATE",
                "message": "Неизвестный защищённый этап; действие остановлено.",
            }
        if confirmation != expected:
            return {
                "status": "ERROR",
                "reason": "CONFIRMATION_MISMATCH",
                "required_confirmation": expected,
                "message": "Подтверждение не совпало; защищённый этап не открыт.",
            }
        try:
            state = orchestrator.approve(
                workflow_id,
                gate=gate,
                approval_id=approval_id,
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            return {
                "status": "ERROR",
                "reason": type(exc).__name__,
                "message": str(exc)[:1000],
            }
        return {
            "status": "APPROVED",
            "message": "Разрешение зафиксировано; система может продолжить следующий разрешённый этап.",
            "workflow": _present(state),
        }

    return orchestrator


def _present(state: dict[str, object]) -> dict[str, object]:
    stage = str(state.get("stage") or "")
    status = str(state.get("status") or "")
    total = int(state.get("current_stage_total") or 0)
    done = int(state.get("current_stage_completed") or 0)
    progress = f"{done}/{total}" if total else None
    next_action = state.get("next_action")
    next_kind = next_action.get("kind") if isinstance(next_action, dict) else None
    return {
        "workflow_id": state.get("workflow_id"),
        "status": status,
        "status_ru": _STATUS_RU.get(status, status),
        "stage": stage,
        "stage_ru": _STAGE_RU.get(stage, stage),
        "progress": progress,
        "requires_approval": bool(state.get("requires_approval")),
        "next_internal_action": next_kind,
        "last_error": state.get("last_error"),
        "updated_at": state.get("updated_at"),
        "source_sha": (state.get("metadata") or {}).get("source_sha")
        if isinstance(state.get("metadata"), dict)
        else None,
    }

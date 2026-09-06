"""API routes for agent status, action history, and privacy settings."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from flask import Flask, jsonify, request


# In-memory agent state (sufficient for this milestone; no DB required)
_agent_state: Dict[str, Any] = {
    "status": "idle",
    "last_task": None,
    "last_updated": None,
    "pending_confirmation_id": None,
    "lifecycle_id": None,
    "execution_id": None,
    "plan_id": None,
    "session_id": None,
}

_action_history: List[Dict[str, Any]] = []
_MAX_HISTORY = 50

# In-memory store for the latest SANITIZED screenshot (base64 data URL)
# PRIVACY BOUNDARY: when privacy mode is on, only sanitized images are stored.
_latest_screenshot: Dict[str, Any] = {
    "data": None,
    "sanitized": False,
    "screenshot_privacy_report": None,
    "visual_context": None,
    "visual_ui_map": None,
    "action_target": None,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_status(status: str, task: str | None = None) -> None:
    _agent_state["status"] = status
    _agent_state["last_updated"] = _utc_now()
    if task is not None:
        _agent_state["last_task"] = task


def _append_history(item: Dict[str, Any]) -> None:
    _action_history.insert(0, item)
    del _action_history[_MAX_HISTORY:]


def _decision_to_action(decision: Dict[str, Any], steps: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    """Build the extension-facing action object from a decision / planned steps."""
    if not steps:
        return None

    step = steps[0]
    action_type = step.get("type")
    resolved = decision.get("resolved_target") or {}

    if action_type == "click":
        action = {"type": "click", "selector": step.get("selector")}
        if resolved:
            action["target"] = {
                "strategy": resolved.get("strategy", "selector"),
                "selector": step.get("selector"),
                "confidence": resolved.get("confidence"),
                "source": resolved.get("source", "dom"),
            }
        return action

    if action_type == "coordinate_click":
        coords = {
            "x": step.get("x"),
            "y": step.get("y"),
        }
        action = {
            "type": "coordinate_click",
            "x": coords["x"],
            "y": coords["y"],
            "coordinates": coords,
            "source": step.get("source", "visual_ui_map"),
        }
        if resolved:
            action["target"] = {
                "strategy": "coordinates",
                "coordinates": coords,
                "confidence": resolved.get("confidence"),
                "source": resolved.get("source", "dom+vision"),
                "id": resolved.get("id"),
                "box": resolved.get("box"),
            }
        return action

    if action_type == "type":
        return {
            "type": "type",
            "selector": step.get("selector"),
            "text": step.get("text", ""),
        }
    if action_type == "scroll":
        return {
            "type": "scroll",
            "direction": step.get("direction", "down"),
            "amount": step.get("amount", 500),
        }
    return None


def _build_action_target_overlay(
    decision: Dict[str, Any], action: Dict[str, Any] | None
) -> Dict[str, Any] | None:
    """Sanitize action targeting metadata for the Visual Perception Map overlay."""
    if not decision and not action:
        return None
    resolved = (decision or {}).get("resolved_target") or {}
    if not resolved and action:
        resolved = action.get("target") or {}
    if not resolved and not action:
        return None

    overlay: Dict[str, Any] = {
        "strategy": resolved.get("strategy")
        or (
            "coordinates"
            if (action or {}).get("type") == "coordinate_click"
            else "selector"
        ),
        "source": resolved.get("source"),
        "confidence": resolved.get("confidence"),
        "id": resolved.get("id"),
        "type": resolved.get("type"),
        "box": resolved.get("box"),
        "status": (decision or {}).get("status"),
        "reason": (decision or {}).get("reasoning") or (decision or {}).get("reason"),
    }
    if overlay["strategy"] == "coordinates":
        coords = resolved.get("coordinates") or {
            "x": (action or {}).get("x"),
            "y": (action or {}).get("y"),
        }
        if coords and coords.get("x") is not None:
            overlay["click_point"] = {"x": coords["x"], "y": coords["y"]}
    elif resolved.get("box"):
        box = resolved["box"]
        overlay["click_point"] = {
            "x": int(box.get("x", 0) + float(box.get("width", 0)) / 2),
            "y": int(box.get("y", 0) + float(box.get("height", 0)) / 2),
        }
    # Never attach raw text / selectors with secrets
    if resolved.get("selector") and not resolved.get("sensitive"):
        overlay["selector"] = resolved.get("selector")
    return overlay


def _safe_history_enrichment(
    *,
    safety: Dict[str, Any] | None = None,
    confirmation: Dict[str, Any] | None = None,
    verification: Dict[str, Any] | None = None,
    recovery_attempts: List[Dict[str, Any]] | None = None,
    lifecycle_id: str | None = None,
    lifecycle_state: str | None = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if safety:
        out["safety"] = {
            "level": safety.get("level"),
            "category": safety.get("category"),
            "reason": safety.get("reason"),
        }
    if confirmation is not None:
        out["confirmation"] = {
            "required": True,
            "id": confirmation.get("id"),
            "approved": confirmation.get("state") in ("approved", "consumed"),
            "category": confirmation.get("category"),
        }
    if verification is not None:
        out["verification"] = {
            "status": verification.get("status"),
            "reason": verification.get("reason"),
            "signals": verification.get("signals"),
        }
    if recovery_attempts is not None:
        out["recovery_attempts"] = recovery_attempts
    if lifecycle_id:
        out["lifecycle_id"] = lifecycle_id
    if lifecycle_state:
        out["lifecycle_state"] = lifecycle_state
    return out


def register_routes(app: Flask) -> None:
    @app.route("/api/agent/status", methods=["GET"])
    def agent_status():
        from agent.confirmation_manager import get_confirmation_manager

        mgr = get_confirmation_manager()
        pending = mgr.pending_public()
        return jsonify(
            {
                "status": _agent_state["status"],
                "last_task": _agent_state.get("last_task"),
                "last_updated": _agent_state.get("last_updated"),
                "pending_confirmation": pending,
                "lifecycle_id": _agent_state.get("lifecycle_id"),
                "plan_id": _agent_state.get("plan_id"),
                "session_id": _agent_state.get("session_id"),
            }
        )

    @app.route("/api/agent/history", methods=["GET"])
    def agent_history():
        return jsonify({"actions": list(_action_history)})

    @app.route("/api/agent/lifecycle", methods=["GET"])
    def agent_lifecycle():
        from agent.lifecycle_registry import get_lifecycle_registry

        registry = get_lifecycle_registry()
        lifecycle_id = request.args.get("id") or _agent_state.get("lifecycle_id")
        view = registry.public_view(lifecycle_id)
        return jsonify({"lifecycle": view})

    @app.route("/api/agent/pending-confirmation", methods=["GET"])
    def pending_confirmation():
        from agent.confirmation_manager import get_confirmation_manager

        mgr = get_confirmation_manager()
        pending = mgr.pending_public()
        if not pending:
            return jsonify({"status": "none", "confirmation": None})
        return jsonify({"status": "requires_confirmation", "confirmation": pending})

    @app.route("/api/agent/confirm", methods=["POST"])
    def agent_confirm():
        """Approve a pending confirmation; queue stored action for extension claim.

        Frontend never receives selector/coordinates — backend remains source of truth.
        """
        import time as time_mod

        from agent.confirmation_manager import get_confirmation_manager
        from agent.lifecycle_registry import get_lifecycle_registry
        from agent.action_state import InvalidTransitionError
        from agent.approved_action_delivery import get_approved_action_delivery

        payload = request.get_json(force=True) or {}
        confirmation_id = payload.get("confirmation_id") or payload.get("id")
        if not confirmation_id:
            return jsonify({"status": "invalid_confirmation"}), 400

        # Reject any attempt to supply a modified action from the client
        if payload.get("action") or payload.get("selector") or payload.get("coordinates"):
            return (
                jsonify(
                    {
                        "status": "invalid_confirmation",
                        "error": "client_action_modification_forbidden",
                    }
                ),
                400,
            )

        t0 = time_mod.perf_counter()
        mgr = get_confirmation_manager()
        result = mgr.approve(confirmation_id)
        if result["status"] != "approved":
            if result["status"] == "expired":
                _set_status("expired", _agent_state.get("last_task"))
                lifecycle_id = result.get("lifecycle_id") or _agent_state.get(
                    "lifecycle_id"
                )
                registry = get_lifecycle_registry()
                if lifecycle_id and registry.get(lifecycle_id):
                    try:
                        registry.transition(lifecycle_id, "expired", "Confirmation expired")
                    except InvalidTransitionError:
                        pass
            return jsonify(result)

        stored_action = result.get("action") or {}
        tab_id = result.get("tab_id")
        window_id = result.get("window_id")
        lifecycle_id = result.get("lifecycle_id") or _agent_state.get("lifecycle_id")

        delivery = get_approved_action_delivery()
        queued = delivery.create(
            confirmation_id=confirmation_id,
            action=stored_action,
            tab_id=tab_id,
            window_id=window_id,
            lifecycle_id=lifecycle_id,
            task=result.get("task") or _agent_state.get("last_task") or "",
            category=result.get("category") or "",
        )
        execution_id = queued.get("execution_id")

        # Persist execution_id on confirmation record (safe metadata only)
        pending = mgr.get(confirmation_id)
        if pending is not None:
            record = pending.to_record()
            record["execution_id"] = execution_id
            # Mark consumed so confirmation token cannot be reused via consume()
            record["consumed"] = True
            mgr.store.put(confirmation_id, record)

        approval_ms = round((time_mod.perf_counter() - t0) * 1000, 3)

        registry = get_lifecycle_registry()
        life_item_for_plan = None
        if lifecycle_id and registry.get(lifecycle_id):
            try:
                registry.transition(lifecycle_id, "approved", "User approved")
                registry.transition(
                    lifecycle_id, "waiting_for_extension", "Waiting for browser"
                )
                item = registry.get(lifecycle_id)
                life_item_for_plan = item
                if item is not None:
                    item["confirmation"] = {
                        "required": True,
                        "approved": True,
                        "id": confirmation_id,
                    }
                    item["action"] = stored_action
                    item["execution_id"] = execution_id
                    item["tab_id"] = tab_id
                    item["execution_status"] = "waiting_for_extension"
                    perf = item.setdefault("performance", {})
                    perf["confirmation_approval_ms"] = approval_ms
                    if queued.get("record", {}).get("performance"):
                        perf.update(queued["record"]["performance"])
            except InvalidTransitionError:
                pass

        _agent_state["pending_confirmation_id"] = None
        _agent_state["execution_id"] = execution_id
        _set_status("waiting_for_extension", _agent_state.get("last_task"))

        # Multi-step plan: resume waiting_for_confirmation → running
        plan_id = (
            (life_item_for_plan.get("plan_id") if life_item_for_plan else None)
            or _agent_state.get("plan_id")
        )
        if plan_id:
            try:
                from agent.task_orchestrator import get_task_orchestrator

                get_task_orchestrator().on_confirmation_approved(
                    plan_id, confirmation_id
                )
            except Exception:
                pass

        # Enrich matching history row (no raw action details)
        for existing in _action_history:
            if existing.get("confirmation", {}).get("id") == confirmation_id:
                existing["confirmation"] = {
                    "required": True,
                    "approved": True,
                    "id": confirmation_id,
                }
                existing["lifecycle_state"] = "waiting_for_extension"
                existing["execution_status"] = "waiting_for_extension"
                existing["status"] = "waiting_for_extension"
                break

        return jsonify(
            {
                "status": "approved",
                "confirmation_id": confirmation_id,
                "execution_id": execution_id,
                "execution_status": "waiting_for_extension",
                "lifecycle_id": lifecycle_id,
            }
        )

    @app.route("/api/agent/approved-action", methods=["GET"])
    def approved_action():
        """Claim an approved action for the given tab (exactly once)."""
        from agent.approved_action_delivery import get_approved_action_delivery
        from agent.lifecycle_registry import get_lifecycle_registry
        from agent.action_state import InvalidTransitionError

        tab_raw = request.args.get("tab_id")
        if tab_raw is None or tab_raw == "":
            return jsonify({"status": "none"})

        try:
            tab_id = int(tab_raw)
        except (TypeError, ValueError):
            return jsonify({"status": "none"})

        delivery = get_approved_action_delivery()
        claimed = delivery.claim_for_tab(tab_id)
        if claimed.get("status") != "approved":
            return jsonify({"status": "none"})

        lifecycle_id = claimed.get("lifecycle_id") or _agent_state.get("lifecycle_id")
        registry = get_lifecycle_registry()
        if lifecycle_id and registry.get(lifecycle_id):
            try:
                item = registry.get(lifecycle_id)
                life = item["lifecycle"] if item else None
                if life and life.state == "waiting_for_extension":
                    registry.transition(lifecycle_id, "claimed", "Extension claimed action")
                if life and life.state == "claimed":
                    registry.transition(lifecycle_id, "executing", "Extension executing")
                if item is not None:
                    item["execution_status"] = "executing"
                    delivery.mark_executing(claimed["execution_id"])
                    claim_perf = (item.get("performance") or {})
                    # Merge claim timing from delivery record
                    rec = delivery.get(claimed["execution_id"]) or {}
                    if rec.get("performance"):
                        claim_perf.update(rec["performance"])
                        item["performance"] = claim_perf
            except InvalidTransitionError:
                pass

        _set_status("executing", claimed.get("task") or _agent_state.get("last_task"))
        _agent_state["execution_id"] = claimed.get("execution_id")

        return jsonify(
            {
                "status": "approved",
                "execution_id": claimed["execution_id"],
                "confirmation_id": claimed.get("confirmation_id"),
                "lifecycle_id": lifecycle_id,
                "action": claimed.get("action"),
                "task": claimed.get("task") or "",
            }
        )

    @app.route("/api/agent/cancel", methods=["POST"])
    def agent_cancel():
        from agent.confirmation_manager import get_confirmation_manager
        from agent.lifecycle_registry import get_lifecycle_registry
        from agent.action_state import InvalidTransitionError
        from agent.approved_action_delivery import get_approved_action_delivery

        payload = request.get_json(force=True) or {}
        confirmation_id = payload.get("confirmation_id") or payload.get("id")
        if not confirmation_id:
            return jsonify({"status": "invalid_confirmation"}), 400

        mgr = get_confirmation_manager()
        result = mgr.cancel(confirmation_id)
        get_approved_action_delivery().cancel_for_confirmation(confirmation_id)

        registry = get_lifecycle_registry()
        lifecycle_id = _agent_state.get("lifecycle_id")
        pending = mgr.get(confirmation_id)
        if pending and pending.lifecycle_id:
            lifecycle_id = pending.lifecycle_id
        if result["status"] == "cancelled" and lifecycle_id and registry.get(lifecycle_id):
            try:
                registry.transition(lifecycle_id, "cancelled", "User cancelled")
            except InvalidTransitionError:
                pass

        _agent_state["pending_confirmation_id"] = None
        if result["status"] == "cancelled":
            _set_status("cancelled", _agent_state.get("last_task"))
            for existing in _action_history:
                if existing.get("confirmation", {}).get("id") == confirmation_id:
                    existing["confirmation"] = {
                        "required": True,
                        "approved": False,
                        "id": confirmation_id,
                    }
                    existing["status"] = "cancelled"
                    existing["lifecycle_state"] = "cancelled"
                    break

            plan_id = _agent_state.get("plan_id")
            if lifecycle_id:
                life = registry.get(lifecycle_id)
                if life and life.get("plan_id"):
                    plan_id = life.get("plan_id")
            if plan_id:
                try:
                    from agent.task_orchestrator import get_task_orchestrator

                    get_task_orchestrator().on_confirmation_cancelled(
                        plan_id, confirmation_id
                    )
                except Exception:
                    pass

        return jsonify(result)

    @app.route("/api/agent/execution", methods=["POST"])
    def agent_execution():
        """Receive post-execution history updates from the extension.

        Updates the most recent matching task entry when present so the
        dashboard shows one row per agent step (decide → execute).
        Optionally verifies before/after sanitized page states.

        When execution_id / confirmation_id are present, validates tab
        correlation and rejects duplicate / mismatched reports.
        """
        import time as time_mod

        from agent.action_verifier import ActionVerifier
        from agent.lifecycle_registry import get_lifecycle_registry
        from agent.action_state import InvalidTransitionError
        from agent.recovery_engine import RecoveryEngine
        from agent.approved_action_delivery import get_approved_action_delivery

        payload = request.get_json(force=True) or {}
        t_roundtrip0 = time_mod.perf_counter()
        execution = payload.get("execution")
        # Sanitize execution feedback — never store typed secrets
        safe_execution = None
        if isinstance(execution, dict):
            safe_execution = {
                "status": execution.get("status")
                or ("success" if execution.get("success") else "failed"),
                "strategy": execution.get("strategy"),
                "coordinates": execution.get("coordinates"),
                "target_found": execution.get("target_found"),
                "element_tag": execution.get("element_tag")
                or (execution.get("target") or {}).get("tag"),
                "element_role": (execution.get("target") or {}).get("role"),
                "execution_time_ms": execution.get("execution_time_ms"),
                "action": execution.get("action"),
                "reason": execution.get("reason") or execution.get("error"),
            }

        execution_id = payload.get("execution_id")
        confirmation_id = payload.get("confirmation_id") or (
            (payload.get("confirmation") or {}).get("id")
        )
        tab_id = payload.get("tab_id")
        correlation = None
        delivery = get_approved_action_delivery()

        # Strict correlation when bridging a confirmed action
        if execution_id or confirmation_id:
            if not execution_id or not confirmation_id:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": "execution_correlation_required",
                            "detail": "execution_id and confirmation_id are required together",
                        }
                    ),
                    400,
                )
            report_status = (
                (safe_execution or {}).get("status")
                or payload.get("status")
                or "failed"
            )
            # Milestone 5B: persistent execution idempotency when durable store is active
            idempotency_key = payload.get("idempotency_key")
            if getattr(delivery, "_repo", None) is not None:
                try:
                    from storage.repositories.execution_repository import (
                        ExecutionRepository,
                    )

                    exec_repo = ExecutionRepository(delivery._db)
                    idem = exec_repo.try_accept(
                        execution_id=execution_id,
                        confirmation_id=confirmation_id,
                        lifecycle_id=payload.get("lifecycle_id")
                        or _agent_state.get("lifecycle_id"),
                        session_id=_agent_state.get("session_id"),
                        tab_id=int(tab_id) if tab_id is not None else None,
                        idempotency_key=idempotency_key,
                        report_status=report_status,
                        safe_result=safe_execution,
                    )
                    if idem.get("status") == "duplicate_execution":
                        return jsonify(
                            {
                                "ok": False,
                                "error": "duplicate_execution",
                                "status": "duplicate_execution",
                                "execution_id": execution_id,
                                "confirmation_id": confirmation_id,
                                "performance": (idem.get("record") or {}).get(
                                    "performance"
                                )
                                or {},
                            }
                        ), 400
                except Exception:
                    pass

            correlation = delivery.report_execution(
                execution_id=execution_id,
                confirmation_id=confirmation_id,
                tab_id=tab_id,
                status=report_status,
                result=safe_execution or execution,
            )
            if not correlation.get("ok"):
                err = correlation.get("error") or "correlation_failed"
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": err,
                            "status": err,
                            "execution_id": execution_id,
                            "confirmation_id": confirmation_id,
                        }
                    ),
                    400,
                )

        verification_payload = None
        pre_state = payload.get("pre_action_state") or payload.get("before")
        post_state = payload.get("post_action_state") or payload.get("after")
        action_type = (
            payload.get("action")
            or (safe_execution or {}).get("action")
            or "click"
        )
        if isinstance(action_type, dict):
            action_type = action_type.get("type") or "click"

        registry = get_lifecycle_registry()
        lifecycle_id = (
            payload.get("lifecycle_id")
            or (correlation or {}).get("lifecycle_id")
            or _agent_state.get("lifecycle_id")
        )
        life_item = registry.get(lifecycle_id) if lifecycle_id else None

        if life_item is not None and (execution_id or safe_execution):
            try:
                life: Any = life_item["lifecycle"]
                if life.state in ("claimed", "waiting_for_extension"):
                    if life.state == "waiting_for_extension":
                        registry.transition(lifecycle_id, "claimed", "Late claim")
                    if life.state == "claimed":
                        registry.transition(lifecycle_id, "executing", "Executing")
                if life.state == "executing":
                    registry.transition(lifecycle_id, "executed", "Action executed")
            except InvalidTransitionError:
                pass

        t_verify0 = time_mod.perf_counter()
        if pre_state is not None and post_state is not None:
            verifier = ActionVerifier()
            exec_ok = None
            if safe_execution:
                exec_ok = safe_execution.get("status") == "success"
            vresult = verifier.verify(
                pre_state,
                post_state,
                action_type=str(action_type),
                execution_success=exec_ok,
            )
            verification_payload = vresult.to_dict()
            if life_item is not None:
                try:
                    life = life_item["lifecycle"]
                    if life.state == "executing":
                        registry.transition(lifecycle_id, "executed", "Action executed")
                    if life.state == "executed":
                        registry.transition(lifecycle_id, "verifying", "Verifying")
                    if vresult.status == "success":
                        registry.transition(lifecycle_id, "success", vresult.reason)
                    elif vresult.status == "unclear":
                        registry.transition(lifecycle_id, "unclear", vresult.reason)
                    else:
                        registry.transition(lifecycle_id, "failed", vresult.reason)
                except InvalidTransitionError:
                    pass
                life_item["verification"] = verification_payload
                perf = life_item.setdefault("performance", {})
                perf["verification_analysis_ms"] = vresult.analysis_ms
                perf["verification_ms"] = round(
                    (time_mod.perf_counter() - t_verify0) * 1000, 3
                )
                if isinstance(post_state, dict) and post_state.get("capture_ms") is not None:
                    perf["verification_snapshot_ms"] = post_state.get("capture_ms")
                if correlation and correlation.get("performance"):
                    perf.update(correlation["performance"])
                perf["execution_result_roundtrip_ms"] = round(
                    (time_mod.perf_counter() - t_roundtrip0) * 1000, 3
                )
                if life_item.get("started_perf") is not None:
                    perf["total_confirm_to_success_ms"] = round(
                        (time_mod.perf_counter() - life_item["started_perf"]) * 1000, 3
                    )
        elif life_item is not None and safe_execution:
            # No verify snapshots — still close lifecycle on failure/success
            try:
                life = life_item["lifecycle"]
                if life.state == "executed":
                    if safe_execution.get("status") == "success":
                        registry.transition(lifecycle_id, "verifying", "No snapshot verify")
                        registry.transition(lifecycle_id, "success", "Executed")
                    else:
                        registry.transition(lifecycle_id, "failed", "Execution failed")
                elif life.state == "executing":
                    registry.transition(lifecycle_id, "executed", "Action executed")
                    if safe_execution.get("status") == "success":
                        registry.transition(lifecycle_id, "verifying", "No snapshot verify")
                        registry.transition(lifecycle_id, "success", "Executed")
                    else:
                        registry.transition(lifecycle_id, "failed", "Execution failed")
            except InvalidTransitionError:
                pass
            perf = life_item.setdefault("performance", {})
            if correlation and correlation.get("performance"):
                perf.update(correlation["performance"])
            perf["execution_result_roundtrip_ms"] = round(
                (time_mod.perf_counter() - t_roundtrip0) * 1000, 3
            )

        # Controlled recovery when verification failed/unclear
        recovery_info = None
        if (
            verification_payload
            and verification_payload.get("status") in ("failed", "unclear")
            and life_item is not None
            and payload.get("request_recovery")
        ):
            engine: RecoveryEngine = life_item.get("recovery_engine") or RecoveryEngine()
            life_item["recovery_engine"] = engine
            safety = life_item.get("safety") or {}
            conf = life_item.get("confirmation") or {}
            plan = engine.plan_recovery(
                original_action=life_item.get("action"),
                verification_status=verification_payload["status"],
                safety_level=safety.get("level") or "safe",
                confirmation_required=bool(conf.get("required")),
                confirmation_valid=bool(conf.get("approved")),
                re_resolved_action=payload.get("re_resolved_action"),
                fused_action=payload.get("fused_action"),
                coordinate_action=payload.get("coordinate_action"),
                reason=verification_payload.get("reason") or "",
            )
            recovery_info = plan.to_dict()
            life_item["recovery_attempts"] = engine.history()
            if plan.allowed and not plan.stop:
                try:
                    registry.transition(
                        lifecycle_id, "recovering", plan.strategy
                    )
                except InvalidTransitionError:
                    pass

        item = {
            "timestamp": payload.get("timestamp") or _utc_now(),
            "task": payload.get("task"),
            "privacy_redactions": payload.get("privacy_redactions", 0),
            "action": payload.get("action"),
            "status": payload.get("status", "unknown"),
            "reason": payload.get("reason"),
            "phase": "action_executed",
            "strategy": payload.get("strategy"),
            "source": payload.get("source"),
            "confidence": payload.get("confidence"),
            "execution": safe_execution,
        }
        if execution_id:
            item["execution_id"] = execution_id
        if confirmation_id:
            item["confirmation"] = {
                "required": True,
                "approved": True,
                "id": confirmation_id,
            }
        if verification_payload:
            item["verification"] = {
                "status": verification_payload.get("status"),
                "reason": verification_payload.get("reason"),
                "signals": verification_payload.get("signals"),
            }
            if verification_payload["status"] == "success":
                item["status"] = "success"
            elif verification_payload["status"] == "failed":
                item["status"] = "verification_failed"
            else:
                item["status"] = "verification_unclear"
        if recovery_info:
            item["recovery"] = recovery_info
        if life_item is not None:
            item["recovery_attempts"] = len(life_item.get("recovery_attempts") or [])
            item["lifecycle_id"] = lifecycle_id
            item["lifecycle_state"] = life_item["lifecycle"].state

        # Enrich overlay with execution result
        overlay = _latest_screenshot.get("action_target")
        if isinstance(overlay, dict) and safe_execution:
            overlay = dict(overlay)
            overlay["execution"] = {
                "status": safe_execution.get("status"),
                "element_tag": safe_execution.get("element_tag"),
                "execution_time_ms": safe_execution.get("execution_time_ms"),
            }
            if verification_payload:
                overlay["verification"] = {
                    "status": verification_payload.get("status"),
                }
            _latest_screenshot["action_target"] = overlay

        updated = False
        for existing in _action_history:
            if existing.get("task") == item["task"] and existing.get("phase") == "action_decided":
                # Preserve safety / confirmation from decide phase
                merged = dict(existing)
                merged.update(item)
                if existing.get("safety") and not item.get("safety"):
                    merged["safety"] = existing["safety"]
                if existing.get("confirmation") and not item.get("confirmation"):
                    merged["confirmation"] = existing["confirmation"]
                existing.clear()
                existing.update(merged)
                updated = True
                break
        if not updated:
            _append_history(item)

        final_status = item["status"] if item["status"] else "idle"
        _set_status(final_status, item.get("task"))
        resp: Dict[str, Any] = {"ok": True, "execution": safe_execution}
        if verification_payload:
            resp["verification"] = verification_payload
        if recovery_info:
            resp["recovery"] = recovery_info
        if execution_id:
            resp["execution_id"] = execution_id
        if life_item is not None:
            resp["lifecycle_state"] = life_item["lifecycle"].state
            resp["performance"] = dict(life_item.get("performance") or {})

        # Advance multi-step plan when this lifecycle belongs to a plan step
        plan_id = (life_item or {}).get("plan_id") if life_item else None
        plan_id = plan_id or payload.get("plan_id") or _agent_state.get("plan_id")
        if plan_id:
            try:
                from agent.task_orchestrator import get_task_orchestrator

                orch = get_task_orchestrator()
                orch.mark_step_executing(plan_id, lifecycle_id)
                vstat = (verification_payload or {}).get("status")
                exec_ok = (safe_execution or {}).get("status") == "success"
                if not vstat:
                    vstat = "success" if exec_ok else "failed"
                plan_result = orch.on_step_execution_reported(
                    plan_id,
                    lifecycle_id=lifecycle_id,
                    verification_status=vstat,
                    execution_success=exec_ok,
                    recovery_attempted=bool(recovery_info and recovery_info.get("allowed")),
                    page=payload.get("page")
                    or (payload.get("post_action_state") or {}).get("page"),
                    safe_page_state=payload.get("post_action_state")
                    or payload.get("after"),
                )
                resp["plan"] = plan_result.get("plan")
                resp["plan_status"] = plan_result.get("status")
                resp["plan_id"] = plan_id
            except Exception:
                pass

        return jsonify(resp)

    @app.route("/api/agent/step", methods=["POST"])
    def agent_step():
        # Lazy-load agent modules only when needed
        import time as time_mod

        from agent.perception import Perception
        from agent.decision_engine import DecisionEngine
        from agent.action_planner import ActionPlanner
        from agent.browser_controller import BrowserController
        from agent.action_safety import ActionRiskClassifier
        from agent.confirmation_manager import get_confirmation_manager
        from agent.action_verifier import ActionVerifier
        from agent.lifecycle_registry import get_lifecycle_registry
        from agent.action_state import InvalidTransitionError

        perception = Perception()
        decision_engine = DecisionEngine()
        planner = ActionPlanner()
        controller = BrowserController()
        classifier = ActionRiskClassifier()
        verifier = ActionVerifier()
        confirm_mgr = get_confirmation_manager()
        registry = get_lifecycle_registry()

        payload = request.get_json(force=True) or {}

        # Prefer new schema; fall back to legacy fields
        task = payload.get("task") or payload.get("goal") or ""
        page = payload.get("page") or payload.get("dom_snapshot") or {}
        page_text = payload.get("page_text") or (
            page.get("visibleText") if isinstance(page, dict) else ""
        )
        privacy_report = payload.get("privacy_report") or {}
        visual_context = payload.get("visual_context")
        if visual_context is None:
            visual_context = _latest_screenshot.get("visual_context")

        visual_ui_map = payload.get("visual_ui_map")
        if visual_ui_map is None:
            visual_ui_map = _latest_screenshot.get("visual_ui_map")

        # Safe browser context for confirmation → extension delivery
        tab_id = payload.get("tab_id")
        window_id = payload.get("window_id")
        try:
            tab_id = int(tab_id) if tab_id is not None else None
        except (TypeError, ValueError):
            tab_id = None
        try:
            window_id = int(window_id) if window_id is not None else None
        except (TypeError, ValueError):
            window_id = None

        # Validate / gate unsafe visual UI maps before perception
        if visual_ui_map is not None:
            from vision.visual_metadata_validator import (
                enforce_privacy_on_map,
                validate_visual_ui_map,
            )

            # Reject payloads that smuggle raw image fields alongside the map
            if payload.get("raw_image") or payload.get("original_image"):
                return (
                    jsonify(
                        {
                            "status": "rejected",
                            "error": "raw_screenshot_blocked",
                            "detail": "Raw image fields are not accepted on /agent/step.",
                        }
                    ),
                    400,
                )

            enforced = enforce_privacy_on_map(visual_ui_map)
            is_safe, reasons = validate_visual_ui_map(enforced)
            if not is_safe or not (enforced or {}).get("privacy_safe", False):
                # Ignore unsafe maps (do not transmit into the agent)
                visual_ui_map = None
            else:
                visual_ui_map = enforced

        # PRIVACY NOTE: `page` must already be sanitized by the extension.
        # visual_context / visual_ui_map are safe metadata only — never raw pixels.
        # Do not log raw page contents.

        lifecycle = registry.create(task=task)
        lifecycle_id = lifecycle.lifecycle_id
        _agent_state["lifecycle_id"] = lifecycle_id
        life_item = registry.get(lifecycle_id)
        assert life_item is not None

        _set_status("perceiving", task)
        observation = perception.observe(
            page,
            page_text or "",
            visual_context=visual_context,
            visual_ui_map=visual_ui_map,
        )

        _set_status("deciding", task)
        decision = decision_engine.decide_next_action(
            observation, task, history=list(_action_history)
        )

        # Ensure safety classification is present
        safety = decision.get("safety")
        if not safety:
            safety = classifier.classify_decision(decision, task).to_dict()
            decision["safety"] = safety
        life_item["safety"] = {
            "level": safety.get("level"),
            "category": safety.get("category"),
            "reason": safety.get("reason"),
        }
        life_item["performance"]["safety_classification_ms"] = safety.get(
            "classification_ms", 0
        )

        try:
            registry.transition(lifecycle_id, "resolved", "Target / decision resolved")
        except InvalidTransitionError:
            pass

        status = decision.get("status") or "no_action"
        confirmation_view = None

        # Capture safe pre-action state (always — useful for later verify)
        pre_state = verifier.capture_pre_action_state(
            page if isinstance(page, dict) else {},
            target=decision.get("resolved_target"),
            safe_page_state=payload.get("safe_page_state"),
        )
        life_item["pre_action_state"] = pre_state.to_dict()
        life_item["performance"]["pre_action_snapshot_ms"] = pre_state.capture_ms

        # Confirmation path
        if status == "requires_confirmation" or safety.get("level") == "confirmation_required":
            proposed = decision.get("proposed_action")
            # Build stored action from proposed or resolved target
            stored = proposed
            if not stored and decision.get("resolved_target"):
                stored = DecisionEngine._proposed_from_decision(
                    {
                        **decision,
                        "action": "click",
                        "target": (decision.get("resolved_target") or {}).get("selector"),
                    }
                )
            if not stored:
                stored = {"type": "click", "selector": None, "pending": True}

            resolved = decision.get("resolved_target") or {}
            target_meta = {
                "label": resolved.get("text") or "Destructive control",
                "type": resolved.get("type") or "button",
                "source": resolved.get("source") or "dom",
                "confidence": resolved.get("confidence"),
                "text": resolved.get("text"),
            }
            t_conf = time_mod.perf_counter()
            created = confirm_mgr.create(
                action=stored,
                category=safety.get("category") or "unknown",
                reason=safety.get("reason")
                or decision.get("reasoning")
                or "Confirmation required",
                target=target_meta,
                task=task,
                lifecycle_id=lifecycle_id,
                tab_id=tab_id,
                window_id=window_id,
            )
            confirmation_view = created["confirmation"]
            life_item["performance"]["confirmation_creation_ms"] = created.get(
                "performance", {}
            ).get("confirmation_creation_ms") or round(
                (time_mod.perf_counter() - t_conf) * 1000, 3
            )
            life_item["confirmation"] = {
                "required": True,
                "approved": False,
                "id": confirmation_view["id"],
                "category": confirmation_view.get("category"),
            }
            if tab_id is not None:
                life_item["tab_id"] = tab_id
            if window_id is not None:
                life_item["window_id"] = window_id
            try:
                registry.transition(
                    lifecycle_id, "requires_confirmation", confirmation_view.get("reason") or ""
                )
            except InvalidTransitionError:
                pass
            _agent_state["pending_confirmation_id"] = confirmation_view["id"]
            status = "requires_confirmation"
            steps: List[Dict[str, Any]] = []
            action = None
        elif safety.get("level") == "blocked" or status == "blocked":
            try:
                registry.transition(lifecycle_id, "blocked", safety.get("reason") or "")
            except InvalidTransitionError:
                pass
            steps = []
            action = None
            status = "no_action"
        else:
            steps = planner.plan(decision)
            action = _decision_to_action(decision, steps)
            status = decision.get("status") or ("success" if action else "no_action")
            if action:
                life_item["action"] = action
                try:
                    # Safe actions skip confirmation → executing readiness
                    registry.transition(lifecycle_id, "executing", "Safe action ready")
                except InvalidTransitionError:
                    try:
                        registry.transition(lifecycle_id, "approved", "Safe auto-approve")
                        registry.transition(lifecycle_id, "executing", "Safe action ready")
                    except InvalidTransitionError:
                        pass

        results = (
            controller.execute(steps)
            if status not in ("requires_confirmation",) and steps
            else []
        )

        reason = decision.get("reasoning") or decision.get("reason")
        resolved = decision.get("resolved_target") or {}

        # Store sanitized action overlay for Visual Perception Map
        overlay = _build_action_target_overlay(decision, action)
        _latest_screenshot["action_target"] = overlay

        life_item["strategy"] = resolved.get("strategy") or (
            (action or {}).get("target") or {}
        ).get("strategy")
        life_item["source"] = resolved.get("source") or (
            (action or {}).get("target") or {}
        ).get("source")
        life_item["confidence"] = resolved.get("confidence") or (
            (action or {}).get("target") or {}
        ).get("confidence")
        if decision.get("performance"):
            life_item["performance"].update(decision["performance"])

        history_item = {
            "timestamp": _utc_now(),
            "task": task,
            "privacy_redactions": privacy_report.get("total_redactions", 0),
            "action": action.get("type") if action else (
                (decision.get("proposed_action") or {}).get("type")
                if status == "requires_confirmation"
                else None
            ),
            "status": status,
            "reason": reason,
            "phase": "action_decided",
            "strategy": life_item.get("strategy"),
            "source": life_item.get("source"),
            "confidence": life_item.get("confidence"),
            "performance": life_item.get("performance"),
            "lifecycle_id": lifecycle_id,
            "lifecycle_state": life_item["lifecycle"].state,
            "recovery_attempts": 0,
        }
        history_item.update(
            _safe_history_enrichment(
                safety=life_item.get("safety"),
                confirmation=confirmation_view,
                lifecycle_id=lifecycle_id,
                lifecycle_state=life_item["lifecycle"].state,
            )
        )
        if confirmation_view:
            history_item["confirmation"] = {
                "required": True,
                "approved": False,
                "id": confirmation_view["id"],
                "category": confirmation_view.get("category"),
            }
        _append_history(history_item)
        _set_status(
            "requires_confirmation"
            if status == "requires_confirmation"
            else ("action_decided" if action else "no_action"),
            task,
        )

        # Clean extension-facing response + backward-compatible fields
        response = {
            "status": status,
            "action": action,
            "reason": reason,
            "privacy_report": privacy_report,
            "visual_context": observation.get("visual_context"),
            "visual_ui_map": observation.get("visual_ui_map"),
            "action_target": overlay,
            "safety": life_item.get("safety"),
            "confirmation": confirmation_view,
            "lifecycle_id": lifecycle_id,
            "lifecycle": registry.public_view(lifecycle_id),
            "pre_action_state": pre_state.to_dict(),
            # Backward compatible
            "observation": {
                "ui_elements": observation.get("ui_elements"),
                "sanitized_text": observation.get("sanitized_text"),
                "privacy_findings_count": len(observation.get("privacy_findings") or []),
                "visual_context": observation.get("visual_context"),
                "visual_ui_map": observation.get("visual_ui_map"),
                "privacy_safe": observation.get("privacy_safe", True),
            },
            "decision": decision,
            "results": results,
        }
        return jsonify(response)

    # ------------------------------------------------------------------
    # Milestone 4B — multi-step task plan API
    # ------------------------------------------------------------------

    def _parse_browser_ids(payload: Dict[str, Any]):
        tab_id = payload.get("tab_id")
        window_id = payload.get("window_id")
        try:
            tab_id = int(tab_id) if tab_id is not None else None
        except (TypeError, ValueError):
            tab_id = None
        try:
            window_id = int(window_id) if window_id is not None else None
        except (TypeError, ValueError):
            window_id = None
        return tab_id, window_id

    def _sanitize_plan_visual_map(payload: Dict[str, Any]):
        visual_ui_map = payload.get("visual_ui_map")
        if visual_ui_map is None:
            visual_ui_map = _latest_screenshot.get("visual_ui_map")
        if visual_ui_map is None:
            return None
        if payload.get("raw_image") or payload.get("original_image"):
            return "RAW_BLOCKED"
        from vision.visual_metadata_validator import (
            enforce_privacy_on_map,
            validate_visual_ui_map,
        )

        enforced = enforce_privacy_on_map(visual_ui_map)
        is_safe, _reasons = validate_visual_ui_map(enforced)
        if not is_safe or not (enforced or {}).get("privacy_safe", False):
            return None
        return enforced

    @app.route("/api/agent/plan", methods=["POST"])
    def agent_plan_create():
        """Create a multi-step plan and optionally resolve the first step."""
        from agent.task_orchestrator import get_task_orchestrator

        payload = request.get_json(force=True) or {}
        goal = (payload.get("goal") or payload.get("task") or "").strip()
        if not goal:
            return jsonify({"status": "invalid", "error": "goal_required"}), 400

        tab_id, window_id = _parse_browser_ids(payload)
        visual_ui_map = _sanitize_plan_visual_map(payload)
        if visual_ui_map == "RAW_BLOCKED":
            return (
                jsonify(
                    {
                        "status": "rejected",
                        "error": "raw_screenshot_blocked",
                    }
                ),
                400,
            )

        orch = get_task_orchestrator()
        operator_replan_approval = bool(
            payload.get("operator_replan_approval")
            or payload.get("require_replan_approval")
        )
        plan = orch.create_plan(goal, tab_id=tab_id, window_id=window_id)
        session = None
        session_id = None
        try:
            from agent.session_manager import get_session_manager
            from agent.session import SESSION_RUNNING

            sm = get_session_manager()
            if sm.storage_available():
                session = sm.create_session(
                    goal=goal,
                    plan=plan,
                    tab_id=tab_id,
                    window_id=window_id,
                    operator_replan_approval=operator_replan_approval,
                )
                session_id = session.session_id
                _agent_state["session_id"] = session_id
                if plan.status not in ("unsupported", "cancelled", "failed"):
                    if session.can_transition(SESSION_RUNNING):
                        session.transition(SESSION_RUNNING, "Plan created")
                        sm.persist_session(session)
        except Exception:
            # Plan remains in-memory; session APIs will fail closed if storage is down
            session = None
            session_id = None

        _agent_state["plan_id"] = plan.plan_id
        _set_status(plan.status, goal)

        if plan.status == "unsupported":
            return jsonify(
                {
                    "status": "unsupported",
                    "plan_id": plan.plan_id,
                    "session_id": session_id,
                    "reason": plan.unsupported_reason,
                    "plan": plan.public_view(),
                    "action": None,
                }
            )

        page = payload.get("page") or payload.get("dom_snapshot")
        # If no page yet, return plan only — extension will resume with snapshot
        if not page:
            return jsonify(
                {
                    "status": "created",
                    "plan_id": plan.plan_id,
                    "session_id": session_id,
                    "plan": plan.public_view(),
                    "session": session.public_view() if session else None,
                    "action": None,
                }
            )

        page_text = payload.get("page_text") or (
            page.get("visibleText") if isinstance(page, dict) else ""
        )
        visual_context = payload.get("visual_context")
        if visual_context is None:
            visual_context = _latest_screenshot.get("visual_context")

        result = orch.resolve_current_step(
            plan.plan_id,
            page=page if isinstance(page, dict) else {},
            page_text=page_text or "",
            visual_context=visual_context,
            visual_ui_map=visual_ui_map,
            privacy_report=payload.get("privacy_report") or {},
            safe_page_state=payload.get("safe_page_state"),
            wait_elapsed_ms=payload.get("wait_elapsed_ms"),
        )

        # Overlay + history for dashboard
        action = result.get("action")
        if result.get("decision"):
            overlay = _build_action_target_overlay(result["decision"], action)
            _latest_screenshot["action_target"] = overlay
            result["action_target"] = overlay

        if result.get("lifecycle_id"):
            _agent_state["lifecycle_id"] = result["lifecycle_id"]
        if result.get("confirmation"):
            _agent_state["pending_confirmation_id"] = result["confirmation"].get("id")
            _set_status("requires_confirmation", goal)
        elif action:
            _set_status("action_decided", goal)
        else:
            _set_status(result.get("status") or plan.status, goal)

        history_item = {
            "timestamp": _utc_now(),
            "task": goal,
            "plan_id": plan.plan_id,
            "action": (action or {}).get("type") if action else None,
            "status": result.get("status"),
            "phase": "plan_step",
            "lifecycle_id": result.get("lifecycle_id"),
            "privacy_redactions": (payload.get("privacy_report") or {}).get(
                "total_redactions", 0
            ),
        }
        if result.get("safety"):
            history_item["safety"] = {
                "level": result["safety"].get("level"),
                "category": result["safety"].get("category"),
                "reason": result["safety"].get("reason"),
            }
        if result.get("confirmation"):
            history_item["confirmation"] = {
                "required": True,
                "approved": False,
                "id": result["confirmation"].get("id"),
                "category": result["confirmation"].get("category"),
            }
        _append_history(history_item)

        result["session_id"] = session_id
        if session:
            result["session"] = session.public_view()
        return jsonify(result)

    @app.route("/api/agent/plan/<plan_id>", methods=["GET"])
    def agent_plan_status(plan_id: str):
        from agent.task_orchestrator import get_task_orchestrator

        orch = get_task_orchestrator()
        plan = orch.get_plan(plan_id)
        if not plan:
            return jsonify({"status": "not_found", "plan_id": plan_id}), 404
        view = plan.public_view()
        return jsonify(
            {
                "status": plan.status,
                "plan_id": plan_id,
                "plan": view,
                "version": view.get("version"),
                "replan_count": view.get("replan_count"),
                "last_replan_reason": view.get("last_replan_reason"),
                "page_change": view.get("page_change"),
                "requires_user_intervention": view.get("requires_user_intervention"),
                "intervention_reason": view.get("intervention_reason"),
            }
        )

    @app.route("/api/agent/plan/cancel", methods=["POST"])
    def agent_plan_cancel():
        from agent.task_orchestrator import get_task_orchestrator

        payload = request.get_json(force=True) or {}
        plan_id = payload.get("plan_id") or _agent_state.get("plan_id")
        if not plan_id:
            return jsonify({"status": "invalid", "error": "plan_id_required"}), 400

        orch = get_task_orchestrator()
        result = orch.cancel_plan(plan_id)
        if result.get("status") == "cancelled":
            _agent_state["pending_confirmation_id"] = None
            _set_status("cancelled", _agent_state.get("last_task"))
        return jsonify(result)

    @app.route("/api/agent/plan/resume", methods=["POST"])
    def agent_plan_resume():
        """Continue current step with fresh page state (or after confirmation)."""
        from agent.task_orchestrator import get_task_orchestrator

        payload = request.get_json(force=True) or {}
        plan_id = payload.get("plan_id") or _agent_state.get("plan_id")
        if not plan_id:
            return jsonify({"status": "invalid", "error": "plan_id_required"}), 400

        visual_ui_map = _sanitize_plan_visual_map(payload)
        if visual_ui_map == "RAW_BLOCKED":
            return (
                jsonify(
                    {
                        "status": "rejected",
                        "error": "raw_screenshot_blocked",
                    }
                ),
                400,
            )

        page = payload.get("page") or payload.get("dom_snapshot") or {}
        page_text = payload.get("page_text") or (
            page.get("visibleText") if isinstance(page, dict) else ""
        )
        visual_context = payload.get("visual_context")
        if visual_context is None:
            visual_context = _latest_screenshot.get("visual_context")

        tab_id, window_id = _parse_browser_ids(payload)
        orch = get_task_orchestrator()
        plan = orch.get_plan(plan_id)
        if plan is not None:
            if tab_id is not None:
                plan.tab_id = tab_id
            if window_id is not None:
                plan.window_id = window_id

        result = orch.resume_plan(
            plan_id,
            page=page if isinstance(page, dict) else {},
            page_text=page_text or "",
            visual_context=visual_context,
            visual_ui_map=visual_ui_map,
            privacy_report=payload.get("privacy_report") or {},
            safe_page_state=payload.get("safe_page_state"),
            wait_elapsed_ms=payload.get("wait_elapsed_ms"),
        )

        action = result.get("action")
        if result.get("decision"):
            overlay = _build_action_target_overlay(result["decision"], action)
            _latest_screenshot["action_target"] = overlay
            result["action_target"] = overlay

        if result.get("lifecycle_id"):
            _agent_state["lifecycle_id"] = result["lifecycle_id"]
        if result.get("confirmation"):
            _agent_state["pending_confirmation_id"] = result["confirmation"].get("id")
            _set_status("requires_confirmation", _agent_state.get("last_task"))
        elif result.get("requires_user_intervention") or result.get("status") == "paused":
            _set_status("paused", _agent_state.get("last_task"))
        elif action:
            _set_status("action_decided", _agent_state.get("last_task"))
        elif result.get("status"):
            _set_status(str(result["status"]), _agent_state.get("last_task"))

        return jsonify(result)

    @app.route("/api/agent/plan/replan", methods=["POST"])
    def agent_plan_replan():
        """Manual adaptive replan — backend authority; client cannot supply steps."""
        from agent.task_orchestrator import get_task_orchestrator

        payload = request.get_json(force=True) or {}
        plan_id = payload.get("plan_id") or _agent_state.get("plan_id")
        if not plan_id:
            return jsonify({"status": "invalid", "error": "plan_id_required"}), 400

        # Reject client-supplied action sequences
        if payload.get("steps") or payload.get("actions") or payload.get("updated_steps"):
            return (
                jsonify(
                    {
                        "status": "rejected",
                        "error": "client_steps_not_allowed",
                    }
                ),
                400,
            )

        visual_ui_map = _sanitize_plan_visual_map(payload)
        if visual_ui_map == "RAW_BLOCKED":
            return (
                jsonify(
                    {
                        "status": "rejected",
                        "error": "raw_screenshot_blocked",
                    }
                ),
                400,
            )

        page = payload.get("page") or payload.get("dom_snapshot") or {}
        if not page:
            return jsonify({"status": "invalid", "error": "fresh_page_required"}), 400

        page_text = payload.get("page_text") or (
            page.get("visibleText") if isinstance(page, dict) else ""
        )
        visual_context = payload.get("visual_context")
        if visual_context is None:
            visual_context = _latest_screenshot.get("visual_context")

        tab_id, window_id = _parse_browser_ids(payload)
        orch = get_task_orchestrator()
        plan = orch.get_plan(plan_id)
        if plan is not None:
            if tab_id is not None:
                plan.tab_id = tab_id
            if window_id is not None:
                plan.window_id = window_id

        result = orch.replan_plan(
            plan_id,
            page=page if isinstance(page, dict) else {},
            page_text=page_text or "",
            visual_context=visual_context,
            visual_ui_map=visual_ui_map,
            privacy_report=payload.get("privacy_report") or {},
            safe_page_state=payload.get("safe_page_state"),
        )
        if result.get("status"):
            _set_status(str(result["status"]), _agent_state.get("last_task"))
        return jsonify(result)

    @app.route("/api/privacy/status", methods=["GET"])
    def privacy_status():
        report = _latest_screenshot.get("screenshot_privacy_report") or {}
        return jsonify(
            {
                "pii_redaction": True,
                "face_blur": True,
                "screenshot_sanitized": bool(_latest_screenshot.get("sanitized")),
                "screenshot_privacy_report": report or None,
                "visual_context": _latest_screenshot.get("visual_context"),
            }
        )

    @app.route("/api/privacy/detect-faces", methods=["POST"])
    def detect_faces_local():
        """Ephemeral on-device face detection (boxes only).

        PRIVACY BOUNDARY:
          - Raw image is decoded in-memory, never stored, never logged.
          - Response contains geometry + confidence only (no crops/embeddings).
          - This is a local privacy worker, not a storage/dashboard endpoint.
        """
        from vision.face_detection import create_face_detector
        from vision.privacy_pipeline import decode_image_input

        payload = request.get_json(force=True) or {}
        # Reject any attempt to also store via this endpoint
        if payload.get("persist") or payload.get("store"):
            return jsonify({"ok": False, "error": "persist_not_allowed"}), 400

        image = payload.get("image")
        min_confidence = float(payload.get("minConfidence", payload.get("min_confidence", 0.6)))
        arr = decode_image_input(image)
        if arr is None:
            return jsonify({"ok": False, "error": "invalid_image", "faces": []}), 400

        detector = create_face_detector(backend="haar", min_confidence=min_confidence)
        faces = detector.detect(arr)
        # Drop image reference ASAP (GC); never assign to module state
        del arr

        return jsonify(
            {
                "ok": True,
                "faces": faces,
                "faces_detected": len(faces),
            }
        )

    @app.route("/api/privacy/sanitize-screenshot", methods=["POST"])
    def sanitize_screenshot_local():
        """Full local privacy pipeline: face detect + unify + redact.

        Returns sanitized image + report + visual_context. Raw input is never stored.
        """
        from vision.privacy_pipeline import process_screenshot_privacy

        payload = request.get_json(force=True) or {}
        if payload.get("persist") or payload.get("store"):
            return jsonify({"ok": False, "error": "persist_not_allowed"}), 400

        result = process_screenshot_privacy(
            payload.get("image"),
            payload.get("dom_boxes") or payload.get("boxes") or [],
            face_detection=payload.get("faceDetection", True) is not False,
            min_confidence=float(
                payload.get("minConfidence", payload.get("min_confidence", 0.6))
            ),
            face_config=payload.get("face_config")
            or {
                "padding_percent": payload.get("paddingPercent", 10),
                "blur_strength": payload.get("blurStrength", "adaptive"),
            },
        )
        if not result.get("ok"):
            return jsonify(result), 400

        # Do not echo face_boxes in HTTP response by default (geometry already
        # reflected in privacy_report.redactions). Keep regions count only.
        return jsonify(
            {
                "ok": True,
                "sanitized_data_url": result["sanitized_data_url"],
                "privacy_report": result["privacy_report"],
                "visual_context": result["visual_context"],
                "timing": result["timing"],
                "region_count": len(result.get("regions") or []),
            }
        )

    @app.route("/api/agent/screenshot", methods=["POST"])
    def receive_screenshot():
        """Receive a screenshot for dashboard display.

        PRIVACY BOUNDARY (Milestone 2A/2B):
        When privacy_mode / sanitized is true, this endpoint must ONLY
        receive an already-redacted image. The extension performs local
        face + PII redaction before this POST. Reject payloads that claim
        privacy mode but omit the sanitized flag.
        """
        payload = request.get_json(force=True) or {}
        image = payload.get("image")
        privacy_mode = payload.get("privacy_mode", True)
        sanitized = payload.get("sanitized", False)
        report = payload.get("screenshot_privacy_report")
        visual_context = payload.get("visual_context")
        visual_ui_map = payload.get("visual_ui_map")
        dom_elements = payload.get("dom_elements") or payload.get("elements")

        # Enforce: privacy-on path requires sanitized=true
        if privacy_mode and not sanitized:
            return (
                jsonify(
                    {
                        "status": "rejected",
                        "error": "raw_screenshot_blocked",
                        "detail": (
                            "Privacy mode requires a locally sanitized screenshot. "
                            "Raw captures must not cross the network boundary."
                        ),
                    }
                ),
                400,
            )

        # Never accept a separate raw_image field
        if payload.get("raw_image") or payload.get("original_image"):
            return (
                jsonify(
                    {
                        "status": "rejected",
                        "error": "raw_screenshot_blocked",
                        "detail": "Original/raw screenshot fields are not accepted.",
                    }
                ),
                400,
            )

        _latest_screenshot["data"] = image
        _latest_screenshot["sanitized"] = bool(sanitized)
        _latest_screenshot["screenshot_privacy_report"] = report
        if visual_context is not None:
            _latest_screenshot["visual_context"] = visual_context
        elif report:
            from vision.visual_metadata import visual_context_from_privacy_report

            _latest_screenshot["visual_context"] = visual_context_from_privacy_report(
                report, screenshot_available=True, privacy_safe=bool(sanitized)
            )

        # Build or accept a privacy-safe Visual UI Map (Milestone 3A)
        if visual_ui_map is not None:
            from vision.visual_metadata_validator import (
                enforce_privacy_on_map,
                validate_visual_ui_map,
            )

            enforced = enforce_privacy_on_map(visual_ui_map)
            is_safe, _ = validate_visual_ui_map(enforced)
            _latest_screenshot["visual_ui_map"] = enforced if is_safe else None
        elif image and (dom_elements is not None or payload.get("build_visual_ui_map")):
            from vision.visual_perception_pipeline import build_visual_ui_map_from_inputs
            from vision.visual_metadata_validator import validate_visual_ui_map

            sensitive = []
            if isinstance(report, dict):
                sensitive = report.get("redactions") or []
            built = build_visual_ui_map_from_inputs(
                image=image if sanitized or not privacy_mode else None,
                dom_elements=dom_elements or [],
                sensitive_regions=sensitive,
            )
            is_safe, _ = validate_visual_ui_map(built)
            _latest_screenshot["visual_ui_map"] = built if is_safe and built.get(
                "privacy_safe", False
            ) else None
        elif visual_ui_map is None and "visual_ui_map" not in payload:
            # Keep previous map unless explicitly cleared
            pass

        return jsonify(
            {
                "status": "received",
                "sanitized": bool(sanitized),
                "total_redactions": (report or {}).get("total_redactions", 0),
                "visual_context": _latest_screenshot.get("visual_context"),
                "visual_ui_map": _latest_screenshot.get("visual_ui_map"),
            }
        )

    @app.route("/api/agent/screenshot", methods=["GET"])
    def get_screenshot():
        """Return the latest stored screenshot (sanitized when privacy is on)."""
        return jsonify(
            {
                "image": _latest_screenshot["data"],
                "sanitized": bool(_latest_screenshot.get("sanitized")),
                "screenshot_privacy_report": _latest_screenshot.get(
                    "screenshot_privacy_report"
                ),
                "visual_context": _latest_screenshot.get("visual_context"),
                "visual_ui_map": _latest_screenshot.get("visual_ui_map"),
                "action_target": _latest_screenshot.get("action_target"),
            }
        )

    @app.route("/api/vision/ui-map", methods=["POST"])
    def build_ui_map():
        """Build a Visual UI Map from a SANITIZED screenshot + DOM geometry.

        PRIVACY BOUNDARY: Input image must already be redacted. Raw captures,
        face crops, and embeddings are rejected. Unsafe maps are not returned
        for agent transmission (privacy_safe=false).
        """
        from vision.visual_perception_pipeline import build_visual_ui_map_from_inputs
        from vision.visual_metadata_validator import validate_visual_ui_map

        payload = request.get_json(force=True) or {}
        if payload.get("raw_image") or payload.get("original_image"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "raw_screenshot_blocked",
                        "privacy_safe": False,
                    }
                ),
                400,
            )

        if payload.get("sanitized") is False and payload.get("privacy_mode", True):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "raw_screenshot_blocked",
                        "detail": "UI map requires a sanitized screenshot.",
                        "privacy_safe": False,
                    }
                ),
                400,
            )

        image = payload.get("image")
        dom_elements = payload.get("dom_elements") or payload.get("elements") or []
        sensitive = payload.get("sensitive_regions") or payload.get("redactions") or []

        ui_map = build_visual_ui_map_from_inputs(
            image=image,
            dom_elements=dom_elements,
            sensitive_regions=sensitive,
            iou_threshold=float(payload.get("iou_threshold", 0.5)),
        )
        is_safe, reasons = validate_visual_ui_map(ui_map)
        ui_map["privacy_safe"] = bool(is_safe and ui_map.get("privacy_safe", True))

        if ui_map["privacy_safe"]:
            _latest_screenshot["visual_ui_map"] = ui_map
        else:
            _latest_screenshot["visual_ui_map"] = None
            ui_map["validation_errors"] = reasons

        return jsonify(
            {
                "ok": ui_map["privacy_safe"],
                "visual_ui_map": ui_map if ui_map["privacy_safe"] else None,
                "privacy_safe": ui_map["privacy_safe"],
                "validation_errors": reasons if not ui_map["privacy_safe"] else [],
                "performance": ui_map.get("performance"),
            }
        )

    # ------------------------------------------------------------------
    # Milestone 5A — Persistent agent sessions & operator control
    # ------------------------------------------------------------------

    @app.route("/api/agent/sessions", methods=["POST"])
    def agent_sessions_create():
        from agent.session import SESSION_RUNNING
        from agent.session_manager import get_session_manager
        from agent.task_orchestrator import get_task_orchestrator
        from storage.database import StorageUnavailableError

        payload = request.get_json(force=True) or {}
        goal = (payload.get("goal") or payload.get("task") or "").strip()
        if not goal:
            return jsonify({"status": "invalid", "error": "goal_required"}), 400

        tab_id, window_id = _parse_browser_ids(payload)
        operator_replan_approval = bool(
            payload.get("operator_replan_approval")
            or payload.get("require_replan_approval")
        )

        try:
            orch = get_task_orchestrator()
            sm = get_session_manager()
            sm.require_storage()
            plan = orch.create_plan(goal, tab_id=tab_id, window_id=window_id)
            session = sm.create_session(
                goal=goal,
                plan=plan,
                tab_id=tab_id,
                window_id=window_id,
                operator_replan_approval=operator_replan_approval,
            )
            if plan.status not in ("unsupported",) and session.can_transition(
                SESSION_RUNNING
            ):
                session.transition(SESSION_RUNNING, "Session started")
                sm.persist_session(session)
            _agent_state["plan_id"] = plan.plan_id
            _agent_state["session_id"] = session.session_id
            _set_status(plan.status, goal)
            return jsonify(
                {
                    "status": "created",
                    "session": session.public_view(),
                    "plan": plan.public_view(),
                    "session_id": session.session_id,
                    "plan_id": plan.plan_id,
                }
            )
        except StorageUnavailableError as exc:
            return jsonify({"status": "storage_unavailable", "error": str(exc)}), 503

    @app.route("/api/agent/sessions", methods=["GET"])
    def agent_sessions_list():
        from agent.session import to_public_session
        from agent.session_manager import get_session_manager
        from storage.database import StorageUnavailableError

        try:
            sm = get_session_manager()
            sm.require_storage()
            active_only = request.args.get("active") in ("1", "true", "yes")
            limit = int(request.args.get("limit") or 50)
            sessions = sm.list_sessions(active_only=active_only, limit=limit)
            return jsonify(
                {"status": "ok", "sessions": [to_public_session(s) for s in sessions]}
            )
        except StorageUnavailableError as exc:
            return jsonify({"status": "storage_unavailable", "error": str(exc)}), 503

    @app.route("/api/agent/sessions/active", methods=["GET"])
    def agent_sessions_active_for_tab():
        from agent.session import to_public_session
        from agent.session_manager import get_session_manager
        from storage.database import StorageUnavailableError

        tab_raw = request.args.get("tab_id")
        try:
            tab_id = int(tab_raw) if tab_raw is not None else None
        except (TypeError, ValueError):
            return jsonify({"status": "invalid", "error": "tab_id_required"}), 400
        if tab_id is None:
            return jsonify({"status": "invalid", "error": "tab_id_required"}), 400
        try:
            sm = get_session_manager()
            sm.require_storage()
            session = sm.get_active_for_tab(tab_id)
            if not session:
                return jsonify({"status": "none", "session": None})
            plan = None
            if session.plan_id:
                plan_obj = sm.load_plan_into_registry(session.plan_id)
                if plan_obj:
                    plan = plan_obj.public_view()
            return jsonify(
                {
                    "status": "ok",
                    "session": to_public_session(session),
                    "plan": plan,
                    "recovery_status": session.recovery_status,
                }
            )
        except StorageUnavailableError as exc:
            return jsonify({"status": "storage_unavailable", "error": str(exc)}), 503

    @app.route("/api/agent/sessions/<session_id>", methods=["GET"])
    def agent_session_get(session_id: str):
        from agent.session_manager import get_session_manager
        from storage.database import StorageUnavailableError

        try:
            sm = get_session_manager()
            sm.require_storage()
            bundle = sm.public_session_bundle(session_id)
            if not bundle:
                return jsonify({"status": "not_found", "session_id": session_id}), 404
            return jsonify({"status": "ok", **bundle})
        except StorageUnavailableError as exc:
            return jsonify({"status": "storage_unavailable", "error": str(exc)}), 503

    @app.route("/api/agent/sessions/<session_id>/pause", methods=["POST"])
    def agent_session_pause(session_id: str):
        from agent.operator_control import get_operator_control

        payload = request.get_json(force=True) or {}
        reason = (payload.get("reason") or "operator_pause")[:200]
        result = get_operator_control().pause_session(session_id, reason=reason)
        code = 200
        if result.get("status") == "not_found":
            code = 404
        elif result.get("status") == "storage_unavailable":
            code = 503
        elif result.get("status") == "invalid_transition":
            code = 409
        return jsonify(result), code

    @app.route("/api/agent/sessions/<session_id>/resume", methods=["POST"])
    def agent_session_resume(session_id: str):
        from agent.operator_control import get_operator_control

        payload = request.get_json(force=True) or {}
        visual_ui_map = _sanitize_plan_visual_map(payload)
        if visual_ui_map == "RAW_BLOCKED":
            return jsonify({"status": "rejected", "error": "raw_screenshot_blocked"}), 400

        page = payload.get("page") or payload.get("dom_snapshot")
        result = get_operator_control().resume_session(
            session_id,
            page=page if isinstance(page, dict) else None,
            page_text=payload.get("page_text") or "",
            visual_context=payload.get("visual_context"),
            visual_ui_map=visual_ui_map,
            privacy_report=payload.get("privacy_report"),
            safe_page_state=payload.get("safe_page_state"),
            execute=payload.get("execute", True),
        )
        sess = result.get("session") or {}
        if sess.get("plan_id"):
            _agent_state["plan_id"] = sess["plan_id"]
        if result.get("status") not in ("not_found", "storage_unavailable"):
            _agent_state["session_id"] = session_id
        code = 200
        if result.get("status") == "not_found":
            code = 404
        elif result.get("status") == "storage_unavailable":
            code = 503
        elif result.get("status") in (
            "cancelled",
            "fresh_perception_required",
            "terminal_session_cannot_resume",
        ):
            code = 409
        return jsonify(result), code

    @app.route("/api/agent/sessions/<session_id>/cancel", methods=["POST"])
    def agent_session_cancel(session_id: str):
        from agent.operator_control import get_operator_control

        payload = request.get_json(force=True) or {}
        reason = (payload.get("reason") or "operator_cancel")[:200]
        result = get_operator_control().cancel_session(session_id, reason=reason)
        if result.get("status") == "cancelled":
            _set_status("cancelled")
        code = 200
        if result.get("status") == "not_found":
            code = 404
        elif result.get("status") == "storage_unavailable":
            code = 503
        return jsonify(result), code

    @app.route("/api/agent/sessions/<session_id>/timeline", methods=["GET"])
    def agent_session_timeline(session_id: str):
        from agent.session_manager import get_session_manager
        from storage.database import StorageUnavailableError

        try:
            sm = get_session_manager()
            sm.require_storage()
            if not sm.get_session(session_id):
                return jsonify({"status": "not_found", "session_id": session_id}), 404
            return jsonify(
                {
                    "status": "ok",
                    "session_id": session_id,
                    "timeline": sm.timeline(session_id),
                }
            )
        except StorageUnavailableError as exc:
            return jsonify({"status": "storage_unavailable", "error": str(exc)}), 503

    @app.route("/api/agent/sessions/<session_id>/recover", methods=["POST"])
    def agent_session_recover(session_id: str):
        from agent.operator_control import get_operator_control
        from agent.session_manager import get_session_manager
        from agent.session_recovery import SessionRecovery
        from storage.database import StorageUnavailableError

        payload = request.get_json(force=True) or {}
        visual_ui_map = _sanitize_plan_visual_map(payload)
        if visual_ui_map == "RAW_BLOCKED":
            return jsonify({"status": "rejected", "error": "raw_screenshot_blocked"}), 400

        try:
            sm = get_session_manager()
            sm.require_storage()
            recovery = SessionRecovery(sm)
            page = payload.get("page") or payload.get("dom_snapshot")
            result = recovery.recover_with_fresh_perception(
                session_id,
                page=page if isinstance(page, dict) else None,
                page_text=payload.get("page_text") or "",
                visual_ui_map=visual_ui_map,
                safe_page_state=payload.get("safe_page_state"),
            )
            decision = (result.get("decision") or {}).get("action")
            if payload.get("auto_resume") and decision in ("resume", "re_resolve"):
                result["resume"] = get_operator_control().resume_session(
                    session_id,
                    page=page if isinstance(page, dict) else None,
                    page_text=payload.get("page_text") or "",
                    visual_ui_map=visual_ui_map,
                    safe_page_state=payload.get("safe_page_state"),
                    execute=True,
                )
            if result.get("status") == "not_found":
                return jsonify(result), 404
            return jsonify(result)
        except StorageUnavailableError as exc:
            return jsonify({"status": "storage_unavailable", "error": str(exc)}), 503

    @app.route("/api/agent/sessions/<session_id>/replan-preview", methods=["GET"])
    def agent_session_replan_preview_get(session_id: str):
        from agent.operator_control import get_operator_control

        result = get_operator_control().get_replan_preview(session_id)
        code = 200
        if result.get("status") == "not_found":
            code = 404
        elif result.get("status") == "storage_unavailable":
            code = 503
        return jsonify(result), code

    @app.route("/api/agent/sessions/<session_id>/replan-preview", methods=["POST"])
    def agent_session_replan_preview_create(session_id: str):
        from agent.operator_control import get_operator_control

        payload = request.get_json(force=True) or {}
        visual_ui_map = _sanitize_plan_visual_map(payload)
        if visual_ui_map == "RAW_BLOCKED":
            return jsonify({"status": "rejected", "error": "raw_screenshot_blocked"}), 400
        page = payload.get("page") or payload.get("dom_snapshot")
        result = get_operator_control().request_replan_preview(
            session_id,
            page=page if isinstance(page, dict) else None,
            page_text=payload.get("page_text") or "",
            visual_ui_map=visual_ui_map,
            safe_page_state=payload.get("safe_page_state"),
            force=bool(payload.get("force")),
        )
        code = 200
        if result.get("status") in ("not_found", "plan_not_found"):
            code = 404
        elif result.get("status") == "storage_unavailable":
            code = 503
        return jsonify(result), code

    @app.route("/api/agent/sessions/<session_id>/approve-replan", methods=["POST"])
    def agent_session_approve_replan(session_id: str):
        from agent.operator_control import get_operator_control

        result = get_operator_control().approve_replan(session_id)
        code = 200
        if result.get("status") == "not_found":
            code = 404
        elif result.get("status") == "storage_unavailable":
            code = 503
        elif result.get("status") in ("no_pending_preview", "max_replan_attempts"):
            code = 409
        return jsonify(result), code

    @app.route("/api/agent/sessions/<session_id>/reject-replan", methods=["POST"])
    def agent_session_reject_replan(session_id: str):
        from agent.operator_control import get_operator_control

        payload = request.get_json(force=True) or {}
        reason = (payload.get("reason") or "replan_rejected_by_operator")[:200]
        result = get_operator_control().reject_replan(session_id, reason=reason)
        code = 200
        if result.get("status") == "not_found":
            code = 404
        elif result.get("status") == "storage_unavailable":
            code = 503
        return jsonify(result), code

    # ------------------------------------------------------------------
    # Milestone 5B — durable action lifecycle / recovery APIs
    # ------------------------------------------------------------------
    @app.route("/api/agent/actions/recovery", methods=["GET"])
    def agent_actions_recovery():
        """List actions in recovery_required / interrupted states."""
        from agent.action_recovery import detect_interrupted_actions
        from agent.approved_action_delivery import get_approved_action_delivery
        from agent.lifecycle_registry import get_lifecycle_registry

        delivery = get_approved_action_delivery()
        registry = get_lifecycle_registry()
        delivery.expire_leases()
        deliveries = delivery.list_recovery_required()
        # Also include live claimed/executing from memory
        for eid, rec in list(getattr(delivery, "_by_execution", {}).items()):
            if rec.get("status") in ("claimed", "executing"):
                deliveries.append(delivery._public_record(rec))
        lifecycles = registry.list_recovery_required()
        items = detect_interrupted_actions(
            delivery_records=deliveries,
            lifecycle_records=lifecycles,
        )
        return jsonify({"status": "ok", "actions": items, "count": len(items)})

    @app.route("/api/agent/actions/pending", methods=["GET"])
    def agent_actions_pending():
        """Pending confirmations + approved actions waiting for browser."""
        from agent.confirmation_manager import get_confirmation_manager
        from agent.approved_action_delivery import get_approved_action_delivery

        mgr = get_confirmation_manager()
        mgr.expire_old()
        confirmations = []
        for record in mgr.store.values():
            from agent.confirmation_manager import PendingConfirmation

            p = PendingConfirmation.from_record(record)
            if p.state == "pending" and not p.is_expired():
                confirmations.append(p.public_view())

        delivery = get_approved_action_delivery()
        waiting = []
        for rec in getattr(delivery, "_by_execution", {}).values():
            if rec.get("status") in ("approved", "waiting_for_browser"):
                waiting.append(delivery._public_record(rec))
        if getattr(delivery, "_repo", None) is not None:
            for rec in delivery._repo.list_by_status(
                ["approved", "waiting_for_browser"], limit=100
            ):
                waiting.append(delivery._public_record(rec))

        # Dedupe by execution_id
        seen = set()
        unique_waiting = []
        for w in waiting:
            eid = w.get("execution_id")
            if eid in seen:
                continue
            seen.add(eid)
            unique_waiting.append(w)

        return jsonify(
            {
                "status": "ok",
                "confirmations": confirmations,
                "approved_actions": unique_waiting,
            }
        )

    @app.route("/api/agent/action/<action_id>", methods=["GET"])
    def agent_action_detail(action_id: str):
        """Public view of a durable action by lifecycle_id or execution_id."""
        from agent.approved_action_delivery import get_approved_action_delivery
        from agent.lifecycle_registry import get_lifecycle_registry

        registry = get_lifecycle_registry()
        delivery = get_approved_action_delivery()
        life = registry.public_view(action_id)
        exec_rec = delivery.get(action_id)
        if life is None and exec_rec is not None:
            lid = exec_rec.get("lifecycle_id")
            life = registry.public_view(lid) if lid else None
        if life is None and exec_rec is None:
            return jsonify({"status": "not_found"}), 404
        return jsonify(
            {
                "status": "ok",
                "lifecycle": life,
                "delivery": delivery._public_record(exec_rec) if exec_rec else None,
            }
        )

    @app.route("/api/agent/action/<action_id>/timeline", methods=["GET"])
    def agent_action_timeline(action_id: str):
        from agent.lifecycle_registry import get_lifecycle_registry
        from agent.approved_action_delivery import get_approved_action_delivery

        registry = get_lifecycle_registry()
        delivery = get_approved_action_delivery()
        lifecycle_id = action_id
        if not registry.get(action_id):
            rec = delivery.get(action_id)
            if rec and rec.get("lifecycle_id"):
                lifecycle_id = rec["lifecycle_id"]
        events = registry.durable_timeline(lifecycle_id)
        return jsonify(
            {
                "status": "ok",
                "lifecycle_id": lifecycle_id,
                "timeline": events,
            }
        )

    @app.route("/api/agent/action/recover", methods=["POST"])
    def agent_action_recover():
        """Run recovery decision for an interrupted action (no blind replay)."""
        import time as time_mod

        from agent.action_recovery import ActionRecoveryEngine
        from agent.action_state import InvalidTransitionError
        from agent.approved_action_delivery import get_approved_action_delivery
        from agent.confirmation_manager import get_confirmation_manager
        from agent.lifecycle_registry import get_lifecycle_registry
        from agent.page_state import build_page_state, page_state_from_dict

        payload = request.get_json(force=True) or {}
        # Reject client-supplied selectors / coordinates mutation
        if payload.get("selector") or payload.get("coordinates") or payload.get("action"):
            return (
                jsonify(
                    {
                        "status": "rejected",
                        "error": "client_action_modification_forbidden",
                    }
                ),
                400,
            )

        t0 = time_mod.perf_counter()
        execution_id = payload.get("execution_id")
        lifecycle_id = payload.get("lifecycle_id")
        tab_id = payload.get("tab_id")
        delivery = get_approved_action_delivery()
        registry = get_lifecycle_registry()

        record = delivery.get(execution_id) if execution_id else None
        if record is None and lifecycle_id:
            item = registry.get(lifecycle_id)
            if item:
                execution_id = item.get("execution_id")
                record = delivery.get(execution_id) if execution_id else None

        if record is None and lifecycle_id is None:
            return jsonify({"status": "not_found"}), 404

        life_item = None
        if lifecycle_id:
            life_item = registry.get(lifecycle_id)
        elif record and record.get("lifecycle_id"):
            lifecycle_id = record["lifecycle_id"]
            life_item = registry.get(lifecycle_id)

        interrupted = (record or {}).get("status") or (
            life_item["lifecycle"].state if life_item else "recovery_required"
        )
        category = (record or {}).get("category") or (
            (life_item or {}).get("category") or ""
        )
        task = (record or {}).get("task") or (
            life_item["lifecycle"].task if life_item else ""
        )
        stored_action = (record or {}).get("action")
        if not stored_action and life_item:
            stored_action = life_item.get("action")

        fresh = payload.get("page") or payload.get("perception") or payload.get(
            "fresh_perception"
        )
        prev_state = None
        if payload.get("previous_page_state"):
            prev_state = page_state_from_dict(payload.get("previous_page_state"))

        engine = ActionRecoveryEngine()
        decision = engine.evaluate(
            interrupted_status=interrupted,
            category=category or "",
            risk_level=((life_item or {}).get("risk_level") or ""),
            task=task or "",
            stored_action=stored_action,
            fresh_perception=fresh if isinstance(fresh, dict) else None,
            previous_page_state=prev_state,
            verification_status=payload.get("verification_status"),
            captcha_detected=bool(payload.get("captcha_detected")),
            credentials_required=bool(payload.get("credentials_required")),
            tab_id=int(tab_id)
            if tab_id is not None
            else ((record or {}).get("tab_id") or (life_item or {}).get("tab_id")),
        )

        # Apply safe backend-owned transitions
        if life_item and lifecycle_id:
            try:
                if decision.decision == "requires_confirmation":
                    life = life_item["lifecycle"]
                    if life.state != "requires_confirmation":
                        if life.state != "recovery_required" and life.can_transition(
                            "recovery_required"
                        ):
                            registry.transition(
                                lifecycle_id,
                                "recovery_required",
                                decision.reason,
                            )
                        if life.state == "recovery_required" or life.can_transition(
                            "requires_confirmation"
                        ):
                            registry.transition(
                                lifecycle_id,
                                "requires_confirmation",
                                decision.reason,
                            )
                    # Create a fresh confirmation from stored action (no coords trust)
                    from agent.action_recovery import strip_trusted_coordinates

                    safe_action = strip_trusted_coordinates(stored_action or {})
                    mgr = get_confirmation_manager()
                    created = mgr.create(
                        action=safe_action,
                        category=category or "unknown",
                        reason=decision.reason,
                        target={"label": task or "Recovered action", "type": "unknown"},
                        task=task or "",
                        lifecycle_id=lifecycle_id,
                        tab_id=(record or {}).get("tab_id")
                        or (life_item or {}).get("tab_id"),
                        window_id=(record or {}).get("window_id"),
                    )
                    decision_dict = decision.to_dict()
                    decision_dict["confirmation"] = created.get("confirmation")
                    decision_dict["performance"] = {
                        **decision.performance,
                        "total_action_recovery_ms": round(
                            (time_mod.perf_counter() - t0) * 1000, 3
                        ),
                    }
                    return jsonify(
                        {"status": "ok", "recovery": decision_dict}
                    )
                if decision.decision == "cancel":
                    registry.transition(lifecycle_id, "cancelled", decision.reason)
                    if execution_id:
                        delivery.cancel_for_confirmation(
                            (record or {}).get("confirmation_id") or ""
                        )
                elif decision.decision == "safe_to_resume" and decision.action:
                    # Re-queue for browser with re-resolved action (no old coords)
                    if execution_id and record:
                        delivery.mark_recovery_required(
                            execution_id, reason="awaiting_safe_resume"
                        )
                    conf_id = (record or {}).get("confirmation_id") or f"recover_{lifecycle_id}"
                    queued = delivery.create(
                        confirmation_id=conf_id + "_resume",
                        action=decision.action,
                        tab_id=(record or {}).get("tab_id")
                        or (life_item or {}).get("tab_id"),
                        window_id=(record or {}).get("window_id"),
                        lifecycle_id=lifecycle_id,
                        task=task or "",
                        category=category or "safe",
                        session_id=(record or {}).get("session_id")
                        or (life_item or {}).get("session_id"),
                        plan_id=(record or {}).get("plan_id")
                        or (life_item or {}).get("plan_id"),
                    )
                    try:
                        registry.transition(
                            lifecycle_id, "waiting_for_extension", "Safe resume queued"
                        )
                    except InvalidTransitionError:
                        pass
                    decision_dict = decision.to_dict()
                    decision_dict["execution_id"] = queued.get("execution_id")
                    decision_dict["performance"] = {
                        **decision.performance,
                        "total_action_recovery_ms": round(
                            (time_mod.perf_counter() - t0) * 1000, 3
                        ),
                    }
                    return jsonify({"status": "ok", "recovery": decision_dict})
                elif decision.decision == "replan":
                    # Integrate with adaptive replanning via session when available
                    session_id = (record or {}).get("session_id") or (
                        life_item or {}
                    ).get("session_id") or _agent_state.get("session_id")
                    if session_id and isinstance(fresh, dict):
                        try:
                            from agent.operator_control import get_operator_control

                            preview = get_operator_control().request_replan_preview(
                                session_id,
                                page=fresh.get("page") or fresh,
                                page_text=payload.get("page_text") or "",
                                visual_ui_map=fresh.get("visual_ui_map"),
                                safe_page_state=payload.get("safe_page_state"),
                                force=True,
                            )
                            decision_dict = decision.to_dict()
                            decision_dict["replan_preview"] = {
                                "status": preview.get("status"),
                                "preview_id": preview.get("preview_id"),
                            }
                            decision_dict["performance"] = {
                                **decision.performance,
                                "total_action_recovery_ms": round(
                                    (time_mod.perf_counter() - t0) * 1000, 3
                                ),
                            }
                            return jsonify({"status": "ok", "recovery": decision_dict})
                        except Exception:
                            pass
            except InvalidTransitionError as exc:
                return jsonify(
                    {
                        "status": "invalid_transition",
                        "error": str(exc),
                        "recovery": decision.to_dict(),
                    }
                ), 409

        decision_dict = decision.to_dict()
        decision_dict["performance"] = {
            **decision.performance,
            "total_action_recovery_ms": round(
                (time_mod.perf_counter() - t0) * 1000, 3
            ),
        }
        return jsonify({"status": "ok", "recovery": decision_dict})

    @app.route("/api/agent/action/revalidate", methods=["POST"])
    def agent_action_revalidate():
        """Require fresh perception and re-run recovery eligibility checks."""
        payload = request.get_json(force=True) or {}
        if not (payload.get("page") or payload.get("perception") or payload.get("fresh_perception")):
            return jsonify(
                {
                    "status": "fresh_perception_required",
                    "error": "Fresh sanitized perception is required.",
                }
            ), 400
        # Delegate to recover
        return agent_action_recover()

    @app.route("/api/agent/action/cancel", methods=["POST"])
    def agent_action_cancel():
        """Cancel a durable action / confirmation (terminal)."""
        from agent.action_state import InvalidTransitionError
        from agent.approved_action_delivery import get_approved_action_delivery
        from agent.confirmation_manager import get_confirmation_manager
        from agent.lifecycle_registry import get_lifecycle_registry

        payload = request.get_json(force=True) or {}
        if payload.get("action") or payload.get("selector") or payload.get("coordinates"):
            return (
                jsonify(
                    {
                        "status": "rejected",
                        "error": "client_action_modification_forbidden",
                    }
                ),
                400,
            )

        confirmation_id = payload.get("confirmation_id")
        execution_id = payload.get("execution_id")
        lifecycle_id = payload.get("lifecycle_id")
        delivery = get_approved_action_delivery()
        registry = get_lifecycle_registry()
        mgr = get_confirmation_manager()

        result: Dict[str, Any] = {"status": "cancelled"}

        if confirmation_id:
            result = mgr.cancel(confirmation_id)
            delivery.cancel_for_confirmation(confirmation_id)

        if execution_id:
            rec = delivery.get(execution_id)
            if rec:
                delivery.mark_recovery_required(execution_id, reason="cancelled_by_operator")
                # Force cancel on delivery
                with delivery._lock:
                    r = delivery._by_execution.get(execution_id)
                    if r and r["status"] != "executed":
                        r["status"] = "cancelled"
                        delivery._persist_unlocked(r)
                if not lifecycle_id:
                    lifecycle_id = rec.get("lifecycle_id")
                if not confirmation_id:
                    confirmation_id = rec.get("confirmation_id")

        if lifecycle_id and registry.get(lifecycle_id):
            try:
                registry.transition(lifecycle_id, "cancelled", "Operator cancelled action")
            except InvalidTransitionError:
                # Already terminal or invalid — report explicitly
                life = registry.get(lifecycle_id)
                state = life["lifecycle"].state if life else None
                if state not in ("cancelled", "success", "expired", "blocked"):
                    return jsonify(
                        {
                            "status": "invalid_transition",
                            "lifecycle_id": lifecycle_id,
                            "current_state": state,
                        }
                    ), 409

        return jsonify(
            {
                "status": result.get("status") or "cancelled",
                "confirmation_id": confirmation_id,
                "execution_id": execution_id,
                "lifecycle_id": lifecycle_id,
            }
        )

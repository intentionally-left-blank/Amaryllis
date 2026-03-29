from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from agents.agent_run_manager import AutonomyCircuitBreakerBlockedError

SUPERVISOR_GRAPH_STATUSES: set[str] = {
    "planned",
    "running",
    "review_required",
    "succeeded",
    "failed",
    "canceled",
}
SUPERVISOR_NODE_STATUSES: set[str] = {
    "planned",
    "queued",
    "running",
    "succeeded",
    "failed",
    "blocked",
    "canceled",
}
_RUN_ACTIVE_STATUSES: set[str] = {"queued", "running"}
_RUN_TERMINAL_STATUSES: set[str] = {"succeeded", "failed", "canceled"}
_OBJECTIVE_GATE_MODES: set[str] = {"auto", "manual"}
_OBJECTIVE_GATE_KEYWORD_MATCH: set[str] = {"any", "all"}
_OBJECTIVE_GATE_ON_FAILURE: set[str] = {"review_required", "failed"}
_OBJECTIVE_VERIFICATION_STATUSES: set[str] = {
    "pending",
    "review_required",
    "passed",
    "failed",
    "skipped",
}

TelemetryEmitter = Callable[[str, dict[str, Any]], None]


class SupervisorTaskGraphManager:
    def __init__(
        self,
        *,
        agent_manager: Any,
        database: Any | None = None,
        telemetry_emitter: TelemetryEmitter | None = None,
        max_graphs: int = 10_000,
        max_nodes_per_graph: int = 256,
    ) -> None:
        self._agent_manager = agent_manager
        self._database = database
        self._telemetry_emitter = telemetry_emitter
        self._max_graphs = max(1, int(max_graphs))
        self._max_nodes_per_graph = max(1, int(max_nodes_per_graph))
        self._lock = RLock()
        self._graphs: dict[str, dict[str, Any]] = {}
        self._hydrate_from_database()

    def create_graph(
        self,
        *,
        user_id: str,
        objective: str,
        nodes: list[dict[str, Any]],
        objective_verification: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        normalized_user = str(user_id or "").strip()
        if not normalized_user:
            raise ValueError("user_id is required")
        normalized_objective = str(objective or "").strip()
        if not normalized_objective:
            raise ValueError("objective is required")
        if not isinstance(nodes, list):
            raise ValueError("nodes must be a list")
        if not nodes:
            raise ValueError("nodes must include at least one node")
        if len(nodes) > self._max_nodes_per_graph:
            raise ValueError(
                f"graph node count exceeds limit ({self._max_nodes_per_graph})"
            )

        now = _utcnow_iso()
        normalized_nodes: dict[str, dict[str, Any]] = {}
        for index, raw in enumerate(nodes):
            if not isinstance(raw, dict):
                raise ValueError(f"node[{index}] must be an object")
            node_id = str(raw.get("node_id") or f"node-{index + 1}").strip()
            if not node_id:
                raise ValueError(f"node[{index}] has empty node_id")
            if node_id in normalized_nodes:
                raise ValueError(f"duplicate node_id: {node_id}")

            agent_id = str(raw.get("agent_id") or "").strip()
            if not agent_id:
                raise ValueError(f"node[{index}] is missing agent_id")

            message = str(raw.get("message") or "").strip()
            if not message:
                raise ValueError(f"node[{index}] is missing message")

            raw_depends = raw.get("depends_on")
            if raw_depends is None:
                depends_on = []
            elif isinstance(raw_depends, list):
                depends_on = []
                for dep_item in raw_depends:
                    dep_id = str(dep_item or "").strip()
                    if not dep_id:
                        raise ValueError(f"node[{index}] has empty dependency reference")
                    if dep_id == node_id:
                        raise ValueError(f"node[{index}] cannot depend on itself")
                    if dep_id not in depends_on:
                        depends_on.append(dep_id)
            else:
                raise ValueError(f"node[{index}].depends_on must be a list")

            max_attempts = raw.get("max_attempts")
            if max_attempts is None:
                normalized_attempts = None
            else:
                normalized_attempts = int(max_attempts)
                if normalized_attempts < 1 or normalized_attempts > 10:
                    raise ValueError(f"node[{index}].max_attempts must be in [1, 10]")

            budget_payload = raw.get("budget")
            if budget_payload is not None and not isinstance(budget_payload, dict):
                raise ValueError(f"node[{index}].budget must be an object")

            node_metadata = raw.get("metadata")
            if node_metadata is not None and not isinstance(node_metadata, dict):
                raise ValueError(f"node[{index}].metadata must be an object")

            normalized_nodes[node_id] = {
                "node_id": node_id,
                "order": index + 1,
                "agent_id": agent_id,
                "message": message,
                "depends_on": depends_on,
                "max_attempts": normalized_attempts,
                "budget": dict(budget_payload) if isinstance(budget_payload, dict) else None,
                "metadata": dict(node_metadata) if isinstance(node_metadata, dict) else {},
                "status": "planned",
                "run_id": None,
                "run_status": None,
                "attempts": 0,
                "created_at": now,
                "started_at": None,
                "completed_at": None,
                "last_error": None,
            }

        for node in normalized_nodes.values():
            for dep_id in node.get("depends_on", []):
                if dep_id not in normalized_nodes:
                    raise ValueError(
                        f"node '{node['node_id']}' depends on unknown node '{dep_id}'"
                    )
        self._assert_acyclic(normalized_nodes)

        objective_gate = self._normalize_objective_gate(
            objective=normalized_objective,
            nodes=normalized_nodes,
            raw_config=objective_verification,
        )
        graph_id = f"sup-{uuid4().hex}"
        graph = {
            "id": graph_id,
            "user_id": normalized_user,
            "objective": normalized_objective,
            "status": "planned",
            "created_at": now,
            "updated_at": now,
            "launched_at": None,
            "finished_at": None,
            "default_session_id": None,
            "metadata": dict(metadata) if isinstance(metadata, dict) else {},
            "objective_gate": objective_gate,
            "objective_verification": {
                "status": "pending",
                "checked_at": None,
                "summary": "Objective verification pending.",
                "checks": [],
                "last_failure_reasons": [],
                "manual_override": None,
            },
            "nodes": normalized_nodes,
            "timeline": [],
            "telemetry": {
                "ticks": 0,
                "runs_started": 0,
                "runs_completed": 0,
                "last_tick_at": None,
            },
        }
        with self._lock:
            self._evict_terminal_if_needed()
            if len(self._graphs) >= self._max_graphs:
                raise ValueError("supervisor graph capacity reached")
            self._graphs[graph_id] = graph
            self._append_timeline(
                graph,
                event="graph_created",
                payload={
                    "graph_id": graph_id,
                    "objective": normalized_objective,
                    "user_id": normalized_user,
                    "node_count": len(normalized_nodes),
                    "actor": _optional_str(actor),
                    "request_id": _optional_str(request_id),
                },
            )
            self._persist_graph(graph)
            return _snapshot(graph)

    def get_graph(self, *, graph_id: str) -> dict[str, Any]:
        normalized_graph_id = str(graph_id or "").strip()
        if not normalized_graph_id:
            raise ValueError("graph_id is required")
        with self._lock:
            graph = self._graphs.get(normalized_graph_id)
            if graph is None:
                raise ValueError(f"Supervisor graph not found: {normalized_graph_id}")
            return _snapshot(graph)

    def list_graphs(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized_user = str(user_id or "").strip() or None
        normalized_status = str(status or "").strip().lower() or None
        if normalized_status is not None and normalized_status not in SUPERVISOR_GRAPH_STATUSES:
            allowed = ", ".join(sorted(SUPERVISOR_GRAPH_STATUSES))
            raise ValueError(f"Invalid graph status '{status}'. Allowed values: {allowed}.")

        capped_limit = max(1, min(int(limit), 2000))
        with self._lock:
            rows = list(self._graphs.values())
            rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
            items: list[dict[str, Any]] = []
            for row in rows:
                if normalized_user is not None and str(row.get("user_id") or "") != normalized_user:
                    continue
                if normalized_status is not None and str(row.get("status") or "") != normalized_status:
                    continue
                items.append(_snapshot(row))
                if len(items) >= capped_limit:
                    break
            return items

    def launch_graph(
        self,
        *,
        graph_id: str,
        user_id: str | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        normalized_graph_id = str(graph_id or "").strip()
        if not normalized_graph_id:
            raise ValueError("graph_id is required")
        with self._lock:
            graph = self._graphs.get(normalized_graph_id)
            if graph is None:
                raise ValueError(f"Supervisor graph not found: {normalized_graph_id}")
            self._assert_owner(graph=graph, user_id=user_id)

            current_status = str(graph.get("status") or "")
            if current_status in {"succeeded", "failed", "canceled"}:
                raise ValueError(f"Supervisor graph is terminal: {normalized_graph_id}")

            now = _utcnow_iso()
            if current_status == "planned":
                graph["status"] = "running"
                graph["launched_at"] = now
                self._append_timeline(
                    graph,
                    event="graph_launched",
                    payload={
                        "graph_id": normalized_graph_id,
                        "actor": _optional_str(actor),
                        "request_id": _optional_str(request_id),
                    },
                )
            if session_id is not None:
                graph["default_session_id"] = str(session_id or "").strip() or None

            self._schedule_ready_nodes(
                graph,
                request_id=request_id,
                actor=actor,
            )
            self._sync_graph_status(graph)
            graph["updated_at"] = _utcnow_iso()
            self._persist_graph(graph)
            return _snapshot(graph)

    def tick_graph(
        self,
        *,
        graph_id: str,
        user_id: str | None = None,
        request_id: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        normalized_graph_id = str(graph_id or "").strip()
        if not normalized_graph_id:
            raise ValueError("graph_id is required")
        with self._lock:
            graph = self._graphs.get(normalized_graph_id)
            if graph is None:
                raise ValueError(f"Supervisor graph not found: {normalized_graph_id}")
            self._assert_owner(graph=graph, user_id=user_id)

            telemetry = graph.get("telemetry")
            if not isinstance(telemetry, dict):
                telemetry = {}
                graph["telemetry"] = telemetry
            telemetry["ticks"] = int(telemetry.get("ticks", 0)) + 1
            telemetry["last_tick_at"] = _utcnow_iso()

            for node in self._iter_nodes(graph):
                node_status = str(node.get("status") or "")
                run_id = str(node.get("run_id") or "").strip()
                if not run_id:
                    continue
                if node_status not in {"queued", "running"}:
                    continue
                self._refresh_node_from_run(
                    graph,
                    node,
                    request_id=request_id,
                    actor=actor,
                )

            self._mark_blocked_nodes(graph, request_id=request_id, actor=actor)
            if str(graph.get("status") or "") in {"planned", "running"}:
                self._schedule_ready_nodes(
                    graph,
                    request_id=request_id,
                    actor=actor,
                )
            self._sync_graph_status(graph)
            graph["updated_at"] = _utcnow_iso()
            self._append_timeline(
                graph,
                event="graph_ticked",
                payload={
                    "graph_id": normalized_graph_id,
                    "status": str(graph.get("status") or ""),
                    "actor": _optional_str(actor),
                    "request_id": _optional_str(request_id),
                },
            )
            self._persist_graph(graph)
            return _snapshot(graph)

    def verify_graph_objective(
        self,
        *,
        graph_id: str,
        user_id: str | None = None,
        override_pass: bool | None = None,
        note: str | None = None,
        force_recheck: bool = False,
        request_id: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        normalized_graph_id = str(graph_id or "").strip()
        if not normalized_graph_id:
            raise ValueError("graph_id is required")
        normalized_note = str(note or "").strip()
        with self._lock:
            graph = self._graphs.get(normalized_graph_id)
            if graph is None:
                raise ValueError(f"Supervisor graph not found: {normalized_graph_id}")
            self._assert_owner(graph=graph, user_id=user_id)

            if force_recheck or override_pass is None:
                self._evaluate_objective_gate(graph, request_id=request_id, actor=actor)

            verification = graph.get("objective_verification")
            if not isinstance(verification, dict):
                verification = {}
                graph["objective_verification"] = verification

            if override_pass is not None:
                checked_at = _utcnow_iso()
                verification["checked_at"] = checked_at
                verification["manual_override"] = {
                    "actor": _optional_str(actor),
                    "at": checked_at,
                    "override_pass": bool(override_pass),
                    "note": normalized_note or None,
                    "request_id": _optional_str(request_id),
                }
                if bool(override_pass):
                    verification["status"] = "passed"
                    verification["summary"] = "Objective verification manually approved."
                    verification["last_failure_reasons"] = []
                else:
                    verification["status"] = "failed"
                    verification["summary"] = "Objective verification manually rejected."
                    verification["last_failure_reasons"] = [normalized_note or "manual rejection"]
                self._append_timeline(
                    graph,
                    event="objective_verification_manual_override",
                    payload={
                        "graph_id": normalized_graph_id,
                        "override_pass": bool(override_pass),
                        "note": normalized_note or None,
                        "actor": _optional_str(actor),
                        "request_id": _optional_str(request_id),
                    },
                )

            self._sync_graph_status(graph)
            graph["updated_at"] = _utcnow_iso()
            self._persist_graph(graph)
            return _snapshot(graph)

    @staticmethod
    def _assert_acyclic(nodes: dict[str, dict[str, Any]]) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def _dfs(node_id: str) -> None:
            if node_id in visited:
                return
            if node_id in visiting:
                raise ValueError(f"graph contains dependency cycle at node '{node_id}'")
            visiting.add(node_id)
            node = nodes.get(node_id, {})
            depends_on = node.get("depends_on")
            if isinstance(depends_on, list):
                for dep_id in depends_on:
                    _dfs(str(dep_id))
            visiting.remove(node_id)
            visited.add(node_id)

        for item in nodes.keys():
            _dfs(item)

    def _schedule_ready_nodes(
        self,
        graph: dict[str, Any],
        *,
        request_id: str | None,
        actor: str | None,
    ) -> None:
        graph_status = str(graph.get("status") or "")
        if graph_status not in {"planned", "running"}:
            return

        for node in self._iter_nodes(graph):
            if str(node.get("status") or "") != "planned":
                continue
            if str(node.get("run_id") or "").strip():
                continue
            dependencies = node.get("depends_on")
            if not isinstance(dependencies, list):
                dependencies = []
            if any(
                str(self._node_by_id(graph, dep_id).get("status") or "") != "succeeded"
                for dep_id in dependencies
            ):
                continue

            try:
                run = self._agent_manager.create_run(
                    agent_id=str(node.get("agent_id") or ""),
                    user_message=str(node.get("message") or ""),
                    user_id=str(graph.get("user_id") or ""),
                    session_id=graph.get("default_session_id"),
                    max_attempts=node.get("max_attempts"),
                    budget=node.get("budget"),
                    run_source="supervisor",
                )
                if not isinstance(run, dict):
                    raise ValueError("agent_manager.create_run returned invalid payload")
                run_id = str(run.get("id") or "").strip()
                if not run_id:
                    raise ValueError("agent_manager.create_run returned empty run id")
                run_status = str(run.get("status") or "queued").strip().lower() or "queued"
                if run_status not in (_RUN_ACTIVE_STATUSES | _RUN_TERMINAL_STATUSES):
                    run_status = "queued"

                now = _utcnow_iso()
                node_metadata = node.get("metadata")
                if isinstance(node_metadata, dict):
                    node_metadata.pop("autonomy_circuit_breaker_last_blocked_revision", None)
                    node_metadata.pop("autonomy_circuit_breaker_blocked", None)
                node["run_id"] = run_id
                node["run_status"] = run_status
                node["attempts"] = int(node.get("attempts", 0)) + 1
                node["started_at"] = now
                if run_status in _RUN_ACTIVE_STATUSES:
                    node["status"] = run_status
                    node["completed_at"] = None
                    node["last_error"] = None
                elif run_status == "succeeded":
                    node["status"] = "succeeded"
                    node["completed_at"] = now
                    node["last_error"] = None
                    self._inc_graph_counter(graph, "runs_completed")
                elif run_status == "canceled":
                    node["status"] = "canceled"
                    node["completed_at"] = now
                    node["last_error"] = "child run canceled"
                else:
                    node["status"] = "failed"
                    node["completed_at"] = now
                    node["last_error"] = "child run failed"
                self._inc_graph_counter(graph, "runs_started")
                self._append_timeline(
                    graph,
                    event="node_run_created",
                    payload={
                        "graph_id": str(graph.get("id") or ""),
                        "node_id": str(node.get("node_id") or ""),
                        "run_id": run_id,
                        "run_status": run_status,
                        "actor": _optional_str(actor),
                        "request_id": _optional_str(request_id),
                    },
                )
            except AutonomyCircuitBreakerBlockedError as exc:
                node_metadata = node.get("metadata")
                if not isinstance(node_metadata, dict):
                    node_metadata = {}
                    node["metadata"] = node_metadata
                revision: int | None = None
                for candidate in (
                    (exc.decision if isinstance(exc.decision, dict) else {}).get("revision"),
                    (exc.snapshot if isinstance(exc.snapshot, dict) else {}).get("revision"),
                ):
                    try:
                        parsed = int(candidate)
                    except Exception:
                        continue
                    if parsed >= 0:
                        revision = parsed
                        break
                emit_blocked_event = True
                if revision is not None:
                    previous_revision = node_metadata.get("autonomy_circuit_breaker_last_blocked_revision")
                    try:
                        previous_revision_int = int(previous_revision)
                    except Exception:
                        previous_revision_int = None
                    emit_blocked_event = previous_revision_int != revision
                    node_metadata["autonomy_circuit_breaker_last_blocked_revision"] = revision
                else:
                    already_blocked = bool(node_metadata.get("autonomy_circuit_breaker_blocked"))
                    emit_blocked_event = not already_blocked
                    node_metadata["autonomy_circuit_breaker_blocked"] = True

                node["run_status"] = None
                node["status"] = "planned"
                node["started_at"] = None
                node["completed_at"] = None
                node["last_error"] = str(exc)
                if emit_blocked_event:
                    self._append_timeline(
                        graph,
                        event="node_run_blocked_autonomy_circuit_breaker",
                        payload={
                            "graph_id": str(graph.get("id") or ""),
                            "node_id": str(node.get("node_id") or ""),
                            "agent_id": str(node.get("agent_id") or ""),
                            "matched_scopes": list(exc.matched_scopes),
                            "autonomy_circuit_breaker": dict(exc.snapshot),
                            "error": str(exc),
                            "actor": _optional_str(actor),
                            "request_id": _optional_str(request_id),
                        },
                    )
            except Exception as exc:
                now = _utcnow_iso()
                node["run_status"] = "failed"
                node["status"] = "failed"
                node["completed_at"] = now
                node["last_error"] = str(exc)
                self._append_timeline(
                    graph,
                    event="node_run_create_failed",
                    payload={
                        "graph_id": str(graph.get("id") or ""),
                        "node_id": str(node.get("node_id") or ""),
                        "error": str(exc),
                        "actor": _optional_str(actor),
                        "request_id": _optional_str(request_id),
                    },
                )

    def _refresh_node_from_run(
        self,
        graph: dict[str, Any],
        node: dict[str, Any],
        *,
        request_id: str | None,
        actor: str | None,
    ) -> None:
        run_id = str(node.get("run_id") or "").strip()
        if not run_id:
            return
        try:
            run = self._agent_manager.get_run(run_id)
        except Exception as exc:
            now = _utcnow_iso()
            node["run_status"] = "failed"
            node["status"] = "failed"
            node["completed_at"] = now
            node["last_error"] = str(exc)
            self._append_timeline(
                graph,
                event="node_run_refresh_failed",
                payload={
                    "graph_id": str(graph.get("id") or ""),
                    "node_id": str(node.get("node_id") or ""),
                    "run_id": run_id,
                    "error": str(exc),
                    "actor": _optional_str(actor),
                    "request_id": _optional_str(request_id),
                },
            )
            return

        if not isinstance(run, dict):
            return
        run_status = str(run.get("status") or "").strip().lower()
        if run_status not in (_RUN_ACTIVE_STATUSES | _RUN_TERMINAL_STATUSES):
            return

        previous_status = str(node.get("status") or "")
        node["run_status"] = run_status
        now = _utcnow_iso()

        if run_status in _RUN_ACTIVE_STATUSES:
            node["status"] = run_status
            if not str(node.get("started_at") or "").strip():
                node["started_at"] = now
            return

        if run_status == "succeeded":
            node["status"] = "succeeded"
            node["completed_at"] = now
            node["last_error"] = None
            if previous_status != "succeeded":
                self._inc_graph_counter(graph, "runs_completed")
            self._append_timeline(
                graph,
                event="node_succeeded",
                payload={
                    "graph_id": str(graph.get("id") or ""),
                    "node_id": str(node.get("node_id") or ""),
                    "run_id": run_id,
                    "actor": _optional_str(actor),
                    "request_id": _optional_str(request_id),
                },
            )
            return

        node["status"] = "canceled" if run_status == "canceled" else "failed"
        node["completed_at"] = now
        if run_status == "canceled":
            node["last_error"] = "child run canceled"
        else:
            node["last_error"] = _extract_run_error(run) or "child run failed"
        self._append_timeline(
            graph,
            event="node_failed" if node["status"] == "failed" else "node_canceled",
            payload={
                "graph_id": str(graph.get("id") or ""),
                "node_id": str(node.get("node_id") or ""),
                "run_id": run_id,
                "error": node.get("last_error"),
                "actor": _optional_str(actor),
                "request_id": _optional_str(request_id),
            },
        )

    def _mark_blocked_nodes(
        self,
        graph: dict[str, Any],
        *,
        request_id: str | None,
        actor: str | None,
    ) -> None:
        for node in self._iter_nodes(graph):
            if str(node.get("status") or "") != "planned":
                continue
            dependencies = node.get("depends_on")
            if not isinstance(dependencies, list) or not dependencies:
                continue

            dep_statuses = [
                str(self._node_by_id(graph, dep_id).get("status") or "")
                for dep_id in dependencies
            ]
            if not any(status in {"failed", "canceled", "blocked"} for status in dep_statuses):
                continue
            node["status"] = "blocked"
            node["run_status"] = None
            node["completed_at"] = _utcnow_iso()
            node["last_error"] = "dependency failed or canceled"
            self._append_timeline(
                graph,
                event="node_blocked",
                payload={
                    "graph_id": str(graph.get("id") or ""),
                    "node_id": str(node.get("node_id") or ""),
                    "dependency_statuses": dep_statuses,
                    "actor": _optional_str(actor),
                    "request_id": _optional_str(request_id),
                },
            )

    def _sync_graph_status(self, graph: dict[str, Any]) -> None:
        node_statuses = [str(item.get("status") or "") for item in self._iter_nodes(graph)]
        if not node_statuses:
            graph["status"] = "failed"
            graph["finished_at"] = _utcnow_iso()
            return

        target_status = str(graph.get("status") or "planned")
        has_active = any(status in {"planned", "queued", "running"} for status in node_statuses)
        has_failed = any(status == "failed" for status in node_statuses)
        has_blocked = any(status == "blocked" for status in node_statuses)
        has_canceled = any(status == "canceled" for status in node_statuses)
        all_succeeded = all(status == "succeeded" for status in node_statuses)

        if all_succeeded:
            existing_verification = graph.get("objective_verification")
            existing_status = (
                str(existing_verification.get("status") or "").strip().lower()
                if isinstance(existing_verification, dict)
                else ""
            )
            if existing_status in {"passed", "skipped", "failed", "review_required"}:
                verification = existing_verification if isinstance(existing_verification, dict) else {}
                verification_status = existing_status
            else:
                verification = self._evaluate_objective_gate(graph)
                verification_status = str(verification.get("status") or "").strip().lower()
            if verification_status in {"passed", "skipped"}:
                target_status = "succeeded"
            elif verification_status == "failed":
                target_status = "failed"
            else:
                target_status = "review_required"
        elif has_failed:
            target_status = "failed"
        elif has_blocked and not has_active:
            target_status = "failed"
        elif has_canceled and not has_active:
            target_status = "canceled"
        elif has_active:
            target_status = "running" if str(graph.get("launched_at") or "").strip() else "planned"

        previous = str(graph.get("status") or "")
        graph["status"] = target_status
        if target_status in {"succeeded", "failed", "canceled"}:
            if not str(graph.get("finished_at") or "").strip():
                graph["finished_at"] = _utcnow_iso()
        else:
            graph["finished_at"] = None
        if previous != target_status:
            self._append_timeline(
                graph,
                event="graph_status_changed",
                payload={
                    "graph_id": str(graph.get("id") or ""),
                    "from_status": previous,
                    "to_status": target_status,
                },
            )

    @staticmethod
    def _node_by_id(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
        nodes = graph.get("nodes")
        if not isinstance(nodes, dict):
            raise ValueError("graph.nodes is corrupted")
        node = nodes.get(str(node_id))
        if not isinstance(node, dict):
            raise ValueError(f"graph refers to unknown node '{node_id}'")
        return node

    @staticmethod
    def _iter_nodes(graph: dict[str, Any]) -> list[dict[str, Any]]:
        nodes = graph.get("nodes")
        if not isinstance(nodes, dict):
            return []
        rows = [item for item in nodes.values() if isinstance(item, dict)]
        rows.sort(key=lambda item: int(item.get("order", 0)))
        return rows

    @staticmethod
    def _assert_owner(*, graph: dict[str, Any], user_id: str | None) -> None:
        if user_id is None:
            return
        normalized_user = str(user_id or "").strip()
        if not normalized_user:
            return
        owner = str(graph.get("user_id") or "").strip()
        if owner and owner != normalized_user:
            raise ValueError("Supervisor graph is owned by another user")

    @staticmethod
    def _inc_graph_counter(graph: dict[str, Any], key: str) -> None:
        telemetry = graph.get("telemetry")
        if not isinstance(telemetry, dict):
            telemetry = {}
            graph["telemetry"] = telemetry
        telemetry[key] = int(telemetry.get(key, 0)) + 1

    def _normalize_objective_gate(
        self,
        *,
        objective: str,
        nodes: dict[str, dict[str, Any]],
        raw_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        config = dict(raw_config) if isinstance(raw_config, dict) else {}
        mode = str(config.get("mode") or "auto").strip().lower() or "auto"
        if mode not in _OBJECTIVE_GATE_MODES:
            allowed = ", ".join(sorted(_OBJECTIVE_GATE_MODES))
            raise ValueError(f"Invalid objective_verification.mode '{mode}'. Allowed values: {allowed}.")

        enabled = bool(config.get("enabled", True))
        keyword_match = str(config.get("keyword_match") or "any").strip().lower() or "any"
        if keyword_match not in _OBJECTIVE_GATE_KEYWORD_MATCH:
            allowed = ", ".join(sorted(_OBJECTIVE_GATE_KEYWORD_MATCH))
            raise ValueError(
                f"Invalid objective_verification.keyword_match '{keyword_match}'. Allowed values: {allowed}."
            )
        on_failure = str(config.get("on_failure") or "review_required").strip().lower() or "review_required"
        if on_failure not in _OBJECTIVE_GATE_ON_FAILURE:
            allowed = ", ".join(sorted(_OBJECTIVE_GATE_ON_FAILURE))
            raise ValueError(
                f"Invalid objective_verification.on_failure '{on_failure}'. Allowed values: {allowed}."
            )

        min_response_chars = _safe_int(config.get("min_response_chars"), default=0, minimum=0)
        if min_response_chars > 20_000:
            raise ValueError("objective_verification.min_response_chars exceeds max value (20000).")

        raw_required_nodes = config.get("required_node_ids")
        if isinstance(raw_required_nodes, list):
            required_node_ids = []
            for item in raw_required_nodes:
                node_id = str(item or "").strip()
                if not node_id:
                    continue
                if node_id not in nodes:
                    raise ValueError(
                        f"objective_verification.required_node_ids contains unknown node '{node_id}'."
                    )
                if node_id not in required_node_ids:
                    required_node_ids.append(node_id)
        else:
            required_node_ids = self._leaf_node_ids(nodes)
        if not required_node_ids:
            required_node_ids = self._leaf_node_ids(nodes)

        raw_keywords = config.get("required_keywords")
        required_keywords: list[str] = []
        if isinstance(raw_keywords, list):
            for item in raw_keywords:
                keyword = str(item or "").strip().lower()
                if keyword and keyword not in required_keywords:
                    required_keywords.append(keyword)

        objective_keywords = self._objective_keywords(objective)
        return {
            "enabled": enabled,
            "mode": mode,
            "on_failure": on_failure,
            "required_node_ids": required_node_ids,
            "min_response_chars": min_response_chars,
            "required_keywords": required_keywords,
            "keyword_match": keyword_match,
            "objective_keywords": objective_keywords,
        }

    @staticmethod
    def _leaf_node_ids(nodes: dict[str, dict[str, Any]]) -> list[str]:
        inbound: dict[str, int] = {str(node_id): 0 for node_id in nodes.keys()}
        for node in nodes.values():
            depends_on = node.get("depends_on")
            if not isinstance(depends_on, list):
                continue
            for dep_id in depends_on:
                dep = str(dep_id or "").strip()
                if dep and dep in inbound:
                    inbound[dep] = inbound.get(dep, 0) + 1
        leaves = [node_id for node_id, inbound_count in inbound.items() if inbound_count == 0]
        leaves.sort()
        return leaves

    @staticmethod
    def _objective_keywords(objective: str, *, max_keywords: int = 8) -> list[str]:
        words: list[str] = []
        for token in str(objective or "").split():
            normalized = "".join(ch for ch in token.lower() if ch.isalnum() or ch in {"-", "_"}).strip("-_")
            if len(normalized) < 4:
                continue
            if normalized in words:
                continue
            words.append(normalized)
            if len(words) >= max_keywords:
                break
        return words

    def _evaluate_objective_gate(
        self,
        graph: dict[str, Any],
        *,
        request_id: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        gate = graph.get("objective_gate")
        if not isinstance(gate, dict):
            gate = self._normalize_objective_gate(
                objective=str(graph.get("objective") or ""),
                nodes={item["node_id"]: item for item in self._iter_nodes(graph)},
                raw_config={},
            )
            graph["objective_gate"] = gate

        verification = graph.get("objective_verification")
        if not isinstance(verification, dict):
            verification = {
                "status": "pending",
                "checked_at": None,
                "summary": "Objective verification pending.",
                "checks": [],
                "last_failure_reasons": [],
                "manual_override": None,
            }
            graph["objective_verification"] = verification

        if not bool(gate.get("enabled", True)):
            verification["status"] = "skipped"
            verification["checked_at"] = _utcnow_iso()
            verification["summary"] = "Objective verification gate is disabled."
            verification["checks"] = []
            verification["last_failure_reasons"] = []
            return verification

        checks: list[dict[str, Any]] = []
        failures: list[str] = []
        required_node_ids = [
            str(item).strip()
            for item in list(gate.get("required_node_ids") or [])
            if str(item).strip()
        ]
        min_response_chars = _safe_int(gate.get("min_response_chars"), default=0, minimum=0)
        required_keywords = [
            str(item).strip().lower()
            for item in list(gate.get("required_keywords") or [])
            if str(item).strip()
        ]
        keyword_match = str(gate.get("keyword_match") or "any").strip().lower() or "any"
        if keyword_match not in _OBJECTIVE_GATE_KEYWORD_MATCH:
            keyword_match = "any"

        responses_by_node: dict[str, str] = {}
        for node_id in required_node_ids:
            try:
                node = self._node_by_id(graph, node_id)
            except Exception:
                checks.append(
                    {
                        "kind": "required_node_succeeded",
                        "node_id": node_id,
                        "passed": False,
                        "reason": "node is missing",
                    }
                )
                failures.append(f"required node '{node_id}' is missing from graph")
                continue
            node_status = str(node.get("status") or "")
            run_id = str(node.get("run_id") or "").strip()
            check = {
                "kind": "required_node_succeeded",
                "node_id": node_id,
                "passed": False,
                "reason": "",
            }
            if node_status != "succeeded":
                check["reason"] = f"node status is '{node_status}'"
                failures.append(f"required node '{node_id}' is not succeeded")
                checks.append(check)
                continue
            if not run_id:
                check["reason"] = "missing run_id"
                failures.append(f"required node '{node_id}' has no run_id")
                checks.append(check)
                continue
            run = None
            try:
                run = self._agent_manager.get_run(run_id)
            except Exception as exc:
                check["reason"] = str(exc)
                failures.append(f"required node '{node_id}' run lookup failed")
                checks.append(check)
                continue
            if not isinstance(run, dict):
                check["reason"] = "run payload is invalid"
                failures.append(f"required node '{node_id}' run payload is invalid")
                checks.append(check)
                continue

            response_text = self._extract_run_response_text(run)
            responses_by_node[node_id] = response_text
            check["passed"] = True
            checks.append(check)

            if min_response_chars > 0:
                response_len = len(response_text.strip())
                passed = response_len >= min_response_chars
                checks.append(
                    {
                        "kind": "min_response_chars",
                        "node_id": node_id,
                        "min": min_response_chars,
                        "actual": response_len,
                        "passed": passed,
                        "reason": "" if passed else f"response too short: {response_len} chars",
                    }
                )
                if not passed:
                    failures.append(
                        f"required node '{node_id}' response is shorter than {min_response_chars} chars"
                    )

        if required_keywords:
            combined_response = "\n".join(responses_by_node.values()).strip().lower()
            matched_keywords = [keyword for keyword in required_keywords if keyword in combined_response]
            if keyword_match == "all":
                keyword_passed = len(matched_keywords) == len(required_keywords)
            else:
                keyword_passed = bool(matched_keywords)
            checks.append(
                {
                    "kind": "required_keywords",
                    "required_keywords": required_keywords,
                    "keyword_match": keyword_match,
                    "matched_keywords": matched_keywords,
                    "passed": keyword_passed,
                    "reason": "" if keyword_passed else "required keywords not found in node responses",
                }
            )
            if not keyword_passed:
                failures.append("required objective keywords not found in node responses")

        checked_at = _utcnow_iso()
        mode = str(gate.get("mode") or "auto").strip().lower() or "auto"
        on_failure = str(gate.get("on_failure") or "review_required").strip().lower() or "review_required"
        if on_failure not in _OBJECTIVE_GATE_ON_FAILURE:
            on_failure = "review_required"

        if failures:
            status = "failed" if on_failure == "failed" else "review_required"
            summary = "Objective verification failed."
        else:
            if mode == "manual":
                status = "review_required"
                summary = "Objective checks passed. Manual verification required."
            else:
                status = "passed"
                summary = "Objective verification passed."

        verification["status"] = status
        verification["checked_at"] = checked_at
        verification["summary"] = summary
        verification["checks"] = checks
        verification["last_failure_reasons"] = failures
        self._append_timeline(
            graph,
            event="objective_verification_evaluated",
            payload={
                "graph_id": str(graph.get("id") or ""),
                "status": status,
                "failures": failures,
                "checks_count": len(checks),
                "actor": _optional_str(actor),
                "request_id": _optional_str(request_id),
            },
        )
        return verification

    @staticmethod
    def _extract_run_response_text(run: dict[str, Any]) -> str:
        candidates: list[Any] = [run.get("response")]
        result = run.get("result")
        if isinstance(result, dict):
            candidates.append(result.get("response"))
            candidates.append(result.get("output"))
            candidates.append(result.get("summary"))
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text:
                return text
        return ""

    def _append_timeline(
        self,
        graph: dict[str, Any],
        *,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        timeline = graph.get("timeline")
        if not isinstance(timeline, list):
            timeline = []
            graph["timeline"] = timeline
        timeline.append(
            {
                "event": str(event or "").strip() or "supervisor_event",
                "at": _utcnow_iso(),
                "payload": dict(payload) if isinstance(payload, dict) else {},
            }
        )
        if len(timeline) > 10_000:
            del timeline[: len(timeline) - 10_000]
        graph["updated_at"] = _utcnow_iso()
        if self._telemetry_emitter is not None:
            self._telemetry_emitter(
                "supervisor_graph_event",
                {
                    "graph_id": str(graph.get("id") or ""),
                    "event": str(event or ""),
                    "status": str(graph.get("status") or ""),
                },
            )

    def _hydrate_from_database(self) -> None:
        if self._database is None:
            return
        try:
            persisted = self._database.list_supervisor_graphs(limit=self._max_graphs)
        except Exception:
            return
        if not isinstance(persisted, list):
            return
        with self._lock:
            for row in persisted:
                if not isinstance(row, dict):
                    continue
                normalized = self._normalize_loaded_graph(row)
                graph_id = str(normalized.get("id") or "").strip()
                if not graph_id:
                    continue
                self._graphs[graph_id] = normalized

    def _persist_graph(self, graph: dict[str, Any]) -> None:
        if self._database is None:
            return
        graph_id = str(graph.get("id") or "").strip()
        if not graph_id:
            return
        timeline = graph.get("timeline")
        checkpoint_count = len(timeline) if isinstance(timeline, list) else 0
        try:
            self._database.upsert_supervisor_graph(
                graph_id=graph_id,
                user_id=str(graph.get("user_id") or ""),
                status=str(graph.get("status") or "planned"),
                objective=str(graph.get("objective") or ""),
                graph=graph,
                checkpoint_count=checkpoint_count,
                created_at=str(graph.get("created_at") or _utcnow_iso()),
                updated_at=str(graph.get("updated_at") or _utcnow_iso()),
                launched_at=_optional_str(str(graph.get("launched_at") or "")),
                finished_at=_optional_str(str(graph.get("finished_at") or "")),
            )
        except Exception:
            if self._telemetry_emitter is not None:
                self._telemetry_emitter(
                    "supervisor_graph_persist_failed",
                    {
                        "graph_id": graph_id,
                    },
                )

    def _persist_graph_delete(self, graph_id: str) -> None:
        if self._database is None:
            return
        normalized = str(graph_id or "").strip()
        if not normalized:
            return
        try:
            self._database.delete_supervisor_graph(normalized)
        except Exception:
            if self._telemetry_emitter is not None:
                self._telemetry_emitter(
                    "supervisor_graph_delete_failed",
                    {
                        "graph_id": normalized,
                    },
                )

    @staticmethod
    def _normalize_loaded_graph(raw_graph: dict[str, Any]) -> dict[str, Any]:
        graph = deepcopy(raw_graph)
        graph["id"] = str(graph.get("id") or "").strip()
        graph["user_id"] = str(graph.get("user_id") or "").strip()
        graph["objective"] = str(graph.get("objective") or "").strip()
        status = str(graph.get("status") or "planned").strip().lower() or "planned"
        if status not in SUPERVISOR_GRAPH_STATUSES:
            status = "planned"
        graph["status"] = status
        graph["created_at"] = str(graph.get("created_at") or _utcnow_iso())
        graph["updated_at"] = str(graph.get("updated_at") or graph["created_at"])
        graph["launched_at"] = _optional_str(str(graph.get("launched_at") or ""))
        graph["finished_at"] = _optional_str(str(graph.get("finished_at") or ""))
        graph["default_session_id"] = _optional_str(str(graph.get("default_session_id") or ""))
        graph["metadata"] = dict(graph.get("metadata") or {}) if isinstance(graph.get("metadata"), dict) else {}

        raw_nodes = graph.get("nodes")
        node_map: dict[str, dict[str, Any]] = {}
        if isinstance(raw_nodes, dict):
            rows = [item for item in raw_nodes.values() if isinstance(item, dict)]
        elif isinstance(raw_nodes, list):
            rows = [item for item in raw_nodes if isinstance(item, dict)]
        else:
            rows = []

        for index, node in enumerate(rows):
            node_id = str(node.get("node_id") or f"node-{index + 1}").strip()
            if not node_id:
                continue
            if node_id in node_map:
                continue
            raw_order = node.get("order")
            try:
                normalized_order = int(raw_order)
            except Exception:
                normalized_order = index + 1
            if normalized_order <= 0:
                normalized_order = index + 1
            depends_on = node.get("depends_on")
            if isinstance(depends_on, list):
                dependencies = []
                for dep in depends_on:
                    dep_id = str(dep or "").strip()
                    if dep_id and dep_id not in dependencies:
                        dependencies.append(dep_id)
            else:
                dependencies = []
            node_map[node_id] = {
                "node_id": node_id,
                "order": normalized_order,
                "agent_id": str(node.get("agent_id") or "").strip(),
                "message": str(node.get("message") or "").strip(),
                "depends_on": dependencies,
                "max_attempts": node.get("max_attempts"),
                "budget": dict(node.get("budget") or {}) if isinstance(node.get("budget"), dict) else None,
                "metadata": dict(node.get("metadata") or {}) if isinstance(node.get("metadata"), dict) else {},
                "status": str(node.get("status") or "planned").strip().lower() or "planned",
                "run_id": _optional_str(str(node.get("run_id") or "")),
                "run_status": _optional_str(str(node.get("run_status") or "")),
                "attempts": _safe_int(node.get("attempts"), default=0, minimum=0),
                "created_at": str(node.get("created_at") or graph["created_at"]),
                "started_at": _optional_str(str(node.get("started_at") or "")),
                "completed_at": _optional_str(str(node.get("completed_at") or "")),
                "last_error": _optional_str(str(node.get("last_error") or "")),
            }
        graph["nodes"] = node_map

        gate_raw = graph.get("objective_gate")
        if not isinstance(gate_raw, dict):
            gate_raw = {}
        gate_mode = str(gate_raw.get("mode") or "auto").strip().lower() or "auto"
        if gate_mode not in _OBJECTIVE_GATE_MODES:
            gate_mode = "auto"
        gate_on_failure = str(gate_raw.get("on_failure") or "review_required").strip().lower() or "review_required"
        if gate_on_failure not in _OBJECTIVE_GATE_ON_FAILURE:
            gate_on_failure = "review_required"
        gate_keyword_match = str(gate_raw.get("keyword_match") or "any").strip().lower() or "any"
        if gate_keyword_match not in _OBJECTIVE_GATE_KEYWORD_MATCH:
            gate_keyword_match = "any"
        required_node_ids: list[str] = []
        raw_required_node_ids = gate_raw.get("required_node_ids")
        if isinstance(raw_required_node_ids, list):
            for item in raw_required_node_ids:
                node_id = str(item or "").strip()
                if node_id and node_id in node_map and node_id not in required_node_ids:
                    required_node_ids.append(node_id)
        if not required_node_ids:
            required_node_ids = sorted(node_map.keys())
        required_keywords: list[str] = []
        raw_keywords = gate_raw.get("required_keywords")
        if isinstance(raw_keywords, list):
            for item in raw_keywords:
                keyword = str(item or "").strip().lower()
                if keyword and keyword not in required_keywords:
                    required_keywords.append(keyword)
        objective_keywords: list[str] = []
        raw_objective_keywords = gate_raw.get("objective_keywords")
        if isinstance(raw_objective_keywords, list):
            for item in raw_objective_keywords:
                keyword = str(item or "").strip().lower()
                if keyword and keyword not in objective_keywords:
                    objective_keywords.append(keyword)
        if not objective_keywords:
            objective_keywords = SupervisorTaskGraphManager._objective_keywords(graph["objective"])
        graph["objective_gate"] = {
            "enabled": bool(gate_raw.get("enabled", True)),
            "mode": gate_mode,
            "on_failure": gate_on_failure,
            "required_node_ids": required_node_ids,
            "min_response_chars": _safe_int(gate_raw.get("min_response_chars"), default=0, minimum=0),
            "required_keywords": required_keywords,
            "keyword_match": gate_keyword_match,
            "objective_keywords": objective_keywords,
        }

        verification_raw = graph.get("objective_verification")
        if not isinstance(verification_raw, dict):
            verification_raw = {}
        verification_status = str(verification_raw.get("status") or "pending").strip().lower() or "pending"
        if verification_status not in _OBJECTIVE_VERIFICATION_STATUSES:
            verification_status = "pending"
        checks = verification_raw.get("checks")
        graph["objective_verification"] = {
            "status": verification_status,
            "checked_at": _optional_str(str(verification_raw.get("checked_at") or "")),
            "summary": str(verification_raw.get("summary") or "").strip() or "Objective verification pending.",
            "checks": [item for item in checks if isinstance(item, dict)] if isinstance(checks, list) else [],
            "last_failure_reasons": [
                str(item).strip()
                for item in list(verification_raw.get("last_failure_reasons") or [])
                if str(item).strip()
            ]
            if isinstance(verification_raw.get("last_failure_reasons"), list)
            else [],
            "manual_override": dict(verification_raw.get("manual_override") or {})
            if isinstance(verification_raw.get("manual_override"), dict)
            else None,
        }

        timeline = graph.get("timeline")
        graph["timeline"] = [item for item in timeline if isinstance(item, dict)] if isinstance(timeline, list) else []
        telemetry = graph.get("telemetry")
        if not isinstance(telemetry, dict):
            telemetry = {}
        graph["telemetry"] = {
            "ticks": _safe_int(telemetry.get("ticks"), default=0, minimum=0),
            "runs_started": _safe_int(telemetry.get("runs_started"), default=0, minimum=0),
            "runs_completed": _safe_int(telemetry.get("runs_completed"), default=0, minimum=0),
            "last_tick_at": _optional_str(str(telemetry.get("last_tick_at") or "")),
        }
        return graph

    def _evict_terminal_if_needed(self) -> None:
        if len(self._graphs) < self._max_graphs:
            return
        terminal = [
            item
            for item in self._graphs.values()
            if str(item.get("status") or "") in {"succeeded", "failed", "canceled"}
        ]
        if not terminal:
            return
        terminal.sort(key=lambda row: str(row.get("updated_at") or ""))
        to_remove = len(self._graphs) - self._max_graphs + 1
        for row in terminal[:to_remove]:
            graph_id = str(row.get("id") or "")
            if graph_id:
                self._graphs.pop(graph_id, None)
                self._persist_graph_delete(graph_id)


def _extract_run_error(run: dict[str, Any]) -> str | None:
    result = run.get("result")
    if isinstance(result, dict):
        raw_error = result.get("error")
        if isinstance(raw_error, str) and raw_error.strip():
            return raw_error.strip()
    raw_error = run.get("error")
    if isinstance(raw_error, str) and raw_error.strip():
        return raw_error.strip()
    return None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _optional_str(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _safe_int(value: Any, *, default: int = 0, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    if minimum is not None and parsed < minimum:
        return int(minimum)
    return parsed


def _snapshot(graph: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(graph)
    nodes = payload.get("nodes")
    if isinstance(nodes, dict):
        node_items = [item for item in nodes.values() if isinstance(item, dict)]
        node_items.sort(key=lambda item: int(item.get("order", 0)))
        payload["nodes"] = node_items
    return payload

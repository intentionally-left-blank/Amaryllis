from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

SUPERVISOR_GRAPH_STATUSES: set[str] = {"planned", "running", "succeeded", "failed", "canceled"}
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
            target_status = "succeeded"
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
        graph["status"] = str(graph.get("status") or "planned").strip().lower() or "planned"
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

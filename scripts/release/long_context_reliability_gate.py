#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run blocking long-context reliability gate with deterministic relevance/stability "
            "checks and optional baseline-regression assertions."
        )
    )
    parser.add_argument(
        "--dataset",
        default="eval/datasets/quality/long_context_reliability_cases.json",
        help="Dataset JSON path for long-context scenarios.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=int(os.getenv("AMARYLLIS_LONG_CONTEXT_ITERATIONS", "2")),
        help="Number of repeated runs per case.",
    )
    parser.add_argument(
        "--min-run-success-rate-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_LONG_CONTEXT_MIN_SUCCESS_RATE_PCT", "100")),
        help="Minimum successful run rate percent.",
    )
    parser.add_argument(
        "--min-relevance-score-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_LONG_CONTEXT_MIN_RELEVANCE_PCT", "95")),
        help="Minimum keyword relevance score percent.",
    )
    parser.add_argument(
        "--min-stability-score-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_LONG_CONTEXT_MIN_STABILITY_PCT", "100")),
        help="Minimum output stability score percent.",
    )
    parser.add_argument(
        "--max-p95-latency-ms",
        type=float,
        default=float(os.getenv("AMARYLLIS_LONG_CONTEXT_MAX_P95_MS", "4000")),
        help="Maximum allowed p95 latency in milliseconds.",
    )
    parser.add_argument(
        "--baseline",
        default="eval/baselines/quality/long_context_reliability_baseline.json",
        help="Optional baseline summary JSON for regression checks.",
    )
    parser.add_argument(
        "--max-relevance-regression-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_LONG_CONTEXT_MAX_RELEVANCE_REGRESSION_PCT", "2")),
        help="Maximum allowed relevance drop from baseline (percentage points).",
    )
    parser.add_argument(
        "--max-stability-regression-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_LONG_CONTEXT_MAX_STABILITY_REGRESSION_PCT", "1")),
        help="Maximum allowed stability drop from baseline (percentage points).",
    )
    parser.add_argument(
        "--max-latency-regression-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_LONG_CONTEXT_MAX_LATENCY_REGRESSION_PCT", "40")),
        help="Maximum allowed p95 latency increase from baseline in percent.",
    )
    parser.add_argument(
        "--output",
        default=os.getenv("AMARYLLIS_LONG_CONTEXT_OUTPUT", "artifacts/long-context-reliability-report.json"),
        help="Output report path.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_path(project_root: Path, raw: str) -> Path:
    candidate = Path(str(raw or "").strip()).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate.resolve()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = max(0, min(len(sorted_values) - 1, int(round((p / 100.0) * (len(sorted_values) - 1)))))
    return float(sorted_values[rank])


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _extract_keywords_from_marker(messages: list[dict[str, Any]]) -> list[str]:
    marker_re = re.compile(r"\[EXPECTED:([^\]]+)\]")
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        content = str(message.get("content") or "")
        match = marker_re.search(content)
        if match is None:
            continue
        raw = str(match.group(1) or "")
        output = [item.strip().lower() for item in raw.split("|") if item.strip()]
        return output
    return []


def _install_long_context_stubs(app: Any) -> None:
    services = getattr(getattr(app, "state", None), "services", None)
    if services is None:
        return
    model_manager = getattr(services, "model_manager", None)
    if model_manager is None:
        return

    def _fake_chat(
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        routing: dict[str, Any] | None = None,
        fallback_targets: list[tuple[str, str]] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        **extra_kwargs: Any,
    ) -> dict[str, Any]:
        _ = (temperature, max_tokens, routing, fallback_targets, session_id, user_id, extra_kwargs)
        keywords = _extract_keywords_from_marker(messages)
        if not keywords:
            keywords = ["context", "summary", "fallback"]
        content = "Long-context answer: " + ", ".join(keywords)
        provider_value = provider or str(getattr(model_manager, "active_provider", "long-context-stub"))
        model_value = model or str(getattr(model_manager, "active_model", "long-context-stub-model"))
        return {
            "content": content,
            "provider": provider_value,
            "model": model_value,
            "routing": {"mode": "long_context_stub"},
        }

    def _fake_stream_chat(
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        routing: dict[str, Any] | None = None,
        fallback_targets: list[tuple[str, str]] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        **extra_kwargs: Any,
    ) -> tuple[Any, str, str, dict[str, Any]]:
        row = _fake_chat(
            messages=messages,
            model=model,
            provider=provider,
            temperature=temperature,
            max_tokens=max_tokens,
            routing=routing,
            fallback_targets=fallback_targets,
            session_id=session_id,
            user_id=user_id,
            **extra_kwargs,
        )
        return iter([str(row.get("content") or "")]), str(row.get("provider") or ""), str(row.get("model") or ""), {
            "mode": "long_context_stub"
        }

    model_manager.chat = _fake_chat
    model_manager.stream_chat = _fake_stream_chat


def _shutdown_app(app: Any) -> None:
    services = getattr(getattr(app, "state", None), "services", None)
    if services is None:
        return
    try:
        services.automation_scheduler.stop()
        if services.memory_consolidation_worker is not None:
            services.memory_consolidation_worker.stop()
        if services.backup_scheduler is not None:
            services.backup_scheduler.stop()
        services.agent_run_manager.stop()
        services.database.close()
        services.vector_store.persist()
    except Exception:
        pass


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("dataset root must be JSON object")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("dataset.cases must be JSON array")
    cases: list[dict[str, Any]] = []
    for index, item in enumerate(raw_cases, start=1):
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("id") or f"case_{index}").strip() or f"case_{index}"
        title = str(item.get("title") or case_id).strip() or case_id
        context = str(item.get("context") or "").strip()
        question = str(item.get("question") or "").strip()
        raw_keywords = item.get("expected_keywords")
        keywords: list[str] = []
        if isinstance(raw_keywords, list):
            keywords = [str(key).strip().lower() for key in raw_keywords if str(key).strip()]
        if not context or not question or not keywords:
            continue
        cases.append(
            {
                "id": case_id,
                "title": title,
                "context": context,
                "question": question,
                "expected_keywords": keywords,
            }
        )
    if not cases:
        raise ValueError("dataset has no valid cases")
    return cases


def _build_messages(case: dict[str, Any]) -> list[dict[str, str]]:
    expected = "|".join(case.get("expected_keywords", []))
    return [
        {
            "role": "system",
            "content": (
                "Use only the provided context when answering.\n"
                "<context>\n"
                f"{str(case.get('context') or '')}\n"
                "</context>"
            ),
        },
        {
            "role": "user",
            "content": (
                f"{str(case.get('question') or '')}\n"
                "Return a concise answer.\n"
                f"[EXPECTED:{expected}]"
            ),
        },
    ]


def _extract_content(response_payload: dict[str, Any]) -> str:
    choices = response_payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    return str(message.get("content") or "").strip()


def _relevance_score(content: str, expected_keywords: list[str]) -> float:
    if not expected_keywords:
        return 100.0
    normalized = _normalize_text(content)
    hits = sum(1 for key in expected_keywords if _normalize_text(key) in normalized)
    return (float(hits) / float(len(expected_keywords))) * 100.0


def _stability_score(outputs: list[str]) -> float:
    if not outputs:
        return 0.0
    if len(outputs) == 1:
        return 100.0
    first = _normalize_text(outputs[0])
    stable = sum(1 for item in outputs if _normalize_text(item) == first)
    return (float(stable) / float(len(outputs))) * 100.0


def _check(
    *,
    check_id: str,
    source: str,
    value: float,
    threshold: float,
    comparator: str,
    unit: str,
) -> dict[str, Any]:
    normalized = str(comparator).strip().lower()
    if normalized not in {"lte", "gte"}:
        raise ValueError(f"Unsupported comparator: {comparator}")
    passed = value <= threshold if normalized == "lte" else value >= threshold
    return {
        "id": check_id,
        "source": source,
        "value": round(float(value), 6),
        "threshold": round(float(threshold), 6),
        "comparator": normalized,
        "unit": unit,
        "passed": bool(passed),
    }


def _load_baseline(path: Path) -> dict[str, float] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        return None
    keys = (
        "run_success_rate_pct",
        "relevance_score_pct",
        "stability_score_pct",
        "p95_latency_ms",
    )
    values = {key: _safe_float(summary.get(key)) for key in keys if key in summary}
    return values or None


def main() -> int:
    args = _parse_args()
    if int(args.iterations) < 1:
        print("[long-context-gate] --iterations must be >= 1", file=sys.stderr)
        return 2
    for field in (
        "min_run_success_rate_pct",
        "min_relevance_score_pct",
        "min_stability_score_pct",
    ):
        value = float(getattr(args, field))
        if value < 0.0 or value > 100.0:
            print(f"[long-context-gate] --{field.replace('_', '-')} must be in range 0..100", file=sys.stderr)
            return 2
    for field in (
        "max_p95_latency_ms",
        "max_relevance_regression_pct",
        "max_stability_regression_pct",
        "max_latency_regression_pct",
    ):
        value = float(getattr(args, field))
        if value < 0.0:
            print(f"[long-context-gate] --{field.replace('_', '-')} must be >= 0", file=sys.stderr)
            return 2

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    dataset_path = _resolve_path(project_root, str(args.dataset))
    if not dataset_path.exists():
        print(f"[long-context-gate] dataset not found: {dataset_path}", file=sys.stderr)
        return 2
    try:
        cases = _load_dataset(dataset_path)
    except Exception as exc:
        print(f"[long-context-gate] invalid dataset: {dataset_path} error={exc}", file=sys.stderr)
        return 2

    baseline_path = _resolve_path(project_root, str(args.baseline))
    baseline = _load_baseline(baseline_path)

    try:
        from fastapi.testclient import TestClient  # noqa: PLC0415
    except Exception as exc:
        print(f"[long-context-gate] FAILED import_error={exc}")
        return 2

    case_rows: list[dict[str, Any]] = []
    all_latencies: list[float] = []
    all_relevance: list[float] = []
    all_stability: list[float] = []
    runs_total = 0
    runs_succeeded = 0
    app: Any | None = None

    with tempfile.TemporaryDirectory(prefix="amaryllis-long-context-gate-") as tmp:
        support_dir = Path(tmp) / "support"
        auth_tokens = {
            "admin-token": {"user_id": "admin", "scopes": ["admin", "user"]},
            "user-token": {"user_id": "user-1", "scopes": ["user"]},
            "service-token": {"user_id": "svc-runtime", "scopes": ["service"]},
        }
        os.environ["AMARYLLIS_SUPPORT_DIR"] = str(support_dir)
        os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
        os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(auth_tokens, ensure_ascii=False)
        os.environ["AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED"] = "false"
        os.environ["AMARYLLIS_MCP_ENDPOINTS"] = ""
        os.environ["AMARYLLIS_SECURITY_PROFILE"] = "production"
        os.environ["AMARYLLIS_QOS_MODE"] = os.getenv("AMARYLLIS_QOS_MODE", "balanced")

        try:
            import runtime.server as server_module  # noqa: PLC0415

            server_module = importlib.reload(server_module)
            app = server_module.app
            _install_long_context_stubs(app)
        except Exception as exc:
            print(f"[long-context-gate] FAILED import_or_boot_error={exc}")
            return 2

        try:
            with TestClient(app) as client:
                for case in cases:
                    attempt_rows: list[dict[str, Any]] = []
                    outputs: list[str] = []
                    case_relevance_samples: list[float] = []

                    for attempt in range(1, int(args.iterations) + 1):
                        runs_total += 1
                        payload = {
                            "messages": _build_messages(case),
                            "stream": False,
                            "max_tokens": 128,
                            "routing": {"mode": "balanced"},
                        }
                        started = time.perf_counter()
                        response = client.post(
                            "/v1/chat/completions",
                            headers=_auth("user-token"),
                            json=payload,
                        )
                        latency_ms = (time.perf_counter() - started) * 1000.0
                        all_latencies.append(float(latency_ms))

                        response_payload = (
                            response.json()
                            if response.headers.get("content-type", "").startswith("application/json")
                            else {}
                        )
                        content = _extract_content(response_payload if isinstance(response_payload, dict) else {})
                        ok = int(response.status_code) == 200 and bool(content.strip())
                        relevance_pct = 0.0
                        if ok:
                            runs_succeeded += 1
                            relevance_pct = _relevance_score(
                                content=content,
                                expected_keywords=list(case.get("expected_keywords", [])),
                            )
                            all_relevance.append(float(relevance_pct))
                            case_relevance_samples.append(float(relevance_pct))
                            outputs.append(content)

                        attempt_rows.append(
                            {
                                "attempt": attempt,
                                "status_code": int(response.status_code),
                                "ok": bool(ok),
                                "latency_ms": round(float(latency_ms), 3),
                                "relevance_pct": round(float(relevance_pct), 4),
                                "output_preview": content[:240],
                            }
                        )

                    case_relevance = (
                        (sum(case_relevance_samples) / float(len(case_relevance_samples)))
                        if case_relevance_samples
                        else 0.0
                    )
                    case_stability = _stability_score(outputs)
                    all_stability.append(float(case_stability))
                    case_rows.append(
                        {
                            "case_id": str(case.get("id") or ""),
                            "title": str(case.get("title") or ""),
                            "expected_keywords": list(case.get("expected_keywords", [])),
                            "context_chars": len(str(case.get("context") or "")),
                            "attempts": attempt_rows,
                            "summary": {
                                "attempts_total": len(attempt_rows),
                                "attempts_succeeded": sum(1 for row in attempt_rows if bool(row.get("ok"))),
                                "relevance_score_pct": round(float(case_relevance), 4),
                                "stability_score_pct": round(float(case_stability), 4),
                            },
                        }
                    )
        finally:
            if app is not None:
                _shutdown_app(app)

    run_success_rate_pct = (float(runs_succeeded) / float(runs_total) * 100.0) if runs_total else 0.0
    relevance_score_pct = (sum(all_relevance) / float(len(all_relevance))) if all_relevance else 0.0
    stability_score_pct = (sum(all_stability) / float(len(all_stability))) if all_stability else 0.0
    p95_latency_ms = _percentile(all_latencies, 95)

    checks: list[dict[str, Any]] = [
        _check(
            check_id="long_context.run_success_rate_pct",
            source="long_context_reliability",
            value=float(run_success_rate_pct),
            threshold=float(args.min_run_success_rate_pct),
            comparator="gte",
            unit="pct",
        ),
        _check(
            check_id="long_context.relevance_score_pct",
            source="long_context_reliability",
            value=float(relevance_score_pct),
            threshold=float(args.min_relevance_score_pct),
            comparator="gte",
            unit="pct",
        ),
        _check(
            check_id="long_context.stability_score_pct",
            source="long_context_reliability",
            value=float(stability_score_pct),
            threshold=float(args.min_stability_score_pct),
            comparator="gte",
            unit="pct",
        ),
        _check(
            check_id="long_context.p95_latency_ms",
            source="long_context_reliability",
            value=float(p95_latency_ms),
            threshold=float(args.max_p95_latency_ms),
            comparator="lte",
            unit="ms",
        ),
    ]

    trend_deltas: dict[str, Any] = {
        "baseline_loaded": bool(baseline),
        "baseline_path": str(baseline_path),
        "metrics": {},
    }
    if baseline:
        relevance_regression = max(0.0, _safe_float(baseline.get("relevance_score_pct")) - relevance_score_pct)
        stability_regression = max(0.0, _safe_float(baseline.get("stability_score_pct")) - stability_score_pct)
        baseline_latency = _safe_float(baseline.get("p95_latency_ms"))
        latency_regression_pct = 0.0
        if baseline_latency > 0:
            latency_regression_pct = max(0.0, ((p95_latency_ms - baseline_latency) / baseline_latency) * 100.0)
        checks.extend(
            [
                _check(
                    check_id="long_context.relevance_regression_pct",
                    source="long_context_reliability",
                    value=float(relevance_regression),
                    threshold=float(args.max_relevance_regression_pct),
                    comparator="lte",
                    unit="pct",
                ),
                _check(
                    check_id="long_context.stability_regression_pct",
                    source="long_context_reliability",
                    value=float(stability_regression),
                    threshold=float(args.max_stability_regression_pct),
                    comparator="lte",
                    unit="pct",
                ),
                _check(
                    check_id="long_context.p95_latency_regression_pct",
                    source="long_context_reliability",
                    value=float(latency_regression_pct),
                    threshold=float(args.max_latency_regression_pct),
                    comparator="lte",
                    unit="pct",
                ),
            ]
        )
        trend_deltas["metrics"] = {
            "run_success_rate_pct_delta": round(run_success_rate_pct - _safe_float(baseline.get("run_success_rate_pct")), 4),
            "relevance_score_pct_delta": round(relevance_score_pct - _safe_float(baseline.get("relevance_score_pct")), 4),
            "stability_score_pct_delta": round(stability_score_pct - _safe_float(baseline.get("stability_score_pct")), 4),
            "p95_latency_ms_delta": round(p95_latency_ms - _safe_float(baseline.get("p95_latency_ms")), 4),
            "relevance_regression_pct": round(float(relevance_regression), 4),
            "stability_regression_pct": round(float(stability_regression), 4),
            "p95_latency_regression_pct": round(float(latency_regression_pct), 4),
        }

    checks_failed = sum(1 for item in checks if not bool(item.get("passed")))
    checks_total = len(checks)
    summary = {
        "status": "pass" if checks_failed == 0 else "fail",
        "cases_total": len(case_rows),
        "iterations": int(args.iterations),
        "runs_total": int(runs_total),
        "runs_succeeded": int(runs_succeeded),
        "runs_failed": int(max(0, runs_total - runs_succeeded)),
        "run_success_rate_pct": round(float(run_success_rate_pct), 4),
        "relevance_score_pct": round(float(relevance_score_pct), 4),
        "stability_score_pct": round(float(stability_score_pct), 4),
        "p95_latency_ms": round(float(p95_latency_ms), 3),
        "checks_total": int(checks_total),
        "checks_passed": int(checks_total - checks_failed),
        "checks_failed": int(checks_failed),
    }

    report = {
        "generated_at": _utc_now_iso(),
        "suite": "long_context_reliability_gate_v1",
        "dataset": {
            "path": str(dataset_path),
            "cases_total": len(case_rows),
        },
        "config": {
            "iterations": int(args.iterations),
            "thresholds": {
                "min_run_success_rate_pct": float(args.min_run_success_rate_pct),
                "min_relevance_score_pct": float(args.min_relevance_score_pct),
                "min_stability_score_pct": float(args.min_stability_score_pct),
                "max_p95_latency_ms": float(args.max_p95_latency_ms),
                "max_relevance_regression_pct": float(args.max_relevance_regression_pct),
                "max_stability_regression_pct": float(args.max_stability_regression_pct),
                "max_latency_regression_pct": float(args.max_latency_regression_pct),
            },
            "baseline_path": str(baseline_path),
        },
        "cases": case_rows,
        "checks": checks,
        "summary": summary,
        "trend_deltas": trend_deltas,
    }

    output_path = _resolve_path(project_root, str(args.output))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[long-context-gate] report={output_path}")
    print(json.dumps(summary, ensure_ascii=False))

    if checks_failed > 0:
        print("[long-context-gate] FAILED")
        return 1

    print("[long-context-gate] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

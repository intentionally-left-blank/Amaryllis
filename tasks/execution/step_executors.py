from __future__ import annotations

import html
import re
from typing import Any

import httpx

from tasks.step_registry import StepExecutionContext, StepExecutionResult, StepExecutorRegistry


def register_default_step_executors(registry: StepExecutorRegistry) -> None:
    registry.register("analyze_request", execute_step_analyze_request)
    registry.register(("fetch_source", "tool_query"), execute_step_fetch_source)
    registry.register("extract_facts", execute_step_extract_facts)
    registry.register(
        ("summarize", "synthesize", "merge_results"),
        execute_step_summarize_like,
    )
    registry.register(
        ("verify", "compare_targets"),
        execute_step_verify_like,
    )
    registry.register(
        ("decompose_subtasks", "subtask_execution", "answer_direct", "general"),
        execute_step_general,
    )


def execute_step_analyze_request(ctx: StepExecutionContext) -> StepExecutionResult:
    payload = _base_step_payload(ctx)
    focus_text = str(ctx.hints.get("task") or ctx.description).strip()
    lowered = focus_text.lower()
    constraints = [
        token
        for token in (
            "json",
            "markdown",
            "table",
            "bullet",
            "concise",
            "short",
            "detailed",
            "security",
            "anonymous",
            "strict",
            "quality",
            "deadline",
        )
        if token in lowered
    ]
    deliverables = [
        token
        for token in (
            "summary",
            "plan",
            "comparison",
            "analysis",
            "code",
            "report",
            "checklist",
        )
        if token in lowered
    ]
    payload.update(
        {
            "task_brief": focus_text[:500],
            "constraints": constraints[:8],
            "deliverables": deliverables[:6] or ["answer"],
        }
    )
    return StepExecutionResult.done(payload)


def execute_step_fetch_source(ctx: StepExecutionContext) -> StepExecutionResult:
    payload = _base_step_payload(ctx)
    source_urls = [item for item in _extract_urls_from_text(ctx.description) if item]
    hint_urls = ctx.hints.get("urls")
    if isinstance(hint_urls, list):
        for item in hint_urls:
            value = str(item).strip()
            if value and value not in source_urls:
                source_urls.append(value)
    query_text = str(ctx.hints.get("query") or ctx.hints.get("task") or ctx.description).strip()
    fetched_sources: list[dict[str, Any]] = []
    source_documents: list[dict[str, Any]] = []
    extracted_points: list[str] = []
    success_count = 0
    for url in source_urls[:5]:
        fetched = _fetch_source_url(url)
        fetched_sources.append(fetched)
        if not bool(fetched.get("ok")):
            continue
        success_count += 1
        text = str(fetched.get("text") or "").strip()
        if text:
            source_documents.append(
                {
                    "url": str(fetched.get("url") or url),
                    "title": str(fetched.get("title") or ""),
                    "text": text[:4500],
                    "char_count": int(fetched.get("char_count", 0)),
                }
            )
            for sentence in _extract_fact_candidates(text, max_items=4):
                if sentence not in extracted_points:
                    extracted_points.append(sentence)
                if len(extracted_points) >= 12:
                    break
        if len(extracted_points) >= 12:
            break

    payload.update(
        {
            "source_urls": source_urls[:8],
            "fetch_status": "ok" if success_count > 0 else ("failed" if source_urls else "no_targets"),
            "fetched_sources": fetched_sources,
            "source_documents": source_documents,
            "extracted_points": extracted_points,
            "tool_blueprint": {
                "intent": "fetch_content" if ctx.step_kind == "fetch_source" else "tool_query",
                "suggested_tools": ["web_search", "filesystem", "python_exec"],
                "query": query_text[:300],
                "targets": source_urls[:8],
                "executed": bool(source_urls),
                "attempted": len(fetched_sources),
                "succeeded": success_count,
            },
            "evidence": {
                "sources": [
                    {
                        "url": str(item.get("url") or ""),
                        "status_code": item.get("status_code"),
                        "content_type": item.get("content_type"),
                        "ok": bool(item.get("ok")),
                    }
                    for item in fetched_sources
                ],
                "snippet_count": len(extracted_points),
            },
        }
    )
    if source_urls and success_count == 0:
        return StepExecutionResult.failed("Unable to fetch any source URL.", payload=payload)
    return StepExecutionResult.done(payload)


def execute_step_extract_facts(ctx: StepExecutionContext) -> StepExecutionResult:
    payload = _base_step_payload(ctx)
    evidence_items = _collect_dependency_evidence_items(ctx.dependency_artifacts)
    extracted: list[str] = []
    facts: list[dict[str, Any]] = []
    seen_claims: set[str] = set()

    for source in evidence_items:
        source_id = str(source.get("source") or "").strip()
        text = str(source.get("text") or "").strip()
        if not text:
            continue
        for candidate in _extract_fact_candidates(text, max_items=8):
            normalized = candidate.lower()
            if normalized in seen_claims:
                continue
            seen_claims.add(normalized)
            extracted.append(candidate)
            facts.append(
                {
                    "claim": candidate,
                    "source": source_id,
                    "confidence": 0.65,
                    "evidence_snippet": candidate[:220],
                }
            )
            if len(extracted) >= 12:
                break
        if len(extracted) >= 12:
            break

    if not extracted:
        for point in ctx.dependency_points:
            text = str(point).strip()
            if not text:
                continue
            extracted.append(text[:260])
            facts.append(
                {
                    "claim": text[:260],
                    "source": "dependency_points",
                    "confidence": 0.5,
                    "evidence_snippet": text[:220],
                }
            )
            if len(extracted) >= 12:
                break

    payload.update(
        {
            "source_issue_ids": sorted(ctx.dependency_artifacts.keys()),
            "extracted_points": extracted,
            "facts": facts,
            "fact_count": len(extracted),
            "evidence": {
                "source_count": len({str(item.get("source") or "") for item in evidence_items if item.get("source")}),
                "evidence_items": evidence_items[:8],
            },
        }
    )
    if not extracted:
        return StepExecutionResult.failed(
            "No evidence-rich dependency content available for fact extraction.",
            payload=payload,
        )
    return StepExecutionResult.done(payload)


def execute_step_summarize_like(ctx: StepExecutionContext) -> StepExecutionResult:
    payload = _base_step_payload(ctx)
    summary_outline = ctx.dependency_points[:8]
    payload.update(
        {
            "source_issue_ids": sorted(ctx.dependency_artifacts.keys()),
            "summary_outline": summary_outline,
            "dependency_insights_count": len(ctx.dependency_points),
        }
    )
    return StepExecutionResult.done(payload)


def execute_step_verify_like(ctx: StepExecutionContext) -> StepExecutionResult:
    payload = _base_step_payload(ctx)
    checklist = [
        "Check coverage against task goals.",
        "Check internal consistency and contradictions.",
        "Check whether output format matches expected constraints.",
    ]
    if ctx.dependency_points:
        checklist.append("Validate claims against dependency artifacts.")

    claims = _collect_claims_for_verification(ctx.dependency_artifacts, ctx.dependency_points)
    evidence_items = _collect_dependency_evidence_items(
        ctx.dependency_artifacts,
        include_claim_lists=False,
    )
    verification_results: list[dict[str, Any]] = []
    supported = 0
    weak = 0
    missing = 0
    for claim in claims[:14]:
        match = _best_claim_support(claim=claim, evidence_items=evidence_items)
        status = str(match.get("status") or "missing_evidence")
        if status == "supported":
            supported += 1
        elif status == "weak_support":
            weak += 1
        else:
            missing += 1
        verification_results.append(
            {
                "claim": claim,
                "status": status,
                "score": float(match.get("score", 0.0)),
                "source": str(match.get("source") or ""),
                "evidence_snippet": str(match.get("snippet") or ""),
            }
        )

    passed = True
    if verification_results:
        passed = missing == 0
    summary = {
        "total_claims": len(verification_results),
        "supported": supported,
        "weak_support": weak,
        "missing_evidence": missing,
        "passed": passed,
    }
    payload.update(
        {
            "verification_checklist": checklist,
            "source_issue_ids": sorted(ctx.dependency_artifacts.keys()),
            "verification_results": verification_results,
            "verification_summary": summary,
            "evidence": {
                "claim_count": len(verification_results),
                "sources": sorted(
                    {
                        str(item.get("source") or "")
                        for item in verification_results
                        if str(item.get("source") or "").strip()
                    }
                ),
            },
        }
    )
    if verification_results and not passed:
        return StepExecutionResult.failed("Verification found unsupported claims.", payload=payload)
    return StepExecutionResult.done(payload)


def execute_step_general(ctx: StepExecutionContext) -> StepExecutionResult:
    payload = _base_step_payload(ctx)
    if ctx.dependency_points:
        payload["context_points"] = ctx.dependency_points[:8]
    return StepExecutionResult.done(payload)


def _base_step_payload(ctx: StepExecutionContext) -> dict[str, Any]:
    return {
        "description": ctx.description,
        "step_kind": ctx.step_kind,
        "requires_tools": ctx.requires_tools,
        "objective": ctx.objective,
        "expected_output": ctx.expected_output,
        "dependency_artifacts_count": len(ctx.dependency_artifacts),
    }


def _fetch_source_url(url: str) -> dict[str, Any]:
    normalized_url = str(url).strip()
    if not normalized_url:
        return {
            "url": "",
            "ok": False,
            "error": "empty_url",
            "status_code": None,
            "content_type": "",
        }
    try:
        response = httpx.get(
            normalized_url,
            timeout=8.0,
            follow_redirects=True,
            headers={"User-Agent": "Amaryllis/0.3 (TaskExecutor fetch_source)"},
        )
        content_type = str(response.headers.get("content-type") or "")
        raw_text = response.text if isinstance(response.text, str) else ""
        clean_text = _sanitize_html_to_text(raw_text)
        excerpt = clean_text[:320]
        title_match = re.search(
            r"<title[^>]*>(?P<title>.*?)</title>",
            raw_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        title = ""
        if title_match:
            title = html.unescape(re.sub(r"<[^>]+>", " ", title_match.group("title"))).strip()
        return {
            "url": normalized_url,
            "ok": 200 <= int(response.status_code) < 300,
            "status_code": int(response.status_code),
            "content_type": content_type,
            "final_url": str(response.url),
            "title": title[:180],
            "text": clean_text[:6000],
            "excerpt": excerpt,
            "char_count": len(clean_text),
            "error": "",
        }
    except Exception as exc:
        return {
            "url": normalized_url,
            "ok": False,
            "status_code": None,
            "content_type": "",
            "final_url": normalized_url,
            "title": "",
            "text": "",
            "excerpt": "",
            "char_count": 0,
            "error": str(exc),
        }


def _sanitize_html_to_text(raw_text: str) -> str:
    value = str(raw_text or "")
    without_scripts = re.sub(
        r"(?is)<(script|style).*?>.*?(</\1>)",
        " ",
        value,
    )
    without_tags = re.sub(r"(?s)<[^>]+>", " ", without_scripts)
    unescaped = html.unescape(without_tags)
    compact = re.sub(r"\s+", " ", unescaped).strip()
    return compact


def _extract_fact_candidates(text: str, *, max_items: int = 12) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return []
    sentences = re.split(r"(?<=[\.\!\?\n])\s+", normalized)
    result: list[str] = []
    seen: set[str] = set()
    for raw in sentences:
        sentence = str(raw).strip(" \t\r\n-•*")
        if len(sentence) < 20 or len(sentence) > 320:
            continue
        if not re.search(r"[A-Za-z0-9]", sentence):
            continue
        lowered = sentence.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(sentence)
        if len(result) >= max(1, max_items):
            break
    return result


def _collect_dependency_evidence_items(
    dependency_artifacts: dict[str, dict[str, Any]],
    *,
    include_claim_lists: bool = True,
) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for issue_id in sorted(dependency_artifacts.keys()):
        artifacts = dependency_artifacts.get(issue_id) or {}
        for artifact_key in sorted(artifacts.keys()):
            artifact = artifacts.get(artifact_key)
            if not isinstance(artifact, dict):
                continue
            source_prefix = f"{issue_id}/{artifact_key}"
            for field in ("description", "task_brief", "objective", "expected_output"):
                value = artifact.get(field)
                if isinstance(value, str) and value.strip():
                    evidence.append(
                        {
                            "source": source_prefix,
                            "text": value.strip()[:2000],
                        }
                    )
            source_docs = artifact.get("source_documents")
            if isinstance(source_docs, list):
                for item in source_docs:
                    if not isinstance(item, dict):
                        continue
                    source_url = str(item.get("url") or source_prefix).strip() or source_prefix
                    text = str(item.get("text") or "").strip()
                    if text:
                        evidence.append({"source": source_url, "text": text[:5000]})
            fetched_sources = artifact.get("fetched_sources")
            if isinstance(fetched_sources, list):
                for item in fetched_sources:
                    if not isinstance(item, dict):
                        continue
                    source_url = str(item.get("url") or source_prefix).strip() or source_prefix
                    excerpt = str(item.get("excerpt") or "").strip()
                    if excerpt:
                        evidence.append({"source": source_url, "text": excerpt[:1200]})
            if include_claim_lists:
                for list_field in ("extracted_points", "summary_outline", "context_points"):
                    points = artifact.get(list_field)
                    if isinstance(points, list):
                        for point in points:
                            text = str(point).strip()
                            if text:
                                evidence.append({"source": source_prefix, "text": text[:800]})
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in evidence:
        source = str(item.get("source") or "").strip()
        text = str(item.get("text") or "").strip()
        if not source or not text:
            continue
        key = (source, text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"source": source, "text": text})
        if len(deduped) >= 80:
            break
    return deduped


def _collect_claims_for_verification(
    dependency_artifacts: dict[str, dict[str, Any]],
    dependency_points: list[str],
) -> list[str]:
    claims: list[str] = []
    for issue_id in sorted(dependency_artifacts.keys()):
        artifacts = dependency_artifacts.get(issue_id) or {}
        for artifact in artifacts.values():
            if not isinstance(artifact, dict):
                continue
            for field in ("extracted_points", "summary_outline"):
                value = artifact.get(field)
                if isinstance(value, list):
                    for item in value:
                        text = str(item).strip()
                        if text:
                            claims.append(text[:260])
    claims.extend(str(item).strip()[:260] for item in dependency_points if str(item).strip())
    deduped: list[str] = []
    seen: set[str] = set()
    for claim in claims:
        lowered = claim.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(claim)
        if len(deduped) >= 24:
            break
    return deduped


def _best_claim_support(
    *,
    claim: str,
    evidence_items: list[dict[str, str]],
) -> dict[str, Any]:
    claim_tokens = _tokenize_overlap_tokens(claim)
    if not claim_tokens:
        return {"status": "missing_evidence", "score": 0.0, "source": "", "snippet": ""}
    best_score = 0.0
    best_source = ""
    best_snippet = ""
    for item in evidence_items:
        text = str(item.get("text") or "")
        if not text:
            continue
        text_tokens = _tokenize_overlap_tokens(text)
        if not text_tokens:
            continue
        overlap = len(claim_tokens & text_tokens)
        score = float(overlap) / float(max(1, len(claim_tokens)))
        if score <= best_score:
            continue
        best_score = score
        best_source = str(item.get("source") or "")
        snippet = text[:220].strip()
        best_snippet = snippet
    if best_score >= 0.5:
        status = "supported"
    elif best_score >= 0.25:
        status = "weak_support"
    else:
        status = "missing_evidence"
    return {
        "status": status,
        "score": round(best_score, 6),
        "source": best_source,
        "snippet": best_snippet,
    }


def _tokenize_overlap_tokens(text: str) -> set[str]:
    tokens = re.findall(r"[A-Za-z0-9]{3,}", str(text or "").lower())
    return {item for item in tokens if item}


def _extract_urls_from_text(text: str) -> list[str]:
    raw_items = re.findall(r"https?://[^\s)\]>]+", str(text or ""), flags=re.IGNORECASE)
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        url = str(item).strip().rstrip(".,;)")
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(url)
    return result

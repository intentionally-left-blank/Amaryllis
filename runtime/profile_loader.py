from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class ProfileLoadError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeProfileManifest:
    schema_version: int
    profile: str
    description: str
    slo_profile: str
    required_env: tuple[str, ...]
    env_defaults: dict[str, str]
    source_path: Path


@dataclass(frozen=True)
class SLOProfileManifest:
    schema_version: int
    profile: str
    description: str
    targets: dict[str, float | int]
    quality_budget: dict[str, float]
    source_path: Path


@dataclass(frozen=True)
class ProfiledEnvironment:
    env: dict[str, str]
    runtime: RuntimeProfileManifest
    slo: SLOProfileManifest


_DEFAULT_RUNTIME_PROFILE = "dev"
_PROFILE_SCHEMA_VERSION = 1
_REQUIRED_SLO_TARGET_KEYS: tuple[str, ...] = (
    "window_sec",
    "request_availability_target",
    "request_latency_p95_ms_target",
    "run_success_target",
    "min_request_samples",
    "min_run_samples",
    "incident_cooldown_sec",
)
_REQUIRED_QUALITY_BUDGET_KEYS: tuple[str, ...] = (
    "error_budget_burn_rate_requests",
    "error_budget_burn_rate_runs",
    "perf_max_p95_latency_ms",
    "perf_max_error_rate_pct",
)

_SLO_TARGET_ENV_MAP: dict[str, str] = {
    "window_sec": "AMARYLLIS_SLO_WINDOW_SEC",
    "request_availability_target": "AMARYLLIS_SLO_REQUEST_AVAILABILITY_TARGET",
    "request_latency_p95_ms_target": "AMARYLLIS_SLO_REQUEST_LATENCY_P95_MS_TARGET",
    "run_success_target": "AMARYLLIS_SLO_RUN_SUCCESS_TARGET",
    "min_request_samples": "AMARYLLIS_SLO_MIN_REQUEST_SAMPLES",
    "min_run_samples": "AMARYLLIS_SLO_MIN_RUN_SAMPLES",
    "incident_cooldown_sec": "AMARYLLIS_SLO_INCIDENT_COOLDOWN_SEC",
}

_QUALITY_BUDGET_ENV_MAP: dict[str, str] = {
    "error_budget_burn_rate_requests": "AMARYLLIS_SLO_BUDGET_REQUEST_BURN_RATE",
    "error_budget_burn_rate_runs": "AMARYLLIS_SLO_BUDGET_RUN_BURN_RATE",
    "perf_max_p95_latency_ms": "AMARYLLIS_PERF_BUDGET_MAX_P95_MS",
    "perf_max_error_rate_pct": "AMARYLLIS_PERF_BUDGET_MAX_ERROR_RATE_PCT",
}


def build_profiled_env(*, base_env: Mapping[str, str], project_root: Path | None = None) -> ProfiledEnvironment:
    root = _project_root(project_root)
    env = {str(key): str(value) for key, value in dict(base_env).items()}

    runtime_profile_name = _normalize_profile_name(
        env.get("AMARYLLIS_RUNTIME_PROFILE", _DEFAULT_RUNTIME_PROFILE)
    )
    runtime_profiles_dir = _path_from_env(
        env.get("AMARYLLIS_RUNTIME_PROFILE_DIR", ""),
        fallback=root / "runtime" / "profiles",
    )
    runtime_manifest = load_runtime_profile(
        runtime_profile_name,
        profiles_dir=runtime_profiles_dir,
    )

    for key, value in runtime_manifest.env_defaults.items():
        if str(env.get(key, "")).strip():
            continue
        env[key] = value

    selected_slo_profile = _normalize_profile_name(
        env.get("AMARYLLIS_SLO_PROFILE", runtime_manifest.slo_profile)
    )
    slo_profiles_dir = _path_from_env(
        env.get("AMARYLLIS_SLO_PROFILE_DIR", ""),
        fallback=root / "slo_profiles",
    )
    slo_manifest = load_slo_profile(
        selected_slo_profile,
        profiles_dir=slo_profiles_dir,
    )

    _apply_slo_defaults(env=env, slo_manifest=slo_manifest)

    missing_required = [name for name in runtime_manifest.required_env if not str(env.get(name, "")).strip()]
    if missing_required:
        missing_joined = ", ".join(sorted(missing_required))
        raise ProfileLoadError(
            f"Runtime profile '{runtime_manifest.profile}' requires non-empty env vars: {missing_joined}"
        )

    env["AMARYLLIS_RUNTIME_PROFILE"] = runtime_manifest.profile
    env["AMARYLLIS_SLO_PROFILE"] = slo_manifest.profile

    return ProfiledEnvironment(env=env, runtime=runtime_manifest, slo=slo_manifest)


def load_runtime_profile(profile: str, *, profiles_dir: Path) -> RuntimeProfileManifest:
    normalized = _normalize_profile_name(profile)
    source_path = profiles_dir / f"{normalized}.json"
    payload = _load_json_object(source_path)

    schema_version = _as_int(payload.get("schema_version"), key="schema_version")
    if schema_version != _PROFILE_SCHEMA_VERSION:
        raise ProfileLoadError(
            f"Runtime profile '{normalized}' has unsupported schema_version={schema_version}; "
            f"expected {_PROFILE_SCHEMA_VERSION}"
        )

    declared_profile = _normalize_profile_name(payload.get("profile"))
    if declared_profile != normalized:
        raise ProfileLoadError(
            f"Runtime profile file '{source_path}' declares profile='{declared_profile}', expected '{normalized}'"
        )

    description = str(payload.get("description") or "").strip()
    slo_profile = _normalize_profile_name(payload.get("slo_profile"))
    required_env = tuple(_as_string_list(payload.get("required_env"), key="required_env"))

    defaults_raw = payload.get("env_defaults")
    if not isinstance(defaults_raw, dict):
        raise ProfileLoadError(f"Runtime profile '{normalized}' field env_defaults must be an object")

    env_defaults: dict[str, str] = {}
    for key, value in defaults_raw.items():
        env_key = str(key or "").strip()
        if not env_key:
            raise ProfileLoadError(f"Runtime profile '{normalized}' has empty env_defaults key")
        env_defaults[env_key] = _stringify_env_value(value)

    return RuntimeProfileManifest(
        schema_version=schema_version,
        profile=normalized,
        description=description,
        slo_profile=slo_profile,
        required_env=required_env,
        env_defaults=env_defaults,
        source_path=source_path,
    )


def load_slo_profile(profile: str, *, profiles_dir: Path) -> SLOProfileManifest:
    normalized = _normalize_profile_name(profile)
    source_path = profiles_dir / f"{normalized}.json"
    payload = _load_json_object(source_path)

    schema_version = _as_int(payload.get("schema_version"), key="schema_version")
    if schema_version != _PROFILE_SCHEMA_VERSION:
        raise ProfileLoadError(
            f"SLO profile '{normalized}' has unsupported schema_version={schema_version}; "
            f"expected {_PROFILE_SCHEMA_VERSION}"
        )

    declared_profile = _normalize_profile_name(payload.get("profile"))
    if declared_profile != normalized:
        raise ProfileLoadError(
            f"SLO profile file '{source_path}' declares profile='{declared_profile}', expected '{normalized}'"
        )

    description = str(payload.get("description") or "").strip()

    targets_raw = payload.get("targets")
    if not isinstance(targets_raw, dict):
        raise ProfileLoadError(f"SLO profile '{normalized}' field targets must be an object")

    for key in _REQUIRED_SLO_TARGET_KEYS:
        if key not in targets_raw:
            raise ProfileLoadError(f"SLO profile '{normalized}' missing targets.{key}")

    targets: dict[str, float | int] = {
        "window_sec": _as_float(targets_raw.get("window_sec"), key="targets.window_sec", min_value=1.0),
        "request_availability_target": _as_float(
            targets_raw.get("request_availability_target"),
            key="targets.request_availability_target",
            min_value=0.5,
            max_value=0.9999,
        ),
        "request_latency_p95_ms_target": _as_float(
            targets_raw.get("request_latency_p95_ms_target"),
            key="targets.request_latency_p95_ms_target",
            min_value=1.0,
        ),
        "run_success_target": _as_float(
            targets_raw.get("run_success_target"),
            key="targets.run_success_target",
            min_value=0.5,
            max_value=0.9999,
        ),
        "min_request_samples": _as_int(
            targets_raw.get("min_request_samples"),
            key="targets.min_request_samples",
            min_value=1,
        ),
        "min_run_samples": _as_int(
            targets_raw.get("min_run_samples"),
            key="targets.min_run_samples",
            min_value=1,
        ),
        "incident_cooldown_sec": _as_float(
            targets_raw.get("incident_cooldown_sec"),
            key="targets.incident_cooldown_sec",
            min_value=1.0,
        ),
    }

    quality_budget_raw = payload.get("quality_budget")
    if not isinstance(quality_budget_raw, dict):
        raise ProfileLoadError(f"SLO profile '{normalized}' field quality_budget must be an object")

    for key in _REQUIRED_QUALITY_BUDGET_KEYS:
        if key not in quality_budget_raw:
            raise ProfileLoadError(f"SLO profile '{normalized}' missing quality_budget.{key}")

    quality_budget: dict[str, float] = {
        "error_budget_burn_rate_requests": _as_float(
            quality_budget_raw.get("error_budget_burn_rate_requests"),
            key="quality_budget.error_budget_burn_rate_requests",
            min_value=0.01,
        ),
        "error_budget_burn_rate_runs": _as_float(
            quality_budget_raw.get("error_budget_burn_rate_runs"),
            key="quality_budget.error_budget_burn_rate_runs",
            min_value=0.01,
        ),
        "perf_max_p95_latency_ms": _as_float(
            quality_budget_raw.get("perf_max_p95_latency_ms"),
            key="quality_budget.perf_max_p95_latency_ms",
            min_value=1.0,
        ),
        "perf_max_error_rate_pct": _as_float(
            quality_budget_raw.get("perf_max_error_rate_pct"),
            key="quality_budget.perf_max_error_rate_pct",
            min_value=0.0,
        ),
    }

    return SLOProfileManifest(
        schema_version=schema_version,
        profile=normalized,
        description=description,
        targets=targets,
        quality_budget=quality_budget,
        source_path=source_path,
    )


def _apply_slo_defaults(*, env: dict[str, str], slo_manifest: SLOProfileManifest) -> None:
    for key, env_key in _SLO_TARGET_ENV_MAP.items():
        if str(env.get(env_key, "")).strip():
            continue
        env[env_key] = _stringify_env_value(slo_manifest.targets[key])

    for key, env_key in _QUALITY_BUDGET_ENV_MAP.items():
        if str(env.get(env_key, "")).strip():
            continue
        env[env_key] = _stringify_env_value(slo_manifest.quality_budget[key])


def _project_root(project_root: Path | None) -> Path:
    if project_root is not None:
        return project_root.resolve()
    return Path(__file__).resolve().parents[1]


def _path_from_env(raw: str, *, fallback: Path) -> Path:
    candidate = str(raw or "").strip()
    if not candidate:
        return fallback.resolve()
    return Path(candidate).expanduser().resolve()


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ProfileLoadError(f"Profile manifest not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileLoadError(f"Invalid JSON in profile manifest {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileLoadError(f"Profile manifest {path} must contain a JSON object")
    return data


def _normalize_profile_name(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        raise ProfileLoadError("Profile name must be non-empty")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
    if any(ch not in allowed for ch in value):
        raise ProfileLoadError(f"Invalid profile name: {raw!r}")
    return value


def _as_string_list(value: Any, *, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProfileLoadError(f"Profile field {key} must be a list")
    items: list[str] = []
    for item in value:
        normalized = str(item or "").strip()
        if not normalized:
            raise ProfileLoadError(f"Profile field {key} must not contain empty values")
        items.append(normalized)
    return items


def _stringify_env_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _as_int(value: Any, *, key: str, min_value: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProfileLoadError(f"Profile field {key} must be an integer") from exc
    if min_value is not None and parsed < min_value:
        raise ProfileLoadError(f"Profile field {key} must be >= {min_value}")
    return parsed


def _as_float(
    value: Any,
    *,
    key: str,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ProfileLoadError(f"Profile field {key} must be a number") from exc
    if min_value is not None and parsed < min_value:
        raise ProfileLoadError(f"Profile field {key} must be >= {min_value}")
    if max_value is not None and parsed > max_value:
        raise ProfileLoadError(f"Profile field {key} must be <= {max_value}")
    return parsed

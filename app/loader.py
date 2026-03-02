from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import ValidationError as PydanticValidationError

from app.models import ModuleManifest


class ModuleLoaderError(Exception):
    pass


class ModuleDirectoryNotFound(ModuleLoaderError):
    pass


class ManifestError(ModuleLoaderError):
    pass


class EntrypointError(ModuleLoaderError):
    pass


@dataclass(frozen=True)
class LoadedModule:
    manifest: ModuleManifest
    run: Callable[[dict[str, Any]], dict[str, Any]]


class ModuleLoader:
    def __init__(self, modules_dir: Path | str = "modules") -> None:
        self.modules_dir = Path(modules_dir).resolve()

    def load(self, module_name: str) -> LoadedModule:
        module_dir = self._resolve_module_dir(module_name)
        manifest = self._load_manifest(module_dir)

        if manifest.runtime_api != "1.0":
            raise ManifestError(
                f"Unsupported runtime_api '{manifest.runtime_api}' for module '{module_name}'. Expected '1.0'."
            )

        run_callable = self._load_run_callable(module_dir, manifest.entrypoint)
        return LoadedModule(manifest=manifest, run=run_callable)

    def _resolve_module_dir(self, module_name: str) -> Path:
        module_dir = (self.modules_dir / module_name).resolve()

        try:
            module_dir.relative_to(self.modules_dir)
        except ValueError as exc:
            raise ModuleDirectoryNotFound(f"Module '{module_name}' was not found.") from exc

        if not module_dir.is_dir():
            raise ModuleDirectoryNotFound(f"Module '{module_name}' was not found.")

        return module_dir

    def _load_manifest(self, module_dir: Path) -> ModuleManifest:
        manifest_path = module_dir / "module.yaml"

        if not manifest_path.is_file():
            raise ManifestError(f"Missing manifest file: '{manifest_path}'.")

        try:
            with manifest_path.open("r", encoding="utf-8") as manifest_file:
                raw_manifest = yaml.safe_load(manifest_file)
        except yaml.YAMLError as exc:
            raise ManifestError(f"Invalid YAML in '{manifest_path}': {exc}") from exc

        if not isinstance(raw_manifest, dict):
            raise ManifestError(f"Manifest '{manifest_path}' must contain a YAML object.")

        try:
            return ModuleManifest.model_validate(raw_manifest)
        except PydanticValidationError as exc:
            raise ManifestError(f"Manifest validation error in '{manifest_path}': {exc}") from exc

    def _load_run_callable(
        self, module_dir: Path, entrypoint: str
    ) -> Callable[[dict[str, Any]], dict[str, Any]]:
        entrypoint_path = (module_dir / entrypoint).resolve()

        try:
            entrypoint_path.relative_to(module_dir)
        except ValueError as exc:
            raise EntrypointError("Entrypoint must be inside module directory.") from exc

        if not entrypoint_path.is_file():
            raise EntrypointError(f"Entrypoint file not found: '{entrypoint_path}'.")

        import_name = f"amaryllis_module_{module_dir.name}"
        spec = importlib.util.spec_from_file_location(import_name, entrypoint_path)

        if spec is None or spec.loader is None:
            raise EntrypointError(f"Unable to load entrypoint: '{entrypoint_path}'.")

        module = importlib.util.module_from_spec(spec)

        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise EntrypointError(f"Failed to import entrypoint '{entrypoint_path}': {exc}") from exc

        run_callable = getattr(module, "run", None)
        if run_callable is None or not callable(run_callable):
            raise EntrypointError(
                f"Entrypoint '{entrypoint_path}' must define callable function run(context: dict) -> dict."
            )

        return run_callable

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
    module_dir: Path
    entrypoint_path: Path


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

        entrypoint_path = self._resolve_entrypoint_path(module_dir, manifest.entrypoint)
        return LoadedModule(
            manifest=manifest,
            module_dir=module_dir,
            entrypoint_path=entrypoint_path,
        )

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

    def _resolve_entrypoint_path(self, module_dir: Path, entrypoint: str) -> Path:
        entrypoint_path = (module_dir / entrypoint).resolve()

        try:
            entrypoint_path.relative_to(module_dir)
        except ValueError as exc:
            raise EntrypointError("Entrypoint must be inside module directory.") from exc

        if not entrypoint_path.is_file():
            raise EntrypointError(f"Entrypoint file not found: '{entrypoint_path}'.")

        return entrypoint_path

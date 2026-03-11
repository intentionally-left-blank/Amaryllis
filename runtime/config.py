from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    app_name: str
    host: str
    port: int
    support_dir: Path
    models_dir: Path
    data_dir: Path
    plugins_dir: Path
    database_path: Path
    vector_index_path: Path
    telemetry_path: Path
    default_provider: str
    default_model: str
    ollama_base_url: str
    enable_ollama_fallback: bool
    openai_base_url: str
    openai_api_key: str | None
    anthropic_base_url: str
    anthropic_api_key: str | None
    openrouter_base_url: str
    openrouter_api_key: str | None

    @classmethod
    def from_env(cls) -> "AppConfig":
        support_dir = Path(
            os.getenv(
                "AMARYLLIS_SUPPORT_DIR",
                str(Path.home() / "Library" / "Application Support" / "amaryllis"),
            )
        ).expanduser()

        models_dir = Path(
            os.getenv(
                "AMARYLLIS_MODELS_DIR",
                str(support_dir / "models"),
            )
        ).expanduser()

        data_dir = Path(
            os.getenv(
                "AMARYLLIS_DATA_DIR",
                str(support_dir / "data"),
            )
        ).expanduser()

        plugins_dir = Path(
            os.getenv(
                "AMARYLLIS_PLUGINS_DIR",
                str(Path.cwd() / "plugins"),
            )
        ).expanduser()

        database_path = Path(
            os.getenv(
                "AMARYLLIS_DATABASE_PATH",
                str(data_dir / "amaryllis.db"),
            )
        ).expanduser()

        vector_index_path = Path(
            os.getenv(
                "AMARYLLIS_VECTOR_INDEX_PATH",
                str(data_dir / "semantic.index"),
            )
        ).expanduser()

        telemetry_path = Path(
            os.getenv(
                "AMARYLLIS_TELEMETRY_PATH",
                str(data_dir / "telemetry.jsonl"),
            )
        ).expanduser()

        fallback_raw = os.getenv("AMARYLLIS_OLLAMA_FALLBACK", "true").strip().lower()
        enable_ollama_fallback = fallback_raw in {"1", "true", "yes", "on"}

        return cls(
            app_name="Amaryllis",
            host=os.getenv("AMARYLLIS_HOST", "localhost"),
            port=int(os.getenv("AMARYLLIS_PORT", "8000")),
            support_dir=support_dir,
            models_dir=models_dir,
            data_dir=data_dir,
            plugins_dir=plugins_dir,
            database_path=database_path,
            vector_index_path=vector_index_path,
            telemetry_path=telemetry_path,
            default_provider=os.getenv("AMARYLLIS_DEFAULT_PROVIDER", "mlx"),
            default_model=os.getenv(
                "AMARYLLIS_DEFAULT_MODEL",
                "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
            ),
            ollama_base_url=os.getenv("AMARYLLIS_OLLAMA_URL", "http://localhost:11434"),
            enable_ollama_fallback=enable_ollama_fallback,
            openai_base_url=os.getenv("AMARYLLIS_OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            openai_api_key=(os.getenv("AMARYLLIS_OPENAI_API_KEY") or "").strip() or None,
            anthropic_base_url=os.getenv("AMARYLLIS_ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1").rstrip(
                "/"
            ),
            anthropic_api_key=(os.getenv("AMARYLLIS_ANTHROPIC_API_KEY") or "").strip() or None,
            openrouter_base_url=os.getenv("AMARYLLIS_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
            openrouter_api_key=(os.getenv("AMARYLLIS_OPENROUTER_API_KEY") or "").strip() or None,
        )

    def ensure_directories(self) -> None:
        self.support_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

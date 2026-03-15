from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Callable, Iterator

from models.model_manager import ModelManager
from runtime.config import AppConfig
from storage.database import Database


class _FakeDownloadProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def list_models(self) -> list[dict[str, Any]]:
        return []

    def suggested_models(self, limit: int = 100) -> list[dict[str, Any]]:
        _ = limit
        return [{"id": "fake/model", "label": "Fake Model"}]

    def capabilities(self) -> dict[str, Any]:
        return {
            "local": True,
            "supports_download": True,
            "supports_load": True,
            "supports_stream": True,
            "supports_tools": False,
            "requires_api_key": False,
        }

    def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "detail": "fake"}

    def download_model(
        self,
        model_id: str,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if callable(progress_callback):
            progress_callback(
                {
                    "status": "running",
                    "progress": 0.35,
                    "completed_bytes": 35,
                    "total_bytes": 100,
                    "message": "phase-1",
                }
            )
        time.sleep(0.05)
        if self.fail:
            raise RuntimeError("simulated download failure")
        if callable(progress_callback):
            progress_callback(
                {
                    "status": "running",
                    "progress": 0.9,
                    "completed_bytes": 90,
                    "total_bytes": 100,
                    "message": "phase-2",
                }
            )
        return {
            "status": "downloaded",
            "provider": "mlx",
            "model": model_id,
            "size_bytes": 100,
        }

    def load_model(self, model_id: str) -> dict[str, Any]:
        return {"status": "loaded", "provider": "mlx", "model": model_id}

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        _ = (messages, model, temperature, max_tokens)
        return "ok"

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> Iterator[str]:
        _ = (messages, model, temperature, max_tokens)
        return iter(["ok"])


class ModelDownloadJobsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-download-jobs-")
        self.base = Path(self._tmp.name)
        self._original_env = os.environ.copy()

        os.environ["AMARYLLIS_SUPPORT_DIR"] = str(self.base / "support")
        os.environ["AMARYLLIS_DATA_DIR"] = str(self.base / "support" / "data")
        os.environ["AMARYLLIS_MODELS_DIR"] = str(self.base / "support" / "models")
        os.environ["AMARYLLIS_PLUGINS_DIR"] = str(self.base / "plugins")
        os.environ["AMARYLLIS_DATABASE_PATH"] = str(self.base / "support" / "data" / "state.db")
        os.environ["AMARYLLIS_VECTOR_INDEX_PATH"] = str(self.base / "support" / "data" / "semantic.index")
        os.environ["AMARYLLIS_TELEMETRY_PATH"] = str(self.base / "support" / "data" / "telemetry.jsonl")
        os.environ["AMARYLLIS_DEFAULT_PROVIDER"] = "mlx"
        os.environ["AMARYLLIS_DEFAULT_MODEL"] = "fake/model"
        os.environ["AMARYLLIS_AUTH_TOKENS"] = "token-1:user-1:user"

        self.config = AppConfig.from_env()
        self.config.ensure_directories()
        self.database = Database(self.config.database_path)

    def tearDown(self) -> None:
        self.database.close()
        os.environ.clear()
        os.environ.update(self._original_env)
        self._tmp.cleanup()

    def _wait_for_terminal(self, manager: ModelManager, job_id: str) -> dict[str, Any]:
        deadline = time.time() + 3.0
        while time.time() < deadline:
            job = manager.get_model_download_job(job_id)
            if str(job.get("status")) in {"succeeded", "failed"}:
                return job
            time.sleep(0.05)
        self.fail("download job did not finish in time")

    def test_model_download_job_success(self) -> None:
        manager = ModelManager(config=self.config, database=self.database)
        manager.providers = {"mlx": _FakeDownloadProvider(fail=False)}

        started = manager.start_model_download(model_id="fake/model", provider="mlx")
        self.assertIn("job", started)
        job_id = str(started["job"]["id"])

        finished = self._wait_for_terminal(manager, job_id)
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(finished["progress"], 1.0)
        self.assertEqual(finished.get("completed_bytes"), 100)

        listing = manager.list_model_download_jobs(limit=10)
        self.assertGreaterEqual(listing["count"], 1)

    def test_model_download_job_failure(self) -> None:
        manager = ModelManager(config=self.config, database=self.database)
        manager.providers = {"mlx": _FakeDownloadProvider(fail=True)}

        started = manager.start_model_download(model_id="fake/model", provider="mlx")
        job_id = str(started["job"]["id"])

        finished = self._wait_for_terminal(manager, job_id)
        self.assertEqual(finished["status"], "failed")
        self.assertIn("simulated download failure", str(finished.get("error", "")))


if __name__ == "__main__":
    unittest.main()

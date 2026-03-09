from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

from agents.agent_manager import AgentManager
from api.agent_api import router as agent_router
from api.chat_api import router as chat_router
from api.model_api import router as model_router
from controller.meta_controller import MetaController
from memory.episodic_memory import EpisodicMemory
from memory.memory_manager import MemoryManager
from memory.semantic_memory import SemanticMemory
from memory.user_memory import UserMemory
from models.model_manager import ModelManager
from planner.planner import Planner
from runtime.config import AppConfig
from storage.database import Database
from storage.vector_store import VectorStore
from tasks.task_executor import TaskExecutor
from tools.tool_executor import ToolExecutor
from tools.tool_registry import ToolRegistry


@dataclass
class ServiceContainer:
    config: AppConfig
    database: Database
    vector_store: VectorStore
    model_manager: ModelManager
    memory_manager: MemoryManager
    tool_registry: ToolRegistry
    tool_executor: ToolExecutor
    meta_controller: MetaController
    planner: Planner
    task_executor: TaskExecutor
    agent_manager: AgentManager


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("amaryllis.runtime")


def create_services() -> ServiceContainer:
    config = AppConfig.from_env()
    config.ensure_directories()

    database = Database(config.database_path)
    vector_store = VectorStore(config.vector_index_path)

    episodic = EpisodicMemory(database)
    semantic = SemanticMemory(database, vector_store)
    user_memory = UserMemory(database)
    memory_manager = MemoryManager(episodic=episodic, semantic=semantic, user_memory=user_memory)

    tool_registry = ToolRegistry()
    tool_registry.load_builtin_tools()
    tool_registry.discover_plugins(config.plugins_dir)

    tool_executor = ToolExecutor(tool_registry)

    model_manager = ModelManager(config=config, database=database)

    meta_controller = MetaController()
    planner = Planner()
    task_executor = TaskExecutor(
        model_manager=model_manager,
        memory_manager=memory_manager,
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        meta_controller=meta_controller,
        planner=planner,
    )

    agent_manager = AgentManager(database=database, task_executor=task_executor)

    return ServiceContainer(
        config=config,
        database=database,
        vector_store=vector_store,
        model_manager=model_manager,
        memory_manager=memory_manager,
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        meta_controller=meta_controller,
        planner=planner,
        task_executor=task_executor,
        agent_manager=agent_manager,
    )


def create_app() -> FastAPI:
    services = create_services()

    app = FastAPI(
        title="Amaryllis Runtime",
        version="0.1.0",
        description="Local AI brain node runtime for macOS.",
    )
    app.state.services = services

    app.include_router(chat_router)
    app.include_router(model_router)
    app.include_router(agent_router)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "app": services.config.app_name,
            "active_provider": services.model_manager.active_provider,
            "active_model": services.model_manager.active_model,
        }

    @app.on_event("shutdown")
    def shutdown_event() -> None:
        logger.info("shutdown_start")
        services.database.close()
        services.vector_store.persist()
        logger.info("shutdown_done")

    return app


app = create_app()

from __future__ import annotations

from common.config import Settings
from common.db import Database
from common.logging_utils import configure_logging, get_logger
from common.vector_store import LightweightVectorStore


class BaseAgent:
    def __init__(self, settings: Settings, db: Database, vector_store: LightweightVectorStore):
        self.settings = settings
        self.db = db
        self.vector_store = vector_store
        configure_logging(
            settings.logs_dir,
            level=settings.get("runtime", "log_level", "INFO"),
        )
        self.logger = get_logger(self.__class__.__name__)

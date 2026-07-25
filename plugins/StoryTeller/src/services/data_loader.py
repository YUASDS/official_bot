from __future__ import annotations

from pathlib import Path
from typing import Any

import ujson
from loguru import logger

Separator = "\n------------------\n"

class DataLoader:
    """Game Data Manager (Singleton)"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.load_data()
        return cls._instance

    def load_data(self) -> None:
        """Load all JSON data files"""
        # Adjust path to find data directory relative to this file
        # src/services/data_loader.py -> ... -> plugins/StoryTeller/data
        base_path = Path(__file__).parent.parent.parent.joinpath("data")

        try:
            self.reply_data = self._load_json(base_path / "reply_data.json")
            self.goods_data = self._load_json(base_path / "goods_data.json")
            self.check_point = self._load_json(base_path / "check_point.json")
            self.monster_data = self._load_json(base_path / "monster_data.json")
            self.shop_data = self._load_json(base_path / "shop_data.json")
            self.environment_data = self._load_json(base_path / "environment_data.json")
            self.event_data = self._load_json(base_path / "event_data.json")
            logger.info("Game data loaded successfully.")
        except Exception as e:
            logger.exception(f"Failed to load game data: {e}")
            # Initialize with empty dicts to prevent crashes
            self.reply_data = {}
            self.goods_data = {}
            self.check_point = {}
            self.monster_data = {}
            self.shop_data = {}
            self.environment_data = {}
            self.event_data = {}

    def _load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            logger.warning(f"File not found: {path}")
            return {}
        try:
            with open(path, encoding="utf-8-sig") as f:
                return ujson.load(f)
        except Exception as e:
            logger.error(f"Error reading {path}: {e}")
            return {}

    def get_event(self, day: str | int) -> str:
        return self.reply_data.get(str(day), "")

# Global instance
data_loader = DataLoader()

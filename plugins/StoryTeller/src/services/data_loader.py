from __future__ import annotations

from pathlib import Path
from typing import Any

import ujson
from loguru import logger

Separator = "\n------------------\n"

# 每日彩蛋互斥链缺省触发器（唯一数据定义：reply_data.json `triggers` 段缺失时的回退，
# 顺序 = 现状链 hidden[jk/qiren] → guest → npc → gm_room；各插件只读，不各自定义）。
_DEFAULT_TRIGGERS = [
    {"id": "jk", "group": "hidden", "kind": "hidden", "priority": 1},
    {"id": "qiren", "group": "hidden", "kind": "hidden", "priority": 1},
    {"id": "guest", "kind": "guest", "priority": 2},
    {"id": "npc", "kind": "npc", "priority": 3},
    {"id": "gm_room", "kind": "gm_room", "priority": 4, "after_monster": True},
]

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
        base_path = Path(__file__).parent.parent.parent.joinpath("data")

        try:
            self.reply_data = self._load_json(base_path / "reply_data.json")
            self.goods_data = self._load_json(base_path / "goods_data.json")
            self.check_point = self._load_json(base_path / "check_point.json")
            self.monster_data = self._load_json(base_path / "monster_data.json")
            self.shop_data = self._load_json(base_path / "shop_data.json")
            self.environment_data = self._load_json(base_path / "environment_data.json")
            self.event_data = self._load_json(base_path / "event_data.json")
            self.spell_data = self._load_json(base_path / "spell_data.json")
            self.text_data = self._load_json(base_path / "text_data.json")
            self.ending_data = self._load_json(base_path / "ending_data.json")
            self.npc_data = self._load_json(base_path / "npc_data.json")
            self.display_data = self._load_json(base_path / "display_data.json")
            self._validate_monster_data()
        except Exception as e:
            logger.exception(f"Failed to load game data: {e}")
            self.reply_data = {}
            self.goods_data = {}
            self.check_point = {}
            self.monster_data = {}
            self.shop_data = {}
            self.environment_data = {}
            self.event_data = {}
            self.spell_data = {}
            self.text_data = {}
            self.ending_data = {}
            self.npc_data = {}
            self.display_data = {}
            return
        # 配置全量校验（fail-fast / warn 双模式，见 config_validator 文档）：
        # 所有 JSON 加载成功后统一校验一次；fail 模式抛错阻止启动（不在上面的
        # except 内，避免被吞掉导致数据被清空后仍继续运行）。
        from .config_validator import run_config_validation

        run_config_validation(base_path)
        logger.info("Game data loaded successfully.")

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

    def _validate_monster_data(self) -> None:
        """校验怪物攻击条目的伤害字段（伤害展示与战斗结算同源的前提）。"""
        for mid, monster in self.monster_data.items():
            for key, action in (monster.get("攻击") or {}).items():
                dmg = action.get("damage")
                if not isinstance(dmg, str) or not dmg:
                    logger.warning(
                        f"monster {mid} ({monster.get('名字')}) 攻击[{key}] 缺少 damage 字段"
                    )

    def get_event(self, day: str | int, flags: dict | None = None) -> str:
        """每日叙事文本；传入 flags 时追加命中 past.* 的「周目联动变体句」（引号块）。

        变体数据在 reply_data.json 的 `story_variants` 段，按日组织：
        [{"flag": "past.hound", "value": "killed", "text": "..."}]——原文一字不改。
        """
        text = self.reply_data.get("event", {}).get(str(day), "")
        variant = self._get_story_variant(str(day), flags)
        if not variant:
            return text
        return f"{text}\n\n{variant}"

    def _get_story_variant(self, day: str, flags: dict | None) -> str:
        """匹配 story_variants[day] 中 flags 命中的变体句（多条全追加，保序）。"""
        if not flags:
            return ""
        variants = (self.reply_data.get("story_variants") or {}).get(day) or []
        lines = []
        for v in variants:
            if not isinstance(v, dict):
                continue
            key = v.get("flag")
            if key and flags.get(key) == v.get("value") and v.get("text"):
                lines.append(f"> {v['text']}")
        return "\n".join(lines) if lines else ""

    def get_triggers(self) -> list[dict[str, Any]]:
        """每日彩蛋互斥链触发器配置（reply_data.json `triggers` 段；缺失回退默认链）。

        统一缺省回退入口：_DEFAULT_TRIGGERS 定义在 data_loader（单一数据定义），
        各插件/adventure 只读不定义，保证无 triggers 段时行为与现状一致。
        """
        cfg = self.reply_data.get("triggers") or []
        return cfg or list(_DEFAULT_TRIGGERS)

    def get_trigger(self, tid: str) -> dict[str, Any]:
        """按 id 读取每日彩蛋触发配置（reply_data.json `triggers` 段）。

        triggers 段为统一「触发」schema 列表（id/group/kind/priority/day_min/day_max/
        dice/value/relation/items…）；缺省回退默认链，未命中回退空 dict，
        调用方用 get(key, 缺省) 兜底。
        """
        for t in self.get_triggers():
            if isinstance(t, dict) and t.get("id") == tid:
                return t
        return {}

    def get_text(self, key: str, default: str = "", **kwargs: Any) -> str:
        """从 text_data.json 按 key 取文本，支持 {placeholder} 格式化。

        key 支持点号路径，如 "battle.no_melee_weapon"。
        """
        value = self.text_data.get(key)
        if value is None and "." in key:
            section, sub = key.split(".", 1)
            value = self.text_data.get(section, {}).get(sub)
        if value is None:
            value = default if default else key
        if kwargs:
            try:
                value = value.format(**kwargs)
            except (KeyError, IndexError, ValueError) as e:
                logger.warning(
                    f"Text format failed for key '{key}' with args {kwargs}: {e}"
                )
        return value

# Global instance
data_loader = DataLoader()

"""内容目录化 第2+3期 一次性迁移脚本：BOSS 目录化 + 世界目录化。

- `data/boss_data.json`（{id: BOSS配置}）→ `data/boss/<id>.json`（每 BOSS 一个文件，字段逐条原样）
- `data/flow_data.json`（{flow_id: flow配置}）三世界（sword_magic/dnd_dungeon/anime_academy）
  → `data/worlds/<世界id>.json`（顶层 {id, 名字, 季:{s1:{...}}}，季.s1 = 原 flow 条目 minus id/名字）
  → `lane_tale`（普通剧情流程）保留在 flow_data.json 原文件不动
- 季级「触发概率」按设计要点 4 补入 s1（value=3 与全局 guest 缺省一致，零行为变化）
- 迁移后原文件由调用方归档 test/archive/（本脚本只产出，不删除）

运行：
  env PYTHONPATH= E:/anaconda3/envs/official_bot/python.exe test/scripts/migrate_content_directories.py
"""
import sys
from pathlib import Path

import ujson

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "plugins" / "StoryTeller" / "data"

_GUEST_WORLDS = ("sword_magic", "dnd_dungeon", "anime_academy")


def dump(obj) -> str:
    return ujson.dumps(
        obj, ensure_ascii=False, indent=2, escape_forward_slashes=False
    ) + "\n"


def migrate_bosses() -> None:
    src = DATA / "boss_data.json"
    data = ujson.loads(src.read_text(encoding="utf-8-sig"))
    out_dir = DATA / "boss"
    out_dir.mkdir(parents=True, exist_ok=True)
    for bid, cfg in data.items():
        out = out_dir / f"{bid}.json"
        out.write_text(dump(cfg), encoding="utf-8")
        # 逐条校验：写入内容与原条目字段级一致（dict 相等）
        reloaded = ujson.loads(out.read_text(encoding="utf-8-sig"))
        assert reloaded == cfg, f"{out} 迁移前后不一致"
        print(f"BOSS  {bid:>6} -> {out.relative_to(ROOT)}  ({len(cfg)} 顶层字段)")


def migrate_worlds() -> None:
    src = DATA / "flow_data.json"
    data = ujson.loads(src.read_text(encoding="utf-8-sig"))
    out_dir = DATA / "worlds"
    out_dir.mkdir(parents=True, exist_ok=True)
    for wid in _GUEST_WORLDS:
        entry = data[wid]
        # 季.s1 = 原 flow 条目 minus id/名字（归属世界顶层）；「触发概率」补季级缺省（value=3）
        season = {k: v for k, v in entry.items() if k not in ("id", "名字")}
        season.setdefault("触发概率", {"dice": "d100", "value": 3, "relation": "lte"})
        world = {
            "id": wid,
            "名字": entry.get("名字", ""),
            "季": {"s1": season},
        }
        out = out_dir / f"{wid}.json"
        out.write_text(dump(world), encoding="utf-8")
        reloaded = ujson.loads(out.read_text(encoding="utf-8-sig"))
        # 校验：世界顶层 id/名字 与原条目一致；季.s1 与原条目（除 id/名字）一致
        assert reloaded["id"] == wid, f"{out} id 不一致"
        assert reloaded["名字"] == entry["名字"], f"{out} 名字不一致"
        assert reloaded["季"]["s1"] == season, f"{out} 季.s1 不一致"
        assert set(season) >= {"标题", "入口", "入口条件", "节点", "收尾", "纪念品", "玩家文案", "触发概率"}
        print(f"WORLD {wid:>6} -> {out.relative_to(ROOT)}  (季.s1, 触发概率={season['触发概率']})")
    # lane_tale 保留检查
    assert "lane_tale" in data, "lane_tale 缺失"
    print(f"KEEP  lane_tale 保留于 {src.relative_to(ROOT)}（普通剧情流程，非异界）")


if __name__ == "__main__":
    migrate_bosses()
    migrate_worlds()
    print("\n迁移完成。原文件请归档至 test/archive/（见设计 §七）。")

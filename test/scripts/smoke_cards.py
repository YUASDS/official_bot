"""图片卡片渲染冒烟测试：生成开场/回合/结算卡片 PNG，人工检查样式。"""
import asyncio
import random
import sys
import traceback
from pathlib import Path

from loguru import logger

logger.remove()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "plugins" / "StoryTeller"))

import nonebot

nonebot.init()

from src.models.player import CreateInvestigator, investigator_repo
from src.models.monster import Monster
from src.services.battle import BattleService
from src.services.data_loader import data_loader
from src.services.sanity import perform_sanity_check
from src.utils.md_format import report_quote, report_section
from src.services.battle_cards import (
    battle_card_html,
    battle_open_html,
    battle_round_html,
    end_card_html,
    sanity_zero_card_html,
)
from src.services.character_cards import (
    candidate_card_html,
    choose_success_card_html,
    create_done_card_html,
)

OUT = Path(__file__).resolve().parent.parent / "cards_out"
OUT.mkdir(exist_ok=True)

random.seed(7)

ci = CreateInvestigator(3)
ci.choose_investigator(1)
sp = ci.skill_point
alloc = []
for skill, cap in {"格斗": 50, "侦查": 50, "手枪": 55, "步枪": 50}.items():
    give = min(cap, sp)
    if give <= 0:
        break
    alloc.append(f"{skill}{give}")
    sp -= give
ci.set_skill("".join(alloc))

qq = "card_smoke"
investigator_repo.delete_by_qq(qq)
inv = ci.create_investigator(qq, "测试员")
inv.set_skill("智力", 99)
inv.save()

monster = Monster("2")
service = BattleService(inv, monster)
env = data_loader.environment_data["失重"].copy()
env["name"] = "失重"
service.set_environment(env)
service.set_madness(True, 2)

san_passed, san_desc, _ = perform_sanity_check(inv, monster)
service.roll_initiative()
reply = (
    f"{data_loader.get_text('battle.report_title', env='失重')}\n\n"
    f"{data_loader.get_text('battle.day_line', day=inv.day)}\n\n"
    f"{report_section(data_loader.get_text('battle.anomaly_title', env='失重'))}\n"
    f"{report_quote(['血月悬空，雾气翻涌。'])}\n\n"
    f"{report_section(data_loader.get_text('battle.monster_intro_title'))}\n"
    f"{monster.出场}\n\n"
    f"{san_desc}\n\n"
    f"{service.get_dex_compare_section()}\n\n"
    f"{service.start_turn()}"
)


async def render(html: str):
    try:
        from util.html2pic import html_to_pic

        return await html_to_pic(html, selector=".card", wait=0.8)
    except Exception:
        traceback.print_exc()
        raise


async def main() -> None:
    img = await render(battle_card_html(service, day_event="血月悬空，雾气翻涌。"))
    (OUT / "01_cg.png").write_bytes(img.read())

    battle_reply = (
        f"{report_section(data_loader.get_text('battle.monster_intro_title'))}\n"
        f"{monster.出场}\n\n"
        f"{san_desc}\n\n"
        f"{service.get_dex_compare_section()}\n\n"
        f"{service.get_status_table()}\n\n"
        f"{service.get_danger_section()}\n\n"
        f"{service.get_action_section()}"
    )
    img = await render(battle_open_html(service, battle_reply))
    (OUT / "02_open_battle.png").write_bytes(img.read())

    res = service.execute_action("格斗")
    img = await render(battle_round_html(service, res))
    (OUT / "02_round.png").write_bytes(img.read())

    while not service.fight_is_over():
        action = "反击" if service.current_turn == "mon" else "格斗"
        res = service.execute_action(action)
        img = await render(battle_round_html(service, res))
        (OUT / f"03_round_end_{service.get_turn_token()}.png").write_bytes(img.read())
        if service.fight_is_over():
            break

    img = await render(end_card_html(service))
    (OUT / "04_end.png").write_bytes(img.read())

    inv.set_skill("san", 0)
    inv.is_survive = False
    inv.save()
    sz_desc = (
        f"{report_section(data_loader.get_text('battle.sanity_title'))}\n"
        f"{data_loader.get_text('report.san_row', icon='🧠', dice=92, target=4, result='❌ **失败**（理智损失 `1d6=4`，当前 SAN `0/65`）')}"
    )
    # SAN 归零三张图：入场CG → 遭遇登场+鉴定 → 心智崩塌结算
    img = await render(battle_card_html(service, day_event="血月悬空，雾气翻涌。"))
    (OUT / "05a_sanity_cg.png").write_bytes(img.read())
    monster_intro = getattr(
        service.monster, "出场", data_loader.get_text("adventure.monster_intro_default")
    )
    encounter = (
        f"{report_section(data_loader.get_text('battle.monster_intro_title'))}\n"
        f"{monster_intro}\n\n"
        f"{sz_desc}"
    )
    img = await render(battle_open_html(service, encounter))
    (OUT / "05b_sanity_check.png").write_bytes(img.read())
    img = await render(sanity_zero_card_html(inv, san_loss=4))
    (OUT / "05c_sanity_collapse.png").write_bytes(img.read())
    inv.set_skill("san", 65)
    inv.is_survive = True
    inv.save()

    # 创建流程三卡：候选 → 选择成功 → 创建完成
    from src.models.player import CreateInvestigator as _CI

    _ci = _CI(3)
    img = await render(candidate_card_html(_ci.investigators_data))
    (OUT / "06_create_choose.png").write_bytes(img.read())
    _ci.choose_investigator(1)
    img = await render(choose_success_card_html(_ci, "测试员"))
    (OUT / "07_choose_success.png").write_bytes(img.read())
    _ci.set_skill("格斗50侦查50")
    img = await render(create_done_card_html(_ci, "测试员"))
    (OUT / "08_create_done.png").write_bytes(img.read())

    for f in sorted(OUT.glob("*.png")):
        size = f.stat().st_size
        print(f"{f.name}: {size} bytes")
        assert size > 2000, f"{f.name} too small"

    investigator_repo.delete_by_qq(qq)
    print("SMOKE PASS")


asyncio.run(main())

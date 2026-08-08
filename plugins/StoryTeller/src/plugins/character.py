from typing import Any

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg

from ..models.player import (
    CreateInvestigator,
    Investigator,
    InvestigatorFormatter,
)
from ..services.data_loader import data_loader
from ..utils.md_format import md_message

# --- State storage ---
_user_states: dict[str, Any] = {}

_t = data_loader.get_text

# --- Commands ---
create_cmd = on_command(
    "创建调查员", aliases={"create_investigator"}, priority=10, block=True
)

choose_cmd = on_command(
    "选择调查员", aliases={"/选择调查员"}, priority=16, block=True
)

skill_cmd = on_command(
    "st", aliases={"/st"}, priority=16, block=True
)

info_cmd = on_command(
    "调查员信息", aliases={"investigator_info", "查看状态"}, priority=10, block=True
)


# --- /创建调查员 ---
@create_cmd.handle()
async def handle_create(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    name = msg.extract_plain_text().strip() or "调查员"

    ci = CreateInvestigator(3)
    formatted = InvestigatorFormatter.format_investigator_info(name, ci.investigators_data)
    _user_states[user_id] = {"creator": ci, "name": name}

    await create_cmd.finish(
        md_message(
            f"\n{_t('character.create_title')}\n\n"
            f"{formatted}\n\n"
            f"{_t('character.choose_hint')}",
            bot,
        )
    )


# --- /选择调查员 <N> ---
@choose_cmd.handle()
async def handle_choose(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    state = _user_states.get(user_id)

    if not state or "creator" not in state:
        await choose_cmd.finish(md_message(f"\n{_t('character.need_create')}", bot))

    arg = msg.extract_plain_text().strip()
    if not arg.isdigit():
        await choose_cmd.finish(md_message(f"\n{_t('character.need_number')}", bot))

    idx = int(arg)
    if idx < 1 or idx > 3:
        await choose_cmd.finish(md_message(f"\n{_t('character.out_of_range')}", bot))

    ci: CreateInvestigator = state["creator"]
    name: str = state["name"]

    if not ci.choose_investigator(idx):
        await choose_cmd.finish(md_message(f"\n{_t('character.choose_failed')}", bot))

    core_attrs = [
        "力量", "体质", "体型", "敏捷",
        "外貌", "智力", "意志", "教育", "幸运",
    ]
    attr_line = " ".join(f"{k}:{ci.select.get(k, 0)}" for k in core_attrs)
    attr_line += f" SAN:{ci.select.get('san', 0)} HP:{ci.select.get('hp', 0)}"

    skill_keys = ["手枪", "步枪", "格斗", "侦查", "急救", "医学"]
    skill_line = " ".join(f"{k}:{ci.select.get(k, 0)}" for k in skill_keys)

    await choose_cmd.finish(
        md_message(
            f"\n{_t('character.choose_success')}\n\n"
            f"{_t('character.name_label', name=name)}\n"
            f"{_t('character.attr_label', attrs=attr_line)}\n"
            f"{_t('character.skill_label', skills=skill_line)}\n\n"
            f"{_t('character.skill_alloc_hint', points=ci.skill_point)}",
            bot,
        )
    )


# --- /st <skills> ---
@skill_cmd.handle()
async def handle_skill(event: Event, bot: Bot, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    state = _user_states.get(user_id)

    if not state or "creator" not in state:
        await skill_cmd.finish(md_message(f"\n{_t('character.need_create')}", bot))

    ci: CreateInvestigator = state["creator"]
    name: str = state["name"]
    skills = msg.extract_plain_text().strip()

    if not skills:
        await skill_cmd.finish(md_message(f"\n{_t('character.need_skill_input')}", bot))

    ok, reply_msg = ci.set_skill(skills)
    if not ok:
        await skill_cmd.finish(md_message(f"\n{reply_msg}", bot))

    inv = ci.create_investigator(user_id, name)
    attrs = InvestigatorFormatter.format_investigator_info(name, ci.select)
    del _user_states[user_id]

    await skill_cmd.finish(
        md_message(f"\n{_t('character.create_done', name=inv.name)}\n\n{attrs}", bot)
    )


# --- /调查员信息 ---
@info_cmd.handle()
async def handle_info(event: Event, bot: Bot):
    inv = Investigator.load(event.get_user_id())
    attrs = inv.get_full_attributes_dict()
    survival = _t("character.dead") if not inv.is_survive else _t("character.survive")

    attr_pairs = [f"{k}:{v}" for k, v in attrs.items()]
    attr_lines: list[str] = []
    current = ""
    for pair in attr_pairs:
        if current and len(current) + len(pair) + 1 > 40:
            attr_lines.append(current.strip())
            current = pair
        else:
            current += " " + pair
    if current.strip():
        attr_lines.append(current.strip())

    nl = "\n"
    res = (
        f"\n{_t('character.info_title')}\n\n"
        f"{_t('character.info_status', status=survival, day=inv.day)}\n\n"
        f"{_t('character.info_attrs')}\n{nl.join(attr_lines)}\n\n"
        f"{inv.str_equipments()}"
    )
    await info_cmd.finish(md_message(res, bot))

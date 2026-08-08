from typing import Any

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.params import CommandArg

from ..models.player import (
    CreateInvestigator,
    Investigator,
    InvestigatorFormatter,
)

# --- State storage ---
_user_states: dict[str, Any] = {}

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
async def handle_create(event: Event, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    name = msg.extract_plain_text().strip() or "调查员"

    ci = CreateInvestigator(3)
    formatted = InvestigatorFormatter.format_investigator_info(name, ci.investigators_data)
    _user_states[user_id] = {"creator": ci, "name": name}

    await create_cmd.finish(
        "\n── 欢迎来到克苏鲁的世界 ──\n\n"
        f"{formatted}\n\n"
        "请选择你想要创建的调查员属性：\n"
        " · /选择调查员 [1-3]"
    )


# --- /选择调查员 <N> ---
@choose_cmd.handle()
async def handle_choose(event: Event, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    state = _user_states.get(user_id)

    if not state or "creator" not in state:
        await choose_cmd.finish("\n请先使用 /创建调查员 生成角色。")

    arg = msg.extract_plain_text().strip()
    if not arg.isdigit():
        await choose_cmd.finish("\n请输入数字，例如：/选择调查员 2")

    idx = int(arg)
    if idx < 1 or idx > 3:
        await choose_cmd.finish("\n超过了可以选择的范围哦~")

    ci: CreateInvestigator = state["creator"]
    name: str = state["name"]

    if not ci.choose_investigator(idx):
        await choose_cmd.finish("\n选择失败。")

    core_attrs = [
        "力量", "体质", "体型", "敏捷",
        "外貌", "智力", "意志", "教育", "幸运",
    ]
    attr_line = " ".join(f"{k}:{ci.select.get(k, 0)}" for k in core_attrs)
    attr_line += f" SAN:{ci.select.get('san', 0)} HP:{ci.select.get('hp', 0)}"

    skill_keys = ["手枪", "步枪", "格斗", "侦查", "急救", "医学"]
    skill_line = " ".join(f"{k}:{ci.select.get(k, 0)}" for k in skill_keys)

    await choose_cmd.finish(
        "\n── 选择成功 ──\n\n"
        f"名称：{name}\n"
        f"属性：{attr_line}\n"
        f"技能：{skill_line}\n\n"
        f"接下来需要分配技能了哦~\n"
        f"共有【{ci.skill_point}】点技能点可以分配\n"
        "请按格式输入（例如：/st 手枪30步枪20）\n"
        "技能上限 75"
    )


# --- /st <skills> ---
@skill_cmd.handle()
async def handle_skill(event: Event, msg: Message = CommandArg()):
    user_id = event.get_user_id()
    state = _user_states.get(user_id)

    if not state or "creator" not in state:
        await skill_cmd.finish("\n请先使用 /创建调查员 生成角色。")

    ci: CreateInvestigator = state["creator"]
    name: str = state["name"]
    skills = msg.extract_plain_text().strip()

    if not skills:
        await skill_cmd.finish("\n请输入技能分配，例如：/st 手枪30步枪20")

    ok, reply_msg = ci.set_skill(skills)
    if not ok:
        await skill_cmd.finish(f"\n{reply_msg}")

    inv = ci.create_investigator(user_id, name)
    attrs = InvestigatorFormatter.format_investigator_info(name, ci.select)
    del _user_states[user_id]

    await skill_cmd.finish(f"\n── 调查员 {inv.name} 创建完成 ──\n\n{attrs}")


# --- /调查员信息 ---
@info_cmd.handle()
async def handle_info(event: Event):
    inv = Investigator.load(event.get_user_id())
    attrs = inv.get_full_attributes_dict()
    survival = "死亡" if not inv.is_survive else "存活"

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
        "\n════ 调查员档案 ════\n\n"
        f"状态：{survival}｜时间：第 {inv.day} 天\n\n"
        f"【属性】\n{nl.join(attr_lines)}\n\n"
        f"{inv.str_equipments()}"
    )
    await info_cmd.finish(res)

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.params import CommandArg
from nonebot_plugin_waiter import waiter

from ..models.player import (
    CreateInvestigator,
    Investigator,
    InvestigatorFormatter,
)

create_cmd = on_command("创建调查员", aliases={"create_investigator"}, priority=10, block=True)

@create_cmd.handle()
async def handle_create(event: Event, msg: Message = CommandArg()) -> None:
    user_id = event.get_user_id()
    name = msg.extract_plain_text().strip()

    if not name:
        await create_cmd.send("请输入角色名，例如：/创建调查员 霍华德")
        return

    # Step 1: Generate 3 candidates
    ci = CreateInvestigator(3)
    formatted = InvestigatorFormatter.format_investigator_info(name, ci.investigators_data)
    await create_cmd.send(f"{formatted}\n请选择其中一组（输入 1/2/3）：")

    # Step 2: Wait for choice
    @waiter(waits=["message"], keep_session=True)
    async def wait_choice(ev: Event):
        return ev.get_plaintext().strip()

    resp = await wait_choice.wait(timeout=120)
    if not resp or not resp.isdigit():
        await create_cmd.finish("超时或输入无效。")

    idx = int(resp)
    if not ci.choose_investigator(idx):
        await create_cmd.finish("选择无效。")

    # Step 3: Skill allocation
    await create_cmd.send(f"你有 {ci.skill_point} 技能点可以分配。请输入技能和点数（如：格斗30 侦查30 手枪30）：")

    @waiter(waits=["message"], keep_session=True)
    async def wait_skill(ev: Event):
        return ev.get_plaintext().strip()

    skills = await wait_skill.wait(timeout=120)
    if not skills:
        await create_cmd.finish("超时。")

    ok, skill_msg = ci.set_skill(skills)
    if not ok:
        await create_cmd.finish(skill_msg)

    # Step 4: Create
    inv = ci.create_investigator(user_id, name)
    await create_cmd.finish(f"调查员 {inv.name} 创建成功！")


info_cmd = on_command("调查员信息", aliases={"investigator_info", "查看状态"}, priority=10, block=True)

@info_cmd.handle()
async def handle_info(event: Event) -> None:
    inv = Investigator.load(event.get_user_id())
    attrs = inv.get_full_attributes_dict()
    formatted = InvestigatorFormatter.format_investigator_info(inv.name, attrs)
    equip_str = inv.str_equipments()
    res = f"{formatted}\n{equip_str}"
    await info_cmd.finish(res)

from nonebot import on_command
from nonebot.adapters import Bot, Event

from ..utils.md_format import md_message

profile_cmd = on_command(
    "个人信息", aliases={"我的ID", "个人ID"}, priority=10, block=True
)


@profile_cmd.handle()
async def handle_profile(event: Event, bot: Bot) -> None:
    user_id = event.get_user_id()
    await profile_cmd.finish(
        md_message(f"\n你的个人ID：`{user_id}`", bot, mention=user_id)
    )

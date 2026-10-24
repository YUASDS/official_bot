from nonebot import on_command
from nonebot.adapters import Bot, Event

from ..models.player import ending_repo
from ..services.data_loader import data_loader
from ..services.stat_render import career_section
from ..utils.md_format import md_message

profile_cmd = on_command(
    "个人信息", aliases={"我的ID", "个人ID"}, priority=10, block=True
)


@profile_cmd.handle()
async def handle_profile(event: Event, bot: Bot) -> None:
    user_id = event.get_user_id()
    parts = [f"你的个人ID：`{user_id}`"]
    career = career_section(user_id)
    if career:
        parts.append(career)
    # 账号级资源：梦之碎片（跨周目保留；无碎片不显示）
    dream = ending_repo.get_dream_fragments(user_id)
    if dream > 0:
        parts.append(
            data_loader.get_text("dream_fragment.profile_line", count=dream)
        )
    await profile_cmd.finish(
        md_message("\n\n".join(parts), bot, mention=user_id)
    )

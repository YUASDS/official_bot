from nonebot import on_command
from nonebot.adapters import Bot, Event

from ..services.data_loader import data_loader
from ..utils.md_format import md_message, report_section

help_cmd = on_command(
    "冒险帮助", aliases={"玩法指南", "帮助"}, priority=10, block=True
)


@help_cmd.handle()
async def handle_help(event: Event, bot: Bot) -> None:
    t = data_loader.get_text
    sections = [
        t("help.title"),
        report_section(t("help.intro_title")),
        t("help.intro"),
        report_section(t("help.cmd_title")),
        t("help.cmd_table"),
        report_section(t("help.battle_title")),
        t("help.battle_table"),
        report_section(t("help.attr_title")),
        t("help.attr_table"),
        report_section(t("help.sanity_title")),
        t("help.sanity"),
        report_section(t("help.shop_title")),
        t("help.shop"),
        report_section(t("help.goal_title")),
        t("help.goal"),
    ]
    await help_cmd.finish(md_message("\n" + "\n\n".join(sections), bot))

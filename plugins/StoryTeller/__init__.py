from nonebot import require
from nonebot.plugin import PluginMetadata

# Import handlers from new plugin structure
from .src.plugins.adventure import adventure_cmd, combat_cmd
from .src.plugins.shop import shop_cmd, buy_cmd
from .src.plugins.character import create_cmd, info_cmd, choose_cmd, skill_cmd, use_item_cmd
from .src.plugins.help import help_cmd
from .src.plugins.profile import profile_cmd
from .src.plugins.gm import gm_gift_cmd, gm_scroll_cmd
from .src.plugins.ending import ending_cmd
from .src.plugins.qiren import qiren_pending
from .src.plugins.stat import stats_cmd, review_cmd, rank_cmd
from .src.plugins.flow import explore_cmd

# Define plugin metadata
__plugin_meta__ = PluginMetadata(
    name="克苏鲁冒险 (Refactored)",
    description="Refactored COC adventure game based on NoneBot2",
    usage="/今日冒险 - 开始今日冒险\n/今日商店 - 查看今日商店\n/购买 <物品ID> [数量] - 购买物品\n/创建调查员 - 创建新角色\n/调查员信息 - 查看角色状态\n/行动 <动作> - 战斗指令\n/冒险帮助 - 查看玩法指南\n/个人信息 - 查看个人ID",
    type="application",
    homepage="https://github.com/your-repo/official_bot",
    supported_adapters={"onebot.v11", "qq", "console"},
)

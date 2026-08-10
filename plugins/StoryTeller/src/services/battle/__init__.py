"""BattleService 战斗服务（Mixin 拆分：状态 / 渲染 / 引擎 / 动作 / 法术 / 伤害 / 结算）。"""

from .actions import BattleActionsMixin
from .base import BattleBaseMixin
from .damage import BattleDamageMixin
from .engine import BattleEngineMixin
from .reporter import BattleReporterMixin
from .settlement import BattleSettlementMixin
from .spells import BattleSpellsMixin


class BattleService(
    BattleBaseMixin,
    BattleReporterMixin,
    BattleEngineMixin,
    BattleActionsMixin,
    BattleSpellsMixin,
    BattleDamageMixin,
    BattleSettlementMixin,
):
    """克苏鲁战斗服务：对外接口与拆分前保持一致。"""

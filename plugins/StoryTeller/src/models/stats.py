"""统计系统二期：6 张统计表模型（peewee，落 inv.db）。

对齐 `.qa/reports/stats-design.md` §4.1 表结构；表由 stats_service 首次写入时
自动 create_tables（safe=True），历史兼容零迁移。

全部写入走 `services/stats_service.py` 的写后不理封装，本模块不暴露任何
对外副作用，仅在导入时确保表存在。
"""

from __future__ import annotations

from contextlib import suppress

from peewee import BooleanField, CharField, IntegerField, TextField

from .player import BaseModel

# 表统一在导入时确保存在（对齐 player.py 仓储构造风格，幂等）
_BASE_DB = BaseModel._meta.database
if _BASE_DB.is_closed():
    _BASE_DB.connect()


class RunStats(BaseModel):
    """周目快照（每局一行，跨周目保留；解决表 A 重建丢历史）。"""

    qq = CharField(verbose_name="QQ号")
    run_id = IntegerField(default=0, verbose_name="周目号")
    ended_at = CharField(default="", verbose_name="结算时间")
    ending_id = CharField(default="", verbose_name="结局ID(空=未结算兜底)")
    variant = CharField(default="", verbose_name="结局变体")
    days_survived = IntegerField(default=0, verbose_name="局内存活天数")
    final_san = IntegerField(default=0, verbose_name="终局SAN")
    final_hp = IntegerField(default=0, verbose_name="终局HP")
    knowledge = IntegerField(default=0, verbose_name="终局知识度")
    mythos_gained = IntegerField(default=0, verbose_name="克苏鲁神话终值")
    door_choice = CharField(default="", verbose_name="门扉抉择(空/A/B/C/...)")
    boss36_defeated = BooleanField(default=False, verbose_name="击败守门人")
    dead_once = BooleanField(default=False, verbose_name="第40天战败复活过")
    refought = BooleanField(default=False, verbose_name="重赴过守门人")
    mirror_defeated = BooleanField(default=False, verbose_name="击败镜中之人")
    fled_day40 = BooleanField(default=False, verbose_name="第40天逃跑过")
    san_zero_hit = BooleanField(default=False, verbose_name="触发SAN归零")
    kills = TextField(default="{}", verbose_name="击杀统计(JSON)")
    relics_count = IntegerField(default=0, verbose_name="信物持有数")
    spells_count = IntegerField(default=0, verbose_name="法术数")
    gold_net = IntegerField(default=0, verbose_name="局内乌帕净收益")
    battles = IntegerField(default=0, verbose_name="局内战斗场次")
    flees = IntegerField(default=0, verbose_name="局内逃跑次数")
    deaths = IntegerField(default=0, verbose_name="局内死亡次数")

    class Meta:
        table_name = "run_stats"
        indexes = ((("qq", "run_id"), True),)


class BattleLog(BaseModel):
    """战斗流水（每场一行）。"""

    qq = CharField(verbose_name="QQ号")
    run_id = IntegerField(default=0, verbose_name="周目号")
    day = IntegerField(default=1, verbose_name="战斗日")
    monster_id = CharField(default="", verbose_name="怪物ID")
    environment = CharField(default="", verbose_name="环境名称")
    result = CharField(default="", verbose_name="结果(win/flee/death/revived)")
    turns = IntegerField(default=0, verbose_name="回合数")
    dmg_dealt = IntegerField(default=0, verbose_name="造成伤害")
    dmg_taken = IntegerField(default=0, verbose_name="承受伤害")
    armor_absorbed = IntegerField(default=0, verbose_name="护甲吸收")
    san_loss = IntegerField(default=0, verbose_name="SAN损失")
    madness = TextField(default="[]", verbose_name="临时疯狂(JSON)")
    consumables = TextField(default="{}", verbose_name="消耗品使用(JSON)")
    spells_cast = TextField(default="{}", verbose_name="法术施放(JSON)")
    fled = BooleanField(default=False, verbose_name="是否逃跑")
    created_at = CharField(default="", verbose_name="写入时间")

    class Meta:
        table_name = "battle_logs"


class GoldLedger(BaseModel):
    """经济流水（乌帕收支，balance_after 冗余快照避免跨库 join）。"""

    qq = CharField(verbose_name="QQ号")
    run_id = IntegerField(default=0, verbose_name="周目号")
    ts = CharField(default="", verbose_name="时间")
    delta = IntegerField(default=0, verbose_name="变化量(正入负出)")
    balance_after = IntegerField(default=0, verbose_name="变更后余额")
    source = CharField(default="other", verbose_name="来源(battle/event/shop/gm/resurrect/other)")
    ref_id = CharField(default="", verbose_name="关联ID")

    class Meta:
        table_name = "gold_ledger"


class EventLog(BaseModel):
    """事件流水（事件选择与效果应用）。"""

    qq = CharField(verbose_name="QQ号")
    run_id = IntegerField(default=0, verbose_name="周目号")
    day = IntegerField(default=1, verbose_name="当前day")
    event_key = CharField(default="", verbose_name="事件key")
    option = CharField(default="", verbose_name="选项")
    check_skill = CharField(default="", verbose_name="检定技能(空=无检定)")
    passed = IntegerField(default=-1, verbose_name="检定是否通过(1/0/-1)")
    effects = TextField(default="{}", verbose_name="生效效果(JSON)")

    class Meta:
        table_name = "event_logs"


class GrowthLog(BaseModel):
    """成长流水（技能成长/事件技能效果）。"""

    qq = CharField(verbose_name="QQ号")
    run_id = IntegerField(default=0, verbose_name="周目号")
    day = IntegerField(default=1, verbose_name="当前day")
    skill = CharField(default="", verbose_name="技能名")
    before = IntegerField(default=0, verbose_name="成长前")
    after = IntegerField(default=0, verbose_name="成长后")
    source = CharField(default="battle", verbose_name="来源(battle/event)")

    class Meta:
        table_name = "growth_logs"


class DailyStat(BaseModel):
    """每日汇总（全服一行，0 点轮转快照）。"""

    date = CharField(unique=True, verbose_name="自然日(YYYY-MM-DD)")
    active_players = IntegerField(default=0, verbose_name="日活跃玩家")
    adventures = IntegerField(default=0, verbose_name="冒险次数(战斗数)")
    battles = IntegerField(default=0, verbose_name="战斗场次")
    wins = IntegerField(default=0, verbose_name="胜利数")
    deaths = IntegerField(default=0, verbose_name="死亡数(含战败复活)")
    endings = TextField(default="{}", verbose_name="当日结局(JSON)")
    gold_in = IntegerField(default=0, verbose_name="当日乌帕流入")
    gold_out = IntegerField(default=0, verbose_name="当日乌帕流出")
    avg_day = IntegerField(default=0, verbose_name="平均战斗日")

    class Meta:
        table_name = "daily_stats"


if _BASE_DB.is_closed():
    _BASE_DB.connect()
# 建表失败绝不影响插件加载（统计写后不理，写入时异常同样被吞）
with suppress(Exception):
    _BASE_DB.create_tables(
        [RunStats, BattleLog, GoldLedger, EventLog, GrowthLog, DailyStat],
        safe=True,
    )

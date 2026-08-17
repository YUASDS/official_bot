"""BattleService · 状态管理：战斗全部可变状态与环境修正。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Optional

from ...models.item import Equipment

if TYPE_CHECKING:
    from ...models.monster import Monster
    from ...models.player import Investigator

# 攻击吸收·属性模式可吸收的目标（player model 技能字段全集 + san）：
# 与 config_validator.KNOWN_SKILLS 对齐，战斗内临时吸收只改快照内目标。
_ABSORBABLE_SKILLS: tuple[str, ...] = (
    "力量", "体质", "体型", "智力", "意志", "敏捷", "教育", "幸运", "外貌",
    "格斗", "闪避", "侦查", "聆听", "手枪", "步枪", "急救", "医学",
    "克苏鲁神话", "san",
)


class BattleBaseMixin:
    def __init__(self, investigator: Investigator, monster: Monster) -> None:
        self.investigator = investigator
        self.monster = monster
        self.player_name = investigator.name
        self.hp_record = {"inv": investigator.hp, "mon": monster.hp}
        self.current_turn: Literal["inv", "mon"] = "inv"
        # 本次行动的发起者（execute_action 入口记录）：战报卡片标题用它，
        # 表示「刚结算的是谁的行动」；current_turn 是行动后翻转的「下一行动者」。
        self.last_actor: Literal["inv", "mon"] = "inv"
        self.current_action = "格斗"
        # 战斗发生时的天数（胜利结算 day+1 后仍显示本场战斗的天数）
        self._battle_day = investigator.day

        self.gun: Optional[Equipment] = None
        self.bullet = 0
        self.max_bullet = 0
        self.succeded_skill = set()
        self._update_gun_status()

        self.is_madness = False
        self.madness_duration = 0

        # 攻击吸收（批次2）：战斗内临时属性吸收快照-恢复（不落 player model 持久字段）。
        # 战斗初始化记录可吸收技能原值；临时吸收只改内存缓存值并登记 _absorb_modified，
        # 战斗结束（对齐 hp/san 结算时机）由 _restore_absorb_snapshot 统一恢复原值。
        # _absorb_perm_delta 累计持久扣减（恢复临时时仅撤销临时部分，保留持久结果）。
        self._absorb_snapshot: dict[str, int] = {
            skill: investigator.get_skill(skill, 0) for skill in _ABSORBABLE_SKILLS
        }
        self._absorb_modified: set[str] = set()
        self._absorb_perm_delta: dict[str, int] = {}

        self.environment: dict[str, dict] = {}
        self._weapon_reply_shown = False
        self.fled = False
        self._turn_counter = 0
        self.end_parts: tuple[str, str] = ("", "")
        self.end_card_ext: dict = {}

        # 条件胜利（坚守战，批次3）：flow 战斗节点透传的 胜利条件（缺省无 → 零回归）。
        # _win_condition 缓存配置；_conditional_won 标记条件胜利已达成；hold_win_text 条件胜利文案。
        self.battle_cfg: dict = {}
        self._win_condition: Optional[dict] = None
        self._conditional_won = False
        self.hold_win_text = ""

        # 法术资源：MP = 意志/5（战斗中不回复）；临时生命（先抵伤害）
        self.max_mp = investigator.get_skill("意志", 0) // 5
        self.mp = self.max_mp
        self.temp_hp = 0
        self._mp_hint_shown = False
        # 怪物临时生命（AI 怪物施法护盾先抵伤害）
        self.monster_temp_hp = 0

        # 骨哨助战剩余回合（506 消耗品，怪物行动前额外 1d4 伤害）
        self.bone_whistle = 0

        # 伙伴助战（NPC 好感度满解锁后由 inject_companion 注入，格式同骨哨但更强数据驱动）
        self.companion: Optional[dict] = None

        # 周目联动：被猎犬杀死过的调查员首次遭遇猎犬时它迟疑一回合（首回合不攻击）
        self.hound_hesitates = False

        # 梦之碎片 / GM 房间余韵：全技能 +30、伤害翻倍、临时生命（本场战斗临时状态）
        self.dream_buff = False

        # 乱入主题玩家文案覆盖：由 guest.py 注入（世界级主题文本，非乱入战斗为空，零影响）
        self._guest_texts: dict = {}

        # 统计二期：战斗流水采集（只读累加，零行为影响）
        self._battle_logged = False
        self._stat_dmg_dealt = 0
        self._stat_dmg_taken = 0
        self._stat_armor_absorbed = 0
        self._stat_consumables: dict = {"505": 0, "506": 0}
        self._stat_spells: dict = {}
        self._initial_san = investigator.get_skill("san", 0)

    def get_turn_token(self) -> int:
        """当前回合令牌（用于按钮防重复点击）。"""
        return self._turn_counter

    def _advance_turn(self) -> None:
        self._turn_counter += 1

    def set_guest_texts(self, texts: dict) -> None:
        """注入乱入主题玩家战斗文案（guest.py 调用，非乱入战斗不调用）。

        键与 reply_data 玩家文案键一致（格斗成功/射击失败/反击成功/高伤害 等）；
        命中时覆盖玩家进攻/失败/反击/闪避/承伤叙述，未命中回退通用文案。
        """
        self._guest_texts = texts if isinstance(texts, dict) else {}

    def set_battle_cfg(self, cfg: dict) -> None:
        """注入战斗配置（flow 战斗节点透传）：解析 胜利条件（坚守战，缺省无）。

        胜利条件 schema：{"类型": "坚守", "回合": N, "文案": "可选"}——坚持满 N 回合且
        玩家存活 → 条件胜利。配置缺失/非法类型 → _win_condition 置 None（零回归）。
        """
        self.battle_cfg = cfg if isinstance(cfg, dict) else {}
        wc = self.battle_cfg.get("胜利条件")
        if isinstance(wc, dict) and wc.get("类型") == "坚守":
            self._win_condition = wc
        else:
            self._win_condition = None
        self._conditional_won = False
        self.hold_win_text = ""

    def _hold_remaining(self) -> int:
        """坚守战剩余回合数（玩家还需坚持的回合数；非坚守战返回一个极大值）。"""
        wc = self._win_condition
        if not wc:
            return 10**9
        try:
            need = int(wc.get("回合", 0))
        except (TypeError, ValueError):
            return 10**9
        return max(0, need - self._turn_counter)

    def _hold_remaining_hint(self) -> str:
        """坚守战剩余回合提示（玩家行动前、剩余回合 ≤ 2 时展示 battle.hold_remaining）。"""
        if self._conditional_won or self.current_turn != "inv":
            return ""
        remaining = self._hold_remaining()
        if remaining <= 0 or remaining > 2:
            return ""
        from ..data_loader import data_loader

        return data_loader.get_text(
            "battle.hold_remaining", 回合=remaining, default=""
        )

    def _get_reply(self, key: str) -> str:
        """玩家战斗文案查询：优先乱入主题覆盖，否则通用 reply_data（行为不变）。

        本方法在 MRO 中先于 BattleReporterMixin._get_reply 生效（BattleBaseMixin 在
        BattleService 继承序首位），覆盖逻辑对非乱入战斗逐字节等价。
        """
        if self._guest_texts.get(key):
            return self._guest_texts[key]
        from ..data_loader import data_loader

        return data_loader.reply_data.get(key, f"[{key}]")

    def set_environment(self, env_data: dict) -> None:
        self.environment = env_data

    def set_madness(self, is_madness: bool, duration: int = 5) -> None:
        self.is_madness = is_madness
        self.madness_duration = duration
        self.madness_total = duration if is_madness else 0

    def apply_dream_buff(self) -> str:
        """梦醒前的余韵 / 梦之碎片·战斗强化：全技能 +30、伤害翻倍、+25 临时生命。

        临时战斗状态，战斗结束随 BattleService 销毁自动还原；不影响角色存档。
        """
        from ..data_loader import data_loader

        self.dream_buff = True
        self.temp_hp += 25
        return data_loader.get_text(
            "dream_fragment.battle_buff",
            default="🌙 幽蓝的光淌进四肢——全技能 +30、伤害翻倍、25 点临时生命护住周身。",
        )

    def _get_player_modified_skill(self, skill_name: str, default: int = 0) -> int:
        base = self.investigator.get_skill(skill_name, default)
        player_mods = self.environment.get("玩家", {})
        if skill_name in player_mods:
            base += player_mods[skill_name]
        # 「射击」为枪械通用修正，作用于手枪/步枪两类鉴定技能
        elif skill_name in ("手枪", "步枪") and "射击" in player_mods:
            base += player_mods["射击"]
        # 梦之碎片余韵：全技能 +30（含格斗/射击/闪避/反击/逃跑/法术对抗）
        if getattr(self, "dream_buff", False):
            base += 30
        return max(0, base)

    def _get_monster_attack_skill(self, monster_action: dict) -> int:
        """怪物攻击技能：攻击表技能 + 环境反击技能/格斗修正；远程行动吃「射击」修正。

        AI 怪物处于「闪避模式」且玩家攻击回合时，怪物改用闪避技能防御
        （闪避 99 + 环境闪避修正）。
        """
        mods = self.environment.get("怪物", {})
        if (
            getattr(self.monster, "is_dodging", False)
            and self.current_turn == "inv"
        ):
            return int(getattr(self.monster, "_ai_dodge", 99)) + mods.get(
                "闪避", 0
            )
        skill = (
            monster_action["skill"]
            + mods.get("反击技能", 0)
            + mods.get("格斗", 0)
        )
        if monster_action.get("type") == "ranged":
            skill += mods.get("射击", 0)
        return skill

    def _get_monster_modified(self, attr: str, default: int = 0) -> int:
        base = getattr(self.monster, attr, default)
        monster_mods = self.environment.get("怪物", {})
        # 数据键「敏捷」与代码属性 dex 互为别名
        if attr == "dex" and "敏捷" in monster_mods:
            attr = "敏捷"
        if attr in monster_mods:
            base += monster_mods[attr]
        return base

    def _update_gun_status(self):
        gun_id = self.investigator.get_equipped_id("远程")
        if gun_id:
            self.gun = Equipment(gun_id)
            if self.gun.is_valid:
                self.bullet = self.gun.bullet
                self.max_bullet = self.gun.max_bullet
            else:
                self.gun = None
        else:
            self.gun = None

    # --- 攻击吸收·临时属性快照恢复（幂等，战斗结束收尾统一调用） ---
    def _restore_absorb_snapshot(self) -> None:
        """恢复战斗内被临时吸收的玩家属性到快照原值（多次调用安全）。

        仅在临时（持久=false）吸收时登记 _absorb_modified；持久吸收不回滚，
        其累计扣减（_absorb_perm_delta）在恢复后仍保留。调用后清空登记，
        确保胜利/死亡/逃跑/结算任一收尾路径都不残留。
        """
        for skill in self._absorb_modified:
            if skill in self._absorb_snapshot:
                base = self._absorb_snapshot[skill]
                permanent = self._absorb_perm_delta.get(skill, 0)
                self.investigator.set_skill(skill, max(0, base - permanent))
        self._absorb_modified.clear()

    # --- 统计二期：战斗流水 / 周目快照（写后不理，绝不回抛） ---
    def _log_battle(self, result: str) -> None:
        """战斗结束写一行 battle_logs（每场战斗仅一次，幂等守卫）。"""
        try:
            if getattr(self, "_battle_logged", False):
                return
            self._battle_logged = True
            from ...services.stats_service import record_battle

            env = getattr(self, "environment", {}) or {}
            env_name = env.get("name", "") if isinstance(env, dict) else ""
            record_battle(
                qq=self.investigator.qq,
                day=getattr(self, "_battle_day", 0),
                monster_id=str(getattr(self.monster, "id", "")),
                environment=str(env_name),
                result=result,
                turns=getattr(self, "_turn_counter", 0),
                dmg_dealt=getattr(self, "_stat_dmg_dealt", 0),
                dmg_taken=getattr(self, "_stat_dmg_taken", 0),
                armor_absorbed=getattr(self, "_stat_armor_absorbed", 0),
                san_loss=max(
                    0,
                    getattr(self, "_initial_san", 0)
                    - self.investigator.get_skill("san", 0),
                ),
                madness=(
                    [getattr(self, "madness_duration", 0)]
                    if getattr(self, "is_madness", False)
                    else []
                ),
                consumables=getattr(self, "_stat_consumables", {}),
                spells_cast=getattr(self, "_stat_spells", {}),
                fled=bool(getattr(self, "fled", False)),
            )
        except Exception:  # noqa: BLE001 - 统计写后不理
            pass

    def _snapshot_run_ending(self, ending_id: str, variant: str = "") -> None:
        """战斗内达成正式结局时写 run_stats 快照。"""
        try:
            from ...services.stats_service import snapshot_run

            snapshot_run(self.investigator, ending_id, variant or None)
        except Exception:  # noqa: BLE001 - 统计写后不理
            pass

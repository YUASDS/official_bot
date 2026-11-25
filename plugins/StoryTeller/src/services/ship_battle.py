"""渡口战斗沿用常规伤害、掉落和成长结算，特殊动作只在指定遭遇出现。"""

import secrets

from .battle import BattleService
from .ship_case import JOURNAL, PENDING, preparations


class ShipBattle(BattleService):
    # 本案怪物不使用变身或吸收。保存影响后续骰点、行动与结算的战斗内状态。
    _fields = (
        "hp_record",
        "current_turn",
        "_turn_counter",
        "bullet",
        "mp",
        "temp_hp",
        "monster_temp_hp",
        "is_madness",
        "madness_duration",
        "bone_whistle",
        "companion",
        "trinket_state",
        "_monster_attr_delta",
        "_dot_on_player",
        "_dot_on_monster",
        "_stat_dmg_dealt",
        "_stat_dmg_taken",
        "_stat_armor_absorbed",
        "_stat_consumables",
        "player_round",
        "breaks",
        "quiet",
        "opening_lead",
        "environment",
        "token_origin",
        "_stat_spells",
        "_initial_san",
        "dream_buff",
        "madness_total",
        "_absorb_snapshot",
        "_absorb_perm_delta",
    )

    def __init__(self, inv, monster):
        super().__init__(inv, monster)
        self.player_round = 0
        self.breaks = 0
        self.quiet = True
        self.opening_lead = False
        self.token_origin = secrets.randbits(48)
        if monster.id == "88":
            _, lens, driver, first = preparations(inv.get_flag(JOURNAL))
            monster.armor -= 2 if lens else 0
            self.temp_hp = 0 if lens else 6
            self.opening_lead = driver
            if not driver:
                monster.hp -= 6
                self.hp_record["mon"] = monster.hp
            if first:
                monster.armor = max(0, monster.armor - 1)
            else:
                self.environment = {"怪物": {"反击技能": -8}}

    def roll_initiative(self):
        super().roll_initiative()
        if self.opening_lead:
            self.current_turn = "inv"

    def get_turn_token(self):
        return self.token_origin + self._turn_counter

    def _player_actions(self):
        actions = super()._player_actions()
        if self.monster.id == "81" and self.player_round < 4:
            actions["inv"].extend(["近身挑扣", "远投铁坠"])
        if self.monster.id == "84" and self.player_round < 2:
            actions["inv"].append("垂手听名")
        return actions

    def _execute_player_action(self, action):
        if action in ("近身挑扣", "远投铁坠", "垂手听名"):
            return (f"你选择了{action}。", self._end_turn())
        return super()._execute_player_action(action)

    def _execute_monster_action(self, action):
        if action in ("反击", "闪避"):
            self.current_action = action
        return super()._execute_monster_action(action)

    def _end_turn(self):
        note = ""
        if self.current_turn == "inv":
            self.player_round += 1
            if self.monster.id == "81" and self.player_round <= 4:
                near = self.player_round <= 2
                legal = (
                    ("近身挑扣", "格斗")
                    if near
                    else ("远投铁坠", "射击", "二连射", "三连射")
                )
                if self.current_action in legal:
                    self.breaks += 1
                    self.monster.armor -= 1
                    self._apply_damage_to_monster(3)
                    note = (
                        "牙扣脱落" if near else "白囊破裂"
                    ) + f"。剩余护甲 {self.monster.armor}。"
                    if self.breaks == 4:
                        self.environment.setdefault("怪物", {})["反击技能"] = -15
                        note += "四处尽断，拖缆攻击技能下降 15。"
            if self.monster.id == "84" and self.player_round <= 2:
                self.quiet &= self.current_action in {
                    "垂手听名",
                    "急救",
                    "使用505",
                    "换弹",
                    "换装",
                }
                if self.quiet and self.player_round == 2:
                    self.monster.armor = 0
                    self.environment.setdefault("怪物", {})["反击技能"] = -15
                    self._apply_damage_to_monster(8)
                    note = "第二遍点名完毕。胸扣自行脱落，护甲归零，失去 8 HP，攻击技能下降 15。"
        elif (
            self.monster.id == "84"
            and self.player_round < 2
            and self.current_action == "反击"
        ):
            self.quiet = False
            note = "它听见你的还击，咬紧了胸扣。"
        prompt = super()._end_turn()
        return f"{note}\n\n{prompt}" if note else prompt

    def checkpoint(self):
        inv = self.investigator
        state = inv.get_flag(PENDING)
        if not state or state["day"] != inv.day:
            return
        snapshot = {k: getattr(self, k) for k in self._fields if hasattr(self, k)}
        snapshot.update(
            armor=self.monster.armor,
            skills=sorted(self.succeded_skill),
            modified=sorted(self._absorb_modified),
        )
        state["battle"] = snapshot
        inv.set_flag(PENDING, state)
        inv.save()

    def restore(self, snapshot):
        for key in self._fields:
            if key in snapshot:
                setattr(self, key, snapshot[key])
        self.monster.armor = snapshot["armor"]
        self.monster.hp = self.hp_record["mon"]
        self.succeded_skill = set(snapshot["skills"])
        self._absorb_modified = set(snapshot["modified"])
        self.refresh_available_actions()

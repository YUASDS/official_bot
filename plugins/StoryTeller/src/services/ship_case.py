"""《雾鸣镇失船案》：同一调查员的二十组跨时调查。冒险日只向前推进。"""

import random
import secrets

import ujson

from ..models.player import ending_repo, investigator_repo
from .daily_service import _skip_daily
from .data_loader import data_loader
from .ending_engine import register_ending

ACTIVE = "ship.active"
PENDING = "ship.pending"
JOURNAL = "ship.journal"
DOOR_ENDINGS = {"E01", "E02", "E03", "E04", "E05", "E08", "E09"}
MAINLINE_ENDINGS = {"E11", "E12", "E13"}


def clear_history(qq: str) -> dict[int, str]:
    """按独立通关局计数；旧图鉴缺失的重复结局由逐局快照补足。"""
    from ..models.stats import RunStats

    collection = ending_repo.get_collection(qq)
    if not collection:
        return {}
    clears = {}
    for row in ujson.loads(collection.endings or "[]"):
        eid, run = row.get("id"), row.get("run")
        if isinstance(run, int) and eid in (DOOR_ENDINGS - {"E05"}) | MAINLINE_ENDINGS:
            clears[run] = eid
    for row in RunStats.select().where(RunStats.qq == qq):
        eid = row.ending_id
        if (
            eid in MAINLINE_ENDINGS
            or eid in DOOR_ENDINGS
            and (eid != "E05" or row.door_choice)
        ):
            clears[row.run_id] = eid
    routes = {
        run: ("mainline" if eid in MAINLINE_ENDINGS else "door", eid)
        for run, eid in clears.items()
    }
    for run, record in ujson.loads(collection.campaign_clears or "{}").items():
        if record["ending"] in DOOR_ENDINGS | MAINLINE_ENDINGS:
            routes[int(run)] = (record["campaign"], record["ending"])
    proofs = {}
    for run, (route, eid) in sorted(routes.items()):
        if run > 0:
            # 重游门扉可补第一段经历；已有门扉通关的后续重游计入二周目。
            tier = 1 if route == "door" and 1 not in proofs else 2
            proofs.setdefault(tier, eid)
    return proofs


def unlocked(qq: str) -> bool:
    clears = clear_history(qq)
    return 1 in clears and 2 in clears


def scene(day):
    return data_loader.ship_case["days"][day - 1]


def pending(inv):
    state = inv.get_flag(PENDING)
    if inv.get_flag(ACTIVE) and state and state["day"] == inv.day:
        return state
    return None


def open_day(inv):
    state = pending(inv)
    if state:
        return state
    day = inv.day
    state = {"day": day, "token": secrets.randbits(60), "phase": "scene"}
    if day in (10, 26):
        pool = _riddles(day)
        state.update(
            phase="riddle",
            answer=random.randrange(len(pool)),
            order=random.sample(range(len(pool)), len(pool)),
        )
    elif day == 21:
        state.update(
            phase="interview", question=0, score=0, order=random.sample(range(3), 3)
        )
    elif day == 40:
        state["phase"] = "radio"
    inv.set_flag(PENDING, state)
    inv.is_adventure = True
    inv.save()
    return state


def _riddles(day):
    return data_loader.ship_case["riddles"][int(day == 26)]


def case_notes(inv):
    journal = inv.get_flag(JOURNAL) or {}
    clues = {
        4: "活缆四步：贴身挑两扣，退远打两囊。",
        9: "头一批失物：罗盘、复写纸、救生圈。",
        12: "送潮人点两遍名。两次垂手听名，受击时只闪避。",
        23: "新船名小满，必须使用独立呼号。",
        25: "第二批失物：航标灯、唱片、电话。",
        27: "旧电台比水管先到九秒。",
        35: "早饭时水手舱的门开着。",
        37: "先送上东堤的人，能留在唱针以外。",
    }
    found = [text for day, text in clues.items() if str(day) in journal]
    return "\n".join(found) or "第一页还空着。"


def _can_wait(journal):
    return journal["37"]["choice"] == 0 or journal["39"]["choice"] == 0


def view(inv, state):
    day, phase = state["day"], state["phase"]
    chapter = scene(day)
    journal = inv.get_flag(JOURNAL) or {}
    era = "过去 · 白榆号" if day % 2 else "现在 · 雾鸣镇"
    date = (
        f"航行第{(day + 1) // 2}日"
        if day < 35
        else (
            ("小满遇险夜" if day % 2 else "小满纪念夜")
            + f" · 第{(day - 35) // 2 + 1}次"
        )
    )
    title = f"失船案 {day}/40 ｜ {era} ｜ {date} ｜ {chapter['title']}"
    lines = []
    echo = journal.get(str(day - 1), {}).get("echo")
    if echo:
        lines.append(echo)
    lines.append(chapter["body"])
    if day == 1:
        marks = {
            "E01": "星图的盐渍",
            "E02": "灯罩内壁的盐霜",
            "E03": "带雾的车票",
            "E04": "裂镜中的船影",
            "E05": "门楣上的星砂",
            "E08": "旁听席的船票",
            "E09": "庄园旧相片",
            "E11": "黯淡一角的星辉",
            "E12": "深渊传回的耳语",
            "E13": "映着湿甲板的镜片",
        }
        history = sorted(clear_history(inv.qq).items())
        if history:
            lines.append(
                "报务桌上压着你的旧案物证："
                + "、".join(marks[eid] for _, eid in history[:2])
            )
    if day == 39:
        lines.append(preparations(journal)[0])
        if journal["37"]["choice"] == 1:
            lines.append(
                "伙夫提醒你：软骨还不会等。想让女孩亲自报船名，得有人守在旧台送话线旁。"
            )
    if phase == "riddle":
        pool = _riddles(day)
        lines.extend(
            [
                "柜上物签：" + "、".join(pool[i][1] for i in state["order"]),
                "谜面：" + pool[state["answer"]][2],
            ]
        )
        labels = [pool[i][0] for i in state["order"]]
    elif phase == "interview":
        prompt, answers = data_loader.ship_case["questions"][state["question"]]
        lines.append(f"第{state['question'] + 1}问 ｜ {prompt}")
        labels = [answers[i][0] for i in state["order"]]
    elif day == 2:
        if journal["1"]["choice"] == 0:
            lines.append("司机领你去父亲点灯的旧泊位。系船柱上贴着救援照的副本。")
        else:
            lines.append(
                "闻秋用私藏的钥匙打开值班柜。柜里正是救援照的底片和当夜的值班簿。"
            )
        labels = [opt["label"] for opt in chapter["options"]]
    elif phase == "radio":
        labels = ["让旧台静默", "亲自接替旧播报", "保留旧呼号"]
        if not _can_wait(journal):
            lines.append("船尾无人压住送话线，旧喉咙也未学会等候；静默线路已失去。")
            labels.pop(0)
    elif phase == "voice":
        labels = (
            ["把话筒交给闻秋", "由我留下值夜"]
            if state["radio"] != "保留旧呼号"
            else ["播放旧救援报告", "由我留下值夜"]
        )
        lines.append("岸台灯亮了。谁来报船名？")
    elif phase == "berth":
        eid = state["ending"]
        labels = [
            {"E14": "接小满进东堤", "E15": "留在九秒里送船", "E16": "接回旧渡口"}[eid],
            "重接线路",
        ]
        lines.append(
            {
                "E14": "女孩亲口报出新呼号。东堤可以留下两个时代的人。",
                "E15": "离开话筒，旧播报就会追上他们。你要留在这九秒里值夜。",
                "E16": "旧渡口会保全岸上的三十一年。仍在船上的人会再次发出呼救。",
            }[eid]
        )
    else:
        labels = [option["label"] for option in chapter["options"]]
    return title, "\n\n".join(lines), labels


def preparations(journal):
    lens = journal["29"]["choice"] == 0
    driver = journal["17"]["choice"] == 0
    first = journal["1"]["choice"] == 0
    return (
        "东堤箱开启。"
        + ("透镜烧掉两层盐甲。" if lens else "药包抵挡六点伤害。")
        + (
            "司机报站引开守卫，抢得先手。"
            if driver
            else "轮机工开泵，守卫失去六点生命。"
        )
        + (
            "孩子举起父亲的灯，照出盐甲的一道缝。"
            if first
            else "闻秋听出假播报，使守卫的攻击技能下降八点。"
        ),
        lens,
        driver,
        first,
    )


def _save_prompt(inv, state):
    state["token"] = secrets.randbits(60)
    inv.set_flag(PENDING, state)
    inv.save()


def respond(inv, choice, token=None):
    """同一事务内保存回答、单局历史、补给和日进度，按钮重放无效。"""
    with investigator_repo.db.atomic():
        state = pending(inv)
        if (
            not state
            or state["phase"] == "fight"
            or (token is not None and token != state["token"])
        ):
            return "stale", "这张船票已经验过了。"
        day, phase = inv.day, state["phase"]
        labels = view(inv, state)[2]
        if len(choice) <= 2 and choice.isdecimal() and 1 <= int(choice) <= len(labels):
            choice = labels[int(choice) - 1]
        if phase == "riddle":
            for name, _, _, aliases in _riddles(day):
                if choice in aliases:
                    choice = name
        if choice not in labels and (phase != "riddle" or not choice):
            return "prompt", "请回复选项文字或编号。"
        idx = labels.index(choice) if choice in labels else -1
        journal = inv.get_flag(JOURNAL) or {}
        chapter = scene(day)
        monster = chapter.get("monster")
        echo = ""
        if phase == "interview":
            points = data_loader.ship_case["questions"][state["question"]][1][
                state["order"][idx]
            ][1]
            state["score"] += points
            reply = [
                "铅笔尖折断了。",
                "它留下一个待核的圆圈。",
                "它郑重地画了一个勾。",
            ][points]
            if state["question"] < 2:
                state.update(
                    question=state["question"] + 1, order=random.sample(range(3), 3)
                )
                _save_prompt(inv, state)
                return "prompt", reply
            if state["score"] >= 4:
                monster = None
                echo = "救生艇上留着它给你的铅笔：人到了，一个一个勾。"
            else:
                echo = "空制服退去，掉在甲板上的铅笔仍写着你的证词。"
            reply += f" 证词 {state['score']}/6。" + (
                "它解开了艇缆。" if not monster else "它一把抓住了你的衣领。"
            )
        elif phase == "riddle":
            answer = _riddles(day)[state["answer"]][0]
            if choice == answer:
                monster = None
                inv.add_item_to_inventory("505")
                reply = "票钳在失物签打了孔。柜门打开，获得米戈神经凝胶 ×1。"
            else:
                reply = f"它剪碎了写着「{answer}」的票，把你拖向柜门。"
        elif phase == "radio":
            state.update(phase="voice", radio=choice)
            _save_prompt(inv, state)
            return "prompt", "你拨动线路，表针开始倒数。"
        elif phase == "voice":
            if state["radio"] == "亲自接替旧播报" or choice == "由我留下值夜":
                eid = "E15"
            else:
                eid = "E16" if state["radio"] == "保留旧呼号" else "E14"
            state.update(phase="berth", ending=eid)
            _save_prompt(inv, state)
            return "prompt", "九秒还剩最后一拍。"
        elif phase == "berth":
            if choice == "重接线路":
                state["phase"] = "radio"
                _save_prompt(inv, state)
                return "prompt", "你重新握住扳柄。"
            eid = state["ending"]
            progress = ending_repo.ensure_progress(inv.qq, day)
            progress.ended = True
            progress.save()
            register_ending(inv, eid)
            inv.clear_flag(PENDING)
            inv.is_adventure = False
            inv.save()
            return "ending", eid
        else:
            option = chapter["options"][idx]
            reply, echo = option["reply"], option.get("echo", "")
            if day == 30 and idx == 1:
                monster = "84"
        journal[str(day)] = {"choice": idx, "echo": echo}
        inv.set_flag(JOURNAL, journal)
        if monster:
            state.update(phase="fight", monster=monster, reply=reply)
            _save_prompt(inv, state)
            return "fight", reply
        inv.clear_flag(PENDING)
        _skip_daily(inv.qq, inv)
        return "done", reply

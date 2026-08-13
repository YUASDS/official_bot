import os
from pathlib import Path
from typing import Union
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

import ujson
from .TimeTool import date_today

ORGIN_PATH = Path(__file__).parent.joinpath(f"day/")
PATH = Path(__file__).parent.joinpath(f"day/{date_today()}.json")
if not ORGIN_PATH.exists():
    os.mkdir(ORGIN_PATH)
if not PATH.exists():
    PATH.write_text("{}", encoding="utf-8")
DATA: dict[str, dict] = ujson.loads(PATH.read_text(encoding="utf-8"))
logger.info(f"{date_today()}初始化完成")


def add_data(qq: Union[int, str], key: str, value):
    if isinstance(qq, int):
        qq = str(qq)
    if qq not in DATA:
        DATA[qq] = {key: value}
    else:
        DATA[qq][key] = value


def get_data(qq: Union[int, str], key: str):
    if isinstance(qq, int):
        qq = str(qq)
    if qq not in DATA:
        DATA[qq] = {}
    return DATA[qq].get(key, None)


def write_json():
    PATH.write_text(ujson.dumps(DATA, ensure_ascii=False), encoding="utf-8")


# 每日 0 点轮转钩子注册表：插件可 register_daily_rollover 注册自己的轮转回调
_daily_rollovers: list = []


def register_daily_rollover(callback) -> None:
    """注册每日 0 点轮转回调（每次回调返回待通知消息列表，返回空则无提示）。"""
    if callback not in _daily_rollovers:
        _daily_rollovers.append(callback)


def refresh():
    global DATA, PATH
    PATH = Path(__file__).parent.joinpath(f"day/{date_today()}.json")
    if not PATH.exists():
        PATH.touch()
        PATH.write_text("{}", encoding="utf-8")
    DATA = ujson.loads(PATH.read_text(encoding="utf-8"))

    # 每日轮转：执行各插件注册的 0 点钩子（如 StoryTeller E10 连续未冒险累计）
    for cb in list(_daily_rollovers):
        try:
            for msg in cb():
                logger.info(f"[每日轮转] {msg}")
        except Exception as e:  # noqa: BLE001 - 单个钩子失败不阻断每日刷新
            logger.warning(f"每日轮转钩子失败: {type(e).__name__} {e}")

    logger.info(f"{date_today()}初始化完成")


back = BackgroundScheduler()
back.add_job(write_json, CronTrigger.from_crontab("* 0 * * *"))
back.add_job(refresh, CronTrigger.from_crontab("0 0 * * *"))
if not back.running:
    try:
        back.start()
    except Exception as e:  # noqa: BLE001 - 已运行/启动失败不阻断导入
        logger.warning(f"每日任务调度器启动失败: {e}")

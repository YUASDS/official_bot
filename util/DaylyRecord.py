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


def refresh():
    global DATA, PATH
    PATH = Path(__file__).parent.joinpath(f"day/{date_today()}.json")
    if not PATH.exists():
        PATH.touch()
        PATH.write_text("{}", encoding="utf-8")
    DATA = ujson.loads(PATH.read_text(encoding="utf-8"))
    logger.info(f"{date_today()}初始化完成")


back = BackgroundScheduler()
back.add_job(write_json, CronTrigger.from_crontab("* 0 * * *"))
back.add_job(refresh, CronTrigger.from_crontab("0 0 * * *"))

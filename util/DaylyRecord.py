import os
import threading
from pathlib import Path
from typing import Union

import ujson
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from .TimeTool import date_today

_day_env = os.environ.get("STORYTELLER_DAY_DIR")
DAY_DIR = Path(_day_env) if _day_env else Path(__file__).parent.joinpath("day")
ORGIN_PATH = DAY_DIR
PATH = DAY_DIR.joinpath(f"{date_today()}.json")
_LOCK = threading.RLock()

ORGIN_PATH.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, text: str) -> None:
    """同目录临时文件 + os.replace，避免进程被杀时留下半截 JSON。"""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _load_day(path: Path) -> dict[str, dict]:
    """加载某日 JSON；损坏文件先备份再重置为空 dict，保证 bot 可启动。"""
    if not path.exists():
        _atomic_write(path, "{}")
        return {}
    try:
        data = ujson.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("daily json 顶层不是对象")
        return data
    except Exception as e:  # noqa: BLE001 - 损坏日文件不应阻断启动
        backup = path.with_name(path.name + ".corrupt")
        logger.warning(f"每日状态文件损坏，已备份到 {backup.name}: {type(e).__name__} {e}")
        try:
            if backup.exists():
                backup.unlink()
            path.replace(backup)
        except Exception as backup_err:  # noqa: BLE001
            logger.warning(f"备份损坏日文件失败: {backup_err}")
        _atomic_write(path, "{}")
        return {}


with _LOCK:
    DATA: dict[str, dict] = _load_day(PATH)
logger.info(f"{date_today()}初始化完成")


def add_data(qq: Union[int, str], key: str, value) -> None:
    if isinstance(qq, int):
        qq = str(qq)
    with _LOCK:
        if not isinstance(DATA.get(qq), dict):
            DATA[qq] = {}
        DATA[qq][key] = value


def get_data(qq: Union[int, str], key: str):
    if isinstance(qq, int):
        qq = str(qq)
    with _LOCK:
        row = DATA.get(qq)
        return row.get(key, None) if isinstance(row, dict) else None


def write_json() -> None:
    """持久化当前日状态（原子替换）。失败仅告警，不抛给业务层。"""
    try:
        with _LOCK:
            path = PATH
            text = ujson.dumps(DATA, ensure_ascii=False)
        _atomic_write(path, text)
    except Exception as e:  # noqa: BLE001 - 落盘失败不应打断游戏流程
        logger.warning(f"每日状态写入失败: {type(e).__name__} {e}")


# 每日 0 点轮转钩子注册表：插件可 register_daily_rollover 注册自己的轮转回调
_daily_rollovers: list = []


def register_daily_rollover(callback) -> None:
    """注册每日 0 点轮转回调（每次回调返回待通知消息列表，返回空则无提示）。"""
    if callback not in _daily_rollovers:
        _daily_rollovers.append(callback)


def refresh() -> None:
    global DATA, PATH
    new_path = DAY_DIR.joinpath(f"{date_today()}.json")
    with _LOCK:
        new_data = _load_day(new_path)
        PATH = new_path
        DATA = new_data

    # 每日轮转：执行各插件注册的 0 点钩子（如每日统计快照/日报）
    for cb in list(_daily_rollovers):
        try:
            for msg in cb():
                logger.info(f"[每日轮转] {msg}")
        except Exception as e:  # noqa: BLE001 - 单个钩子失败不阻断每日刷新
            logger.warning(f"每日轮转钩子失败: {type(e).__name__} {e}")

    logger.info(f"{date_today()}初始化完成")


back = BackgroundScheduler()
# 只在 0 点轮转；状态写入由业务点调用 write_json()（原子写），不再每分钟定时重写。
back.add_job(refresh, CronTrigger.from_crontab("0 0 * * *"))
if not back.running:
    try:
        back.start()
    except Exception as e:  # noqa: BLE001 - 已运行/启动失败不阻断导入
        logger.warning(f"每日任务调度器启动失败: {e}")

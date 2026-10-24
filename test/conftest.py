"""测试环境隔离：DB 与每日状态都指向临时目录，禁止写生产 inv.db/userData.db/util/day。"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_TEST_ROOT = tempfile.mkdtemp(prefix="storyteller-test-")
_NONEBOT_DAY_DIR = os.path.join(_TEST_ROOT, "day")
os.makedirs(_NONEBOT_DAY_DIR, exist_ok=True)

os.environ["STORYTELLER_DB_PATH"] = os.path.join(_TEST_ROOT, "inv.db")
os.environ["STORYTELLER_USER_DB_PATH"] = os.path.join(_TEST_ROOT, "userData.db")
os.environ["STORYTELLER_DAY_DIR"] = _NONEBOT_DAY_DIR
# 避免测试中的 nonebot.init() 读取生产 .env.prod
os.environ["ENVIRONMENT"] = "dev"

atexit.register(shutil.rmtree, _TEST_ROOT, True)

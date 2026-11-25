"""测试环境隔离：DB 与每日状态都指向临时目录，禁止写生产 inv.db/userData.db/util/day。"""

import atexit
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

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

@pytest.fixture
def boss_invitation():
    """通过真实邀请界面提取按钮 token，模拟 QQ 回传同一条消息。"""
    from src.services import boss_framework as bosses
    from unittest.mock import patch

    def issue(boss_id, user_id, bot):
        with patch.object(bosses, "build_keyboard", wraps=bosses.build_keyboard) as keyboard:
            asyncio.run(bosses.send_boss_dialogue(boss_id, user_id, bot, AsyncMock()))
        rows = keyboard.call_args.args[0]
        return int(rows[0][0][1].rsplit(":", 1)[1])

    return issue

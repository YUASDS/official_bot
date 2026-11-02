"""同一用户的游戏操作互斥；重复点击不排队，异常或取消后立即释放。

NoneBot 的异步处理器在同一事件循环运行，检查与登记之间没有 await。
将整个状态转换（含异步渲染）视为一次操作，避免旧请求作用到下一回合。
"""

from functools import wraps
from typing import Any, Awaitable, Callable

_busy_users: set[str] = set()


def exclusive_user_operation(func: Callable[..., Awaitable[Any]]) -> Callable:
    """装饰以 user_id 为第一个参数的异步游戏入口。"""

    @wraps(func)
    async def wrapped(user_id: str, *args, **kwargs) -> Any:
        if user_id in _busy_users:
            return
        _busy_users.add(user_id)
        try:
            return await func(user_id, *args, **kwargs)
        finally:
            _busy_users.discard(user_id)

    return wrapped

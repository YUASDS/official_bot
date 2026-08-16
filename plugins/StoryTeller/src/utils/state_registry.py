"""统一用户状态注册表。

背景（架构债 P2）：各插件/服务各自持有 `user_id -> dict` 状态（guest/gm_room/npc/
qiren/flow/event/door），清理时需逐个 try/except import（character._cleanup_ended_state
原实现）。新增状态就要手改清理处，且插件间互相 import 状态 dict。

方案：状态 dict 定义处调用 `register_state_store(store)` 注册一次；
清理统一走 `clear_user_state(user_id)` 遍历注册表。新状态只需一行注册，
清理处零改动；插件不再互相 import 对方的状态 dict。
"""

_state_stores: list[dict] = []


def register_state_store(store: dict) -> None:
    """注册一个用户状态 dict（幂等：同一对象只注册一次，按身份比较）。"""
    for s in _state_stores:
        if s is store:  # 身份比较：dict 的 == 是内容比较，空 dict 全相等会误判幂等
            return
    _state_stores.append(store)


def clear_user_state(user_id: str) -> None:
    """从所有已注册状态 dict 中移除该用户（异常逐个吞掉，清理不阻断）。"""
    for store in _state_stores:
        try:
            store.pop(user_id, None)
        except Exception:  # noqa: BLE001 - 清理写后不理
            pass

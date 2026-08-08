from ..models.monster import Monster
from ..models.player import Investigator
from .dice_roller import roll_dice


def perform_sanity_check(
    investigator: Investigator, monster: Monster
) -> tuple[bool, str, int]:
    san_loss_str = monster.san_loss
    loss_success_expr = "0"
    loss_fail_expr = "1"

    if "/" in san_loss_str:
        parts = san_loss_str.split("/")
        loss_success_expr = parts[0]
        loss_fail_expr = parts[1]
    else:
        loss_fail_expr = san_loss_str

    current_san = investigator.get_skill("san")
    _res, val = roll_dice("1d100")

    passed = val <= current_san

    loss_expr = loss_success_expr if passed else loss_fail_expr
    _loss_res, loss_val = roll_dice(loss_expr)

    investigator_san = max(0, current_san - loss_val)
    investigator.set_skill("san", investigator_san)

    result_text = "成功" if passed else "失败"
    desc = (
        f"\n【理智检定】{val}/{current_san} → {result_text}\n"
        f"  理智损失：{loss_expr}={loss_val}"
        f"｜当前SAN：{investigator_san}"
    )

    return passed, desc, loss_val

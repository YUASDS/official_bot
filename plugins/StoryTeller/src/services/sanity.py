from ..models.monster import Monster
from ..models.player import Investigator
from .data_loader import data_loader
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

    t = data_loader.get_text
    result_text = t("sanity.success") if passed else t("sanity.fail")
    desc = (
        f"\n{t('sanity.title', dice=val, target=current_san, result=result_text)}\n"
        f"  {t('sanity.loss', expr=loss_expr, value=loss_val)}"
        f"｜{t('sanity.current_san', value=investigator_san)}"
    )

    return passed, desc, loss_val

from ..models.monster import Monster
from ..models.player import Investigator
from ..utils.md_format import report_check_table, report_section
from .data_loader import data_loader
from .dice_roller import get_success_icon, roll_dice


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
    max_san = investigator.get_skill("意志") or current_san
    _res, val = roll_dice("1d100")

    passed = val <= current_san

    loss_expr = loss_success_expr if passed else loss_fail_expr
    _loss_res, loss_val = roll_dice(loss_expr)

    investigator_san = max(0, current_san - loss_val)
    investigator.set_skill("san", investigator_san)

    t = data_loader.get_text
    icon = get_success_icon(1) if passed else get_success_icon(0)
    result_text = t("dice.success") if passed else t("dice.failure")
    result = t(
        "report.san_result",
        icon=icon,
        level=result_text,
        expr=loss_expr,
        value=loss_val,
        cur=investigator_san,
        max=max_san,
    )
    row = t(
        "report.san_row",
        icon=t("report.icon_brains"),
        dice=val,
        target=current_san,
        result=result,
    )
    desc = (
        f"{report_section(t('battle.sanity_title'))}\n"
        f"{report_check_table([row])}"
    )

    return passed, desc, loss_val

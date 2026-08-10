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


def run_sanity_and_madness(
    inv: Investigator, monster: Monster
) -> tuple[str, str, bool, int, bool, int]:
    """理智检定 + （理智损失≥5 时）智力检定。

    返回 (san_desc, madness_desc, is_mad, madness_duration, san_zero, san_loss)。
    san_zero 为 True 表示 SAN 归零（永久疯狂），由调用方结束游戏。
    """
    san_passed, san_desc, san_loss = perform_sanity_check(inv, monster)

    madness_desc = ""
    is_mad = False
    madness_duration = 5
    if not san_passed:
        current_san = inv.get_skill("san")
        if current_san <= 0:
            inv.save()
            return san_desc, madness_desc, is_mad, madness_duration, True, san_loss

        # 理智损失 ≥5 才进行智力检定：成功则陷入临时疯狂
        if san_loss >= 5:
            int_val = inv.get_skill("智力")
            _, int_check_res = roll_dice("1d100")
            if int_check_res <= int_val:
                _, madness_duration = roll_dice("1d10")
                is_mad = True
                level_icon = get_success_icon(1)
                level_text = data_loader.get_text("dice.success")
                quote = data_loader.get_text(
                    "adventure.int_check_success",
                    duration=madness_duration,
                )
            else:
                level_icon = get_success_icon(0)
                level_text = data_loader.get_text("dice.failure")
                quote = data_loader.get_text("adventure.int_check_fail")

            row = data_loader.get_text(
                "adventure.int_check_row",
                icon=data_loader.get_text("report.icon_brains"),
                dice=int_check_res,
                target=int_val,
                result=data_loader.get_text(
                    "report.check_result",
                    icon=level_icon,
                    level=level_text,
                ),
            )
            madness_desc = (
                f"\n{report_section(data_loader.get_text('adventure.int_check_title'))}\n"
                f"{report_check_table([row])}\n"
                f"{quote}"
            )
    inv.save()  # 持久化 SAN 扣减
    return san_desc, madness_desc, is_mad, madness_duration, False, san_loss

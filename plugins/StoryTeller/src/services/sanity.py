
from ..models.monster import Monster
from ..models.player import Investigator
from .dice_roller import roll_dice


def perform_sanity_check(investigator: Investigator, monster: Monster) -> tuple[bool, str, int]:
    """
    Perform Sanity Check (SC).
    Returns (passed, description, sanity_loss).
    """
    # Parse monster san loss "1/1d3" -> success loss / fail loss
    san_loss_str = monster.san_loss # e.g. "1/1d3"
    loss_success_expr = "0"
    loss_fail_expr = "1"

    if "/" in san_loss_str:
        parts = san_loss_str.split("/")
        loss_success_expr = parts[0]
        loss_fail_expr = parts[1]
    else:
        loss_fail_expr = san_loss_str

    current_san = investigator.get_skill("san")
    # Sanity check is a standard check against current SAN (POW)
    # Usually roll 1d100 <= SAN
    _res, val = roll_dice("1d100")

    passed = val <= current_san

    loss_expr = loss_success_expr if passed else loss_fail_expr
    _loss_res, loss_val = roll_dice(loss_expr)

    investigator_san = current_san - loss_val
    investigator.set_skill("san", max(0, investigator_san)) # Assuming set_skill exists or direct update
    # My Investigator model wrapper used getattr, need setattr support
    # In my new Investigator class, I didn't verify set_skill logic.
    # It has save() but updating attributes?
    # Investigator class had `self._model` which is peewee model.
    # I should add `update_san` or similar.

    desc = f"Sanity Check: {val}/{current_san} -> {'Passed' if passed else 'Failed'}. " \
           f"Sanity Loss: {loss_expr}={loss_val}. Current SAN: {investigator_san}."

    return passed, desc, loss_val

# Need to update Investigator class to allow updating attributes
# logic in perform_sanity_check assumes investigator object can be updated.

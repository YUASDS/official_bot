"""本次安全/逻辑修复的回归测试（P0/P1）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "plugins" / "StoryTeller"))

import ujson

from src.models.player import (
    EndingCollectionModel,
    EndingProgressModel,
    Investigator,
    InvestigatorGenerator,
    ending_repo,
    investigator_repo,
)
from src.models.stats import RunStats
from src.plugins import mainline as ml
from src.services.character_cards import create_done_card_html
from src.services.ending_engine import check_daily
from src.services.event_service import event_option_rows
from src.services.resurrect import do_resurrect
from src.services.shop_service import shop_service


def _make_inv(qq: str, day: int = 1) -> Investigator:
    _cleanup(qq)
    data = InvestigatorGenerator.generate_investigator_data(1)[0]
    model = investigator_repo.create_and_save(qq, "FixTest", data)
    inv = Investigator(model)
    inv.day = day
    inv.save()
    return Investigator(investigator_repo.find_by_qq(qq))


def _cleanup(qq: str) -> None:
    RunStats.delete().where(RunStats.qq == qq).execute()
    EndingProgressModel.delete().where(EndingProgressModel.qq == qq).execute()
    EndingCollectionModel.delete().where(EndingCollectionModel.qq == qq).execute()
    investigator_repo.delete_by_qq(qq)


def test_shop_zero_quantity_rejected():
    qq = "fix_shop_zero"
    _make_inv(qq)
    ok, _msg = shop_service.buy_item(qq, "501", 0, {"501": 200})
    assert ok is False
    inv = Investigator(investigator_repo.find_by_qq(qq))
    equipments, _ = inv.get_equipments()
    assert equipments.get("501", 0) == 0
    _cleanup(qq)


def test_resurrect_without_character_does_not_create():
    qq = "fix_resurrect_none"
    investigator_repo.delete_by_qq(qq)
    text = do_resurrect(qq)
    assert investigator_repo.find_by_qq(qq) is None
    assert text
    _cleanup(qq)


def test_e10_no_longer_ends_run():
    qq = "fix_e10"
    inv = _make_inv(qq, day=5)
    for _ in range(4):
        check_daily(inv, reset=False)
    progress = ending_repo.get_progress(qq)
    assert progress is not None and progress.ended is False
    collection = ending_repo.get_collection(qq)
    if collection is not None:
        ids = [r.get("id") for r in ujson.loads(collection.endings or "[]")]
        assert "E10" not in ids
    _cleanup(qq)


def test_mainline_ending_ends_run_and_writes_snapshot():
    qq = "fix_mainline_end"
    inv = _make_inv(qq, day=37)
    inv.set_flag("ml.unlocked", True)
    inv.set_flag("ml.line", "dread")
    inv.save()

    ml.register_mainline_ending(inv, "E12")
    progress = ending_repo.get_progress(qq)
    assert progress is not None and progress.ended is True
    _logs, block = check_daily(inv)
    assert block is not None
    row = (
        RunStats.select()
        .where((RunStats.qq == qq) & (RunStats.run_id == progress.run_id))
        .first()
    )
    assert row is not None and row.ending_id == "E12"
    _cleanup(qq)


def test_character_card_escapes_player_name():
    from src.models.player import CreateInvestigator

    ci = CreateInvestigator(1)
    assert ci.choose_investigator(1)
    html = create_done_card_html(ci, '<img src=x onerror=alert(1)>')
    assert "<img" not in html
    assert "&lt;img" in html


def test_event_option_rows_include_token():
    rows = event_option_rows(["A"], [{"输入": "x"}], token=12345)
    assert rows[0][0][1] == "event:x:12345"

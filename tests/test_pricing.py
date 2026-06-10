import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quotebot.pricing import JobSpec, calculate, scaffold_from_text


def test_spreadsheet_full_calculator_example():
    # Mirrors the xlsx Full Quick Calculator defaults:
    # 15 panels, 2 batteries, 1 face, 1 string, £800 scaffold, G99, no EV
    q = calculate(JobSpec(panels=15, batteries=2, roof_faces=1, dc_strings=1,
                          scaffold="two_storey"))
    # 1200+375+440+200+150+50+200+80+400+800+300+100 = 4295
    assert q.total == 4295


def test_small_bungalow_no_g99():
    # 6 panels @0.44kW = 2.64kW <= 3.68 -> no G99 line
    q = calculate(JobSpec(panels=6, batteries=1, scaffold="bungalow"))
    labels = [l for l, _ in q.lines]
    assert not any("G99" in l for l in labels)
    # 480+150+220+100+150+50+200+80+400+400+100 = 2330
    assert q.total == 2330


def test_g99_auto_threshold():
    assert not calculate(JobSpec(panels=8, panel_wattage_kw=0.46)).spec.g99_required  # 3.68 exactly
    assert calculate(JobSpec(panels=9, panel_wattage_kw=0.44)).spec.g99_required      # 3.96


def test_ev_charger_and_extras():
    base = calculate(JobSpec(panels=10)).total
    with_ev = calculate(JobSpec(panels=10, ev_charger=True)).total
    assert with_ev - base == 150
    with_cu = calculate(JobSpec(panels=10, extras=["new_consumer_unit"])).total
    assert with_cu - base == 400


def test_scaffold_from_bare_storey_digits():
    # "storeys=1" from a phone command must not silently price as two-storey
    assert scaffold_from_text("1") == "bungalow"
    assert scaffold_from_text("2") == "two_storey"
    assert scaffold_from_text("3") == "three_storey"


def test_scaffold_from_text():
    assert scaffold_from_text("Bungalow with easy access") == "bungalow"
    assert scaffold_from_text("3 storey terrace") == "three_storey"
    assert scaffold_from_text("two storey hipped roof") == "two_storey_complex"
    assert scaffold_from_text("standard semi detached") == "two_storey"
    assert scaffold_from_text("") == "two_storey"

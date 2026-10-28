from app.agent.guardrail import check, figures_in, numbers_from

RESULT = {
    "best": {
        "metrics": {
            "power_avg": {"value": 7.86, "unit": "W"},
            "accuracy": {"value": 0.985},
            "latency": {"value": 24.4, "unit": "ms"},
        },
        "config": {"power_mode_id": "15W", "batch": 1},
        "energy_saving_percent": 72.3,
    }
}


def ok(text, extra=None):
    return check(text, [RESULT], extra)


def test_numbers_from_tool_results_are_accepted_with_human_rounding():
    assert ok("Average power is 7.86 W, or about 7.9 W, or roughly 8 W.").ok
    assert ok("Energy drops by 72.3%, about 72%.").ok
    assert ok("Latency is 24.4 ms.").ok


def test_fraction_to_percent_and_ms_to_s_scaling():
    assert ok("Accuracy retention is 98.5%.").ok  # 0.985 -> 98.5 %
    assert ok("That is 0.0244 s per frame.").ok  # 24.4 ms -> 0.0244 s


def test_numbers_inside_ids_count_as_present():
    assert ok("Use the 15W mode.").ok  # "15W" appears as a string in the tool result


def test_fabricated_figures_are_flagged_and_redacted():
    r = ok("It saves 99.5% energy and runs at 31 W.")
    assert not r.ok and r.unverified == ["99.5", "31"]
    assert "99.5" not in r.text and r.text.count("[unverified figure]") == 2


def test_small_whole_numbers_without_units_are_tolerated_but_units_are_not():
    assert ok("There are three options; option 2 is best, and 3 changes are needed.").ok
    r = ok("It would draw 5 W.")
    assert not r.ok and r.unverified == ["5"]
    assert not ok("It takes 9 ms.").ok


def test_numbers_the_user_gave_are_allowed():
    text = "At 30 FPS and 40 °C ambient this holds."
    assert not ok(text).ok
    assert ok(text, ["Run it at 30 FPS, 40 °C ambient"]).ok


def test_list_markers_and_identifiers_are_not_data():
    assert ok("1. Switch precision\n2. Lower the clock\n3) Add a heatsink").ok
    assert [f.raw for f in figures_in("YOLOv8s on fp16 and int8")] == []


def test_thousands_separators_and_decimals_parse():
    assert [f.value for f in figures_in("12,500 mJ and 3.25 W")] == [12500.0, 3.25]


def test_numbers_from_walks_nested_structures():
    got = numbers_from({"a": [1, {"b": 2.5}], "c": "15W", "d": True, "e": None})
    assert sorted(got) == [1.0, 2.5, 15.0]

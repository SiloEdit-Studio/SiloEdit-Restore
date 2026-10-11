from src.ui.main_window import preferred_result_path


def test_final_composition_is_preferred_for_result_preview():
    record = {
        "output": "partial.jpg",
        "hat_output": "cover.jpg",
        "final_output": "final.jpg",
    }

    assert preferred_result_path(record) == "final.jpg"


def test_result_preview_falls_back_to_cover_then_partial_output():
    assert preferred_result_path({
        "output": "partial.jpg",
        "hat_output": "cover.jpg",
        "final_output": None,
    }) == "cover.jpg"
    assert preferred_result_path({
        "output": "partial.jpg",
        "hat_output": None,
        "final_output": None,
    }) == "partial.jpg"

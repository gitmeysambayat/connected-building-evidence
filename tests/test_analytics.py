"""Independent checks of chronological evaluation and real-record lineage.

Small in-memory fixtures below test join failure cases. Reported experiments
are fitted separately from the unchanged, pinned measured CSV files.
"""

from __future__ import annotations

from bisect import bisect_right
import copy
import hashlib
import json
from pathlib import Path
import shutil

import joblib
import numpy as np
import pytest

from twin import analytics


def _split(sample):
    issue, target = sample["issue"], sample["target"]
    cutoff1 = issue[0] + 0.6 * (issue[-1] - issue[0])
    cutoff2 = issue[0] + 0.8 * (issue[-1] - issue[0])
    return (
        (issue < cutoff1) & (target < cutoff1),
        (issue >= cutoff1) & (issue < cutoff2) & (target < cutoff2),
        issue >= cutoff2,
    )


@pytest.fixture(scope="module")
def fitted(tmp_path_factory):
    output = tmp_path_factory.mktemp("temperature-experiment") / "report.json"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(analytics, "OUTPUT", output)
        result = analytics.fit(force=True)
    return result, output.parent


def test_previous_join_rejects_future_and_stale_observations():
    observations = np.array([[10.0, 1.0], [20.0, 2.0], [50.0, 999.0]])
    values, valid = analytics.previous(observations, np.array([9.0, 10.0, 19.0, 30.0, 31.0, 49.0]), 10)
    assert valid.tolist() == [False, True, True, True, False, False]
    assert values[valid].tolist() == [1.0, 1.0, 2.0]
    # The large future value cannot enter an accepted feature before time 50.
    assert 999.0 not in values[valid]


def test_build_examples_rejects_missing_weather_and_uses_no_future_control(monkeypatch):
    issue_times = np.arange(0.0, 6 * 3600.0 + 1, 300.0)
    air = np.column_stack([issue_times, 20.0 + issue_times / 36000.0])
    weather = np.array([[0.0, 5.0], [5 * 3600.0, 999.0]])
    controls = np.array([[0.0, 18.0], [5 * 3600.0, 99.0]])

    def fixture_load(name):
        if name.endswith("_Temperature"):
            return air
        if name.endswith("_SetpointHistory"):
            return controls
        return weather

    monkeypatch.setattr(analytics, "load", fixture_load)
    sample = analytics.build_examples("Bathroom")
    # From 2 h + 5 min to 5 h, the last weather observation exceeds its 2 h cap.
    assert not np.any((sample["issue"] > 7200) & (sample["issue"] < 18000))
    earlier = sample["issue"] < 18000
    assert np.all(sample["x"][earlier, 3] == 5.0)
    assert np.all(sample["x"][earlier, 4] == 18.0)
    assert np.all(sample["target"] > sample["issue"])


@pytest.mark.parametrize("room", analytics.ROOMS)
def test_real_examples_use_measured_targets_and_available_feature_rows(room):
    sample = analytics.build_examples(room)
    air = analytics.load(room + "_Temperature")
    weather = analytics.load(room + ("_OutdoorTemperature" if room == "Room2" else "_Virtual_OutdoorTemperature"))
    control = analytics.load(room + "_SetpointHistory")
    assert np.all(np.diff(air[:, 0]) > 0)
    assert np.isfinite(air).all()
    issue_indices = sample["source_line"] - 1
    np.testing.assert_array_equal(air[issue_indices, 0], sample["issue"])
    np.testing.assert_array_equal(air[issue_indices, 1], sample["x"][:, 0])
    target_indices = np.searchsorted(air[:, 0], sample["target"])
    np.testing.assert_array_equal(air[target_indices, 0], sample["target"])
    np.testing.assert_array_equal(air[target_indices, 1], sample["y"])
    assert np.all(abs(sample["target"] - sample["issue"] - 3600) <= 300)
    assert np.all(sample["target"] > sample["issue"])

    # Independent bisect lookup checks records throughout the actual time span.
    for row in np.linspace(0, len(sample["issue"]) - 1, 21, dtype=int):
        issued = sample["issue"][row]
        for feature, series, requested, maximum_age in (
            (1, air, issued - 1800, 900),
            (2, air, issued - 3600, 900),
            (3, weather, issued, 7200),
            (4, control, issued, 86400),
        ):
            index = bisect_right(series[:, 0].tolist(), requested) - 1
            assert index >= 0
            assert 0 <= requested - series[index, 0] <= maximum_age
            assert sample["x"][row, feature] == series[index, 1]


@pytest.mark.parametrize("room", analytics.ROOMS)
def test_split_purges_future_targets_and_scaler_is_train_only(room, fitted):
    report, model_directory = fitted
    result = next(row for row in report["rooms"] if row["room"] == room)
    sample = analytics.build_examples(room)
    train, validation, test = _split(sample)
    assert sample["target"][train].max() < sample["issue"][validation].min()
    assert sample["target"][validation].max() < sample["issue"][test].min()
    assert not np.any(train & validation)
    assert not np.any(train & test)
    assert not np.any(validation & test)
    assert [result["train_count"], result["validation_count"], result["test_count"]] == [int(mask.sum()) for mask in (train, validation, test)]
    assert result["purged_at_boundaries"] == len(sample["issue"]) - sum(int(mask.sum()) for mask in (train, validation, test))
    saved = joblib.load(model_directory / f"20261018_v1_{room}_temperature_model.joblib")
    scaler = saved["model"].named_steps["standardscaler"]
    assert scaler.n_samples_seen_ == train.sum()
    np.testing.assert_allclose(scaler.mean_, sample["x"][train].mean(axis=0), rtol=1e-12)


@pytest.mark.parametrize("room", analytics.ROOMS)
def test_report_metrics_and_examples_reproduce_from_saved_model(room, fitted):
    report, model_directory = fitted
    result = next(row for row in report["rooms"] if row["room"] == room)
    sample = analytics.build_examples(room)
    _, validation, test = _split(sample)
    saved = joblib.load(model_directory / f"20261018_v1_{room}_temperature_model.joblib")
    model = saved["model"]
    actual = sample["y"][test]
    baseline = sample["x"][test, 0]
    prediction = baseline + model.predict(sample["x"][test])
    error = prediction - actual
    assert result["model"]["mae"] == pytest.approx(np.mean(abs(error)), abs=5e-6)
    assert result["model"]["rmse"] == pytest.approx(np.sqrt(np.mean(error ** 2)), abs=5e-6)
    assert result["model"]["bias"] == pytest.approx(np.mean(error), abs=5e-6)
    assert result["baseline"]["mae"] == pytest.approx(np.mean(abs(baseline - actual)), abs=5e-6)
    validation_prediction = sample["x"][validation, 0] + model.predict(sample["x"][validation])
    descriptive_band = np.quantile(abs(validation_prediction - sample["y"][validation]), 0.9)
    assert result["interval_half_width"] == pytest.approx(descriptive_band, abs=5e-6)
    assert result["test_interval_coverage"] == pytest.approx(np.mean(abs(error) <= descriptive_band), abs=5e-6)
    # The coverage percentage concerns retained observed rows, not elapsed time.
    assert result["eligibility_percent"] == pytest.approx(100 * len(sample["issue"]) / sample["source_rows"], abs=0.005)
    assert len(result["example"]) <= 240

    air = analytics.load(room + "_Temperature")
    for example in result["example"]:
        issue_line = example["issue_source_line"]
        target_line = example["target_source_line"]
        assert analytics.iso(air[issue_line - 1, 0]) == example["issued_at"]
        assert analytics.iso(air[target_line - 1, 0]) == example["timestamp"]
        assert example["baseline"] == round(float(air[issue_line - 1, 1]), 4)
        assert example["actual"] == round(float(air[target_line - 1, 1]), 4)


def test_source_hashes_match_pinned_measured_records(fitted):
    report, _ = fitted
    for filename, expected in report["source_sha256"].items():
        assert hashlib.sha256((analytics.SOURCE / "Measurements" / filename).read_bytes()).hexdigest() == expected


def test_validation_gate_rejects_models_that_underperform_persistence(fitted):
    report, _ = fitted
    for result in report["rooms"]:
        assert result["validation_baseline"]["mae"] < result["validation_model"]["mae"]
        assert result["recommended_predictor"] == "persistence"
        assert result["model_assurance"] == "Ridge rejected by validation gate"
        assert result["recommendation_basis"] == "validation MAE only; no test-based model selection"
    # The actual held-out negative result must remain visible after rejection.
    assert all(row["model"]["mae"] > row["baseline"]["mae"] for row in report["rooms"])


def test_test_labels_cannot_change_selection_or_recommendation(tmp_path, monkeypatch):
    original = analytics.build_examples("Kitchen")
    monkeypatch.setattr(analytics, "ROOMS", ("Kitchen",))
    monkeypatch.setattr(analytics, "OUTPUT", tmp_path / "report.json")
    monkeypatch.setattr(analytics, "build_examples", lambda _room: original)
    first = analytics.fit(force=True)["rooms"][0]
    model_path = tmp_path / "20261018_v1_Kitchen_temperature_model.joblib"
    first_model = joblib.load(model_path)["model"].named_steps["ridge"]
    coefficients = first_model.coef_.copy()
    modified = copy.deepcopy(original)
    _, _, held_out = _split(modified)
    # A deliberately extreme in-memory held-out target tests information flow.
    # The pinned source CSVs are never edited.
    modified["y"][held_out] += 10000.0
    monkeypatch.setattr(analytics, "build_examples", lambda _room: modified)
    second = analytics.fit(force=True)["rooms"][0]
    second_model = joblib.load(model_path)["model"].named_steps["ridge"]
    np.testing.assert_array_equal(coefficients, second_model.coef_)
    assert first["selected_alpha"] == second["selected_alpha"]
    assert first["validation_model"] == second["validation_model"]
    assert first["recommended_predictor"] == second["recommended_predictor"]
    assert first["model_assurance"] == second["model_assurance"]
    assert first["model"]["mae"] != second["model"]["mae"]


def test_report_rejects_changed_source_data_or_different_ifc(fitted, tmp_path, monkeypatch):
    result, _ = fitted
    original_source = analytics.SOURCE
    copied_source = tmp_path / "source"
    measurements = copied_source / "Measurements"
    measurements.mkdir(parents=True)
    for filename in result["source_sha256"]:
        shutil.copyfile(original_source / "Measurements" / filename, measurements / filename)
    shutil.copyfile(original_source / "04_Flat.ifc", copied_source / "04_Flat.ifc")
    output = tmp_path / "report.json"
    output.write_text(json.dumps(result), encoding="utf-8")
    monkeypatch.setattr(analytics, "SOURCE", copied_source)
    monkeypatch.setattr(analytics, "OUTPUT", output)
    assert analytics.report()["status"] == "fitted"
    selected = next(iter(result["source_sha256"]))
    changed = measurements / selected
    changed.write_bytes(changed.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="stale|source has changed"):
        analytics.report()
    shutil.copyfile(original_source / "Measurements" / selected, changed)
    changed_ifc = copied_source / "04_Flat.ifc"
    changed_ifc.write_bytes(changed_ifc.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="different IFC source"):
        analytics.report()

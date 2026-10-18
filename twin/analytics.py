"""Chronological one-hour room-temperature experiment on the pinned public CSVs.

This is an empirical forecast, not a calibrated thermal/structural model. All
    features are observations assumed available by their recorded issue time. Future measured targets
are joined within five minutes; no interpolation across gaps is used.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "source"
OUTPUT = Path(os.environ.get("TWIN_ANALYTICS_PATH", ROOT / "data" / "derived" / "20261018_v1_temperature_forecast.json"))
ROOMS = ("Bathroom", "Kitchen", "Room1", "Room2", "Room3", "Toilet")
FEATURES = ["air_temperature_now", "air_temperature_30min_ago", "air_temperature_60min_ago",
            "outdoor_temperature_available_now", "last_recorded_setpoint", "hour_sin_utc", "hour_cos_utc"]


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat().replace("+00:00", "Z")


def load(name: str) -> np.ndarray:
    return np.loadtxt(SOURCE / "Measurements" / f"{name}.csv", delimiter="\t", ndmin=2)


def previous(series: np.ndarray, times: np.ndarray, max_age: int) -> tuple[np.ndarray, np.ndarray]:
    """As-of join: never take an observation from after the requested issue time."""
    index = np.searchsorted(series[:, 0], times, side="right") - 1
    clipped = np.maximum(index, 0)
    valid = (index >= 0) & (times - series[clipped, 0] <= max_age)
    return series[clipped, 1], valid


def build_examples(room: str) -> dict:
    air = load(f"{room}_Temperature")
    weather_name = f"{room}_OutdoorTemperature" if room == "Room2" else f"{room}_Virtual_OutdoorTemperature"
    weather = load(weather_name)
    control = load(f"{room}_SetpointHistory")
    t = air[:, 0]
    lag30, ok30 = previous(air, t - 1800, 900)
    lag60, ok60 = previous(air, t - 3600, 900)
    outside, okoutside = previous(weather, t, 7200)
    # A last command may persist, but the experimental feature is capped at 24 h
    # to avoid asserting a controller state across arbitrarily long gaps.
    setpoint, okset = previous(control, t, 86400)
    target_time = t + 3600
    right = np.clip(np.searchsorted(t, target_time), 0, len(t) - 1)
    left = np.maximum(right - 1, 0)
    target_index = np.where(abs(t[right] - target_time) < abs(t[left] - target_time), right, left)
    ok_target = abs(t[target_index] - target_time) <= 300
    phase = (t % 86400) * (2 * np.pi / 86400)
    x = np.column_stack([air[:, 1], lag30, lag60, outside, setpoint, np.sin(phase), np.cos(phase)])
    mask = ok30 & ok60 & okoutside & okset & ok_target & np.isfinite(x).all(axis=1)
    return {"x": x[mask], "y": air[target_index[mask], 1], "issue": t[mask],
            "target": t[target_index[mask]], "source_line": np.arange(1, len(t) + 1)[mask],
            "target_source_line": target_index[mask] + 1,
            "source_rows": len(t), "source_files": [f"{room}_Temperature.csv", weather_name + ".csv", f"{room}_SetpointHistory.csv"]}


def metrics(actual, predicted) -> dict:
    error = np.asarray(predicted) - np.asarray(actual)
    return {"mae": round(float(np.mean(abs(error))), 5), "rmse": round(float(np.sqrt(np.mean(error ** 2))), 5),
            "bias": round(float(np.mean(error)), 5), "unit": "°C"}


def fit(force: bool = False) -> dict:
    if OUTPUT.exists() and not force:
        return report()
    # Serving the verified report does not need the training runtime in memory.
    import joblib
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    audit = json.loads((SOURCE / "20261018_v1_Exact_Mapping_and_Data_Audit.json").read_text(encoding="utf-8"))
    room_guids = {r["room"]: r["ifc_guid"] for r in audit["room_mapping"]}
    result = {"status": "fitted", "experiment": "one-hour air-temperature forecast", "horizon_minutes": 60,
              "target_tolerance_seconds": 300, "generated_at": datetime.now(timezone.utc).isoformat(),
              "features": FEATURES, "model_family": "StandardScaler and Ridge regression of temperature change",
              "model_sha256": hashlib.sha256((SOURCE / "04_Flat.ifc").read_bytes()).hexdigest(),
              "source_commit": "34acb1fb7a24005ddf29f7f232081713c2f78291",
              "selection": "alpha selected using validation MAE only from [0.1, 1, 10, 100, 1000]",
              "split": "60/20/20 of each room's eligible issue-time span; training and validation targets purged at next split boundary",
              "interval": "90th percentile absolute validation error after alpha selection on that same validation period; descriptive and selection-affected, not calibrated",
              "rooms": [], "source_sha256": {}, "limitations": [
                  "Published 2017 observations from one flat. No external-building validation or causal energy-saving claim.",
                  "Measured samples are irregular. Eligibility checks exclude missing lags and targets; excluded coverage is reported.",
                  "The persistence comparator predicts the current air temperature one hour ahead.",
                  "Setpoint is a recorded command, outdoor temperature is a shared weather-service feed, and thermostat temperature is not used as room air temperature.",
                  "The measured target may be up to five minutes either side of one hour. The model is not a calibrated physics model.",
                  "Forecast error can vary by room and season. A model beating persistence here does not establish operational suitability."]}
    result["limitations"].extend([
        "Metrics weight eligible observed records equally, not elapsed time. Serial dependence means these are not independent experimental trials.",
        "Eligibility percentage is a fraction of air-temperature source observations, not a fraction of monitored time.",
        "The first experiment is frozen after its test evaluation. Its results are not improved by tuning against the test period."])
    result["limitations"].append("Event timestamps establish ordering. The source does not provide arrival latency, so availability by the recorded timestamp is an experimental assumption.")
    for room in ROOMS:
        sample = build_examples(room)
        x, y, t, target = (sample[k] for k in ("x", "y", "issue", "target"))
        first, last = float(t[0]), float(t[-1])
        cutoff1, cutoff2 = first + .6 * (last - first), first + .8 * (last - first)
        masks = [(t < cutoff1) & (target < cutoff1), (t >= cutoff1) & (t < cutoff2) & (target < cutoff2), t >= cutoff2]
        train, val, test = masks
        candidates = []
        for alpha in (.1, 1, 10, 100, 1000):
            model = make_pipeline(StandardScaler(), Ridge(alpha=alpha))
            model.fit(x[train], y[train] - x[train, 0])
            predicted = x[val, 0] + model.predict(x[val])
            candidates.append((float(np.mean(abs(predicted - y[val]))), alpha, model))
        _, alpha, model = min(candidates, key=lambda item: item[0])
        # Leave the model fitted on training only. The same validation period
        # selects alpha and supplies the descriptive, selection-affected band.
        val_pred = x[val, 0] + model.predict(x[val])
        pred = x[test, 0] + model.predict(x[test])
        band = float(np.quantile(abs(val_pred - y[val]), .9))
        base = x[test, 0]
        test_metrics, base_metrics = metrics(y[test], pred), metrics(y[test], base)
        selected = np.unique(np.linspace(0, int(test.sum()) - 1, min(240, int(test.sum()))).astype(int))
        row = {"room": room, "room_guid": room_guids[room], "train_count": int(train.sum()),
               "validation_count": int(val.sum()), "test_count": int(test.sum()), "source_rows": sample["source_rows"],
               "eligible_count": len(t), "eligibility_percent": round(100 * len(t) / sample["source_rows"], 2),
               "eligibility_basis": "fraction of source air-temperature observations, not elapsed time",
               "purged_at_boundaries": int(len(t) - sum(m.sum() for m in masks)), "selected_alpha": alpha,
               "train_period": [iso(t[train][0]), iso(target[train][-1])],
               "validation_period": [iso(t[val][0]), iso(target[val][-1])], "test_period": [iso(t[test][0]), iso(target[test][-1])],
               "model": test_metrics, "baseline": base_metrics, "validation_model": metrics(y[val], val_pred),
               "validation_baseline": metrics(y[val], x[val, 0]),
               "improvement_percent": round(100 * (1 - test_metrics["mae"] / base_metrics["mae"]), 2),
               "interval_half_width": round(band, 5), "test_interval_coverage": round(float(np.mean(abs(pred-y[test]) <= band)), 5),
               "source_files": sample["source_files"], "example": [
                   {"timestamp": iso(target[test][i]), "issued_at": iso(t[test][i]),
                    "source_line": int(sample["source_line"][test][i]),
                    "issue_source_line": int(sample["source_line"][test][i]),
                    "target_source_line": int(sample["target_source_line"][test][i]), "actual": round(float(y[test][i]), 4),
                    "predicted": round(float(pred[i]), 4), "baseline": round(float(base[i]), 4),
                    "lower": round(float(pred[i] - band), 4), "upper": round(float(pred[i] + band), 4)} for i in selected]}
        row["recommended_predictor"] = "persistence" if row["validation_baseline"]["mae"] <= row["validation_model"]["mae"] else "ridge_experimental"
        row["recommendation_basis"] = "validation MAE only; no test-based model selection"
        row["model_assurance"] = "Ridge rejected by validation gate" if row["recommended_predictor"] == "persistence" else "Ridge passes validation comparator, still experimental"
        result["rooms"].append(row)
        joblib.dump({"model": model, "features": FEATURES, "room_guid": room_guids[room], "trained_until": iso(target[train][-1])},
                    OUTPUT.parent / f"20261018_v1_{room}_temperature_model.joblib")
        for name in sample["source_files"]:
            result["source_sha256"][name] = hashlib.sha256((SOURCE / "Measurements" / name).read_bytes()).hexdigest()
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def report() -> dict:
    if not OUTPUT.exists():
        return {"status": "not_fitted", "rooms": [], "limitations": ["Run scripts/build_analytics.py first."]}
    result = json.loads(OUTPUT.read_text(encoding="utf-8"))
    for name, expected in result.get("source_sha256", {}).items():
        target = (SOURCE / "Measurements" / name).resolve()
        if not target.is_relative_to((SOURCE / "Measurements").resolve()) or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError("Forecast report is stale or its pinned source has changed: " + name)
    if result.get("model_sha256") != hashlib.sha256((SOURCE / "04_Flat.ifc").read_bytes()).hexdigest():
        raise ValueError("Forecast report refers to a different IFC source. Rebuild the experiment.")
    return result

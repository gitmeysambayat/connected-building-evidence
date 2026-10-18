"""Descriptive room-air versus recorded heating-command review.

This is a reproducible comparison, not a comfort assessment, fault diagnosis or
energy-saving estimate. Setpoint histories are commands, not measurements.
"""
from datetime import datetime, timezone
import numpy as np
from twin import data

ROOMS = ("Bathroom", "Kitchen", "Room1", "Room2", "Room3", "Toilet")


def compare(air, commands, maximum_command_age=86400, threshold=1.0):
    air = np.asarray(air, dtype=float)
    commands = np.asarray(commands, dtype=float)
    indices = np.searchsorted(commands[:, 0], air[:, 0], side="right") - 1
    clipped = np.maximum(indices, 0)
    ages = air[:, 0] - commands[clipped, 0]
    eligible = (indices >= 0) & (ages <= maximum_command_age)
    delta = air[eligible, 1] - commands[clipped[eligible], 1]
    return {"eligible": eligible, "command_indices": clipped, "ages": ages, "delta": delta,
            "eligible_observations": int(eligible.sum()), "total_air_observations": len(air),
            "observation_coverage_percent": round(100 * float(np.mean(eligible)), 2),
            "mean_air_minus_setpoint_c": round(float(np.mean(delta)), 4) if len(delta) else None,
            "median_air_minus_setpoint_c": round(float(np.median(delta)), 4) if len(delta) else None,
            "mean_absolute_difference_c": round(float(np.mean(abs(delta))), 4) if len(delta) else None,
            "fraction_more_than_1c_below": round(float(np.mean(delta < -threshold)), 5) if len(delta) else None,
            "fraction_more_than_1c_above": round(float(np.mean(delta > threshold)), 5) if len(delta) else None}


def report():
    result = {"status": "calculated", "title": "Heating setpoint tracking", "threshold_c": 1,
        "maximum_command_age_seconds": 86400, "rooms": [],
        "method": "As-of comparison of original room-air observations with the last recorded setpoint at or before that observation, capped at 24 hours old. Observation-weighted; no interpolation.",
        "limitations": [
            "A last recorded command is an assumed reference, not confirmation that a controller applied or maintained it.",
            "The 1 °C comparison band is an illustrative application rule, not a regulatory comfort or equipment-fault limit.",
            "Percentages weight observations, not elapsed time. Irregular sampling and gaps prevent interpreting them as percentages of occupied hours.",
            "No occupancy, heating power or energy-consumption measurements are supplied. These differences cannot establish comfort, faults, energy savings or causation.",
            "Thermostat temperature is measured near a radiator and is not substituted for the room-air channel."]}
    with data.connection() as con:
        for room in ROOMS:
            air_channel, command_channel = (data.get_channel(room + suffix) for suffix in ("_Temperature", "_SetpointHistory"))
            if not (air_channel["mapping_verified"] and command_channel["mapping_verified"] and
                    air_channel["room_guid"] == command_channel["room_guid"] and
                    air_channel["model_sha256"] == command_channel["model_sha256"] and
                    air_channel["source_kind"] == "measured_sensor" and command_channel["source_kind"] == "controller_setpoint"):
                raise ValueError("Setpoint comparison is not supported by a verified same-room source association.")
            rows = []
            for channel in (air_channel, command_channel):
                rows.append(np.asarray(con.execute("SELECT observed_at,value,source_line FROM observations WHERE channel_id=? ORDER BY observed_at,id", (channel["id"],)).fetchall(), dtype=float))
            air, command = rows
            compared = compare(air, command)
            eligible_rows = np.flatnonzero(compared.pop("eligible"))
            command_indices = compared.pop("command_indices")
            ages = compared.pop("ages")
            delta = compared.pop("delta")
            examples = []
            # Extremes are descriptive examples, not fault-labelled events.
            if len(delta):
                for index in sorted({int(np.argmin(delta)), int(np.argmax(delta))}):
                    ai = eligible_rows[index]
                    ci = command_indices[ai]
                    examples.append({"air_timestamp": datetime.fromtimestamp(air[ai,0], timezone.utc).isoformat(),
                        "air_source_line": int(air[ai,2]), "air_c": float(air[ai,1]),
                        "command_timestamp": datetime.fromtimestamp(command[ci,0], timezone.utc).isoformat(),
                        "command_source_line": int(command[ci,2]), "setpoint_c": float(command[ci,1]),
                        "command_age_seconds": int(ages[ai]), "difference_c": round(float(delta[index]), 4)})
            result["rooms"].append({"room": room, "room_guid": air_channel["room_guid"],
                "model_sha256": air_channel["model_sha256"], **compared, "examples": examples,
                "air_channel_id": air_channel["id"], "command_channel_id": command_channel["id"],
                "source_sha256": {air_channel["id"]: air_channel["source_sha256"], command_channel["id"]: command_channel["source_sha256"]}})
    return result

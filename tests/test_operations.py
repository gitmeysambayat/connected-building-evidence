import numpy as np
import pytest
from twin import data, operations


def test_as_of_commands_cannot_use_the_future_or_persist_indefinitely():
    air = [[100,20,1], [200,21,2], [300,22,3], [90000,25,4]]
    commands = [[150,19,1],[250,20,2]]
    r = operations.compare(air, commands)
    assert r["eligible"].tolist() == [False,True,True,False]
    assert r["delta"].tolist() == [2,2]
    assert r["observation_coverage_percent"] == 50


def test_rule_is_strictly_more_than_one_degree_and_observation_weighted():
    air = [[10,19,1],[20,18.9,2],[500,21,3],[510,21.1,4]]
    r = operations.compare(air, [[0,20,1]])
    assert r["fraction_more_than_1c_below"] == .25
    assert r["fraction_more_than_1c_above"] == .25
    assert r["mean_air_minus_setpoint_c"] == 0


def test_actual_report_links_verified_rooms_and_recomputable_source_examples():
    data.initialise()
    r = operations.report()
    assert len(r["rooms"]) == 6
    assert len({row["room_guid"] for row in r["rooms"]}) == 6
    for room in r["rooms"]:
        assert room["eligible_observations"] <= room["total_air_observations"]
        for item in room["examples"]:
            assert 0 <= item["command_age_seconds"] <= 86400
            assert item["difference_c"] == pytest.approx(item["air_c"]-item["setpoint_c"], abs=.0001)
            for prefix, channel in (("air", room["air_channel_id"]), ("command", room["command_channel_id"])):
                with data.connection() as con:
                    source = con.execute("SELECT observed_at,value FROM observations WHERE channel_id=? AND source_line=?", (channel,item[prefix+"_source_line"])).fetchone()
                assert source is not None
                assert source["value"] == item["air_c" if prefix=="air" else "setpoint_c"]
        assert data.get_channel(room["air_channel_id"])["room_guid"] == data.get_channel(room["command_channel_id"])["room_guid"]

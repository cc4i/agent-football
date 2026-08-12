from pathlib import Path

import pytest

import fake_host
import rooms

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "match-3-1.jsonl"


def test_the_shipped_fixture_parses_into_frames():
    frames = fake_host.parse_log(FIXTURE)
    assert len(frames) == 15
    assert frames[0]["kind"] == "kickoff"
    assert frames[-1]["kind"] == "full_time"


def test_the_fixture_is_a_three_one_win_whose_first_goal_is_early():
    goals = [frame for frame in fake_host.parse_log(FIXTURE)
             if frame.get("kind") == "goal"]
    assert [goal["payload"]["team"] for goal in goals] == ["blue", "red", "blue", "blue"]
    assert goals[0]["t"] < 30       # inside the 500-point first-goal bracket


def test_comments_and_blank_lines_are_skipped(tmp_path):
    log = tmp_path / "m.jsonl"
    log.write_text('# a note\n\n{"t": 0, "type": "event", "kind": "kickoff"}\n')
    assert len(fake_host.parse_log(log)) == 1


def test_frames_come_back_in_time_order(tmp_path):
    log = tmp_path / "m.jsonl"
    log.write_text('{"t": 2, "type": "state"}\n{"t": 1, "type": "state"}\n')
    assert [frame["t"] for frame in fake_host.parse_log(log)] == [1, 2]


def test_a_frame_with_no_time_is_refused(tmp_path):
    log = tmp_path / "m.jsonl"
    log.write_text('{"type": "state"}\n')
    with pytest.raises(ValueError, match="numeric 't'"):
        fake_host.parse_log(log)


def test_a_frame_of_an_unknown_type_is_refused(tmp_path):
    log = tmp_path / "m.jsonl"
    log.write_text('{"t": 0, "type": "shout"}\n')
    with pytest.raises(ValueError, match="type must be"):
        fake_host.parse_log(log)


def test_an_event_with_no_kind_is_refused(tmp_path):
    log = tmp_path / "m.jsonl"
    log.write_text('{"t": 0, "type": "event"}\n')
    with pytest.raises(ValueError, match="no kind"):
        fake_host.parse_log(log)


def test_a_state_frame_becomes_a_host_state_message():
    assert fake_host.to_message({"t": 1.5, "type": "state", "payload": {"clock": 178}}) == {
        "type": "host.state", "payload": {"clock": 178}}


def test_an_event_frame_carries_the_match_clock_in_milliseconds():
    frame = {"t": 27.4, "type": "event", "kind": "goal", "payload": {"team": "blue"}}
    assert fake_host.to_message(frame) == {
        "type": "host.event", "kind": "goal", "match_ms": 27400,
        "payload": {"team": "blue"}}


async def test_replay_waits_out_the_gap_between_frames():
    waits, sent = [], []

    async def send(message):
        sent.append(message)

    async def sleep(seconds):
        waits.append(seconds)

    frames = [{"t": 0.0, "type": "state"}, {"t": 2.0, "type": "state"},
              {"t": 3.0, "type": "state"}]
    await fake_host.replay(frames, send, sleep=sleep)
    assert waits == [2.0, 1.0]
    assert len(sent) == 3


async def test_speed_shortens_every_gap():
    waits = []

    async def send(message):
        pass

    async def sleep(seconds):
        waits.append(seconds)

    await fake_host.replay([{"t": 0.0, "type": "state"}, {"t": 6.0, "type": "state"}],
                           send, speed=3.0, sleep=sleep)
    assert waits == [2.0]


def test_the_fixture_replays_into_a_live_room(client, live_room):
    code = live_room()
    frames = fake_host.parse_log(FIXTURE)

    with client.websocket_connect(f"/ws/rooms/{code}?client_id=phone-7") as host:
        host.receive_json()                     # the opening room snapshot
        last = None
        for frame in frames:
            host.send_json(fake_host.to_message(frame))
            last = host.receive_json()

    connection = client.app.state.conn
    log = rooms.events(connection, rooms.by_code(connection, code)["id"])
    assert [entry["kind"] for entry in log] == [
        "kickoff", "goal", "goal", "goal", "goal", "full_time"]
    assert log[1]["match_ms"] == 27400
    assert last == {"type": "event", "seq": 6, "kind": "full_time",
                    "match_ms": 180000, "payload": {"score": [3, 1]}}


def test_non_object_json_raises_value_error(tmp_path):
    log = tmp_path / "m.jsonl"
    log.write_text('"hello"\n')
    with pytest.raises(ValueError):
        fake_host.parse_log(log)

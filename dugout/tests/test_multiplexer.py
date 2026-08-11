import asyncio

from google.antigravity.types import Text, Thought, ToolResult

import channel
import session


class FakeToolCall:
    def __init__(self, name, args=None):
        self.name = name
        self.args = args or {}
        self.id = "call-1"
        self.canonical_path = None
        self.server_name = None


class FakeResponse:
    """Stands in for ChatResponse: three independent async iterators."""

    def __init__(self, thoughts=(), tool_calls=(), chunks=(), usage="u"):
        self._thoughts, self._tool_calls = list(thoughts), list(tool_calls)
        # The real stream carries StreamChunk objects, never bare strings.
        self._chunks = [Text(step_index=0, text=c) if isinstance(c, str) else c
                        for c in chunks]
        self._usage = usage

    async def _drain(self, items, delay):
        for item in items:
            await asyncio.sleep(delay)
            yield item

    @property
    def thoughts(self):
        return self._drain(self._thoughts, 0.001)

    @property
    def tool_calls(self):
        return self._drain(self._tool_calls, 0.002)

    @property
    def chunks(self):
        return self._drain(self._chunks, 0.003)

    @property
    def usage_metadata(self):
        return self._usage


async def collect(response):
    return [e async for e in session.multiplex(response)]


def test_tuning_tool_names_attribute_to_their_subagent():
    assert session.actor_for_tool_call("tune_forward") == "subagent:forward-tuner"
    assert session.actor_for_tool_call("tune_goalkeeper") == "subagent:goalkeeper-tuner"


def test_other_tools_attribute_to_antigravity():
    assert session.actor_for_tool_call("get_match_status") == session.ACTOR_AGENT
    assert session.actor_for_tool_call("run_command") == session.ACTOR_AGENT


async def test_every_event_from_all_three_sources_arrives():
    events = await collect(FakeResponse(
        thoughts=["t1", "t2"],
        tool_calls=[FakeToolCall("get_match_status")],
        chunks=["hello ", "world"]))
    kinds = [e["kind"] for e in events]
    assert kinds.count("thought") == 2
    assert kinds.count("tool_call") == 1
    assert kinds.count("text") == 2
    assert kinds.count("usage") == 1


async def test_usage_is_the_final_event():
    events = await collect(FakeResponse(thoughts=["t"], chunks=["c"]))
    assert events[-1]["kind"] == "usage"


async def test_every_event_carries_an_actor():
    events = await collect(FakeResponse(
        thoughts=["t"], tool_calls=[FakeToolCall("tune_forward")], chunks=["c"]))
    assert all(e["actor"] for e in events)
    by_kind = {e["kind"]: e for e in events}
    assert by_kind["tool_call"]["actor"] == "subagent:forward-tuner"
    assert by_kind["thought"]["actor"] == session.ACTOR_AGENT


async def test_ordering_within_a_single_source_is_preserved():
    events = await collect(FakeResponse(thoughts=["first", "second", "third"]))
    texts = [e["data"] for e in events if e["kind"] == "thought"]
    assert texts == ["first", "second", "third"]


async def test_a_failing_source_becomes_an_error_event_not_a_crash():
    class Exploding(FakeResponse):
        @property
        def thoughts(self):
            async def boom():
                yield "one"
                raise RuntimeError("stream died")
            return boom()

    events = await collect(Exploding(chunks=["still here"]))
    kinds = [e["kind"] for e in events]
    assert "error" in kinds
    assert "text" in kinds
    assert kinds[-1] == "usage"


async def test_the_three_streams_are_pumped_concurrently():
    gate = asyncio.Event()

    class Interlocked(FakeResponse):
        @property
        def thoughts(self):
            async def g():
                yield "releases the gate"
                gate.set()
            return g()

        @property
        def chunks(self):
            async def g():
                await gate.wait()   # a sequential drain never gets here
                yield Text(step_index=0, text="chunk")
            return g()

    events = await asyncio.wait_for(collect(Interlocked()), timeout=2)
    assert [e["kind"] for e in events] == ["thought", "text", "usage"]


async def test_a_raising_source_property_becomes_an_error_event():
    class RaisingProperty(FakeResponse):
        @property
        def thoughts(self):
            raise RuntimeError("property read failed")

    events = await collect(RaisingProperty(tool_calls=[FakeToolCall("foo")], chunks=["c"]))
    kinds = [e["kind"] for e in events]
    assert "error" in kinds
    assert "tool_call" in kinds
    assert "text" in kinds
    assert kinds[-1] == "usage"


async def test_early_consumer_abandonment_closes_cleanly():
    async def abandon(response):
        gen = session.multiplex(response)
        event = await gen.__anext__()
        assert event["kind"] == "thought"
        return

    await abandon(FakeResponse(thoughts=["first", "second"], chunks=["c"]))


async def test_only_text_chunks_reach_the_text_stream():
    # chunks is the unfiltered stream: thoughts and tool calls arrive on it too.
    # Passing them through doubles every event that has its own pump and renders
    # the raw object repr in the match log.
    events = await collect(FakeResponse(chunks=[
        Text(step_index=1, text="on the "),
        Thought(step_index=1, text="internal reasoning"),
        Text(step_index=1, text="ball"),
    ]))
    assert [e["data"] for e in events if e["kind"] == "text"] == ["on the ", "ball"]


async def test_a_thought_is_not_duplicated_by_the_chunk_stream():
    thought = Thought(step_index=0, text="sizing up the kit")
    events = await collect(FakeResponse(thoughts=["sizing up the kit"],
                                        chunks=[thought]))
    assert [e["kind"] for e in events] == ["thought", "usage"]


async def test_text_events_carry_the_step_they_belong_to():
    # Four subagents stream text at once. Without the step index the client
    # concatenates them into one paragraph and the sentences interleave.
    events = await collect(FakeResponse(chunks=[
        Text(step_index=3, text="defender done"),
        Text(step_index=7, text="keeper done"),
    ]))
    steps = [(e["step"], e["data"]) for e in events if e["kind"] == "text"]
    assert steps == [(3, "defender done"), (7, "keeper done")]


def test_a_shout_result_belongs_to_the_game_not_antigravity():
    # The call is Antigravity's, because Antigravity made it. The numbers are
    # the game's, because its own coach, captain and players chose them.
    assert session.actor_for_tool_call("shout_to_the_team") == session.ACTOR_AGENT
    assert session.actor_for_tool_result("shout_to_the_team") == session.ACTOR_GAME


def test_a_tuning_result_is_attributed_to_its_subagent():
    assert (session.actor_for_tool_result("tune_goalkeeper")
            == "subagent:goalkeeper-tuner")


async def test_a_published_result_surfaces_as_a_tool_result_event():
    async def publish_soon():
        # multiplex opens the channel, so wait just long enough for that.
        await asyncio.sleep(0.0001)
        channel.publish("tune_forward", {"ok": True})

    task = asyncio.create_task(publish_soon())
    events = await collect(FakeResponse(thoughts=["thinking"]))
    await task
    results = [e for e in events if e["kind"] == "tool_result"]
    assert len(results) == 1
    assert results[0]["data"].name == "tune_forward"
    assert results[0]["data"].result == {"ok": True}


async def test_a_tune_result_is_attributed_to_its_subagent():
    async def publish_soon():
        await asyncio.sleep(0.0001)
        channel.publish("tune_goalkeeper", {"ok": True})

    task = asyncio.create_task(publish_soon())
    events = await collect(FakeResponse(thoughts=["thinking"]))
    await task
    results = [e for e in events if e["kind"] == "tool_result"]
    assert results[0]["actor"] == "subagent:goalkeeper-tuner"


async def test_a_shout_result_is_attributed_to_the_game():
    async def publish_soon():
        await asyncio.sleep(0.0001)
        channel.publish("shout_to_the_team", {"shouted": "press"})

    task = asyncio.create_task(publish_soon())
    events = await collect(FakeResponse(thoughts=["thinking"]))
    await task
    results = [e for e in events if e["kind"] == "tool_result"]
    assert results[0]["actor"] == session.ACTOR_GAME


async def test_a_turn_with_nothing_published_still_finishes():
    # The channel's pump is not counted in remaining, so the multiplexer does
    # not wait for a fourth _DONE that will never arrive.
    events = await asyncio.wait_for(collect(FakeResponse(thoughts=["t"])), timeout=2)
    assert events[-1]["kind"] == "usage"


async def test_usage_is_still_the_final_event_with_a_published_result():
    async def publish_soon():
        await asyncio.sleep(0.0001)
        channel.publish("tune_forward", {"ok": True})

    task = asyncio.create_task(publish_soon())
    events = await collect(FakeResponse(thoughts=["t"], chunks=["c"]))
    await task
    assert any(e["kind"] == "tool_result" for e in events)
    assert events[-1]["kind"] == "usage"
    assert [e["kind"] for e in events].count("usage") == 1


async def test_a_tool_result_on_the_chunk_stream_is_not_what_we_read():
    # The SDK never puts a ToolResult on `chunks`: conversation.receive_chunks
    # yields Thought, Text and ToolCall only. Anything that appears there is
    # not a tool result, and must not be mistaken for one.
    response = FakeResponse(chunks=[ToolResult(name="tune_forward", result={"ok": True})])
    kinds = [event["kind"] async for event in session.multiplex(response)]
    assert "tool_result" not in kinds

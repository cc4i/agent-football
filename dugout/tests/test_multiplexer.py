import asyncio

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
        self._chunks, self._usage = list(chunks), usage

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
                yield "chunk"
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

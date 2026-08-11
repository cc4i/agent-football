import asyncio

import pytest

import channel


@pytest.fixture(autouse=True)
def fresh():
    channel.close_channel()
    yield
    channel.close_channel()


async def test_a_published_result_comes_back_out():
    channel.open_channel()
    channel.publish("tune_forward", {"ok": True})
    result = await anext(channel.results())
    assert result.name == "tune_forward"
    assert result.result == {"ok": True}


async def test_publishing_with_no_channel_open_is_a_no_op():
    # Every tuning test calls the tool directly, outside a turn. Nothing is
    # listening, and that must not be an error.
    channel.publish("tune_forward", {"ok": True})


async def test_a_new_channel_does_not_carry_the_last_turn_over():
    channel.open_channel()
    channel.publish("tune_forward", {"ok": True})
    channel.open_channel()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(anext(channel.results()), 0.05)


async def test_a_result_published_from_a_worker_thread_arrives():
    # The SDK runs sync tools through asyncio.to_thread, so every tune is
    # published from a thread that is not the event loop's.
    channel.open_channel()
    await asyncio.to_thread(channel.publish, "tune_defender", {"ok": True})
    result = await asyncio.wait_for(anext(channel.results()), 1)
    assert result.name == "tune_defender"


async def test_results_are_read_in_the_order_they_were_published():
    channel.open_channel()
    for role in ("defender", "midfielder", "forward"):
        channel.publish(f"tune_{role}", {"role": role})
    stream = channel.results()
    seen = [(await anext(stream)).result["role"] for _ in range(3)]
    assert seen == ["defender", "midfielder", "forward"]

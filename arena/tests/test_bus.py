import asyncio

import pytest

from bus import WALL, Bus, room_topic


async def test_a_subscriber_receives_what_is_published():
    bus = Bus()
    with bus.subscribe(room_topic("K7F2")) as subscription:
        bus.publish(room_topic("K7F2"), {"type": "state", "clock": 12})
        assert await anext(subscription) == {"type": "state", "clock": 12}


async def test_two_subscribers_on_one_room_both_get_the_frame():
    bus = Bus()
    with bus.subscribe(room_topic("K7F2")) as viewer, \
         bus.subscribe(room_topic("K7F2")) as screen:
        bus.publish(room_topic("K7F2"), {"type": "state"})
        assert await anext(viewer) == {"type": "state"}
        assert await anext(screen) == {"type": "state"}


async def test_rooms_do_not_hear_each_other():
    bus = Bus()
    with bus.subscribe(room_topic("K7F2")) as subscription:
        bus.publish(room_topic("M3QX"), {"type": "state"})
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(anext(subscription), 0.05)


async def test_publishing_with_nobody_listening_is_a_no_op():
    bus = Bus()
    bus.publish(WALL, {"type": "wall", "rooms": []})
    assert bus.subscriber_count(WALL) == 0


async def test_a_slow_subscriber_loses_the_oldest_frames_not_the_newest():
    # A state frame is disposable. A tile that stalls must not hold up the
    # match for everyone else, and a stale position is worth less than a fresh
    # one, so the queue drops from the front.
    bus = Bus()
    with bus.subscribe(WALL, maxsize=2) as subscription:
        for number in range(5):
            bus.publish(WALL, {"n": number})
        assert subscription.dropped == 3
        assert [await anext(subscription) for _ in range(2)] == [{"n": 3}, {"n": 4}]


async def test_closing_a_subscription_stops_delivery():
    bus = Bus()
    subscription = bus.subscribe(WALL)
    subscription.close()
    assert bus.subscriber_count(WALL) == 0
    bus.publish(WALL, {"type": "wall"})
    # The queue has the close sentinel, but nothing else.
    assert subscription.queue.qsize() == 1


async def test_a_topic_with_no_subscribers_left_is_forgotten():
    bus = Bus()
    with bus.subscribe(room_topic("K7F2")):
        assert bus.subscriber_count(room_topic("K7F2")) == 1
    assert bus.subscriber_count(room_topic("K7F2")) == 0


async def test_the_room_topic_is_scoped_by_code():
    assert room_topic("K7F2") == "room:K7F2"
    assert room_topic("K7F2") != WALL


async def test_close_wakes_a_blocked_reader():
    bus = Bus()
    subscription = bus.subscribe(WALL)

    async def reader():
        with pytest.raises(StopAsyncIteration):
            await anext(subscription)

    reader_task = asyncio.create_task(reader())
    await asyncio.sleep(0.01)
    subscription.close()
    await asyncio.wait_for(reader_task, timeout=0.1)

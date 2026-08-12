"""Publish/subscribe for live match traffic. In-process, no broker.

One arena serves one venue, so a dictionary of queues is the whole of it.
State frames are disposable: a subscriber that cannot keep up loses its oldest
frames rather than stalling the match for everybody else.
"""

import asyncio

WALL = "wall"

_CLOSED = object()


def room_topic(code):
    return f"room:{code}"


class Subscription:
    """One socket's feed. Async-iterate it, or `await anext(...)` a single message."""

    def __init__(self, bus, topic, maxsize):
        self._bus = bus
        self.topic = topic
        self.queue = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def deliver(self, message):
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.queue.get_nowait()
            self.queue.put_nowait(message)
            self.dropped += 1

    def close(self):
        self._bus.unsubscribe(self)
        try:
            self.queue.put_nowait(_CLOSED)
        except asyncio.QueueFull:
            self.queue.get_nowait()
            self.queue.put_nowait(_CLOSED)

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.queue.get()
        if message is _CLOSED:
            raise StopAsyncIteration
        return message

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()
        return False


class Bus:
    def __init__(self):
        self._topics = {}

    def subscribe(self, topic, maxsize=64):
        subscription = Subscription(self, topic, maxsize)
        self._topics.setdefault(topic, set()).add(subscription)
        return subscription

    def unsubscribe(self, subscription):
        subscribers = self._topics.get(subscription.topic)
        if subscribers is None:
            return
        subscribers.discard(subscription)
        if not subscribers:
            del self._topics[subscription.topic]

    def publish(self, topic, message):
        """Hand a message to every current subscriber. Never blocks.

        Call this from the event loop. Every route that publishes is `async
        def` for that reason: a sync route runs in a threadpool, and waking a
        waiting consumer from another thread is not safe.
        """
        for subscription in tuple(self._topics.get(topic, ())):
            subscription.deliver(message)

    def subscriber_count(self, topic):
        return len(self._topics.get(topic, ()))

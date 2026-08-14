"""Which grounds instances are connected, and what each of them is running.

The arena assigns rather than the grounds claiming, because the grounds have
nothing to claim from: the wall socket carries only rooms that are already
live, and a room cannot go live without a host. An instance watching for work
would be waiting for a room that is waiting for it.

So this is the arena's answer to a question it could not previously ask - is
there anybody who can run a match right now - and `POST /start` asks it.

In memory rather than in a column, and single-instance for the same reason the
match bus is: a socket lives in the process that accepted it, and an assignment
is only worth anything to the process that can act on it.

Nothing here knows what a match is. It counts slots.
"""


class Grounds:
    def __init__(self):
        # socket -> matches it said it would take, and socket -> how many it has.
        self._spare = {}
        self._load = {}
        # room code -> the socket running it.
        self._where = {}

    def joined(self, socket, capacity):
        """Take an instance's word for how many matches it will hold.

        A capacity that is not a number, or is negative, becomes nought: an
        instance that announces rubbish is given no work rather than all of it.
        """
        try:
            announced = int(capacity)
        except (TypeError, ValueError):
            announced = 0
        self._spare[socket] = max(0, announced)
        self._load[socket] = 0

    def left(self, socket):
        """Forget an instance and everything it was running.

        Its matches are not reassigned. A live room whose grounds went away
        stops reporting and the sweep abandons it, exactly as it abandons a
        room whose screen closed - re-hosting would attach a fresh simulation
        to a match already twenty minutes old, with the clock at zero and the
        arena's log saying 2-1.
        """
        self._spare.pop(socket, None)
        self._load.pop(socket, None)
        for code in [code for code, held in self._where.items() if held is socket]:
            self._where.pop(code, None)

    def assign(self, code):
        """Give this match to the emptiest instance with room, or say nobody has.

        A room already assigned answers yes without spending a second slot.
        Kick-off is a button and a button gets double-tapped, and the arena
        would otherwise fill up with an instance's own duplicates.
        """
        if code in self._where:
            return True
        free = [socket for socket in self._spare
                if self._load[socket] < self._spare[socket]]
        if not free:
            return False
        # Spread rather than pack. Two instances at half load ride out a burst
        # that one at full load and one idle would not, and the cost of an
        # empty slot on a warm instance is nothing.
        chosen = min(free, key=lambda socket: self._load[socket])
        self._where[code] = chosen
        self._load[chosen] += 1
        return True

    def release(self, code):
        """Forget an assignment and hand back the socket that had it, if any."""
        socket = self._where.pop(code, None)
        if socket is not None and socket in self._load:
            self._load[socket] = max(0, self._load[socket] - 1)
        return socket

    def socket_for(self, code):
        return self._where.get(code)

    def capacity(self):
        """Matches the connected instances between them said they would take."""
        return sum(self._spare.values())

    def running(self):
        """Matches assigned right now, across every instance."""
        return len(self._where)

    def connected(self):
        return len(self._spare)

"""What one address may ask for, and how many matches a venue may hold.

Two unauthenticated endpoints create rows: a player and a room. On a laptop
that is fine. On a URL anybody can find it is an invitation, and the cost of
saying no is a dictionary.

In memory, because there is one instance by design. If that ever stops being
true this is one of the four things that has to move, and the others are the
bus, host liveness and the chain's semaphore.
"""

import ipaddress
import time

# An address with nothing to say for this long is dropped rather than kept.
# A venue's phones over an evening should not become a dict that only grows.
IDLE_SECONDS = 3600

# How often that dropping is worth walking the dict for. Sweeping on every
# request is a full scan per call, on the path a flood hits hardest, to reclaim
# entries that took an hour to become worth reclaiming.
SWEEP_SECONDS = 60


class Bucket:
    """A token bucket per key. `burst` at once, refilling at `rate` a second."""

    def __init__(self, rate, burst):
        self.rate = rate
        self.burst = burst
        self._seen = {}
        self._swept = None

    def take(self, key, now=None):
        """Spend one token for `key`. False if there was not one to spend."""
        now = time.monotonic() if now is None else now
        self._forget(now)
        tokens, last = self._seen.get(key, (float(self.burst), now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens < 1.0:
            self._seen[key] = (tokens, now)
            return False
        self._seen[key] = (tokens - 1.0, now)
        return True

    def _forget(self, now):
        # Rate-limited itself, for the same reason everything else here is: the
        # scan costs a pass over every key that has spoken this hour, and the
        # calls it would run on are the ones already arriving too fast.
        if self._swept is not None and now - self._swept < SWEEP_SECONDS:
            return
        self._swept = now
        for key in [key for key, (_, last) in self._seen.items()
                    if now - last > IDLE_SECONDS]:
            del self._seen[key]


def client_ip(request):
    """The caller's address as far as it can be known behind a proxy.

    Cloud Run appends the real client to X-Forwarded-For and everything before
    it is whatever the client claimed, so the last entry is the only one worth
    reading. Falling back to the socket is for running without a proxy.

    This is a topology assumption. It is right for a direct *.run.app service
    (this deployment), and wrong behind a Google external load balancer, which
    appends its own address after the client's. There the last entry is the
    balancer and every caller in the world shares one bucket.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        last_entry = forwarded.split(",")[-1].strip()
        # If the last entry does not parse as an IP, fall back to the socket
        # rather than trusting it as a key.
        try:
            ipaddress.ip_address(last_entry)
            return last_entry
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"

# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The supervisor drives a page and never touches a match.

Everything about football is in the page. This decides which rooms this
instance runs, which is bookkeeping, and it is tested against a fake page with
no Chromium anywhere.
"""

import pytest

from supervisor import Supervisor


class FakePage:
    """Somewhere to evaluate JavaScript that remembers what it was asked.

    `playing` is what `window.grounds.running()` would answer. Tests set it to
    say what the page has actually got, which is not always what the supervisor
    believes: a match that reached full time lets itself go.
    """

    def __init__(self, playing=None):
        self.calls = []
        self.playing = playing

    async def evaluate(self, expression, arg=None):
        self.calls.append((expression, arg))
        if "running()" in expression:
            return self.playing
        return None


@pytest.fixture
def page():
    return FakePage()


def an_assignment(code="AAAA", token="t", seed=None):
    return {"type": "host", "code": code, "token": token, "seed": seed or f"{code}-1"}


def test_it_announces_its_capacity(page):
    supervisor = Supervisor(page, capacity=12)

    assert supervisor.hello() == {"type": "grounds.here", "capacity": 12}


async def test_a_host_message_starts_a_match(page):
    supervisor = Supervisor(page, capacity=4)

    await supervisor.apply(an_assignment())

    assert supervisor.running == {"AAAA"}
    expression, arg = page.calls[-1]
    assert "window.grounds.host" in expression
    assert arg == {"code": "AAAA", "token": "t", "seed": "AAAA-1"}


async def test_a_drop_message_stops_one(page):
    supervisor = Supervisor(page, capacity=4)
    await supervisor.apply(an_assignment())

    await supervisor.apply({"type": "drop", "code": "AAAA"})

    assert supervisor.running == set()
    expression, arg = page.calls[-1]
    assert "window.grounds.drop" in expression
    assert arg == {"code": "AAAA"}


async def test_the_same_room_twice_is_hosted_once(page):
    """The arena is idempotent about this and so is the page. So is this."""
    supervisor = Supervisor(page, capacity=4)

    await supervisor.apply(an_assignment())
    await supervisor.apply(an_assignment())

    assert supervisor.running == {"AAAA"}
    assert len([call for call in page.calls if "host" in call[0]]) == 1


async def test_it_refuses_past_its_capacity(page):
    supervisor = Supervisor(page, capacity=1)

    await supervisor.apply(an_assignment("AAAA"))
    await supervisor.apply(an_assignment("BBBB"))

    assert supervisor.running == {"AAAA"}


async def test_a_message_it_does_not_know_is_ignored(page):
    supervisor = Supervisor(page, capacity=4)

    await supervisor.apply({"type": "sing", "code": "AAAA"})

    assert supervisor.running == set()
    assert page.calls == []


async def test_a_message_that_is_not_a_message_is_ignored(page):
    supervisor = Supervisor(page, capacity=4)

    await supervisor.apply("host AAAA please")
    await supervisor.apply({"type": "host"})

    assert supervisor.running == set()
    assert page.calls == []


async def test_a_token_never_appears_in_the_expression(page):
    """The page is driven with an argument, not an interpolated string.

    A token spliced into JavaScript is a token in a stack trace, in a CDP log,
    and in anything that ever records what was evaluated.
    """
    supervisor = Supervisor(page, capacity=4)

    await supervisor.apply(an_assignment(token="secret"))

    expression, _ = page.calls[-1]
    assert "secret" not in expression


async def test_a_room_the_page_could_not_take_is_not_counted(page):
    """A page that threw is a slot this instance would hold and never use."""

    async def refuse(expression, arg=None):
        raise RuntimeError("the page is not having it")

    page.evaluate = refuse
    supervisor = Supervisor(page, capacity=4)

    await supervisor.apply(an_assignment())

    assert supervisor.running == set()


async def test_it_forgets_everything_when_the_page_is_reloaded(page):
    """Not re-hosted.

    A match twenty minutes old cannot be resumed by a fresh simulation with the
    clock at zero, and the arena's sweep is already the right answer to a match
    nobody is playing.
    """
    supervisor = Supervisor(page, capacity=4)
    await supervisor.apply(an_assignment())

    supervisor.page_reloaded()

    assert supervisor.running == set()


# Full time is the ending nobody sends a message about. The page blows the
# whistle, the arena closes the room on it and answers nothing, because it
# knows the page knows. Without the sweep below, an instance's list would only
# ever grow, and after `capacity` matches it would stop taking any.


async def test_a_match_that_reached_full_time_stops_being_counted(page):
    supervisor = Supervisor(page, capacity=4)
    await supervisor.apply(an_assignment("AAAA"))
    await supervisor.apply(an_assignment("BBBB"))

    page.playing = ["BBBB"]
    await supervisor.reconcile()

    assert supervisor.running == {"BBBB"}


async def test_reconciling_never_invents_a_match(page):
    """The page is the authority on what is playing, not on what was assigned.

    Adding here would let a page that answered oddly talk this instance into
    holding slots the arena never gave it.
    """
    supervisor = Supervisor(page, capacity=4)
    page.playing = ["AAAA", "BBBB"]

    await supervisor.reconcile()

    assert supervisor.running == set()


async def test_a_page_that_answers_nothing_changes_nothing(page):
    """A reload lands here before `page_reloaded` does, and one bad answer must
    not be read as "the venue stopped playing football"."""
    supervisor = Supervisor(page, capacity=4)
    await supervisor.apply(an_assignment())

    page.playing = None
    await supervisor.reconcile()

    assert supervisor.running == {"AAAA"}

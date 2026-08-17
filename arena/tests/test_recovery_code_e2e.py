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

"""Recovery code E2E regression tests for silent failures on /register.

Both blocking findings in the review were silent failures - no banner, no error,
nothing in the console. The button simply re-enabled. The offline tests pass
through the API and cannot see a page that swallows a refusal, which is why the
bugs shipped past a full green suite and were only caught in a browser.

These tests pin the journey E1 exists to protect: a returning manager on a fresh
phone getting through /register with their recovery code.
"""

import pytest

pytestmark = pytest.mark.e2e

# An iPhone 14, which is the handset the phone pages are drawn for.
HANDSET = {"width": 390, "height": 844}


async def register_and_capture_code(page, server, name, email):
    """Register a manager and return their player ID and recovery code."""
    await page.goto(f"{server}/register")
    await page.fill("#name", name)
    await page.fill("#email", email)

    # Handle alerts (old version) by dismissing them
    page.once("dialog", lambda dialog: dialog.dismiss())

    await page.click("#done")

    # Wait to land on /home (either directly or via code display page)
    await page.wait_for_function(
        "() => document.querySelector('.code-large') || window.location.pathname === '/home'",
        timeout=10_000
    )

    # If they got a code display page (new version), capture it
    code_element = await page.query_selector(".code-large")
    if code_element:
        code = await code_element.text_content()
        await page.click("a[href='/home']")
        await page.wait_for_url(f"{server}/home", timeout=10_000)
    else:
        # Old version (alert) or no email - get code from API
        await page.wait_for_url(f"{server}/home", timeout=10_000)
        me_response = await page.request.get(f"{server}/api/players/me")
        me = await me_response.json()
        code = me.get("recovery_code")

    # Get the player ID from /api/players/me
    me_response = await page.request.get(f"{server}/api/players/me")
    me = await me_response.json()
    player_id = me["id"]

    return player_id, code


@pytest.fixture
async def phone(real_arena_server):
    """A handset in front of the venue."""
    from playwright.async_api import async_playwright

    async with async_playwright() as driving:
        browser = await driving.chromium.launch()
        page = await browser.new_page(viewport=HANDSET)
        complaints = []
        page.on("pageerror", lambda blew_up: complaints.append(str(blew_up)))
        page.on("console",
                lambda note: complaints.append(note.text)
                if note.type == "error" and not note.text.startswith("Failed to load")
                else None)

        page.arena = real_arena_server
        yield page
        await browser.close()
    assert not complaints, f"the phone logged errors: {complaints}"


async def test_a_returning_manager_gets_through_register_on_a_fresh_phone(phone):
    """The journey E1 exists to protect: same name, same address, with the code.

    This was BLOCKING 1 - /register had no recovery-code box, so the refusal was
    silently swallowed. The page looked identical before and after tapping the
    button. A returning manager could not get in.
    """
    # Register once, capture the code
    player_id, code = await register_and_capture_code(
        phone, phone.arena, "Alex Rivera", "alex@example.com"
    )
    assert code is not None, "should have gotten a recovery code"

    # Clear cookies - fresh phone
    await phone.context.clear_cookies()

    # Go to /register, type the same name and address, enter the code
    await phone.goto(f"{phone.arena}/register")
    await phone.fill("#name", "Alex Rivera")
    await phone.fill("#email", "alex@example.com")
    await phone.fill("#recovery-code", code)
    await phone.click("#done")

    # Should land on home (the code display or direct to home)
    await phone.wait_for_function(
        "() => window.location.pathname === '/home' || document.querySelector('.code-large')",
        timeout=10_000
    )
    # If code display, click through
    if await phone.query_selector(".code-large"):
        await phone.click("a[href='/home']")
    await phone.wait_for_url(f"{phone.arena}/home", timeout=10_000)

    # Assert they are the SAME player, not a second row and not renamed
    me_response = await phone.request.get(f"{phone.arena}/api/players/me")
    me = await me_response.json()
    assert me["id"] == player_id, "should be the same player row"
    assert me["display_name"] == "Alex Rivera", "should not have been renamed"


async def test_a_stranger_without_the_code_is_told_so_visibly_on_register(phone):
    """A claim without the code shows the refusal, located at the recovery box.

    This was BLOCKING 1 - the refusal was silently swallowed. No banner, no hint,
    nothing in the console. The old behaviour would pass any test that only checked
    "did not get in", so we assert on what the person actually sees.
    """
    # Register once to claim the address
    _, code = await register_and_capture_code(
        phone, phone.arena, "Alex Rivera", "alex@example.com"
    )
    assert code is not None

    # Clear cookies - attacker's phone
    await phone.context.clear_cookies()

    # Try to claim without the code
    await phone.goto(f"{phone.arena}/register")
    await phone.fill("#name", "Alex Rivera")
    await phone.fill("#email", "alex@example.com")
    # Leave recovery-code empty
    await phone.click("#done")

    # Wait a moment for any response
    await phone.wait_for_timeout(1000)

    # Assert the refusal is SHOWN and located at the recovery box, not the name box
    recovery_hint = await phone.query_selector("#recovery-hint")
    assert recovery_hint is not None, "recovery hint element should exist"
    is_visible = await recovery_hint.is_visible()
    assert is_visible, "recovery hint should be visible"

    hint_text = await recovery_hint.text_content()
    assert "recovery code" in hint_text.lower(), f"hint should mention recovery code: {hint_text}"

    # Assert the page did not navigate
    assert phone.url == f"{phone.arena}/register", "should still be on /register"


async def test_a_taken_name_does_not_disable_button_with_code_present(phone):
    """Recovery code in the box unblocks the button for a taken name.

    This was BLOCKING 2 - a returning manager typing their own name saw it as taken
    and the button disabled. They could not type their name because the availability
    check said "taken" before they could enter their code.
    """
    # Register to claim the name
    _, code = await register_and_capture_code(
        phone, phone.arena, "Alex Rivera", "alex@example.com"
    )
    assert code is not None

    # Clear cookies
    await phone.context.clear_cookies()

    # Go to /register and type the name - it will be marked as taken
    await phone.goto(f"{phone.arena}/register")
    await phone.fill("#name", "Alex Rivera")

    # Wait for the availability check to say "taken"
    await phone.wait_for_selector("#name-hint:not([hidden])", timeout=5_000)
    name_hint_text = await phone.text_content("#name-hint")
    assert "taken" in name_hint_text.lower(), "name should be marked as taken"

    # The button should be disabled with just a taken name
    button = await phone.query_selector("#done")
    is_disabled = await button.is_disabled()
    assert is_disabled, "button should be disabled for taken name alone"

    # Fill in the email
    await phone.fill("#email", "alex@example.com")

    # Now type something in the recovery code box
    await phone.fill("#recovery-code", "AB34KP")  # Wrong code, but has content

    # Wait a moment for the button to update
    await phone.wait_for_timeout(500)

    # The button should now be enabled
    is_disabled = await button.is_disabled()
    assert not is_disabled, "button should be enabled with taken name + recovery code content"

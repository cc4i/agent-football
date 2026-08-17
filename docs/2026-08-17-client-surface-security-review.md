# Client surface security review

**Date:** 2026-08-17
**Scope:** every surface an untrusted client can reach - the arena's HTTP API,
its WebSockets, its five static pages, the pitch bundle the arena serves at
`/pitch`, the grounds' one route, the dugout's local server, and the two agent
proxy routes in front of the coach.
**Deployment assumed:** `deploy/service.yaml` + `deploy/README.md:352`, i.e. the
arena is a public Cloud Run service with `allUsers` bound to `roles/run.invoker`.
The coach and captain are loopback sidecars with no published port; the dugout
is not deployed.

Seven findings: two high, two medium, three low. The two high ones are both
authorisation gaps rather than injection bugs - the injection surfaces are, on
the whole, in good shape (see "What was checked and found sound").

---

## Vuln 1 - Authorisation bypass: the public agent proxy writes to any room's squad with the service token

* **Severity: HIGH** (confidence: high - the exploit path is a URL)
* **Category:** `authorization_bypass` / `broken_access_control`
* **Where:**
  * `arena/proxy.py:84` and `arena/proxy.py:109` - the two proxied routes
  * `arena/app.py:369` - mounted on the public app, no dependency, no session
  * `game/agents/specialist_agents/tools.py:41-48` and `:243-246` - the tools read the target room from session state
  * `game/frontend/src/main.js:426-445` - the pitch builds that state from its own URL
  * `game/frontend/src/arena.js:38-39` - the URL is `?room=` and `?team=`
  * `arena/app.py:1414-1423` - the check this walks around

### Description

The arena proxies exactly two ADK paths so the pitch's own coach bar keeps
working. Both are unauthenticated, and the request body is forwarded verbatim:

```python
# arena/proxy.py:95
raw = await _body(request)
reply = await http.post(coach.session_path(user), content=raw, ...)
```

The body of the session-create call is `{"state": {...}}`, and `state` is what
the whole chain reads its target from. `update_profile` and
`restore_baseline_profiles` take the room and the dugout from there:

```python
# game/agents/specialist_agents/tools.py:41
room = tool_context.state.get("room_code") or arena_client.DEFAULT_ROOM
team = tool_context.state.get("team") or arena_client.DEFAULT_TEAM
```

and then write with `X-Arena-Service` (`arena_client.py:78`), which
`_require_profile_writer` accepts unconditionally. The seat check that governs
every other write to a dugout is never reached.

`carry_the_dugout` / `take_the_dugout` (`agent.py:36`, `captain_server.py:44`)
carry that state across the A2A hop, so all four specialists inherit the
attacker's room.

The proxy's own docstring anticipates half of this - *"An open proxy in front of
an unauthenticated ADK server is a free language model for whoever finds it"* -
and answers it by narrowing the path allowlist. The two paths left on the
allowlist are the two that matter.

### Exploit scenario

No crafted request is needed. The lab page is part of the bundle the arena
serves publicly (`arena/app.py:2410-2419`), and its shout box does all of this
already:

1. Read any live room code off `GET /api/rooms/open`, or off the big screen.
2. Open `https://<arena>/pitch/?room=<CODE>&team=<side>`.
3. Type into "Coach Shouts" and press send.

`main.js:437` puts `room_code: <CODE>, team: <side>` into the session state, the
chain runs, and four specialists rewrite that dugout's attributes. `triggerShout`
calls `sendInstructionToAgent` unconditionally, so the local Phaser game does not
even have to be started.

Three concrete outcomes:

* **Sabotage.** Wreck a stranger's squad mid-match from a phone that holds no
  session and no seat - the keeper pushed up the pitch, the forward slowed.
* **Leaderboard fraud.** In your *own* solo match, target `team=red`. Red is the
  house side, has no seat owner, and is therefore unreachable through
  `PATCH .../profiles/{role}` (403). Through this route it is writable. Slowing
  it is measured in `arena/sabotage.py:10-13` as 5-1-0 with no losses, and the
  match stays `ranked` - only a non-1.0 speed unranks a room
  (`arena/app.py:1995`). This is the graded, thresholded "quiet word" feature
  handed over unmetered.
* **Free inference.** `/run_sse` streams the coach's output back to the caller
  on the venue's Vertex billing, with no session of any kind.

`POST /run_sse` with `newMessage.parts[0].text == "RESTORE_BASELINE"` is the
cheapest version: the coach calls `restore_baseline_profiles` itself
(`agent.py:69`), one model hop, resetting any dugout in the venue.

### Recommendation

The room a chain acts on must be decided by the arena, never by the caller -
which is exactly what `POST /api/rooms/{code}/shout` already does
(`app.py:1186`, `chain.py:186`). Bring the proxy up to the same rule:

1. In `proxy.open_session`, parse the body and **replace** `state` with a
   server-chosen value rather than forwarding the caller's. The pitch's coach
   bar only ever needs the workshop, so pin `{"room_code": codes.WORKSHOP,
   "team": "blue"}` and drop everything else the caller sent.
2. Refuse `room_code` values other than `codes.WORKSHOP` outright, so the
   failure is a 403 rather than a silent redirection.
3. Have `arena_client._send` name the room it was authorised for in a header the
   arena cross-checks against the path, so a specialist that somehow acquires a
   different room cannot spend the service token on it.
4. Longer term, drop `?room=`/`?team=` from `game/frontend/src/arena.js` for the
   deployed bundle. The lab is the workshop; a venue's rooms are reached through
   `/play`, which checks a seat.

---

## Vuln 2 - Authentication bypass: an unverified email address is a login

* **Severity: HIGH** (confidence: high)
* **Category:** `authentication_bypass`
* **Where:** `arena/rooms.py:53-55` (`upsert_player`), `arena/app.py:712-736`
  (`POST /api/players`), `arena/board.py:207-231` (the masked address the board
  publishes)

### Description

`POST /api/players` is unauthenticated and resolves identity by email first:

```python
# arena/rooms.py:53
mine = _player_by_email(conn, email_hash)
if mine is None:
    mine = player_id           # only then the cookie
```

and the route then signs a session cookie for whatever id came back
(`app.py:727`). Nothing verifies that the caller controls the address. The
behaviour is deliberate and tested - `test_an_address_brings_a_manager_back_on_a_phone_with_no_cookie`
and `test_an_address_outranks_a_cookie_somebody_else_left_on_the_phone` -
which is the point: the address is treated as an authenticator, and it is not
one.

The address is also partly published. `GET /api/board` is unauthenticated and
returns `email_masked` for every ranked manager: `a***x@example.com` keeps the
first character, the last character and the whole domain. Against a workshop
roster on one corporate domain and a real name shown beside it, that is usually
enough to reconstruct the address; and at an event, attendees know each other's
addresses anyway.

Note the limit, which is real: a manager who registered **without** an address
cannot be taken over this way - the name-uniqueness check refuses
(`rooms.py:57-62`). Only address-registered managers are affected.

### Exploit scenario

1. Read `GET /api/board`: `Alex Rivera`, `a***x@acme.com`.
2. `POST /api/players` with `{"display_name": "Alex Rivera", "email": "alex@acme.com"}`.
3. The response sets a valid `arena_session` cookie for Alex's player id.

From there the attacker is Alex: `GET /api/players/me` gives Alex's live room,
`/play?room=...` gives Alex's dugout, shouts land in Alex's name in the log and
on the big screen, and results are written against Alex's board entry. Passing
a *different* `display_name` in step 2 also renames Alex on the board, since
`_write_player` updates the row.

### Recommendation

Stop treating the address as proof. Options, in preference order:

1. **Drop the email lookup.** It exists only to carry one manager across two
   phones. A one-time code shown on the first phone and typed on the second
   does the same job with a secret the venue actually issued.
2. If the lookup stays, only let an address *claim* a row when the caller
   already holds that row's session, and let it *create* a new row otherwise -
   i.e. reverse the precedence at `rooms.py:53` so the cookie wins and the
   address is only ever a hint.
3. Stop publishing `email_masked` on `/api/board` and in the room snapshot. It
   is drawn under a name that already identifies the manager, so it adds nothing
   a reader needs and narrows the guess for whoever is attacking it.

---

## Vuln 3 - The local coach server is open to every website the developer visits

* **Severity: MEDIUM** (confidence: high; local-development only)
* **Category:** `cors_misconfiguration`
* **Where:** `game/run.sh:84`

```bash
uv run adk web . --allow_origins='*' &
```

### Description

The ADK web server has no authentication of any kind - `game/Dockerfile.coach:1`
says so - and this hands it a wildcard CORS policy on `http://localhost:8000`.
Any page loaded in the workshop participant's browser can then read and write it
cross-origin for as long as the stack is up.

That server holds the same tools Vuln 1 abuses, and the local `.env` gives it
`ARENA_SERVICE_TOKEN`, so a malicious page reaches the local arena's profile
writes through it. It can also read every ADK session, which is where shout text
and huddle output live.

### Exploit scenario

A participant follows the GUIDE, runs `game/run.sh`, and in another tab opens
any page carrying a hostile ad or script. That page does
`fetch('http://localhost:8000/apps/agents/users/x/sessions', {method:'POST',
body:'{"state":{"room_code":"WRKS","team":"blue"}}'})`, then `/run_sse`, and
reads the streamed response - rewriting the local squad and exfiltrating
everything in the session store. `--allow_origins='*'` is what makes the
response readable; without it the browser would block the read.

### Recommendation

Name the one origin that needs it: `--allow_origins='http://localhost:5173'`.
Bind the server to `127.0.0.1` in the local script as well - the wide bind in
`Dockerfile.coach` is justified there by the Cloud Run probe and does not apply
on a laptop.

---

## Vuln 4 - The physics token is a bearer credential carried in a URL query string

* **Severity: MEDIUM** (confidence: medium - needs read access to platform logs)
* **Category:** `credential_exposure`
* **Where:** `arena/app.py:1776` (`client_id` as a query parameter),
  `arena/app.py:146-163` (`_RedactClientId`), `arena/static/socket.js:14`,
  `game/frontend/src/arena.js:90`

### Description

`host_client_id` is the credential the arena is most careful with - minted at
`create_room`, sent to exactly one grounds over one authenticated socket, and
never in an HTTP response (`rooms.py:152-163`). It then travels as
`/ws/rooms/{code}?client_id=<token>`.

`_RedactClientId` scrubs it from uvicorn's access log, and its docstring names
the risk precisely: *"Anyone with the arena's stdout or a proxy log could seize
physics for any live match."* The filter covers the arena's own logger. It does
not cover Cloud Run's request log, which records the full request URL including
the query string, and which anyone with `roles/logging.viewer` on the project
can read.

Holding that token is more than replay. `_handle_from_host` (`app.py:1910`)
accepts `host.state` and `host.event` from it, and `host.event` appends
arbitrary `kind` and `payload` to the room's log - including `goal` and
`full_time`. Scoring is recomputed from that log (`board.record`), so the token
is sufficient to fabricate a scoreline and the board entry that follows it.

### Recommendation

Move the credential out of the URL. The WebSocket handshake is an HTTP request,
so a header works for the grounds (`grounds/main.py:125` already sends
`X-Arena-Service` that way); for the browser sockets, which cannot set headers,
send the token as the first message on the open socket and authenticate there
instead. Either way the token stops appearing in any URL anything logs.

---

## Vuln 5 - Session cookie is not marked `Secure`

* **Severity: LOW** (confidence: high; impact deployment-dependent)
* **Category:** `session_management`
* **Where:** `arena/app.py:727-732`

```python
response.set_cookie(COOKIE, identity.sign_token(...), httponly=True, samesite="lax")
```

`httponly` and `samesite` are both right. `secure` is missing, so nothing stops
the cookie being sent over cleartext HTTP.

On today's deployment the exposure is small: `*.run.app` sits under the `.app`
gTLD, which is HSTS-preloaded, so a browser will not make a cleartext request to
it at all. The gap opens the moment this is served on a custom domain, a tunnel,
or a venue LAN - all of which `ARENA_PUBLIC_URL` explicitly supports
(`app.py:267`) - where an attacker on the wifi can force one plaintext request
and read a session that never expires (see Vuln 7).

**Recommendation:** add `secure=True` when `PRODUCTION`, and `path="/"`
explicitly while you are there.

---

## Vuln 6 - No security response headers on any arena page

* **Severity: LOW** (defence in depth)
* **Category:** `missing_security_headers`
* **Where:** `arena/app.py` - `PutTheConnectionBack` is the only middleware
  (`app.py:420`); `_page()` sets `Cache-Control` and nothing else (`app.py:629`)

No `Content-Security-Policy`, no `X-Frame-Options` / `frame-ancestors`, no
`X-Content-Type-Options`, no `Referrer-Policy`.

Two consequences worth naming:

* **Clickjacking.** Any site can frame `/play?room=X` or `/arena` and overlay
  its own UI on top of "Kick off", "I'm ready", "New room" or the shout box.
  The session cookie is `SameSite=Lax`, which does not help here - a framed page
  is a first-party navigation as far as the cookie is concerned.
* **No second line of defence.** Every client file in this repo builds DOM with
  `textContent` and `createElement`, which is why there is no XSS finding in
  this report. A CSP is what keeps that true after the next change.

**Recommendation:** one small middleware setting, on every response:
`Content-Security-Policy: default-src 'self'; frame-ancestors 'self'; style-src
'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com;
img-src 'self' data:; object-src 'none'; base-uri 'none'`, plus
`X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`.
`frame-ancestors 'self'` rather than `none`: `/arena` frames `/board` on the
same origin (`arena.js:210`).

---

## Vuln 7 - Session tokens never expire and cannot be revoked

* **Severity: LOW**
* **Category:** `session_management`
* **Where:** `arena/identity.py:49-71`

```python
def sign_token(player_id, secret):
    body = str(player_id)
    return f"{body}.{_mac(body, secret)}"
```

The token is the player id plus an HMAC over the player id. There is no issue
time, no expiry, no nonce and no version, so it is a permanent bearer credential
for that player and it is identical every time it is minted. Nothing can revoke
one short of rotating `ARENA_SECRET`, which logs out the whole venue at once -
`app.py:198` calls that out as the reason the secret must be stable.

The HMAC itself is fine: 256-bit, constant-time compare, and careful about
non-ASCII cookies.

**Recommendation:** put an issue timestamp in the signed body and reject tokens
older than an event's length (a day is generous). If per-player revocation is
ever wanted, add a `token_version` column to `player` and sign it in too.

---

## What was checked and found sound

Recording this so the next reviewer does not repeat it.

* **SQL injection.** Every statement in `arena/db.py`, `rooms.py`, `profiles.py`
  and `board.py` is parameterised. The two f-strings in `board.py` interpolate
  the module constant `RANKED`, not input. `_ensure_database` uses
  `sql.Identifier`. NUL bytes - which psycopg refuses to bind and which would
  otherwise be a 500 on unauthenticated input - are turned away up front in
  `JoinRequest.bindable_as_text`, `_known_team`, `_known_role` and
  `name_available`.
* **XSS.** No `innerHTML` with interpolated data anywhere in
  `arena/static/*.js`, `dugout/static/chat.js` or the pitch bundle. The three
  `innerHTML` uses in `game/frontend/src/main.js` are static literals. Model
  output - specialist quips, huddle lines, `substitution.detail` - reaches the
  DOM through `textContent` in `relay.js:249` and `relay.js:109`, and the source
  comments say that is deliberate. Manager names likewise.
* **Path traversal.** `Playbook._load` (`playbook.py:36`) checks the name
  against a tuple before building a filename; `attributes.baseline_for` checks
  against `ROLES`; `proxy._USER_PATTERN` plus `coach.session_path`'s
  percent-encoding fence the one path segment a client controls.
* **Command / JS injection into the grounds.** `grounds/supervisor.py:31-33`
  passes the room code and physics token to `page.evaluate` as an argument
  object rather than interpolating them into the script, and says why.
* **Secret comparison.** `_same_secret` / `_service_token_ok`
  (`app.py:1426-1481`) compare bytes in constant time, handle latin-1 headers
  and surrogate-bearing environment values, and an unset token authenticates
  nobody.
* **Token entropy.** Room credentials are `secrets.token_urlsafe(16)`; room
  codes are `secrets.choice` over a 32-character alphabet.
* **Host-frame handling.** `_handle_from_host` re-checks the token against the
  current row on every message, bounds the payload, rejects non-string `kind`,
  bool-vs-int `match_ms`, out-of-range BIGINT and unencodable UTF-16
  surrogates, and appends server keys last so a host cannot forge `type` or
  another room's `code`.
* **Attribute validation.** `attributes.validate` allowlists names against the
  shipped baseline *and* the simulated set, rejects bools, and range-checks
  every value; a patch lands all of its values or none.
* **Committed secrets.** None. `.env` is gitignored; only `.env.example` files
  are tracked.
* **Prompt injection.** Bounded by design for the intended path: a shout's words
  reach the model, but the room and dugout are stamped by the arena
  (`chain.py:186`, `tools.py:145`) and `caused_by` is decided server-side
  (`app.py:1339`), so a manager cannot talk the chain into another match or
  claim a goal for a shout. Vuln 1 is what walks around that, and it does so by
  setting session state directly rather than by anything said to a model.
* **`X-Forwarded-For` handling.** `limits.client_ip` reads the last entry, which
  is correct for a direct `*.run.app` service and therefore not spoofable here.
  The load-balancer caveat is already documented in the source.
* **CSRF.** State-changing routes are POST/PATCH with a JSON body, and the
  cookie is `SameSite=Lax`, so a cross-site form cannot carry it and a
  cross-site `fetch` needs a preflight nothing answers. Same reasoning covers
  the dugout's unauthenticated `POST /chat` on localhost.

---

## Suggested order of work

1. **Vuln 1** - pin the session state server-side in `arena/proxy.py`. One
   function, and it closes the only path to unauthenticated writes.
2. **Vuln 2** - reverse the cookie/address precedence in `rooms.upsert_player`,
   and stop publishing masked addresses on the board.
3. **Vuln 3** - one flag in `game/run.sh`.
4. **Vulns 5 and 6** - `secure=True` and a headers middleware; both are a few
   lines and neither changes behaviour.
5. **Vulns 4 and 7** - larger, and each needs a protocol decision.

# Stopping the two ways to cheat the board: a design

Written 2026-08-17, against the findings in
[`2026-08-17-client-surface-security-review.md`](2026-08-17-client-surface-security-review.md).
Nothing here has been built.

Revised once after review. The two changes worth knowing if you read the first
draft: C1's allowlist stopped at the top level of the request body and let
`newMessage` through untouched, which was a hole rather than a nit; and E1's
recovery code moved from derived to stored, because derived could never be
reissued. Both are argued below.

Scope was cut deliberately, twice, and the cuts are recorded at the foot of
this document. What is left is the two findings that let somebody move a number
on a leaderboard they did not earn:

- **C1** - a stranger rewrites any squad in the venue through the coach proxy.
- **E1** - a stranger becomes another manager by typing their email address.

The constraint over all of it: **the deployed venue works, and this must not be
what breaks it.**

## What must not change

| Surface | Why it is safe |
|---|---|
| Every phone route - `/scan`, `/register`, `/join/{code}`, `/home`, `/play`, `/board` | C1 does not touch them. E1 adds one optional field to the two join forms and one line to `/home`. |
| The big screen - `/arena` | Untouched. |
| A phone's shout | `POST /api/rooms/{code}/shout` goes to `chain.py:187` → `coach.stream`, which dials the coach directly. It has never used the proxy. |
| The grounds | Untouched. No code change, no deploy coupling. |
| The pitch bundle | Neither change touches `game/frontend/`, so no `npm run build` and no new bundle. |
| Physics, scoring, the event log, the bus, the sweep | Untouched. |
| `deploy/*`, the four Dockerfiles | Untouched. |

---

# C1 - The room a chain writes to is the arena's to decide

## What is wrong

`arena/proxy.py:84` and `:109` are public, unauthenticated, and forward the
caller's body to the coach untouched. That body decides the ADK session's
state, and `game/agents/specialist_agents/tools.py:41` reads the room and the
dugout out of that state before writing with `X-Arena-Service` - which
`_require_profile_writer` (`arena/app.py:1414`) waves through. The seat check
that governs every other write to a dugout is never reached.

The reachable form is a URL. The lab page ships in the bundle the arena serves
at `/pitch`, its coach bar is visible on load, and
`game/frontend/src/main.js:437` builds the session state from `?room=` and
`?team=`.

The cheat that follows: in your own solo match, target `team=red` - the house
side, which has no seat owner and is therefore refused a normal PATCH - and
slow it. `arena/sabotage.py:10` measures that as five wins in six with no
losses. The room stays `ranked`, because only a non-1.0 speed unranks one.

## What the body can do, read out of the installed ADK

Four things, not one. **Patching `state` alone would look like a fix and leave
three doors open.**

| # | Field | Where it lands | What it buys an attacker |
|---|---|---|---|
| 1 | `state` on create-session | `api_server.py:397` | names the room |
| 2 | `events[].actions.state_delta` on create-session | `api_server.py:400`, applied by `append_event` at `:1123` | names the room |
| 3 | `stateDelta` on `/run_sse` | `api_server.py:382`, applied at `runners.py:1496` | names the room |
| 4 | `newMessage.parts[]` on `/run_sse` | `api_server.py:380`, typed `types.Content` | see below |

`main.js:466` already sends `stateDelta: null`, so vector 3 is live on the wire
today.

**Vector 4 is not about the room at all**, which is why the first draft missed
it. A `Part` is not a string. Read from the installed `google.genai`:

```
['code_execution_result', 'executable_code', 'file_data', 'function_call',
 'function_response', 'inline_data', 'media_resolution', 'part_metadata',
 'text', 'thought', 'thought_signature', 'tool_call', 'tool_response',
 'video_metadata']

FileData fields: ['display_name', 'file_uri', 'mime_type']
```

`fileData.fileUri` is fetched by Vertex using **the caller's** credentials,
which here are the instance's service account. So an unauthenticated caller can
name a GCS object, have the model read it, and read the answer back off the
`/run_sse` stream that this proxy politely relays to them. `functionResponse`
forges a tool result into the conversation. Neither has anything to do with
shouting at a squad, and neither is stopped by pinning the room.

## The rule

The two proxied routes stop **forwarding** the caller's body and start
**rebuilding** it from an allowlist - and the allowlist goes all the way down,
not one level. An allowlist rather than a patch, because the table above is the
ADK's shape today and a version bump can lengthen it.

**`POST /api-apps/agents/users/{user}/sessions`** forwards exactly:

```python
{"state": {"room_code": codes.WORKSHOP,
           "team": "red" if caller_said_red else "blue"}}
```

`events` gone. `session_id` gone - the ADK generates one, and dropping it
removes any question of colliding with the session `chain.py` opens for a real
room. Every other `state` key gone, `__session_metadata__` included.

**`POST /run_sse`** forwards exactly `appName`, `userId`, `sessionId`,
`newMessage` and `streaming`, where:

- `appName` is pinned to `coach.COACH_APP` and `userId` to a new `LAB_USER`
  constant. Both routes use the same constant, so the create call's path
  segment and the run call's body agree by construction, and no caller names a
  user bucket. This also puts the lab's sessions in a different bucket from the
  arena's own chain, which uses `coach.COACH_USER = "arena"`.
- `sessionId` passes through - it is how a caller names the session they just
  created, and ADK generates it as a uuid4, so it is not guessable.
- `newMessage` is **rebuilt, not forwarded**: `{"role": "user", "parts":
  [{"text": ...}]}`, keeping only parts that carry a non-empty string `text`,
  at most 8 of them, each capped at 2000 characters. Every other part kind is
  dropped. The lab sends one text part (`main.js:464`), so this costs it
  nothing.
- `streaming` is coerced to a bool.
- `stateDelta`, `functionCallEventId` and `invocationId` are dropped.

Unknown fields are dropped silently rather than refused: `main.js` sends
`stateDelta: null` today and a 400 would break the lab page. A caller sending a
**non-null** state-writing field, or a non-text part, gets a `logger.warning`,
so abuse is visible without being fatal.

`_USER_PATTERN` and `coach.session_path`'s encoding stay. With the user pinned
they are no longer the fence, but a clear 400 on a strange segment beats
silently ignoring it, and their tests keep their meaning.

## Why this is sufficient for the cheat

`WRKS` is created with `ranked = 0` (`rooms.py:167`) and `board.RANKED`
requires `ranked = 1`. After C1, **nothing reachable through the proxy can
touch either board.** The cheat stops existing rather than being fenced off.

`team` stays honoured within the workshop, because both its dugouts are
unranked and belong to nobody, and the lab reads both squads
(`game/frontend/src/arena.js:39`).

## What C1 leaves behind, stated so the deferral is informed

Everything reachable through the proxy now lands on the one shared workshop
room. Two consequences, neither of which touches a board:

- **The workshop is grief-able.** A stranger can rewrite the squad under a live
  five-stage demo running against the deployed arena.
- **`WRKS` accumulates rows.** It never ends, so every write appends to `event`
  forever. `MAX_REPLAY_EVENTS` caps what is read back; nothing caps what is
  stored.

Both are closed by the deferred lab perimeter, not by C1.

## Cost

Zero for the lab: with no `?room=` it already targets `WRKS`, so this matches
current behaviour exactly. The only thing that stops working is
`/pitch/?room=<somebody else's match>`, which is the vulnerability.

## Files

`arena/proxy.py`, `arena/tests/test_proxy.py`. Nothing else.

---

# E1 - An address stops being a login

## What is wrong

`arena/rooms.py:53` resolves identity by address before the cookie:

```python
mine = _player_by_email(conn, email_hash)
if mine is None:
    mine = player_id
```

and `app.py:727` then signs a session for whatever row came back. Nothing
verifies the caller controls the address. `arena/proxy.py:53` already says the
consequence out loud: *"`POST /api/players` hands out sessions to anybody who
asks."*

`GET /api/board` publishes `a***x@acme.com` beside the manager's real name,
which on a one-domain workshop roster is usually enough to reconstruct.

## Why it is a cheating finding and not only an identity one

On the solo board only the best run counts, so a hijacker can improve somebody
else's entry but not spoil it. The versus board is different: `board.versus`
accumulates played, won, drew, lost and goal difference across **every** result,
and `_new_ratings` moves Elo on each. So playing badly as somebody else
degrades their record and their rating permanently. Add renaming their board
entry, and hijacking a live seat mid-match.

## The rule

An address may still bring a manager back to their own row. It may no longer do
so on its own.

**A recovery code is required whenever the address resolves to a row the
caller's cookie does not already name.** That one sentence is the whole change,
and it leaves the common paths untouched:

| Case | Cookie | Address resolves to | Code needed |
|---|---|---|---|
| First registration | none | nothing | no - a row is created |
| Same phone, playing again | their row | their row | **no** - the cookie already names it |
| Returning on a new phone | none | their row | **yes** |
| Borrowed phone holding somebody else's session | Sam's row | Alex's row | **yes** - and with it, Alex still wins, as `test_an_address_outranks_a_cookie...` requires |
| Attacker with a known address | none or their own | the victim's row | **yes, and they do not have it** |

## The code: stored, not derived

Six characters from `codes.ALPHABET`, drawn with `secrets.choice`, generated at
insert and kept in a `recovery_code` column on `player`.

```python
# identity.py
RECOVERY_LENGTH = 6

def new_recovery_code():
    """Six characters that prove an address is yours. Read across a room, typed on a phone."""
    return "".join(secrets.choice(codes.ALPHABET) for _ in range(RECOVERY_LENGTH))
```

### Why stored, having first designed it derived

The first draft derived the code as `HMAC(EMAIL_SALT, "recovery:" + email)`,
which needs no column and no migration. That is the wrong trade, for two
reasons the first draft did not state:

- **A derived code can never be reissued.** It is shown on `/home`, on a phone
  screen, in a crowded room. Shoulder-surfed, it is that manager's code forever
  and across every event sharing the salt. A column can be regenerated with an
  `UPDATE`.
- **It inherits `EMAIL_SALT`, which has a public default outside production.**
  `app.py:190` falls back to the literal `"arena-dev-salt"`. The Dockerfile sets
  `ARENA_ENV=production` so containers are safe, but this repo explicitly
  supports a laptop venue behind a tunnel (`app.py:267`). On one of those, every
  recovery code in the building is computable from the repository.

And the argument for derived was weaker than it looked. `db.MIGRATIONS` already
runs four `ALTER TABLE ... IF NOT EXISTS` statements on every boot, and
`_pull_apart_shared_names` is already a boot-time per-row backfill. A column is
one line in each.

### Schema and backfill

```sql
ALTER TABLE player ADD COLUMN IF NOT EXISTS recovery_code TEXT;
```

Backfilled in `init_db` beside `_pull_apart_shared_names`, whose shape it
copies: select the rows where `recovery_code IS NULL AND email_hash IS NOT
NULL`, generate one each, update. Only rows with an address, because a row that
no address resolves to can never be claimed and needs no code.

### Stored in the clear, deliberately

It has to be displayed to its owner on `/home`, so it cannot be hashed. That is
the right call for what it guards - one board entry at a one-day event, in a
private database - and it is recorded here rather than left for a reviewer to
wonder about. It is not a password and must never grow into one.

### No regenerate endpoint in this change

The point of a column is that reissuing is *possible*; shipping the UI for it
is scope this change does not need. An operator reissues with one `UPDATE`. One
route can add it later.

### Guessing

32^6 is about 1.07 x 10^9. `POST /api/players` is already rate-limited by
`PLAYER_RATE, PLAYER_BURST = 1.0, 120` (`app.py:90`), keyed on the **client IP**
- and `app.py:77` notes a venue behind one NAT is a single bucket, so a
brute-forcer is spending the same budget the room's legitimate joins need.
Neither direction matters at 10^9, but the bucket being shared is worth knowing.

## The API contract

`JoinRequest` gains:

```python
recovery_code: str = Field(default="", max_length=identity.RECOVERY_LENGTH)
```

Normalised by a validator: stripped and upper-cased, because people type
lower-case. Then either empty, or exactly `RECOVERY_LENGTH` characters all in
`codes.ALPHABET`. The alphabet check also refuses a NUL, so it needs no separate
guard the way `display_name` does.

### Where the check goes, which is not arbitrary

It runs **before** the name-clash check, because it decides `mine`, and the
name check's outcome depends on `mine`:

```python
by_address = _player_by_email(conn, email_hash)
if by_address is not None and by_address != player_id:
    # A claim: the address names a row this phone's cookie does not.
    if not _code_matches(conn, by_address, recovery_code):
        raise NeedsRecoveryCode(...)
mine = by_address if by_address is not None else player_id
# ... the existing name_holder check, unchanged
```

Put after the name check, a wrong code would first be compared against a row
the caller is not going to get, and the refusal would be about the wrong thing.

`_code_matches` compares with `hmac.compare_digest` on bytes, like every other
secret in the arena, and a row with a NULL code matches nothing.

### The refusal, and how the form knows where to put it

`_rules()` maps every `RoomError` to a 409 with a string body, and
`signup.js:101` routes every 409 to the name box. A code refusal needs to land
under the code box instead, so it answers in the located shape the arena
already uses for `profiles.Rejected` (`app.py:1332`), with one key added:

```json
{"detail": {"problems": ["that address is already registered here - enter its recovery code, or pick a different name"],
            "field": "recovery_code"}}
```

`api.js:58` already reads `detail.problems` for the sentence. `api.js:70`
(`blamed`) learns to read `detail.field` as well as pydantic's `loc`, which is
the only client change needed to route it.

## Where a manager sees it

- Once, when they register: the `POST /api/players` response carries it and
  `/register` shows it before sending them on.
- Any time, on `/home`, which needs their session - so only the owner sees it.
  `GET /api/players/me` grows the field.

It is only useful alongside the address, so showing it to its owner leaks
nothing.

## The second half: the board stops publishing addresses

`board._run` and `board._standing` drop `email`; `rooms.snapshot` drops it from
each seat; `board.js` loses `masked()` and its two call sites.

Smaller now that E1 exists - the address is no longer a credential - but it is
still personal data on an unauthenticated endpoint, it is the thing that tells
an attacker which address to try, and it sits under a name that already
identifies the manager. It buys a reader nothing.

Consequence: `join.html:52` and `register.html:44` promise "the board shows
`a***x@example.com`". That copy stops being true and changes with it. The
`POST /api/players` response keeps echoing the caller's **own** masked address -
that is theirs, and it is how the form confirms what it stored.

## What this drops from the earlier scope

The first draft proposed blocking a rename on an address claim. No longer
wanted: with E1 a claim is authenticated, so renaming is legitimate again, and
blocking it was a UX cost for nothing.

## Files

| File | Why |
|---|---|
| `arena/db.py` | the column, the backfill |
| `arena/identity.py` | `new_recovery_code`, `RECOVERY_LENGTH` |
| `arena/rooms.py` | `upsert_player` ordering, `_code_matches` |
| `arena/app.py` | the request field, the located refusal, both player responses |
| `arena/board.py` | drop `email` |
| `arena/static/api.js` | route a field-located refusal |
| `arena/static/signup.js` | the code box |
| `arena/static/join.html`, `register.html` | its markup |
| `arena/static/register.js` | show the code after registering |
| `arena/static/home.html`, `home.js` | show the code |
| `arena/static/board.js` | drop `masked()` |
| `arena/README.md` | `/api/players` and `/api/players/me` both change shape |
| `arena/tests/conftest.py` | `phones.join` becomes a claim on a re-join |

Fourteen files against C1's two. Worth knowing before sequencing.

## The alternative that was considered and not taken

**Delete the email field entirely.** It is the smallest *complete* fix: the
address stops being a credential because it stops existing, the personal data
goes with it, and the board question disappears. Similar file count, and every
edit is a deletion.

Not taken because it removes a shipped feature - the only thing an address buys
is one place on the board across two phones (`identity.py:6`) - and removing
something that works is a bigger disruption to a stable venue than adding a
field. **A product call rather than a security one. If the cross-phone case is
not actually used at your events, say so and this becomes the better answer.**

---

# Sequencing

C1 and E1 are independent. C1 first: two files, near-zero blast radius, and it
is the whole of the anti-cheat against a stranger. E1 is fourteen files and
defends a narrower attack that needs a known address.

1. **C1.** Verify: the lab still drives `WRKS`; each of the four vectors is
   refused; a phone shouts and sees a huddle.
2. **E1.** Verify: register, play, register again on a fresh cookie jar with the
   code, and without it.

# Test plan

| Test | Kind |
|---|---|
| a hostile `state` on create-session is replaced by `WRKS` | new |
| a seeded `events[].actions.state_delta` is dropped | new |
| a `stateDelta` on `/run_sse` is dropped | new |
| a `fileData` part never reaches the coach; nor do the other non-text kinds | new |
| the part count and per-part length are capped | new |
| `appName` and `userId` are pinned whatever the caller sends | new |
| `team: red` survives, within `WRKS` | new |
| the lab's current body still works end to end | new |
| `test_open_session_carries_body_and_returns_session_id` | **inverts** - it asserts today that the body arrives unaltered, `state` included, which is the vulnerability written down as a guarantee |
| a fresh player gets a code; one without an address does not | new |
| the backfill gives an existing address-bearing row a code, once | new |
| a claim from a cookie-less phone without the code is refused, located on `recovery_code` | new |
| a claim with the code returns the original row | new |
| a lower-case code is accepted | new |
| the same phone playing again still needs no code | new |
| an address still outranks somebody else's cookie, given the code | changed |
| `test_an_address_brings_a_manager_back_on_a_phone_with_no_cookie` | changed |
| the board carries no address | new |
| `test_board.py:161`, `test_rooms.py:386` | changed |

**Fixture audit.** `conftest.phones.join()` clears the jar and re-posts name and
email, which under E1 is a claim. Two files call `phones.fresh()` and then
re-join - `test_joining.py` and `test_scanning.py` - and both need reading line
by line. The fixture itself grows an optional code argument rather than every
call site learning about it.

Plus the full suite and the wall E2E, unchanged and green.

# What is deliberately not in this

Each was in an earlier draft or was asked for, and each is recorded so that
leaving it out is a decision rather than an oversight.

- **The lab perimeter (`ARENA_LAB_KEY`).** Gating `/run_sse`, the session route
  and `/pitch` behind a shared key. After C1 this no longer defends the board -
  but it is not merely about cost either. The proxy path calls the coach
  directly and **bypasses `chain.Chain`'s semaphore** while still spending the
  same Vertex quota as live shouts, so a flood degrades or fails managers'
  shouts *during matches*. It also closes the two `WRKS` consequences above.
  Deferred, with that understood. Note for whoever picks it up: `/arena` imports
  `/pitch/viewer.js` and every match texture from the same mount, so the gate
  must refuse the exact path `index.html` and never the mount.
- **Tiered access (a + b + c).** Restricting who may watch, who may host and who
  may run a big screen. The blocker is that `POST /api/players` mints a session
  for anybody, so tiers only mean something if registration is gated too - a
  venue passcode, or Google sign-in. Not a small change, and it reshapes the
  product.
- **IAP.** Investigated and rejected: IAP on Cloud Run is a service-level
  toggle that protects all ingress paths, and the arena serves phones and the
  big screen from one service. It would also break the grounds, which dials
  `/ws/grounds` over the public URL with a static header and no OIDC token.
- **Review findings 4-7.** The physics token in the WebSocket query string, the
  session cookie's missing `Secure`, no CSP or `frame-ancestors`, and sessions
  that never expire. None lets a stranger manufacture points.

---

# Reviewed 2026-08-18, and cleared to build

Checked claim by claim against the code and the installed ADK rather than read
for plausibility. Everything checkable was accurate, including the line numbers
and the field surface:

| Claim | Verified against |
|---|---|
| Both proxied routes forward the caller's body verbatim | `arena/proxy.py:95`, `:120` - `content=raw` in both |
| `state` and `events` on create-session, `state_delta` and `new_message` on `/run_sse` | the installed ADK's request models at `api_server.py:397`, `:400`, `:382`, `:380` - all four exactly where this document says |
| A `Part` carries fourteen non-text kinds including `file_data` | `google.genai.types.Part.model_fields`, read from the venv; `FileData` is `display_name`/`file_uri`/`mime_type` as quoted |
| The lab sends `stateDelta: null` and a room from its URL | `game/frontend/src/main.js:433-467` |
| Pinning to `WRKS` puts the proxy out of reach of both boards | `arena/rooms.py:167` - `0 if code == codes.WORKSHOP else 1`, against `board.RANKED` |
| An address outranks a cookie | `arena/rooms.py:52-54` |
| A column and a boot-time backfill are the house pattern | `arena/db.py:148-153` and `_pull_apart_shared_names` at `:252` |
| 32^6 for a six-character code | `codes.ALPHABET` is 32 characters |

Two things to add, neither of which changes the plan.

**The free-inference path survives C1, and that is now a deployment decision
rather than a note.** Pinning the room stops the board being cheated; it does
not stop a stranger spending the venue's Vertex quota through `/run_sse` and
reading the stream back. The document defers this to the lab perimeter and says
why. What it does not say is that a bound already exists: `COACH_RATE,
COACH_BURST = 5.0, 60` (`arena/app.py:114`) meters both proxied routes at the
instance, which is the honest unit for a venue behind one address. Five calls a
second sustained is a real ceiling but not a small one, so anybody putting this
on a public URL should know that is the number they are accepting.

**The backfill mints codes that existing managers have no way to read.** A row
with an address gets a code at boot, but a manager learns their code from
`/home`, which needs their session. Somebody who registered before this change,
lost their cookie, and comes back on a second phone is now locked out of the row
they used to reach - the feature E1 exists to protect, removed for exactly the
people who already used it. This is moot for a venue starting on a fresh
database, which is the case this is first being deployed into, and that is why
it is an amendment and not a blocker. It must be answered before this is applied
to a database that already holds managers: either a reissue route, or an
operator-run `UPDATE` and a way to tell people, or accepting that pre-change
addresses stop working and saying so in the copy.

Sequencing stands: C1 first, then E1.

# Dugout smoke checklist

Deliberately manual. Agent output is nondeterministic and a flaky test in a
workshop repo is worse than no test.

Prerequisites: `agy login` completed, `dugout/.env` has GOOGLE_CLOUD_PROJECT
and GOOGLE_CLOUD_LOCATION, and `game/.env` exists too (`cp game/.env.example
game/.env`). The game's own ADK coach needs its own credentials. Without them
the pitch still renders and steps 1 to 10 all pass, but every call into the
coach fails with "No API key was provided", which breaks the shout chain in
step 9 and the baseline restore in step 11.

Export `ARENA_SERVICE_TOKEN` in every shell, the same value in each. The
squad lives in the arena now, and the arena refuses a write from a caller with
no token: the dugout's tuners and the game's four player agents both carry it
instead of a phone session. Get it wrong in one shell only and the symptom is
quiet, a squad that reads fine and never moves.

The agent runs shell commands unrestricted, by design, so that it can launch
the Playwright script it writes in stage 2. Run this on your own machine.

1. `cd arena && ./run.sh`, wait for :8003. It owns the squad, so it goes up
   first.
2. `cd game && ./run.sh`, wait for Vite on :5173.
3. `cd dugout && ./run.sh`, open http://localhost:8002.
4. Header shows Antigravity lit amber and four green game dots, arena among
   them. No red banner.
   - If a red banner appears, it prints the precise reason. The most likely cause
     is a missing Antigravity login. Run `agy login` in a terminal, then reload
     the page. The composer stays disabled until the agent is reachable, so being
     unable to type is expected and not a second fault.
5. Team sheet lists five stages (rebrand, take the field, read the game, tune
   the squad, shout to the bench), none marked done after a fresh dugout
   launch. Every stage keeps its
   suggested line whether or not it is done, so any of them can be run again;
   a done stage offers "Run it again". Tweaking and re-running is the point.
   Stage completion is session-relative: the tools record what they were used
   for, and a sprite only counts if it is newer than the session start. That
   is what makes the quest replayable, since nobody has to reset anything by
   hand. Take the field is the exception and reads the present tense: a match
   being played right now.
   - Press Start over to begin a new session without restarting the server.
     It blanks the quest, clears the log and gives the agent a fresh
     conversation, leaving the kit on disk and the squad in the arena alone.
   - Do press it if you open the page onto a quest someone else was halfway
     through. A dugout left running keeps counting yesterday's work, and
     because each stage is judged independently you can otherwise arrive at
     something incoherent, like stage 4 done while stage 3 has not been run.
6. Send "Kit us out in black and gold with a wolf crest."
   - Trajectory shows a thought, then CALLED generate_team_avatars.
   - The log ends with the generated strip itself, outfield sheet and keeper,
     on a checkered background so a transparent cut-out is obvious. A green
     rectangle instead means the chroma-key missed.
   - Every event names its actor in the gutter.
   - Stage 1 flips to done. Reload http://localhost:5173 and the new kit is on
     the pitch, and in the portrait in the top left corner. The players are a
     few pixels tall mid-match, so that portrait is where you actually read
     the strip; the opponent's sits in the top right for comparison.
7. Send "Now get us on the pitch."
   - Antigravity writes a Playwright script and runs it.
   - A Chrome window opens in front of you and plays the match, muted. This is
     the point of the stage: if it comes up headless, the script is wrong.
   - It fills the screen. A window a fraction of the display, or a maximised
     one with the page still small inside it, means the script pinned a size
     or passed a viewport.
   - The simulation runs at 1x. Antigravity must not touch the speed slider on
     its own; that control belongs to you.
   - If it hits the #kick-off-btn stability timeout, it should retry with
     force=True on its own. That self-correction is the moment worth watching.
   - /tmp/futsal_status.json appears and updates, and
     http://localhost:9222/json/version answers, which is how later turns
     reach this same window.
8. Send "How are we doing?" and confirm it reports a real score, and the
   squad's real attributes with the band each one has to stay inside. Those
   come from the arena, so they are the same numbers the game is playing with.
9. Send "Tell the lads to push up and press high." This is stage 5 on the team
   sheet, the other way to change the team.
   - One call to shout_to_the_team, not a script it writes itself. It goes to
     the arena over HTTP, so nothing opens a second window and the match on
     screen is not touched.
   - The chain runs coach, then team captain over A2A on :8001, then the four
     specialists. Each answer comes back down the workshop's own socket and the
     tool waits for the captain's huddle, which always arrives, so Antigravity
     should call it once and report what it heard.
   - A second panel appears, headed "The game's agents", in cyan. It shows
     the same bars for whatever the four player agents changed. The
     shout_to_the_team call above it stays amber and named Antigravity,
     because Antigravity made the call; the panel is cyan, because the
     game's agents chose the numbers.
   - Every lane names the room, the dugout and the player it moved:
     WRKS/blue/forward. Both this stage and stage 4 write there, which is the
     point of the room being reserved.
   - Stage 5 ticks, stage 4 does not. Both routes move the same attributes
     through the same arena, so a shout that also ticked "tune the squad"
     would be claiming the subagents ran when they did not.
   - Shout with no match on screen and it still goes through, because the
     squad lives in the arena rather than in the page. The answer says so and
     tells you to take the field to watch the difference.
   - Out-of-range or invented attributes are refused by the arena, so they
     never reach the squad: the specialist is told which ones and why, and
     corrects itself. Nothing should crash.
   - Stop the arena and shout again. It comes back naming :8003 rather than
     failing silently, and the arena dot in the header goes red.
10. Send "They keep breaking through the middle. Tighten it up."
    - Four subagents run. Each tool call is attributed to its own role, so the
      gutter reads DEFENDER, MIDFIELDER, FORWARD, GOALKEEPER rather than
      Antigravity four times.
    - One panel appears, headed "Antigravity subagents", with a lane per role
      in the role's own colour. A lane opens as soon as its tuner starts and
      shows a pulsing "working" until it reports, so the four are visibly
      running at once rather than appearing one at a time.
    - Each changed attribute is one row: the name, the old value, the new
      value, and a bar underneath. On the bar, a faint tick is the shipped
      baseline, a hollow dot is the value before this call, and the filled dot
      is where it landed. The tuner's reason sits at the foot of its lane.
    - Nothing renders a line of raw JSON. The tune call itself reads only
      "Called tune_defender", because the panel carries the numbers.
    - A tuner that changes the same attribute twice keeps one row, still
      measured from the first value it moved off.
    - The winning-the-match skill is listed on this stage. Click it to read
      exactly what Antigravity was told about the simulation.
    - All four lanes land within a few seconds of each other, each headed
      `WRKS/blue/<role>`, and the arena's room log has the four profile.patch
      events to match.
    - Any out-of-range attempt comes back as a violation list, not a crash. The
      arena refuses the whole write and names every problem at once, so the
      tuner can correct them in one go.
    - A match is three minutes at 1x and a tuning turn takes three to five, so
      expect full time before the changes land. That is the honest default.
    - To watch them land instead, drag the simulation speed down to 0.5x before
      kick-off, which buys six minutes. Then watch the window rather than the
      dugout: the back line tightens almost at once, because the arena
      tells the pitch what moved down the same socket and the scene picks it up
      on its next frame.
11. Refresh http://localhost:5173: the squad is clean again. The workshop page
    asks for the shipped baseline on every load, which is what makes the stages
    repeatable in a room that outlives them. It goes through the game's ADK
    coach, so it needs game/.env; check the browser console if the numbers do
    not move back.

Afterwards, run `git status`. A smoke run dirties tracked files: the sprites
are regenerated, so restore `game/frontend/public/assets/sprites/` unless you
meant to keep the new kit, and delete the scripts the agent wrote at the
repository root. The squad itself is no longer among them, since it lives in
the arena's database rather than in the working tree.

Failure to check on purpose: stop the game stack and send a message. Its three
dots go red within about four seconds while the arena stays green, since the
header polls each of them rather than reading once at load. The agent has
shell access, so it does not just report
game_not_running: it reads the logs, brings the stack back up with
game/run.sh and restarts the match. That self-repair is worth watching. Give
it about forty seconds; all three dots should go green, not just the pitch.

## The venue

Two checks the dugout stages do not reach, because they are about the venue's
football rather than the workshop's. Both need a grounds up - see the fourth
shell in the root README, and note the arena wants `ARENA_PITCH_DIR` for it.

12. **A match outlives the screen watching it.** Open `/arena`, open a room,
    join from a phone, take a dugout, kick off. Watch the clock start, then
    close the arena tab entirely and wait ninety seconds - past
    `HOST_GONE_SECONDS` and a sweep, which is when this used to be an abandoned
    match. Reopen `/arena`. The match is still there, the clock has gone on
    without you by about ninety seconds, and the score is whatever it became
    while nobody was looking. This is the whole reason the grounds exists; if
    it fails, nothing else on this page matters.

13. **A venue with no pitch says so.** Stop the grounds and kick off. Expected:
    a refusal in words - `no pitch is free to run this match` - and a room
    still sitting in its lobby with its dugouts as they were. What must not
    happen is a room that goes live with a clock that never starts, which is
    the failure nobody can diagnose from the floor. Start the grounds again and
    kick off the same room to confirm it recovers without being reopened.

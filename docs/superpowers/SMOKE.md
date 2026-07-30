# Dugout smoke checklist

Deliberately manual. Agent output is nondeterministic and a flaky test in a
workshop repo is worse than no test.

Prerequisites: `agy login` completed, `dugout/.env` has GOOGLE_CLOUD_PROJECT
and GOOGLE_CLOUD_LOCATION, and `game/.env` exists too (`cp game/.env.example
game/.env`). The game's own ADK coach needs its own credentials. Without them
the pitch still renders and steps 1 to 8 all pass, but every call into the
coach fails with "No API key was provided", which silently breaks the baseline
backup and restore in step 9.

The agent runs shell commands unrestricted, by design, so that it can launch
the Playwright script it writes in stage 2. Run this on your own machine.

1. `cd game && ./run.sh`, wait for Vite on :5173.
2. `cd dugout && ./run.sh`, open http://localhost:8002.
3. Header shows Antigravity lit amber and three green game dots. No red banner.
   - If a red banner appears, it prints the precise reason. The most likely cause
     is a missing Antigravity login. Run `agy login` in a terminal, then reload
     the page. The composer stays disabled until the agent is reachable, so being
     unable to type is expected and not a second fault.
4. Team sheet lists four stages (rebrand, take the field, read the game, tune the
   squad), none marked done after a fresh dugout launch. Stage completion is
   session-relative: a filesystem write is only counted if it is newer than the
   dugout process start. Restarting the dugout therefore blanks the quest even
   though the work really happened. This is what makes the quest replayable,
   since each run starts clean without anyone having to reset files by hand.
5. Send "Kit us out in black and gold with a wolf crest."
   - Trajectory shows a thought, then CALLED generate_team_avatars.
   - Every event names its actor in the gutter.
   - Stage 1 flips to done. Reload http://localhost:5173 and the new kit is on
     the pitch.
6. Send "Now get us on the pitch."
   - Antigravity writes a Playwright script and runs it.
   - If it hits the #kick-off-btn stability timeout, it should retry with
     force=True on its own. That self-correction is the moment worth watching.
   - /tmp/futsal_status.json appears and updates.
7. Send "How are we doing?" and confirm it reports a real score.
8. Send "They keep breaking through the middle. Tighten it up."
   - Four subagents run. Each tool call is attributed to its own role, so the
     gutter reads DEFENDER, MIDFIELDER, FORWARD, GOALKEEPER rather than
     Antigravity four times.
   - All four role files are rewritten within a few seconds of each other.
   - Any out-of-range attempt comes back as a violation list, not a crash.
   - A match lasts three minutes and a tuning turn takes longer than that, so
     expect the match to have ended by the time the changes land. To watch
     them take effect live, ask for another kick-off first and keep the turn
     short.
9. Refresh http://localhost:5173 in the tab you already had open: baselines
   restore and the squad is clean again. Two things to know. It must be the
   same tab, because the restore is keyed on sessionStorage and a new tab or
   window counts as a first load, which backs the current attributes up as the
   new baseline instead. And it goes through the game's ADK coach, so it needs
   game/.env; check the browser console if the files do not change.

Failure to check on purpose: stop the game stack and send a message. The three
game dots go red within about four seconds, since the header polls rather than
reading once at load. The agent has shell access, so it does not just report
game_not_running: it reads the logs, restarts the Vite server and the match,
and carries on. That self-repair is worth watching. It brings back the pitch
only, so the coach and captain dots stay red until you run game/run.sh.

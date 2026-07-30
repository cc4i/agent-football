# Dugout smoke checklist

Deliberately manual. Agent output is nondeterministic and a flaky test in a
workshop repo is worse than no test.

Prerequisites: `agy login` completed, `dugout/.env` has GOOGLE_CLOUD_PROJECT
and GOOGLE_CLOUD_LOCATION, and `game/.env` exists too (`cp game/.env.example
game/.env`). The game's own ADK coach needs its own credentials. Without them
the pitch still renders and steps 1 to 9 all pass, but every call into the
coach fails with "No API key was provided", which breaks the shout chain in
step 8 and the baseline restore in step 10.

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
   - A Chrome window opens in front of you and plays the match, muted. This is
     the point of the stage: if it comes up headless, the script is wrong.
   - It drops the simulation to 0.5x before kick-off, so the three minute match
     runs for six and is still going when you tune the squad in step 8.
   - If it hits the #kick-off-btn stability timeout, it should retry with
     force=True on its own. That self-correction is the moment worth watching.
   - /tmp/futsal_status.json appears and updates, and
     http://localhost:9222/json/version answers, which is how later turns
     reach this same window.
7. Send "How are we doing?" and confirm it reports a real score.
8. Send "Tell the lads to push up and press high."
   - Antigravity attaches to the match window already on screen rather than
     opening a second one, and types into the game's shout bar. Watch the
     window, not just the dugout: the shout appears in the trace panel.
   - The chain runs coach, then team captain over A2A on :8001, then the four
     specialists, and each player answers in #terminal-body. Antigravity
     reports those answers back to you.
   - Out-of-range or invented attributes come back as "Rejected: ..." and the
     specialist corrects itself. Nothing should crash.
9. Send "They keep breaking through the middle. Tighten it up."
   - Four subagents run. Each tool call is attributed to its own role, so the
     gutter reads DEFENDER, MIDFIELDER, FORWARD, GOALKEEPER rather than
     Antigravity four times.
   - All four role files are rewritten within a few seconds of each other.
   - Any out-of-range attempt comes back as a violation list, not a crash.
   - The match should still be playing when they land, because step 6 halved
     the simulation speed. Watch the window: the back line visibly tightens
     within about two seconds of the files changing.
   - If the match has already ended, ask for another kick-off and repeat. A
     tuning turn takes three to five minutes against a six minute match, so
     it is close.
10. Refresh http://localhost:5173 in the tab you already had open: baselines
   restore and the squad is clean again. Two things to know. It must be the
   same tab, because the restore is keyed on sessionStorage and a new tab or
   window counts as a first load, which backs the current attributes up as the
   new baseline instead. And it goes through the game's ADK coach, so it needs
   game/.env; check the browser console if the files do not change.

Failure to check on purpose: stop the game stack and send a message. The three
game dots go red within about four seconds, since the header polls rather than
reading once at load. The agent has shell access, so it does not just report
game_not_running: it reads the logs, brings the stack back up with
game/run.sh and restarts the match. That self-repair is worth watching. Give
it about forty seconds; all three dots should go green, not just the pitch.

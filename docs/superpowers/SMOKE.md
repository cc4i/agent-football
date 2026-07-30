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
   session start. That is what makes the quest replayable, since nobody has to
   reset files by hand.
   - Press Start over to begin a new session without restarting the server.
     It blanks the quest, clears the log and gives the agent a fresh
     conversation, leaving the kit and the squad on disk alone.
   - Do press it if you open the page onto a quest someone else was halfway
     through. A dugout left running keeps counting yesterday's work, and
     because each stage is judged independently you can otherwise arrive at
     something incoherent, like stage 4 done while stage 3 is still locked.
5. Send "Kit us out in black and gold with a wolf crest."
   - Trajectory shows a thought, then CALLED generate_team_avatars.
   - Every event names its actor in the gutter.
   - Stage 1 flips to done. Reload http://localhost:5173 and the new kit is on
     the pitch, and in the portrait in the top left corner. The players are a
     few pixels tall mid-match, so that portrait is where you actually read
     the strip; the opponent's sits in the top right for comparison.
6. Send "Now get us on the pitch."
   - Antigravity writes a Playwright script and runs it.
   - A Chrome window opens in front of you and plays the match, muted. This is
     the point of the stage: if it comes up headless, the script is wrong.
   - The simulation runs at 1x. Antigravity must not touch the speed slider on
     its own; that control belongs to you.
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
   - A match is three minutes at 1x and a tuning turn takes three to five, so
     expect full time before the changes land. That is the honest default.
   - To watch them land instead, drag the simulation speed down to 0.5x before
     kick-off, which buys six minutes. Then watch the window rather than the
     dugout: the back line tightens within about two seconds of the files
     changing, because the game re-reads player_state every two seconds and
     pushes it straight into the running match.
10. Refresh http://localhost:5173 in the tab you already had open: baselines
   restore and the squad is clean again. Two things to know. It must be the
   same tab, because the restore is keyed on sessionStorage and a new tab or
   window counts as a first load, which backs the current attributes up as the
   new baseline instead. And it goes through the game's ADK coach, so it needs
   game/.env; check the browser console if the files do not change.

Afterwards, run `git status`. A smoke run dirties tracked files and some of it
is not obvious: the sprites are regenerated, the four role files are tuned,
and the `*_baseline.json` files get rewritten too, because the game captures
whatever is on disk as the new baseline on a first page load. Committing that
would ship a tuned squad as the starting eleven. Restore
`game/frontend/public/player_state/` and `game/frontend/public/assets/sprites/`
unless you meant to keep them, and delete the scripts the agent wrote at the
repository root.

Failure to check on purpose: stop the game stack and send a message. The three
game dots go red within about four seconds, since the header polls rather than
reading once at load. The agent has shell access, so it does not just report
game_not_running: it reads the logs, brings the stack back up with
game/run.sh and restarts the match. That self-repair is worth watching. Give
it about forty seconds; all three dots should go green, not just the pitch.

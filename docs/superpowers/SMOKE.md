# Dugout smoke checklist

Deliberately manual. Agent output is nondeterministic and a flaky test in a
workshop repo is worse than no test.

Prerequisites: `agy login` completed, `dugout/.env` has GOOGLE_CLOUD_PROJECT
and GOOGLE_CLOUD_LOCATION.

1. `cd game && ./run.sh`, wait for Vite on :5173.
2. `cd dugout && ./run.sh`, open http://localhost:8002.
3. Header shows Antigravity lit amber and three green game dots. No red banner.
4. Team sheet lists four stages, none marked done on a clean tree.
5. Send "Kit us out in black and gold with a wolf crest."
   - Trajectory shows a thought, then CALLED generate_team_avatars.
   - Every event names its actor in the gutter.
   - Stage 1 flips to done. Reload the game tab and the new kit is on the pitch.
6. Send "Now get us on the pitch."
   - Antigravity writes a Playwright script and runs it.
   - If it hits the #kick-off-btn stability timeout, it should retry with
     force=True on its own. That self-correction is the moment worth watching.
   - /tmp/futsal_status.json appears and updates.
7. Send "How are we doing?" and confirm it reports a real score.
8. Send "They keep breaking through the middle. Tighten it up."
   - Four subagents run. Each tool call is attributed to its own role.
   - Attribute changes land in the match within about two seconds.
   - Any out-of-range attempt comes back as a violation list, not a crash.
9. Refresh the game tab: baselines restore and the squad is clean again.

Failure to check on purpose: stop the game stack and send a message. The agent
should report game_not_running and tell you to run game/run.sh.

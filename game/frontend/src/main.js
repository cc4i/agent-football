// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

import './style.css';
import Phaser from 'phaser';
import { SoccerGameScene, GAME_DURATION_SEC, STATUS_CHECK_MS } from './game';
import { Sound } from './audio';
import { createStatusHook } from './status.js';
import { showCondition } from './substitutions.js';
import { room, shouldRunStatusCheck, opposite, readProfiles, connect } from './arena.js';

let gameInstance = null;
let currentProfiles = {};

// The other dugout. Kept apart from `currentProfiles` because that one is this
// page's own squad: it is what the attribute panel shows, what the debug log
// diffs, and what the workshop stages work on. The opposition is simulation
// input and nothing else.
let otherProfiles = {};

// Toggle to show/hide the "Debug logs" panel entirely. Set to false to remove
// the panel from the UI (it will not be rendered and no logs are collected).
const DEBUG_LOGS_ENABLED = true;
const MAX_DEBUG_ENTRIES = 200;

// Inject the premium automated dashboard HTML structure
document.querySelector('#app').innerHTML = `
  <div class="game-wrapper">
    <!-- Title Header with Top-Right Simulation Speed -->
    <header class="game-header">
      <div class="header-titles">
        <h1 class="neon-text">Futsal WorldCup</h1>
        <p class="sub-title">
          <svg class="gemini-icon" viewBox="0 0 24 24" width="16" height="16" aria-label="Gemini">
            <defs>
              <linearGradient id="gemini-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stop-color="#4E95FF" />
                <stop offset="50%" stop-color="#9A7BFF" />
                <stop offset="100%" stop-color="#FF758F" />
              </linearGradient>
            </defs>
            <path fill="url(#gemini-grad)" d="M11.04 19.32Q12 21.51 12 24q0-2.49.93-4.68.96-2.19 2.58-3.81t3.81-2.55Q21.51 12 24 12q-2.49 0-4.68-.93a12.3 12.3 0 0 1-3.81-2.58 12.3 12.3 0 0 1-2.58-3.81Q12 2.49 12 0q0 2.49-.96 4.68-.93 2.19-2.55 3.81a12.3 12.3 0 0 1-3.81 2.58Q2.49 12 0 12q2.49 0 4.68.96 2.19.93 3.81 2.55t2.55 3.81"/>
          </svg>
          <span>Powered by Gemini</span>
        </p>
      </div>

      <!-- Simulation Speed Control (Top Right) -->
      <div class="sim-speed-bar">
        <div class="slider-header">
          <span class="slider-label">Speed</span>
          <span class="slider-val" id="val-sim-speed">1.00x</span>
        </div>
        <input 
          type="range" 
          class="slider-input" 
          id="sim-speed-input" 
          min="0.5" 
          max="3" 
          step="0.25" 
          value="1" 
        />
      </div>
    </header>

    <!-- Phaser Game Canvas Container -->
    <main class="game-container">
      
      <!-- Start Screen -->
      <div id="start-screen" class="menu-screen active">
        <div class="menu-content">
          <div class="promo-visual">
            <div class="visual-team blue-glow">BLUE (AI)</div>
            <div class="versus">VS</div>
            <div class="visual-team red-glow">RED (AI)</div>
          </div>

          <div style="text-align: center; color: var(--text-muted); max-width: 500px; line-height: 1.6; font-size: 0.95rem;">
            <p>Welcome to the Automated Futsal Simulator Sandbox!</p>
            <p>Both teams play autonomously based on their behavioral attributes. Shout at the squad, tune them from the dugout, or adjust simulation speed to run experiments.</p>
          </div>

          <div class="menu-actions">
            <button id="audio-toggle-btn" class="action-btn secondary">
              🔊 SOUND: ON
            </button>
            <button id="kick-off-btn" class="action-btn primary pulse">
              KICK OFF!
            </button>
          </div>
        </div>
      </div>

      <!-- Side-by-side Layout -->
      <div class="game-layout">
        <!-- Phaser Game Canvas Container -->
        <div id="phaser-container" class="canvas-container"></div>

        <!-- 📟 Live Agent Terminal -->
        <div id="agent-terminal" class="agent-terminal">
          <div class="terminal-header">
            <span class="terminal-title">📟 Live Agent Trace</span>
            <button id="terminal-clear" class="terminal-clear">Clear</button>
          </div>
          <div id="terminal-body" class="terminal-body">
            <div class="terminal-line line-system">> Simulator ready. Waiting for Coach instruction...</div>
          </div>
        </div>
      </div>

      <!-- Game Over Overlay -->
      <div id="game-over-screen" class="menu-screen">
        <div class="menu-content game-over-box">
          <h2 class="game-over-title">FULL TIME!</h2>
          <div class="final-winner" id="winner-announcement">BLUE TEAM WINS!</div>
          
          <div class="final-score-board">
            <div class="score-box blue-text" id="final-p1-score">3</div>
            <div class="score-divider">-</div>
            <div class="score-box red-text" id="final-p2-score">1</div>
          </div>

          <button id="rematch-btn" class="action-btn primary">
            REMATCH!
          </button>
        </div>
      </div>

    </main>

    <!-- Interactive Coach Shout Bar (Sandbox shout interaction) -->
    <div class="coach-shout-bar active">
      <div class="coach-bar-content">
        <input type="text" id="shout-message-input" placeholder="Shout instructions (e.g., shoot, defend, attack)..." />
        <button id="shout-send-btn">Shout!</button>
      </div>
    </div>

    ${DEBUG_LOGS_ENABLED ? `
    <!-- Debug Logs Panel -->
    <details id="debug-log-panel" class="debug-log-panel">
      <summary class="debug-log-summary">
        <span class="debug-log-title">🛠️ Debug logs</span>
        <span class="debug-log-hint">player config changes as the arena reports them</span>
        <button id="debug-log-clear" class="debug-log-clear" type="button">Clear</button>
      </summary>
      <div id="debug-log-body" class="debug-log-body">
        <div class="debug-empty">Waiting for player config changes…</div>
      </div>
    </details>
    ` : ''}

    <!-- Footer Info -->
    <footer class="game-footer">
      <p>Configure player attributes to run experiments and test defensive vs aggressive configurations.</p>
    </footer>
  </div>
`;

// ---- Debug logs: track and render per-attribute config changes ----------

// Format a value for display in the debug log (round floats, mark missing).
function fmtDebugValue(v) {
  if (v === undefined) return '∅';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}

// Compute the list of attribute-level differences between two profiles.
function diffProfile(oldProfile, newProfile) {
  const changes = [];
  const keys = new Set([
    ...Object.keys(oldProfile || {}),
    ...Object.keys(newProfile || {})
  ]);
  keys.forEach(key => {
    const before = oldProfile ? oldProfile[key] : undefined;
    const after = newProfile ? newProfile[key] : undefined;
    if (before !== after) changes.push({ key, before, after });
  });
  return changes;
}

// Append a log entry (newest on top) describing changes to a role's config.
function appendDebugLog(role, changes, label) {
  if (!DEBUG_LOGS_ENABLED || changes.length === 0) return;
  const body = document.getElementById('debug-log-body');
  if (!body) return;

  const placeholder = body.querySelector('.debug-empty');
  if (placeholder) placeholder.remove();

  const time = new Date().toLocaleTimeString();
  const entry = document.createElement('div');
  entry.className = `debug-entry debug-${role}`;

  const span = (className, text) => {
    const el = document.createElement('span');
    el.className = className;
    el.textContent = text;
    return el;
  };

  const head = document.createElement('div');
  head.className = 'debug-entry-head';
  head.append(span('debug-time', `[${time}]`), span('debug-role', String(role).toUpperCase()));
  if (label) head.append(span('debug-label', label));

  const changesEl = document.createElement('div');
  changesEl.className = 'debug-entry-changes';
  changes.forEach(c => {
    const change = span('debug-change', '');
    change.append(
      span('debug-key', c.key),
      ' ',
      span('debug-from', fmtDebugValue(c.before)),
      span('debug-arrow', ' → '),
      span('debug-to', fmtDebugValue(c.after)),
    );
    changesEl.append(change);
  });

  entry.append(head, changesEl);

  body.prepend(entry);

  while (body.children.length > MAX_DEBUG_ENTRIES) {
    body.removeChild(body.lastChild);
  }
}

// ---- Profiles: read from the arena, moved by the room's socket ----------

// Hand the current profiles to a running scene. It keeps its own copy, so
// nothing takes effect on the pitch until it is told.
function applyProfilesToScene() {
  window.currentProfiles = currentProfiles; // Expose globally for Phaser
  if (!gameInstance) return;
  const scene = gameInstance.scene.getScene('SoccerGameScene');
  if (!scene) return;
  scene.updateProfiles(room.team, currentProfiles);
  scene.updateProfiles(opposite(), otherProfiles);
}

// This dugout's squad as the arena holds it. Read once at load and again on a
// rematch; from then on the socket says what moved and why.
async function loadProfiles() {
  try {
    const squad = await readProfiles(room.code, room.team);
    Object.entries(squad).forEach(([role, profile]) => {
      currentProfiles[role] = profile;
      // Log the initial values so the panel shows the starting config.
      appendDebugLog(role, diffProfile({}, profile), 'initial load');
    });
    // This page runs the whole pitch, so it reads the other dugout too. In the
    // workshop that is the house side the arena seeded from the baselines.
    otherProfiles = await readProfiles(room.code, opposite());
    applyProfilesToScene();
  } catch (err) {
    console.error("Failed to load player profiles:", err);
  }
}

// One profile.patch off the room socket. It carries only what moved, which is
// all the simulation and the debug panel need -- and it arrives when it
// happens, rather than up to two seconds later.
function applyPatch(payload) {
  const mine = payload.team === room.team;
  const squad = mine ? currentProfiles : otherProfiles;
  const before = squad[payload.role] || {};
  const after = { ...before, ...(payload.changed || {}) };
  const changes = diffProfile(before, after);
  if (!changes.length) return;
  squad[payload.role] = after;
  // The panel and the log belong to this page's dugout. The opposition moving
  // is not this manager's news, and printing it would read as their own squad
  // changing under them.
  if (mine) appendDebugLog(payload.role, changes, payload.actor || 'applied');
  applyProfilesToScene();
}

// Debug logs: clear button (stop the click from toggling the <details>)
const debugClearBtn = document.getElementById('debug-log-clear');
if (debugClearBtn) {
  debugClearBtn.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const body = document.getElementById('debug-log-body');
    if (body) {
      body.innerHTML = '<div class="debug-empty">Waiting for player config changes…</div>';
    }
  });
}

// Audio toggling
const audioBtn = document.getElementById('audio-toggle-btn');
audioBtn.addEventListener('click', () => {
  const isEnabled = Sound.toggle();
  audioBtn.textContent = isEnabled ? '🔊 SOUND: ON' : '🔇 SOUND: OFF';
  Sound.playMenuClick();
});

// Kick off starts the simulation
const kickOffBtn = document.getElementById('kick-off-btn');
kickOffBtn.addEventListener('click', () => {
  Sound.playMenuClick();
  document.getElementById('start-screen').classList.remove('active');
  startPhaserGame();
});

// Rematch button starts the match again
const rematchBtn = document.getElementById('rematch-btn');
rematchBtn.addEventListener('click', () => {
  Sound.playMenuClick();
  document.getElementById('game-over-screen').classList.remove('active');

  appendTerminalLine("system", `> 🔄 Rematch clicked: Restoring starting baseline profiles...`);

  // Back to the shipped squad before restarting, so a rematch is a fresh
  // experiment rather than a continuation of the last one's shouts.
  sendInstructionToAgent("RESTORE_BASELINE", { showHuddle: false }).then(() => {
    loadProfiles(); // Reload the fresh baseline profiles
    if (gameInstance) {
      const scene = gameInstance.scene.getScene('SoccerGameScene');
      scene.restartMatch();
      Sound.playWhistle();
    }
  });
});

// Simulation speed slider listener
document.getElementById('sim-speed-input').addEventListener('input', (e) => {
  const speed = parseFloat(e.target.value);
  document.getElementById('val-sim-speed').textContent = speed.toFixed(2) + 'x';

  if (gameInstance) {
    const scene = gameInstance.scene.getScene('SoccerGameScene');
    if (scene) {
      scene.setSimulationSpeed(speed);
    }
  }
});

// Coach Shouts sandbox interface interaction
const shoutInput = document.getElementById('shout-message-input');
const shoutBtn = document.getElementById('shout-send-btn');

// A shout and the periodic condition check are independent errands to two
// independent ADK sessions, so they no longer share one lock. The old single
// flag meant the check, which runs for ~13s out of every 55s, could swallow a
// shout that arrived inside its window. What is still serialised is shouts
// against each other: two chains moving the same squad at once would leave the
// profiles in whichever order the two language models happened to finish in.
let shoutInFlight = false;
let checkInFlight = false;
let pendingShout = null;

function setShoutControls(enabled, label) {
  if (shoutBtn) {
    shoutBtn.disabled = !enabled;
    shoutBtn.textContent = label;
  }
  if (shoutInput) shoutInput.disabled = !enabled;
}

async function sendInstructionToAgent(msg, options = {}) {
  const { showHuddle = true } = options;
  const isBackground = !showHuddle;

  if (isBackground) {
    // Nothing is queued behind a condition check: the next one is 55 seconds
    // away and asks the same question, so a skipped one costs nothing.
    if (checkInFlight || shoutInFlight) {
      console.log("Skipping periodic status check: the squad is already busy.");
      return;
    }
    checkInFlight = true;
  } else if (shoutInFlight) {
    // One shout behind the one going out, and the manager can see it there.
    // Silently dropping it is what made a shout feel unreliable.
    pendingShout = msg;
    appendTerminalLine("system", `> ⏳ Shout queued: "${msg}" - the squad is still answering the last one...`);
    setShoutControls(false, "Queued...");
    return;
  } else {
    shoutInFlight = true;
    setShoutControls(false, "Thinking...");
  }

  // Per run, not per page: a shout and a condition check can now be in the air
  // at the same time, and one must not be able to apply the other's huddle.
  const run = { huddle: null };

  try {
    const outgoingMsg = `${msg}\n\n${getFitnessReport()}`;

    // Log initial trigger in terminal
    if (showHuddle) {
      appendTerminalLine("system", `> 📣 Coach shouted: "${msg}"`);
      appendTerminalLine("coach", `📣 Coach: "Relaying instruction to Team Captain over A2A (port 8001)..."`);
    } else {
      appendTerminalLine("system", `> 🤖 Running periodic status check...`);
    }

    // 1. Every instruction gets its own session.
    // Reusing one session for a whole match grows both the coach's history and the
    // captain's A2A context with each turn, and flash-lite starts failing on the
    // bloated context: transfer_to_agent comes back MALFORMED_FUNCTION_CALL, or the
    // captain invocation dies and A2A relays a bare "An error occurred during
    // processing". Measured 2/8 huddles when reused vs 6/6 with a fresh session.
    // Shouts are independent instructions, so there is no history worth keeping.
    console.log("Creating new agent session...");
    const sessionRes = await fetch('/api-apps/agents/users/user/sessions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/plain, */*'
      },
      body: JSON.stringify({
        state: {
          // The specialists read the room from here, exactly as the arena's
          // own chain sets it. Without it they file every injury against the
          // workshop, whichever match this tab is actually running.
          room_code: room.code,
          team: room.team,
          __session_metadata__: {
            displayName: "Futsal Coach Session"
          }
        }
      })
    });
    if (!sessionRes.ok) {
      throw new Error(`Failed to create session: ${sessionRes.statusText}`);
    }
    run.session = (await sessionRes.json()).id;
    console.log(`Agent session created successfully. Session ID: ${run.session}`);

    // 2. Send the message to /run_sse with streaming: true
    console.log(`Sending instruction to agent (streaming): "${outgoingMsg}"`);
    const runRes = await fetch('/run_sse', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        appName: "agents",
        userId: "user",
        sessionId: run.session,
        newMessage: {
          role: "user",
          parts: [{ text: outgoingMsg }]
        },
        streaming: true, // 🟢 Enable streaming!
        stateDelta: null
      })
    });

    if (!runRes.ok) {
      throw new Error(`Failed to run agent: ${runRes.statusText}`);
    }

    // 3. Read the SSE stream chunk by chunk
    const reader = runRes.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");

      // Keep the last incomplete line in the buffer
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.trim().startsWith('data: ')) {
          const jsonStr = line.replace(/^data:\s*/, '').trim();
          if (!jsonStr) continue;
          try {
            const event = JSON.parse(jsonStr);
            processAgentEvent(event, { showHuddle, run });
          } catch (err) {
            // Silent catch for partial or malformed chunks
          }
        }
      }
    }

    // Apply the accumulated huddle data at the end of the stream
    if (run.huddle && showHuddle) {
      console.log("Applying final huddle data to game:", run.huddle);
      if (gameInstance) {
        const scene = gameInstance.scene.getScene('SoccerGameScene');
        if (scene && scene.gameActive) {
          scene.showTeamHuddle(run.huddle);
        }
      }
    } else if (!run.huddle && showHuddle) {
      appendTerminalLine("system", `> ⚠️ No huddle response received from Team Captain.`);
    }
  } catch (err) {
    console.error("Error communicating with agent:", err);
    appendTerminalLine("system", `> ❌ Error: ${err.message}`);
  } finally {
    if (isBackground) {
      checkInFlight = false;
      return;
    }
    shoutInFlight = false;

    if (pendingShout !== null) {
      const queued = pendingShout;
      pendingShout = null;
      // Defer so this invocation finishes unwinding before the queued one starts.
      setTimeout(() => sendInstructionToAgent(queued), 0);
    } else {
      setShoutControls(true, "Shout!");
    }
  }
}

// 📟 Helper to parse and log agent events to the terminal in real-time
function processAgentEvent(event, options = {}) {
  const { showHuddle = true, run = {} } = options;

  // Log the raw event to the console for debugging A2A stream contents
  console.log("[Stream Event]", event);

  // Helper to append terminal line only if it's not a background request
  const printLine = (type, text) => {
    if (showHuddle) {
      appendTerminalLine(type, text);
    }
  };

  // 🔴 Handle backend errors (e.g., stale session) gracefully by invalidating the session ID
  if (event.error) {
    printLine("system", `> ❌ Session Error: ${event.error}`);
    // Nothing to invalidate: the next instruction opens a session of its own.
    return;
  }

  const author = event.author;
  const content = event.content;
  const actions = event.actions;

  // 1. Check for A2A Transfer (Coach -> Captain)
  if (author === "ManagerAgent" && actions && actions.transferToAgent === "team_captain") {
    printLine("coach", `🔗 Coach: Relayed to Team Captain over A2A!`);
    return;
  }

  // 2. Check for Captain delegating to specialists
  if (author === "TeamCaptain") {
    if (content && content.parts) {
      for (const part of content.parts) {
        if (part.functionCall) {
          const call = part.functionCall;
          const targetRole = call.name.replace("Specialist", "").toLowerCase();
          const instruction = call.args.instruction || "";
          printLine("captain", `🎛️ Captain: Delegating to ${targetRole.toUpperCase()} ➔ "${instruction}"`);
        }
      }
    }
  }

  // 3. Check for Specialist actions (updating profile or MCP)
  if (author && author.endsWith("Specialist")) {
    const role = author.replace("Specialist", "").toLowerCase();

    if (content && content.parts) {
      for (const part of content.parts) {
        // Tool Calls (update_profile or MCP)
        if (part.functionCall) {
          const call = part.functionCall;
          if (call.name === "update_profile") {
            const changes = JSON.stringify(call.args.changes);
            printLine(role, `🛡️ ${author}: Calling update_profile tool ➔ ${changes}`);
          } else if (call.name === "report_injury") {
            printLine(role, `⚠️ ${author} (MCP): Reported injury! Severity: "${call.args.severity || 'knock'}"`);
          } else if (call.name === "request_substitution") {
            printLine(role, `🔁 ${author} (MCP): Requested substitution! Reason: "${call.args.reason || 'tired'}"`);
          }
        }

        // Text responses (quirky quotes)
        if (part.text) {
          const text = part.text.trim();
          // Skip if it's the final JSON huddle
          if (!text.startsWith("{")) {
            printLine(role, `💬 ${author}: "${text}"`);
          }
        }
      }
    }
  }

  // 4. Check for final huddle JSON (exposing this to ANY author because the Coach
  //    relays the Captain's response, so the final event comes from ManagerAgent).
  if (content && content.parts) {
    for (const part of content.parts) {
      if (part.text) {
        const text = part.text.trim();
        if (text.startsWith("{") && text.includes('"huddle"')) {
          try {
            const parsed = JSON.parse(text);
            if (parsed.huddle) {
              run.huddle = parsed.huddle;
              printLine("captain", `📋 Captain: Huddle assembled!`);
              Object.entries(run.huddle).forEach(([player, quote]) => {
                printLine("system", `   └─ ${player.toUpperCase()}: "${quote}"`);
              });
            }
          } catch (e) {
            // Partial JSON
          }
        }
      }
    }
  }
}

// 📟 Append a styled line to the UI terminal
function appendTerminalLine(type, text) {
  const body = document.getElementById("terminal-body");
  if (!body) return;

  const line = document.createElement("div");
  line.className = `terminal-line line-${type}`;
  line.textContent = text;
  body.appendChild(line);

  // Auto-scroll to bottom
  body.scrollTop = body.scrollHeight;

  // Limit lines to 100 to prevent bloat
  while (body.children.length > 100) {
    body.removeChild(body.firstChild);
  }
}

function triggerShout() {
  const msg = shoutInput.value.trim();
  if (msg) {
    if (gameInstance) {
      const scene = gameInstance.scene.getScene('SoccerGameScene');
      if (scene && scene.gameActive) {
        Sound.playMenuClick();
        scene.showCoachShout(1, msg.toUpperCase());

        const lowerMsg = msg.toLowerCase();
        if (lowerMsg === 'shoot' || lowerMsg === 'kick') {
          scene.coachShoot(1);
        } else if (lowerMsg === 'jump' || lowerMsg === 'head') {
          scene.coachJump(1);
        } else if (lowerMsg === 'defend') {
          scene.coachDefend(1);
        } else if (lowerMsg === 'attack') {
          scene.coachAttack(1);
        }
      }
    }

    // Asynchronously call the agent
    sendInstructionToAgent(msg);
    shoutInput.value = '';
  }
}

shoutBtn.addEventListener('click', triggerShout);
shoutInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    triggerShout();
  }
});

function startPhaserGame() {
  if (gameInstance) return;

  const config = {
    type: Phaser.AUTO,
    width: 1408,
    height: 768,
    parent: 'phaser-container',
    physics: {
      default: 'arcade',
      arcade: {
        gravity: { y: 0 },
        debug: false
      }
    },
    // An instance rather than the class, because the role has to be settled
    // before create() runs a whistle and a clock. The lab always runs its own,
    // and there is no longer any other kind of pitch this page can be: the
    // grounds host a venue's matches on host.html and the wall draws them
    // through viewer.js.
    scene: [new SoccerGameScene({ role: 'host' })]
  };

  gameInstance = new Phaser.Game(config);
  window.__futsal = { status: createStatusHook(() => gameInstance) };

  gameInstance.events.once('ready', () => {
    const scene = gameInstance.scene.getScene('SoccerGameScene');
    if (scene) {
      applyProfilesToScene();
      nameTheManagers();
      const speedVal = parseFloat(document.getElementById('sim-speed-input').value);
      scene.setSimulationSpeed(speedVal);
    }
  });
}

// Listen for game-over event and update UI scoreboard overlay
window.addEventListener('soccer-game-over', (e) => {
  const { winnerMsg, winColor, score1, score2 } = e.detail;

  const winnerAnnouncement = document.getElementById('winner-announcement');
  winnerAnnouncement.textContent = winnerMsg;
  winnerAnnouncement.style.color = winColor;

  document.getElementById('final-p1-score').textContent = score1;
  document.getElementById('final-p2-score').textContent = score2;

  document.getElementById('game-over-screen').classList.add('active');
});

// ---- Player condition: fitness report + injury/substitution notifications ----

const ROLES = ['defender', 'midfielder', 'forward', 'goalkeeper'];

// Roles tire at slightly different rates (forwards/mids cover more ground).
const TIRE_RATE = { forward: 1.25, midfielder: 1.2, defender: 0.95, goalkeeper: 0.5 };

// The active scene, or null if no match is running.
function getActiveScene() {
  if (!gameInstance) return null;
  const scene = gameInstance.scene.getScene('SoccerGameScene');
  return scene && scene.gameActive ? scene : null;
}

// Build a short per-role tiredness note from match progress (matchTime counts
// down from 90s). No real stamina model - this just gives the player agents
// something to reason about so injuries/subs can emerge late in a match.
function getFitnessReport() {
  const scene = getActiveScene();
  const matchTime = scene ? scene.matchTime : GAME_DURATION_SEC;
  const progress = Math.min(1, Math.max(0, (GAME_DURATION_SEC - matchTime) / GAME_DURATION_SEC));

  const notes = ROLES.map(role => {
    const wear = progress * (TIRE_RATE[role] || 1) + Math.random() * 0.15;
    let level;
    if (wear < 0.45) level = 'fresh';
    else if (wear < 0.7) level = 'tiring';
    else if (wear < 0.95) level = 'very tired';
    else level = 'exhausted';
    return `${role}: ${level}`;
  });
  return `Fitness report (relay each player's condition note to them) - ${notes.join('; ')}.`;
}

// Periodically ask the team to self-report condition (autonomous injuries/subs),
// independent of coach shouts. Huddle bubbles are suppressed for these checks.
// Periodic status check interval (imported from game.js)
function runStatusCheck() {
  if (!getActiveScene()) return;
  sendInstructionToAgent(
    "STATUS CHECK: Players, do not change tactics. Only call your substitution or injury tool if you are clearly too tired or hurt, based on your fitness note.",
    { showHuddle: false }
  );
}

// The room as the arena last described it. Held here because it arrives on
// connect and Phaser takes a moment longer to boot than a socket does.
let seated = null;

function nameTheManagers() {
  const scene = gameInstance && gameInstance.scene.getScene('SoccerGameScene');
  if (scene && seated) scene.nameManagers(seated);
}

// The room's feed, listened to and never spoken on: no token is asked for, so
// the arena would refuse a frame from here anyway. Profile moves, shouts and
// knocks arrive on it. Open before anything is loaded so nothing said between
// the read and the connect is missed.
connect(room.code, {
  // Who is in the two dugouts, which is what the nameplates on the pitch say.
  onRoom: (message) => {
    seated = message;
    nameTheManagers();
  },
  onEvent: (message) => {
    if (message.kind === 'profile.patch') return applyPatch(message.payload);
    // A knock arrives on the same feed as everything else that happens in this
    // match, addressed to this room. It used to be a file this page polled,
    // which is how a knock in one match once toasted in another.
    if (message.kind === 'substitution') return showCondition(message.payload);
    const watching = gameInstance && gameInstance.scene.getScene('SoccerGameScene');
    if (watching) watching.cheer(message.kind);
  },
});

if (room.inMatch) {
  // Pointed at a venue's room rather than the workshop, which is a person
  // reading a match's squads and not the match itself: the grounds are playing
  // that one and the wall is drawing it. So the squads are read and left
  // exactly as the venue has them, and the pitch below is this page's own.
  loadProfiles().then(() => {
    document.getElementById('start-screen').classList.remove('active');
    startPhaserGame();
  });
} else {
  // The lab starts every session from the shipped squad, which is what makes
  // its stages repeatable. The workshop room is long-lived, so this has to be
  // asked for rather than assumed -- and it is why the branch above exists at
  // all: RESTORE_BASELINE into a room two managers are playing in would wipe
  // every shout they have made.
  console.log("--> [SYSTEM] Workshop: resetting to the shipped baseline...");
  sendInstructionToAgent("RESTORE_BASELINE", { showHuddle: false }).then(loadProfiles);
}

// The autonomous "are you tired?" chain, and the workshop alone. It fires
// about three times per three-minute match, and each one wakes a coach, a
// captain and four specialists. At fifty rooms that is hundreds of Gemini
// calls a minute nobody asked for, queued in front of the shouts managers
// actually typed. The workshop is long-lived and has an audience watching for
// exactly this kind of autonomous behaviour, so it keeps it.
if (shouldRunStatusCheck()) {
  setInterval(runStatusCheck, STATUS_CHECK_MS);
}

// 📟 Wire up terminal clear button
const terminalClearBtn = document.getElementById("terminal-clear");
if (terminalClearBtn) {
  terminalClearBtn.addEventListener("click", () => {
    const body = document.getElementById("terminal-body");
    if (body) {
      body.innerHTML = '<div class="terminal-line line-system">> Terminal cleared. Ready.</div>';
    }
  });
}

// Log initial state
appendTerminalLine("system", "> Simulator started. Outfield players running with default profiles.");

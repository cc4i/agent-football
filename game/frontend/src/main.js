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

let gameInstance = null;
let currentProfiles = {};

// Toggle to show/hide the "Debug logs" panel entirely. Set to false to remove
// the panel from the UI (it will not be rendered and no logs are collected).
const DEBUG_LOGS_ENABLED = true;
const MAX_DEBUG_ENTRIES = 200;

// Inject the premium automated dashboard HTML structure
document.querySelector('#app').innerHTML = `
  <div class="game-wrapper">
    <!-- Title Header -->
    <header class="game-header">
      <h1 class="neon-text">Worldcup Mania</h1>
      <p class="sub-title">Gemini-powered Agentic Football</p>
    </header>

    <!-- Simulation Speed Control -->
    <div class="sim-speed-bar">
      <div class="slider-header">
        <span class="slider-label" style="color: var(--gold-accent); font-weight: 800;">Simulation Speed</span>
        <span class="slider-val" id="val-sim-speed" style="color: #ffffff; font-weight: 800;">1.00x</span>
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
            <p>Welcome to the Automated Soccer Simulator Sandbox!</p>
            <p>Both teams play autonomously based on their behavioral attributes. Manually update individual files under the player_state/ folder on disk to tweak attributes, or adjust simulation speed to run experiments.</p>
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
        <span class="debug-log-hint">player config changes detected while polling player_state/*.json</span>
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

let lastFetchedTexts = {
  defender: "",
  midfielder: "",
  forward: "",
  goalkeeper: ""
};

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

// Fetch and load initial profiles from individual JSON files
async function loadProfiles() {
  try {
    const roles = ['defender', 'midfielder', 'forward', 'goalkeeper'];
    const fetched = await Promise.all(roles.map(async role => {
      const res = await fetch(`/player_state/${role}.json?t=` + Date.now());
      const text = await res.text();
      return { role, text, json: JSON.parse(text) };
    }));

    // Store last fetched texts and profiles
    fetched.forEach(({ role, text, json }) => {
      lastFetchedTexts[role] = text;
      currentProfiles[role] = json;
      // Log the initial values so the panel shows the starting config.
      appendDebugLog(role, diffProfile({}, json), 'initial load');
    });

    window.currentProfiles = currentProfiles; // Expose globally for Phaser

    // If the game scene is already running, update it
    if (gameInstance) {
      const scene = gameInstance.scene.getScene('SoccerGameScene');
      if (scene) {
        scene.updateBlueProfiles(currentProfiles);
      }
    }
  } catch (err) {
    console.error("Failed to load player profiles:", err);
  }
}

// Check individual JSON files on disk for changes
async function checkJSONForChanges() {
  try {
    const roles = ['defender', 'midfielder', 'forward', 'goalkeeper'];
    let changed = false;

    await Promise.all(roles.map(async role => {
      const res = await fetch(`/player_state/${role}.json?t=` + Date.now());
      const text = await res.text();
      if (text !== lastFetchedTexts[role]) {
        console.log(`Detected changes in player_state/${role}.json on disk. Updating simulation...`);
        const newProfile = JSON.parse(text);
        const changes = diffProfile(currentProfiles[role], newProfile);
        appendDebugLog(role, changes, 'applied');
        lastFetchedTexts[role] = text;
        currentProfiles[role] = newProfile;
        changed = true;
      }
    }));

    if (changed) {
      window.currentProfiles = currentProfiles;

      // If the game scene is already running, update it
      if (gameInstance) {
        const scene = gameInstance.scene.getScene('SoccerGameScene');
        if (scene) {
          scene.updateBlueProfiles(currentProfiles);
        }
      }
    }
  } catch (err) {
    console.error("Failed to fetch player profiles for update check:", err);
  }
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

  // Restore profiles to LAB01 starting baseline before restarting
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

let currentSessionId = null;
let currentHuddleData = null;
let isRequestInProgress = false;
let isBackgroundRequestInProgress = false;
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

  if (isRequestInProgress) {
    if (isBackground) {
      console.log("Skipping periodic status check: another request is in progress.");
      return;
    }
    // A background status check holds the lock for ~13s out of every 55s. Queue the
    // shout instead of dropping it, otherwise it disappears with no user-visible trace.
    if (isBackgroundRequestInProgress) {
      pendingShout = msg;
      appendTerminalLine("system", `> ⏳ Shout queued: "${msg}" — waiting for the status check to finish...`);
      setShoutControls(false, "Queued...");
      return;
    }
    console.warn("Request already in progress. Ignoring shout.");
    return;
  }

  isRequestInProgress = true;
  isBackgroundRequestInProgress = isBackground;
  if (!isBackground) {
    setShoutControls(false, "Thinking...");
  }

  currentHuddleData = null; // Reset for this run

  try {
    const outgoingMsg = `${msg}\n\n${getFitnessReport()}`;

    // Log initial trigger in terminal
    if (showHuddle) {
      appendTerminalLine("system", `> 📣 Coach shouted: "${msg}"`);
      appendTerminalLine("coach", `📣 Coach: "Relaying instruction to Team Captain over A2A (port 8001)..."`);
    } else {
      appendTerminalLine("system", `> 🤖 Running periodic status check...`);
    }

    // 1. Create a session if we don't have one
    if (!currentSessionId) {
      console.log("Creating new agent session...");
      const sessionRes = await fetch('/api-apps/agents/users/user/sessions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json, text/plain, */*'
        },
        body: JSON.stringify({
          state: {
            __session_metadata__: {
              displayName: "Football Coach Session"
            }
          }
        })
      });
      if (!sessionRes.ok) {
        throw new Error(`Failed to create session: ${sessionRes.statusText}`);
      }
      const sessionData = await sessionRes.json();
      currentSessionId = sessionData.id;
      console.log(`Agent session created successfully. Session ID: ${currentSessionId}`);
    }

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
        sessionId: currentSessionId,
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
            processAgentEvent(event, { showHuddle });
          } catch (err) {
            // Silent catch for partial or malformed chunks
          }
        }
      }
    }

    // Apply the accumulated huddle data at the end of the stream
    if (currentHuddleData && showHuddle) {
      console.log("Applying final huddle data to game:", currentHuddleData);
      if (gameInstance) {
        const scene = gameInstance.scene.getScene('SoccerGameScene');
        if (scene && scene.gameActive) {
          scene.showTeamHuddle(currentHuddleData);
        }
      }
    } else if (!currentHuddleData && showHuddle) {
      appendTerminalLine("system", `> ⚠️ No huddle response received from Team Captain.`);
    }
  } catch (err) {
    console.error("Error communicating with agent:", err);
    appendTerminalLine("system", `> ❌ Error: ${err.message}`);
  } finally {
    isRequestInProgress = false;
    isBackgroundRequestInProgress = false;

    if (pendingShout !== null) {
      const queued = pendingShout;
      pendingShout = null;
      // Defer so this invocation finishes unwinding before the queued one starts.
      setTimeout(() => sendInstructionToAgent(queued), 0);
    } else if (!isBackground) {
      setShoutControls(true, "Shout!");
    }
  }
}

// 📟 Helper to parse and log agent events to the terminal in real-time
function processAgentEvent(event, options = {}) {
  const { showHuddle = true } = options;

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
    console.warn("Session error detected. Invalidating currentSessionId.");
    currentSessionId = null; // Force a fresh session on the next shout
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
              currentHuddleData = parsed.huddle;
              printLine("captain", `📋 Captain: Huddle assembled!`);
              Object.entries(currentHuddleData).forEach(([player, quote]) => {
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
    scene: [SoccerGameScene]
  };

  gameInstance = new Phaser.Game(config);

  gameInstance.events.once('ready', () => {
    const scene = gameInstance.scene.getScene('SoccerGameScene');
    if (scene) {
      scene.updateBlueProfiles(currentProfiles);
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
// down from 90s). No real stamina model — this just gives the player agents
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
  return `Fitness report (relay each player's condition note to them) — ${notes.join('; ')}.`;
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

// ---- Substitution / injury notification toasts (top-right) ----

let notificationStack = null;
function ensureNotificationStack() {
  if (notificationStack) return notificationStack;
  notificationStack = document.createElement('div');
  notificationStack.id = 'notification-stack';
  document.body.appendChild(notificationStack);
  return notificationStack;
}

function showNotification(role, action, reason) {
  const stack = ensureNotificationStack();
  const isInjury = action === 'injury';
  const toast = document.createElement('div');
  toast.className = `pitch-toast ${isInjury ? 'toast-injury' : 'toast-sub'}`;
  const icon = isInjury ? '⚠️' : '🔁';
  const verb = isInjury ? 'reported an injury' : 'requested a substitution';
  const iconEl = document.createElement('span');
  iconEl.className = 'toast-icon';
  iconEl.textContent = icon;

  const textEl = document.createElement('span');
  textEl.className = 'toast-text';
  const strong = document.createElement('strong');
  strong.textContent = String(role).toUpperCase();
  textEl.append(strong, ` ${verb}`);
  if (reason) {
    const reasonEl = document.createElement('span');
    reasonEl.className = 'toast-reason';
    reasonEl.textContent = `(${reason})`;
    textEl.append(' ', reasonEl);
  }

  toast.append(iconEl, textEl);
  stack.appendChild(toast);
  setTimeout(() => toast.classList.add('toast-hide'), 5000);
  setTimeout(() => toast.remove(), 5600);
}

// Track the last-seen timestamp per role so each request shows exactly once.
const lastSubTs = { defender: 0, midfielder: 0, forward: 0, goalkeeper: 0 };

// Seed timestamps from any pre-existing file so stale entries don't toast on load.
async function primeSubstitutions() {
  try {
    const res = await fetch('/player_state/substitutions.json?t=' + Date.now());
    if (!res.ok) return;
    const data = await res.json();
    ROLES.forEach(role => {
      if (data[role] && data[role].ts) lastSubTs[role] = data[role].ts;
    });
  } catch (err) {
    // No file yet — nothing to prime.
  }
}

async function checkSubstitutions() {
  try {
    const res = await fetch('/player_state/substitutions.json?t=' + Date.now());
    if (!res.ok) return; // file may not exist yet
    const data = await res.json();
    ROLES.forEach(role => {
      const entry = data[role];
      if (entry && entry.ts && entry.ts > lastSubTs[role]) {
        lastSubTs[role] = entry.ts;
        console.log(`Player condition event: ${role} -> ${entry.action} (${entry.reason})`);
        showNotification(role, entry.action, entry.reason);
      }
    });
  } catch (err) {
    // Silent: a malformed/missing file is fine.
  }
}

// Load profiles initially on start (and handle baseline backup/restore)
const isInitialized = sessionStorage.getItem('lab02_initialized');

if (!isInitialized) {
  console.log("--> [SYSTEM] First load: backing up LAB01 baseline...");
  sendInstructionToAgent("BACKUP_BASELINE", { showHuddle: false }).then(() => {
    sessionStorage.setItem('lab02_initialized', 'true');
    loadProfiles();
  });
} else {
  console.log("--> [SYSTEM] Refresh detected: restoring LAB01 baseline...");
  sendInstructionToAgent("RESTORE_BASELINE", { showHuddle: false }).then(() => {
    loadProfiles();
  });
}

// Check for changes on disk every 2 seconds
setInterval(checkJSONForChanges, 2000);

// Poll for player condition events (injuries / sub requests) and toast them.
primeSubstitutions().then(() => setInterval(checkSubstitutions, 2000));

// Periodic team condition self-check so injuries/subs can happen autonomously.
setInterval(runStatusCheck, STATUS_CHECK_MS);

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

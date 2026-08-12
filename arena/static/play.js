/**
 * The dugout in your hand: the score, the shape of the match, and the relay.
 *
 * This page never advances physics and never scores anything. It reads the
 * room over one socket and posts instructions back over HTTP, which is what
 * lets the same code stand behind a preset chip today and a typed shout later.
 */

import { get, post, Refused } from "/static/api.js";
import { openRoom } from "/static/socket.js";

const CODE = (new URLSearchParams(location.search).get("room") || "").toUpperCase();
const ROLE_TAGS = { defender: "DEF", midfielder: "MID", forward: "FWD", goalkeeper: "GK" };
const SIDE_LABEL = { blue: "Blue", red: "Red" };

const el = (id) => document.getElementById(id);
const problem = el("problem");
const lobby = el("lobby");
const live = el("live");
const relay = el("relay");
const seats = el("seats");
const go = el("go");
const mini = el("mini");

let room = null;
let mine = null;      // the dugout this phone holds, or null
let lastSeq = 0;      // the log entry we have drawn up to
let shouting = false;
const said = new Map();  // shout seq -> the fan of branches it caused
const dots = [];

el("code").textContent = CODE || "····";

start();

async function start() {
  if (!CODE) return refuse("This address is missing its room. Scan the code on the screen.");
  try {
    const [snapshot, me, chips] = await Promise.all([
      get(`/api/rooms/${CODE}`),
      get(`/api/rooms/${CODE}/me`),
      get("/api/presets"),
    ]);
    mine = me.team;
    if (!mine) {
      return refuse("You have no dugout in this match. Scan the code on the screen to take one.");
    }
    drawChips(chips.presets);
    await catchUp();
    draw(snapshot);
    listen();
  } catch (failure) {
    complain(failure);
  }
}

function listen() {
  openRoom(CODE, {
    onMessage(message) {
      if (message.type === "room") return draw(message);
      if (message.type === "state") return paint(message);
      if (message.type === "event") return record(message);
    },
    // A reconnect can have missed events, and the relay is the one thing on
    // this page that is not re-sent on connect.
    onOpen: () => catchUp().catch(() => {}),
    onDrop(reason, permanent) {
      if (permanent) refuse(reason);
    },
  });
}

async function catchUp() {
  const { events } = await get(`/api/rooms/${CODE}/events?since=${lastSeq}`);
  for (const entry of events) record({ ...entry, type: "event" });
}

/* ── The room, and what it lets you do next ─────────────────────────── */

function draw(snapshot) {
  room = snapshot;
  const seat = snapshot.seats[mine];
  el("side").textContent = `${SIDE_LABEL[mine]}${seat && seat.ready ? " · ready" : ""}`;
  el("side").className = `side-chip ${mine === "blue" ? "b" : "r"}`;

  const started = snapshot.status !== "lobby";
  lobby.hidden = started;
  live.hidden = !started;
  el("ft").hidden = snapshot.status === "live";
  el("mini-tag").textContent = snapshot.status === "live" ? "LIVE" : "FULL TIME";
  el("composer").hidden = snapshot.status !== "live";
  if (!started) drawLobby(snapshot);
}

function drawLobby(snapshot) {
  const waiting = snapshot.mode === "solo"
    ? "The house side is already out there."
    : "Kick-off starts the moment both dugouts are ready.";
  el("lobby-sub").textContent = waiting;

  seats.replaceChildren(...["blue", "red"]
    .filter((team) => snapshot.mode === "versus" || team === "blue")
    .map((team) => {
      const seat = snapshot.seats[team];
      const row = document.createElement("div");
      row.className = `mrow${team === mine ? " you" : ""}`;
      const who = document.createElement("span");
      who.textContent = seat
        ? `${team === "blue" ? "🔵" : "🔴"} ${seat.name}${team === mine ? " (you)" : ""}`
        : `${team === "blue" ? "🔵" : "🔴"} Open`;
      const state = document.createElement("b");
      state.textContent = seat ? (seat.ready ? "Ready" : "Joining…") : "Waiting";
      row.append(who, state);
      return row;
    }));

  const seat = snapshot.seats[mine];
  const everyone = snapshot.open_seats.length === 0
    && Object.values(snapshot.seats).every((entry) => entry.ready);

  if (!seat || !seat.ready) {
    go.textContent = "I'm ready";
    go.disabled = false;
    go.dataset.does = "ready";
  } else if (everyone) {
    go.textContent = "Kick off";
    go.disabled = false;
    go.dataset.does = "start";
  } else {
    go.textContent = "Waiting for the other dugout";
    go.disabled = true;
    go.dataset.does = "";
  }
}

go.addEventListener("click", async () => {
  const does = go.dataset.does;
  if (!does) return;
  go.disabled = true;
  problem.hidden = true;
  try {
    if (does === "ready") {
      draw(await post(`/api/rooms/${CODE}/seats/${mine}/ready`, { ready: true }));
    } else {
      draw(await post(`/api/rooms/${CODE}/start`));
    }
  } catch (failure) {
    complain(failure);
    go.disabled = false;
  }
});

/* ── The match ──────────────────────────────────────────────────────── */

function paint(frame) {
  if (Array.isArray(frame.score)) {
    el("score-blue").textContent = frame.score[0];
    el("score-red").textContent = frame.score[1];
  }
  if (typeof frame.clock === "number") el("clock").textContent = mmss(frame.clock);
  place(frame);
}

function mmss(seconds) {
  const whole = Math.max(0, Math.round(seconds));
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}

function place(frame) {
  // Positions arrive as fractions of the pitch, so the same frame draws the
  // handset's thumbnail, a wall tile and a full-size viewer without rescaling.
  const spots = [
    ...(frame.blue || []).map((at) => ["b", at]),
    ...(frame.red || []).map((at) => ["r", at]),
    ...(frame.ball ? [["ball", frame.ball]] : []),
  ];
  while (dots.length < spots.length) {
    const dot = document.createElement("i");
    mini.append(dot);
    dots.push(dot);
  }
  dots.forEach((dot, index) => {
    const spot = spots[index];
    dot.hidden = !spot;
    if (!spot) return;
    const [side, [x, y]] = spot;
    dot.className = `dot ${side}`;
    dot.style.left = `${clamp(x) * 100}%`;
    dot.style.top = `${clamp(y) * 100}%`;
  });
}

const clamp = (value) => Math.min(1, Math.max(0, Number(value) || 0));

/* ── The relay ──────────────────────────────────────────────────────── */

function record(message) {
  if (message.seq) lastSeq = Math.max(lastSeq, message.seq);
  if (message.kind === "shout.sent") return drawShout(message);
  if (message.kind === "profile.patch") return drawPatch(message);
  if (message.kind === "goal") return drawGoal(message);
}

function drawShout(message) {
  if (said.has(message.seq)) return;
  const block = document.createElement("div");
  block.className = `said${message.payload.team === mine ? "" : " away"}`;
  const tag = document.createElement("span");
  tag.className = "tag";
  tag.textContent = message.payload.team === mine ? "You" : SIDE_LABEL[message.payload.team];
  const quote = document.createElement("q");
  quote.textContent = message.payload.text;
  block.append(tag, quote);

  const fan = document.createElement("div");
  fan.className = "fan";
  said.set(message.seq, fan);
  // Newest at the top: on a handset the thing you just did should not be the
  // thing you have to scroll to find.
  relay.prepend(block, fan);
  el("elapsed").textContent = `${said.size} shout${said.size === 1 ? "" : "s"}`;
}

function drawPatch(message) {
  const fan = said.get(message.payload.shout_seq);
  // Kick-off stances move the same profiles and belong to no shout. The squad
  // did not answer them, so the relay does not pretend they did.
  if (!fan) return;
  const branch = document.createElement("div");
  branch.className = "branch done";
  const role = document.createElement("span");
  role.className = "br-role";
  role.textContent = ROLE_TAGS[message.payload.role] || message.payload.role;
  const body = document.createElement("div");
  body.className = "br-body";
  const deltas = document.createElement("div");
  deltas.className = "deltas";
  for (const [attribute, value] of Object.entries(message.payload.changed || {})) {
    const delta = document.createElement("span");
    delta.className = "delta";
    delta.append(spaced(attribute), Object.assign(document.createElement("b"),
                                                  { textContent: round(value) }));
    deltas.append(delta);
  }
  body.append(deltas);
  branch.append(role, body);
  fan.append(branch);
}

function drawGoal(message) {
  const banner = document.createElement("div");
  banner.className = "banner warn";
  const team = message.payload && message.payload.team;
  const icon = document.createElement("span");
  icon.textContent = "⚽";
  const words = document.createElement("div");
  words.textContent = team === mine ? "You scored." : "They scored.";
  banner.append(icon, words);
  relay.prepend(banner);
}

// "pressingIntensity" reads as one long word on a 375px screen.
const spaced = (attribute) => `${attribute.replace(/([A-Z])/g, " $1").toLowerCase()} `;
const round = (value) => (typeof value === "number" ? String(Math.round(value * 100) / 100) : value);

/* ── The chips ──────────────────────────────────────────────────────── */

function drawChips(presets) {
  el("chips").replaceChildren(...presets.map((preset) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = `${preset.icon} ${preset.label}`;
    chip.addEventListener("click", () => shout(preset.name));
    return chip;
  }));
}

async function shout(name) {
  if (shouting) return;
  shouting = true;
  setChips(true);
  problem.hidden = true;
  try {
    await post(`/api/rooms/${CODE}/shout`, { preset: name });
  } catch (failure) {
    complain(failure);
  } finally {
    shouting = false;
    setChips(false);
  }
}

function setChips(busy) {
  for (const chip of el("chips").children) chip.disabled = busy;
}

/* ── Saying what went wrong ─────────────────────────────────────────── */

function complain(failure) {
  if (!(failure instanceof Refused)) throw failure;
  if (failure.status === 401) {
    return refuse("Your phone has no session here. Scan the code on the screen to join.");
  }
  problem.textContent = failure.message;
  problem.hidden = false;
}

function refuse(message) {
  problem.textContent = message;
  problem.hidden = false;
  lobby.hidden = true;
  live.hidden = true;
}

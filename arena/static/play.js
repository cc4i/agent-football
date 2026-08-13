/**
 * The dugout in your hand: the score, the shape of the match, and the relay.
 *
 * This page never advances physics and never scores anything. It reads the
 * room over one socket and posts instructions back over HTTP, which is what
 * lets the same code stand behind a preset chip today and a typed shout later.
 */

import { get, post, Refused } from "/static/api.js";
import { icon } from "/static/dom.js";
import { openRoom } from "/static/socket.js";

const CODE = (new URLSearchParams(location.search).get("room") || "").toUpperCase();
const ROLE_TAGS = { defender: "DEF", midfielder: "MID", forward: "FWD", goalkeeper: "GK" };
// Front to back, which is the order a manager reads a squad in.
const ROLES = ["forward", "midfielder", "defender", "goalkeeper"];
const SIDE_LABEL = { blue: "Blue", red: "Red" };
const OUTCOME = { won: "won", drew: "drew", lost: "lost" };
const MEDALS = ["🥇", "🥈", "🥉"];
// The trunk of the chain, top to bottom, with what each hop says before it has
// anything of its own to report.
const RUNGS = [
  ["coach", "Coach", "Relaying over A2A"],
  ["captain", "Captain", "Waiting on the coach"],
  ["squad", "Squad", "Waiting on the brief"],
];

const el = (id) => document.getElementById(id);
const problem = el("problem");
const lobby = el("lobby");
const live = el("live");
const sheet = el("result");
const relay = el("relay");
const seats = el("seats");
const go = el("go");
const mini = el("mini");
const box = el("shout");

let room = null;
let mine = null;      // the dugout this phone holds, or null
let lastSeq = 0;      // the log entry we have drawn up to
let shouting = false; // a shout of ours is on its way to the arena
let held = 0;         // the shout the notice under the composer is about
let crowded = false;  // the banner is holding a refusal about the queue
let earned = null;    // the arena's word on what this match was worth
let asking = false;   // that word is on its way
const shouts = new Map();  // shout seq -> the block of the relay it owns
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
      if (String(message.type).startsWith("relay.")) return relayed(message);
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
  const started = snapshot.status !== "lobby";
  const over = started && snapshot.status !== "live";
  const abandoned = snapshot.status === "abandoned";
  // "Ready" only means something while there is still something to be ready
  // for; once the whistle has gone the chip says what is happening instead.
  const note = started ? "" : (seat && seat.ready ? " · ready" : "");
  // The result screen puts the scoreline in the chip, and it knows the
  // scoreline; until then the chip is just the side this phone is holding.
  if (sheet.hidden) el("side").textContent = `${SIDE_LABEL[mine]}${note}`;
  el("side").className = `side-chip ${mine === "blue" ? "b" : "r"}`;

  lobby.hidden = started;
  live.hidden = !started || !sheet.hidden;
  el("ft").hidden = !over;
  el("ft").textContent = abandoned ? "Abandoned" : "Full time";
  el("mini-tag").textContent = snapshot.status === "live" ? "LIVE"
    : abandoned ? "ABANDONED" : "FULL TIME";
  el("composer").hidden = snapshot.status !== "live";
  if (!started) drawLobby(snapshot);
  if (over) settle();
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
      who.append(
        icon(team === "blue" ? "🔵" : "🔴"),
        seat ? `${seat.name}${team === mine ? " (you)" : ""}` : "Open",
      );
      const state = document.createElement("b");
      state.textContent = seat ? (seat.ready ? "Ready" : "Not ready") : "Waiting";
      row.append(who, state);
      return row;
    }));

  const seat = snapshot.seats[mine];
  const everyone = snapshot.open_seats.length === 0
    && Object.values(snapshot.seats).every((entry) => entry.ready);

  if (!seat || !seat.ready) {
    // Solo has nobody to wait for, so the heading must not claim there is.
    el("lobby-title").textContent = snapshot.mode === "solo"
      ? "Ready when you are"
      : "Ready when they are";
    go.textContent = "I'm ready";
    go.disabled = false;
    go.dataset.does = "ready";
  } else if (everyone) {
    el("lobby-title").textContent = "Kick off when you like";
    go.textContent = "Kick off";
    go.disabled = false;
    go.dataset.does = "start";
  } else {
    el("lobby-title").textContent = "Waiting on the other dugout";
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
  if (Array.isArray(frame.score)) showScore(frame.score);
  if (typeof frame.clock === "number") el("clock").textContent = mmss(frame.clock);
  place(frame);
}

function showScore([blue, red]) {
  el("score-blue").textContent = blue;
  el("score-red").textContent = red;
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
  // Frames stop the moment the match does. A phone that reloads at full time
  // has nothing left to paint from, so the scoreline comes out of the log.
  const payload = message.payload || {};
  if (Array.isArray(payload.score)) showScore(payload.score);
  if (message.kind === "full_time") el("clock").textContent = mmss(0);
  if (message.kind === "shout.sent") return drawShout(message);
  if (message.kind === "profile.patch") return drawPatch(message);
  if (message.kind === "goal") return drawGoal(message);
}

function drawShout(message) {
  if (shouts.has(message.seq)) return;
  const said = document.createElement("div");
  said.className = `said${message.payload.team === mine ? "" : " away"}`;
  const tag = document.createElement("span");
  tag.className = "tag";
  tag.textContent = message.payload.team === mine ? "You" : SIDE_LABEL[message.payload.team];
  const quote = document.createElement("q");
  quote.textContent = message.payload.text;
  said.append(tag, quote);

  const fan = document.createElement("div");
  fan.className = "fan";
  shouts.set(message.seq, { said, fan, chain: null, rungs: {}, branches: new Map() });
  // Newest at the top: on a handset the thing you just did should not be the
  // thing you have to scroll to find.
  relay.prepend(said, fan);
  el("elapsed").textContent = `${shouts.size} shout${shouts.size === 1 ? "" : "s"}`;
}

function drawPatch(message) {
  const block = shouts.get(message.payload.shout_seq);
  // Kick-off stances move the same profiles and belong to no shout. The squad
  // did not answer them, so the relay does not pretend they did.
  if (!block) return;
  const branch = branchFor(block, message.payload.role);
  // A chip and a shout replayed out of the log have no chain to light the
  // branch, so the write itself is the answer. A live chain lights its own.
  if (!branch.state) mark(block, message.payload.role, "done", "");
  branch.body.append(deltas(message.payload.changed));
}

function deltas(changed) {
  const row = document.createElement("div");
  row.className = "deltas";
  for (const [attribute, value] of Object.entries(changed || {})) {
    const delta = document.createElement("span");
    delta.className = "delta";
    delta.append(spaced(attribute), Object.assign(document.createElement("b"),
                                                  { textContent: round(value) }));
    row.append(delta);
  }
  return row;
}

function drawGoal(message) {
  const banner = document.createElement("div");
  banner.className = "banner warn";
  const team = message.payload && message.payload.team;
  const words = document.createElement("div");
  words.textContent = team === mine ? "You scored." : "They scored.";
  banner.append(icon("⚽"), words);
  relay.prepend(banner);
}

// "pressingIntensity" reads as one long word on a 375px screen.
const spaced = (attribute) => `${attribute.replace(/([A-Z])/g, " $1").toLowerCase()} `;
const round = (value) => (typeof value === "number" ? String(Math.round(value * 100) / 100) : value);

/* ── The chain a shout travels down ─────────────────────────────────── */

function relayed(message) {
  const block = shouts.get(message.seq);
  if (!block) return;
  if (message.type === "relay.waiting") {
    return queue(message.team, message.seq, `The venue is busy - ${message.ahead} ahead of you`);
  }
  // This shout is moving, so whatever the composer was saying about it is over.
  queue(message.team, message.seq, "");
  wire(block);

  if (message.type === "relay.coach") {
    return rung(block, "coach", message.state === "done" ? "done" : "live",
                message.state === "done" ? "Handed to the captain" : "Relaying over A2A");
  }
  if (message.type === "relay.captain") {
    if (message.state !== "thinking") return rung(block, "captain", "done", "Briefed the squad");
    rung(block, "coach", "done", "Handed to the captain");
    rung(block, "captain", "live", "Briefing the squad");
    // The four go out together, so they all start waiting together and the
    // branches exist to be lit before any of them has said anything.
    for (const role of ROLES) mark(block, role, "live", "");
    return tally(block, false);
  }
  if (message.type === "relay.specialist") {
    mark(block, message.role, message.state, message.text || "");
    return tally(block, false);
  }
  if (message.type === "relay.trouble") {
    // The signal can die at any hop, so the trouble lands on whichever one was
    // still carrying it rather than always on the coach.
    const stuck = RUNGS.map(([name]) => name).find((name) => block.rungs[name].state !== "done");
    return rung(block, stuck || "squad", "failed", message.text);
  }
  if (message.type === "relay.huddle") {
    // A chain of ours ending frees a slot, so a refusal about the queue has
    // been overtaken by events and comes down with it.
    if (crowded && message.team === mine) {
      crowded = false;
      problem.hidden = true;
    }
    for (const [role, line] of Object.entries(message.huddle || {})) mark(block, role, "done", line);
    // Whatever is still lit when the chain ends never answered. A chain that
    // died before the brief went out has no branches, and inventing four
    // silent players would blame the squad for something upstream of them.
    for (const role of ROLES) if (block.branches.has(role)) mark(block, role, "missing", "");
    if (message.state === "done") {
      rung(block, "coach", "done", "Handed to the captain");
      rung(block, "captain", "done", message.status || "Briefed the squad");
    }
    tally(block, true);
  }
}

function wire(block) {
  // Built when the first word of a chain arrives rather than when the shout is
  // drawn. A shout replayed out of the log after a reload has no chain to
  // replay: what the squad did is logged and shows, what it said on the way is
  // a progress report for whoever was watching at the time.
  if (block.chain) return;
  const chain = document.createElement("ol");
  chain.className = "chain";
  for (const [name, who, waiting] of RUNGS) {
    const item = document.createElement("li");
    item.className = "rung";
    const row = document.createElement("div");
    row.className = "rung-row";
    const label = document.createElement("span");
    label.className = "rung-who";
    label.textContent = who;
    const note = document.createElement("span");
    note.className = "rung-what";
    note.textContent = waiting;
    row.append(label, note);
    item.append(row);
    chain.append(item);
    block.rungs[name] = { item, note, state: "" };
  }
  // The fan moves inside the last rung, so the four branches hang off the same
  // wire the coach and the captain sit on. That fan-out is real: the captain
  // briefs a ParallelAgent.
  block.rungs.squad.item.append(block.fan);
  block.said.after(chain);
  block.chain = chain;
}

function rung(block, name, state, what) {
  const found = block.rungs[name];
  // A hop that failed stays failed, and keeps saying why: a later message
  // about the rest of the chain must not paper over where it broke.
  if (!found || found.state === "failed") return;
  found.state = state;
  found.item.className = `rung ${state}`;
  found.note.textContent = what;
}

function branchFor(block, role) {
  let found = block.branches.get(role);
  if (found) return found;
  const branch = document.createElement("div");
  branch.className = "branch";
  const tag = document.createElement("span");
  tag.className = "br-role";
  tag.textContent = ROLE_TAGS[role] || role;
  const body = document.createElement("div");
  body.className = "br-body";
  branch.append(tag, body);
  block.fan.append(branch);
  found = { branch, body, line: null, state: "" };
  block.branches.set(role, found);
  return found;
}

function mark(block, role, state, words) {
  const found = branchFor(block, role);
  // A player who has answered is not un-answered by anything that lands after,
  // and their own words beat the captain's summary of them.
  if (found.state === "done") return;
  found.state = state;
  found.branch.className = `branch ${state}`;
  if (state === "done" && !words) return;   // a chip's branch: what moved is the answer
  const line = document.createElement(words ? "q" : "span");
  if (!words) line.className = "br-wait";
  line.textContent = words || (state === "missing" ? "no answer" : "thinking");
  if (found.line) found.line.replaceWith(line);
  else found.body.prepend(line);
  found.line = line;
}

function tally(block, ended) {
  const answered = [...block.branches.values()].filter((one) => one.state === "done").length;
  // The huddle completes on three, so a chain that ended with one player quiet
  // is done rather than failed.
  rung(block, "squad", ended ? (answered ? "done" : "failed") : "live",
       ended ? `${answered} of 4 answered`
             : answered ? `${answered} of 4 in`
                        : "Four players, in parallel");
}

/* ── Saying something ───────────────────────────────────────────────── */

function drawChips(presets) {
  el("chips").replaceChildren(...presets.map((preset) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.append(icon(preset.icon), preset.label);
    chip.addEventListener("click", () => shout({ preset: preset.name }));
    return chip;
  }));
}

el("send").addEventListener("click", () => shout({ text: box.value }));
box.addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  shout({ text: box.value });
});

async function shout(body) {
  // A chip is one word from the arena's own catalogue; words are whatever the
  // manager typed, and an empty box is a mis-tap rather than an instruction.
  if (shouting || (body.text !== undefined && !body.text.trim())) return;
  shouting = true;
  setComposer(true);
  problem.hidden = true;
  try {
    const said = await post(`/api/rooms/${CODE}/shout`, body);
    if (body.text !== undefined) box.value = "";
    // The chain behind this one is still going out, so the composer says so
    // rather than leaving a manager wondering whether the tap registered.
    if (said.ahead) queue(mine, said.seq, "Your last call goes out when the squad is back in");
  } catch (failure) {
    complain(failure);
  } finally {
    shouting = false;
    setComposer(false);
  }
}

function setComposer(busy) {
  for (const chip of el("chips").children) chip.disabled = busy;
  box.disabled = busy;
  el("send").disabled = busy;
}

// The notice is tied to one shout, so another shout's progress cannot clear a
// line that was not describing it.
function queue(team, seq, words) {
  if (team !== mine) return;
  const notice = el("queued");
  if (words) {
    held = seq;
    notice.replaceChildren(icon("⏳"),
                           Object.assign(document.createElement("span"), { textContent: words }));
    notice.hidden = false;
    return;
  }
  if (seq !== held) return;
  held = 0;
  notice.hidden = true;
}

/* ── Full time ──────────────────────────────────────────────────────── */

/**
 * Ask the arena what the match was worth, once, and show it.
 *
 * The phone computes nothing here. Every number and every line of the
 * breakdown was worked out on the server from this room's own log at the
 * whistle and stored, so a manager who reloads this screen sees the same total
 * they saw the first time.
 */
async function settle() {
  if (earned || asking) return;
  asking = true;
  try {
    earned = await get(`/api/rooms/${CODE}/result`);
  } catch (failure) {
    // The scoreline and the relay are still behind this, so a result that will
    // not load is a missing panel rather than a dead page.
    return complain(failure);
  } finally {
    asking = false;
  }
  const yours = earned.results[mine];
  // An abandoned match was scored for nobody. The live view stays up with the
  // badge saying what happened, because there is no sheet to put over it.
  if (yours) drawResult(earned, yours);
}

function drawResult(result, yours) {
  el("side").textContent =
    `${SIDE_LABEL[mine]} · ${OUTCOME[yours.outcome]} ${yours.goals_for}-${yours.goals_against}`;
  el("r-mode").textContent = result.mode === "solo" ? "Score attack" : "Head to head";
  el("r-points").textContent = figure(yours.points);
  el("r-rank").textContent = standing(result, yours);
  el("r-breakdown").replaceChildren(...yours.breakdown.map(brow));
  el("r-top").replaceChildren(
    ...result.top.map((row, place) => leader(row, place, result.mode, yours.player_id)));
  el("r-top").hidden = result.top.length === 0;
  el("r-hint").textContent = aside(result, yours);

  live.hidden = true;
  sheet.hidden = false;
}

function standing(result, yours) {
  // The workshop and anything the host ran fast still earn a breakdown, because
  // what a practice run was worth is worth reading. They just rank nowhere, and
  // saying so is kinder than leaving the line blank.
  if (!result.ranked) return "A practice run - it earns points but no place on the board";
  const where = result.standing[mine];
  if (!where) return "";
  const best = result.mode !== "solo" ? ""
    : where.best ? " · new personal best" : " · your best run still stands";
  const rating = yours.rating === null ? "" : ` · rating ${Math.round(yours.rating)}`;
  return `${ordinal(where.rank)} of ${where.of}${best}${rating}`;
}

function brow(row) {
  const line = document.createElement("div");
  line.className = `brow ${row.points > 0 ? "plus" : row.points < 0 ? "minus" : "zero"}`;
  line.append(Object.assign(document.createElement("span"), { textContent: row.label }),
              Object.assign(document.createElement("b"), { textContent: signed(row.points) }));
  return line;
}

function leader(row, place, mode, you) {
  const line = document.createElement("div");
  const yours = row.player_id === you;
  line.className = `mrow${yours ? " you" : ""}`;
  const who = document.createElement("span");
  who.append(icon(MEDALS[place]), `${row.name}${yours ? " (you)" : ""}`);
  const score = document.createElement("b");
  // The head to head board is not ranked on points, so showing a total beside
  // a name there would be showing the wrong number.
  score.textContent = mode === "solo" ? figure(row.points)
                                      : `${row.won}-${row.drew}-${row.lost}`;
  line.append(who, score);
  return line;
}

function aside(result, yours) {
  const rule = result.mode === "solo"
    ? "Only your best run counts - scan the code on the screen for another go."
    : "Rating is shown but does not sort the board: one match is not a rating.";
  return `${rule} ${shoutsAside(yours)}`;
}

function shoutsAside(yours) {
  if (!yours.shouts) {
    return "You made no calls. A shout followed by a goal inside 45 seconds is worth 100.";
  }
  const landed = `${yours.shouts} shout${yours.shouts === 1 ? "" : "s"}`;
  if (!yours.effective) return `${landed} landed, none followed by a goal inside 45 seconds.`;
  if (yours.shouts === 1) return "Your one shout was followed by a goal inside 45 seconds.";
  return `${landed} landed, ${yours.effective} followed by a goal inside 45 seconds.`;
}

const figure = (points) => points.toLocaleString("en-GB");
const signed = (points) =>
  (points ? `${points > 0 ? "+" : "-"}${figure(Math.abs(points))}` : "0");

function ordinal(place) {
  // 11th, 12th and 13th, which the last-digit rule below gets wrong.
  const teens = place % 100;
  if (teens >= 11 && teens <= 13) return `${place}th`;
  return `${place}${["th", "st", "nd", "rd"][place % 10] || "th"}`;
}

/* ── Saying what went wrong ─────────────────────────────────────────── */

function complain(failure) {
  if (!(failure instanceof Refused)) throw failure;
  if (failure.status === 401) {
    return refuse("Your phone has no session here. Scan the code on the screen to join.");
  }
  // A refusal about the queue stops being true the moment the squad is back
  // in, so it is remembered here and cleared there rather than sitting at the
  // top of the screen saying something that has stopped being so.
  crowded = failure.status === 429;
  problem.textContent = failure.message;
  problem.hidden = false;
}

function refuse(message) {
  problem.textContent = message;
  problem.hidden = false;
  lobby.hidden = true;
  live.hidden = true;
}

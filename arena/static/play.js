/**
 * The dugout in your hand: the score, the shape of the match, and the relay.
 *
 * This page never advances physics and never scores anything. It reads the
 * room over one socket and posts instructions back over HTTP, which is what
 * lets the same code stand behind a preset chip today and a typed shout later.
 */

import { get, post, Refused } from "/static/api.js";
import { icon } from "/static/dom.js";
import { relayFeed } from "/static/relay.js";
import { openRoom, screenToken } from "/static/socket.js";
import { figure, ordinal } from "/static/words.js";

const CODE = (new URLSearchParams(location.search).get("room") || "").toUpperCase();
const SIDE_LABEL = { blue: "Blue", red: "Red" };
const OUTCOME = { won: "won", drew: "drew", lost: "lost" };
const MEDALS = ["🥇", "🥈", "🥉"];

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
let painted = false;  // a frame from the host has been on this screen
let read = false;     // the log has been read since the match ended
const dots = [];
// Both dugouts in the one feed: a manager wants to see what they are up
// against, and on a handset there is no room for a second column of it. Opened
// once /me has said which of the two is theirs, since that is what the feed
// tags as "You".
let feed = null;

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
    feed = relayFeed({
      into: relay,
      mine,
      onQueued: queue,
      onHuddle: freed,
      onCount: (drawn) => {
        el("elapsed").textContent = `${drawn} shout${drawn === 1 ? "" : "s"}`;
      },
    });
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
    // The token, if this phone is the one that opened the room. Between sitting
    // down and kicking off there is nobody else behind a phone's room -- no
    // screen, and no grounds until the whistle -- so without this the sweep
    // gives up on it while its manager is deciding whether to press Ready. A
    // phone that walked into somebody else's room holds no token, passes the
    // empty string, and is the viewer it has always been.
    clientId: screenToken(CODE),
    onMessage(message) {
      if (message.type === "room") return draw(message);
      if (message.type === "state") return paint(message);
      if (message.type === "event") return record(message);
      if (String(message.type).startsWith("relay.")) return feed.relayed(message);
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
  // Three states, not two. A room can end without ever having been played -
  // its screen closes while it is still waiting for a manager - and the live
  // view over a 0-0 scoreline and an empty pitch would be describing a match
  // that nobody had. That one keeps the lobby view, which then says it closed.
  const started = snapshot.started;
  const ended = snapshot.status !== "lobby" && snapshot.status !== "live";
  const over = started && ended;
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
  el("ft").className = abandoned ? "ft-badge gone" : "ft-badge";
  el("mini-tag").textContent = snapshot.status === "live" ? "LIVE"
    : abandoned ? "ABANDONED" : "FULL TIME";
  el("composer").hidden = snapshot.status !== "live";
  // The composer goes when the match does, and an empty relay saying "make a
  // call below" would then be pointing at a box that is no longer there. A
  // room never comes back to life, so this is never put back.
  if (over) relay.dataset.empty = "Nothing was said in this one.";
  // Frames are never stored, so a phone opened after an abandonment has no
  // clock to show and the three minutes it was born with is not one: that
  // reads as a match that never kicked off. Full time has 00:00 from the log.
  if (abandoned && !painted) el("clock").textContent = "--:--";
  if (!started) drawLobby(snapshot);
  if (over) settle();
  // The snapshot says a room is over; the log says why. Those can arrive by
  // different routes, because the arena that gave up on a room is not
  // necessarily the one holding this socket, and only the one holding this
  // socket can publish to it. So the ending is taken as the cue to read the
  // log rather than as one more thing waiting to be delivered.
  if (ended && !read) {
    read = true;
    catchUp().catch(() => {});
  }
}

function drawLobby(snapshot) {
  const shut = snapshot.status !== "lobby";

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
      // Nothing is pending in a room that has shut, so neither is anybody's
      // readiness: "Not ready" there reads as something still to be done.
      state.textContent = shut ? "" : seat ? (seat.ready ? "Ready" : "Not ready") : "Waiting";
      row.append(who, state);
      return row;
    }));

  // A room that closed before anything was played in it. The banner above has
  // said why; this is the rest of the page agreeing with it rather than going
  // on offering a whistle to blow, a screen to watch and a score to wait for.
  // The seats stay, because they are the last true thing on it and a manager
  // wants to see they were in there.
  if (shut) {
    el("lobby-title").textContent = "This room closed";
    el("lobby-sub").textContent = "Nothing was ever played in it.";
    el("how").hidden = true;
    go.hidden = true;
    go.dataset.does = "";
    el("elsewhere").hidden = false;
    return;
  }

  // Nothing kicks off on its own -- somebody presses the button -- so the line
  // under the heading must not promise a whistle that never comes.
  el("lobby-sub").textContent = snapshot.mode === "solo"
    ? "The house side is already out there."
    : "Once both dugouts are ready, either of you can kick off.";

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
  painted = true;
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
  // A match that stopped without a whistle owes both dugouts an explanation.
  // It comes out of the log rather than the socket, so a phone that reloads
  // afterwards is told the same thing as one that was watching at the time.
  if (message.kind === "abandoned") {
    problem.textContent = payload.reason || "This match was abandoned.";
    problem.hidden = false;
  }
  feed.event(message);
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

// A chain of ours ending frees a slot, so a refusal about the queue has been
// overtaken by events and comes down with it.
function freed(team) {
  if (!crowded || team !== mine) return;
  crowded = false;
  problem.hidden = true;
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

const signed = (points) =>
  (points ? `${points > 0 ? "+" : "-"}${figure(Math.abs(points))}` : "0");

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

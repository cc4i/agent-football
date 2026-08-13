/**
 * The big screen: centre court, the wall, and the director between them.
 *
 * The screen opens a room, so the screen holds that room's physics token and
 * the pitch it frames is the host. That is the rule stated by pointing:
 * whoever scanned the screen owns the screen.
 *
 * Everything else in the venue is watched rather than run. The wall carries a
 * tile per live room off one socket, and centre court frames whichever of them
 * the director has chosen -- as a viewer, so nothing on this screen ever
 * advances a match somebody else is playing.
 */

import { get, post, Refused } from "/static/api.js";
import { relayFeed } from "/static/relay.js";
import { openRoom, openWall } from "/static/socket.js";

const el = (id) => document.getElementById(id);
const problem = el("problem");
const lobby = el("lobby");
const court = el("court");

const params = new URLSearchParams(location.search);
// Solo is the default because the house side is always available and a second
// manager is not. `?mode=versus` opens a room with a red dugout to take.
const MODE = params.get("mode") === "versus" ? "versus" : "solo";

const HOUSE = "The house side";
// Six tiles fit across a wall-mounted screen at a size you can read from the
// back of a room. A seventh match rotates through rather than shrinking them.
const TILES = 6;
const ROTATE_MS = 12000;
// The least time a match holds centre court. Without it, two rooms trading
// goals would strobe the screen between them.
const DWELL_MS = 8000;
const GOAL_HEAT_MS = 10000;
const ENDGAME_SEC = 30;
const TICK_MS = 2000;
// A room stays "live" in the arena until somebody blows the whistle on it, and
// a screen that was closed mid-match never does. Frames are the only proof a
// match is still being played, so a room that has stopped sending them stops
// being on now -- and comes back the moment one arrives.
const STALE_MS = 15000;
// Less rope for one that has never sent a frame at all: a match sends ten a
// second, so silence from a room the arena has just named as live means its
// screen is gone rather than that it is between frames.
const SILENT_MS = 4000;

let code = (params.get("room") || "").toUpperCase();
let venue = { pitch_url: "" };
let ours = null;            // this screen's own room, as the arena last told us
// The room framed on centre court, or null for none. Undefined until the first
// pass of the director, so that "nothing is on" is itself something to draw.
let showing;
let pinned = null;          // the operator's choice, which outlives the director's
let framedAt = 0;
let turned = 0;             // the first tile of the strip's current page
let stripped = "";          // the page the strip is drawing, so it is redrawn once
let courtRoom = null;       // somebody else's room, open only while they are on
const live = new Map();     // code -> what the wall knows about that room
const tiles = new Map();    // code -> the strip's elements for it

const feeds = {
  blue: relayFeed({ into: el("relay-blue"), only: "blue", goals: false }),
  red: relayFeed({ into: el("relay-red"), only: "red", goals: false }),
};

start();

async function start() {
  try {
    venue = await get("/api/venue");
    ours = code ? await get(`/api/rooms/${code}`) : await open();
    code = ours.code;
    dress(ours);
    // Our own room's socket stays open whatever is on the screen: it is how the
    // lobby learns a seat filled and how the whistle gets here. While our own
    // match is the one on centre court it carries the relay too, so the usual
    // case is one room socket rather than two.
    openRoom(code, {
      onMessage(message) {
        if (message.type === "room") return mine(message);
        if (showing === code) courtside(message);
      },
      onOpen: () => showing === code && replay(code),
      onDrop: (reason, permanent) => permanent && say(reason),
    });
    openWall({ onMessage: wall });
    setInterval(direct, TICK_MS);
    direct();
  } catch (failure) {
    if (!(failure instanceof Refused)) throw failure;
    say(failure.message);
  }
}

async function open() {
  const opened = await post("/api/rooms", { mode: MODE });
  // The token is the room's physics, and it exists in exactly two places: the
  // arena's database and this tab. sessionStorage rather than localStorage so
  // closing the tab gives it up, and reloading does not.
  sessionStorage.setItem(tokenKey(opened.code), opened.host_token);
  history.replaceState(null, "", `/arena?room=${opened.code}`);
  return opened;
}

const tokenKey = (roomCode) => `arena.host.${roomCode}`;
const hostToken = () => sessionStorage.getItem(tokenKey(code)) || "";
/** Whether this tab is the one advancing its own room's physics right now. */
const hostingLive = () => Boolean(ours && ours.status === "live" && hostToken());

/* ── This screen's own room ─────────────────────────────────────────── */

function dress(snapshot) {
  el("code").textContent = snapshot.code;
  el("code-2").textContent = snapshot.code;
  el("code-3").textContent = snapshot.code;
  el("join-url").textContent = snapshot.join_url;
  el("where").textContent = snapshot.mode === "solo"
    ? "Solo · against the house side" : "Head to head";
  el("board").src = "/board";

  for (const holder of ["qr", "qr-mini"]) {
    const qr = document.createElement("img");
    qr.src = `/api/rooms/${snapshot.code}/qr.svg`;
    qr.alt = `QR code for room ${snapshot.code}`;
    el(holder).replaceChildren(qr);
  }

  const hosting = Boolean(hostToken());
  el("role").className = `role-chip${hosting ? "" : " viewer"}`;
  el("role").replaceChildren(document.createElement("i"),
                             document.createTextNode(hosting ? "Hosting" : "Watching"));
}

function mine(snapshot) {
  ours = snapshot;
  el("again").hidden = snapshot.status !== "finished";
  drawSeats(snapshot);
  direct();
}

function drawSeats(snapshot) {
  const teams = snapshot.mode === "solo" ? ["blue"] : ["blue", "red"];
  const taken = teams.filter((team) => snapshot.seats[team]).length;
  el("badge").textContent = snapshot.status === "finished"
    ? "Full time. Open a new room to play again."
    : taken === teams.length
      ? "Ready when they are"
      : `Waiting for ${teams.length - taken === 1 ? "a manager" : "two managers"}`;

  const cards = [];
  teams.forEach((team, index) => {
    if (index) cards.push(text("span", "vs-mark", "vs"));
    cards.push(seatCard(team, snapshot.seats[team]));
  });
  if (snapshot.mode === "solo") {
    cards.push(text("span", "vs-mark", "vs"), houseCard());
  }
  el("seats").replaceChildren(...cards);
}

function seatCard(team, seat) {
  const card = document.createElement("div");
  const side = team === "blue" ? "b" : "r";
  card.className = `seat ${side} ${seat ? "filled" : "open"}`;
  card.append(text("div", "side", team === "blue" ? "Blue dugout" : "Red dugout"));
  if (!seat) {
    card.append(text("div", "open-label", "Scan to take it"));
    return card;
  }
  card.append(text("div", "name", seat.name), text("div", "sub", seat.philosophy));
  card.append(text("div", `ready${seat.ready ? "" : " waiting"}`,
                   seat.ready ? "Ready" : "Getting settled"));
  return card;
}

function houseCard() {
  const card = document.createElement("div");
  card.className = "seat r filled";
  card.append(text("div", "side", "Red dugout"),
              text("div", "name", HOUSE),
              text("div", "sub", "the shipped squad, 0W-1D-7L"));
  return card;
}

function text(tag, className, words) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = words;
  return node;
}

/* ── The wall ───────────────────────────────────────────────────────── */

/**
 * One socket for the whole venue.
 *
 * `wall` is the roster -- who is playing what, which changes at a whistle --
 * and `wall.state` is a frame of one of them. Six tiles off six sockets would
 * be six reconnect loops and six copies of this code.
 */
function wall(message) {
  if (message.type === "wall") return roster(message.rooms || []);
  if (message.type !== "wall.state") return;
  const room = live.get(message.code);
  if (!room) return;
  const before = room.frame;
  room.frame = message;
  room.frameAt = Date.now();
  // A goal is the score changing, which is the one thing the wall is told
  // about a match without being told. It is what makes a tile worth watching.
  if (before && line(before) !== line(message)) room.goalAt = room.frameAt;
  paintTile(room);
}

function roster(rooms) {
  const now = Date.now();
  const seen = new Set();
  for (const row of rooms) {
    seen.add(row.code);
    const known = live.get(row.code);
    if (known) Object.assign(known, row);
    else live.set(row.code, { ...row, frame: null, frameAt: 0, since: now, goalAt: 0 });
  }
  for (const gone of [...live.keys()]) if (!seen.has(gone)) live.delete(gone);
  // A pin lasts until that match ends, and it has.
  if (pinned && !live.has(pinned)) pinned = null;
  strip();
  direct();
}

/** Whether a room is being played rather than merely believed in. */
const onNow = (room, now) =>
  (room.frameAt ? now - room.frameAt < STALE_MS : now - room.since < SILENT_MS);

/** Which rooms the wall shows: everything on except the one already big. */
const elsewhere = (now = Date.now()) =>
  [...live.values()].filter((room) => room.code !== showing && onNow(room, now));

function strip() {
  const others = elsewhere();
  // Pages are full or there is nothing to fill them: with seven rooms the last
  // page starts at the second, not at the seventh with five empty slots after.
  const last = Math.max(0, others.length - TILES);
  if (turned > last) turned = 0;
  const visible = others.slice(turned, turned + TILES);

  // Redrawn only when the page itself changes. The strip is repainted on every
  // director tick, and rebuilding six canvases each time would blank them until
  // their next frame -- a wall that flickers every two seconds.
  const page = visible.map((room) => room.code).join(" ");
  if (page !== stripped) {
    stripped = page;
    tiles.clear();
    el("wall-hint").hidden = visible.length === 0;
    el("tiles").replaceChildren(...visible.map((room, index) => tileFor(room, index + 1)));
    if (!visible.length) {
      el("tiles").append(text("p", "wall-none",
                              "No other matches right now. Scan the code to start one."));
    }
  }
  for (const room of visible) paintTile(room);
}

function tileFor(room, number) {
  const tile = document.createElement("button");
  tile.type = "button";
  tile.className = "tile";
  tile.title = `Put ${room.code} on the big screen`;
  tile.addEventListener("click", () => pin(room.code));

  const top = document.createElement("div");
  top.className = "tile-top";
  top.append(text("span", "tile-no", String(number)),
             text("span", "tile-code", room.code),
             text("span", "tile-clock", clock(room)));

  const canvas = document.createElement("canvas");
  canvas.className = "tile-pitch";
  canvas.width = 320;
  canvas.height = 180;

  const names = document.createElement("div");
  names.className = "tile-line";
  names.append(text("span", "nm b", room.blue || "Open"),
               text("b", "sc", scoreline(room)),
               text("span", "nm r", room.red || (room.mode === "solo" ? HOUSE : "Open")));

  tile.append(top, canvas, names);
  tiles.set(room.code, { tile, canvas, clock: top.lastChild, score: names.children[1] });
  return tile;
}

const line = (frame) => (Array.isArray(frame.score) ? frame.score.join("-") : "");
const scoreline = (room) => (room.frame && line(room.frame)) || "0-0";

function clock(room) {
  const seconds = room.frame && typeof room.frame.clock === "number" ? room.frame.clock : null;
  if (seconds === null) return "--:--";
  const whole = Math.max(0, Math.round(seconds));
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}

/**
 * A match as nine dots and a ball.
 *
 * Deliberately not Phaser: six of these at four frames a second next to a real
 * pitch would spend a wall screen's whole budget on thumbnails. Positions are
 * fractions, so this is the same frame centre court is drawing, at 320 by 180.
 */
function paintTile(room) {
  const held = tiles.get(room.code);
  if (!held || !held.canvas.isConnected) return;
  held.clock.textContent = clock(room);
  held.score.textContent = scoreline(room);

  const paint = held.canvas.getContext("2d");
  const { width, height } = held.canvas;
  paint.clearRect(0, 0, width, height);
  paint.strokeStyle = "rgba(255,255,255,.14)";
  paint.lineWidth = 2;
  paint.strokeRect(8, 8, width - 16, height - 16);
  paint.beginPath();
  paint.moveTo(width / 2, 8);
  paint.lineTo(width / 2, height - 8);
  paint.stroke();
  paint.beginPath();
  paint.arc(width / 2, height / 2, 26, 0, Math.PI * 2);
  paint.stroke();

  const frame = room.frame;
  if (!frame) return;
  const spot = (at, colour, size) => {
    if (!Array.isArray(at)) return;
    paint.fillStyle = colour;
    paint.beginPath();
    paint.arc(clamp(at[0]) * width, clamp(at[1]) * height, size, 0, Math.PI * 2);
    paint.fill();
  };
  for (const at of frame.blue || []) spot(at, "#4b90ff", 4.5);
  for (const at of frame.red || []) spot(at, "#f87171", 4.5);
  spot(frame.ball, "#ffcc00", 3.5);
}

const clamp = (value) => Math.min(1, Math.max(0, Number(value) || 0));

/* ── The director ───────────────────────────────────────────────────── */

/**
 * Choose what is on the big screen, and say which of us chose it.
 *
 * Called on every roster change, every whistle in our own room and on a slow
 * tick, because two of the three reasons a match becomes worth watching --
 * the clock running down, a goal going cold -- are the passing of time and
 * nothing arriving.
 */
function direct() {
  const now = Date.now();
  const wanted = choose(now);
  if (wanted !== showing) cutTo(wanted, now);
  // Every pass, not only on a change: a room's managers reach the wall in
  // their own message, which routinely lands after the match they are in, and
  // a match that has stopped sending frames drops off the strip on its own.
  if (showing) label(showing);
  strip();

  el("director").hidden = ![...live.values()].some((room) => onNow(room, now));
  const chosen = pinned && live.get(pinned);
  el("directing").textContent = chosen ? `Pinned · ${versus(chosen)}` : "Auto";
  el("director").className = `dir-chip${chosen ? " pinned" : ""}`;
}

function choose(now) {
  // Our own live match never leaves centre court while this tab is hosting it.
  // The physics is in that iframe: re-pointing it to watch somebody else would
  // stop the match this screen is responsible for, which no amount of a better
  // game elsewhere is worth.
  if (hostingLive()) return code;
  // A pin holds until the operator lifts it or the match ends -- but not
  // through a screen that has stopped reporting, because there is nothing on
  // the other end of it to hold. It takes the screen back if it comes round.
  if (pinned && live.has(pinned) && onNow(live.get(pinned), now)) return pinned;
  // Somebody is standing in front of this screen filling in a team sheet. They
  // get to watch their own name land in a seat, whatever else is on elsewhere.
  // An empty lobby is not worth a live match, and the rail keeps the QR code on
  // the screen either way, so nobody loses the way in.
  if (ours && ours.status === "lobby" && Object.keys(ours.seats).length) return null;

  // A room only the arena still believes in is not a match, and putting a
  // frozen pitch on the big screen is worse than putting nothing on it.
  const playing = [...live.values()].filter((room) => onNow(room, now) && room.frame);
  if (!playing.length) return null;
  const best = playing
    .map((room) => ({ room, worth: worth(room, now) }))
    .sort((a, b) => b.worth - a.worth)[0];
  const current = showing && live.get(showing);
  if (!current || !onNow(current, now)) return best.room.code;
  // A match that is still nearly as interesting keeps the screen: switching
  // costs a reload of the pitch, and the room somebody is watching is worth
  // more than the arithmetic difference between it and the next one.
  if (now - framedAt < DWELL_MS) return showing;
  return best.worth > worth(current, now) ? best.room.code : showing;
}

function worth(room, now) {
  if (!room.frame) return 0;   // nothing to show: the host has not reported yet
  let score = 1;
  if (room.goalAt && now - room.goalAt < GOAL_HEAT_MS) score += 4;
  const [blue, red] = Array.isArray(room.frame.score) ? room.frame.score : [0, 0];
  if (blue === red) score += 2;
  if (typeof room.frame.clock === "number" && room.frame.clock <= ENDGAME_SEC) score += 3;
  return score;
}

const versus = (room) =>
  `${room.blue || "Open"} v ${room.red || (room.mode === "solo" ? HOUSE : "Open")}`;

/* ── Centre court ───────────────────────────────────────────────────── */

function cutTo(wanted, now) {
  showing = wanted;
  framedAt = now;
  lobby.hidden = Boolean(wanted);
  court.hidden = !wanted;
  // The lobby carries its own way in. The rail only needs one when the screen
  // has given the room over to somebody else's match.
  el("join-mini").hidden = !wanted || wanted === code;
  strip();

  if (courtRoom) {
    courtRoom.close();
    courtRoom = null;
  }
  if (!wanted) {
    el("pitch").removeAttribute("src");
    for (const feed of Object.values(feeds)) feed.clear();
    return;
  }
  watch(wanted);
  listen(wanted);
}

/** Whose match this is, over the two feeds. */
function label(wanted) {
  const room = live.get(wanted) || {};
  const solo = (wanted === code ? ours.mode : room.mode) === "solo";
  el("who-blue").textContent = room.blue || "";
  el("who-red").textContent = solo ? HOUSE : (room.red || "");
  el("relay-blue").dataset.empty = "Nothing said yet.";
  el("relay-red").dataset.empty = solo
    ? "No dugout here. The house side plays the squad as it shipped."
    : "Nothing said yet.";
}

/** Point the pitch at a room: as its host if we hold the token, else watching. */
function watch(wanted) {
  const at = new URL(venue.pitch_url || location.origin);
  at.searchParams.set("room", wanted);
  at.searchParams.set("team", "blue");
  const token = wanted === code ? hostToken() : "";
  at.searchParams.set("as", token ? "host" : "viewer");
  if (token) at.searchParams.set("client_id", token);
  const address = at.toString();
  // Only if it moved. Re-setting the same src reloads the iframe, and if that
  // iframe is the host of a live match, reloading it is losing the match.
  if (el("pitch").src !== address) el("pitch").src = address;
}

function listen(wanted) {
  if (wanted === code) return replay(wanted);   // already on the wire, since page one
  courtRoom = openRoom(wanted, {
    onMessage: courtside,
    // What the squad did is in the log; what the chain said on the way is not.
    // A reconnect, or a cut to a match already an hour old, re-reads the log
    // rather than showing an empty rail beside a match in its second half.
    onOpen: () => replay(wanted),
  });
}

/** Anything off centre court's room. Both feeds see it; each keeps its half. */
function courtside(message) {
  if (message.type === "event") {
    feeds.blue.event(message);
    feeds.red.event(message);
    return;
  }
  if (!String(message.type).startsWith("relay.")) return;
  feeds.blue.relayed(message);
  feeds.red.relayed(message);
}

async function replay(wanted) {
  // Cleared here rather than at the cut, because a reconnect replays the same
  // log again and the second pass must not draw everything twice.
  for (const feed of Object.values(feeds)) feed.clear();
  try {
    const { events } = await get(`/api/rooms/${wanted}/events?since=0`);
    // The screen may have cut away while this was in flight.
    if (wanted !== showing) return;
    for (const entry of events) courtside({ ...entry, type: "event" });
  } catch (failure) {
    if (!(failure instanceof Refused)) throw failure;
  }
}

/* ── The operator ───────────────────────────────────────────────────── */

function pin(wanted) {
  if (hostingLive()) {
    return say(`This screen is hosting ${code}, so that match holds centre court `
               + "until the whistle.");
  }
  problem.hidden = true;
  pinned = wanted === pinned ? null : wanted;
  direct();
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    pinned = null;
    problem.hidden = true;
    return direct();
  }
  // Cmd-1 and Ctrl-1 belong to the browser's tabs, and an operator reaching for
  // one of those is not asking for a match.
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const number = Number(event.key);
  if (!Number.isInteger(number) || number < 1 || number > TILES) return;
  // Off the strip as drawn rather than the list behind it, so the number on a
  // tile is the number that puts it up whatever the rotation is doing.
  const wanted = [...tiles.keys()][number - 1];
  if (wanted) pin(wanted);
});

el("again").addEventListener("click", () => location.assign(`/arena?mode=${MODE}`));

// A seventh match is a real venue, and the six on the strip must not be the
// same six all evening.
setInterval(() => {
  const others = elsewhere();
  if (others.length <= TILES) return;
  const last = others.length - TILES;
  turned = turned >= last ? 0 : Math.min(turned + TILES, last);
  strip();
}, ROTATE_MS);

function say(message) {
  problem.textContent = message;
  problem.hidden = false;
}

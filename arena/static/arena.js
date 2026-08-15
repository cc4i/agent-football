/**
 * The big screen: centre court, the wall, and the director between them.
 *
 * The screen opens a room and holds it, and that is all it holds. Every match
 * in the venue is played on the grounds, so nothing on this page advances
 * anybody's football -- including its own. What this page does is choose which
 * match the room is looking at and draw it.
 *
 * Centre court is one canvas, mounted once from /pitch/viewer.js and pointed
 * at a different match on a cut. It was an iframe per match until the venue
 * grew past a handful: a click cost a page load, a Phaser boot and a texture
 * decode, which is affordable on a twelve-second carousel and absurd on a wall
 * of fifty tiles somebody is browsing.
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
// Six was sized to be read from the back of a room. A grid somebody walks up to
// and clicks is a different budget: twelve fits a 1080p wall at a size a thumb
// can hit, and puts fifty matches on five pages rather than nine.
const TILES = 12;
const ROTATE_MS = 12000;
// The carousel is for when nobody is there. The moment somebody pages or pins
// it stops, and it starts again once they have stopped touching it.
const BROWSING_MS = 30000;
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
// How long a result stands before the screen opens the lobby for the next
// match. Long enough to read a scoreline from the back of a room, short enough
// that the queue on the other side of it is not waiting on anybody.
const NEXT_LOBBY_MS = 20000;

let code = (params.get("room") || "").toUpperCase();
let venue = { pitch_url: "" };
let ours = null;            // this screen's own room, as the arena last told us
// The room framed on centre court, or null for none. Undefined until the first
// pass of the director, so that "nothing is on" is itself something to draw.
let showing;
let pinned = null;          // the operator's choice, which outlives the director's
let framedAt = 0;
let page = 0;               // which page of the strip is up
let browsing = null;        // running while somebody is working the wall by hand
let stripped = "";          // the page the strip is drawing, so it is redrawn once
let paged = "";             // the same, for the page buttons under it
let courtRoom = null;       // somebody else's room, open only while they are on
let pitch = null;           // centre court's canvas, once /pitch has been loaded
let leaving = false;        // this page is on its way out to a room that works
let handover = 0;           // the countdown from a result to the next lobby
let switching = false;      // a mode change is with the arena, awaiting its word
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
    // Not awaited. Opening a room and drawing the lobby is what the person
    // standing in front of the screen is waiting on, and there is nothing for
    // centre court to show until the director has chosen something anyway.
    bootCourt();
    ours = code ? await get(`/api/rooms/${code}`) : await open();
    // A tab that has come back to a room that died while it was away. Answered
    // before the room is drawn rather than after the socket says the same
    // thing, so nobody watches a dead room's QR code go up and come down.
    if (ours.status === "abandoned") return startFresh(ours.mode);
    code = ours.code;
    dress(ours);
    // Our own room's socket stays open whatever is on the screen: it is how the
    // lobby learns a seat filled and how the whistle gets here. While our own
    // match is the one on centre court it carries the relay too, so the usual
    // case is one room socket rather than two.
    // A room is only as real as the tab holding it: the screen token lives in
    // this tab's sessionStorage and dies with it, so a lobby whose screen has
    // closed can never be run by anybody, ever. Left unsaid, the arena had no
    // way to tell that room from a screen waiting patiently in front of a
    // queue, and went on offering it to every phone in the building. Saying so
    // is this socket being open, and nothing else: a tab the browser has put to
    // sleep still answers a ping from its network stack, which is more than can
    // be said for anything on a timer.
    openRoom(code, {
      // The token goes on this socket so the screen can vouch for its own room.
      // A tab that is only watching has no token, which is what it is.
      clientId: screenToken(),
      onMessage(message) {
        if (message.type === "room") return mine(message);
        if (showing === code) courtside(message);
      },
      onOpen() {
        if (showing === code) replay(code);
      },
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
  sessionStorage.setItem(tokenKey(opened.code), opened.screen_token);
  // The mode stays in the URL beside the code. Replaced by the code alone, a
  // head-to-head screen forgot what it was the moment it opened its first
  // room, and every reload of it after that was a solo screen.
  history.replaceState(null, "", `/arena?room=${opened.code}&mode=${opened.mode}`);
  return opened;
}

/**
 * Leave a room this screen can do nothing with, and open another.
 *
 * A room is abandoned when nothing has held it for the half minute the arena
 * allows, which since it started sweeping lobbies means a tab that was closed,
 * a lid that shut, or a venue's wifi gone for thirty seconds. However it
 * happened, the physics token went with the tab, so this screen can never host
 * that room again -- and a screen is the only thing in the building that can
 * open a room at all. Left drawing it, it stood there showing "Ready when they
 * are" over a QR code for a room that no longer existed, with the way out
 * hidden because the way out is shown for a match that finished and this one
 * never started. Meanwhile every phone in the venue read "no screen is waiting
 * for a manager this second" and had nothing at all to tap.
 *
 * Hosting is the only reason this page is open, so it goes and hosts. A full
 * load rather than opening a room in place: `start` already does all of it in
 * the right order, and the alternative is unpicking a socket, a token and a
 * strip by hand to arrive in the same state.
 */
function startFresh(mode) {
  // The arena publishes an ending to its own watchers as well as deciding it,
  // so one dead room can arrive here more than once.
  if (leaving) return;
  leaving = true;
  el("badge").textContent = "That room closed. Opening a new one…";
  el("again").hidden = true;
  location.assign(`/arena?mode=${mode || MODE}`);
}

const tokenKey = (roomCode) => `arena.screen.${roomCode}`;
const screenToken = () => sessionStorage.getItem(tokenKey(code)) || "";

/* ── This screen's own room ─────────────────────────────────────────── */

function dress(snapshot) {
  el("code").textContent = snapshot.code;
  el("code-2").textContent = snapshot.code;
  el("code-3").textContent = snapshot.code;
  el("join-url").textContent = snapshot.join_url;
  el("board").src = "/board";

  for (const holder of ["qr", "qr-mini"]) {
    const qr = document.createElement("img");
    qr.src = `/api/rooms/${snapshot.code}/qr.svg`;
    qr.alt = `QR code for room ${snapshot.code}`;
    el(holder).replaceChildren(qr);
  }

  const hosting = Boolean(screenToken());
  el("role").className = `role-chip${hosting ? "" : " viewer"}`;
  el("role").replaceChildren(document.createElement("i"),
                             document.createTextNode(hosting ? "Hosting" : "Watching"));
  describe(snapshot);
}

/**
 * What this room plays, and whether that is still ours to change.
 *
 * Separate from `dress` because the mode is no longer settled when the room is
 * opened: it is redrawn on every room message, where re-running `dress` would
 * re-fetch the QR and reload the standings under the lobby on each one.
 */
function describe(snapshot) {
  el("where").textContent = snapshot.mode === "solo"
    ? "Solo · against the house side" : "Head to head";
  // Offered only while this tab is holding a room that has not kicked off. A
  // screen that is only watching has no business reshaping somebody's lobby,
  // and after the whistle the mode is what the match was scored against.
  el("mode-switch").hidden = !(screenToken() && snapshot.status === "lobby");
  for (const mode of ["solo", "versus"]) {
    el(`mode-${mode}`).setAttribute("aria-pressed", String(snapshot.mode === mode));
  }
}

function mine(snapshot) {
  ours = snapshot;
  // The other way a room dies under a screen that is still looking at it: the
  // link drops for longer than the sweep waits, and it comes back to a room
  // the arena has already given up on.
  if (snapshot.status === "abandoned") return startFresh(snapshot.mode);
  el("again").hidden = snapshot.status !== "finished";
  describe(snapshot);
  drawSeats(snapshot);
  if (snapshot.status === "finished" && screenToken()) handOver();
  direct();
}

/**
 * Full time. Stand the result up for a moment, then open the next lobby.
 *
 * A screen is the only thing in the building that can open a room, and it can
 * hold one at a time, so the whole venue's turnstile is this one page. Waiting
 * for somebody to press "New room" put that turnstile behind a click nobody in
 * the queue is standing next to: the manager who just played is looking at
 * their phone, the next four are looking at theirs, and the screen sat on
 * "Full time. Open a new room to play again." until an organiser walked over.
 * Every phone in the room read "no screen is waiting for a manager this
 * second" for the whole of it, which is the same sentence a venue with nothing
 * plugged in shows.
 *
 * The count is shown rather than run down quietly, because a screen that
 * clears a result on its own is one somebody is about to miss the score on.
 * The button stays where it was and skips the wait for anybody who wants it.
 */
function handOver() {
  if (leaving || handover) return;
  const mode = (ours && ours.mode) || MODE;
  let left = Math.round(NEXT_LOBBY_MS / 1000);
  const count = () => {
    if (left <= 0) {
      clearInterval(handover);
      leaving = true;
      location.assign(`/arena?mode=${mode}`);
      return;
    }
    el("badge").textContent = `Full time. Next lobby in ${left}s`;
    left -= 1;
  };
  count();
  handover = setInterval(count, 1000);
}

function drawSeats(snapshot) {
  const teams = snapshot.mode === "solo" ? ["blue"] : ["blue", "red"];
  const taken = teams.filter((team) => snapshot.seats[team]).length;
  // The handover owns the badge once it has started counting, so a room
  // message arriving behind it does not put the sentence it replaced back.
  if (!handover) {
    el("badge").textContent = snapshot.status === "finished"
      ? "Full time. Open a new room to play again."
      : taken === teams.length
        ? "Ready when they are"
        : `Waiting for ${teams.length - taken === 1 ? "a manager" : "two managers"}`;
  }

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

/** How many pages of tiles the matches on now come to. Never fewer than one. */
const pageCount = (count) => Math.max(1, Math.ceil(count / TILES));

/**
 * How many tiles go on each page, once those pages are spread evenly.
 *
 * Not TILES. Filling each page to twelve and letting the last one take the
 * remainder is right for a list somebody scrolls and wrong for a strip that
 * turns itself over: thirteen matches divide as twelve and one, and the
 * carousel then puts that one tile up -- alone in a twelve-column grid, eleven
 * columns of nothing beside it -- and holds it there for a full rotation. Seven
 * and six is the same thirteen matches and no empty page to turn to.
 *
 * The page count is unchanged, so this moves tiles between pages rather than
 * adding a page: fifty matches are five pages either way, 10 10 10 10 9 instead
 * of 12 12 12 12 1.
 */
const perPage = (count) => Math.ceil(count / pageCount(count)) || TILES;

function strip() {
  const others = elsewhere();
  const pages = pageCount(others.length);
  const per = perPage(others.length);
  // Pages do not overlap, because a numbered page that shares half its tiles
  // with the one before it is a page nobody can hold in their head. The last
  // one is short instead, and a page that emptied under somebody -- matches do
  // end -- hands them the last page there is rather than a blank one.
  if (page >= pages) page = pages - 1;
  const first = page * per;
  const visible = others.slice(first, first + per);

  // Redrawn only when the page itself changes. The strip is repainted on every
  // director tick, and rebuilding twelve canvases each time would blank them
  // until their next frame -- a wall that flickers every two seconds.
  //
  // Keyed on where the page starts as well as what is on it, because the
  // numbering below is drawn from that offset and the two can move apart:
  // thirteen matches put codes 8 to 13 on page two, and one of the first seven
  // ending leaves the same six codes there numbered 7 to 12.
  const shown = `${first} ${visible.map((room) => room.code).join(" ")}`;
  if (shown !== stripped) {
    stripped = shown;
    tiles.clear();
    el("wall-hint").hidden = visible.length === 0;
    // Numbered along the whole strip rather than from one on every page. A
    // number that restarts counts nothing -- page two reads 1 to 10 exactly
    // like page one -- where a number that runs on is a count: the last tile
    // on the last page is how many matches are on.
    el("tiles").replaceChildren(
        ...visible.map((room, index) => tileFor(room, first + index + 1)));
    if (!visible.length) {
      el("tiles").append(text("p", "wall-none",
                              "No other matches right now. Scan the code to start one."));
    }
  }
  // Outside the redraw, which only runs when the page turns: a match ending on
  // page four changes the total without changing anything on page one, and the
  // total is the one number on this header that has to be right from any page.
  const total = el("wall-count");
  total.textContent = String(others.length);
  total.hidden = others.length === 0;
  pager(pages);
  for (const room of visible) paintTile(room);
}

/**
 * One button per page, with an arrow either side.
 *
 * Rebuilt only when the numbers or the page change, for the same reason the
 * tiles are: this runs every couple of seconds, and a button replaced under a
 * finger is a press that lands on nothing.
 */
function pager(pages) {
  const shape = `${pages} ${page}`;
  if (shape === paged) return;
  paged = shape;
  const box = el("pages");
  box.hidden = pages < 2;
  // One page is every match already, so a control for choosing it says nothing.
  if (pages < 2) return box.replaceChildren();

  const step = (words, label, to) => {
    const button = text("button", "page-step", words);
    button.type = "button";
    button.title = label;
    button.setAttribute("aria-label", label);
    button.disabled = to < 0 || to >= pages;
    button.addEventListener("click", () => goToPage(to));
    return button;
  };
  const numbers = Array.from({ length: pages }, (_, index) => {
    const on = index === page;
    const button = text("button", `page-no${on ? " on" : ""}`, String(index + 1));
    button.type = "button";
    button.dataset.page = String(index);
    button.title = `Page ${index + 1} of ${pages}`;
    button.setAttribute("aria-current", on ? "true" : "false");
    button.addEventListener("click", () => goToPage(index));
    return button;
  });
  box.replaceChildren(step("‹", "Previous page", page - 1),
                      ...numbers,
                      step("›", "Next page", page + 1));
}

/** Somebody is working the wall by hand. The carousel gets out of their way. */
function browsingNow() {
  clearTimeout(browsing);
  browsing = setTimeout(() => { browsing = null; }, BROWSING_MS);
}

function goToPage(next) {
  page = Math.max(0, Math.min(next, pageCount(elsewhere().length) - 1));
  browsingNow();
  strip();
}

function tileFor(room, number) {
  const tile = document.createElement("button");
  tile.type = "button";
  tile.className = "tile";
  tile.dataset.code = room.code;
  // What is printed on it, so the keyboard can find a tile by the number
  // somebody is reading off it rather than by where it happens to be drawn.
  tile.dataset.no = String(number);
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
               text("b", "sc", scoreline(room)));
  // The house side is on the other end of every solo match in the building, so
  // saying so on a tile is a third of its width spent on "The hou...". A name
  // is what somebody scans a wall of twelve for, and this is the room to print
  // one in. Head to head has two of them and prints both.
  if (room.mode !== "solo") names.append(text("span", "nm r", room.red || "Open"));

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

  const chip = el("director");
  chip.hidden = ![...live.values()].some((room) => onNow(room, now));
  const chosen = pinned && live.get(pinned);
  el("directing").textContent = chosen ? `Pinned · ${versus(chosen)}` : "Auto";
  chip.className = `dir-chip${chosen ? " pinned" : ""}`;
  // Inert while the director has it. The chip reports who is choosing and its
  // only power is to stop being the operator, so on auto there is nothing for
  // a press to do -- and pinning whatever happens to be up at that moment,
  // silently, is the one thing it must not do.
  chip.disabled = !chosen;
  chip.title = chosen ? "Hand the big screen back to auto" : "";
}

/**
 * Which match centre court should be on, or null for this screen's own lobby.
 *
 * This screen's own room is in the running like every other. The physics is on
 * a ground now, so cutting away from it costs nothing but the view: the match
 * plays on, and the rail keeps the QR code up for the people in front of it.
 */
function choose(now) {
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
  // A match that is still nearly as interesting keeps the screen. The cut is
  // cheap now, but the watching is not: a screen that flicks away mid-move
  // every time the arithmetic tilts is unwatchable however smooth each cut is.
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

/**
 * Who is playing, for a chip with about thirty characters to say it in.
 *
 * The same rule the tiles use, and for the same reason: the house side is on
 * the other end of every solo match in the building, so printing it spends the
 * chip on a phrase that is true of everything and distinguishes nothing -- and
 * spends it badly, because what gets ellipsised to pay for it is the one name
 * that does distinguish this match. "Pinned · Wall Check 610 v The ho..." reads
 * worse than "Pinned · Wall Check 610" and says less.
 *
 * Head to head has two names that are both worth printing, and prints both.
 */
const versus = (room) => (room.mode === "solo"
  ? room.blue || "Open"
  : `${room.blue || "Open"} v ${room.red || "Open"}`);

/* ── Centre court ───────────────────────────────────────────────────── */

/**
 * Boot centre court: one canvas, once, for as long as this page is open.
 *
 * The address comes off `venue.pitch_url`, which is `/pitch` where the arena
 * serves the build and the dev server otherwise, so there is nothing new to
 * configure. The module is named rather than hashed for exactly this reason:
 * a wall cannot read a build manifest.
 */
async function bootCourt() {
  const from = `${(venue.pitch_url || "").replace(/\/$/, "")}/viewer.js`;
  try {
    const { mount } = await import(from);
    pitch = mount(el("pitch"));
    // The director runs on its own clock and may have chosen while Phaser was
    // still loading, in which case nobody is going to call the cut again.
    if (showing) pitch.point(showing);
  } catch (failure) {
    console.error("centre court could not load the pitch", failure);
    say("The pitch did not load, so the big screen can only show the wall.");
  }
}

function cutTo(wanted, now) {
  showing = wanted;
  framedAt = now;
  lobby.hidden = Boolean(wanted);
  court.hidden = !wanted;
  // What is framed, said out loud in the DOM. Nothing on the page reads it --
  // it is for the test that drives fifty matches past this screen, and for
  // anybody with the inspector open wondering which room they are looking at.
  court.dataset.showing = wanted || "";
  // The lobby carries its own way in. The rail only needs one when the screen
  // has given the room over to somebody else's match.
  el("join-mini").hidden = !wanted || wanted === code;
  strip();

  if (courtRoom) {
    courtRoom.close();
    courtRoom = null;
  }
  // Including the cut to nothing: the scoreline, the clock and both nameplates
  // belong to the match being left, and the next thing put on this canvas must
  // not open on them.
  if (pitch) pitch.point(wanted || null);
  if (!wanted) {
    for (const feed of Object.values(feeds)) feed.clear();
    return;
  }
  listen(wanted);
}

/** Whose match this is, over the two feeds and on the pitch itself. */
function label(wanted) {
  const room = live.get(wanted) || {};
  const solo = (wanted === code ? ours.mode : room.mode) === "solo";
  el("who-blue").textContent = room.blue || "";
  el("who-red").textContent = solo ? HOUSE : (room.red || "");
  el("relay-blue").dataset.empty = "Nothing said yet.";
  el("relay-red").dataset.empty = solo
    ? "No dugout here. The house side plays the squad as it shipped."
    : "Nothing said yet.";
  // The two plates in the corners of the pitch. The framed page used to read
  // these off its own room socket; mounted, it is told, and the wall already
  // knows because the roster is what the tiles are drawn from. Every pass
  // rather than only on a cut, because a manager who sat down after kick-off
  // reaches the roster after the match they are in.
  if (pitch) {
    pitch.managers({ mode: solo ? "solo" : "versus",
                     seats: { blue: { name: room.blue }, red: { name: room.red } } });
  }
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

/**
 * Anything off centre court's room. Both feeds see it; each keeps its half.
 *
 * `sounding` is false for the log a cut re-reads. A match already in its
 * second half has a dozen goals behind it, and every one of them would arrive
 * at once: a dozen whistles and a dozen white flashes over a pitch that has
 * not drawn a frame yet.
 */
function courtside(message, sounding = true) {
  // Ten a second, and the only reason this socket carries more than the wall's
  // does. Handed straight over: the scene draws it on its own next tick.
  if (message.type === "state") {
    if (pitch) pitch.frame(message);
    return;
  }
  if (message.type === "event") {
    if (pitch && sounding) pitch.cheer(message.kind);
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
    for (const entry of events) courtside({ ...entry, type: "event" }, false);
  } catch (failure) {
    if (!(failure instanceof Refused)) throw failure;
  }
}

/* ── The operator ───────────────────────────────────────────────────── */

function pin(wanted) {
  problem.hidden = true;
  pinned = wanted === pinned ? null : wanted;
  // Somebody is at the wall. Whether they pinned a match or let one go, the
  // page under their hand is not to slide out from under it.
  browsingNow();
  direct();
}

/**
 * Give the screen back to the director, all of it.
 *
 * The carousel starts again on the next turn rather than in half a minute,
 * because this is somebody saying they have finished rather than somebody
 * pausing between tiles -- which is what `browsingNow` is already for.
 *
 * Two ways in, one behaviour. Escape is the operator at a keyboard; the chip
 * is the same intent on a screen that has no keyboard, and a wall that meant
 * two slightly different things by "auto" would be a wall nobody trusts.
 */
function toAuto() {
  pinned = null;
  clearTimeout(browsing);
  browsing = null;
  problem.hidden = true;
  direct();
}

el("director").addEventListener("click", toAuto);

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") return toAuto();
  // Cmd-1 and Ctrl-1 belong to the browser's tabs, and an operator reaching for
  // one of those is not asking for a match.
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.key === "ArrowLeft") return goToPage(page - 1);
  if (event.key === "ArrowRight") return goToPage(page + 1);
  // One key, so the shortcuts run out at nine and the rest of the wall is
  // reached with the mouse. The alternative is a two-key sequence to save a
  // click on a screen somebody is already standing at, which is worse.
  const number = Number(event.key);
  if (!Number.isInteger(number) || number < 1 || number > 9) return;
  // The tile wearing that number, not the nth tile drawn. Those were the same
  // thing while every page started at one; now that the numbering runs on
  // across the strip, following the position instead would answer 9 on page
  // two with a tile printed 19. A number that means one thing everywhere is
  // worth more than a shortcut on every page, so past the ninth match the key
  // does nothing and the tile says as much by not being numbered 1 to 9.
  const wanted = el("tiles").querySelector(`.tile[data-no="${number}"]`);
  if (wanted) pin(wanted.dataset.code);
});

// The mode of the match just played rather than the page's, which after a room
// has been opened is whatever the URL still says. A head-to-head screen that
// pressed this at full time got a solo room and one seat for two managers.
el("again").addEventListener("click",
                             () => location.assign(`/arena?mode=${(ours && ours.mode) || MODE}`));

/**
 * Turn the waiting room between score attack and head to head.
 *
 * The mode used to live only in the address: a screen opened solo unless
 * somebody had thought to put `?mode=versus` on the URL, and changing their
 * mind meant typing one in. On a screen on a wall with a queue in front of it
 * that is not a way at all, so a venue ran whichever mode the laptop happened
 * to be opened in all evening.
 *
 * The room is turned rather than replaced, so the code stays the one on the
 * wall, the QR beside it goes on pointing at the same match, and the manager
 * already filling in the join form is not dropped on their way in.
 */
async function chooseMode(mode) {
  if (switching || !ours || ours.mode === mode) return;
  switching = true;
  el("mode-switch").setAttribute("aria-busy", "true");
  try {
    // The arena announces this to our own socket as well, but this is the tab
    // that asked: it should not have to watch its own click go to the arena
    // and come back before the pill moves.
    mine(await post(`/api/rooms/${code}/mode`, { mode, screen_token: screenToken() }));
    problem.hidden = true;
    // The address is what "New room" and the full-time handover fall back on
    // when there is no room left to read a mode from, so it follows the choice.
    history.replaceState(null, "", `/arena?room=${code}&mode=${mode}`);
  } catch (failure) {
    if (!(failure instanceof Refused)) throw failure;
    say(failure.message);
    // The arena refused, and it is still the authority on what this room is:
    // put the pill back on the mode the room actually has.
    describe(ours);
  } finally {
    switching = false;
    el("mode-switch").removeAttribute("aria-busy");
  }
}

for (const mode of ["solo", "versus"]) {
  el(`mode-${mode}`).addEventListener("click", () => chooseMode(mode));
}

// A thirteenth match is a real venue, and the twelve on the strip must not be
// the same twelve all evening. Not while somebody is working the wall: paging
// under a hand that is reaching for a tile is the wall taking the screen back
// off the person standing in front of it.
setInterval(() => {
  if (browsing || pinned) return;
  const pages = pageCount(elsewhere().length);
  if (pages < 2) return;
  page = (page + 1) % pages;
  strip();
}, ROTATE_MS);

function say(message) {
  problem.textContent = message;
  problem.hidden = false;
}

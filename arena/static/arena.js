/**
 * The big screen: a room to scan into, and then the match at the size of it.
 *
 * The screen opens the room, so the screen holds the physics token and the
 * pitch it frames is the host. That is the rule stated by pointing: whoever
 * scanned the screen owns the screen.
 */

import { get, post, Refused } from "/static/api.js";
import { openRoom } from "/static/socket.js";

const el = (id) => document.getElementById(id);
const problem = el("problem");
const lobby = el("lobby");
const court = el("court");

const params = new URLSearchParams(location.search);
// Solo is the default because the house side is always available and a second
// manager is not. `?mode=versus` opens a room with a red dugout to take.
const MODE = params.get("mode") === "versus" ? "versus" : "solo";

let code = (params.get("room") || "").toUpperCase();
let venue = { pitch_url: "" };
let framed = false;

start();

async function start() {
  try {
    venue = await get("/api/venue");
    const snapshot = code ? await get(`/api/rooms/${code}`) : await open();
    code = snapshot.code;
    dress(snapshot);
    draw(snapshot);
    openRoom(code, {
      onMessage: (message) => message.type === "room" && draw(message),
      onDrop: (reason, permanent) => permanent && say(reason),
    });
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

function dress(snapshot) {
  el("code").textContent = snapshot.code;
  el("code-2").textContent = snapshot.code;
  el("join-url").textContent = snapshot.join_url;
  el("where").textContent = snapshot.mode === "solo"
    ? "Solo · against the house side" : "Head to head";

  const qr = document.createElement("img");
  qr.src = `/api/rooms/${snapshot.code}/qr.svg`;
  qr.alt = `QR code for room ${snapshot.code}`;
  el("qr").replaceChildren(qr);

  const hosting = Boolean(hostToken());
  el("role").className = `role-chip${hosting ? "" : " viewer"}`;
  el("role").replaceChildren(Object.assign(document.createElement("i"), {}),
                             document.createTextNode(hosting ? "Hosting" : "Watching"));
}

function draw(snapshot) {
  const started = snapshot.status !== "lobby";
  lobby.hidden = started;
  court.hidden = !started;
  el("again").hidden = snapshot.status !== "finished";
  if (started) return kickOff(snapshot);
  drawSeats(snapshot);
}

function drawSeats(snapshot) {
  const teams = snapshot.mode === "solo" ? ["blue"] : ["blue", "red"];
  const taken = teams.filter((team) => snapshot.seats[team]).length;
  el("badge").textContent = taken === teams.length
    ? "Ready when they are"
    : `Waiting for ${teams.length - taken === 1 ? "a manager" : "two managers"}`;

  const cards = [];
  teams.forEach((team, index) => {
    if (index) cards.push(Object.assign(document.createElement("span"),
                                        { className: "vs-mark", textContent: "vs" }));
    cards.push(seatCard(team, snapshot.seats[team]));
  });
  if (snapshot.mode === "solo") {
    cards.push(Object.assign(document.createElement("span"),
                             { className: "vs-mark", textContent: "vs" }));
    cards.push(houseCard());
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
              text("div", "name", "The house side"),
              text("div", "sub", "the shipped squad, 0W-1D-7L"));
  return card;
}

function text(tag, className, words) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = words;
  return node;
}

function kickOff(snapshot) {
  if (framed) return;
  framed = true;
  const token = hostToken();
  if (!token) {
    // Step 6 gives this screen a viewer that renders the host's frames. Until
    // then, saying so is better than framing a second pitch that would run its
    // own physics and disagree with the real one.
    court.hidden = true;
    return say("This match is hosted on another screen.");
  }
  const at = new URL(venue.pitch_url || location.origin);
  at.searchParams.set("room", snapshot.code);
  at.searchParams.set("team", "blue");
  at.searchParams.set("as", "host");
  at.searchParams.set("client_id", token);
  el("pitch").src = at.toString();
}

el("again").addEventListener("click", () => location.assign(`/arena?mode=${MODE}`));

function say(message) {
  problem.textContent = message;
  problem.hidden = false;
}

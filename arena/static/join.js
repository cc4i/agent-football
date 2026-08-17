/**
 * The join form: a name, an optional email and an opening stance, then a dugout.
 *
 * The room comes from the address the QR encoded, so there is nothing to type
 * and nothing to get wrong. Three calls in order: become a player, take a
 * seat, go to the dugout.
 *
 * A phone the venue already knows skips the first of those. It has a cookie
 * from the last match, and asking somebody to type their own name again in
 * front of a screen with a queue behind it is asking them to make a typo. The
 * two boxes are replaced by the name they already have and a way to change it,
 * and the boxes themselves - what they check, and where they say it - are
 * `signup.js`, because the register page asks exactly the same two questions.
 *
 * A refusal with a box to point at is said underneath that box. The banner at
 * the top is for the ones with no box: a room that has kicked off, a seat
 * somebody took first, a phone off the wifi.
 */

import { get, post, Refused } from "/static/api.js";
import { icon } from "/static/dom.js";
import { signup } from "/static/signup.js";
import { keepScreenToken, openRoom, screenToken } from "/static/socket.js";

const CODE = decodeURIComponent(location.pathname.split("/").pop() || "").toUpperCase();
const SIDE = { blue: "blue", red: "red" };

const form = document.getElementById("join");
const problem = document.getElementById("problem");
const pills = document.getElementById("pills");
const blurb = document.getElementById("blurb");
const take = document.getElementById("take");
const boxes = document.getElementById("boxes");
const known = document.getElementById("known");
const knownName = document.getElementById("known-name");
const change = document.getElementById("change");
const name = document.getElementById("name");
const title = document.getElementById("title");
const sub = document.getElementById("sub");
const stanceField = document.getElementById("stance");
const onward = document.getElementById("onward");
const onwardOwn = document.getElementById("onward-own");
const onwardMatch = document.getElementById("onward-match");

let stance = null;
let seat = null;
// This room cannot be taken, and the page has become the one that says where
// else to go. Latched rather than recomputed because the identity call lands
// separately -- see `drawOnward`.
let refused = false;
// Who the arena says this phone is, until the manager says otherwise. Null
// means the two boxes are showing and the join has to be made.
let me = null;
// A join already on its way. The button is disabled for the whole of it, and
// `ready` is what holds it there while the form clears its own marks.
let sending = false;

const who = signup({
  name,
  nameHint: document.getElementById("name-hint"),
  email: document.getElementById("email"),
  emailHint: document.getElementById("email-hint"),
  changed: ready,
});

document.getElementById("code").textContent = CODE;

// A room this phone opened is a room only this phone is behind: no screen, and
// no grounds until the whistle. The sweep gives up on a lobby nobody vouches
// for after HOST_GONE_SECONDS, and picking a philosophy is easily that long, so
// the socket is opened for no other reason than to say somebody is still here.
// Nothing is read off it -- this form draws from the one snapshot it fetched --
// and a phone joining a room somebody else opened holds no token, so it opens
// nothing at all.
if (screenToken(CODE)) openRoom(CODE, { clientId: screenToken(CODE) });

start();

async function start() {
  // Out with the room rather than behind it. A phone with no session is the
  // ordinary case here rather than a problem, so this is caught to null and
  // nothing below waits on it - but it is what decides which of the two
  // identity states the page shows, and awaited last it decided too late:
  // a manager the venue knows spent a measured 596ms reading "Name on the
  // board" before their own name arrived to replace it.
  const identity = get("/api/players/me").catch(() => null);
  try {
    const [room, stances] = await Promise.all([
      get(`/api/rooms/${CODE}`),
      get("/api/philosophies"),
    ]);
    drawStances(stances.philosophies);
    settle(room);
  } catch (failure) {
    complain(failure);
  }
  // Whichever it is, the page stops being undecided about it here. Unless the
  // room turned out to be one nobody can take, in which case neither identity
  // state belongs on the page: there is nothing left to sign a name up for.
  const player = await identity;
  if (player) greet(player);
  else if (!refused) boxes.hidden = false;
}

function greet(player) {
  me = player;
  knownName.textContent = player.display_name;
  known.hidden = refused;
  boxes.hidden = true;
  // The identity is half of what the way on is drawn from, and it may well be
  // the half that arrived second.
  drawOnward();
}

change.addEventListener("click", () => {
  // Their own name back in the box, because most people changing it are fixing
  // a typo in it rather than becoming somebody else.
  name.value = me ? me.display_name : "";
  me = null;
  known.hidden = true;
  boxes.hidden = false;
  name.focus();
  name.select();
  ready();
});

function drawStances(stances) {
  pills.replaceChildren(...stances.map((entry) => {
    const pill = document.createElement("button");
    pill.type = "button";
    pill.className = "pill";
    pill.append(icon(entry.icon), entry.label);
    pill.addEventListener("click", () => {
      stance = entry.name;
      blurb.textContent = entry.blurb;
      for (const other of pills.children) other.classList.toggle("on", other === pill);
      ready();
    });
    return pill;
  }));
  // The first stance is chosen for them: a form that cannot be submitted until
  // you have discovered a hidden requirement is a form people abandon.
  if (pills.firstChild) pills.firstChild.click();
}

function settle(room) {
  seat = room.open_seats[0] || null;
  describeRoom(room);

  // None of the three says "scan the code for the next one" any more. The code
  // they scanned is this one, there is no next one until somebody opens it, and
  // the controls underneath now answer the question the sentence was dodging.
  // What is left here is which of the three happened, said once.
  if (room.status === "live") {
    return refuse("That match has already kicked off.");
  }
  if (room.status !== "lobby") {
    // Finished, or given up on because its screen went away. Either way it is
    // over, and "already kicked off" would send somebody looking for a match
    // to watch that nobody is playing.
    return refuse("That room is closed.");
  }
  if (!seat) {
    return refuse("Both dugouts are taken.");
  }
  take.textContent = `Take the ${SIDE[seat]} dugout`;
  ready();
}

/**
 * The bar over the form: which mode this room is, and where it has got to.
 *
 * Its own function below the refusals rather than three lines above them, so
 * that the first thing this file says about a live room is what it does about
 * one. It is drawn for every room including the ones about to be refused: what
 * happened is the first thing somebody arriving on a dead code wants, and the
 * banner underneath says it in a sentence rather than in two words.
 */
function describeRoom(room) {
  const mode = room.mode === "solo" ? "Solo" : "Versus";
  // "Full" is true of a finished room in the arithmetic sense that it has no
  // free seat, and false in the sense anybody reads it -- it says come back in
  // three minutes, over a room that is never coming back.
  const state = room.status === "live" ? "kicked off"
    : room.status !== "lobby" ? "closed"
      : seat ? "seat open" : "full";
  document.getElementById("mode").textContent = `${mode} · ${state}`;
}

function ready() {
  take.disabled = sending || !(stance && seat) || who.refused;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (take.disabled) return;
  problem.hidden = true;
  sending = true;
  ready();

  // A manager who has not touched the boxes is already a player, and joining
  // again would be one more thing between them and the whistle.
  if (!me) {
    let player;
    try {
      player = await who.submit();
    } catch (failure) {
      return giveUp(failure);
    }
    if (!player) {
      sending = false;
      return ready();
    }
  }

  try {
    await post(`/api/rooms/${CODE}/seats/${seat}`, { philosophy: stance });
    location.assign(`/play?room=${encodeURIComponent(CODE)}`);
  } catch (failure) {
    giveUp(failure);
  }
});

function giveUp(failure) {
  sending = false;
  complain(failure);
  ready();
}

function complain(failure) {
  if (!(failure instanceof Refused)) throw failure;
  problem.textContent = failure.message;
  problem.hidden = false;
}

function refuse(message) {
  problem.textContent = message;
  problem.hidden = false;
  take.disabled = true;
  seat = null;
  refused = true;
  // The form goes with it. A stance to pick and a Take button to press, over a
  // room that has neither, is most of a handset spent on controls that do
  // nothing -- and the one control that would help is below the fold behind
  // them. What the page is for now is the way on.
  title.textContent = "Find another match";
  sub.hidden = true;
  stanceField.hidden = true;
  take.hidden = true;
  boxes.hidden = true;
  known.hidden = true;
  onward.hidden = false;
  drawOnward();
}

/**
 * Which way on to offer, once both answers are in.
 *
 * Called from the refusal and again when the identity lands, because those two
 * arrive in either order: `start` fires them together and waits only on the
 * room. Whichever is second finds `refused` already set and draws.
 *
 * A manager who is already sitting in a match is sent back to it instead of
 * being offered a room of their own. Two rooms held by one person is one lobby
 * standing empty in the venue's list with nobody behind it, and at a venue with
 * a queue that is somebody else's match.
 */
function drawOnward() {
  if (!refused) return;
  const playing = me && me.room && me.room.code;
  onwardOwn.hidden = Boolean(playing);
  onwardMatch.hidden = !playing;
  if (playing) onwardMatch.href = `/play?room=${encodeURIComponent(playing)}`;
}

/**
 * Open a room from here, which is the same door the home page offers.
 *
 * The token is kept before leaving for the same reason it is there: whoever
 * opened a room is the only one holding it, and the sweep gives up on a lobby
 * nobody vouches for in HOST_GONE_SECONDS.
 */
for (const button of onwardOwn.querySelectorAll("[data-mode]")) {
  button.addEventListener("click", async () => {
    for (const other of onwardOwn.querySelectorAll("[data-mode]")) other.disabled = true;
    problem.hidden = true;
    try {
      const opened = await post("/api/rooms", { mode: button.dataset.mode });
      keepScreenToken(opened.code, opened.screen_token);
      location.assign(`/join/${encodeURIComponent(opened.code)}`);
    } catch (failure) {
      // The venue's own limits speak here -- too many rooms too fast, or a
      // venue at MAX_LIVE_ROOMS -- in words a manager can act on.
      complain(failure);
      for (const other of onwardOwn.querySelectorAll("[data-mode]")) other.disabled = false;
    }
  });
}

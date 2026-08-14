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

let stance = null;
let seat = null;
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
  // Whichever it is, the page stops being undecided about it here.
  const player = await identity;
  if (player) greet(player);
  else boxes.hidden = false;
}

function greet(player) {
  me = player;
  knownName.textContent = player.display_name;
  known.hidden = false;
  boxes.hidden = true;
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
  const mode = room.mode === "solo" ? "Solo" : "Versus";
  seat = room.open_seats[0] || null;
  document.getElementById("mode").textContent =
    seat ? `${mode} · seat open` : `${mode} · full`;

  if (room.status !== "lobby") {
    return refuse("That match has already kicked off. Scan the code for the next one.");
  }
  if (!seat) {
    return refuse("Both dugouts are taken. Scan the code for the next room.");
  }
  take.textContent = `Take the ${SIDE[seat]} dugout`;
  ready();
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
}

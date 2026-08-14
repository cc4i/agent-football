/**
 * The join form: a name, an optional email and an opening stance, then a dugout.
 *
 * The room comes from the address the QR encoded, so there is nothing to type
 * and nothing to get wrong. Three calls in order: become a player, take a
 * seat, go to the dugout.
 *
 * The name is the one thing here that somebody else can already have taken, so
 * the arena is asked about it while it is being typed rather than only when
 * the button is tapped. The join itself is still what decides -- two phones can
 * type the same name in the same second -- and it says the same sentence.
 *
 * A refusal about one of these two boxes is said underneath that box. The
 * banner at the top is for the ones with no box to point at: a room that has
 * kicked off, a seat somebody else took, a phone off the wifi.
 */

import { get, post, Refused } from "/static/api.js";
import { icon } from "/static/dom.js";

const CODE = decodeURIComponent(location.pathname.split("/").pop() || "").toUpperCase();
const SIDE = { blue: "blue", red: "red" };

// How long a phone keyboard goes quiet before the name is worth asking about.
// Long enough that typing a name is one question rather than a dozen, short
// enough that the answer is there before a thumb reaches the button.
const TYPING_PAUSE = 400;

const form = document.getElementById("join");
const problem = document.getElementById("problem");
const pills = document.getElementById("pills");
const blurb = document.getElementById("blurb");
const take = document.getElementById("take");
const name = document.getElementById("name");
const nameHint = document.getElementById("name-hint");
const email = document.getElementById("email");
const emailHint = document.getElementById("email-hint");

let stance = null;
let seat = null;
// Only a name the arena has actually refused blocks the button. Somewhere
// between a keystroke and an answer it is unknown, and a form that will not be
// submitted until a network call comes back is a form a bad wifi can lock.
let nameTaken = false;
let asking = 0;
let pause = null;

document.getElementById("code").textContent = CODE;

start();

async function start() {
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
}

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
  take.disabled = !(stance && seat) || nameTaken;
}

name.addEventListener("input", () => {
  // Whatever the arena last said was about a name that is no longer in the box.
  aboutTheName(null);
  clearTimeout(pause);
  pause = setTimeout(askAboutTheName, TYPING_PAUSE);
});

async function askAboutTheName() {
  const typed = name.value.trim();
  if (!typed) return;
  const question = ++asking;
  let answer;
  try {
    answer = await get(`/api/players/available?name=${encodeURIComponent(typed)}`);
  } catch {
    // The join will say so for itself if the name really is taken. A phone that
    // cannot reach the arena, or a name the check will not answer about at all,
    // must not be reported to a manager as a name somebody else has.
    return;
  }
  // A later keystroke has already overtaken this one, so its answer is about a
  // name that is no longer in the box.
  if (question !== asking) return;
  aboutTheName(answer.available ? null : `${answer.name} is taken. Try another name.`);
}

email.addEventListener("input", () => aboutTheAddress(null));

function aboutTheName(trouble) {
  nameTaken = Boolean(trouble);
  mark(name, nameHint, trouble);
  ready();
}

function aboutTheAddress(trouble) {
  // Nothing here blocks the button the way a taken name does. The arena is the
  // only judge of what an address looks like, so the only way to find out
  // whether the next thing typed suits it is to send it.
  mark(email, emailHint, trouble);
}

function mark(box, hint, trouble) {
  hint.textContent = trouble || "";
  hint.hidden = !trouble;
  box.classList.toggle("wrong", Boolean(trouble));
}

/**
 * A refused join, said under the box that has to change if there is one.
 *
 * A 409 is the name every time: it is the only thing on this form that somebody
 * else can be holding. A 422 names its own fields, and the arena's two are both
 * boxes here -- but only a refusal about exactly one of them can go under a box,
 * because the sentence covers every problem it found and half of it under each
 * would be two wrong sentences.
 */
const BOXES = { display_name: aboutTheName, email: aboutTheAddress };

function sayWhereItBelongs(failure) {
  if (!(failure instanceof Refused)) throw failure;
  if (failure.status === 409) return aboutTheName(failure.message);
  const boxes = failure.fields.map((field) => BOXES[field]).filter(Boolean);
  if (boxes.length === 1 && boxes.length === failure.fields.length) {
    return boxes[0](failure.message);
  }
  complain(failure);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (take.disabled) return;
  // Everything the last tap was told is about to be answered again, and cleared
  // before the button is held down rather than after, because clearing a mark
  // reconsiders whether the button should be enabled.
  problem.hidden = true;
  aboutTheName(null);
  aboutTheAddress(null);
  take.disabled = true;
  // Nothing is going to answer about a name that is already being submitted.
  clearTimeout(pause);

  try {
    await post("/api/players", {
      display_name: name.value.trim(),
      email: email.value.trim(),
    });
  } catch (failure) {
    sayWhereItBelongs(failure);
    ready();
    return;
  }

  try {
    await post(`/api/rooms/${CODE}/seats/${seat}`, { philosophy: stance });
    location.assign(`/play?room=${encodeURIComponent(CODE)}`);
  } catch (failure) {
    complain(failure);
    ready();
  }
});

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

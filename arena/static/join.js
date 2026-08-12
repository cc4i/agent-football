/**
 * The join form: a name, an email and an opening stance, then a dugout.
 *
 * The room comes from the address the QR encoded, so there is nothing to type
 * and nothing to get wrong. Three calls in order: become a player, take a
 * seat, go to the dugout.
 */

import { get, post, Refused } from "/static/api.js";

const CODE = decodeURIComponent(location.pathname.split("/").pop() || "").toUpperCase();
const SIDE = { blue: "blue", red: "red" };

const form = document.getElementById("join");
const problem = document.getElementById("problem");
const pills = document.getElementById("pills");
const blurb = document.getElementById("blurb");
const take = document.getElementById("take");
const name = document.getElementById("name");
const email = document.getElementById("email");

let stance = null;
let seat = null;

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
    pill.textContent = `${entry.icon} ${entry.label}`;
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
  take.disabled = !(stance && seat);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (take.disabled) return;
  take.disabled = true;
  problem.hidden = true;
  try {
    await post("/api/players", {
      display_name: name.value.trim(),
      email: email.value.trim(),
    });
    await post(`/api/rooms/${CODE}/seats/${seat}`, { philosophy: stance });
    location.assign(`/play?room=${encodeURIComponent(CODE)}`);
  } catch (failure) {
    complain(failure);
    take.disabled = false;
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

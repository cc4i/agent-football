/**
 * A manager's own page: where they stand, and what they can walk into next.
 *
 * This is the far side of the printed code for everybody who has scanned it
 * once already, which by the middle of an event is nearly everybody. It has to
 * answer three questions in the order somebody actually has them: is my match
 * still on, where can I play, and how am I doing.
 *
 * The rooms are polled rather than pushed. What changes them is somebody
 * across the room opening a lobby on a screen, which is a several-second event
 * to begin with, and a socket per idle phone in the building costs more than
 * one small read every few seconds.
 */

import { get, Refused } from "/static/api.js";
import { figure, ordinal } from "/static/words.js";

// How often to look for a room that has opened. Slow enough to be nothing at
// all on the arena, quick enough that somebody watching a screen open a lobby
// sees it appear here before they think to reload.
const LOOK_AGAIN = 4000;

const who = document.getElementById("who");
const standing = document.getElementById("standing");
const problem = document.getElementById("problem");
const resume = document.getElementById("resume");
const resumeCode = document.getElementById("resume-code");
const rooms = document.getElementById("rooms");

const MODES = { solo: "Score attack", versus: "Head to head" };

look();
setInterval(() => {
  // A backgrounded tab is a phone in a pocket. Waking it to poll would drain a
  // battery all afternoon for a page nobody is looking at.
  if (!document.hidden) look();
}, LOOK_AGAIN);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) look();
});

async function look() {
  let me;
  try {
    me = await get("/api/players/me");
  } catch (failure) {
    // A phone with no session on a page that is entirely about one. That is
    // not an error to report, it is somebody who has not registered yet.
    if (failure instanceof Refused && failure.status === 401) {
      location.replace("/register");
      return;
    }
    return complain(failure);
  }

  who.textContent = me.display_name;
  showTheMatchTheyLeft(me.room);

  try {
    const [board, open] = await Promise.all([get("/api/board"), get("/api/rooms/open")]);
    standing.textContent = whereTheyStand(board, me);
    drawRooms(open.rooms.filter((room) => room.code !== (me.room && me.room.code)), me);
    problem.hidden = true;
  } catch (failure) {
    complain(failure);
  }
}

function showTheMatchTheyLeft(room) {
  resume.hidden = !room;
  if (!room) return;
  resumeCode.textContent = room.code;
  // A room still in its lobby has no dugout to go back to yet - the phone
  // belongs on the same screen it would have gone to after taking the seat.
  resume.href = `/play?room=${encodeURIComponent(room.code)}`;
  resume.querySelector("b").textContent =
    room.status === "live" ? "Back to your dugout" : "Back to the lobby";
  resume.querySelector(".eyebrow").textContent =
    room.status === "live" ? "Your match is still on" : "You have a seat in this one";
}

function whereTheyStand(board, me) {
  const place = board.solo.findIndex((run) => run.player_id === me.id);
  if (place >= 0) {
    const run = board.solo[place];
    return `${ordinal(place + 1)} of ${board.solo.length} on score attack`
      + ` · ${figure(run.points)} points on your best run`;
  }
  const head = board.versus.find((row) => row.player_id === me.id);
  if (head) {
    return `${record(head)} head to head · nothing on score attack yet`;
  }
  return "No runs on the board yet. Take a dugout and you are on it.";
}

function record(head) {
  const parts = [[head.won, "won"], [head.drew, "drawn"], [head.lost, "lost"]]
    .filter(([many]) => many)
    .map(([many, word]) => `${many} ${word}`);
  return parts.join(", ");
}

function drawRooms(open, me) {
  if (!open.length) {
    rooms.replaceChildren(nothingOpen(me));
    return;
  }
  rooms.replaceChildren(...open.map(card));
}

function card(room) {
  // An anchor rather than a button: a room is a page, and a long press should
  // offer to open it rather than do nothing.
  //
  // Naming the side is only honest when there is one left. An empty head to
  // head has both, and which one you take is a question for the join page.
  const seat = room.open_seats.length === 1 ? room.open_seats[0] : null;
  const link = document.createElement("a");
  link.className = seat ? `room ${seat}` : "room";
  link.href = `/join/${encodeURIComponent(room.code)}`;
  link.append(
    Object.assign(document.createElement("b"), { className: "mono", textContent: room.code }),
    Object.assign(document.createElement("span"), { textContent: waitingIn(room) }),
    Object.assign(document.createElement("em"),
      { textContent: seat ? `Take the ${seat} dugout` : "Take a dugout" }),
  );
  return link;
}

function waitingIn(room) {
  const mode = MODES[room.mode] || room.mode;
  const held = Object.values(room.seats);
  // Who is already in there, because "one seat left" and "Sam is waiting for
  // somebody" are the same fact and only one of them makes anybody walk over.
  return held.length ? `${mode} · ${held[0]} is waiting` : `${mode} · nobody in it yet`;
}

function nothingOpen(me) {
  const box = document.createElement("p");
  box.className = "roomless";
  box.textContent = me.room
    ? "Nothing else is open. Finish the match you are in."
    : "No screen is waiting for a manager this second. One appears here as soon as"
      + " somebody opens a lobby, or scan the code on the big screen itself.";
  return box;
}

function complain(failure) {
  if (!(failure instanceof Refused)) throw failure;
  problem.textContent = failure.message;
  problem.hidden = false;
}

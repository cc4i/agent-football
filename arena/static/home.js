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

import { get, post, Refused } from "/static/api.js";
import { keepScreenToken } from "/static/socket.js";
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
const ownRoom = document.getElementById("open-room");

/**
 * Open a room from the hand, which is the second way into the venue.
 *
 * A screen can open a room and holds one at a time, so the whole venue used to
 * be one page wide: the second manager to walk up to a screen with football on
 * it was told to wait for the match to end. Nothing on the arena had to change
 * to widen it -- `POST /api/rooms` has always answered anybody, and the token
 * it hands back has always been what vouches for the lobby.
 *
 * The token is kept before leaving, because the next page is the one that has
 * to hold the room: a lobby nobody is behind is swept in HOST_GONE_SECONDS,
 * and picking a philosophy takes longer than that if the phone rings.
 */
for (const button of ownRoom.querySelectorAll("[data-mode]")) {
  button.addEventListener("click", async () => {
    for (const other of ownRoom.querySelectorAll("[data-mode]")) other.disabled = true;
    problem.hidden = true;
    try {
      const opened = await post("/api/rooms", { mode: button.dataset.mode });
      keepScreenToken(opened.code, opened.screen_token);
      location.assign(`/join/${encodeURIComponent(opened.code)}`);
    } catch (failure) {
      // The venue's own limits speak here: too many rooms too fast from one
      // address, or a venue already at MAX_LIVE_ROOMS. Both say so in words a
      // manager can act on, so they are shown rather than translated.
      complain(failure);
      for (const other of ownRoom.querySelectorAll("[data-mode]")) other.disabled = false;
    }
  });
}

const MODES = { solo: "Score attack", versus: "Head to head" };
// The same two, in the middle of a sentence rather than at the head of a line.
const WORDS = { solo: "score attack", versus: "head to head" };
const OTHER = { solo: "versus", versus: "solo" };

// Rooms this phone has asked to be turned, code -> the mode it asked for. Here
// rather than on the card, because the list is rebuilt from scratch every poll
// and an ask that vanished four seconds after it was made would read as one
// that never went.
const askedFor = new Map();

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
  if (me.recovery_code) {
    document.getElementById("code-value").textContent = me.recovery_code;
    document.getElementById("recovery-code").hidden = false;
  }
  showTheMatchTheyLeft(me.room);

  try {
    const [board, open] = await Promise.all([get("/api/board"), get("/api/rooms/open")]);
    standing.textContent = whereTheyStand(board, me);
    drawRooms(open.rooms.filter((room) => room.code !== (me.room && me.room.code)),
              open.playing, me);
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

function drawRooms(open, playing, me) {
  // A room that now plays what this phone asked for has been turned, so the
  // ask is spent. Anything no longer on the list has closed and takes its ask
  // with it, which is also what keeps this map from growing all evening.
  const still = new Set(open.map((room) => room.code));
  for (const [code, mode] of askedFor) {
    const room = open.find((open_room) => open_room.code === code);
    if (!still.has(code) || (room && room.mode === mode)) askedFor.delete(code);
  }
  if (!open.length) {
    rooms.replaceChildren(nothingOpen(playing, me));
    return;
  }
  rooms.replaceChildren(...open.map(card));
}

function card(room) {
  const box = document.createElement("div");
  box.className = "room-card";
  box.append(joinLink(room), askChip(room));
  return box;
}

function joinLink(room) {
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

/**
 * The way to want the other mode without being the screen that opened it.
 *
 * A screen guesses what a room plays before anybody has walked up to it, and
 * it is the only thing that may change that guess. So a queue of people who
 * want head to head, at a venue whose screens all opened score attack, had
 * nothing to tap and no way to say so - the choice was already made, by
 * somebody who had not met them. This asks. The screen still decides.
 */
function askChip(room) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "ask-mode";
  const asked = askedFor.get(room.code);
  if (asked) {
    chip.textContent = `Asked for ${WORDS[asked]} · the screen decides`;
    chip.disabled = true;
    return chip;
  }
  const other = OTHER[room.mode];
  chip.textContent = `Ask for ${WORDS[other]}`;
  chip.addEventListener("click", () => askFor(room.code, other, chip));
  return chip;
}

async function askFor(code, mode, chip) {
  chip.disabled = true;
  try {
    await post(`/api/rooms/${code}/mode-request`, { mode });
    askedFor.set(code, mode);
    chip.textContent = `Asked for ${WORDS[mode]} · the screen decides`;
    problem.hidden = true;
  } catch (failure) {
    // Put the chip back: every refusal here is one the same tap could pass a
    // moment later, once somebody stands up or the screen catches up.
    chip.disabled = false;
    complain(failure);
  }
}

function waitingIn(room) {
  const mode = MODES[room.mode] || room.mode;
  const held = Object.values(room.seats);
  // Who is already in there, because "one seat left" and "Sam is waiting for
  // somebody" are the same fact and only one of them makes anybody walk over.
  return held.length ? `${mode} · ${held[0]} is waiting` : `${mode} · nobody in it yet`;
}

/**
 * The empty state, which is three different situations wearing one face.
 *
 * A screen holds one room and one room seats one or two managers, so the
 * fourth person through the door finds nothing to tap - and the sentence they
 * used to find said no screen was waiting, which is what a venue with nothing
 * plugged in says too. They had no way to tell "two minutes" from "broken",
 * and the honest answer was standing on the wall in front of them.
 *
 * Both of those sentences then told somebody to wait, which was true when a
 * screen was the only thing that could open a room and is not any more: the
 * control underneath this is one tap from a room of their own. So what is left
 * to say here is only which of the three situations it is, and the way out is
 * the same in every one.
 */
function nothingOpen(playing, me) {
  const box = document.createElement("p");
  box.className = "roomless";
  box.textContent = me.room
    ? "Nothing else is open. Finish the match you are in."
    : playing.length
      ? `${onNow(playing)} Nothing is waiting for a manager, so start your own below.`
      : "Nobody has a room open this second. Start one below.";
  return box;
}

/**
 * What is being played, named while there is one match to name.
 *
 * A live room has its dugouts filled, which is what let it kick off, so the
 * one match case is a person to point at. The red dugout of a score attack is
 * the house side and is not.
 */
function onNow(playing) {
  const [match] = playing;
  const named = playing.length === 1
    && [match.blue, match.red].filter(Boolean).join(" vs ");
  if (named) return `${named} ${match.red ? "are" : "is"} playing right now.`;
  const many = playing.length > 1;
  return `${figure(playing.length)} match${many ? "es are" : " is"} on right now.`;
}

function complain(failure) {
  if (!(failure instanceof Refused)) throw failure;
  problem.textContent = failure.message;
  problem.hidden = false;
}

/**
 * The standings, on a wall or in a hand.
 *
 * Two boards that never merge, because beating the shipped squad and beating
 * a person are not the same achievement. Nothing here computes a point: the
 * arena scored every match from its own log, and this page renders what it
 * says.
 *
 * It spends most of an event unattended, so it swaps boards on its own and
 * refetches whenever a match ends anywhere in the venue. A click stops the
 * cycle, because somebody reading a table should not have it slide away.
 */

import { get } from "/static/api.js";
import { icon } from "/static/dom.js";
import { figure } from "/static/words.js";
import { openWall } from "/static/socket.js";

const CYCLE_MS = 12_000;
const MEDALS = ["🥇", "🥈", "🥉"];
const PODIUM = ["g", "s", "br"];   // gold in the middle, which is what `order` does
const OUTCOME = { won: "Won", drew: "Drew", lost: "Lost" };
const OUTCOME_TAG = { won: "w", drew: "d", lost: "l" };

const el = (id) => document.getElementById(id);
const tabs = { solo: el("tab-solo"), versus: el("tab-versus") };

let showing = "solo";
let pinned = false;      // somebody clicked a tab, so the page stopped cycling
let cycle = null;

// On a wall or in a hand, decided by the one thing that cannot be got wrong by
// whoever linked here. The big screen frames this page under its lobby, and a
// wall has nowhere to go home to; a phone arrived from home, the join form or
// the result sheet, and every one of those was a one-way trip.
if (window.self === window.top) el("board-home").hidden = false;

for (const [which, tab] of Object.entries(tabs)) {
  tab.addEventListener("click", () => {
    pinned = true;
    el("tick").hidden = true;
    clearTimeout(cycle);
    show(which);
  });
}

/**
 * The big screen framing this page, telling it which board to hold up.
 *
 * Only ever from the page that framed it, and only ever the one message. The
 * announcer talks over this frame for forty seconds, and the twelve-second
 * cycle would otherwise slide the board away in the middle of a sentence
 * about it.
 */
window.addEventListener("message", (note) => {
  if (note.origin !== location.origin) return;
  if (!note.data || note.data.type !== "board.show") return;
  if (note.data.pinned === false) return unpin();
  pinned = true;
  el("tick").hidden = true;
  clearTimeout(cycle);
  show(note.data.board === "versus" ? "versus" : "solo");
});

function unpin() {
  pinned = false;
  el("tick").hidden = false;
  turn();
}

load();
listen();

function listen() {
  // The wall topic carries a message at every kick-off and every whistle, which
  // is exactly when a board changes. Polling would either lag the venue or
  // hammer it, and the socket is already there.
  openWall({
    onMessage: (message) => { if (message.type === "wall") load(); },
    onDrop: (reason, permanent) => { if (permanent) complain(reason); },
  });
}

async function load() {
  try {
    const board = await get("/api/board");
    el("problem").hidden = true;
    el("where").textContent =
      `arena · board · ${count(board.managers, "manager", "managers", "nobody yet")}`;
    drawSolo(board.solo);
    drawVersus(board.versus);
    if (!pinned) turn();
  } catch (failure) {
    complain(failure.message);
  }
}

/* ── Which board is up ──────────────────────────────────────────────── */

function show(which) {
  showing = which;
  for (const [name, tab] of Object.entries(tabs)) {
    tab.setAttribute("aria-selected", String(name === which));
    el(`view-${name}`).hidden = name !== which;
  }
}

function turn() {
  clearTimeout(cycle);
  restartTick();
  cycle = setTimeout(() => {
    show(showing === "solo" ? "versus" : "solo");
    turn();
  }, CYCLE_MS);
}

function restartTick() {
  const bar = el("tick").firstElementChild;
  // Restarted by hand rather than by CSS class, because a fetch landing
  // mid-cycle resets the clock and the bar has to agree with it.
  bar.style.animation = "none";
  void bar.offsetWidth;
  bar.style.animation = `tick ${CYCLE_MS}ms linear forwards`;
}

/* ── Score attack ───────────────────────────────────────────────────── */

function drawSolo(runs) {
  podium("podium-solo", runs, (run) => ({
    lines: [`${count(run.goals_for, "goal", "goals", "No goals")} · ${run.goals_against} conceded`,
            run.first_goal_ms === null ? "never scored" : `first at ${clock(run.first_goal_ms)}`],
    score: figure(run.points),
    unit: "points",
  }));

  table("table-solo", runs, {
    empty: ["Nobody has run at the house side yet",
            "Scan the code on the arena screen and the first place is yours."],
    columns: [
      { head: "Opening shape", draw: (run) => cell("", titled(run.philosophy)) },
      { head: "Goals", draw: (run) => cell("num", `${run.goals_for}-${run.goals_against}`) },
      { head: "First goal",
        draw: (run) => cell("num", run.first_goal_ms === null ? "-" : clock(run.first_goal_ms)) },
      // "0 of 0" is a column of noise on a board where most runs are silent.
      { head: "Shouts that scored",
        draw: (run) => cell("num", run.shouts ? `${run.effective} of ${run.shouts}` : "-") },
    ],
    total: { head: "Points", draw: (run) => cell("tot", figure(run.points)) },
  });
}

/* ── Head to head ───────────────────────────────────────────────────── */

function drawVersus(table_) {
  podium("podium-versus", table_, (standing) => ({
    lines: [`${count(standing.played, "match", "matches", "none played")} · ${signed(standing.difference)} goal difference`],
    score: `${standing.won} - ${standing.drew} - ${standing.lost}`,
    unit: "won drew lost",
  }));

  table("table-versus", table_, {
    empty: ["No duels yet",
            "Two phones in one room and the winner takes this board."],
    columns: [
      { head: "Played", draw: (one) => cell("num", String(one.played)) },
      { head: "Record", draw: (one) => cell("num", `${one.won}-${one.drew}-${one.lost}`) },
      { head: "Goals", draw: (one) => cell("num", `${one.goals_for}-${one.goals_against}`) },
      { head: "Difference", draw: (one) => cell("num", signed(one.difference)) },
      { head: "Last match", draw: (one) => lastMatch(one.last) },
    ],
    // A duel one dugout never turned up for pays no rating, so there can be a
    // standing here with nothing to show in this column.
    total: { head: "Rating",
             draw: (one) => cell("tot", one.rating === null ? "-"
                                                            : figure(Math.round(one.rating))) },
  });
}

function lastMatch(last) {
  const box = document.createElement("td");
  if (!last) return box;
  const tag = document.createElement("span");
  tag.className = `tag ${OUTCOME_TAG[last.outcome]}`;
  tag.textContent = OUTCOME[last.outcome];
  box.append(tag, ` ${last.goals_for}-${last.goals_against}`);
  // A duel always has somebody on the other side, but a name can be missing if
  // the other dugout's result has not been written; saying "v " and stopping
  // would read as a bug.
  if (last.against) box.append(` v ${last.against}`);
  return box;
}

/* ── The two shapes both boards are drawn in ────────────────────────── */

function podium(into, rows, describe) {
  const three = rows.slice(0, 3);
  el(into).replaceChildren(...three.map((row, place) => {
    const pod = document.createElement("div");
    pod.className = `pod ${PODIUM[place]}`;
    const medal = icon(MEDALS[place]);
    medal.classList.add("medal");
    const name = document.createElement("div");
    name.className = "nm";
    name.textContent = row.name;

    const told = describe(row);
    const state = document.createElement("div");
    state.className = "st";
    told.lines.forEach((line, index) => {
      if (index) state.append(document.createElement("br"));
      state.append(line);
    });
    const score = document.createElement("div");
    score.className = "sc";
    score.append(told.score,
                 Object.assign(document.createElement("small"), { textContent: told.unit }));

    pod.append(medal, name, ...masked("div", "em", row.email), state, score);
    return pod;
  }));
}

/**
 * One board as a table: rank, manager, the detail, and the number it is on.
 *
 * The detail columns are marked `.det` and a phone drops them, because eight
 * columns on a 375px screen either shrink to nothing or scroll sideways under
 * a page that already scrolls down. Rank, who, and the figure the board is
 * ordered by survive, which is the board.
 */
function table(into, rows, { empty, columns, total }) {
  if (!rows.length) return el(into).replaceChildren(nobody(...empty));

  const header = document.createElement("tr");
  header.append(head("#", "rk"), head("Manager"),
                ...columns.map((column) => head(column.head, "det")),
                head(total.head, "tot"));

  const body = document.createElement("tbody");
  body.append(...rows.map((one, index) => {
    const line = document.createElement("tr");
    line.append(cell(index ? "rk" : "rk top", String(index + 1)), who(one));
    for (const column of columns) line.append(detail(column.draw(one)));
    line.append(total.draw(one));
    return line;
  }));

  const grid = document.createElement("table");
  grid.className = "table";
  grid.append(document.createElement("thead"), body);
  grid.firstElementChild.append(header);
  el(into).replaceChildren(grid);
}

function head(label, className) {
  const th = document.createElement("th");
  if (className) th.className = className;
  th.textContent = label;
  return th;
}

function detail(box) {
  box.classList.add("det");
  return box;
}

function who(one) {
  const box = cell("who-cell", "");
  const name = document.createElement("b");
  name.textContent = one.name;
  box.append(name, ...masked("span", "", one.email));
  return box;
}

/**
 * The masked address under a manager's name, as nothing or as one element.
 *
 * An address is optional, so a great many rows have none, and an empty element
 * is not the same as no element: on the podium it is a margin and a line box
 * where the managers either side have a line of text, which reads as a
 * rendering fault rather than as somebody who kept their address to themselves.
 */
function masked(tag, className, address) {
  if (!address) return [];
  const line = document.createElement(tag);
  if (className) line.className = className;
  line.textContent = address;
  return [line];
}

function cell(className, text) {
  const box = document.createElement("td");
  if (className) box.className = className;
  box.textContent = text;
  return box;
}

function nobody(title, line) {
  const box = document.createElement("div");
  box.className = "board-empty";
  box.append(Object.assign(document.createElement("b"), { textContent: title }),
             Object.assign(document.createElement("span"), { textContent: line }));
  return box;
}

/* ── Numbers as a manager reads them ────────────────────────────────── */

const signed = (value) => (value > 0 ? `+${value}` : String(value));
const titled = (word) => (word ? word.charAt(0).toUpperCase() + word.slice(1) : "-");

// Both forms spelled out rather than an -s rule, which turns two matches into
// two matchs.
function count(many, one, more, none) {
  return many ? `${many} ${many === 1 ? one : more}` : none;
}

function clock(ms) {
  const whole = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

function complain(message) {
  el("problem").textContent = message;
  el("problem").hidden = false;
}

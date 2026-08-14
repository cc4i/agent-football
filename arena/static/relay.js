/**
 * The relay: what a manager said, and what the squad did about it.
 *
 * One shout becomes a block -- the words, the three hops of the chain under
 * them, and four branches off the last hop for the players who answered. The
 * same block is drawn in a manager's hand and on the big screen's right rail,
 * so it lives here rather than in either page.
 *
 * A feed renders; it decides nothing. Which shouts it is shown, whose side is
 * "you", and what the page does about a queue are all the caller's, which is
 * what lets one rail carry the blue dugout and the one beside it carry red.
 */

import { icon } from "/static/dom.js";

const ROLE_TAGS = { defender: "DEF", midfielder: "MID", forward: "FWD", goalkeeper: "GK" };
// Front to back, which is the order a manager reads a squad in.
const ROLES = ["forward", "midfielder", "defender", "goalkeeper"];
const SIDE_LABEL = { blue: "Blue", red: "Red" };
// The trunk of the chain, top to bottom, with what each hop says before it has
// anything of its own to report.
const RUNGS = [
  ["coach", "Coach", "Relaying over A2A"],
  ["captain", "Captain", "Waiting on the coach"],
  ["squad", "Squad", "Waiting on the brief"],
];

/**
 * Open a feed into an element.
 *
 * @param {object} options
 * @param {HTMLElement} options.into - where blocks are prepended.
 * @param {?string} options.mine - the team whose shouts read as "You".
 * @param {?string} options.only - render this team's shouts and no other.
 * @param {boolean} options.goals - whether a goal gets a banner in the feed.
 * @param {?function} options.onQueued - (team, seq, words) the venue is busy.
 * @param {?function} options.onHuddle - (team) a chain of theirs has ended.
 * @param {?function} options.onCount - (n) shouts drawn so far.
 */
export function relayFeed({
  into, mine = null, only = null, goals = true,
  onQueued = null, onHuddle = null, onCount = null,
} = {}) {
  const shouts = new Map();  // shout seq -> the block of the feed it owns

  /** An entry off the room's log. Anything that is not the relay's is ignored. */
  function event(message) {
    const payload = message.payload || {};
    if (only && payload.team && payload.team !== only) return;
    if (message.kind === "shout.sent") return drawShout(message);
    if (message.kind === "profile.patch") return drawPatch(message);
    if (message.kind === "substitution") return drawCondition(message);
    if (goals && message.kind === "goal") return drawGoal(message);
  }

  function drawShout(message) {
    if (shouts.has(message.seq)) return;
    const said = document.createElement("div");
    said.className = `said${message.payload.team === mine ? "" : " away"}`;
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = message.payload.team === mine
      ? "You" : SIDE_LABEL[message.payload.team];
    const quote = document.createElement("q");
    quote.textContent = message.payload.text;
    said.append(tag, quote);

    const fan = document.createElement("div");
    fan.className = "fan";
    shouts.set(message.seq, { said, fan, chain: null, rungs: {}, branches: new Map() });
    // Newest at the top: on a handset the thing you just did should not be the
    // thing you have to scroll to find.
    into.prepend(said, fan);
    if (onCount) onCount(shouts.size);
  }

  function drawPatch(message) {
    const block = shouts.get(message.payload.shout_seq);
    // Kick-off stances move the same profiles and belong to no shout. The squad
    // did not answer them, so the relay does not pretend they did.
    if (!block) return;
    const branch = branchFor(block, message.payload.role);
    // A chip and a shout replayed out of the log have no chain to light the
    // branch, so the write itself is the answer. A live chain lights its own.
    if (!branch.state) mark(block, message.payload.role, "done", "");
    branch.body.append(deltas(message.payload.changed));
  }

  /**
   * A player agent reporting on itself: a knock, or a request to come off.
   *
   * A banner in the dugout that owns the player rather than a toast over the
   * pitch. A toast was right when this was a poll on the one browser running
   * the match; it is wrong for a log every screen and every phone reads, where
   * a wall cutting to an hour-old match would flash a dozen of them at once.
   * Where the goals go, so it is still there thirty seconds later when the
   * manager looks up.
   */
  function drawCondition(message) {
    const { team, role, action, detail } = message.payload;
    const hurt = action === "injury";
    const banner = document.createElement("div");
    banner.className = `banner ${hurt ? "warn" : "info"}`;
    const words = document.createElement("div");
    const who = document.createElement("b");
    // On the big screen the column is the dugout, so naming it again would be
    // saying "Blue" twice. In a hand there is one column for both.
    const whose = mine ? (team === mine ? "Your " : "Their ") : "";
    who.textContent = `${whose}${ROLE_TAGS[role] || role} `
      + (hurt ? "is hurt" : "wants to come off");
    words.append(who, detail || "");
    banner.append(icon(hurt ? "🚑" : "🔁"), words);
    into.prepend(banner);
  }

  function drawGoal(message) {
    const banner = document.createElement("div");
    banner.className = "banner warn";
    const team = message.payload && message.payload.team;
    const words = document.createElement("div");
    words.textContent = team === mine ? "You scored." : "They scored.";
    banner.append(icon("⚽"), words);
    into.prepend(banner);
  }

  /* ── The chain a shout travels down ───────────────────────────────── */

  function relayed(message) {
    const block = shouts.get(message.seq);
    if (!block) return;
    if (message.type === "relay.waiting") {
      if (onQueued) {
        onQueued(message.team, message.seq,
                 `The venue is busy - ${message.ahead} ahead of you`);
      }
      return;
    }
    // This shout is moving, so whatever the page was saying about it is over.
    if (onQueued) onQueued(message.team, message.seq, "");
    wire(block);

    if (message.type === "relay.coach") {
      return rung(block, "coach", message.state === "done" ? "done" : "live",
                  message.state === "done" ? "Handed to the captain" : "Relaying over A2A");
    }
    if (message.type === "relay.captain") {
      if (message.state !== "thinking") return rung(block, "captain", "done", "Briefed the squad");
      rung(block, "coach", "done", "Handed to the captain");
      rung(block, "captain", "live", "Briefing the squad");
      // The four go out together, so they all start waiting together and the
      // branches exist to be lit before any of them has said anything.
      for (const role of ROLES) mark(block, role, "live", "");
      return tally(block, false);
    }
    if (message.type === "relay.specialist") {
      mark(block, message.role, message.state, message.text || "");
      return tally(block, false);
    }
    if (message.type === "relay.trouble") {
      // The signal can die at any hop, so the trouble lands on whichever one was
      // still carrying it rather than always on the coach.
      const stuck = RUNGS.map(([name]) => name).find((name) => block.rungs[name].state !== "done");
      return rung(block, stuck || "squad", "failed", message.text);
    }
    if (message.type === "relay.huddle") {
      if (onHuddle) onHuddle(message.team);
      for (const [role, line] of Object.entries(message.huddle || {})) mark(block, role, "done", line);
      // Whatever is still lit when the chain ends never answered. A chain that
      // died before the brief went out has no branches, and inventing four
      // silent players would blame the squad for something upstream of them.
      for (const role of ROLES) if (block.branches.has(role)) mark(block, role, "missing", "");
      if (message.state === "done") {
        rung(block, "coach", "done", "Handed to the captain");
        rung(block, "captain", "done", message.status || "Briefed the squad");
      }
      tally(block, true);
    }
  }

  function wire(block) {
    // Built when the first word of a chain arrives rather than when the shout is
    // drawn. A shout replayed out of the log after a reload has no chain to
    // replay: what the squad did is logged and shows, what it said on the way is
    // a progress report for whoever was watching at the time.
    if (block.chain) return;
    const chain = document.createElement("ol");
    chain.className = "chain";
    for (const [name, who, waiting] of RUNGS) {
      const item = document.createElement("li");
      item.className = "rung";
      const row = document.createElement("div");
      row.className = "rung-row";
      const label = document.createElement("span");
      label.className = "rung-who";
      label.textContent = who;
      const note = document.createElement("span");
      note.className = "rung-what";
      note.textContent = waiting;
      row.append(label, note);
      item.append(row);
      chain.append(item);
      block.rungs[name] = { item, note, state: "" };
    }
    // The fan moves inside the last rung, so the four branches hang off the same
    // wire the coach and the captain sit on. That fan-out is real: the captain
    // briefs a ParallelAgent.
    block.rungs.squad.item.append(block.fan);
    block.said.after(chain);
    block.chain = chain;
  }

  function rung(block, name, state, what) {
    const found = block.rungs[name];
    // A hop that failed stays failed, and keeps saying why: a later message
    // about the rest of the chain must not paper over where it broke.
    if (!found || found.state === "failed") return;
    found.state = state;
    found.item.className = `rung ${state}`;
    found.note.textContent = what;
  }

  function branchFor(block, role) {
    let found = block.branches.get(role);
    if (found) return found;
    const branch = document.createElement("div");
    branch.className = "branch";
    const tag = document.createElement("span");
    tag.className = "br-role";
    tag.textContent = ROLE_TAGS[role] || role;
    const body = document.createElement("div");
    body.className = "br-body";
    branch.append(tag, body);
    block.fan.append(branch);
    found = { branch, body, line: null, state: "" };
    block.branches.set(role, found);
    return found;
  }

  function mark(block, role, state, words) {
    const found = branchFor(block, role);
    // A player who has answered is not un-answered by anything that lands after,
    // and their own words beat the captain's summary of them.
    if (found.state === "done") return;
    found.state = state;
    found.branch.className = `branch ${state}`;
    if (state === "done" && !words) return;   // a chip's branch: what moved is the answer
    const line = document.createElement(words ? "q" : "span");
    if (!words) line.className = "br-wait";
    line.textContent = words || (state === "missing" ? "no answer" : "thinking");
    if (found.line) found.line.replaceWith(line);
    else found.body.prepend(line);
    found.line = line;
  }

  function tally(block, ended) {
    const answered = [...block.branches.values()].filter((one) => one.state === "done").length;
    // The huddle completes on three, so a chain that ended with one player quiet
    // is done rather than failed.
    rung(block, "squad", ended ? (answered ? "done" : "failed") : "live",
         ended ? `${answered} of 4 answered`
               : answered ? `${answered} of 4 in`
                          : "Four players, in parallel");
  }

  return {
    event,
    relayed,
    /** Start again on another match. The blocks belong to the room they came from. */
    clear() {
      shouts.clear();
      into.replaceChildren();
      if (onCount) onCount(0);
    },
  };
}

function deltas(changed) {
  const row = document.createElement("div");
  row.className = "deltas";
  for (const [attribute, value] of Object.entries(changed || {})) {
    const delta = document.createElement("span");
    delta.className = "delta";
    delta.append(spaced(attribute), Object.assign(document.createElement("b"),
                                                  { textContent: round(value) }));
    row.append(delta);
  }
  return row;
}

// "pressingIntensity" reads as one long word on a 375px screen.
const spaced = (attribute) => `${attribute.replace(/([A-Z])/g, " $1").toLowerCase()} `;
const round = (value) => (typeof value === "number" ? String(Math.round(value * 100) / 100) : value);

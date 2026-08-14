const log = document.querySelector('.log');
const stagesEl = document.querySelector('.stages');
const input = document.querySelector('#say');
const sendBtn = document.querySelector('.send');
const haltBtn = document.querySelector('.halt');
const restartBtn = document.querySelector('.restart');
const acting = document.querySelector('.acting');

const ACTOR_CLASS = { user: 'a-you', antigravity: 'a-agy', game: 'a-sys' };
const ACTOR_LABEL = { user: 'You', antigravity: 'Antigravity', game: 'Game' };
const VERB = {
  generate_team_avatars: 'Called', get_match_status: 'Called',
  read_player_stats: 'Called', create_file: 'Wrote', edit_file: 'Edited',
  run_command: 'Ran', start_subagent: 'Started',
};

let lastActor = null;
let eventCount = 0;
let tokenCount = null;
let matchClock = null;
let skills = [];
// The quest stage each skill belongs to.
const SKILL_STAGES = { tune_the_squad: true };
let inFlight = null;

const label = (actor) =>
  actor.startsWith('subagent:') ? actor.slice(9).replace('-tuner', '') : (ACTOR_LABEL[actor] || actor);

const actorClass = (actor) =>
  actor.startsWith('subagent:') ? 'a-agy' : (ACTOR_CLASS[actor] || 'a-agy');

function addEvent(actor, minute, node) {
  document.querySelector('.empty-state')?.remove();

  const ev = document.createElement('div');
  ev.className = `ev ${actorClass(actor)}`;
  const same = actor === lastActor;
  lastActor = actor;

  const minDiv = el('div', 'min');
  const minB = el('b', null, minute);
  const whoSpan = el('span', same ? 'who cont' : 'who', same ? '·' : label(actor));
  minDiv.append(minB, whoSpan);

  const bodyDiv = el('div', 'body');
  bodyDiv.append(node);

  ev.append(minDiv, bodyDiv);
  log.append(ev);
  log.scrollTop = log.scrollHeight;

  eventCount++;
  document.querySelector('#event-count').textContent = `${eventCount} event${eventCount === 1 ? '' : 's'}`;
}

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

// **bold** and `code`, the only markdown the models reach for mid-sentence.
// Anything unpaired is left as the characters it is: a half-typed marker is
// what a streaming answer looks like a keystroke before it closes.
const MARKUP = /\*\*([^*]+)\*\*|`([^`]+)`/g;

function richBody(source) {
  // Model output, so it is built as DOM nodes and never innerHTML. Its own
  // line breaks are kept, because the answers are lists as often as prose and
  // one long run-on paragraph is not what was written.
  const out = document.createDocumentFragment();
  const lines = String(source).split('\n').filter((line) => line.trim());
  for (const [i, line] of lines.entries()) {
    if (i) out.append(el('br'));
    let at = 0;
    for (const found of line.matchAll(MARKUP)) {
      if (found.index > at)
        out.append(document.createTextNode(line.slice(at, found.index)));
      out.append(found[1] ? el('b', null, found[1]) : el('code', null, found[2]));
      at = found.index + found[0].length;
    }
    if (at < line.length) out.append(document.createTextNode(line.slice(at)));
  }
  return out;
}

function richText(cls, source) {
  const p = el('p', cls);
  p.append(richBody(source));
  return p;
}

const LANE_CLASS = { defender: 'def', midfielder: 'mid', forward: 'fwd', goalkeeper: 'gk' };
const TUNERS = Object.keys(LANE_CLASS).map((role) => `${role}-tuner`);
const PANEL_HEAD = {
  'a-agy': ['Antigravity subagents', 'one player each'],
  'a-sys': ["The game's agents", 'four player agents, through the coach'],
};

// One panel per turn per system, so four subagents working at once read as
// four lanes filling in rather than a dozen scattered log entries.
let panels = {};

const at = (node, pct) => { node.style.left = `${pct}%`; return node; };
const fmt = (n) => (typeof n === 'number' ? String(Number(n.toFixed(3))) : String(n));

function panelFor(actor, minute) {
  const family = actorClass(actor);
  if (panels[family]) return panels[family];

  const [title, note] = PANEL_HEAD[family] || PANEL_HEAD['a-agy'];
  const head = el('div', 'lanes-hd');
  head.append(el('b', null, title), el('span', null, note));
  const lanes = el('div', 'lanes');
  const wrap = el('div');
  wrap.append(head, lanes);

  panels[family] = { lanes, byRole: {} };
  addEvent(family === 'a-sys' ? actor : 'antigravity', minute, wrap);
  return panels[family];
}

function laneFor(panel, role, where) {
  if (panel.byRole[role]) return panel.byRole[role];

  const lane = el('div', `lane ${LANE_CLASS[role] || ''}`);
  const header = el('header');
  header.append(el('i'), el('b', null, role));
  const working = el('div', 'working');
  working.append(el('i'), el('span', null, 'working…'));
  const rows = el('div', 'rows');
  const whys = el('div', 'whys');
  // Where the squad lives now: a room and a dugout in the arena, not a file.
  lane.append(header, el('div', 'where', where || `WRKS/blue/${role}`),
              working, rows, whys);

  panel.lanes.append(lane);
  panel.byRole[role] = { working, rows, whys, byAttribute: {} };
  return panel.byRole[role];
}

function barNode(d) {
  // The bar reinforces the numbers beside it, so it carries the same reading
  // for anyone who cannot see it.
  const bar = el('div', 'bar');
  bar.setAttribute('role', 'img');
  bar.setAttribute('aria-label',
    `${d.attribute} moved from ${d.before == null ? 'unset' : fmt(d.before)}`
    + ` to ${fmt(d.after)}, allowed ${fmt(d.min)} to ${fmt(d.max)}`
    + (d.baseline == null ? '' : `, shipped ${fmt(d.baseline)}`));

  if (d.baselinePct != null) bar.append(at(el('i', 'tick'), d.baselinePct));
  if (d.beforePct != null) {
    const moved = at(el('i', 'moved'), Math.min(d.beforePct, d.afterPct));
    moved.style.width = `${Math.abs(d.afterPct - d.beforePct)}%`;
    bar.append(moved, at(el('i', 'was'), d.beforePct));
  }
  bar.append(at(el('i', `now${d.off ? ' off' : ''}`), d.afterPct));
  return bar;
}

function deltaRow(d) {
  const row = el('div', 'row');
  const line = el('div', 'delta');
  const values = el('span');
  values.append(el('s', null, d.before == null ? '-' : fmt(d.before)),
                document.createTextNode(' → '),
                el('em', null, fmt(d.after)));
  const name = el('u', null, d.attribute);
  name.title = d.attribute;  // the lane is narrow enough to truncate a long one
  line.append(name, values);
  row.append(line, barNode(d));
  return row;
}

function drawTuning(actor, minute, entries) {
  const panel = panelFor(actor, minute);
  for (const entry of entries) {
    const lane = laneFor(panel, entry.role, entry.where);
    lane.working.remove();
    for (const d of entry.deltas) {
      // A second call touching the same attribute keeps one row. What the
      // manager wants measured from is the first value, not the last.
      const seen = lane.byAttribute[d.attribute];
      const merged = seen ? { ...d, before: seen.before, beforePct: seen.beforePct } : d;
      const row = deltaRow(merged);
      if (seen) seen.row.replaceWith(row); else lane.rows.append(row);
      lane.byAttribute[d.attribute] =
        { row, before: merged.before, beforePct: merged.beforePct };
    }
    if (entry.reason) lane.whys.append(el('p', 'why', entry.reason));
    if (entry.violations) {
      // .out .bad is a descendant selector (chat.css:150), so the red text
      // has to be a child node rather than a second class on the same node.
      const out = el('pre', 'out');
      out.append(el('span', 'bad', entry.violations.join('\n')));
      lane.whys.append(out);
    }
  }
  log.scrollTop = log.scrollHeight;
}

function startedTuners(args) {
  // The SDK does not name the subagent in a field worth relying on, so the
  // four tuner names are matched against the whole argument blob.
  const blob = JSON.stringify(args ?? {});
  return TUNERS.filter((name) => blob.includes(name));
}

function toolCallNode(payload) {
  const wrap = el('div', 'act');
  wrap.append(el('span', 'verb', VERB[payload.name] || 'Called'));
  const call = el('span', 'call');
  call.append(el('span', 'fn', payload.name));
  // A tune's arguments are the changed numbers, and the lanes panel draws
  // those properly a moment later. Printing the raw JSON here as well is the
  // clutter this widget exists to remove.
  if (!payload.name.startsWith('tune_'))
    call.append(el('span', 'arg', `(${JSON.stringify(payload.args ?? {})})`));
  wrap.append(call);
  return wrap;
}

function kitNode(payload) {
  // Show the strip that was just generated. Served by the game on :5173, with
  // the timestamp busting the cache so a re-run does not show the old kit.
  const box = el('div', 'kit');
  box.append(el('span', 'verb', 'Kit'));
  for (const src of payload.images) {
    const img = document.createElement('img');
    img.src = `http://localhost:5173${src}?t=${payload.at}`;
    img.alt = `${payload.team} team kit`;
    box.append(img);
  }
  return box;
}

function skillBody(source) {
  // Kept in a <pre> so the tables stay aligned, but the markdown markers
  // themselves are noise. Built as nodes, never innerHTML.
  const out = document.createDocumentFragment();
  for (const line of String(source).split('\n')) {
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      out.append(el('b', 'skill-h', heading[2]), document.createTextNode('\n'));
      continue;
    }
    for (const [i, part] of line.split('**').entries()) {
      if (part) out.append(i % 2 ? el('b', null, part) : document.createTextNode(part));
    }
    out.append(document.createTextNode('\n'));
  }
  return out;
}

function openSkill(skill) {
  const sheet = document.querySelector('.skill-sheet');
  sheet.querySelector('.skill-name').textContent = skill.name;
  sheet.querySelector('.skill-desc').textContent = skill.description;
  sheet.querySelector('.skill-body').replaceChildren(skillBody(skill.body));
  sheet.hidden = false;
}

async function loadSkills() {
  skills = await (await fetch('/skills')).json();
}

async function renderStages() {
  const data = await (await fetch('/stages')).json();
  stagesEl.replaceChildren(...data.map((s, i) => {
    // Nothing is ever locked. Done is a badge, not a gate: the point is to
    // rebrand, tune, look at the result and go again.
    const isLive = !s.done && data.slice(0, i).every(p => p.done);
    const li = el('li', `stage ${s.done ? 'done' : (isLive ? 'live' : 'open')}`);

    const tile = el('div', 'tile', String(i + 1));
    const content = el('div');
    const h3 = el('h3', null, s.title);
    const p = el('p', null, s.blurb);

    content.append(h3, p);

    const suggest = el('div', 'suggest');
    suggest.append(el('span', null, s.done ? 'Run it again' : 'Suggested'),
                   el('q', null, s.suggested));
    suggest.onclick = () => { input.value = s.suggested; input.focus(); };
    content.append(suggest);

    // Tuning is the stage where knowing the simulation decides the result, so
    // show the manager what Antigravity has been told before it starts.
    for (const skill of SKILL_STAGES[s.id] ? skills : []) {
      const chip = el('button', 'skill-chip');
      chip.append(el('i'), el('span', null, skill.name));
      chip.title = skill.description;
      chip.onclick = () => openSkill(skill);
      content.append(chip);
    }

    li.append(tile, content);
    return li;
  }));
}

async function checkHealth() {
  const { agent, game, match } = await (await fetch('/health')).json();
  document.body.classList.toggle('is-blocked', !agent.ok);
  if (!agent.ok) document.querySelector('.blocked p').textContent = agent.detail;
  document.querySelector('#agy-status').textContent = `${agent.version} · ${agent.ok ? 'ready' : 'offline'}`;
  for (const [name, up] of Object.entries(game)) {
    document.querySelector(`.svc[data-service="${name}"]`)
      ?.classList.toggle('down', !up);
  }
  // Four unexplained port numbers mean nothing until one of them dies, so say
  // what to do at the moment it matters. Two scripts start these, and being
  // told to run the wrong one is worse than being told nothing.
  const warn = document.querySelector('.rig-warn');
  const script = game.arena === false ? 'arena/run.sh' : 'game/run.sh';
  warn.replaceChildren(document.createTextNode('not running · '), el('code', null, script));
  warn.hidden = Object.values(game).every(Boolean);
  showScoreline(match);
}

async function restart() {
  if (inFlight) return;
  restartBtn.disabled = true;
  try {
    await fetch('/reset', { method: 'POST' });
    log.replaceChildren(el('div', 'empty-state'));
    log.firstChild.append(
      el('h3', null, 'Nothing has happened yet'),
      el('p', null, 'Send the suggested line from the team sheet or type your own.'));
    lastActor = null;
    panels = {};
    eventCount = 0;
    tokenCount = null;
    document.querySelector('#event-count').textContent = '0 events';
    document.querySelector('#token-count').textContent = '-';
    await renderStages();
  } finally {
    restartBtn.disabled = false;
  }
}

function showScoreline(match) {
  const box = document.querySelector('.scoreline');
  const live = match && !match.error;
  box.hidden = !live;
  if (!live) { matchClock = null; return; }
  matchClock = match.matchTime ?? null;
  box.replaceChildren(
    el('b', null, `${match.score1 ?? 0}`),
    el('span', 'vs', 'v'),
    el('b', null, `${match.score2 ?? 0}`),
    el('em', null, minuteLabel()));
}

// matchTime counts down, so this is time remaining, shown exactly as the
// game's own scoreboard shows it. Rendering it as an elapsed minute would run
// the match log backwards.
const minuteLabel = () => {
  if (matchClock === null) return '--:--';
  const m = Math.floor(matchClock / 60);
  return `${m}:${String(matchClock % 60).padStart(2, '0')}`;
};

function setWorking(on, detail) {
  acting.style.display = on ? 'flex' : 'none';
  if (on) acting.querySelector('code').textContent = detail || '';
  haltBtn.disabled = !on;
  sendBtn.disabled = on;
}

async function send() {
  const message = input.value.trim();
  if (!message || inFlight) return;
  input.value = '';
  inFlight = new AbortController();
  setWorking(true, 'thinking');
  panels = {};

  let res;
  try {
    res = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
      signal: inFlight.signal,
    });
  } catch {
    inFlight = null;
    setWorking(false);
    return;
  }

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  let textNode = null;
  let textStep = null;
  let textSaid = '';

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      const frames = buffer.split('\n\n');
      buffer = frames.pop();
      for (const frame of frames) {
        const kind = frame.match(/^event: (.+)$/m)?.[1];
        const data = frame.match(/^data: (.+)$/m)?.[1];
        if (!kind || !data) continue;
        const { actor, payload, step } = JSON.parse(data);
        const minute = minuteLabel();

        if (kind === 'user') { addEvent(actor, minute, el('p', 'say you', payload)); }
        else if (kind === 'thought') { addEvent(actor, minute, richText('thought', payload)); textNode = null; }
        else if (kind === 'tool_call') {
          addEvent(actor, minute, toolCallNode(payload));
          textNode = null;
          // A started tuner gets its lane straight away, so the panel shows
          // four subagents at work rather than appearing only once one lands.
          for (const name of payload.name === 'start_subagent'
            ? startedTuners(payload.args) : [])
            laneFor(panelFor('antigravity', minute), name.replace('-tuner', ''));
        }
        else if (kind === 'tuning') { drawTuning(actor, minute, payload); textNode = null; }
        else if (kind === 'text') {
          // A new step means a different speaker; appending would splice two
          // subagents' sentences into one another.
          if (!textNode || step !== textStep) {
            textNode = el('p', 'say', '');
            textSaid = '';
            textStep = step;
            addEvent(actor, minute, textNode);
          }
          // Redrawn from the whole answer rather than appended to, because a
          // marker can arrive split across two chunks and only the text either
          // side of it says what it was.
          textSaid += payload;
          textNode.replaceChildren(richBody(textSaid));
        }
        else if (kind === 'kit') { addEvent(actor, minute, kitNode(payload)); textNode = null; }
        else if (kind === 'error') { addEvent(actor, minute, el('pre', 'out bad', payload)); }
        else if (kind === 'usage') {
          const total = payload?.total;
          if (typeof total === 'number' && isFinite(total)) {
            tokenCount = total;
            document.querySelector('#token-count').textContent = `${(tokenCount / 1000).toFixed(1)}k tokens`;
          }
        }
        else if (kind === 'stage_done') { renderStages(); }
      }
    }
  } catch {
    addEvent('antigravity', minuteLabel(), el('p', 'say', 'Halted.'));
  }

  inFlight = null;
  setWorking(false);
}

function halt() {
  // Aborting closes the stream, which closes the server generator, which
  // cancels the SDK turn. Nothing to do server side.
  inFlight?.abort();
}

sendBtn.onclick = send;
haltBtn.onclick = halt;
restartBtn.onclick = restart;
input.onkeydown = (e) => { if (e.key === 'Enter') send(); };
document.querySelector('.skill-close').onclick =
  () => { document.querySelector('.skill-sheet').hidden = true; };
document.querySelector('.skill-sheet').onclick = (e) => {
  if (e.target.classList.contains('skill-sheet')) e.target.hidden = true;
};

loadSkills().then(renderStages);
checkHealth();
setInterval(checkHealth, 4000);
setWorking(false);

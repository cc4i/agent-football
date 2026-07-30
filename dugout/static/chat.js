const log = document.querySelector('.log');
const stagesEl = document.querySelector('.stages');
const input = document.querySelector('#say');
const sendBtn = document.querySelector('.send');
const haltBtn = document.querySelector('.halt');
const restartBtn = document.querySelector('.restart');
const acting = document.querySelector('.acting');

const ACTOR_CLASS = { user: 'a-you', antigravity: 'a-agy' };
const ACTOR_LABEL = { user: 'You', antigravity: 'Antigravity' };
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

function richText(cls, source) {
  // Thoughts arrive as markdown. Built as DOM nodes, never innerHTML: this is
  // model output and may contain anything.
  const p = el('p', cls);
  for (const [i, part] of String(source).split('**').entries()) {
    if (!part) continue;
    p.append(i % 2 ? el('b', null, part) : document.createTextNode(part));
  }
  return p;
}

function changesTable(changes, reason) {
  // A tuner's whole contribution is which numbers it moved. As raw JSON on one
  // line that is unreadable, and this is the stage people are here to watch.
  const box = el('div', 'changes');
  const table = el('table');
  for (const [key, value] of Object.entries(changes)) {
    const row = el('tr');
    row.append(el('td', 'k', key), el('td', 'v', String(value)));
    table.append(row);
  }
  box.append(table);
  if (reason) box.append(el('p', 'why', reason));
  return box;
}

function toolCallNode(payload) {
  const wrap = el('div', 'act');
  wrap.append(el('span', 'verb', VERB[payload.name] || 'Called'));
  const call = el('span', 'call');
  call.append(el('span', 'fn', payload.name));

  const args = payload.args ?? {};
  const isTune = payload.name.startsWith('tune_') && args.changes;
  if (!isTune) call.append(el('span', 'arg', `(${JSON.stringify(args)})`));
  wrap.append(call);

  if (isTune) wrap.append(changesTable(args.changes, args.reason));
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
  document.querySelector('#agy-status').textContent = agent.ok ? '0.1.9 · ready' : '0.1.9 · offline';
  for (const [name, up] of Object.entries(game)) {
    document.querySelector(`.svc[data-service="${name}"]`)
      ?.classList.toggle('down', !up);
  }
  // Three unexplained port numbers mean nothing until one of them dies, so
  // say what to do at the moment it matters.
  document.querySelector('.rig-warn').hidden = Object.values(game).every(Boolean);
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
        else if (kind === 'tool_call') { addEvent(actor, minute, toolCallNode(payload)); textNode = null; }
        else if (kind === 'text') {
          // A new step means a different speaker; appending would splice two
          // subagents' sentences into one another.
          if (!textNode || step !== textStep) {
            textNode = el('p', 'say', '');
            textStep = step;
            addEvent(actor, minute, textNode);
          }
          textNode.textContent += payload;
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

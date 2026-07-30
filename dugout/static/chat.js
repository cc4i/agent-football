const log = document.querySelector('.log');
const stagesEl = document.querySelector('.stages');
const input = document.querySelector('#say');
const sendBtn = document.querySelector('.send');
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

function toolCallNode(payload) {
  const wrap = el('div', 'act');
  wrap.append(el('span', 'verb', VERB[payload.name] || 'Called'));
  const call = el('span', 'call');
  call.append(el('span', 'fn', payload.name));
  call.append(el('span', 'arg', `(${JSON.stringify(payload.args ?? {})})`));
  wrap.append(call);
  return wrap;
}

async function renderStages() {
  const data = await (await fetch('/stages')).json();
  stagesEl.replaceChildren(...data.map((s, i) => {
    const isLive = !s.done && data.slice(0, i).every(p => p.done);
    const li = el('li', `stage ${s.done ? 'done' : (isLive ? 'live' : 'locked')}`);

    const tile = el('div', 'tile', String(i + 1));
    const content = el('div');
    const h3 = el('h3', null, s.title);
    const p = el('p', null, s.blurb);

    content.append(h3, p);

    if (isLive) {
      const suggest = el('div', 'suggest');
      const suggestSpan = el('span', null, 'Suggested');
      const suggestQ = el('q', null, s.suggested);
      suggest.append(suggestSpan, suggestQ);
      suggest.onclick = () => { input.value = s.suggested; input.focus(); };
      content.append(suggest);
    }

    li.append(tile, content);
    return li;
  }));
}

async function checkHealth() {
  const { agent, game } = await (await fetch('/health')).json();
  document.body.classList.toggle('is-blocked', !agent.ok);
  if (!agent.ok) document.querySelector('.blocked p').textContent = agent.detail;
  document.querySelector('#agy-status').textContent = agent.ok ? '0.1.9 · ready' : '0.1.9 · offline';
  for (const [name, up] of Object.entries(game)) {
    document.querySelector(`.svc[data-service="${name}"]`)
      ?.classList.toggle('down', !up);
  }
}

function setWorking(on, detail) {
  acting.style.display = on ? 'flex' : 'none';
  if (on) acting.querySelector('code').textContent = detail || '';
}

async function send() {
  const message = input.value.trim();
  if (!message) return;
  input.value = '';
  setWorking(true, 'thinking');

  const res = await fetch('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  });

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  let textNode = null;

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
      const { actor, payload } = JSON.parse(data);
      const minute = '--′';

      if (kind === 'user') { addEvent(actor, minute, el('p', 'say you', payload)); }
      else if (kind === 'thought') { addEvent(actor, minute, el('p', 'thought', payload)); textNode = null; }
      else if (kind === 'tool_call') { addEvent(actor, minute, toolCallNode(payload)); textNode = null; }
      else if (kind === 'text') {
        if (!textNode) { textNode = el('p', 'say', ''); addEvent(actor, minute, textNode); }
        textNode.textContent += payload;
      }
      else if (kind === 'error') { addEvent(actor, minute, el('pre', 'out bad', payload)); }
      else if (kind === 'usage') {
        if (payload !== null) {
          tokenCount = payload;
          document.querySelector('#token-count').textContent = `${(tokenCount / 1000).toFixed(1)}k tokens`;
        }
      }
      else if (kind === 'stage_done') { renderStages(); }
    }
  }
  setWorking(false);
}

sendBtn.onclick = send;
input.onkeydown = (e) => { if (e.key === 'Enter') send(); };
renderStages();
checkHealth();
setWorking(false);

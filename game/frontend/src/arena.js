// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

/**
 * The pitch's end of the arena.
 *
 * Profiles used to be four files on disk that this page re-read every two
 * seconds. They now belong to a room and a dugout, so the pitch asks the arena
 * for them once and is told when they move. Same page, same look; it simply
 * knows which match it is rendering.
 *
 * With no `?room=` this is the workshop, which the arena opens for itself. That
 * is what keeps the five-stage lab working unchanged while a venue full of
 * phones plays their own matches beside it.
 */

// Vite proxies these to the arena on :8003, so every call here is same-origin
// and nothing needs CORS opened for it. See vite.config.js.
const WORKSHOP = 'WRKS';
const ROLES = ['defender', 'midfielder', 'forward', 'goalkeeper'];

const params = new URLSearchParams(window.location.search);

export const room = {
  code: (params.get('room') || WORKSHOP).toUpperCase(),
  team: params.get('team') === 'red' ? 'red' : 'blue',
  // `host` advances physics and reports what happened. Everyone else watches.
  as: params.get('as') === 'host' ? 'host' : 'viewer',
  clientId: params.get('client_id') || '',
  // The lab page is the workshop room with no role asked for; a match always
  // says which. This is what decides whether the workshop chrome is drawn.
  inMatch: Boolean(params.get('room')),
};

export const isHost = () => room.as === 'host' && Boolean(room.clientId);

/**
 * Whether this pitch renders somebody else's match instead of running its own.
 *
 * Being in a match without the token that holds its physics is exactly what
 * watching is. The workshop is deliberately not a match, so the lab still runs
 * its own simulation with no token anywhere in sight.
 */
export const isViewer = () => room.inMatch && !isHost();

/**
 * Whether this pitch should run the autonomous "are you tired?" check.
 *
 * Each one wakes a coach, a captain and four specialists, about three
 * times in a three-minute match. Multiplied by a venue full of rooms that
 * is hundreds of model calls a minute nobody asked for, queued in front of
 * the shouts managers actually typed. The workshop is long-lived and has
 * an audience watching for exactly this kind of autonomous behaviour, so
 * it is the one place that keeps it.
 */
export const shouldRunStatusCheck = (r = room) => !r.inMatch;

/**
 * Ask the screen to stay awake while this tab is the one running a match.
 *
 * Physics lives here, so a screen that dims and sleeps takes the match with
 * it: frames stop, and after half a minute of silence the arena marks the room
 * abandoned. That is the right thing to do to a host that has gone, and the
 * wrong thing to do to one sitting in front of an idle laptop, so the host
 * asks not to be left. Browsers drop the lock whenever the page is hidden,
 * which is why it is taken again on the way back rather than only once.
 *
 * The lock is held for as long as the page is: this pitch exists only while
 * its match does, and there is nothing left to keep awake once it is gone.
 * Settles once the first attempt has been answered, one way or the other.
 */
export async function keepAwake(navigation = navigator, page = document) {
  if (!navigation.wakeLock) return;
  let held = null;

  const take = async () => {
    if (held || page.visibilityState !== 'visible') return;
    try {
      held = await navigation.wakeLock.request('screen');
      // The browser can revoke it without telling this code twice.
      held.addEventListener('release', () => { held = null; });
    } catch {
      // Refused: no user gesture yet, a policy against it, or a flat battery.
      // None of those are worth interrupting a match over, and the arena's own
      // thirty seconds of grace is the backstop either way.
    }
  };

  page.addEventListener('visibilitychange', take);
  await take();
}

/** The other dugout. A host drives both of them. */
export const opposite = (team = room.team) => (team === 'red' ? 'blue' : 'red');

/** Every role's attributes for one dugout, as the arena holds them. */
export async function readProfiles(team = room.team) {
  const response = await fetch(
    `/api/rooms/${encodeURIComponent(room.code)}/teams/${encodeURIComponent(team)}/profiles`);
  if (!response.ok) throw new Error(`the arena has no ${team} profiles for ${room.code}`);
  const body = await response.json();
  // Only the four roles the pitch knows how to drive, in a known order.
  return Object.fromEntries(ROLES.map(role => [role, body.profiles[role]]).filter(([, p]) => p));
}

/**
 * Hold a socket open on this room.
 *
 * Reconnects on its own: the arena's log is gapless and the room is re-sent on
 * every connect, so coming back is always safe. Giving up would strand a match
 * that is still being played.
 */
export function connect({ onRoom, onEvent, onState } = {}) {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const query = room.clientId ? `?client_id=${encodeURIComponent(room.clientId)}` : '';
  const address =
    `${scheme}://${window.location.host}/ws/rooms/${encodeURIComponent(room.code)}${query}`;

  let socket = null;
  let wait = 500;
  let retry = null;
  let stopped = false;

  const open = () => {
    socket = new WebSocket(address);
    socket.addEventListener('open', () => { wait = 500; });
    socket.addEventListener('message', (packet) => {
      let message;
      try {
        message = JSON.parse(packet.data);
      } catch {
        return;
      }
      if (message.type === 'room' && onRoom) onRoom(message);
      if (message.type === 'event' && onEvent) onEvent(message);
      if (message.type === 'state' && onState) onState(message);
    });
    socket.addEventListener('close', (event) => {
      if (stopped) return;
      // 4404 is the arena saying this room does not exist. Retrying a mistyped
      // code forever would be worse than stopping.
      if (event.code === 4404) return;
      retry = window.setTimeout(open, wait);
      wait = Math.min(wait * 2, 8000);
    });
  };
  open();

  const send = (message) => {
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
  };

  return {
    /** Positions, score and clock. Dropped if they arrive faster than the wire. */
    state: (payload) => isHost() && send({ type: 'host.state', payload }),
    /** Something that happened. This is what scoring is later computed from. */
    event: (kind, payload, matchMs) =>
      isHost() && send({ type: 'host.event', kind, payload, match_ms: matchMs }),
    close() {
      stopped = true;
      window.clearTimeout(retry);
      if (socket) socket.close();
    },
  };
}

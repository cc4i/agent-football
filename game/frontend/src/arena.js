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
 * `room` describes the lab page and nothing else: with no `?room=` it is the
 * workshop, which the arena opens for itself, and that is what keeps the
 * five-stage lab working unchanged while a venue full of phones plays their
 * own matches beside it. `readProfiles` and `connect` are the shared half, and
 * the grounds page uses them for fifty rooms at once with no `room` in sight.
 */

// Vite proxies these to the arena on :8003, so every call here is same-origin
// and nothing needs CORS opened for it. See vite.config.js.
const WORKSHOP = 'WRKS';
const ROLES = ['defender', 'midfielder', 'forward', 'goalkeeper'];

const params = new URLSearchParams(window.location.search);

export const room = {
  code: (params.get('room') || WORKSHOP).toUpperCase(),
  team: params.get('team') === 'red' ? 'red' : 'blue',
  // The lab is the workshop room, and a `?room=` is somebody pointing it at a
  // venue's squads instead. Everything the lab does to a room that it must not
  // do to a match in progress hangs off this.
  inMatch: Boolean(params.get('room')),
};

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

/** The other dugout. Whoever runs the physics drives both of them. */
export const opposite = (team = room.team) => (team === 'red' ? 'blue' : 'red');

/** Every role's attributes for one dugout, as the arena holds them. */
export async function readProfiles(code, team) {
  const response = await fetch(
    `/api/rooms/${encodeURIComponent(code)}/teams/${encodeURIComponent(team)}/profiles`);
  if (!response.ok) throw new Error(`the arena has no ${team} profiles for ${code}`);
  const body = await response.json();
  // Only the four roles the pitch knows how to drive, in a known order.
  return Object.fromEntries(ROLES.map(role => [role, body.profiles[role]]).filter(([, p]) => p));
}

/**
 * Hold a socket open on a room.
 *
 * The room is an argument rather than the module's own. A page used to be
 * about exactly one match, settled from its URL before any of this ran; the
 * grounds page is about fifty at once, so which room a socket is for became
 * something the caller says.
 *
 * `hosting` is whether this socket may drive physics. It defaults to holding a
 * token, because a page that was handed one was handed it to play with, and
 * the arena checks the token on every frame regardless.
 *
 * Reconnects on its own: the arena's log is gapless and the room is re-sent on
 * every connect, so coming back is always safe. Giving up would strand a match
 * that is still being played.
 */
export function connect(code, { clientId = '', hosting = Boolean(clientId),
                                onRoom, onEvent, onState } = {}) {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const query = clientId ? `?client_id=${encodeURIComponent(clientId)}` : '';
  const address =
    `${scheme}://${window.location.host}/ws/rooms/${encodeURIComponent(code)}${query}`;

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
    state: (payload) => hosting && send({ type: 'host.state', payload }),
    /** Something that happened. This is what scoring is later computed from. */
    event: (kind, payload, matchMs) =>
      hosting && send({ type: 'host.event', kind, payload, match_ms: matchMs }),
    close() {
      stopped = true;
      window.clearTimeout(retry);
      if (socket) socket.close();
    },
  };
}

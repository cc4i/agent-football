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
 * N matches in one page, none of them drawn.
 *
 * The scene has had two roles since before this existed: a host runs physics
 * and reports what happened, a viewer eases toward the frames it is sent and
 * simulates nothing. This is the host role with no tab around it, which is the
 * whole of the change -- the simulation is not re-implemented here, it is
 * re-hosted.
 *
 * Phaser.HEADLESS still builds every display object the scene creates, because
 * create() builds them before it consults its role. That is fine, and it is
 * why this is a browser and not jsdom: Chromium has a real DOM and a real
 * canvas, and HEADLESS simply never draws to them. The render pass is the
 * expensive half and it is the half that is gone.
 *
 * There is no UI and no URL to be in a room by. The supervisor calls
 * `window.grounds.host(code, token, seed)` over CDP, once per assignment.
 */
import Phaser from 'phaser';

import { connect, readProfiles } from './arena.js';
import { Sound } from './audio.js';
import { SoccerGameScene } from './game.js';

const WIDTH = 1408;
const HEIGHT = 768;
const TEAMS = ['blue', 'red'];

// Nobody is listening to a server. Left on, every whistle and every goal in
// the venue would build an oscillator, run it and throw it away.
Sound.toggle(false);

/** Room code -> the game, the socket and the two squads playing in it. */
const matches = new Map();

/**
 * Take on a match: a socket, both squads, and a game with no picture.
 *
 * Resolves once the match is actually running, so the supervisor's count and
 * this page's agree. `running()` includes the room from the first line, though,
 * because a second assignment for a room already being set up has to be
 * refused while it is still being set up.
 */
async function host(code, token, seed) {
  if (matches.has(code)) return;
  const match = { game: null, feed: null, squads: { blue: {}, red: {} } };
  matches.set(code, match);

  const scene = new SoccerGameScene({ role: 'host', seed });

  match.feed = connect(code, {
    clientId: token,
    // Who is in the two dugouts, which is what the nameplates say. It arrives
    // on connect, which may be before the scene has plates to write on; the
    // scene holds it and applies it when it has.
    onRoom: (message) => scene.nameManagers(message),
    onEvent: (message) => {
      if (message.kind !== 'profile.patch') return;
      const team = moved(match, message.payload);
      if (team) scene.updateProfiles(team, match.squads[team]);
    },
  });

  // Frames are what the match looks like and events are what happened to it:
  // frames may be dropped, events may not, so they travel separately.
  scene.frameSink = match.feed.state;
  scene.reporter = (kind, payload, matchMs) => {
    match.feed.event(kind, payload, matchMs);
    // The whistle this page blew itself. The arena closes the room on it and
    // sends nothing back -- it knows we know -- so a pitch is given up here or
    // it is held for the rest of the evening. Deferred by a turn because this
    // runs inside the game's own update.
    if (kind === 'full_time') window.setTimeout(() => drop(code), 0);
  };

  await Promise.all(TEAMS.map((team) => read(match, code, team)));
  // Dropped, or dropped and re-hosted, while the arena was answering.
  if (matches.get(code) !== match) return;
  TEAMS.forEach((team) => scene.updateProfiles(team, match.squads[team]));

  match.game = new Phaser.Game({
    type: Phaser.HEADLESS,
    width: WIDTH,
    height: HEIGHT,
    // No canvas anybody looks at and no keyboard behind it. Fifty games in one
    // page grabbing focus from each other would be fifty for nothing.
    autoFocus: false,
    physics: { default: 'arcade', arcade: { gravity: { y: 0 }, debug: false } },
    // An instance rather than the class: which of the two things this scene is
    // has to be settled before create() runs a whistle and a clock.
    scene: [scene],
  });
}

/** Let a match go: no more frames, no more physics, no more slot. */
function drop(code) {
  const match = matches.get(code);
  if (!match) return;
  matches.delete(code);
  if (match.feed) match.feed.close();
  if (match.game) match.game.destroy(true);
}

/** The rooms this page is playing. The supervisor reconciles against it. */
function running() {
  return Array.from(matches.keys());
}

/** One dugout's squad as the arena holds it, or the scene's own defaults. */
async function read(match, code, team) {
  try {
    const squad = await readProfiles(code, team);
    Object.entries(squad).forEach(([role, profile]) => {
      // Under, not over: a shout that landed while the arena was answering has
      // already moved this role, and that move is the newer of the two.
      match.squads[team][role] = { ...profile, ...(match.squads[team][role] || {}) };
    });
  } catch (problem) {
    // A match on the shipped baselines is a worse match. No match at all is a
    // room sitting at 0-0 until the sweep tells two managers it stopped
    // reporting, which is a lie about what went wrong.
    console.warn(`grounds: no ${team} squad for ${code}`, problem);
  }
}

/** Apply one profile.patch. Returns the dugout it moved, or nothing. */
function moved(match, payload) {
  const squad = match.squads[payload.team];
  if (!squad) return null;
  squad[payload.role] = { ...(squad[payload.role] || {}), ...(payload.changed || {}) };
  return payload.team;
}

window.grounds = { host, drop, running };

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
 * The page a server loads instead of a person.
 *
 * What it has to get right is bookkeeping, not football: one game and one
 * socket per hosted room, the right token on the right socket, and everything
 * let go of when the room is dropped. The football is `game.js`, tested by
 * everything beside this file.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

// arena.js settles which room the page is in as it loads, and these run in
// node. One property is enough to get the module in; `window.grounds` is then
// hung off the same object, exactly as it is in a browser.
globalThis.window = {
  location: { search: '', protocol: 'http:', host: 'arena.test' },
  setTimeout: (run) => setTimeout(run, 0),
};

// A socket per room with no arena anywhere. This is the whole point of the
// page being its own unit: it can be driven by hand from a console, and so it
// can be driven from here.
const sockets = [];
const squads = { blue: {}, red: {} };
vi.mock('../src/arena.js', async (importOriginal) => ({
  ...(await importOriginal()),
  connect: (code, options) => {
    const socket = {
      code,
      options,
      sent: [],
      closed: false,
      state: (payload) => socket.sent.push({ type: 'state', payload }),
      event: (kind, payload, matchMs) => socket.sent.push({ kind, payload, matchMs }),
      close: () => { socket.closed = true; },
    };
    sockets.push(socket);
    return socket;
  },
  readProfiles: async (code, team) => {
    const squad = squads[team];
    if (!squad) throw new Error(`the arena has no ${team} profiles for ${code}`);
    return structuredClone(squad);
  },
}));

// Phaser boots a real game per room. Stubbed: what this file is about is one
// game per hosted room and no more, not what any of them do.
const games = [];
vi.mock('phaser', () => ({
  default: {
    HEADLESS: 3,
    Scene: class { constructor(config) { this.sceneConfig = config; } },
    Game: class {
      constructor(config) {
        this.config = config;
        this.destroyed = false;
        games.push(this);
      }
      destroy() { this.destroyed = true; }
    },
    Math: { Distance: { Between: (x1, y1, x2, y2) => Math.hypot(x2 - x1, y2 - y1) } },
  },
}));

vi.mock('../src/audio.js', () => ({
  Sound: new Proxy({}, { get: () => () => {} }),
}));

/** The scene instance a booted game was handed. */
const sceneOf = (game) => game.config.scene[0];

describe('the grounds page', () => {
  beforeEach(async () => {
    sockets.length = 0;
    games.length = 0;
    squads.blue = { forward: { speed: 260 } };
    squads.red = { forward: { speed: 250 } };
    vi.resetModules();
    await import('../src/host.js');
  });

  it('runs one game per hosted room', async () => {
    await window.grounds.host('AAAA', 'token-a', 'AAAA-1');
    await window.grounds.host('BBBB', 'token-b', 'BBBB-2');

    expect(games).toHaveLength(2);
    expect(window.grounds.running().sort()).toEqual(['AAAA', 'BBBB']);
  });

  it("opens one socket per hosted room, bearing that room's token", async () => {
    await window.grounds.host('AAAA', 'token-a', 'AAAA-1');

    expect(sockets).toHaveLength(1);
    expect(sockets[0].code).toBe('AAAA');
    expect(sockets[0].options.clientId).toBe('token-a');
  });

  it('hosting the same room twice is not two games', async () => {
    await window.grounds.host('AAAA', 'token-a', 'AAAA-1');
    await window.grounds.host('AAAA', 'token-a', 'AAAA-1');

    expect(games).toHaveLength(1);
    expect(sockets).toHaveLength(1);
  });

  it('does not run the same room twice when both arrive at once', async () => {
    // The reads take a turn of the loop, and the arena is entitled to send a
    // second assignment inside one: a double-tapped kick-off button, or a
    // reconnect that resent what it had already sent.
    await Promise.all([
      window.grounds.host('AAAA', 'token-a', 'AAAA-1'),
      window.grounds.host('AAAA', 'token-a', 'AAAA-1'),
    ]);

    expect(games).toHaveLength(1);
  });

  it('dropping destroys the game and closes the socket', async () => {
    await window.grounds.host('AAAA', 'token-a', 'AAAA-1');
    await window.grounds.host('BBBB', 'token-b', 'BBBB-2');

    window.grounds.drop('AAAA');

    expect(games[0].destroyed).toBe(true);
    expect(sockets[0].closed).toBe(true);
    expect(games[1].destroyed).toBe(false);
    expect(window.grounds.running()).toEqual(['BBBB']);
  });

  it('dropping a room it never had is not an error', () => {
    expect(() => window.grounds.drop('ZZZZ')).not.toThrow();
  });

  it('passes the seed and the host role into the scene', async () => {
    await window.grounds.host('AAAA', 'token-a', 'AAAA-1');

    const scene = sceneOf(games[0]);
    expect(scene.seed).toBe('AAAA-1');
    expect(scene.role).toBe('host');
  });

  it('wires the scene to the room it is playing in', async () => {
    await window.grounds.host('AAAA', 'token-a', 'AAAA-1');
    const scene = sceneOf(games[0]);

    scene.frameSink({ clock: 12 });
    scene.reporter('goal', { team: 'blue' }, 4200);

    expect(sockets[0].sent).toEqual([
      { type: 'state', payload: { clock: 12 } },
      { kind: 'goal', payload: { team: 'blue' }, matchMs: 4200 },
    ]);
  });

  it('gives both dugouts their squads before the match starts', async () => {
    await window.grounds.host('AAAA', 'token-a', 'AAAA-1');

    const scene = sceneOf(games[0]);
    expect(scene.blueProfiles).toEqual({ forward: { speed: 260 } });
    expect(scene.redProfiles).toEqual({ forward: { speed: 250 } });
  });

  it('plays on when the arena will not say what a squad is', async () => {
    // A match on the shipped baselines is a worse match. No match at all is a
    // room that sits at 0-0 until the sweep abandons it, and tells its two
    // managers their host stopped reporting.
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    squads.blue = null;

    await window.grounds.host('AAAA', 'token-a', 'AAAA-1');

    expect(games).toHaveLength(1);
    expect(window.grounds.running()).toEqual(['AAAA']);
    // Red still got theirs; one dugout's bad luck is not both dugouts'.
    expect(sceneOf(games[0]).redProfiles).toEqual({ forward: { speed: 250 } });
  });

  it('moves a squad when a shout moves it', async () => {
    await window.grounds.host('AAAA', 'token-a', 'AAAA-1');
    const scene = sceneOf(games[0]);

    sockets[0].options.onEvent({
      kind: 'profile.patch',
      payload: { team: 'red', role: 'forward', changed: { speed: 300 } },
    });

    expect(scene.redProfiles).toEqual({ forward: { speed: 300 } });
    expect(scene.blueProfiles).toEqual({ forward: { speed: 260 } });
  });

  it('ignores anything on the socket that is not a squad moving', async () => {
    await window.grounds.host('AAAA', 'token-a', 'AAAA-1');
    const scene = sceneOf(games[0]);

    sockets[0].options.onEvent({ kind: 'goal', payload: { team: 'blue' } });

    expect(scene.blueProfiles).toEqual({ forward: { speed: 260 } });
  });

  it('puts the managers on the nameplates when the arena says who they are', async () => {
    await window.grounds.host('AAAA', 'token-a', 'AAAA-1');
    const scene = sceneOf(games[0]);

    const seated = { seats: { blue: { name: 'Alex Rivera' } }, mode: 'solo' };
    sockets[0].options.onRoom(seated);

    expect(scene.managers).toBe(seated);
  });

  it('gives up a match the moment it blows its own full-time whistle', async () => {
    // Nobody else will. The arena closes the room on this very whistle and
    // does not send a drop back, so a pitch not given up here is a pitch held
    // for the rest of the evening.
    await window.grounds.host('AAAA', 'token-a', 'AAAA-1');
    sceneOf(games[0]).reporter('full_time', { score: [2, 1] }, 180_000);

    await new Promise((settle) => setTimeout(settle, 0));

    expect(window.grounds.running()).toEqual([]);
    expect(games[0].destroyed).toBe(true);
    expect(sockets[0].closed).toBe(true);
    // The whistle still went to the arena. It is what closes the room.
    expect(sockets[0].sent).toEqual([
      { kind: 'full_time', payload: { score: [2, 1] }, matchMs: 180_000 },
    ]);
  });
});

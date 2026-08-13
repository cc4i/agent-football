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

import { describe, it, expect, vi, beforeEach } from 'vitest';

// The real applyFrame from game.js, without booting Phaser or the DOM. The
// viewer path touches almost none of the engine: positions, two animations and
// three bits of text, which is the point of it being one early return.
vi.mock('phaser', () => ({
  default: {
    Scene: class {},
    Math: { Distance: { Between: (x1, y1, x2, y2) => Math.hypot(x2 - x1, y2 - y1) } },
  },
}));

vi.mock('../src/audio.js', () => ({
  Sound: new Proxy({}, { get: () => () => {} }),
}));

import { SoccerGameScene } from '../src/game.js';

// A full step: the ease is `delta / 90`, so at 90ms a sprite lands exactly on
// the reported position and the arithmetic in these tests stays readable.
const WHOLE_STEP = 90;

function sprite(x, y) {
  return {
    x,
    y,
    rotation: 0,
    animation: null,
    facingLeft: null,
    store: new Map(),
    setPosition(nx, ny) { this.x = nx; this.y = ny; },
    play(key) { this.animation = key; },
    setFlipX(on) { this.facingLeft = on; },
    getData(key) { return this.store.get(key); },
    setData(key, value) { this.store.set(key, value); },
  };
}

function label() {
  return {
    words: '',
    x: 0,
    y: 0,
    setText(value) { this.words = String(value); },
    setPosition(x, y) { this.x = x; this.y = y; },
  };
}

/** A viewer scene with everything applyFrame reaches for, and nothing else. */
function watching({ width = 1408, height = 768 } = {}) {
  const scene = new SoccerGameScene({ role: 'viewer' });
  scene.sys = { game: { config: { width, height } } };
  scene.ball = sprite(0, 0);
  scene.ballShadow = sprite(0, 0);
  scene.gk1 = sprite(0, 0);
  scene.gk2 = sprite(0, 0);
  scene.bluePlayers = [sprite(0, 0), sprite(0, 0), sprite(0, 0), sprite(0, 0)];
  scene.redPlayers = [sprite(0, 0), sprite(0, 0), sprite(0, 0), sprite(0, 0)];
  scene.scoreText1 = label();
  scene.scoreText2 = label();
  scene.timeText = label();
  return scene;
}

/** A frame with everybody parked in the corner, for a test to move one of. */
function frame(overrides = {}) {
  const corner = () => [0, 0];
  return {
    score: [0, 0],
    clock: 180,
    ball: corner(),
    blue: [corner(), corner(), corner(), corner(), corner()],
    red: [corner(), corner(), corner(), corner(), corner()],
    ...overrides,
  };
}

describe('the viewer path', () => {
  let scene;

  beforeEach(() => {
    scene = watching();
  });

  it('is the only thing a viewer does in update', () => {
    const applyFrame = vi.spyOn(scene, 'applyFrame').mockImplementation(() => {});
    scene.gameActive = true;
    scene.publishFrame = () => { throw new Error('a viewer published a frame'); };

    scene.update(1000, 16);

    expect(applyFrame).toHaveBeenCalledWith(16);
  });

  it('draws nothing at all before the first frame arrives', () => {
    scene.applyFrame(WHOLE_STEP);

    expect(scene.ball.x).toBe(0);
    expect(scene.bluePlayers[0].animation).toBe(null);
  });

  it('reads positions as fractions of its own canvas, not the host pixels', () => {
    // Half the width on a screen half the size is half the width. This is what
    // lets one frame drive a wall, a laptop and a phone's thumbnail.
    const small = watching({ width: 704, height: 384 });
    small.wire = frame({ ball: [0.5, 0.5] });

    small.applyFrame(WHOLE_STEP);

    expect(small.ball.x).toBe(352);
    expect(small.ball.y).toBe(192);
  });

  it('eases toward the reported position rather than snapping to it', () => {
    scene.wire = frame({ ball: [1, 1] });

    // A third of a step, so a third of the way there.
    scene.applyFrame(WHOLE_STEP / 3);

    expect(scene.ball.x).toBeCloseTo(1408 / 3, 5);
    expect(scene.ball.y).toBeCloseTo(768 / 3, 5);
  });

  it('never overshoots on a long frame', () => {
    // A tab that was throttled comes back with a huge delta. It should arrive,
    // not sail past and be dragged back on the frame after.
    scene.wire = frame({ ball: [0.25, 0.25] });

    scene.applyFrame(5000);

    expect(scene.ball.x).toBeCloseTo(352, 5);
    expect(scene.ball.y).toBeCloseTo(192, 5);
  });

  it('keeps the ball shadow under the ball', () => {
    scene.wire = frame({ ball: [0.5, 0.5] });

    scene.applyFrame(WHOLE_STEP);

    expect([scene.ballShadow.x, scene.ballShadow.y]).toEqual([scene.ball.x, scene.ball.y]);
  });

  it('runs a player who is still travelling, facing the way they are going', () => {
    scene.bluePlayers[1].x = 200;
    scene.wire = frame();
    scene.wire.blue[2] = [0.5, 0.5];

    scene.applyFrame(WHOLE_STEP);

    expect(scene.bluePlayers[1].animation).toBe('blue_run');
    expect(scene.bluePlayers[1].facingLeft).toBe(false);
  });

  it('turns a player who is heading back the other way', () => {
    scene.redPlayers[0].x = 1400;
    scene.redPlayers[0].y = 400;
    scene.wire = frame();
    scene.wire.red[1] = [0.1, 0.5];

    scene.applyFrame(WHOLE_STEP);

    expect(scene.redPlayers[0].animation).toBe('red_run');
    expect(scene.redPlayers[0].facingLeft).toBe(true);
  });

  it('idles a player who is already standing where the host says', () => {
    scene.wire = frame();

    scene.applyFrame(WHOLE_STEP);

    expect(scene.bluePlayers.map((p) => p.animation)).toEqual(Array(4).fill('blue_idle'));
    expect(scene.redPlayers.map((p) => p.animation)).toEqual(Array(4).fill('red_idle'));
  });

  it('carries a player name along with the player', () => {
    const name = label();
    scene.bluePlayers[0].setData('label', name);
    scene.wire = frame();
    scene.wire.blue[1] = [0.5, 0.5];

    scene.applyFrame(WHOLE_STEP);

    expect([name.x, name.y]).toEqual([704, 384 - 45]);
  });

  it('carries a live shout bubble too, and leaves a spent one where it fell', () => {
    const live = { alpha: 1, x: 0, y: 0, setPosition(x, y) { this.x = x; this.y = y; } };
    const words = label();
    const spent = { alpha: 0, x: 0, y: 0, setPosition(x, y) { this.x = x; this.y = y; } };
    scene.bluePlayers[0].setData('shoutGraphics', live);
    scene.bluePlayers[0].setData('shoutText', words);
    scene.bluePlayers[1].setData('shoutGraphics', spent);
    scene.bluePlayers[1].setData('shoutText', label());
    scene.wire = frame();
    scene.wire.blue[1] = [0.5, 0.5];
    scene.wire.blue[2] = [0.5, 0.5];

    scene.applyFrame(WHOLE_STEP);

    expect([live.x, live.y]).toEqual([704, 384 - 75]);
    expect([words.x, words.y]).toEqual([704, 384 - 75]);
    expect([spent.x, spent.y]).toEqual([0, 0]);
  });

  it('rolls the ball the way it is travelling', () => {
    scene.wire = frame({ ball: [0.5, 0.5] });

    scene.applyFrame(WHOLE_STEP);
    const rightward = scene.ball.rotation;
    scene.wire = frame({ ball: [0.1, 0.5] });
    scene.applyFrame(WHOLE_STEP);

    expect(rightward).toBeGreaterThan(0);
    expect(scene.ball.rotation).toBeLessThan(rightward);
  });

  it('leaves the keepers to their own animations', () => {
    scene.wire = frame();
    scene.wire.blue[0] = [0.9, 0.9];

    scene.applyFrame(WHOLE_STEP);

    expect(scene.gk1.x).toBeCloseTo(1267.2, 5);
    expect(scene.gk1.animation).toBe(null);
  });

  it('shows the host score and the host clock', () => {
    scene.wire = frame({ score: [3, 1], clock: 65 });

    scene.applyFrame(WHOLE_STEP);

    expect(scene.scoreText1.words).toBe('3');
    expect(scene.scoreText2.words).toBe('1');
    expect(scene.timeText.words).toBe('01:05');
  });

  it('leaves a side alone rather than throwing when a frame is short', () => {
    // Nothing on the wire is trusted to be well formed: it arrives over a
    // socket, and one bad frame must not take the screen down for the rest of
    // the match.
    scene.wire = { ball: [0.5, 0.5], blue: [[0.2, 0.2]], red: null, score: 'nonsense' };

    expect(() => scene.applyFrame(WHOLE_STEP)).not.toThrow();
    expect(scene.ball.x).toBe(704);
    expect(scene.redPlayers[0].x).toBe(0);
    expect(scene.scoreText1.words).toBe('');
  });
});

describe('the manager nameplates', () => {
  let scene;
  let plates;

  beforeEach(() => {
    scene = new SoccerGameScene({ role: 'viewer' });
    // create() hands back a setter per plate rather than the Phaser objects, so
    // a test needs nothing more than two functions to know what the crowd reads.
    plates = { blue: '', red: '' };
    scene.nameBlue = (words) => { plates.blue = words; };
    scene.nameRed = (words) => { plates.red = words; };
  });

  it('puts both managers in their own corners', () => {
    scene.nameManagers({ mode: 'versus',
                         seats: { blue: { name: 'Mara Bell' }, red: { name: 'Dee Okafor' } } });

    expect(plates).toEqual({ blue: 'Mara Bell', red: 'Dee Okafor' });
  });

  it('gives a solo run the house side to play against', () => {
    scene.nameManagers({ mode: 'solo', seats: { blue: { name: 'Sol Amari' } } });

    expect(plates).toEqual({ blue: 'Sol Amari', red: 'The house side' });
  });

  it('falls back to the colours in a room nobody has sat down in', () => {
    scene.nameManagers({ mode: 'versus', seats: {} });

    expect(plates).toEqual({ blue: 'BLUE', red: 'RED' });
  });

  it('cuts a name long enough to push its own plate off the touchline', () => {
    scene.nameManagers({ mode: 'versus', seats: { blue: { name: 'Bartholomew Fotheringay' } } });

    expect(plates.blue).toBe('Bartholomew Fotheri…');
  });

  it('holds the room until there are plates to write it on', () => {
    // The socket connects before Phaser boots, so the first snapshot routinely
    // arrives with nothing to draw it on. create() applies it once there is.
    const early = new SoccerGameScene({ role: 'viewer' });

    expect(() => early.nameManagers({ mode: 'solo', seats: {} })).not.toThrow();
    expect(early.managers).toEqual({ mode: 'solo', seats: {} });
  });
});

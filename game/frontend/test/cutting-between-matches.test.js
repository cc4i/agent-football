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

import { describe, it, expect, beforeEach, vi } from 'vitest';

// As in viewer-frame.test.js: the real scene, without booting Phaser or the
// DOM. Cutting between matches touches the same handful of sprites and three
// bits of text that drawing a frame does.
vi.mock('phaser', () => ({
  default: {
    Scene: class {},
    Math: { Distance: { Between: (x1, y1, x2, y2) => Math.hypot(x2 - x1, y2 - y1) } },
  },
}));

vi.mock('../src/audio.js', () => ({
  Sound: new Proxy({}, { get: () => () => {} }),
}));

import { SoccerGameScene, GAME_DURATION_SEC } from '../src/game.js';

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

/** A viewer scene mid-match, with everything a cut disturbs. */
function watching() {
  const scene = new SoccerGameScene({ role: 'viewer' });
  scene.sys = { game: { config: { width: 1408, height: 768 } } };
  scene.ball = sprite(1408, 768);
  scene.ballShadow = sprite(1408, 768);
  scene.gk1 = sprite(1408, 768);
  scene.gk2 = sprite(1408, 768);
  scene.bluePlayers = [sprite(1408, 768), sprite(1408, 768)];
  scene.redPlayers = [sprite(1408, 768), sprite(1408, 768)];
  scene.scoreText1 = label();
  scene.scoreText2 = label();
  scene.timeText = label();
  scene.plates = { blue: '', red: '' };
  scene.nameBlue = (words) => { scene.plates.blue = words; };
  scene.nameRed = (words) => { scene.plates.red = words; };
  return scene;
}

/** A frame with everyone in the far corner, so a badly drawn cut is obvious. */
function frame(overrides = {}) {
  const corner = () => [0, 0];
  return {
    score: [0, 0],
    clock: GAME_DURATION_SEC,
    ball: corner(),
    blue: [corner(), corner(), corner()],
    red: [corner(), corner(), corner()],
    ...overrides,
  };
}

describe('pointing a viewer at a different match', () => {
  let scene;

  beforeEach(() => {
    scene = watching();
  });

  it("drops the last match's pending frame", () => {
    scene.wire = frame({ score: [3, 1] });

    scene.point('BBBB');

    expect(scene.wire).toBeNull();
  });

  it('remembers which match it is now about', () => {
    scene.point('BBBB');

    expect(scene.code).toBe('BBBB');
  });

  it('asks for the next frame to be snapped rather than eased', () => {
    scene.point('BBBB');

    expect(scene.snapNext).toBe(true);
  });

  it('puts the scoreline and the clock back before the new match is drawn', () => {
    // Everything on the board belongs to the match being left. Leaving 3-1 up
    // for the tenth of a second until the first frame arrives is a scoreline
    // the room reads and believes.
    scene.score1 = 3;
    scene.score2 = 1;
    scene.matchTime = 65;
    scene.scoreText1.setText('3');
    scene.scoreText2.setText('1');
    scene.timeText.setText('01:05');

    scene.point('BBBB');

    expect([scene.score1, scene.score2]).toEqual([0, 0]);
    expect(scene.matchTime).toBe(GAME_DURATION_SEC);
    expect([scene.scoreText1.words, scene.scoreText2.words]).toEqual(['0', '0']);
    expect(scene.timeText.words).toBe('03:00');
  });

  it('takes the last two managers off the plates', () => {
    // Their names are worse than no name: they are two people in the room,
    // and the wall would be crediting them with somebody else's match.
    scene.nameManagers({ mode: 'versus',
                         seats: { blue: { name: 'Mara Bell' }, red: { name: 'Dee Okafor' } } });

    scene.point('BBBB');

    expect(scene.plates).toEqual({ blue: 'BLUE', red: 'RED' });
  });

  it('can be pointed before there is anything to draw on', () => {
    // The wall mounts the canvas once and cuts to its first match immediately.
    // Phaser boots on its own clock, so the first cut routinely lands before
    // create() has made a single text object.
    const cold = new SoccerGameScene({ role: 'viewer' });

    expect(() => cold.point('AAAA')).not.toThrow();
    expect(cold.code).toBe('AAAA');
  });
});

describe('the first frame after a cut', () => {
  let scene;

  beforeEach(() => {
    scene = watching();
    scene.point('BBBB');
  });

  it('lands the sprites on it rather than a step toward it', () => {
    // The whole reason point() exists. Eased, this frame walks eleven sprites
    // the length of the pitch, in front of the room, over about a second.
    scene.wire = frame();

    scene.applyFrame(WHOLE_STEP / 3);

    expect([scene.ball.x, scene.ball.y]).toEqual([0, 0]);
    expect(scene.bluePlayers.map((p) => p.x)).toEqual([0, 0]);
    expect(scene.redPlayers.map((p) => p.y)).toEqual([0, 0]);
    expect([scene.gk1.x, scene.gk2.x]).toEqual([0, 0]);
  });

  it('leaves the players standing rather than sprinting on the spot', () => {
    // A snap covers the pitch, and distance covered is what tells the viewer
    // someone is running. Nobody ran: the two matches are unrelated, and how
    // these players are moving is not known until the frame after this one.
    scene.wire = frame();

    scene.applyFrame(WHOLE_STEP);

    expect(scene.bluePlayers.map((p) => p.animation)).toEqual(['blue_idle', 'blue_idle']);
    expect(scene.redPlayers.map((p) => p.animation)).toEqual(['red_idle', 'red_idle']);
    expect(scene.bluePlayers.map((p) => p.facingLeft)).toEqual([null, null]);
  });

  it('does not roll the ball across the cut', () => {
    scene.wire = frame();

    scene.applyFrame(WHOLE_STEP);

    expect(scene.ball.rotation).toBe(0);
    expect([scene.ballShadow.x, scene.ballShadow.y]).toEqual([0, 0]);
  });

  it('goes back to easing on the frame after', () => {
    scene.wire = frame();
    scene.applyFrame(WHOLE_STEP);

    scene.wire = frame({ ball: [1, 1] });
    scene.applyFrame(WHOLE_STEP / 2);

    expect(scene.snapNext).toBe(false);
    expect(scene.ball.x).toBeCloseTo(704, 5);
  });

  it('is still the only thing that snaps: an ordinary frame eases', () => {
    const easing = watching();
    easing.wire = frame();

    easing.applyFrame(WHOLE_STEP / 2);

    expect(easing.ball.x).toBeCloseTo(704, 5);
  });
});

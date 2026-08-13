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

// The kits are generated per match, so the pitch is handed a different sheet
// every time: seven ready poses one match, six the next, sometimes no dive row
// at all, and never the same size twice. Nothing here may count frames or
// pixels in advance.

import { describe, it, expect, vi } from 'vitest';

vi.mock('phaser', () => ({ default: { Scene: class {}, Math: {} } }));
vi.mock('../src/audio.js', () => ({
  Sound: new Proxy({}, { get: () => () => {} }),
}));

import { SoccerGameScene, posesNamed, fitHeight, fitBody } from '../src/game.js';

function scene(sheets) {
  const made = {};
  const fake = Object.create(SoccerGameScene.prototype);
  fake.textures = {
    exists: (key) => key in sheets,
    get: (key) => ({ getFrameNames: () => sheets[key] }),
  };
  fake.anims = {
    create: (config) => { made[config.key] = config; },
    exists: (key) => key in made,
  };
  fake.made = made;
  return fake;
}

const KEEPER = (ready, dives) => [
  ...Array.from({ length: ready }, (_, i) => `ready_${i}`),
  ...Array.from({ length: dives }, (_, i) => `dive_left_${i}`),
  ...Array.from({ length: dives }, (_, i) => `dive_right_${i}`),
];

describe('posesNamed', () => {
  it('picks the frames of one pose and leaves the rest', () => {
    const names = ['ready_0', 'dive_left_0', 'ready_1', 'dive_right_0'];
    expect(posesNamed(names, 'ready')).toEqual(['ready_0', 'ready_1']);
    expect(posesNamed(names, 'dive_left')).toEqual(['dive_left_0']);
  });

  it('keeps the order the poses were drawn in, not the order they are listed', () => {
    expect(posesNamed(['ready_2', 'ready_10', 'ready_1'], 'ready'))
      .toEqual(['ready_1', 'ready_2', 'ready_10']);
  });

  it('takes a pose that is a single frame under its own name', () => {
    expect(posesNamed(['idle', 'run_0', 'kick'], 'idle')).toEqual(['idle']);
  });

  it('does not mistake one pose for the start of another', () => {
    expect(posesNamed(['run_0', 'running_0'], 'run')).toEqual(['run_0']);
  });

  it('answers with nothing when the model drew no such pose', () => {
    expect(posesNamed(['idle'], 'kick')).toEqual([]);
  });
});

describe('createAnimations', () => {
  it('gives the keeper every ready pose the model drew', () => {
    const fake = scene({
      goalkeeper_blue: KEEPER(7, 5),
      goalkeeper_red: KEEPER(6, 5),
    });

    fake.createAnimations();

    expect(fake.made.gk_blue_ready.frames).toHaveLength(7);
    expect(fake.made.gk_red_ready.frames).toHaveLength(6);
  });

  it('names the texture each frame is cut from', () => {
    const fake = scene({ goalkeeper_blue: KEEPER(2, 2) });

    fake.createAnimations();

    expect(fake.made.gk_blue_dive_left.frames).toEqual([
      { key: 'goalkeeper_blue', frame: 'dive_left_0' },
      { key: 'goalkeeper_blue', frame: 'dive_left_1' },
    ]);
  });

  it('stands a keeper who was drawn no dive up rather than leaving a dive missing', () => {
    // A dive the pitch asks for and the sheet has not got is a crash mid-save.
    const fake = scene({ goalkeeper_blue: ['ready_0', 'ready_1'] });

    fake.createAnimations();

    expect(fake.made.gk_blue_dive_left.frames).toEqual([
      { key: 'goalkeeper_blue', frame: 'ready_0' },
      { key: 'goalkeeper_blue', frame: 'ready_1' },
    ]);
  });

  it('runs an outfield player on the poses the sheet has', () => {
    const fake = scene({ player_blue: ['idle', 'run_0', 'run_1', 'kick'] });

    fake.createAnimations();

    expect(fake.made.blue_run.frames.map((f) => f.frame)).toEqual(['run_0', 'run_1']);
    expect(fake.made.blue_run.repeat).toBe(-1);
    expect(fake.made.blue_kick.frames.map((f) => f.frame)).toEqual(['kick']);
  });

  it('falls back to standing when the model drew no kick', () => {
    const fake = scene({ player_blue: ['idle', 'run_0'] });

    fake.createAnimations();

    expect(fake.made.blue_kick.frames.map((f) => f.frame)).toEqual(['idle']);
    expect(fake.made.blue_run.frames.map((f) => f.frame)).toEqual(['run_0']);
  });

  it('asks nothing of a sheet that never loaded', () => {
    const fake = scene({ player_blue: ['idle'] });

    fake.createAnimations();

    expect(fake.made.red_idle).toBeUndefined();
    expect(fake.made.gk_blue_ready).toBeUndefined();
  });
});

function sprite(frameWidth, frameHeight) {
  return {
    frame: { width: frameWidth, height: frameHeight },
    scaleX: 1,
    scaleY: 1,
    body: {
      width: 0, height: 0, offsetX: 0, offsetY: 0,
      setSize(w, h) { this.width = w; this.height = h; },
      setOffset(x, y) { this.offsetX = x; this.offsetY = y; },
    },
    setScale(s) { this.scaleX = s; this.scaleY = s; },
  };
}

describe('fitHeight', () => {
  it('stands every figure the same height on the pitch, whatever size it was drawn', () => {
    const big = sprite(96, 124);
    const small = sprite(40, 62);

    fitHeight(big, 38);
    fitHeight(small, 38);

    expect(big.frame.height * big.scaleY).toBeCloseTo(38);
    expect(small.frame.height * small.scaleY).toBeCloseTo(38);
  });
});

describe('fitBody', () => {
  it('gives a body its size on the pitch rather than in the art', () => {
    const p = sprite(96, 124);
    fitHeight(p, 38);

    fitBody(p, 11, 30);

    expect(p.body.width * p.scaleX).toBeCloseTo(11);
    expect(p.body.height * p.scaleY).toBeCloseTo(30);
  });

  it('centres the body on the figure, and drops it toward the feet when asked', () => {
    const p = sprite(96, 124);
    fitHeight(p, 38);

    fitBody(p, 11, 30, 2);

    const middleX = (p.body.offsetX + p.body.width / 2) * p.scaleX;
    const middleY = (p.body.offsetY + p.body.height / 2) * p.scaleY;
    expect(middleX).toBeCloseTo(p.frame.width * p.scaleX / 2);
    expect(middleY).toBeCloseTo(p.frame.height * p.scaleY / 2 + 2);
  });
});

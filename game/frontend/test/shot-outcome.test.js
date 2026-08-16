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

// Following one shot from the boot to whatever it met first.
//
// `blocked` counts every touch between ball and keeper, which is why one
// measured match reported 240 of them: the ball rolls onto a keeper and the
// collider fires every frame it stays there. That number cannot answer "how
// many shots were saved", and the answer to that is the whole question -- both
// sides take about thirteen shots a match and one of them scores six times
// more often. `shotEnds` answers it by counting each shot exactly once.

import { describe, it, expect, vi } from 'vitest';

vi.mock('phaser', () => ({
  default: { Scene: class {}, Math: { Clamp: (v, a, b) => Math.min(b, Math.max(a, v)) } },
}));

vi.mock('../src/audio.js', () => ({
  Sound: new Proxy({}, { get: () => () => {} }),
}));

import { SoccerGameScene } from '../src/game.js';

function scene() {
  return {
    liveShot: null,
    shotEnds: [
      SoccerGameScene.prototype.freshShotEnds(),
      SoccerGameScene.prototype.freshShotEnds(),
    ],
    resolveShot: SoccerGameScene.prototype.resolveShot,
  };
}

const total = (ends) => Object.values(ends).reduce((a, b) => a + b, 0);

describe('shot outcomes', () => {
  it('counts a shot once, against the side that took it', () => {
    const s = scene();
    s.liveShot = { team: 1, at: 0 };
    s.resolveShot('goal');
    expect(s.shotEnds[0].goal).toBe(1);
    expect(total(s.shotEnds[1])).toBe(0);
  });

  it('counts nothing more once a shot has already ended', () => {
    // A ball that goes in also touches the netting a frame later. Without
    // this, one shot would be a goal and a frame and a rebound.
    const s = scene();
    s.liveShot = { team: 1, at: 0 };
    s.resolveShot('goal');
    s.resolveShot('frame');
    s.resolveShot('taken');
    expect(total(s.shotEnds[0])).toBe(1);
    expect(s.shotEnds[0].goal).toBe(1);
  });

  it('ignores everything that happens while no shot is in flight', () => {
    // Most ball-keeper touches are not shots at all.
    const s = scene();
    s.resolveShot('saved');
    s.resolveShot('taken');
    expect(total(s.shotEnds[0])).toBe(0);
    expect(total(s.shotEnds[1])).toBe(0);
  });

  it('keeps the two sides apart', () => {
    const s = scene();
    s.liveShot = { team: 2, at: 0 };
    s.resolveShot('saved');
    expect(s.shotEnds[1].saved).toBe(1);
    expect(total(s.shotEnds[0])).toBe(0);
  });

  it('starts every counter at zero and names every way a shot can end', () => {
    const ends = SoccerGameScene.prototype.freshShotEnds();
    expect(Object.keys(ends).sort()).toEqual(
      ['faded', 'frame', 'goal', 'saved', 'stolen', 'taken', 'wide']);
    expect(total(ends)).toBe(0);
  });
});

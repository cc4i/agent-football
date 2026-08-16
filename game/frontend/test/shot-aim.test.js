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

// Where a shot is aimed. The old rule was
//
//   shotY = oppGk.y < 380 ? 460 + rand*25 : 300 - rand*25
//
// which asks whether the keeper is above a constant. A keeper with
// `attackPositioning: 0` never leaves the centre line, so the test was false on
// every shot of every match and every shot went into the same 25px band.
// Measured over 18 recorded matches on the venue, that is what blue was doing.
//
// `aimAtGoal` replaces it, and these are the properties that must hold.

import { describe, it, expect, vi } from 'vitest';

vi.mock('phaser', () => ({
  default: {
    Scene: class {},
    Math: { Clamp: (v, min, max) => Math.min(max, Math.max(min, v)) },
  },
}));

vi.mock('../src/audio.js', () => ({
  Sound: new Proxy({}, { get: () => () => {} }),
}));

import { SoccerGameScene } from '../src/game.js';

const MOUTH_TOP = 262;
const MOUTH_BOTTOM = 498;

// A minimal `this` for aimAtGoal: the mouth, and a rigged coin.
function scene(rolls = [0.4]) {
  let i = 0;
  return {
    goalMouthTop: MOUTH_TOP,
    goalMouthBottom: MOUTH_BOTTOM,
    chance: () => rolls[i++ % rolls.length],
    aimAtGoal: SoccerGameScene.prototype.aimAtGoal,
  };
}

const aim = (s, keeperY, profile = {}) =>
  s.aimAtGoal({ y: keeperY }, profile);

describe('aimAtGoal', () => {
  it('never aims where the frame or the ball would clip a post', () => {
    // The posts are 12px and the ball is 7px, so the aim band is inset by 20.
    for (let keeperY = 280; keeperY <= 480; keeperY += 10) {
      for (const finishing of [0, 0.25, 0.5, 0.75, 1]) {
        const y = aim(scene(), keeperY, { finishing });
        expect(y).toBeGreaterThanOrEqual(MOUTH_TOP + 20);
        expect(y).toBeLessThanOrEqual(MOUTH_BOTTOM - 20);
      }
    }
  });

  it('does not send every shot to one corner when the keeper holds the centre', () => {
    // The regression. Red's keeper sits at exactly 380 all match.
    const high = aim(scene([0.2]), 380, { finishing: 1 });
    const low = aim(scene([0.8]), 380, { finishing: 1 });
    expect(high).not.toBeCloseTo(low);
    expect(Math.min(high, low)).toBeLessThan(380);
    expect(Math.max(high, low)).toBeGreaterThan(380);
  });

  it('shoots at the open side when the keeper has committed to one', () => {
    // Keeper high in the goal leaves the ground below it open, and the reverse.
    expect(aim(scene(), 300, { finishing: 1 })).toBeGreaterThan(400);
    expect(aim(scene(), 460, { finishing: 1 })).toBeLessThan(360);
  });

  it('places the ball nearer the post the better the finisher', () => {
    const keeperY = 300;                      // committed high, so aim low
    const poor = aim(scene(), keeperY, { finishing: 0 });
    const fair = aim(scene(), keeperY, { finishing: 0.5 });
    const good = aim(scene(), keeperY, { finishing: 1 });
    expect(fair).toBeGreaterThan(poor);
    expect(good).toBeGreaterThan(fair);
    expect(good).toBeCloseTo(MOUTH_BOTTOM - 20);
  });

  it('still troubles the goal when nothing has been written on the shooter', () => {
    // A profile with no `finishing` is the red squad and any non-forward.
    const missing = aim(scene(), 300, {});
    const half = aim(scene(), 300, { finishing: 0.5 });
    expect(missing).toBeCloseTo(half);
    expect(missing).toBeGreaterThan(330);
  });

  it('keeps a poor finisher clear of the keeper it is shooting past', () => {
    // 0.0 must still leave the keeper's body, or the attribute would be a
    // switch that turns scoring off rather than a lever.
    const keeperY = 380;
    const y = aim(scene([0.2]), keeperY, { finishing: 0 });
    expect(Math.abs(y - keeperY)).toBeGreaterThan(17.5);   // half the body
  });
});

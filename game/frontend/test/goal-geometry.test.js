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
 * Both goals are open, and the ball can reach both goal lines.
 *
 * `buildGoalColliders(backX, frontX)` puts a wall the full height of the mouth
 * at `backX` - the back of the net - and the two posts at `frontX`, the goal
 * line. Called with the arguments the other way round, that full-height wall
 * lands *on* the goal line and boards the goal up.
 *
 * That is what happened to the right-hand goal, which is the one blue attacks.
 * It is invisible: the nets are painted into the background image, so the
 * screen looked right while blue's shots stopped 5px short of scoring. Over 36
 * measured matches blue scored 0 and red 41, and the ball's x never once
 * exceeded 0.890 of the pitch - the near face of a wall centred on 1258.
 *
 * Read out of the source rather than by booting Phaser: what broke was the
 * order of two arguments, and that is a fact about the text.
 */

import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const SOURCE = readFileSync(
  fileURLToPath(new URL('../src/game.js', import.meta.url)), 'utf8');

/** The number a `this.<name> = <number>` assignment gives, once. */
function constant(name) {
  const found = SOURCE.match(new RegExp(`this\\.${name}\\s*=\\s*(\\d+)`));
  expect(found, `game.js no longer defines ${name}`).toBeTruthy();
  return Number(found[1]);
}

/** The two arguments each `buildGoalColliders(a, b)` call is made with. */
function calls() {
  return [...SOURCE.matchAll(/this\.buildGoalColliders\(\s*this\.(\w+)\s*,\s*this\.(\w+)\s*\)/g)]
    .map(([, backX, frontX]) => ({ backX, frontX }));
}

describe('goal geometry', () => {
  it('builds exactly two goals', () => {
    expect(calls()).toHaveLength(2);
  });

  it('puts the back of the net behind the goal line, at both ends', () => {
    const goalCentre = (constant('leftGoalLine') + constant('rightGoalLine')) / 2;
    for (const { backX, frontX } of calls()) {
      const back = constant(backX);
      const front = constant(frontX);
      // "Behind" is away from the middle of the pitch. The full-height wall
      // belongs there; the goal line belongs nearer the halfway line than it.
      const backIsFurtherOut = Math.abs(back - goalCentre) > Math.abs(front - goalCentre);
      expect(backIsFurtherOut,
        `buildGoalColliders(${backX}=${back}, ${frontX}=${front}) puts the ` +
        'full-height back net on the goal line, which boards the goal up: ' +
        'nothing can be scored at that end')
        .toBe(true);
    }
  });

  it('names a line and a back for each end, never two of one kind', () => {
    // The swap that caused this reads perfectly well at a glance, because both
    // names are plausible in both slots. Spelling the rule out catches it.
    for (const { backX, frontX } of calls()) {
      expect(backX, `${backX} is not a back-of-net`).toMatch(/GoalBack$/);
      expect(frontX, `${frontX} is not a goal line`).toMatch(/GoalLine$/);
    }
  });

  it('leaves the mouth of each goal clear across its full height', () => {
    const top = constant('goalMouthTop');
    const bottom = constant('goalMouthBottom');
    expect(bottom).toBeGreaterThan(top);
    // The scoring test in checkGoals uses this same band, so a mouth the
    // colliders disagreed with would be a goal that cannot be scored or a
    // wall that can be shot through.
    expect(SOURCE).toContain('this.ball.y > this.goalMouthTop');
    expect(SOURCE).toContain('this.ball.y < this.goalMouthBottom');
  });
});

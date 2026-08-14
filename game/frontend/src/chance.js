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
 * A match that can be played the same way twice.
 *
 * The simulation drew from `Math.random()` and seeded nothing, so no test
 * could assert that two runs of one match agree -- which is exactly the
 * assertion needed once physics moved off the screen and onto a server. "The
 * grounds play the same football a tab does" has to be checkable.
 *
 * Nine lines here rather than Phaser's RandomDataGenerator, for one reason:
 * the pitch's tests mock Phaser away entirely because its bundle wants a DOM
 * at import time, so anything living inside Phaser is unreachable from a unit
 * test. A seeded stream that cannot be tested without a browser is not much of
 * an improvement on an unseeded one.
 *
 * mulberry32, seeded through a string hash. Not cryptographic and not trying
 * to be: this decides whether a striker dribbles, and what it has to be is the
 * same decision twice.
 */

/** Fold a seed string down to the 32 bits mulberry32 starts from. */
function hash(seed) {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i += 1) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/**
 * A function returning floats in [0, 1), determined entirely by `seed`.
 *
 * @param {string} seed
 * @returns {() => number}
 */
export function seededChance(seed) {
  let state = hash(String(seed));
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

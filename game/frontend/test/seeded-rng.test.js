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

import { describe, it, expect, vi } from 'vitest';

import { seededChance } from '../src/chance.js';

// Phaser's bundle reaches for `window` at import time, so the pitch's tests
// mock it away. The scene needs enough of one to be constructed and no more.
vi.mock('phaser', () => ({
  default: {
    Scene: class {},
    Math: { Distance: { Between: (x1, y1, x2, y2) => Math.hypot(x2 - x1, y2 - y1) } },
  },
}));

vi.mock('../src/audio.js', () => ({
  Sound: new Proxy({}, { get: () => () => {} }),
}));

const { SoccerGameScene } = await import('../src/game.js');

const drawFrom = (chance, count = 40) => Array.from({ length: count }, chance);

describe('seededChance', () => {
  it('gives the same stream twice for one seed', () => {
    expect(drawFrom(seededChance('ABCD-1'))).toEqual(drawFrom(seededChance('ABCD-1')));
  });

  it('gives different streams for different seeds', () => {
    expect(drawFrom(seededChance('ABCD-1'))).not.toEqual(drawFrom(seededChance('ABCD-2')));
  });

  it('gives different streams for seeds one character apart', () => {
    // A room code and a row id is the seed, so neighbouring matches differ by
    // very little. A hash that let those collide would make two matches in the
    // same venue play identically.
    expect(drawFrom(seededChance('ABCD-1'))).not.toEqual(drawFrom(seededChance('ABCE-1')));
  });

  it('stays inside [0, 1)', () => {
    const chance = seededChance('ABCD-1');
    for (let i = 0; i < 5000; i += 1) {
      const value = chance();
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThan(1);
    }
  });

  it('spreads across the range rather than clustering', () => {
    // Cheap smoke test on the generator itself: ten buckets, five thousand
    // draws, none of them empty and none of them holding half the results.
    const chance = seededChance('ABCD-1');
    const buckets = new Array(10).fill(0);
    for (let i = 0; i < 5000; i += 1) buckets[Math.floor(chance() * 10)] += 1;
    expect(Math.min(...buckets)).toBeGreaterThan(300);
    expect(Math.max(...buckets)).toBeLessThan(800);
  });
});

describe('SoccerGameScene chance', () => {
  it('is deterministic when seeded', () => {
    const one = new SoccerGameScene({ role: 'host', seed: 'ABCD-1' });
    const two = new SoccerGameScene({ role: 'host', seed: 'ABCD-1' });
    one.seedChance();
    two.seedChance();
    expect(drawFrom(() => one.chance())).toEqual(drawFrom(() => two.chance()));
  });

  it('is a different match for a different seed', () => {
    const one = new SoccerGameScene({ role: 'host', seed: 'ABCD-1' });
    const two = new SoccerGameScene({ role: 'host', seed: 'ABCD-2' });
    one.seedChance();
    two.seedChance();
    expect(drawFrom(() => one.chance())).not.toEqual(drawFrom(() => two.chance()));
  });

  it('falls back to Math.random with no seed', () => {
    // The workshop lab passes no seed and must go on behaving as it always has.
    const scene = new SoccerGameScene({ role: 'host' });
    scene.seedChance();
    for (let i = 0; i < 100; i += 1) {
      const value = scene.chance();
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThan(1);
    }
  });

  it('draws nothing from Math.random once seeded', () => {
    // Determinism that holds until the one call somebody forgot to convert is
    // not determinism. This is the assertion that catches that.
    const scene = new SoccerGameScene({ role: 'host', seed: 'ABCD-1' });
    scene.seedChance();
    const real = Math.random;
    Math.random = () => { throw new Error('a seeded match reached Math.random'); };
    try {
      for (let i = 0; i < 100; i += 1) scene.chance();
    } finally {
      Math.random = real;
    }
  });
});

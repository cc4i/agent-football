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

vi.mock('phaser', () => ({ default: { Scene: class {}, Math: {} } }));
vi.mock('../src/audio.js', () => ({
  Sound: new Proxy({}, { get: () => () => {} }),
}));

import { asset } from '../src/game.js';

// Vite rewrites asset URLs it can see in imports. It cannot see a string
// literal passed to Phaser's loader, so those have to be built from the base
// or every sprite 404s the moment the bundle is served from /pitch/.
describe('asset', () => {
  const base = import.meta.env.BASE_URL;

  it('joins onto the configured base', () => {
    expect(new URL(asset('assets/sprites/ball.png')).pathname)
      .toBe(`${base}assets/sprites/ball.png`);
  });

  it('tolerates a leading slash, because every call site had one', () => {
    expect(asset('/assets/sprites/ball.png')).toBe(asset('assets/sprites/ball.png'));
  });

  it('never doubles the separator', () => {
    expect(new URL(asset('assets/ui/scoreboard.png')).pathname).not.toContain('//');
  });

  it('is absolute, so a page on another origin still finds the sprites', () => {
    // The wall imports this module from the pitch and draws it on the arena's
    // own page. A bare path would be asked of whichever server that page came
    // from, which in development is the one that has never heard of a sprite.
    // `new URL` throws on anything that is not absolute, which is the check.
    expect(() => new URL(asset('assets/sprites/ball.png'))).not.toThrow();
  });
});

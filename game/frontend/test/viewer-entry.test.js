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

// Enough Phaser to be constructed against. `Game` records rather than boots:
// what is being tested is the configuration the wall's pitch is mounted with,
// and every line of it is a decision somebody could quietly undo.
const built = [];

vi.mock('phaser', () => ({
  default: {
    AUTO: 0,
    Scale: { FIT: 3, CENTER_BOTH: 1 },
    Scene: class {},
    Math: { Distance: { Between: (x1, y1, x2, y2) => Math.hypot(x2 - x1, y2 - y1) } },
    Game: class {
      constructor(config) {
        this.config = config;
        this.destroyed = false;
        built.push(this);
      }

      destroy(removeCanvas) { this.destroyed = removeCanvas; }
    },
  },
}));

vi.mock('../src/audio.js', () => ({
  Sound: new Proxy({}, { get: () => () => {} }),
}));

import { mount } from '../src/viewer.js';
import config from '../vite.config.js';

/** The `element` a wall hands in: a dataset and nothing else is touched. */
function container() {
  return { dataset: {} };
}

describe('the wall mounting a pitch', () => {
  let element;
  let court;

  beforeEach(() => {
    built.length = 0;
    element = container();
    court = mount(element);
  });

  it('draws into the box the page gave it, at the shape the pitch is', () => {
    // The iframe was sized by CSS and a canvas is not: 1408x768 is what the
    // scene lays itself out for, and the scaler is what makes that fill a wall.
    const { scale } = built[0].config;
    expect(scale.parent).toBe(element);
    expect([scale.width, scale.height]).toEqual([1408, 768]);
    expect([scale.mode, scale.autoCenter]).toEqual([3, 1]);
  });

  it('is a picture rather than a control', () => {
    // Escape and the tile numbers belong to the wall. A canvas that took focus
    // on boot, or a click that landed in it, would swallow both.
    expect(built[0].config.autoFocus).toBe(false);
    expect(built[0].config.input)
      .toEqual({ keyboard: false, mouse: false, touch: false, gamepad: false });
  });

  it('runs somebody else\'s match rather than one of its own', () => {
    expect(built[0].config.scene[0].role).toBe('viewer');
  });

  it('holds the canvas back until the new match has sent a frame', () => {
    // Everything on it is the last match's, down to where its players were
    // standing, and the socket for the new one has still to connect.
    court.point('BBBB');
    expect(element.dataset.waiting).toBe('true');

    court.frame({ type: 'state', score: [0, 0] });
    expect(element.dataset.waiting).toBeUndefined();
  });

  it('hands a frame to the scene rather than drawing it', () => {
    const frame = { type: 'state', score: [1, 0] };

    court.frame(frame);

    expect(built[0].config.scene[0].wire).toBe(frame);
  });

  it('points the scene at the match the wall chose', () => {
    court.point('CCCC');

    expect(built[0].config.scene[0].code).toBe('CCCC');
  });

  it('takes the canvas with it when the wall is done', () => {
    court.destroy();

    expect(built[0].destroyed).toBe(true);
  });
});

describe('the viewer entry in the build', () => {
  const { entryFileNames } = config.build.rollupOptions.output;

  it('is named, because the wall imports it by name', () => {
    // /pitch/bundle/* is frozen for a year on the way out, so the wall cannot
    // name a file in it. Hashing this one is how the import 404s in production
    // and nowhere else.
    expect(entryFileNames({ name: 'viewer' })).toBe('viewer.js');
  });

  it('leaves everything else hashed and in the bundle directory', () => {
    for (const name of ['main', 'host']) {
      expect(entryFileNames({ name })).toBe('bundle/[name]-[hash].js');
    }
  });

  it('builds the module the wall asks the arena for', () => {
    expect(config.build.rollupOptions.input.viewer).toMatch(/src\/viewer\.js$/);
  });
});

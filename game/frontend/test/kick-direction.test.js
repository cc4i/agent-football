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

// We want to exercise the REAL releaseKick() logic from game.js without booting
// Phaser or the DOM. game.js does `import Phaser from 'phaser'` and
// `import { Sound } from './audio'`, so we mock both with the minimum surface
// releaseKick / findTeammateInDirection actually use.
vi.mock('phaser', () => {
  const Phaser = {
    Scene: class {},
    Math: {
      Distance: { Between: (x1, y1, x2, y2) => Math.hypot(x2 - x1, y2 - y1) },
      Angle: { Between: (x1, y1, x2, y2) => Math.atan2(y2 - y1, x2 - x1) },
      Clamp: (v, min, max) => Math.min(max, Math.max(min, v)),
    },
  };
  return { default: Phaser };
});

vi.mock('../src/audio.js', () => ({
  Sound: new Proxy({}, { get: () => () => {} }),
}));

import { SoccerGameScene } from '../src/game.js';

// Capture velocity assigned to the ball by releaseKick.
function makeBall(x, y) {
  return {
    x,
    y,
    _vx: 0,
    _vy: 0,
    body: { velocity: { x: 0, y: 0 } },
    setVelocity(vx, vy) {
      this._vx = vx;
      this._vy = vy;
      this.body.velocity.x = vx;
      this.body.velocity.y = vy;
    },
  };
}

function makePlayer(x, y) {
  return { x, y };
}

// Build a minimal `this` for releaseKick. `teammates` lets us force the
// shoot branch (empty) or exercise the pass branch.
function makeScene({ player, ballAt, teammates = [] }) {
  const ball = makeBall(ballAt.x, ballAt.y);
  const scene = {
    possessor: player, // player has the ball, so the kick always fires
    ball,
    ballZVelocity: 0,
    captureReadyAt: 0,
    lastTouchTeam: 0,
    bluePlayers: teammates,
    redPlayers: teammates,
    cameras: { main: { shake: () => {} } },
    time: { now: 1000 },
    // use the real helper
    findTeammateInDirection: SoccerGameScene.prototype.findTeammateInDirection,
  };
  return scene;
}

function release(scene, player, dx, dy, power = 0.85) {
  SoccerGameScene.prototype.releaseKick.call(scene, player, 1, dx, dy, power);
}

const DEG = (rad) => (rad * 180) / Math.PI;

describe('releaseKick direction (shoot branch, no teammates in range)', () => {
  // Eight compass directions in screen space (y+ is DOWN).
  const dirs = [
    { name: 'right (→ toward blue goal)', dx: 1, dy: 0 },
    { name: 'down-right', dx: 1, dy: 1 },
    { name: 'down (↓ bottom of pitch)', dx: 0, dy: 1 },
    { name: 'down-left', dx: -1, dy: 1 },
    { name: 'left', dx: -1, dy: 0 },
    { name: 'up-left', dx: -1, dy: -1 },
    { name: 'up (↑ top of pitch)', dx: 0, dy: -1 },
    { name: 'up-right', dx: 1, dy: -1 },
  ];

  for (const d of dirs) {
    it(`kicks toward ${d.name}`, () => {
      const player = makePlayer(700, 380);
      const scene = makeScene({ player, ballAt: { x: 726, y: 388 } });
      release(scene, player, d.dx, d.dy);

      const vx = scene.ball._vx;
      const vy = scene.ball._vy;
      const wantAngle = DEG(Math.atan2(d.dy, d.dx));
      const gotAngle = DEG(Math.atan2(vy, vx));

      // Helpful trace so the sweep prints actual vs intended.
      console.log(
        `${d.name.padEnd(28)} intended=(${d.dx},${d.dy}) ` +
          `want≈${wantAngle.toFixed(0)}°  got=(${vx.toFixed(0)},${vy.toFixed(0)}) ${gotAngle.toFixed(0)}°`
      );

      // The kick should travel in the intended direction.
      let diff = Math.abs(wantAngle - gotAngle) % 360;
      if (diff > 180) diff = 360 - diff;
      expect(diff).toBeLessThan(5);
    });
  }
});

describe('vertical component is preserved (downward kicks must go down)', () => {
  it('a straight-down kick yields positive vy', () => {
    const player = makePlayer(700, 380);
    const scene = makeScene({ player, ballAt: { x: 726, y: 388 } });
    release(scene, player, 0, 1);
    expect(scene.ball._vy).toBeGreaterThan(0); // y+ is down on screen
    expect(Math.abs(scene.ball._vx)).toBeLessThan(1);
  });
});

// The ball is rendered at  sprite.y = body.y + halfHeight - ballZ.  With the
// loft removed, ballZVelocity is 0, so the on-screen path equals the ground
// path: the ball rolls in its true direction. (Before the fix, the per-frame
// ballZVelocity added ~ballZVelocity*60 px/s of UPWARD screen motion, which is
// what made every kick look "diagonal top".)
const FPS = 60;
function screenLaunchVelocity(scene) {
  return { vx: scene.ball._vx, vy: scene.ball._vy - scene.ballZVelocity * FPS };
}

describe('on-screen launch direction matches the ground direction (loft removed)', () => {
  it('a FLAT kick toward goal travels flat on screen too', () => {
    const player = makePlayer(700, 380);
    const scene = makeScene({ player, ballAt: { x: 726, y: 388 } });
    release(scene, player, 1, 0, 0.85); // intended: straight right, dy = 0

    const { vx, vy } = screenLaunchVelocity(scene);
    const screenAngle = DEG(Math.atan2(vy, vx));
    console.log(
      `FLAT kick: ground=(${scene.ball._vx.toFixed(0)},${scene.ball._vy.toFixed(0)}) ` +
        `screen=(${vx.toFixed(0)},${vy.toFixed(0)}) angle=${screenAngle.toFixed(0)}°`
    );
    expect(scene.ball._vy).toBe(0);            // physics: flat
    expect(vy).toBe(0);                         // screen: flat (no upward drag)
    expect(Math.abs(screenAngle)).toBeLessThan(1);
  });

  it('a DOWN-RIGHT kick actually travels downward on screen', () => {
    const player = makePlayer(700, 380);
    const scene = makeScene({ player, ballAt: { x: 726, y: 388 } });
    release(scene, player, 1, 1, 0.85); // intended: down-right (toward bottom)

    const { vy } = screenLaunchVelocity(scene);
    console.log(
      `DOWN-RIGHT kick: ground vy=${scene.ball._vy.toFixed(0)} ` +
        `screen vy=${vy.toFixed(0)} (${vy > 0 ? 'down' : 'up'})`
    );
    expect(scene.ball._vy).toBeGreaterThan(0); // physics says down
    expect(vy).toBe(scene.ball._vy);           // screen matches: still down
  });

  it('the on-screen path matches the ground path in all 8 directions', () => {
    const dirs = [
      [1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1],
    ];
    for (const [dx, dy] of dirs) {
      const player = makePlayer(700, 380);
      const scene = makeScene({ player, ballAt: { x: 726, y: 388 } });
      release(scene, player, dx, dy, 0.85);
      const { vx, vy } = screenLaunchVelocity(scene);
      const groundAngle = DEG(Math.atan2(scene.ball._vy, scene.ball._vx));
      const screenAngle = DEG(Math.atan2(vy, vx));
      let diff = Math.abs(groundAngle - screenAngle) % 360;
      if (diff > 180) diff = 360 - diff;
      expect(diff).toBeLessThan(1);
    }
  });
});

describe('no loft is applied (ball stays on the ground)', () => {
  it('a kick leaves the ball z-velocity at zero', () => {
    const player = makePlayer(700, 380);
    const scene = makeScene({ player, ballAt: { x: 726, y: 388 } });
    release(scene, player, 1, 0, 0.85);
    expect(scene.ballZVelocity).toBe(0);
    expect(scene.ballZ).toBe(0);
  });
});

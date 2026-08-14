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
 * One canvas for the whole wall, and a way to point it at a match.
 *
 * The wall used to frame the pitch page in an iframe, which meant every cut
 * cost a page load, a Phaser boot and a texture decode. That was affordable
 * for a carousel turning every twelve seconds and is not for fifty tiles
 * somebody clicks through. This is the same scene mounted directly: booted
 * once when the wall opens, pointed at a different match from then on.
 *
 * Nothing here opens a socket. The wall already holds one per room for its
 * tiles and one more for whatever is on centre court, so frames, events and
 * the two managers' names are handed in rather than fetched -- which is what
 * keeps this file about drawing and the wall about which match is on.
 *
 * A stable filename on purpose: /pitch/bundle/* is content-hashed so a year of
 * caching is safe, which means the wall cannot name a file in it. This one is
 * built un-hashed and served revalidated instead, and it is the only thing the
 * wall imports.
 */
import Phaser from 'phaser';

import { SoccerGameScene } from './game.js';

const WIDTH = 1408;
const HEIGHT = 768;

/**
 * Put a pitch in `element` and hand back the four things a wall does to it.
 *
 * @param {HTMLElement} element - the container. It is sized by the page and
 *   the canvas is fitted into it, so the wall's layout stays the wall's.
 * @returns {{point: function(?string), frame: function(object),
 *            cheer: function(string), managers: function(object),
 *            destroy: function()}}
 */
export function mount(element) {
  const scene = new SoccerGameScene({ role: 'viewer' });
  const game = new Phaser.Game({
    type: Phaser.AUTO,
    // Centre court is a picture, not a control: the operator's Escape and tile
    // numbers belong to the page. The iframe was held off with pointer-events
    // and this is the same argument one layer in -- no focus on boot, and no
    // input plugins to take a click that lands on the canvas.
    autoFocus: false,
    input: { keyboard: false, mouse: false, touch: false, gamepad: false },
    physics: { default: 'arcade', arcade: { gravity: { y: 0 }, debug: false } },
    scale: {
      parent: element,
      width: WIDTH,
      height: HEIGHT,
      // The iframe was a box the page sized and the match filled. A canvas is
      // 1408x768 whatever the wall gives it, so the scaler is what stands in
      // for that: the pitch keeps its shape and grows to the space it has.
      mode: Phaser.Scale.FIT,
      autoCenter: Phaser.Scale.CENTER_BOTH,
    },
    // An instance rather than the class, because which of the two things this
    // scene is has to be settled before create() runs a whistle and a clock.
    scene: [scene],
  });

  return {
    /**
     * Show a different match, or nothing at all.
     *
     * The canvas is marked as waiting until a frame of the new match arrives.
     * Everything on it belongs to the last one -- eleven sprites standing
     * where that match left them -- and the socket for the new one has still
     * to connect, which is long enough to read.
     */
    point(code) {
      element.dataset.waiting = 'true';
      scene.point(code);
    },
    /** A frame off centre court's room socket. Drawn on the next tick. */
    frame(message) {
      if (element.dataset.waiting) delete element.dataset.waiting;
      scene.wire = message;
    },
    /** Something that happened in it: a goal to flash, a whistle to blow. */
    cheer(kind) {
      scene.cheer(kind);
    },
    /** Who is in the two dugouts, for the nameplates in the corners. */
    managers(snapshot) {
      scene.nameManagers(snapshot);
    },
    destroy() {
      game.destroy(true);
    },
  };
}

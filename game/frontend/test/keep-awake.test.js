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

import { describe, it, expect } from 'vitest';

// arena.js reads which room it is in from the query string as it loads, and
// these tests run in node rather than a browser. One property is enough to get
// the module in; keepAwake is handed its own browser by every test below.
globalThis.window = { location: { search: '' } };
const { keepAwake } = await import('../src/arena.js');

/** A browser that grants every lock, and remembers each one it handed out. */
function browser({ grants = true } = {}) {
  const given = [];
  const listeners = [];
  const page = {
    visibilityState: 'visible',
    addEventListener(name, run) { if (name === 'visibilitychange') listeners.push(run); },
    // Hide and show the tab the way a browser does: the lock it granted is
    // revoked on the way out, and the page is told on the way back in.
    async hide() {
      this.visibilityState = 'hidden';
      given.filter((lock) => !lock.released).forEach((lock) => lock.revoke());
      await Promise.all(listeners.map((run) => run()));
    },
    async show() {
      this.visibilityState = 'visible';
      await Promise.all(listeners.map((run) => run()));
    },
  };
  const navigation = {
    wakeLock: {
      async request(kind) {
        if (!grants) throw new Error('the browser said no');
        const lock = { kind, released: false, onRelease: null };
        lock.addEventListener = (name, run) => { if (name === 'release') lock.onRelease = run; };
        lock.revoke = () => { lock.released = true; if (lock.onRelease) lock.onRelease(); };
        given.push(lock);
        return lock;
      },
    },
  };
  return { navigation, page, given };
}

describe('keeping the host screen awake', () => {
  it('asks for the screen while the match is on it', async () => {
    const { navigation, page, given } = browser();

    await keepAwake(navigation, page);

    expect(given.map((lock) => lock.kind)).toEqual(['screen']);
  });

  it('asks again when the tab comes back, since the browser took it away', async () => {
    // Half a minute hidden is enough for the arena to abandon the room, so a
    // host that has just been looked away from has to re-arm itself.
    const { navigation, page, given } = browser();
    await keepAwake(navigation, page);

    await page.hide();
    await page.show();

    expect(given).toHaveLength(2);
    expect(given[1].released).toBe(false);
  });

  it('does not ask for one it cannot be given while the tab is hidden', async () => {
    const { navigation, page, given } = browser();
    page.visibilityState = 'hidden';

    await keepAwake(navigation, page);

    expect(given).toHaveLength(0);
  });

  it('holds the one it has rather than stacking another on top', async () => {
    const { navigation, page, given } = browser();
    await keepAwake(navigation, page);

    await page.show();

    expect(given).toHaveLength(1);
  });

  it('plays on when the browser refuses', async () => {
    // A flat battery and a policy against locks both land here, and neither is
    // worth an unhandled rejection in the middle of a match.
    const { navigation, page } = browser({ grants: false });

    await expect(keepAwake(navigation, page)).resolves.toBeUndefined();
  });

  it('plays on where there is no such thing as a wake lock', async () => {
    const { page } = browser();

    await expect(keepAwake({}, page)).resolves.toBeUndefined();
  });
});

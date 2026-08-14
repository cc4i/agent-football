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

import { describe, expect, it, vi } from 'vitest';
import { createSubstitutionPoll } from '../src/substitutions.js';

const POLL_URL = '/player_state/substitutions/ABCD__blue.json';

// One fetch stub, handed a queue of what the file says on each successive poll.
const serving = (...bodies) => {
  const calls = [];
  const fetch = vi.fn(async (url) => {
    calls.push(url);
    const body = bodies[Math.min(calls.length - 1, bodies.length - 1)];
    if (body === undefined) return { ok: false, json: async () => ({}) };
    return { ok: true, json: async () => body };
  });
  return { fetch, calls };
};

const pollFor = (fetch) => {
  const notify = vi.fn();
  return { notify, poll: createSubstitutionPoll({ url: POLL_URL, fetch, notify }) };
};

const knock = (ts) => ({ forward: { ts, action: 'injury', reason: 'hamstring' } });

describe('the substitution poll', () => {
  it('asks for the file itself, with nothing on the end of it', async () => {
    // A `?t=${Date.now()}` made every poll a fresh URL, so the `no-cache` on the
    // mount could never answer with the 304 it exists for.
    const { fetch, calls } = serving({});
    await pollFor(fetch).poll.check();
    expect(calls).toEqual([POLL_URL]);
    expect(calls[0]).not.toContain('?');
  });

  it('says nothing about an entry it has already shown', async () => {
    const { fetch } = serving(knock(1700), knock(1700));
    const { notify, poll } = pollFor(fetch);
    await poll.check();
    expect(notify).toHaveBeenCalledTimes(1);
    await poll.check();
    expect(notify).toHaveBeenCalledTimes(1);
  });

  it('says something when the same player asks again', async () => {
    const { fetch } = serving(knock(1700), knock(1701));
    const { notify, poll } = pollFor(fetch);
    await poll.check();
    await poll.check();
    expect(notify).toHaveBeenCalledTimes(2);
    expect(notify).toHaveBeenLastCalledWith('forward', 'injury', 'hamstring');
  });

  it('keeps each role to its own timestamp', async () => {
    const { fetch } = serving({
      forward: { ts: 1700, action: 'injury', reason: 'hamstring' },
      defender: { ts: 1200, action: 'substitution', reason: 'tired' },
    });
    const { notify, poll } = pollFor(fetch);
    await poll.check();
    expect(notify).toHaveBeenCalledTimes(2);
  });

  it('primes from a file left by an earlier match rather than toasting it', async () => {
    const { fetch } = serving(knock(1700));
    const { notify, poll } = pollFor(fetch);
    await poll.prime();
    await poll.check();
    expect(notify).not.toHaveBeenCalled();
  });

  it('still reports what happens after priming', async () => {
    const { fetch } = serving(knock(1700), knock(1800));
    const { notify, poll } = pollFor(fetch);
    await poll.prime();
    await poll.check();
    expect(notify).toHaveBeenCalledWith('forward', 'injury', 'hamstring');
  });

  it('ignores a response that is not ok, because the file may not exist yet', async () => {
    const fetch = vi.fn(async () => ({ ok: false, json: async () => knock(1700) }));
    const { notify, poll } = pollFor(fetch);
    await expect(poll.check()).resolves.toBeUndefined();
    expect(notify).not.toHaveBeenCalled();
  });

  it('ignores a fetch that throws, and a body that is not json', async () => {
    const refused = vi.fn(async () => { throw new Error('offline'); });
    await expect(pollFor(refused).poll.check()).resolves.toBeUndefined();

    const garbled = vi.fn(async () => ({
      ok: true, json: async () => { throw new SyntaxError('half-written'); },
    }));
    const { notify, poll } = pollFor(garbled);
    await expect(poll.check()).resolves.toBeUndefined();
    expect(notify).not.toHaveBeenCalled();
  });
});

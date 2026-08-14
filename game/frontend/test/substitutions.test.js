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

// @vitest-environment jsdom
//
// The only file in the pitch that builds DOM rather than football, so the only
// one that needs a DOM to be tested in. Everything else here runs in node.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { showCondition } from '../src/substitutions.js';

// There is no poll left to test. A knock is a room event now, so what this
// file holds on to is the drawing: the words a manager reads off the corner of
// the pitch, and the toast clearing itself up afterwards.
const knock = { role: 'forward', action: 'injury', detail: 'hamstring' };

const stack = () => document.getElementById('notification-stack');

beforeEach(() => {
  document.body.replaceChildren();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('the condition toast', () => {
  it('names the player and says what they reported', () => {
    const toast = showCondition(knock);
    expect(toast.textContent).toContain('FORWARD');
    expect(toast.textContent).toContain('reported an injury');
    expect(toast.textContent).toContain('(hamstring)');
  });

  it('tells an injury and a substitution request apart', () => {
    expect(showCondition(knock).className).toContain('toast-injury');
    const asked = showCondition({ role: 'goalkeeper', action: 'substitution', detail: 'tired' });
    expect(asked.className).toContain('toast-sub');
    expect(asked.textContent).toContain('requested a substitution');
  });

  it('renders the detail as text rather than as markup', () => {
    // It is a language model's words, arriving over a socket, and drawn on a
    // wall in a room full of people.
    const toast = showCondition({ ...knock, detail: '<img src=x onerror=alert(1)>' });
    expect(toast.querySelector('img')).toBeNull();
    expect(toast.textContent).toContain('<img src=x onerror=alert(1)>');
  });

  it('leaves the brackets out when the player said nothing', () => {
    expect(showCondition({ role: 'defender', action: 'injury' }).textContent)
      .not.toContain('(');
  });

  it('stacks one match\'s reports in the order they arrived', () => {
    showCondition(knock);
    showCondition({ role: 'defender', action: 'substitution', detail: 'tired' });
    expect([...stack().children].map((toast) => toast.querySelector('strong').textContent))
      .toEqual(['FORWARD', 'DEFENDER']);
  });

  it('clears itself up, so a long match does not fill the corner', () => {
    showCondition(knock);
    expect(stack().children).toHaveLength(1);
    vi.advanceTimersByTime(5000);
    expect(stack().firstChild.className).toContain('toast-hide');
    vi.advanceTimersByTime(600);
    expect(stack().children).toHaveLength(0);
  });
});

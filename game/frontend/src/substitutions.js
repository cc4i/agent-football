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

// The toast in the top-right corner when a player reports a knock or asks to
// come off. This file used to hold the poll that noticed one: a JSON file
// beside the pitch, read every two seconds, with the whole rule about acting on
// an entry once. The report is a room event now, so noticing it is the socket's
// job and the only thing left here is the drawing -- which is still worth its
// own file, because it is the one thing on this page that is not a football.

const VERB = {
  injury: 'reported an injury',
  substitution: 'requested a substitution',
};

// How long a toast stays up, and how long its fade lasts afterwards.
const SHOWN_MS = 5000;
const FADING_MS = 600;

function stack() {
  let found = document.getElementById('notification-stack');
  if (found) return found;
  found = document.createElement('div');
  found.id = 'notification-stack';
  document.body.appendChild(found);
  return found;
}

/**
 * Draw one condition report.
 *
 * @param {object} report - a `substitution` event's payload.
 * @param {string} report.role - whose it is: defender, midfielder, forward, goalkeeper.
 * @param {string} report.action - 'injury' or 'substitution'.
 * @param {string} report.detail - what the player said about it, in its own words.
 */
export function showCondition({ role, action, detail } = {}) {
  const injured = action === 'injury';
  const toast = document.createElement('div');
  toast.className = `pitch-toast ${injured ? 'toast-injury' : 'toast-sub'}`;

  const iconEl = document.createElement('span');
  iconEl.className = 'toast-icon';
  iconEl.textContent = injured ? '⚠️' : '🔁';

  const textEl = document.createElement('span');
  textEl.className = 'toast-text';
  const strong = document.createElement('strong');
  strong.textContent = String(role || '').toUpperCase();
  textEl.append(strong, ` ${VERB[action] || VERB.substitution}`);
  if (detail) {
    const reasonEl = document.createElement('span');
    reasonEl.className = 'toast-reason';
    reasonEl.textContent = `(${detail})`;
    textEl.append(' ', reasonEl);
  }

  toast.append(iconEl, textEl);
  stack().appendChild(toast);
  setTimeout(() => toast.classList.add('toast-hide'), SHOWN_MS);
  setTimeout(() => toast.remove(), SHOWN_MS + FADING_MS);
  return toast;
}

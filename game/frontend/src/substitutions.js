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

// Watches the file the game's MCP server writes when a player asks to come off
// or reports a knock. Out here rather than in main.js because the rule inside
// it - act on an entry once, the first time its timestamp moves - is the kind
// of thing a test should be able to hold on its own.
const ROLES = ['defender', 'midfielder', 'forward', 'goalkeeper'];

export function createSubstitutionPoll({ url, fetch, notify }) {
  // The last timestamp acted on per role, so each request shows exactly once.
  const lastSeen = { defender: 0, midfielder: 0, forward: 0, goalkeeper: 0 };

  // No cache-buster on the URL. The mount serving this sends `no-cache`, which
  // already means the browser asks every time and only reuses the body when the
  // ETag still matches. A `?t=` would make every poll a fresh URL and so a full
  // 200 forever - at a poll every two seconds per pitch, a venue's worth of JSON
  // bodies a second that could each have been an empty 304.
  async function read() {
    try {
      const response = await fetch(url);
      if (!response.ok) return null; // The file may not exist yet.
      return await response.json();
    } catch (err) {
      // No file, or a half-written one. Either way there is nothing to act on.
      return null;
    }
  }

  return {
    // Seed the timestamps from whatever is already there, so a knock from an
    // earlier match does not toast the moment this page loads.
    async prime() {
      const data = await read();
      if (!data) return;
      ROLES.forEach((role) => {
        if (data[role] && data[role].ts) lastSeen[role] = data[role].ts;
      });
    },

    async check() {
      const data = await read();
      if (!data) return;
      ROLES.forEach((role) => {
        const entry = data[role];
        if (entry && entry.ts && entry.ts > lastSeen[role]) {
          lastSeen[role] = entry.ts;
          notify(role, entry.action, entry.reason);
        }
      });
    },
  };
}

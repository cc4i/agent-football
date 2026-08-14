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

import { resolve } from 'node:path';

import { defineConfig } from 'vite';

export default defineConfig({
  // Deployed, the pitch is a subdirectory of the arena rather than an origin of
  // its own: with no domain there is no certificate, so there is no second
  // HTTPS origin to be had. The dev server keeps serving from / and
  // import.meta.env.BASE_URL follows either way.
  base: process.env.PITCH_BASE || '/',

  // Vite's own output is content-hashed and the public/ folder's is not, and
  // they land in the same dist/assets/ by default. Separating them is what lets
  // the arena cache the bundle for a year and still let a regenerated kit
  // through: one directory is safe to freeze, the other never is.
  build: {
    assetsDir: 'bundle',
    rollupOptions: {
      // The viewer entry exists to be imported, and an entry nothing in the
      // build imports has its exports dropped by default: the wall's
      // `import { mount }` then fails against a one-line file that pulls the
      // shared chunk in and re-exports nothing from it.
      preserveEntrySignatures: 'strict',
      input: {
        // The pitch, unchanged: the lab, and the picture of a match on a wall.
        main: resolve(import.meta.dirname, 'index.html'),
        // The farm's page. The same build on purpose, and loaded from the
        // arena at runtime, so version skew between what simulates a match and
        // what renders it is impossible by construction.
        host: resolve(import.meta.dirname, 'host.html'),
        // No page of its own: a module the arena's wall imports to mount a
        // pitch in its own layout.
        viewer: resolve(import.meta.dirname, 'src/viewer.js'),
      },
      output: {
        // Everything else is content-hashed and frozen for a year on the way
        // out. This one file is named, because the wall has to import it
        // without having read a manifest to find out what it is called.
        entryFileNames: (chunk) => (chunk.name === 'viewer'
          ? 'viewer.js'
          : 'bundle/[name]-[hash].js'),
      },
    },
  },

  server: {
    proxy: {
      '/api-apps': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        secure: false,
        rewrite: (path) => path.replace(/^\/api-apps/, '/apps')
      },
      '/run_sse': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        secure: false
      },
      // The arena owns rooms, profiles and the match feed. Proxying rather than
      // opening CORS on :8003 keeps the pitch same-origin, which is what lets
      // the big screen frame it and lets the socket carry the host token.
      // Anchored patterns, so this can never swallow /api-apps above it.
      '^/api/': {
        target: 'http://localhost:8003',
        changeOrigin: true,
        secure: false
      },
      '^/ws/': {
        target: 'ws://localhost:8003',
        ws: true,
        changeOrigin: true,
        secure: false
      }
    }
  }
});

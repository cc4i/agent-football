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

import { defineConfig } from 'vite';

export default defineConfig({
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

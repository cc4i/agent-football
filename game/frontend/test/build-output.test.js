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
 * What the arena actually gets handed, built the way the image builds it.
 *
 * The rest of the suite reads the config. This one runs it, because the whole
 * of the wall's centre court is `import { mount } from '/pitch/viewer.js'`
 * resolving against a real file - and the first build of that file was a
 * valid, minified, thirty-byte module that exported nothing at all. The
 * config was right; the output was empty. Nothing short of building would
 * have said so, and what it costs is a second.
 */
import { mkdtemp, readFile, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

import { describe, it, expect, beforeAll } from 'vitest';
import { build } from 'vite';

const FRONTEND = resolve(import.meta.dirname, '..');

describe('the built pitch', () => {
  let out;

  beforeAll(async () => {
    out = await mkdtemp(join(tmpdir(), 'pitch-build-'));
    await build({
      root: FRONTEND,
      logLevel: 'silent',
      // Deployed under /pitch/ rather than at the root, as the arena serves it.
      base: '/pitch/',
      build: { outDir: out, emptyOutDir: true },
    });
  }, 180000);

  it('gives the wall a module it can import a mount from', async () => {
    const entry = await readFile(join(out, 'viewer.js'), 'utf8');

    expect(entry).toMatch(/export\s*\{[^}]*\bmount\b/);
  });

  it('keeps that module out of the directory that is frozen for a year', async () => {
    const bundle = await readdir(join(out, 'bundle'));

    expect(bundle).not.toContain('viewer.js');
    // And everything in there is still hashed, which is what makes freezing it
    // safe in the first place.
    for (const name of bundle) expect(name).toMatch(/-[A-Za-z0-9_-]{8,}\.(js|css)$/);
  });

  it('still builds the two pages', async () => {
    const root = await readdir(out);

    expect(root).toEqual(expect.arrayContaining(['index.html', 'host.html']));
  });
});

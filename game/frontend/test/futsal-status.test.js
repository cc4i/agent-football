import { describe, expect, it, vi } from 'vitest';
import { createStatusHook } from '../src/status.js';

// arena.js reads which room it is in from the query string once, as it loads,
// so a test picks its room by setting the search string and asking for a fresh
// copy of the module.
const arenaIn = async (search) => {
  globalThis.window = { location: { search } };
  vi.resetModules();
  return import('../src/arena.js');
};

const sceneStub = (fields) => ({
  scene: { getScene: (key) => (key === 'SoccerGameScene' ? fields : null) },
});

describe('createStatusHook', () => {
  it('returns null before kick-off, when no game exists yet', () => {
    expect(createStatusHook(() => null)()).toBeNull();
  });

  it('returns null when the scene is not running', () => {
    const game = { scene: { getScene: () => null } };
    expect(createStatusHook(() => game)()).toBeNull();
  });

  it('reports the live score, clock and active flag', () => {
    const game = sceneStub({ score1: 2, score2: 1, matchTime: 41.5, gameActive: true });
    expect(createStatusHook(() => game)()).toEqual({
      score1: 2, score2: 1, matchTime: 41.5, gameActive: true,
    });
  });

  it('still reports the final score after full time', () => {
    const game = sceneStub({ score1: 3, score2: 1, matchTime: 0, gameActive: false });
    const status = createStatusHook(() => game)();
    expect(status.gameActive).toBe(false);
    expect(status.score1).toBe(3);
  });

  it('coerces missing numeric fields to null rather than undefined', () => {
    const game = sceneStub({ gameActive: true });
    const status = createStatusHook(() => game)();
    expect(status.score1).toBeNull();
    expect(status.matchTime).toBeNull();
  });
});

describe('who runs the autonomous status check', () => {
  it('runs it in the workshop', async () => {
    const { shouldRunStatusCheck, isViewer } = await arenaIn('');
    expect(shouldRunStatusCheck()).toBe(true);
    expect(isViewer()).toBe(false);
  });

  it('does not run it in a real match', async () => {
    const { shouldRunStatusCheck, isViewer } = await arenaIn('?room=ABCD&as=host&client_id=host-1');
    expect(shouldRunStatusCheck()).toBe(false);
    expect(isViewer()).toBe(false);
  });

  it('still polls for injuries in a real match, because a shout can cause one', async () => {
    // A host in a real match runs the substitution poll (!isViewer() is false)
    // but not the status check (shouldRunStatusCheck() is false).
    const { shouldRunStatusCheck, isViewer } = await arenaIn('?room=ABCD&as=host&client_id=host-1');
    expect(shouldRunStatusCheck()).toBe(false);
    expect(isViewer()).toBe(false);
  });

  it('does not poll for a viewer, whose match somebody else is running', async () => {
    // A viewer runs neither: no status check (shouldRunStatusCheck() is false)
    // and no substitution poll (isViewer() is true, so !isViewer() is false).
    const { shouldRunStatusCheck, isViewer } = await arenaIn('?room=ABCD');
    expect(shouldRunStatusCheck()).toBe(false);
    expect(isViewer()).toBe(true);
  });
});

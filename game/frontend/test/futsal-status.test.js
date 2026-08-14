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
    const { shouldRunStatusCheck } = await arenaIn('');
    expect(shouldRunStatusCheck()).toBe(true);
  });

  it('does not run it in a venue room', async () => {
    // Each check wakes a coach, a captain and four specialists. The venue's
    // own matches are played by the grounds, which has no such timer, so this
    // is about the lab: pointed at a live room it must watch and not prod.
    const { shouldRunStatusCheck } = await arenaIn('?room=ABCD');
    expect(shouldRunStatusCheck()).toBe(false);
  });

  it('ignores the roles a tab used to be able to ask for', async () => {
    // A pitch was a host, a viewer or the workshop. The grounds took the first
    // and the wall's direct mount took the second, so these are two query
    // parameters nothing reads -- and a stale bookmark bearing them opens the
    // lab, rather than a page that thinks it is running somebody's match.
    const { room } = await arenaIn('?as=host&client_id=host-1');
    expect(room).toEqual({ code: 'WRKS', team: 'blue', inMatch: false });
  });
});

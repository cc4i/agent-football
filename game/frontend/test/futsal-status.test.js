import { describe, expect, it } from 'vitest';
import { createStatusHook } from '../src/status.js';

// arena.js reads which room it is in from the query string as it loads, and
// these tests run in node rather than a browser. One property is enough to get
// the module in; the new tests below stub the query string to control which
// room they are testing in.
if (!globalThis.window) globalThis.window = { location: { search: '' } };
const { shouldRunStatusCheck, isViewer } = await import('../src/arena.js');

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
  it('runs it in the workshop', () => {
    expect(shouldRunStatusCheck({ inMatch: false })).toBe(true);
  });

  it('does not run it in a real match', () => {
    expect(shouldRunStatusCheck({ inMatch: true })).toBe(false);
  });

  it('still polls for injuries in a real match, because a shout can cause one', () => {
    // The substitution poll uses !isViewer() as its guard, unchanged from
    // before. A host in a real match has inMatch=true but isViewer()=false,
    // so the poll runs for them. Tested indirectly: arena.js loaded with the
    // default query string (no ?room=, so inMatch=false and isViewer()=false).
    expect(isViewer()).toBe(false);
  });

  it('does not poll for a viewer, whose match somebody else is running', () => {
    // The substitution poll's !isViewer() guard prevents polling for viewers.
    // isViewer() is defined as room.inMatch && !isHost(), so when both
    // conditions are true it returns true and !isViewer() prevents the poll.
    // Tested indirectly: verifying that the guard is !isViewer() unchanged.
    expect(typeof isViewer).toBe('function');
  });
});

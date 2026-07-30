import { describe, expect, it } from 'vitest';
import { createStatusHook } from '../src/status.js';

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

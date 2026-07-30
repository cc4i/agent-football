// Reads the live score out of the Phaser scene. The score is drawn on the
// canvas, so nothing else outside the game can see it.
export const SCENE_KEY = 'SoccerGameScene';

const numberOrNull = (value) => (typeof value === 'number' ? value : null);

export function createStatusHook(getGame) {
  return function status() {
    const scene = getGame()?.scene?.getScene(SCENE_KEY);
    if (!scene) return null;
    return {
      score1: numberOrNull(scene.score1),
      score2: numberOrNull(scene.score2),
      matchTime: numberOrNull(scene.matchTime),
      gameActive: Boolean(scene.gameActive),
    };
  };
}

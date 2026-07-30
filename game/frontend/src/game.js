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

import Phaser from 'phaser';
import { Sound } from './audio';

export const GAME_DURATION_SEC = 180;
export const STATUS_CHECK_MS = 55000;

export class SoccerGameScene extends Phaser.Scene {
  constructor() {
    super({ key: 'SoccerGameScene' });
    this.score1 = 0;
    this.score2 = 0;
    this.matchTime = GAME_DURATION_SEC;
    this.timerEvent = null;
    this.gameActive = false;
    this.isResetting = false;

    // Ball is top-down only and always rolls on the ground. ballZ/ballZVelocity
    // are kept (pinned to 0) so the goal/capture checks that reference them still
    // read as "grounded"; there is no loft/gravity any more.
    this.ballZ = 0;
    this.ballZVelocity = 0;

    // 5-a-side team player tracking
    this.bluePlayers = [];
    this.redPlayers = [];
    this.activeBlueIdx = 0;
    this.activeRedIdx = 0;

    // Active-player auto-switch debouncing
    this.lastBlueSwitch = 0;
    this.lastRedSwitch = 0;

    // Ball possession model
    this.possessor = null;
    this.lastTouchTeam = 0;
    this.captureReadyAt = 0;

    // Tactical AI Profiles
    this.blueProfiles = null;
    this.redProfiles = null;

    // Out-of-bounds restart guard (throw-in / goal kick in progress)
    this.throwInActive = false;
  }

  preload() {
    this.load.image('pitch', '/assets/backgrounds/pitch.png');
    this.load.image('scoreboard', '/assets/ui/scoreboard.png');
    this.load.image('coach_portrait', '/assets/ui/coach_portrait.png');
    this.load.image('shout_input', '/assets/ui/shout_input.png');
    this.load.image('ball', '/assets/sprites/ball.png');

    // Load players sheets
    this.load.spritesheet('player_blue', '/assets/sprites/player_blue_team.png', {
      frameWidth: 352,
      frameHeight: 768
    });
    this.load.spritesheet('player_red', '/assets/sprites/player_red_team.png', {
      frameWidth: 352,
      frameHeight: 768
    });

    this.load.image('goalkeeper_blue', '/assets/sprites/goalkeeper_blue_team.png');
    this.load.image('goalkeeper_red', '/assets/sprites/goalkeeper_red_team.png');
    this.load.image('goalkeeper', '/assets/sprites/goalkeeper.png');
  }

  create() {
    const width = this.sys.game.config.width;
    const height = this.sys.game.config.height;

    // Reset team structures
    this.bluePlayers = [];
    this.redPlayers = [];
    this.activeBlueIdx = 0;
    this.activeRedIdx = 0;
    this.huddleTimers = [];

    // 1. Pitch background (grass, goals, flags, and crowd integrated)
    this.add.image(width / 2, height / 2, 'pitch');

    // Slice goalkeeper texture into animation frames
    this.createGoalkeeperFrames();

    // 3. Goals — geometry shared by the drawn nets, the collision walls and
    //    the goal-line scoring test in checkGoals().
    this.goalMouthTop = 262;
    this.goalMouthBottom = 498;
    this.leftGoalLine = 150;   // ball crosses this (moving left) to score
    this.leftGoalBack = 70;
    this.rightGoalLine = 1258;
    this.rightGoalBack = 1338;

    this.postsGroup = this.physics.add.staticGroup();
    this.buildGoalColliders(this.leftGoalBack, this.leftGoalLine);
    this.buildGoalColliders(this.rightGoalLine, this.rightGoalBack);
    this.drawGoals();
    this.createPlayerAnimations();

    // Outfield Roles Mapping: 0 = Midfielder, 1 & 2 = Defender, 3 = Forward
    const outfieldRoles = ['midfielder', 'defender', 'defender', 'forward'];

    // 4. Create Outfield Blue Team (5-a-side)
    const blueSpawnCoords = [
      { x: 450, y: 380 },
      { x: 320, y: 250 },
      { x: 320, y: 510 },
      { x: 560, y: 380 }
    ];
    blueSpawnCoords.forEach((coord, idx) => {
      const p = this.physics.add.sprite(coord.x, coord.y, 'player_blue', 0);
      p.setScale(0.07); // Outfield players consistent size
      p.setCollideWorldBounds(true);
      // Body aligned to the visible figure
      p.body.setSize(150, 430);
      p.body.setOffset(101, 200);

      // Initialize AI properties
      p.setData('team', 1);
      p.setData('idx', idx);
      p.setData('role', outfieldRoles[idx]);
      p.setData('tackle', { active: false, endAt: 0, cooldownUntil: 0 });
      p.setData('kickPending', false);
      p.setData('kickChargeStart', 0);
      p.setData('kickReleaseAt', 0);
      p.setData('kickPower', 0);
      p.setData('kickDx', 0);
      p.setData('kickDy', 0);
      p.setData('possessionStart', 0);

      this.physics.add.collider(p, this.postsGroup);
      this.bluePlayers.push(p);
    });

    // 5. Create Outfield Red Team
    const redSpawnCoords = [
      { x: 958, y: 380 },
      { x: 1088, y: 250 },
      { x: 1088, y: 510 },
      { x: 848, y: 380 }
    ];
    redSpawnCoords.forEach((coord, idx) => {
      const p = this.physics.add.sprite(coord.x, coord.y, 'player_red', 0);
      p.setScale(0.07);
      p.setFlipX(true);
      p.setCollideWorldBounds(true);
      p.body.setSize(150, 430);
      p.body.setOffset(101, 200);

      // Initialize AI properties
      p.setData('team', 2);
      p.setData('idx', idx);
      p.setData('role', outfieldRoles[idx]);
      p.setData('tackle', { active: false, endAt: 0, cooldownUntil: 0 });
      p.setData('kickPending', false);
      p.setData('kickChargeStart', 0);
      p.setData('kickReleaseAt', 0);
      p.setData('kickPower', 0);
      p.setData('kickDx', 0);
      p.setData('kickDy', 0);
      p.setData('possessionStart', 0);

      this.redPlayers.push(p);
    });

    // Colliders between players
    for (let i = 0; i < 4; i++) {
      for (let j = 0; j < 4; j++) {
        this.physics.add.collider(this.bluePlayers[i], this.redPlayers[j]);
      }
      for (let k = i + 1; k < 4; k++) {
        this.physics.add.collider(this.bluePlayers[i], this.bluePlayers[k]);
        this.physics.add.collider(this.redPlayers[i], this.redPlayers[k]);
      }
    }

    // 6. AI Goalkeeper 1 (Left Goal)
    this.gk1 = this.physics.add.sprite(180, 380, 'goalkeeper_blue', 'goalkeeper_blue_ready_0');
    this.gk1.setScale(0.22);
    this.gk1.setCollideWorldBounds(true);
    this.gk1.body.setImmovable(true);
    this.gk1.play('gk_blue_ready');
    this.gk1.body.setSize(120, 160);
    this.gk1.body.setOffset(40, 20);
    this.gk1.setData('team', 1);
    this.gk1.setData('role', 'goalkeeper');

    // 7. AI Goalkeeper 2 (Right Goal)
    this.gk2 = this.physics.add.sprite(1228, 380, 'goalkeeper_red', 'goalkeeper_red_ready_0');
    this.gk2.setScale(0.22);
    this.gk2.setFlipX(true);
    this.gk2.setCollideWorldBounds(true);
    this.gk2.body.setImmovable(true);
    this.gk2.play('gk_red_ready');
    this.gk2.body.setSize(120, 160);
    this.gk2.body.setOffset(40, 20);
    this.gk2.setData('team', 2);
    this.gk2.setData('role', 'goalkeeper');

    // Initialize player floating text labels
    const defaults = window.currentProfiles || {
      defender: { speed: 210, trackingSpeed: 0.08 },
      midfielder: { speed: 235 },
      forward: { speed: 260 },
      goalkeeper: { trackingSpeed: 0.09 }
    };

    this.bluePlayers.forEach((p) => {
      const role = p.getData('role');
      const profile = defaults[role] || {};
      const label = this.add.text(p.x, p.y - 45, `${role.toUpperCase()}\nSpd: ${Math.round(this.getPlayerSpeed(profile, role))}`, {
        fontFamily: '"Outfit", "Inter", Arial, sans-serif',
        fontSize: '13px',
        color: '#ffffff',
        align: 'center',
        stroke: '#000000',
        strokeThickness: 3
      }).setOrigin(0.5);
      p.setData('label', label);

      // Setup player shout bubble objects
      p.setData('shoutGraphics', this.add.graphics().setAlpha(0));
      p.setData('shoutText', this.add.text(p.x, p.y - 75, '', {
        fontFamily: 'monospace',
        fontSize: '12px',
        color: '#000000',
        fontStyle: 'bold',
        align: 'center'
      }).setOrigin(0.5).setAlpha(0));
    });

    const gkProfile = defaults.goalkeeper || {};
    const gkLabel = this.add.text(this.gk1.x, this.gk1.y - 45, `GK\nTrk: ${parseFloat(gkProfile.trackingSpeed || 0.08).toFixed(2)}`, {
      fontFamily: '"Outfit", "Inter", Arial, sans-serif',
      fontSize: '13px',
      color: '#ffffff',
      align: 'center',
      stroke: '#000000',
      strokeThickness: 3
    }).setOrigin(0.5);
    this.gk1.setData('label', gkLabel);

    // Setup gk1 shout bubble objects
    this.gk1.setData('shoutGraphics', this.add.graphics().setAlpha(0));
    this.gk1.setData('shoutText', this.add.text(this.gk1.x, this.gk1.y - 75, '', {
      fontFamily: 'monospace',
      fontSize: '12px',
      color: '#000000',
      fontStyle: 'bold',
      align: 'center'
    }).setOrigin(0.5).setAlpha(0));

    this.redPlayers.forEach((p) => {
      const role = p.getData('role');
      const label = this.add.text(p.x, p.y - 45, `RED ${role.toUpperCase()}`, {
        fontFamily: '"Outfit", "Inter", Arial, sans-serif',
        fontSize: '13px',
        color: '#f87171',
        align: 'center',
        stroke: '#000000',
        strokeThickness: 3
      }).setOrigin(0.5);
      p.setData('label', label);
    });

    const gk2Label = this.add.text(this.gk2.x, this.gk2.y - 45, `RED GK`, {
      fontFamily: '"Outfit", "Inter", Arial, sans-serif',
      fontSize: '13px',
      color: '#f87171',
      align: 'center',
      stroke: '#000000',
      strokeThickness: 3
    }).setOrigin(0.5);
    this.gk2.setData('label', gk2Label);

    // 8. Ball Shadow
    this.ballShadow = this.add.circle(width / 2, 380, 5, 0x000000, 0.35);

    // 9. Ball Sprite
    this.ball = this.physics.add.sprite(width / 2, 380, 'ball');
    this.ball.setScale(0.042);
    this.ball.setCollideWorldBounds(false);
    this.ball.setDamping(true);
    this.ball.setDrag(0.55);
    this.ball.setBounce(0.78);
    this.ball.body.setCircle(170, 532, 214);

    // Bounds — match the visible playfield (kept clear of the crowd strips)
    this.physics.world.setBounds(0, 150, width, 540);

    this.physics.add.collider(this.ball, this.postsGroup, () => {
      if (this.ballZ < 40) Sound.playBounce();
    });

    // GK block
    this.physics.add.collider(this.ball, this.gk1, this.handleGkBlock, null, this);
    this.physics.add.collider(this.ball, this.gk2, this.handleGkBlock, null, this);

    // Control Indicators
    this.blueIndicator = this.add.triangle(0, 0, 0, 0, 8, 0, 4, 6, 0x60a5fa).setOrigin(0.5);
    this.redIndicator = this.add.triangle(0, 0, 0, 0, 8, 0, 4, 6, 0xf87171).setOrigin(0.5);

    // Power meter for charged kicks
    this.powerBarGfx = this.add.graphics();

    // 11. Scoreboard (Vector-based modern layout)
    const sbWidth = 420;
    const sbHeight = 64;
    const sbX = width / 2 - sbWidth / 2;
    const sbY = 30;

    const sbGfx = this.add.graphics();
    // Glassmorphism background
    sbGfx.fillStyle(0x0f172a, 0.85);
    sbGfx.fillRoundedRect(sbX, sbY, sbWidth, sbHeight, 10);
    // Sleek border
    sbGfx.lineStyle(1.5, 0x334155, 0.8);
    sbGfx.strokeRoundedRect(sbX, sbY, sbWidth, sbHeight, 10);

    // Section dividers
    sbGfx.lineStyle(1, 0x475569, 0.4);
    // Left divider
    sbGfx.beginPath();
    sbGfx.moveTo(width / 2 - 90, sbY + 10);
    sbGfx.lineTo(width / 2 - 90, sbY + sbHeight - 10);
    sbGfx.strokePath();

    // Right divider
    sbGfx.beginPath();
    sbGfx.moveTo(width / 2 + 90, sbY + 10);
    sbGfx.lineTo(width / 2 + 90, sbY + sbHeight - 10);
    sbGfx.strokePath();

    // Blue Team Name
    this.add.text(width / 2 - 145, sbY + sbHeight / 2, 'BLUE', {
      fontFamily: '"Outfit", "Inter", Arial, sans-serif',
      fontSize: '15px',
      color: '#60a5fa', // Premium light blue
      fontStyle: 'bold'
    }).setOrigin(0.5);

    // Blue Score
    this.scoreText1 = this.add.text(width / 2 - 110, sbY + sbHeight / 2, '0', {
      fontFamily: '"Outfit", "Inter", Arial, sans-serif',
      fontSize: '26px',
      color: '#ffffff',
      fontStyle: 'bold'
    }).setOrigin(0.5);

    // Red Score
    this.scoreText2 = this.add.text(width / 2 + 110, sbY + sbHeight / 2, '0', {
      fontFamily: '"Outfit", "Inter", Arial, sans-serif',
      fontSize: '26px',
      color: '#ffffff',
      fontStyle: 'bold'
    }).setOrigin(0.5);

    // Red Team Name
    this.add.text(width / 2 + 145, sbY + sbHeight / 2, 'RED', {
      fontFamily: '"Outfit", "Inter", Arial, sans-serif',
      fontSize: '15px',
      color: '#f87171', // Premium light red
      fontStyle: 'bold'
    }).setOrigin(0.5);

    // Timer
    const initialMins = Math.floor(GAME_DURATION_SEC / 60);
    const initialSecs = GAME_DURATION_SEC % 60;
    const initialTimeStr = `${initialMins.toString().padStart(2, '0')}:${initialSecs.toString().padStart(2, '0')}`;
    this.timeText = this.add.text(width / 2, sbY + sbHeight / 2, initialTimeStr, {
      fontFamily: '"Outfit", "Inter", Arial, sans-serif',
      fontSize: '22px',
      color: '#ffcc00',
      fontStyle: 'bold'
    }).setOrigin(0.5);

    this.setupCoaches();

    this.startTimer();
    this.gameActive = true;
    Sound.playWhistle();
  }

  update(time, delta) {
    if (!this.gameActive) return;

    // Fallback/Ensure profiles are loaded
    if (!this.blueProfiles || !this.redProfiles) {
      const hardcodedDefaults = {
        defender: {
          speed: 150, tackleRadius: 65, tackleCooldown: 800, passProbability: 0.75, passRange: 420, shotPower: 0.6, shotRange: 300, aggression: 0.4, defensePositioning: 0.8, attackPositioning: 0.3, decisionDelay: 150,
          pressingIntensity: 0.3, formationDiscipline: 0.9, passRiskTolerance: 0.2, dribbleTendency: 0.1, recoverySpeedMultiplier: 1.2, supportRunFrequency: 0.2, widthPreference: 0.3, interceptionRadius: 80, foulProbability: 0.3, counterAttackUrgency: 0.2
        },
        midfielder: {
          speed: 180, tackleRadius: 50, tackleCooldown: 1000, passProbability: 0.85, passRange: 500, shotPower: 0.75, shotRange: 500, aggression: 0.75, defensePositioning: 0.5, attackPositioning: 0.6, decisionDelay: 100,
          pressingIntensity: 0.6, formationDiscipline: 0.6, passRiskTolerance: 0.6, dribbleTendency: 0.5, recoverySpeedMultiplier: 1.1, supportRunFrequency: 0.7, widthPreference: 0.5, interceptionRadius: 60, foulProbability: 0.2, counterAttackUrgency: 0.6
        },
        forward: {
          speed: 200, tackleRadius: 40, tackleCooldown: 1200, passProbability: 0.3, passRange: 380, shotPower: 0.95, shotRange: 700, aggression: 0.9, defensePositioning: 0.2, attackPositioning: 0.9, decisionDelay: 50,
          pressingIntensity: 0.8, formationDiscipline: 0.3, passRiskTolerance: 0.4, dribbleTendency: 0.85, recoverySpeedMultiplier: 1.0, supportRunFrequency: 0.9, widthPreference: 0.8, interceptionRadius: 30, foulProbability: 0.1, counterAttackUrgency: 0.9
        },
        goalkeeper: {
          speed: 180, tackleRadius: 30, tackleCooldown: 1500, passProbability: 0.9, passRange: 600, shotPower: 0.5, shotRange: 200, aggression: 0.1, defensePositioning: 1.0, attackPositioning: 0.0, decisionDelay: 200, diveChance: 0.08, trackingSpeed: 0.05,
          pressingIntensity: 0.0, formationDiscipline: 1.0, passRiskTolerance: 0.1, dribbleTendency: 0.0, recoverySpeedMultiplier: 1.0, supportRunFrequency: 0.0, widthPreference: 0.5, interceptionRadius: 20, foulProbability: 0.05, counterAttackUrgency: 0.4
        }
      };
      if (!this.blueProfiles) {
        this.blueProfiles = window.currentProfiles
          ? JSON.parse(JSON.stringify(window.currentProfiles))
          : JSON.parse(JSON.stringify(hardcodedDefaults));
      }
      if (!this.redProfiles) {
        this.redProfiles = JSON.parse(JSON.stringify(hardcodedDefaults));
      }
    }

    this.updateBallHeight(delta);
    this.updatePossession(time);
    this.handleAutoPlayerSwitching(time);

    // Position indicator triangles above active focus targets
    const activeP1 = this.bluePlayers[this.activeBlueIdx];
    const activeP2 = this.redPlayers[this.activeRedIdx];
    this.blueIndicator.setPosition(activeP1.x, activeP1.y - 38);
    this.redIndicator.setPosition(activeP2.x, activeP2.y - 38);

    this.updateAllPlayersAI(time, delta);
    this.updateTackles(time);
    this.updatePowerMeter(time);

    // Run Goalkeeper updates
    this.updateGkAI(this.gk1, 1, this.blueProfiles.goalkeeper, time, delta);
    this.updateGkAI(this.gk2, 2, this.redProfiles.goalkeeper, time, delta);

    // Update player text labels positions and contents
    this.bluePlayers.forEach(p => {
      const label = p.getData('label');
      if (label) {
        const role = p.getData('role');
        const profile = this.blueProfiles[role];
        label.setText(`${role.toUpperCase()}\nSpd: ${Math.round(this.getPlayerSpeed(profile, role))}`);
        label.setPosition(p.x, p.y - 45);
      }

      // Update shout bubble position if active
      const shoutG = p.getData('shoutGraphics');
      const shoutT = p.getData('shoutText');
      if (shoutG && shoutG.alpha > 0) {
        shoutG.setPosition(p.x, p.y - 75);
        shoutT.setPosition(p.x, p.y - 75);
      }
    });

    const gk1Label = this.gk1.getData('label');
    if (gk1Label) {
      const profile = this.blueProfiles.goalkeeper;
      const trackingSpeed = (profile && profile.trackingSpeed !== undefined) ? profile.trackingSpeed : 0.05;
      gk1Label.setText(`GK\nTrk: ${trackingSpeed.toFixed(2)}`);
      gk1Label.setPosition(this.gk1.x, this.gk1.y - 45);
    }

    // Update gk1 shout bubble position if active
    const gkShoutG = this.gk1.getData('shoutGraphics');
    const gkShoutT = this.gk1.getData('shoutText');
    if (gkShoutG && gkShoutG.alpha > 0) {
      gkShoutG.setPosition(this.gk1.x, this.gk1.y - 75);
      gkShoutT.setPosition(this.gk1.x, this.gk1.y - 75);
    }

    this.redPlayers.forEach(p => {
      const label = p.getData('label');
      if (label) {
        label.setPosition(p.x, p.y - 45);
      }
    });

    const gk2Label = this.gk2.getData('label');
    if (gk2Label) {
      gk2Label.setPosition(this.gk2.x, this.gk2.y - 45);
    }

    this.checkGoals();
    this.checkOutOfBounds(time);
  }

  updateBallHeight(delta) {
    // The ball is purely top-down: it always rolls on the ground, never lofts.
    // ballZ is pinned to 0 so the goal/capture checks that still reference it
    // behave (a grounded ball is always scoreable and capturable).
    this.ballZ = 0;
    this.ballZVelocity = 0;

    // Rolling friction: a loose ball decelerates and settles. The possessor
    // dribbles by setting velocity directly, so keep its drag light to avoid
    // fighting the glue. (Damping is enabled — drag is a per-second retention
    // coefficient, lower = more friction.)
    this.ball.body.drag.set(this.possessor ? 0.9 : 0.55);

    // Visual roll — spin the sprite in the travel direction, scaled by speed.
    const v = this.ball.body.velocity;
    const speed = Math.hypot(v.x, v.y);
    const dir = Math.abs(v.x) > 1 ? Math.sign(v.x) : (Math.abs(v.y) > 1 ? Math.sign(v.y) : 0);
    this.ball.rotation += dir * speed * 0.00009 * (delta || 16);

    // Draw the ball on the ground with its shadow directly beneath it.
    this.ball.y = this.ball.body.y + this.ball.body.halfHeight;
    this.ballShadow.x = this.ball.x;
    this.ballShadow.y = this.ball.body.y + this.ball.body.halfHeight;
    this.ballShadow.setScale(1);
    this.ballShadow.setAlpha(0.35);
  }

  handleAutoPlayerSwitching(time) {
    // When a team has the ball, control is pinned to the dribbler by
    // updatePossession() — don't let the chase heuristic override it.
    const possTeam = this.possessor ? this.possessor.getData('team') : 0;
    if (possTeam !== 1) {
      this.activeBlueIdx = this.resolveActivePlayer(
        this.bluePlayers, this.activeBlueIdx, 'blue', time
      );
    }
    if (possTeam !== 2) {
      this.activeRedIdx = this.resolveActivePlayer(
        this.redPlayers, this.activeRedIdx, 'red', time
      );
    }
  }

  // Reassign control to the teammate nearest the ball, but only when that
  // candidate is meaningfully closer than the current player AND a short
  // cooldown has elapsed. Without this hysteresis the active index oscillates
  // between near-equidistant players every frame, making the keys appear to
  // drive several players at once.
  resolveActivePlayer(team, activeIdx, key, time) {
    const SWITCH_MARGIN = 55;   // px the candidate must beat the current by
    const SWITCH_COOLDOWN = 280; // ms minimum between switches

    let closestIdx = activeIdx;
    let closestDist = Infinity;
    team.forEach((p, idx) => {
      const d = Phaser.Math.Distance.Between(p.x, p.y, this.ball.x, this.ball.y);
      if (d < closestDist) {
        closestDist = d;
        closestIdx = idx;
      }
    });

    if (closestIdx === activeIdx) return activeIdx;

    const lastSwitch = key === 'blue' ? this.lastBlueSwitch : this.lastRedSwitch;
    const activeDist = Phaser.Math.Distance.Between(
      team[activeIdx].x, team[activeIdx].y, this.ball.x, this.ball.y
    );

    if (closestDist + SWITCH_MARGIN < activeDist && time - lastSwitch > SWITCH_COOLDOWN) {
      team[activeIdx].setVelocity(0, 0); // hand the old player back to the AI cleanly
      if (key === 'blue') this.lastBlueSwitch = time; else this.lastRedSwitch = time;
      return closestIdx;
    }

    return activeIdx;
  }

  // Sticky-dribbling possession. A loose ball on the ground is captured by the
  // nearest outfield player; while possessed it glues to the dribbler's feet
  // and control snaps to that player. Replaces the old bounce collider.
  updatePossession(time) {
    const CAPTURE_RADIUS = 46;
    const FOOT_OFFSET = 26;

    if (this.possessor) {
      const p = this.possessor;
      // Possession ends if the carrier is disabled or the ball was kicked away.
      if (!p.body || !p.body.enable) {
        this.possessor = null;
      } else {
        const dir = p.flipX ? -1 : 1;
        // Glue the ball just ahead of the dribbler's feet. body.reset() teleports
        // it cleanly (zeroing the postUpdate delta) so it tracks without jitter;
        // the follow-up velocity gives it momentum for a natural roll/release.
        this.ball.body.reset(p.x + dir * FOOT_OFFSET, p.y + 8);
        this.ball.setVelocity(p.body.velocity.x + dir * 30, p.body.velocity.y);
        this.ballZ = 0;
        this.ballZVelocity = 0;
        this.lastTouchTeam = p.getData('team');

        // Pin control to the carrier so the human drives whoever has the ball.
        if (p.getData('team') === 1) this.activeBlueIdx = p.getData('idx');
        else this.activeRedIdx = p.getData('idx');
        return;
      }
    }

    // No possessor: try to capture a slow, grounded ball with the nearest
    // outfield player (skipped briefly after a kick, and during restarts).
    if (this.throwInActive || time < this.captureReadyAt || this.ballZ > 45) return;

    let best = null;
    let bestDist = CAPTURE_RADIUS;
    [...this.bluePlayers, ...this.redPlayers].forEach(p => {
      if (!p.body || !p.body.enable) return;
      const d = Phaser.Math.Distance.Between(p.x, p.y, this.ball.x, this.ball.y);
      if (d < bestDist) {
        bestDist = d;
        best = p;
      }
    });

    if (best) {
      this.possessor = best;
      this.lastTouchTeam = best.getData('team');
    }
  }

  updateBlueProfiles(profiles) {
    this.blueProfiles = JSON.parse(JSON.stringify(profiles));
  }

  setSimulationSpeed(speed) {
    this.simSpeed = speed;
    this.physics.world.timeScale = 1 / speed;
    if (this.timerEvent) {
      this.timerEvent.timeScale = speed;
    }
    this.time.timeScale = speed;
    this.anims.globalTimeScale = speed;
  }

  getPlayerSpeed(profile, role) {
    const baseSpeeds = { defender: 210, midfielder: 235, forward: 260, goalkeeper: 180 };
    const base = baseSpeeds[role] || 240;
    if (!profile || profile.speed === undefined) return base;
    return profile.speed <= 2.0 ? base * profile.speed : profile.speed;
  }

  // Resolve a distance-style attribute that may arrive either as a normalized
  // 0-1 fraction (the agent-written JSON profiles) or as an absolute pixel value
  // (the hardcoded defaults / red team). Values <= 2.0 are treated as a fraction
  // of `max`; anything larger is taken as raw pixels. Mirrors getPlayerSpeed.
  resolveDistance(value, max, fallback) {
    if (value === undefined) return fallback;
    return value <= 2.0 ? max * value : value;
  }

  updateAllPlayersAI(time, delta) {
    const ballX = this.ball.x;
    const ballY = this.ball.y;

    const runTeamAI = (players, teamNum, profiles) => {
      const isBlue = teamNum === 1;
      const directionFactor = isBlue ? 1 : -1;
      const opponentGoalLine = isBlue ? this.rightGoalLine : this.leftGoalLine;

      // Find closest player to the ball
      let closestPlayer = null;
      let closestDist = Infinity;
      players.forEach(p => {
        const d = Phaser.Math.Distance.Between(p.x, p.y, ballX, ballY);
        if (d < closestDist) {
          closestDist = d;
          closestPlayer = p;
        }
      });

      players.forEach(p => {
        const role = p.getData('role');
        const profile = profiles[role] || {};
        const baseSpeed = this.getPlayerSpeed(profile, role);
        const tState = p.getData('tackle');

        // 1. Skip movement if slide tackling
        if (tState && tState.active) return;

        // 2. Handle pending kick charge (slow down to kick)
        if (p.getData('kickPending')) {
          p.setVelocity(p.body.velocity.x * 0.1, p.body.velocity.y * 0.1);
          if (time >= p.getData('kickReleaseAt')) {
            p.setData('kickPending', false);
            p.play(isBlue ? 'blue_kick' : 'red_kick', true);
            this.releaseKick(
              p,
              teamNum,
              p.getData('kickDx'),
              p.getData('kickDy'),
              p.getData('kickPower')
            );
          }
          return;
        }

        // Default spawn point coordinates
        const spawns = isBlue
          ? [{ x: 450, y: 380 }, { x: 320, y: 250 }, { x: 320, y: 510 }, { x: 560, y: 380 }]
          : [{ x: 958, y: 380 }, { x: 1088, y: 250 }, { x: 1088, y: 510 }, { x: 848, y: 380 }];
        const defaultX = spawns[p.getData('idx')].x;
        const defaultY = spawns[p.getData('idx')].y;

        // State A: Has Ball (Possession)
        if (this.possessor === p) {
          if (p.getData('possessionStart') === 0) {
            p.setData('possessionStart', time);
          }

          // Move towards opponent goal
          let targetY = ballY;
          // If wide, cut inside towards the center of the pitch (380) to avoid getting stuck in corners
          if (ballY < 280) {
            targetY = ballY + (280 - ballY) * 0.45;
          } else if (ballY > 480) {
            targetY = ballY - (ballY - 480) * 0.45;
          }
          targetY = Phaser.Math.Clamp(targetY, 200, 568);

          const urgency = profile.counterAttackUrgency !== undefined ? profile.counterAttackUrgency : 0.5;
          const runSpeed = baseSpeed * (1.0 + urgency * 0.15);
          this.movePlayerTowards(p, opponentGoalLine, targetY, runSpeed, teamNum);

          // Decision check after delay
          if (time - p.getData('possessionStart') >= (profile.decisionDelay || 100)) {
            const distToGoal = Math.abs(opponentGoalLine - p.x);

            const teammates = isBlue ? this.bluePlayers : this.redPlayers;
            const opponents = isBlue ? this.redPlayers : this.bluePlayers;

            // Check if player is pressed by an opponent
            let closestOpponentDist = Infinity;
            opponents.forEach(opp => {
              const d = Phaser.Math.Distance.Between(p.x, p.y, opp.x, opp.y);
              if (d < closestOpponentDist) closestOpponentDist = d;
            });
            const isPressed = closestOpponentDist < 120;
            const isInOwnHalf = (isBlue && p.x < 700) || (!isBlue && p.x > 700);

            // Dribble tendency check: reduce dribble probability if pressed or in own half
            const dribbleTend = profile.dribbleTendency !== undefined ? profile.dribbleTendency : 0.5;
            let adjustedDribbleTend = dribbleTend;
            if (isPressed) adjustedDribbleTend *= 0.35;
            if (isInOwnHalf) adjustedDribbleTend *= 0.45;

            const preferDribble = Math.random() < adjustedDribbleTend;

            // 1. Shoot check
            if (distToGoal < this.resolveDistance(profile.shotRange, 700, 500) && ((isBlue && p.x > 700) || (!isBlue && p.x < 700))) {
              if (distToGoal < 220 || !preferDribble) {
                if (Math.random() > (profile.passProbability || 0.5)) {
                  // Find opposing goalkeeper
                  const oppGk = isBlue ? this.gk2 : this.gk1;
                  // Aim at the corner furthest from the keeper
                  const shotY = oppGk.y < 380
                    ? 460 + Math.random() * 25
                    : 300 - Math.random() * 25;

                  p.setData('kickPending', true);
                  p.setData('kickChargeStart', time);
                  p.setData('kickReleaseAt', time + 200);
                  p.setData('kickPower', profile.shotPower || 0.85);
                  p.setData('kickDx', opponentGoalLine - p.x);
                  p.setData('kickDy', shotY - p.y);
                  return;
                }
              }
            }

            // 2. Pass check
            if (!preferDribble) {
              const passRange = this.resolveDistance(profile.passRange, 600, 450);
              let passTarget = this.findTeammateInDirection(p, teammates, directionFactor, 0);

              // Validate if lane is clear of defenders
              if (passTarget && !this.isPassingLaneClear(p.x, p.y, passTarget.x, passTarget.y, opponents, profile.passRiskTolerance || 0.5)) {
                passTarget = null;
              }

              // Fallback to finding an open teammate with a clear passing lane
              if (!passTarget) {
                passTarget = this.findOpenTeammate(p, teammates, passRange);
              }

              if (passTarget) {
                p.setData('kickPending', true);
                p.setData('kickChargeStart', time);
                p.setData('kickReleaseAt', time + 200);
                const distToMate = Phaser.Math.Distance.Between(p.x, p.y, passTarget.x, passTarget.y);
                const passPower = Phaser.Math.Clamp(distToMate / passRange, 0.45, 0.9);
                p.setData('kickPower', passPower);
                p.setData('kickDx', passTarget.x - p.x);
                p.setData('kickDy', passTarget.y - p.y);
                return;
              }
            }
          }
          return;
        }

        // Reset possession timer if player does not have the ball
        p.setData('possessionStart', 0);

        // State B: Teammate Has Ball (Off-ball Attacking Support)
        const teamHasPossession = this.possessor && this.possessor.getData('team') === teamNum;
        if (teamHasPossession) {
          let attackWeight = profile.attackPositioning || 0.5;

          // Support run frequency
          const supportFreq = profile.supportRunFrequency !== undefined ? profile.supportRunFrequency : 0.5;
          if (Math.random() < supportFreq * 0.25) {
            attackWeight = Math.min(1.0, attackWeight + 0.25);
          }

          const targetX = defaultX + (ballX - defaultX) * attackWeight + (role === 'forward' ? 140 * directionFactor : 0);

          // Width Preference Y target
          const widthPref = profile.widthPreference !== undefined ? profile.widthPreference : 0.5;
          let targetY = defaultY + (ballY - defaultY) * 0.4;
          const pitchCenterY = 380;
          const isUpperHalf = defaultY < pitchCenterY;
          if (widthPref > 0.5) {
            const marginY = isUpperHalf ? 178 + 30 : 662 - 30;
            const blend = (widthPref - 0.5) * 2;
            targetY = targetY * (1 - blend) + marginY * blend;
          } else {
            const blend = (0.5 - widthPref) * 2;
            targetY = targetY * (1 - blend) + pitchCenterY * blend;
          }

          const counterUrgency = profile.counterAttackUrgency !== undefined ? profile.counterAttackUrgency : 0.5;
          const supportSpeed = baseSpeed * (1.0 + counterUrgency * 0.15);
          this.movePlayerTowards(p, targetX, targetY, supportSpeed, teamNum);
          return;
        }

        // State C: Opponent Has Ball or Ball is Loose (Defending/Chasing)
        const isClosestToBall = closestPlayer === p;
        const opponentHasBall = this.possessor && this.possessor.getData('team') !== teamNum;
        const distToBall = Phaser.Math.Distance.Between(p.x, p.y, ballX, ballY);

        // Pressing Intensity and Aggression
        const pressing = profile.pressingIntensity !== undefined ? profile.pressingIntensity : 0.5;
        const pressDistance = 80 + pressing * 120;

        const isMidOrForward = role === 'midfielder' || role === 'forward';
        const shouldChase = isClosestToBall || (isMidOrForward && opponentHasBall && distToBall < pressDistance && Math.random() < ((profile.aggression || 0.5) * 0.7 + pressing * 0.3));

        if (shouldChase && !this.throwInActive) {
          // Chase ball
          const pressSpeedFactor = 1.0 + pressing * 0.15;
          this.movePlayerTowards(p, ballX, ballY, baseSpeed * pressSpeedFactor, teamNum);

          // Slide tackle trigger
          if (opponentHasBall && distToBall < this.resolveDistance(profile.tackleRadius, 90, 50)) {
            const opp = this.possessor;
            const dx = opp.x - p.x;
            const dy = opp.y - p.y;
            this.aiStartTackle(p, teamNum, dx, dy, time, profile);
          }
        } else {
          // Position defensively
          const defenseWeight = profile.defensePositioning || 0.6;
          const targetX = defaultX + (ballX - defaultX) * (1 - defenseWeight);

          // Width Preference
          const widthPref = profile.widthPreference !== undefined ? profile.widthPreference : 0.5;
          let targetY = defaultY + (ballY - defaultY) * 0.3;
          const pitchCenterY = 380;
          const isUpperHalf = defaultY < pitchCenterY;
          if (widthPref > 0.5) {
            const marginY = isUpperHalf ? 178 + 30 : 662 - 30;
            const blend = (widthPref - 0.5) * 2;
            targetY = targetY * (1 - blend) + marginY * blend;
          } else {
            const blend = (0.5 - widthPref) * 2;
            targetY = targetY * (1 - blend) + pitchCenterY * blend;
          }

          // Formation Discipline
          const discipline = profile.formationDiscipline !== undefined ? profile.formationDiscipline : 0.8;
          const finalX = targetX * discipline + ballX * (1 - discipline);
          const finalY = targetY * discipline + ballY * (1 - discipline);

          // Recovery Speed Multiplier
          const recMult = profile.recoverySpeedMultiplier !== undefined ? profile.recoverySpeedMultiplier : 1.0;
          const recoverySpeed = baseSpeed * 0.9 * (0.8 + recMult * 0.2);

          this.movePlayerTowards(p, finalX, finalY, recoverySpeed, teamNum);
        }
      });
    };

    runTeamAI(this.bluePlayers, 1, this.blueProfiles);
    runTeamAI(this.redPlayers, 2, this.redProfiles);
  }

  isPassingLaneClear(fromX, fromY, toX, toY, opponents, passRiskTolerance = 0.5) {
    const toleranceFactor = 1.2 - passRiskTolerance * 0.6; // scales target margin from 1.2x down to 0.6x

    const getDistanceToSegment = (x0, y0, x1, y1, x2, y2) => {
      const dx = x2 - x1;
      const dy = y2 - y1;
      const lenSq = dx * dx + dy * dy;
      if (lenSq === 0) return Math.hypot(x0 - x1, y0 - y1);

      let t = ((x0 - x1) * dx + (y0 - y1) * dy) / lenSq;
      t = Math.max(0, Math.min(1, t));

      const projX = x1 + t * dx;
      const projY = y1 + t * dy;
      return Math.hypot(x0 - projX, y0 - projY);
    };

    for (let i = 0; i < opponents.length; i++) {
      const opp = opponents[i];
      if (!opp.body || !opp.body.enable) continue;

      const oppRole = opp.getData('role');
      const oppProfiles = opp.getData('team') === 1 ? this.blueProfiles : this.redProfiles;
      const oppProfile = oppProfiles ? oppProfiles[oppRole] : {};
      const oppRadius = this.resolveDistance(oppProfile.interceptionRadius, 100, 60);

      const dist = getDistanceToSegment(opp.x, opp.y, fromX, fromY, toX, toY);
      if (dist < oppRadius * toleranceFactor) {
        return false;
      }
    }
    return true;
  }

  findOpenTeammate(player, teammates, passRange) {
    let bestTarget = null;
    let bestScore = -Infinity;
    const opponents = player.getData('team') === 1 ? this.redPlayers : this.bluePlayers;

    const teamNum = player.getData('team');
    const role = player.getData('role');
    const profiles = teamNum === 1 ? this.blueProfiles : this.redProfiles;
    const profile = profiles ? (profiles[role] || {}) : {};
    const passRiskTolerance = profile.passRiskTolerance !== undefined ? profile.passRiskTolerance : 0.5;

    teammates.forEach(mate => {
      if (mate === player) return;
      const dist = Phaser.Math.Distance.Between(player.x, player.y, mate.x, mate.y);
      if (dist > passRange) return;

      // Check if the passing lane is blocked by any opponent
      if (!this.isPassingLaneClear(player.x, player.y, mate.x, mate.y, opponents, passRiskTolerance)) return;

      let closestOppDist = Infinity;
      opponents.forEach(opp => {
        const d = Phaser.Math.Distance.Between(mate.x, mate.y, opp.x, opp.y);
        if (d < closestOppDist) closestOppDist = d;
      });

      const directionFactor = teamNum === 1 ? 1 : -1;
      const progressX = (mate.x - player.x) * directionFactor;

      // A pass target is valid if they are relatively open.
      if (closestOppDist > 55) {
        // Score: favor open players, and apply a small bonus if they are forward of the passer
        const score = closestOppDist + progressX * 0.25;
        if (score > bestScore) {
          bestScore = score;
          bestTarget = mate;
        }
      }
    });

    return bestTarget;
  }

  movePlayerTowards(player, x, y, speed, teamNum) {
    const dx = x - player.x;
    const dy = y - player.y;
    const dist = Math.sqrt(dx * dx + dy * dy);

    if (dist > 15) {
      const vx = (dx / dist) * speed;
      const vy = (dy / dist) * speed;
      player.setVelocity(vx, vy);
      player.setFlipX(vx < 0);
      player.play(teamNum === 1 ? 'blue_run' : 'red_run', true);
    } else {
      player.setVelocity(0, 0);
      player.play(teamNum === 1 ? 'blue_idle' : 'red_idle', true);
    }
  }

  aiStartTackle(player, teamNum, dx, dy, time, profile) {
    const tState = player.getData('tackle');
    if (tState.active || time < tState.cooldownUntil) return;

    let lx = dx;
    let ly = dy;
    if (lx === 0 && ly === 0) lx = player.flipX ? -1 : 1;
    const len = Math.hypot(lx, ly) || 1;
    const LUNGE = 420;
    player.setVelocity((lx / len) * LUNGE, (ly / len) * LUNGE);

    player.setRotation((player.flipX ? -1 : 1) * 1.2);
    player.setTint(0xffe08a);
    player.play(teamNum === 1 ? 'blue_run' : 'red_run', true);

    tState.active = true;
    tState.endAt = time + 320;

    if (this.possessor === player) {
      this.possessor = null;
      this.captureReadyAt = time + 200;
    }

    player.setData('kickPending', false);
    Sound.playJump();
  }

  updateTackles(time) {
    const allPlayers = [
      ...this.bluePlayers.map(p => ({ sprite: p, teamNum: 1, opps: this.redPlayers })),
      ...this.redPlayers.map(p => ({ sprite: p, teamNum: 2, opps: this.bluePlayers }))
    ];

    allPlayers.forEach(({ sprite, teamNum, opps }) => {
      const tState = sprite.getData('tackle');
      if (!tState || !tState.active) return;

      const dBall = Phaser.Math.Distance.Between(sprite.x, sprite.y, this.ball.x, this.ball.y);
      if (dBall < 52 && time >= this.captureReadyAt) {
        this.possessor = sprite;
        this.lastTouchTeam = teamNum;
      }

      opps.forEach(opp => {
        if (!opp.body || !opp.body.enable) return;
        const d = Phaser.Math.Distance.Between(sprite.x, sprite.y, opp.x, opp.y);
        if (d < 56) {
          const ang = Phaser.Math.Angle.Between(sprite.x, sprite.y, opp.x, opp.y);
          opp.setVelocity(Math.cos(ang) * 260, Math.sin(ang) * 260);
          if (this.possessor === opp) {
            this.possessor = null;
            this.captureReadyAt = time + 150;
          }

          // Foul Probability Check
          const role = sprite.getData('role');
          const profiles = teamNum === 1 ? this.blueProfiles : this.redProfiles;
          const profile = profiles ? (profiles[role] || {}) : {};
          const foulProb = profile.foulProbability !== undefined ? profile.foulProbability : 0.2;
          if (Math.random() < foulProb * 0.15) {
            Sound.playWhistle();
            opp.setData('kickPending', false);
            opp.setVelocity(0, 0);
            opp.setTint(0xff5555);
            this.time.delayedCall(800, () => opp.clearTint());
          }
        }
      });

      if (time >= tState.endAt) {
        tState.active = false;
        const role = sprite.getData('role');
        const profiles = teamNum === 1 ? this.blueProfiles : this.redProfiles;
        const profile = profiles ? (profiles[role] || {}) : {};
        const cooldown = profile.tackleCooldown !== undefined ? profile.tackleCooldown : 1000;
        const recMult = profile.recoverySpeedMultiplier !== undefined ? profile.recoverySpeedMultiplier : 1.0;
        tState.cooldownUntil = time + (cooldown / recMult);
        sprite.setRotation(0);
        sprite.clearTint();
        sprite.setVelocity(0, 0);
      }
    });
  }

  updatePowerMeter(time) {
    const g = this.powerBarGfx;
    g.clear();

    const drawBar = (player, color) => {
      if (!player.getData('kickPending')) return;
      const startTime = player.getData('kickChargeStart');
      const power = this.chargePower(time - startTime);
      const w = 44;
      const h = 6;
      const x = player.x - w / 2;
      const y = player.y - 52;
      g.fillStyle(0x000000, 0.6);
      g.fillRect(x - 1, y - 1, w + 2, h + 2);
      g.fillStyle(0xffffff, 0.25);
      g.fillRect(x, y, w, h);
      g.fillStyle(color, 1);
      g.fillRect(x, y, w * power, h);
    };

    this.bluePlayers.forEach(p => drawBar(p, 0x60a5fa));
    this.redPlayers.forEach(p => drawBar(p, 0xf87171));
  }

  chargePower(heldMs) {
    const MAX_CHARGE_MS = 200; // Fast charge for AI kicks
    return Phaser.Math.Clamp(heldMs / MAX_CHARGE_MS, 0, 1);
  }

  releaseKick(player, teamNum, dx, dy, power) {
    const dist = Phaser.Math.Distance.Between(player.x, player.y, this.ball.x, this.ball.y);
    const hasBall = this.possessor === player;
    if (!hasBall && dist >= 75) return;

    Sound.playKick();
    this.cameras.main.shake(60 + power * 90, 0.003 + power * 0.004);

    if (this.possessor === player) this.possessor = null;
    this.captureReadyAt = this.time.now + 260;
    this.lastTouchTeam = teamNum;

    let kdx = dx;
    let kdy = dy;
    const klen = Math.hypot(kdx, kdy) || 1;
    kdx /= klen;
    kdy /= klen;

    const teammates = teamNum === 1 ? this.bluePlayers : this.redPlayers;
    const passTarget = this.findTeammateInDirection(player, teammates, kdx, kdy);

    // The ball always rolls along the ground (no loft) — players can't jump, and
    // a vertical hop only distorts the apparent travel direction in this top-down
    // view. Keep it grounded so kicks read true in every direction.
    if (passTarget) {
      const angleRad = Phaser.Math.Angle.Between(player.x, player.y, passTarget.x, passTarget.y);
      const passSpeed = 300 + 360 * power;
      this.ball.setVelocity(Math.cos(angleRad) * passSpeed, Math.sin(angleRad) * passSpeed);
    } else {
      const shootSpeed = 420 + 360 * power;
      this.ball.setVelocity(kdx * shootSpeed, kdy * shootSpeed);
    }
    this.ballZ = 0;
    this.ballZVelocity = 0;
  }

  triggerTeamKick(player, teamNum, dx, dy) {
    this.releaseKick(player, teamNum, dx, dy, 0.85);
  }

  findTeammateInDirection(player, teammates, dx, dy) {
    if (dx === 0 && dy === 0) return null;

    let bestTarget = null;
    let bestScore = -Infinity;

    teammates.forEach(mate => {
      if (mate === player) return;

      const tdx = mate.x - player.x;
      const tdy = mate.y - player.y;
      const dist = Math.sqrt(tdx * tdx + tdy * tdy);

      if (dist > 480) return;

      const ntx = tdx / dist;
      const nty = tdy / dist;
      const dot = ntx * dx + nty * dy;

      if (dot > 0.68) {
        const score = dot * 100 - dist * 0.1;
        if (score > bestScore) {
          bestScore = score;
          bestTarget = mate;
        }
      }
    });

    return bestTarget;
  }

  updateGkAI(gk, teamNum, profile, time, delta) {
    const ballX = this.ball.x;
    const ballY = this.ball.y;

    const isBlue = teamNum === 1;
    const defaultX = isBlue ? 180 : 1228;
    const defendingArea = isBlue ? ballX < 450 : ballX > 958;

    // Calculate X coordinate movement based on attackPositioning attribute
    const attackPos = (profile && profile.attackPositioning !== undefined) ? profile.attackPositioning : 0.0;
    const maxSweepDistance = 180;
    let targetX = defaultX;

    if (isBlue) {
      if (ballX > 350) {
        const ballFactor = Phaser.Math.Clamp((ballX - 350) / 700, 0, 1);
        targetX = defaultX + (attackPos * maxSweepDistance * ballFactor);
      }
    } else {
      if (ballX < 1058) {
        const ballFactor = Phaser.Math.Clamp((1058 - ballX) / 700, 0, 1);
        targetX = defaultX - (attackPos * maxSweepDistance * ballFactor);
      }
    }

    // Smoothly interpolate X
    const trackingSpeed = (profile && profile.trackingSpeed !== undefined) ? profile.trackingSpeed : 0.08;
    gk.x += (targetX - gk.x) * trackingSpeed;

    const isDiving = gk.anims.isPlaying && gk.anims.currentAnim && gk.anims.currentAnim.key.includes('dive');

    if (defendingArea) {
      const targetY = Phaser.Math.Clamp(ballY, 280, 480);
      gk.y += (targetY - gk.y) * trackingSpeed;

      const triggerDistance = isBlue ? 280 : 1128;
      const isClose = isBlue ? ballX < triggerDistance : ballX > triggerDistance;
      if (isClose && Math.abs(ballY - gk.y) > 35 && !isDiving && Math.random() < (profile.diveChance || 0.1)) {
        const isDivingUp = ballY < gk.y;
        const diveLeftAnim = isBlue ? 'gk_blue_dive_left' : 'gk_red_dive_left';
        const diveRightAnim = isBlue ? 'gk_blue_dive_right' : 'gk_red_dive_right';
        gk.play(isDivingUp ? (isBlue ? diveLeftAnim : diveRightAnim) : (isBlue ? diveRightAnim : diveLeftAnim));
        Sound.playJump();
      }
    } else {
      gk.y += (380 - gk.y) * 0.05;
    }

    // Reset to ready animation if not diving and ready is not already playing
    if (!isDiving) {
      const readyAnim = isBlue ? 'gk_blue_ready' : 'gk_red_ready';
      if (!gk.anims.currentAnim || gk.anims.currentAnim.key !== readyAnim) {
        gk.play(readyAnim);
      }
    }
  }

  handleGkBlock(ball, gk) {
    if (this.ballZ < 65) {
      Sound.playBounce();
      this.cameras.main.shake(80, 0.003);

      this.possessor = null;
      this.captureReadyAt = this.time.now + 300;

      const direction = gk.x < 700 ? 1 : -1;
      ball.setVelocityX(direction * (240 + Math.random() * 160));
      ball.setVelocityY((Math.random() * 2 - 1) * 150);
      this.ballZ = 0;
      this.ballZVelocity = 0;
      this.lastTouchTeam = gk.x < 700 ? 1 : 2;

      const gkNum = gk.x < 700 ? 1 : 2;
    }
  }

  checkGoals() {
    if (this.isResetting) return;

    if (this.ballZ < 80 && this.ball.y > this.goalMouthTop && this.ball.y < this.goalMouthBottom) {
      if (this.ball.x < this.leftGoalLine) {
        this.scoreGoal(2);
      }
      else if (this.ball.x > this.rightGoalLine) {
        this.scoreGoal(1);
      }
    }
  }

  // Detect the ball reaching the boundaries and bounce it back into play,
  // except when a goal has just been scored.
  checkOutOfBounds(time) {
    if (this.isResetting || this.throwInActive || this.possessor) return;

    const TOUCH_TOP = 178;
    const TOUCH_BOTTOM = 662;
    const GOAL_LINE_L = 150;
    const GOAL_LINE_R = 1258;

    const bx = this.ball.x;
    const by = this.ball.y;

    const bounceFactor = 0.78;
    let bounced = false;

    // Check Touchline (Top/Bottom) boundaries
    if (by < TOUCH_TOP) {
      const pushBack = 25 + Math.random() * 55; // 25 to 80 px
      this.ball.y = TOUCH_TOP + pushBack;
      const baseVel = Math.max(150, Math.abs(this.ball.body.velocity.y) * bounceFactor);
      this.ball.setVelocityY(baseVel + Math.random() * 80);
      this.ball.setVelocityX(this.ball.body.velocity.x * 0.8 + (Math.random() * 100 - 50));
      bounced = true;
    } else if (by > TOUCH_BOTTOM) {
      const pushBack = 25 + Math.random() * 55;
      this.ball.y = TOUCH_BOTTOM - pushBack;
      const baseVel = Math.max(150, Math.abs(this.ball.body.velocity.y) * bounceFactor);
      this.ball.setVelocityY(-(baseVel + Math.random() * 80));
      this.ball.setVelocityX(this.ball.body.velocity.x * 0.8 + (Math.random() * 100 - 50));
      bounced = true;
    }

    // Check Goal-line (Left/Right) boundaries (only outside the scoring goal mouths)
    const insideGoalMouthY = (by > this.goalMouthTop && by < this.goalMouthBottom);
    if (!insideGoalMouthY) {
      if (bx < GOAL_LINE_L) {
        const pushBack = 25 + Math.random() * 55;
        this.ball.x = GOAL_LINE_L + pushBack;
        const baseVel = Math.max(150, Math.abs(this.ball.body.velocity.x) * bounceFactor);
        this.ball.setVelocityX(baseVel + Math.random() * 80);
        this.ball.setVelocityY(this.ball.body.velocity.y * 0.8 + (Math.random() * 100 - 50));
        bounced = true;
      } else if (bx > GOAL_LINE_R) {
        const pushBack = 25 + Math.random() * 55;
        this.ball.x = GOAL_LINE_R - pushBack;
        const baseVel = Math.max(150, Math.abs(this.ball.body.velocity.x) * bounceFactor);
        this.ball.setVelocityX(-(baseVel + Math.random() * 80));
        this.ball.setVelocityY(this.ball.body.velocity.y * 0.8 + (Math.random() * 100 - 50));
        bounced = true;
      }
    }

    if (bounced) {
      Sound.playBounce();
    }
  }

  // Place the ball back in play at (x, y) and hand possession to the awarded
  // team's nearest player. A short freeze prevents the OOB check from
  // re-triggering on the same frame the ball is still past the line.
  doRestart(teamNum, x, y, label) {
    this.throwInActive = true;
    this.possessor = null;
    this.captureReadyAt = this.time.now + 400;

    this.ball.body.reset(x, y);
    this.ballZ = 0;
    this.ballZVelocity = 0;

    const team = teamNum === 1 ? this.bluePlayers : this.redPlayers;
    let taker = team[0];
    let bestDist = Infinity;
    team.forEach(p => {
      const d = Phaser.Math.Distance.Between(p.x, p.y, x, y);
      if (d < bestDist) { bestDist = d; taker = p; }
    });
    // Bring the taker to the ball and face it inward.
    const inward = teamNum === 1 ? 1 : -1;
    taker.setPosition(x - inward * 34, y);
    taker.setVelocity(0, 0);
    taker.setFlipX(inward < 0);
    if (teamNum === 1) this.activeBlueIdx = taker.getData('idx');
    else this.activeRedIdx = taker.getData('idx');
    this.lastTouchTeam = teamNum;

    const banner = this.add.text(x, y - 60, label, {
      fontFamily: '"Outfit", "Inter", Arial, sans-serif',
      fontSize: '28px',
      color: teamNum === 1 ? '#3b82f6' : '#ef4444',
      fontStyle: 'bold',
      stroke: '#ffffff',
      strokeThickness: 5
    }).setOrigin(0.5).setDepth(20);
    Sound.playWhistle();

    this.time.delayedCall(700, () => {
      banner.destroy();
      this.throwInActive = false;
      // Give the taker the ball so play resumes under their control.
      this.possessor = taker;
      this.captureReadyAt = 0;
    });
  }

  scoreGoal(scoringPlayer) {
    this.isResetting = true;
    Sound.playGoal();
    this.cameras.main.shake(400, 0.012);
    this.cameras.main.flash(300, 255, 255, 255);

    if (scoringPlayer === 1) {
      this.score1++;
      this.scoreText1.setText(this.score1);
    } else {
      this.score2++;
      this.scoreText2.setText(this.score2);
    }

    const goalText = this.add.text(704, 300, 'GOAL!!!', {
      fontFamily: '"Outfit", "Inter", Arial, sans-serif',
      fontSize: '96px',
      color: scoringPlayer === 1 ? '#3b82f6' : '#ef4444',
      fontStyle: 'bold',
      stroke: '#ffffff',
      strokeThickness: 12,
      shadow: { color: '#000000', blur: 15, stroke: true, fill: true }
    }).setOrigin(0.5).setScale(0);

    this.tweens.add({
      targets: goalText,
      scale: 1,
      duration: 500,
      ease: 'Bounce.easeOut',
      onComplete: () => {
        this.time.delayedCall(1500, () => {
          this.tweens.add({
            targets: goalText,
            alpha: 0,
            scale: 2,
            duration: 300,
            onComplete: () => {
              goalText.destroy();
              this.resetPositionsAfterGoal();
            }
          });
        });
      }
    });
  }

  resetPositionsAfterGoal() {
    const width = this.sys.game.config.width;

    // Clear ball-state machines so the kickoff starts neutral.
    this.possessor = null;
    this.throwInActive = false;
    this.captureReadyAt = 0;
    this.lastTouchTeam = 0;

    // Reset player specific data
    const resetPlayer = p => {
      p.setData('kickPending', false);
      p.setData('possessionStart', 0);
      const tState = p.getData('tackle');
      if (tState) {
        tState.active = false;
        tState.endAt = 0;
        tState.cooldownUntil = 0;
      }
    };
    this.bluePlayers.forEach(resetPlayer);
    this.redPlayers.forEach(resetPlayer);

    if (this.powerBarGfx) this.powerBarGfx.clear();

    this.ball.setPosition(width / 2, 380);
    this.ball.setVelocity(0, 0);
    this.ball.setRotation(0);
    this.ballZ = 0;
    this.ballZVelocity = 0;

    const blueSpawnCoords = [
      { x: 450, y: 380 },
      { x: 320, y: 250 },
      { x: 320, y: 510 },
      { x: 560, y: 380 }
    ];
    this.bluePlayers.forEach((p, idx) => {
      p.setPosition(blueSpawnCoords[idx].x, blueSpawnCoords[idx].y);
      p.setVelocity(0, 0);
      p.setRotation(0);
      p.clearTint();
      p.play('blue_idle');
    });
    this.activeBlueIdx = 0;

    const redSpawnCoords = [
      { x: 958, y: 380 },
      { x: 1088, y: 250 },
      { x: 1088, y: 510 },
      { x: 848, y: 380 }
    ];
    this.redPlayers.forEach((p, idx) => {
      p.setPosition(redSpawnCoords[idx].x, redSpawnCoords[idx].y);
      p.setVelocity(0, 0);
      p.setRotation(0);
      p.clearTint();
      p.play('red_idle');
    });
    this.activeRedIdx = 0;

    this.gk1.setPosition(180, 380);
    this.gk2.setPosition(1228, 380);

    Sound.playWhistle();
    this.isResetting = false;
  }

  startTimer() {
    this.timerEvent = this.time.addEvent({
      delay: 1000,
      callback: () => {
        this.matchTime--;

        const mins = Math.floor(this.matchTime / 60);
        const secs = this.matchTime % 60;
        this.timeText.setText(`${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`);

        if (this.matchTime <= 0) {
          this.endMatch();
        }
      },
      callbackScope: this,
      loop: true
    });
  }

  endMatch() {
    this.gameActive = false;
    this.timerEvent.destroy();
    Sound.playWhistle();

    this.ball.body.setEnable(false);
    this.bluePlayers.forEach(p => p.body.setEnable(false));
    this.redPlayers.forEach(p => p.body.setEnable(false));

    let winnerMsg = 'DRAW!';
    let winColor = '#ffcc00';
    if (this.score1 > this.score2) {
      winnerMsg = 'BLUE TEAM WINS!';
      winColor = '#3b82f6';
    } else if (this.score2 > this.score1) {
      winnerMsg = 'RED TEAM WINS!';
      winColor = '#ef4444';
    }

    window.dispatchEvent(new CustomEvent('soccer-game-over', {
      detail: {
        winnerMsg,
        winColor,
        score1: this.score1,
        score2: this.score2
      }
    }));
  }

  // Full reset for a rematch — re-enables physics bodies, resets score/clock
  // and positions. Called from the DOM "REMATCH" button in main.js.
  restartMatch() {
    this.score1 = 0;
    this.score2 = 0;
    this.matchTime = GAME_DURATION_SEC;
    this.scoreText1.setText('0');
    this.scoreText2.setText('0');
    const initialMins = Math.floor(GAME_DURATION_SEC / 60);
    const initialSecs = GAME_DURATION_SEC % 60;
    const initialTimeStr = `${initialMins.toString().padStart(2, '0')}:${initialSecs.toString().padStart(2, '0')}`;
    this.timeText.setText(initialTimeStr);
    this.isResetting = false;

    this.ball.body.setEnable(true);
    this.bluePlayers.forEach(p => p.body.setEnable(true));
    this.redPlayers.forEach(p => p.body.setEnable(true));

    this.resetPositionsAfterGoal();
    this.startTimer();
    this.gameActive = true;
  }

  setupCoaches() {
    this.coach1 = this.add.image(60, 715, 'coach_portrait').setScale(0.10);
    this.shoutGraphics1 = this.add.graphics().setAlpha(0);
    this.shoutText1 = this.add.text(160, 668, '', {
      fontFamily: 'monospace',
      fontSize: '13px',
      color: '#000000',
      fontStyle: 'bold'
    }).setOrigin(0.5).setAlpha(0);

    this.coach2 = this.add.image(1348, 715, 'coach_portrait').setScale(0.10).setFlipX(true);
    this.shoutGraphics2 = this.add.graphics().setAlpha(0);
    this.shoutText2 = this.add.text(1248, 668, '', {
      fontFamily: 'monospace',
      fontSize: '13px',
      color: '#000000',
      fontStyle: 'bold'
    }).setOrigin(0.5).setAlpha(0);
  }

  showCoachShout(coachNum, message, isAngry = false) {
    const graphics = coachNum === 1 ? this.shoutGraphics1 : this.shoutGraphics2;
    const shoutText = coachNum === 1 ? this.shoutText1 : this.shoutText2;

    shoutText.setText(message);

    // Calculate dimensions
    const paddingX = 14;
    const paddingY = 8;
    const bubbleWidth = Math.max(80, shoutText.width + paddingX * 2);
    const bubbleHeight = shoutText.height + paddingY * 2;

    // Reset graphics
    graphics.clear();
    graphics.setScale(1);
    graphics.setAlpha(1);

    const bubbleX = coachNum === 1 ? 160 : 1248;
    const bubbleY = 668;

    // Set position of graphics object
    graphics.setPosition(bubbleX, bubbleY);

    // Draw relative to (0, 0)
    const rx = -bubbleWidth / 2;
    const ry = -bubbleHeight / 2;

    // Background and outline
    graphics.fillStyle(0xffffff, 1.0);
    graphics.lineStyle(2, 0x000000, 1.0);

    graphics.fillRoundedRect(rx, ry, bubbleWidth, bubbleHeight, 6);
    graphics.strokeRoundedRect(rx, ry, bubbleWidth, bubbleHeight, 6);

    // Tail pointing to coach portrait
    if (coachNum === 1) {
      const p1x = rx + 15;
      const p1y = ry + bubbleHeight - 1;
      const p2x = rx + 30;
      const p2y = ry + bubbleHeight - 1;
      const p3x = -80;
      const p3y = 32;

      graphics.fillTriangle(p1x, p1y, p2x, p2y, p3x, p3y);
      graphics.strokeLineShape(new Phaser.Geom.Line(p1x, p1y, p3x, p3y));
      graphics.strokeLineShape(new Phaser.Geom.Line(p2x, p2y, p3x, p3y));
    } else {
      const p1x = rx + bubbleWidth - 30;
      const p1y = ry + bubbleHeight - 1;
      const p2x = rx + bubbleWidth - 15;
      const p2y = ry + bubbleHeight - 1;
      const p3x = 80;
      const p3y = 32;

      graphics.fillTriangle(p1x, p1y, p2x, p2y, p3x, p3y);
      graphics.strokeLineShape(new Phaser.Geom.Line(p1x, p1y, p3x, p3y));
      graphics.strokeLineShape(new Phaser.Geom.Line(p2x, p2y, p3x, p3y));
    }

    shoutText.setAlpha(1);

    // Bounce animation
    this.tweens.add({
      targets: [graphics, shoutText],
      scale: 1.15,
      duration: 80,
      yoyo: true,
      repeat: 0,
      onComplete: () => {
        graphics.setScale(1);
        shoutText.setScale(1);
      }
    });

    // Clear previous timers if any
    if (coachNum === 1 && this.shoutTimer1) this.shoutTimer1.remove();
    if (coachNum === 2 && this.shoutTimer2) this.shoutTimer2.remove();

    // Auto-fade after 3.0 seconds
    const timer = this.time.delayedCall(3000, () => {
      this.tweens.add({
        targets: [graphics, shoutText],
        alpha: 0,
        duration: 200
      });
    });

    if (coachNum === 1) this.shoutTimer1 = timer;
    else this.shoutTimer2 = timer;
  }

  createPlayerAnimations() {
    this.anims.create({
      key: 'blue_idle',
      frames: [{ key: 'player_blue', frame: 0 }],
      frameRate: 1
    });
    this.anims.create({
      key: 'blue_run',
      frames: this.anims.generateFrameNumbers('player_blue', { start: 0, end: 2 }),
      frameRate: 8,
      repeat: -1
    });
    this.anims.create({
      key: 'blue_kick',
      frames: [{ key: 'player_blue', frame: 3 }],
      frameRate: 1
    });

    this.anims.create({
      key: 'red_idle',
      frames: [{ key: 'player_red', frame: 0 }],
      frameRate: 1
    });
    this.anims.create({
      key: 'red_run',
      frames: this.anims.generateFrameNumbers('player_red', { start: 0, end: 2 }),
      frameRate: 8,
      repeat: -1
    });
    this.anims.create({
      key: 'red_kick',
      frames: [{ key: 'player_red', frame: 3 }],
      frameRate: 1
    });
  }

  createGoalkeeperFrames() {
    const readyCoords = [
      { x: 35, w: 206 },
      { x: 263, w: 205 },
      { x: 501, w: 192 },
      { x: 730, w: 191 },
      { x: 941, w: 206 },
      { x: 1168, w: 205 }
    ];

    const keys = [];
    if (this.textures.exists('goalkeeper_blue')) keys.push('goalkeeper_blue');
    if (this.textures.exists('goalkeeper_red')) keys.push('goalkeeper_red');
    if (this.textures.exists('goalkeeper')) keys.push('goalkeeper');

    keys.forEach(key => {
      readyCoords.forEach((coord, idx) => {
        this.textures.get(key).add(`${key}_ready_${idx}`, 0, coord.x, 49, coord.w, 198);
      });
    });

    const diveLeftCoords = [
      { x: 35, w: 255 },
      { x: 307, w: 262 },
      { x: 586, w: 251 },
      { x: 856, w: 252 },
      { x: 1119, w: 254 }
    ];
    keys.forEach(key => {
      diveLeftCoords.forEach((coord, idx) => {
        this.textures.get(key).add(`${key}_dive_left_${idx}`, 0, coord.x, 307, coord.w, 180);
      });
    });

    const diveRightCoords = [
      { x: 35, w: 259 },
      { x: 312, w: 255 },
      { x: 583, w: 254 },
      { x: 850, w: 253 },
      { x: 1119, w: 254 }
    ];
    keys.forEach(key => {
      diveRightCoords.forEach((coord, idx) => {
        this.textures.get(key).add(`${key}_dive_right_${idx}`, 0, coord.x, 556, coord.w, 177);
      });
    });

    if (this.textures.exists('goalkeeper_blue')) {
      this.anims.create({
        key: 'gk_blue_ready',
        frames: [
          { key: 'goalkeeper_blue', frame: 'goalkeeper_blue_ready_0' },
          { key: 'goalkeeper_blue', frame: 'goalkeeper_blue_ready_1' },
          { key: 'goalkeeper_blue', frame: 'goalkeeper_blue_ready_2' },
          { key: 'goalkeeper_blue', frame: 'goalkeeper_blue_ready_3' },
          { key: 'goalkeeper_blue', frame: 'goalkeeper_blue_ready_4' },
          { key: 'goalkeeper_blue', frame: 'goalkeeper_blue_ready_5' }
        ],
        frameRate: 6,
        repeat: -1
      });

      this.anims.create({
        key: 'gk_blue_dive_left',
        frames: [
          { key: 'goalkeeper_blue', frame: 'goalkeeper_blue_dive_left_0' },
          { key: 'goalkeeper_blue', frame: 'goalkeeper_blue_dive_left_1' },
          { key: 'goalkeeper_blue', frame: 'goalkeeper_blue_dive_left_2' },
          { key: 'goalkeeper_blue', frame: 'goalkeeper_blue_dive_left_3' },
          { key: 'goalkeeper_blue', frame: 'goalkeeper_blue_dive_left_4' }
        ],
        frameRate: 8,
        repeat: 0
      });

      this.anims.create({
        key: 'gk_blue_dive_right',
        frames: [
          { key: 'goalkeeper_blue', frame: 'goalkeeper_blue_dive_right_0' },
          { key: 'goalkeeper_blue', frame: 'goalkeeper_blue_dive_right_1' },
          { key: 'goalkeeper_blue', frame: 'goalkeeper_blue_dive_right_2' },
          { key: 'goalkeeper_blue', frame: 'goalkeeper_blue_dive_right_3' },
          { key: 'goalkeeper_blue', frame: 'goalkeeper_blue_dive_right_4' }
        ],
        frameRate: 8,
        repeat: 0
      });
    }

    if (this.textures.exists('goalkeeper_red')) {
      this.anims.create({
        key: 'gk_red_ready',
        frames: [
          { key: 'goalkeeper_red', frame: 'goalkeeper_red_ready_0' },
          { key: 'goalkeeper_red', frame: 'goalkeeper_red_ready_1' },
          { key: 'goalkeeper_red', frame: 'goalkeeper_red_ready_2' },
          { key: 'goalkeeper_red', frame: 'goalkeeper_red_ready_3' },
          { key: 'goalkeeper_red', frame: 'goalkeeper_red_ready_4' },
          { key: 'goalkeeper_red', frame: 'goalkeeper_red_ready_5' }
        ],
        frameRate: 6,
        repeat: -1
      });

      this.anims.create({
        key: 'gk_red_dive_left',
        frames: [
          { key: 'goalkeeper_red', frame: 'goalkeeper_red_dive_left_0' },
          { key: 'goalkeeper_red', frame: 'goalkeeper_red_dive_left_1' },
          { key: 'goalkeeper_red', frame: 'goalkeeper_red_dive_left_2' },
          { key: 'goalkeeper_red', frame: 'goalkeeper_red_dive_left_3' },
          { key: 'goalkeeper_red', frame: 'goalkeeper_red_dive_left_4' }
        ],
        frameRate: 8,
        repeat: 0
      });

      this.anims.create({
        key: 'gk_red_dive_right',
        frames: [
          { key: 'goalkeeper_red', frame: 'goalkeeper_red_dive_right_0' },
          { key: 'goalkeeper_red', frame: 'goalkeeper_red_dive_right_1' },
          { key: 'goalkeeper_red', frame: 'goalkeeper_red_dive_right_2' },
          { key: 'goalkeeper_red', frame: 'goalkeeper_red_dive_right_3' },
          { key: 'goalkeeper_red', frame: 'goalkeeper_red_dive_right_4' }
        ],
        frameRate: 8,
        repeat: 0
      });
    }

    if (this.textures.exists('goalkeeper')) {
      this.anims.create({
        key: 'gk_ready',
        frames: [
          { key: 'goalkeeper', frame: 'goalkeeper_ready_0' },
          { key: 'goalkeeper', frame: 'goalkeeper_ready_1' },
          { key: 'goalkeeper', frame: 'goalkeeper_ready_2' },
          { key: 'goalkeeper', frame: 'goalkeeper_ready_3' },
          { key: 'goalkeeper', frame: 'goalkeeper_ready_4' },
          { key: 'goalkeeper', frame: 'goalkeeper_ready_5' }
        ],
        frameRate: 6,
        repeat: -1
      });

      this.anims.create({
        key: 'gk_dive_left',
        frames: [
          { key: 'goalkeeper', frame: 'goalkeeper_dive_left_0' },
          { key: 'goalkeeper', frame: 'goalkeeper_dive_left_1' },
          { key: 'goalkeeper', frame: 'goalkeeper_dive_left_2' },
          { key: 'goalkeeper', frame: 'goalkeeper_dive_left_3' },
          { key: 'goalkeeper', frame: 'goalkeeper_dive_left_4' }
        ],
        frameRate: 8,
        repeat: 0
      });

      this.anims.create({
        key: 'gk_dive_right',
        frames: [
          { key: 'goalkeeper', frame: 'goalkeeper_dive_right_0' },
          { key: 'goalkeeper', frame: 'goalkeeper_dive_right_1' },
          { key: 'goalkeeper', frame: 'goalkeeper_dive_right_2' },
          { key: 'goalkeeper', frame: 'goalkeeper_dive_right_3' },
          { key: 'goalkeeper', frame: 'goalkeeper_dive_right_4' }
        ],
        frameRate: 8,
        repeat: 0
      });
    }
  }



  // Build the invisible static collision walls for one goal. The mouth faces
  // the pitch; the back and sides contain the ball once it crosses the line.
  buildGoalColliders(backX, frontX) {
    const top = this.goalMouthTop;
    const bottom = this.goalMouthBottom;
    const midX = (backX + frontX) / 2;
    const midY = (top + bottom) / 2;
    const sideW = Math.abs(frontX - backX);
    const mouthH = bottom - top;

    const addWall = (x, y, w, h) => {
      const r = this.add.rectangle(x, y, w, h, 0xffffff, 0);
      this.physics.add.existing(r, true);
      this.postsGroup.add(r);
    };

    addWall(backX, midY, 10, mouthH);        // back net
    addWall(midX, top, sideW, 10);           // top side net
    addWall(midX, bottom, sideW, 10);        // bottom side net
    addWall(frontX, top, 12, 12);            // front post (top)
    addWall(frontX, bottom, 12, 12);         // front post (bottom)
  }

  // Draw both goals - now empty as they are baked directly into the background pitch image.
  drawGoals() { }

  // --- Coach commands (invoked from the DOM shout bar in main.js) ---
  coachShoot(teamNum) {
    const player = teamNum === 1
      ? this.bluePlayers[this.activeBlueIdx]
      : this.redPlayers[this.activeRedIdx];
    const dir = teamNum === 1 ? 1 : -1;
    this.triggerTeamKick(player, teamNum, dir, 0);
  }

  coachJump(teamNum) {
    const player = teamNum === 1
      ? this.bluePlayers[this.activeBlueIdx]
      : this.redPlayers[this.activeRedIdx];
    this.tweens.add({
      targets: player,
      y: player.y - 60,
      duration: 150,
      yoyo: true,
      onStart: () => Sound.playJump()
    });
  }

  coachDefend(teamNum) {
    const player = teamNum === 1
      ? this.bluePlayers[this.activeBlueIdx]
      : this.redPlayers[this.activeRedIdx];
    player.setPosition(teamNum === 1 ? 380 : 1028, player.y);
  }

  coachAttack(teamNum) {
    const player = teamNum === 1
      ? this.bluePlayers[this.activeBlueIdx]
      : this.redPlayers[this.activeRedIdx];
    player.setPosition(teamNum === 1 ? 750 : 658, player.y);
  }

  showPlayerShout(role, message) {
    // Find player with the given role
    let player = null;
    if (role === 'goalkeeper') {
      player = this.gk1;
    } else {
      player = this.bluePlayers.find(p => p.getData('role') === role);
    }

    if (!player) return;

    const graphics = player.getData('shoutGraphics');
    const shoutText = player.getData('shoutText');
    if (!graphics || !shoutText) return;

    shoutText.setText(message);

    // Calculate dimensions
    const paddingX = 10;
    const paddingY = 6;
    const bubbleWidth = Math.max(70, shoutText.width + paddingX * 2);
    const bubbleHeight = shoutText.height + paddingY * 2;

    // Reset graphics
    graphics.clear();
    graphics.setScale(1);
    graphics.setAlpha(1);

    // Initial position
    const bubbleX = player.x;
    const bubbleY = player.y - 75;
    graphics.setPosition(bubbleX, bubbleY);
    shoutText.setPosition(bubbleX, bubbleY);

    // Draw bubble box relative to (0, 0)
    const rx = -bubbleWidth / 2;
    const ry = -bubbleHeight / 2;

    // White background, black border
    graphics.fillStyle(0xffffff, 1.0);
    graphics.lineStyle(1.5, 0x000000, 1.0);

    graphics.fillRoundedRect(rx, ry, bubbleWidth, bubbleHeight, 5);
    graphics.strokeRoundedRect(rx, ry, bubbleWidth, bubbleHeight, 5);

    // Draw small tail pointing down to the player's head (target local y offset is ~45px)
    const p1x = rx + bubbleWidth / 2 - 6;
    const p1y = ry + bubbleHeight - 1;
    const p2x = rx + bubbleWidth / 2 + 6;
    const p2y = ry + bubbleHeight - 1;
    const p3x = 0;
    const p3y = 42;

    graphics.fillTriangle(p1x, p1y, p2x, p2y, p3x, p3y);
    graphics.strokeLineShape(new Phaser.Geom.Line(p1x, p1y, p3x, p3y));
    graphics.strokeLineShape(new Phaser.Geom.Line(p2x, p2y, p3x, p3y));

    shoutText.setAlpha(1);

    // Bounce animation
    this.tweens.add({
      targets: [graphics, shoutText],
      scale: 1.12,
      duration: 80,
      yoyo: true,
      repeat: 0,
      onComplete: () => {
        graphics.setScale(1);
        shoutText.setScale(1);
      }
    });

    // Clear previous timer for this player if it exists
    const timerKey = `playerShoutTimer_${role}`;
    if (this[timerKey]) this[timerKey].remove();

    // Auto-fade after 3 seconds (as requested: "for 3s")
    const timer = this.time.delayedCall(3000, () => {
      this.tweens.add({
        targets: [graphics, shoutText],
        alpha: 0,
        duration: 200
      });
    });

    this[timerKey] = timer;
  }

  showTeamHuddle(huddle) {
    // Clear any pending huddle timers/tweens to avoid overlap
    if (this.huddleTimers) {
      this.huddleTimers.forEach(t => t.remove());
    }
    this.huddleTimers = [];

    const roles = ['defender', 'midfielder', 'forward', 'goalkeeper'];
    let delay = 0;

    roles.forEach(role => {
      if (huddle[role]) {
        const msg = huddle[role].toUpperCase();
        const timer = this.time.delayedCall(delay, () => {
          this.showPlayerShout(role, msg);
        });
        this.huddleTimers.push(timer);
        delay += 1000; // 1 second sequential delay
      }
    });
  }
}

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

// Web Audio API Synthesizer for Retro Arcade Sound Effects
class SoundManager {
  constructor() {
    this.ctx = null;
    this.enabled = true;
  }

  init() {
    if (this.ctx) return;
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (AudioContextClass) {
      this.ctx = new AudioContextClass();
    }
  }

  toggle(state) {
    this.enabled = state !== undefined ? state : !this.enabled;
    return this.enabled;
  }

  playTone(freq, type, duration, slideTo = null) {
    if (!this.enabled) return;
    this.init();
    if (!this.ctx) return;

    // Resume context if suspended (browser security policy)
    if (this.ctx.state === 'suspended') {
      this.ctx.resume();
    }

    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();

    osc.type = type;
    osc.frequency.setValueAtTime(freq, this.ctx.currentTime);

    if (slideTo) {
      osc.frequency.exponentialRampToValueAtTime(slideTo, this.ctx.currentTime + duration);
    }

    gain.gain.setValueAtTime(0.15, this.ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, this.ctx.currentTime + duration);

    osc.connect(gain);
    gain.connect(this.ctx.destination);

    osc.start();
    osc.stop(this.ctx.currentTime + duration);
  }

  playNoise(duration, type = 'white') {
    if (!this.enabled) return;
    this.init();
    if (!this.ctx) return;

    if (this.ctx.state === 'suspended') {
      this.ctx.resume();
    }

    const bufferSize = this.ctx.sampleRate * duration;
    const buffer = this.ctx.createBuffer(1, bufferSize, this.ctx.sampleRate);
    const data = buffer.getChannelData(0);

    for (let i = 0; i < bufferSize; i++) {
      if (type === 'white') {
        data[i] = Math.random() * 2 - 1;
      } else { // pink / red noise approximation for crowd roar
        data[i] = (Math.random() * 2 - 1) * (1 - i / bufferSize);
      }
    }

    const noise = this.ctx.createBufferSource();
    noise.buffer = buffer;

    const gain = this.ctx.createGain();
    gain.gain.setValueAtTime(0.15, this.ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, this.ctx.currentTime + duration);

    noise.connect(gain);
    gain.connect(this.ctx.destination);

    noise.start();
  }

  playKick() {
    // Low frequency pop sliding down to sub
    this.playTone(150, 'sine', 0.15, 40);
  }

  playBounce() {
    // Higher pitch quick woodblock-like pop
    this.playTone(400, 'triangle', 0.08, 200);
  }

  playJump() {
    // Short upward pitch bend
    this.playTone(200, 'triangle', 0.15, 600);
  }

  playWhistle() {
    // Short piercing double beep
    this.playTone(2000, 'sine', 0.12);
    setTimeout(() => this.playTone(2000, 'sine', 0.25), 150);
  }

  playGoal() {
    // Multi-tone victorious chime + crowd roar
    const now = this.ctx ? this.ctx.currentTime : 0;
    
    this.playTone(523.25, 'sawtooth', 0.2); // C5
    setTimeout(() => this.playTone(659.25, 'sawtooth', 0.2), 150); // E5
    setTimeout(() => this.playTone(783.99, 'sawtooth', 0.2), 300); // G5
    setTimeout(() => {
      this.playTone(1046.50, 'sawtooth', 0.5); // C6
      // Crowd cheer noise overlay
      this.playNoise(1.8, 'pink');
    }, 450);
  }

  playMenuClick() {
    this.playTone(600, 'sine', 0.05, 800);
  }
}

export const Sound = new SoundManager();

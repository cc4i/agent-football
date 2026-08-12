# Multi-Tenant & 1v1 PvP Game Requirements

## Overview
This document outlines the core requirements and architecture considerations for supporting multi-tenant gameplay, interactive agent control, and head-to-head 2-player (1v1 PvP) battles for the Futsal WorldCup game and dugout system.

---

## Core Requirements

### 1. QR Code Onboarding & Match Initiation
- Users can scan a QR code on their device (e.g. mobile phone or tablet) to access their personal Dugout portal.
- Users provide registration details (name, handle, email) to authenticate and enter the matchmaking lobby or start an individual match.

### 2. Interactive Agent Control (Dugout)
- Users act as team managers through a natural-language chat interface.
- Users send tactical instructions, adjust attributes (speed, aggression, tackle radius, positioning, etc.), and trigger tactical shouts in real time.
- The multi-agent hierarchy (Dugout Agent $\rightarrow$ ADK Coach $\rightarrow$ Team Captain $\rightarrow$ Specialist Players) autonomously executes the strategy.

### 3. Multi-Tenant Concurrent Play
- The backend infrastructure, storage, and agent runtime must support complete multi-tenancy.
- Multiple users must be able to register, launch matches, manage agent swarms, and view real-time game logs simultaneously with zero cross-tenant state leakage or data collisions.

### 4. 2-Player Head-to-Head (1v1 PvP) Mode
- **Matchmaking & Match Rooms**: Two users (Player 1 as Blue Team, Player 2 as Red Team) can join the same match room (e.g., via direct QR code invite link or a public lobby queue).
- **Dual Dugout Agent Swarms**:
  - **Player 1**: Controls the Blue Team's branding, attributes, and coach shouts via their private Dugout chat.
  - **Player 2**: Controls the Red Team's branding, attributes, and coach shouts via their private Dugout chat.
- **Synchronized Match Simulation**:
  - The match runs in real-time with continuous tactical updates from both players.
  - Can be displayed on a shared stadium/arena screen (spectator view) and/or synchronized in real-time to both players' mobile devices over WebSockets / WebRTC.
- **Live In-Match Tactical Counter-Play**:
  - Player 1 shouts "high pressing / shoot on sight" $\rightarrow$ Blue Team adapts.
  - Player 2 observes this and shouts "sit deep / counter-attack" $\rightarrow$ Red Team counter-adjusts.

### 5. Leaderboard & Scoring System
- Match results from both PvE and 1v1 PvP matches are recorded on a global leaderboard.
- Scoring & ranking algorithms incorporate:
  - Match outcome (Win / Draw / Loss) & Elo / MMR rating
  - Total goals scored & goal differential
  - Speed / time to goals
  - Tactical efficiency and agent response metrics

---

## 1v1 PvP Architecture Blueprint

```
 ┌───────────────────────────┐                      ┌───────────────────────────┐
 │   PLAYER 1 (BLUE TEAM)    │                      │    PLAYER 2 (RED TEAM)    │
 │   • Mobile Dugout Chat    │                      │   • Mobile Dugout Chat    │
 │   • Blue Agent Swarm      │                      │   • Red Agent Swarm       │
 └─────────────┬─────────────┘                      └─────────────┬─────────────┘
               │                                                  │
               │ (Tactics / Shouts)                               │ (Tactics / Shouts)
               ▼                                                  ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                     MATCH ROOM / SERVER SIMULATOR (ROOM_ID)                  │
 │                                                                              │
 │    ┌──────────────────────────┐          ┌──────────────────────────┐        │
 │    │    Blue Team Profiles    │          │    Red Team Profiles     │        │
 │    │   (Updated by Player 1)  │          │   (Updated by Player 2)  │        │
 │    └────────────┬─────────────┘          └────────────┬─────────────┘        │
 │                 │                                     │                      │
 │                 ▼                                     ▼                      │
 │          ═══════════════════════════════════════════════════                 │
 │                           PHASER 2D PITCH ENGINE                            │
 │                        (Blue AI vs. Red AI Physics)                         │
 │          ═══════════════════════════════════════════════════                 │
 │                                     │                                        │
 └─────────────────────────────────────┼────────────────────────────────────────┘
                                       │
                      WebSocket / SSE Real-Time Broadcast
                                       │
                ┌──────────────────────┴──────────────────────┐
                ▼                                             ▼
 ┌───────────────────────────────┐             ┌───────────────────────────────┐
 │      ARENA SPECTATOR VIEW     │             │     PLAYERS' MATCH FEEDS      │
 │   (Big Screen / Public URL)   │             │   (Live Score, Pitch Sync)    │
 └───────────────────────────────┘             └───────────────────────────────┘
```

---

## What Needs to Be Done to Enable 1v1 PvP

1. **Room & Matchmaking Service**:
   - Create a Match Room coordinator (`MatchRoomManager`) tracking `room_id`, `player1_id (blue)`, `player2_id (red)`, and `status (waiting, playing, finished)`.
   - Provide QR codes containing match join URLs (e.g. `https://futsal.app/join/{room_id}`).

2. **Dual-Team Profile & State Partitioning**:
   - Extend the profile manager to isolate both teams per room:
     - `rooms/{room_id}/team_1_blue/{role}.json` (or in-memory / Redis key)
     - `rooms/{room_id}/team_2_red/{role}.json`
   - Update the simulation engine to apply Player 1's tactics to `this.blueProfiles` and Player 2's tactics to `this.redProfiles`.

3. **Dual Dugout & Routing**:
   - Each player connects to their own Dugout session bound to `(room_id, team_color)`.
   - Player 1's tools mutate Blue Team state; Player 2's tools mutate Red Team state.

4. **Real-Time Match Synchronization**:
   - Use WebSockets to broadcast match state (ball coordinates, player positions, score, game clock, tactical huddles/toasts) from the authoritative match server to both players and any spectator screens.

5. **Competitive Scoring & Leaderboard Engine**:
   - Persist match histories and compute Elo/MMR adjustments upon full-time whistle.

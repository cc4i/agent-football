# Multi-Tenant Game & Dugout Requirements

## Overview
This document outlines the core requirements for supporting multi-tenant gameplay and interactive agent control for the Futsal WorldCup game and dugout showcase.

## Requirements

1. **QR Code Onboarding & Match Initiation**
   - Users can scan a QR code on their device to access the game portal.
   - Users provide registration details (e.g., name, email) to register and kick off their individual game match.

2. **Interactive Agent Control (Dugout)**
   - Users can send messages to adjust and control agents in real-time.
   - Agents (manager, coach, captain, specialist players) adapt tactics, tune attributes, or make tactical shouts to help the user win the match.

3. **Leaderboard & Scoring System**
   - Match results are evaluated and ranked on a central leaderboard.
   - Scoring formulas account for match metrics, including:
     - Total goals scored
     - Time / speed to goals
     - Goal differential and defensive record

4. **Multi-Tenant Concurrent Play**
   - The game engine, backend services, and dugout showcase must support full multi-tenancy.
   - Multiple users must be able to register, launch matches, send agent instructions, and view results simultaneously without data collision or state interference.

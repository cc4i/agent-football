# Mobile QR Controller & Tournament Leaderboard System Design

## 1. Executive Overview

The **Mobile QR Controller & Tournament Leaderboard System** transforms the Futsal WorldCup simulation into an engaging, multi-device esports competition. 

Players use their physical smartphone as an interactive **Head Coach Controller** to direct AI agents in real time via QR code onboarding, while a central **Tournament Leaderboard** ranks coaches based on scoring performance, tactical agility, and defensive prowess.

---

## 2. End-to-End User Journey

```mermaid
flowchart TD
    A[🖥️ Big Screen Pitch: Idle / Start Screen] -->|Displays QR Code + Direct Link| B[📱 User scans QR with Smartphone]
    B --> C[📱 Mobile Web App opens /mobile]
    C -->|Enter Coach Name, Email & Tactic| D[📱 Tap '🚀 Kick Off Match!']
    D -->|Realtime Signal| E[🖥️ Big Screen Starts Match + Sets Coach Nameplate]
    
    subgraph Live Match (180s Simulation)
        E <-->|Sync Score, Clock & Match Stats| F[📱 Mobile Controller View]
        F -->|Send Custom Shouts or 1-Tap Tactical Presets| G[🤖 Gemini ADK / Team Captain]
        G -->|A2A Huddle & Player Behavioral Tuning| E
        E -->|Display Live Shout Bubble & Agent Logs| F
    end

    E -->|Whistle: FULL TIME| H[📊 Scoring Engine Computes Match Points]
    H --> I[🏆 Leaderboard Service Updates Rankings]
    I --> J[🖥️ Big Screen: Gold/Silver/Bronze Podium & Standings Table]
    I --> K[📱 Mobile: Personal Score Breakdown & Rank Badge]
```

---

## 3. Core Subsystems & Experience

### A. QR Code Onboarding & Lobby Initiation
* **Big Screen (Desktop/Pitch Display)**:
  * Renders a crisp SVG QR code on the idle start screen alongside LAN IP and web URL.
  * Continuously listens for mobile kickoff signals to transition from lobby to active pitch gameplay.
* **Mobile Web Onboarding (`/mobile.html`)**:
  * Lightweight, touch-friendly mobile form requesting:
    1. **Coach Name** (e.g., *Coach Alex* — updates pitch nameplate and live commentary).
    2. **Email Address** (e.g., *alex@example.com* — used for tournament leaderboard tracking).
    3. **Tactical Philosophy** (e.g., *High Press, Tiki-Taka, Counter Attack, Park The Bus*).
  * **"🚀 Kick Off Match!" Button**: Dispatches an instant start signal to the big screen.

---

### B. Live Mobile Coach Controller (In-Game)
* **Real-time Match Telemetry**:
  * Live Scoreboard (`BLUE 2 - 1 RED`) and Countdown Clock (`01:45`).
  * Live match event alerts (e.g., *"⚽ Goal scored by Blue Forward at 01:23!"*).
* **Tactical Coaching Controls**:
  * **Freeform Shout Input**: Custom text input dispatched to Gemini ADK agents over Agent-to-Agent (A2A) protocol.
  * **1-Tap Tactical Presets**:
    * ⚡ *High Press & Shoot* (increases forward pressing and shooting urgency).
    * 🛡️ *Tight Defense / Clear Ball* (drops midfielders deep and increases tackle intensity).
    * 🔄 *Counter Attack Wide* (increases pass range and width preference).
    * 🎯 *Shoot on Sight* (reduces pass threshold and maximizes shot power).
* **Live AI Agent Feedback**:
  * Displays real-time confirmation from the AI Team Captain (e.g., *"Captain: Understood Coach! Pushing forward line high"*).

---

### C. Tournament Leaderboard & Scoring Engine

#### 1. Performance Scoring Formula
To reward aggressive attacking, rapid goals, disciplined defense, and tactical mastery, total tournament score is computed as:

$$\text{Total Score} = \text{Base Result} + \text{Goal Bonus} - \text{Conceded Penalty} + \text{Quick Strike Bonus} + \text{Clean Sheet Bonus} + \text{Tactical Engagement}$$

| Category | Points | Description |
| :--- | :--- | :--- |
| **Match Outcome** | **+1,000 pts** (Win)<br>**+400 pts** (Draw)<br>**+100 pts** (Loss) | Base victory points |
| **Goals Scored** | **+300 pts** per goal | Reward for Blue team attack |
| **Goals Conceded** | **-100 pts** per goal (max -500) | Penalty for defensive lapses |
| **Quick Strike Bonus** | **+500 pts** (Goal $\le$ 30s)<br>**+350 pts** (Goal $\le$ 60s)<br>**+200 pts** (Goal $\le$ 120s)<br>**+100 pts** (Goal > 120s) | Speed to score first goal |
| **Clean Sheet** | **+300 pts** | Awarded if conceded 0 goals |
| **Tactical Mastery** | **+50 pts** per shout (max 5 shouts = **+250 pts**) | Rewards active tactical coaching |

#### 2. Leaderboard Presentation
* **Big Screen (Desktop/Pitch)**:
  * **Top 3 Podium**: Olympic-style Gold 🥇 (1st), Silver 🥈 (2nd), and Bronze 🥉 (3rd) pedestals with glowing neon styling.
  * **Standings Table**: Top 20 ranking table detailing Rank, Coach Name, Masked Email, Tactic, Goals, Quick Strike time, and Total Points.
  * Accessible anytime via the **"🏆 Standings"** header button.
* **Mobile Screen (Smartphone)**:
  * Individual Performance Summary: Point breakdown, personal rank badge (e.g., *"Rank #1 on Global Leaderboard"*), Top 3 standings, and a **"🔄 Play Again"** button.

---

## 4. Technical Architecture & Communication Protocols

```
┌──────────────────────────────┐              ┌──────────────────────────────┐
│  Big Screen (Desktop Pitch)  │              │    Mobile Phone Controller   │
│  - Phaser 4 Engine           │              │  - Touch-optimized HTML/CSS  │
│  - Dynamic QR Code Generator │              │  - Realtime Telemetry Sync   │
│  - Tournament Podium Overlay │              │  - 1-Tap Tactical Presets    │
└──────────────┬───────────────┘              └──────────────┬───────────────┘
               │                                             │
               │ HTTP / SSE / BroadcastChannel               │ HTTP REST / SSE
               ▼                                             ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                    FastAPI Backend & Dugout Relay Hub                     │
│  - GET /api/leaderboard (Top standings)                                    │
│  - POST /api/leaderboard (Submit match score)                              │
│  - GET /api/match/status (Live score & timer polling)                      │
│  - POST /api/match/join (Register coach profile)                           │
│  - POST /api/match/shout (Relay tactical shouts to ADK Agents)             │
└──────────────────────────────────────┬─────────────────────────────────────┘
                                       │
                                       ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                  Google ADK & A2A Multi-Agent Architecture                 │
│  - Head Coach Agent (Port 8000)                                            │
│  - Team Captain Agent (Port 8001, A2A Protocol)                            │
│  - Specialist Player Subagents (Forward, Midfielder, Defender, Goalkeeper) │
└────────────────────────────────────────────────────────────────────────────┘
```

### Communication & Sync Mechanisms
1. **Cross-Device HTTP / SSE Relay**:
   - Mobile controller communicates with backend endpoints `/api/match/join` and `/api/match/shout`.
   - Big screen fetches live shouts and applies them to the Phaser pitch and A2A agent pipeline.
2. **Local Zero-Latency Sync**:
   - `BroadcastChannel('futsal_match_sync')` and `localStorage` events enable instantaneous, zero-latency local testing across browser windows and devices.
3. **Resilient Persistence**:
   - Leaderboard entries persist in server-side storage (SQLite / JSON) with local client caching for offline resilience.

---

## 5. Security & Privacy Safeguards

1. **Email Privacy Masking**:
   - Public screens never display full user email addresses.
   - Emails are automatically masked before rendering (e.g., `alex.ferguson@example.com` $\rightarrow$ `a****n@example.com`).
2. **Input Sanitization**:
   - Coach names and custom shout strings are stripped of HTML tags and length-capped to prevent injection or layout disruption.
3. **Session Scoping**:
   - Each match kickoff creates a dedicated, ephemeral ADK agent session to prevent context bloat and ensure fast LLM responses.

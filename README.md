# Guardio — Cyber Defense Command

<!-- cspell:ignore Guardio guardio devkey uvicorn wscat pytest venv GUARDIO -->

[![CI](https://github.com/Arths17/Guardio/actions/workflows/ci.yml/badge.svg)](https://github.com/Arths17/Guardio/actions/workflows/ci.yml)

A real-time cyberattack visualization platform. Watch DDoS floods, ransomware outbreaks, and APT intrusions unfold live across a 108-node global network — streamed via WebSocket to an interactive world map, force-directed infrastructure graph, and incident timeline.

Built for hackathons. Cinematic by design.

---

## What it looks like

The app has five views, all driven by the same live WebSocket stream:

| View | What you see |
| --- | --- |
| **Threat Map** | World map with animated attack arcs flying between nodes. Nodes glow red when compromised, pulse orange when stressed, flicker purple when encrypted. |
| **Infra Graph** | Force-directed graph of all 108 nodes grouped by security zone (external → DMZ → internal → data → cloud → IoT). Edges turn red as nodes fall. |
| **Timeline** | Chronological incident log — every attack phase, node state change, IDS alert, and defense action in one expandable stream. |
| **Intelligence** | Protocol distribution charts, IDS detection table, attack summary, defense action log. |
| **AI Copilot** | Chat interface backed by Gemini — ask about the current threat posture, get mitigation recommendations, analyze the latest alert. |

---

## How the simulation works

The simulation is the core engine. Hit **START SIM** to boot it.

### What happens on START SIM

1. **108 nodes initialize** across 6 security zones with real geographic coordinates. Each node is assigned a type (server, database, cloud, client, IoT) and starts in a `healthy` state.
2. **A background loop fires every 120ms**, generating network packets between nodes. These stream to the frontend as WebSocket events — you see them as arcs on the world map and as entries in the alert feed.
3. **The IDS watches every packet**. Suspicious traffic (high rate, attack protocols, anomalous flags) scores above a threshold and fires an alert.

### Launching an attack

Click any of the attack buttons in the top bar while the simulation is running. Each attack is a scripted multi-phase sequence:

| Attack | What happens |
| --- | --- |
| **DDoS** | Probe phase → 20 sources flood a single server with UDP packets → server goes offline → anycast scrubbing activates → recovery |
| **Malware** | Patient-zero client gets infected → spreads via SMB/RDP to nearby servers → EDR quarantine kicks in |
| **Ransomware** | Lateral movement phase → exfiltration → ransomware encrypts nodes one by one (purple glow) → ransom demand event → recovery |
| **Phishing** | Spear-phishing campaign → credential theft → attacker replays stolen credentials → privilege escalation |
| **Botnet** | C2 server recruits IoT devices → enrolled bots beacon back → coordinated DDoS against infrastructure |
| **APT** | OSINT recon → zero-day exploitation → persistence installed → slow lateral movement → silent exfiltration |

Each attack emits events the frontend reacts to: node states change, threat level climbs, IDS alerts fire, and automated defenses respond.

### Node state machine

```text
healthy → probing → stressed → compromised → encrypted → recovering → healthy
                                          ↓
                                       offline
```

| State | Color | Trigger |
| --- | --- | --- |
| `healthy` | Green | Normal operation |
| `probing` | Yellow | Reconnaissance packets detected |
| `stressed` | Orange | High load or active flood |
| `compromised` | Red | Host breached |
| `encrypted` | Purple | Ransomware locked the node |
| `recovering` | Cyan | Mitigation applied, restoring |
| `isolated` | Gray | Segment-isolated by firewall |
| `offline` | Dark | Service unavailable |

---

## Quick start

No API keys required. AI features degrade gracefully without a Gemini key.

> **All `make` commands run from the project root** (`Guardio/`).

### 1 — Install

```bash
cd Guardio
make install
```

Or manually:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm install && cd ..
```

### 2 — Start the backend

```bash
make dev-backend
# or: source .venv/bin/activate && uvicorn backend.main:app --reload --port 8000
```

Backend: `http://localhost:8000` · API docs: `http://localhost:8000/docs`

### 3 — Start the frontend

```bash
make dev-frontend
# or: cd frontend && npm run dev
```

Frontend: `http://localhost:3000`

### 4 — Run it

1. Open `http://localhost:3000`
2. Click **START SIM** — the world map populates with nodes and packet arcs
3. Hit any attack button in the top bar to launch a scenario
4. Navigate between views with the left icon rail
5. Click **STOP SIM** to end the session (events are saved as a replay)

---

## Configuration

Copy `.env.example` to `.env` to override defaults:

```bash
cp .env.example .env
```

| Variable | Default | Description |
| --- | --- | --- |
| `GUARDIO_API_KEY` | `devkey` | API key for control endpoints. Frontend sends this automatically. |
| `GUARDIO_DISABLE_AI` | `false` | Set `true` to stub AI responses without a Gemini key. |
| `GEMINI_API_KEY` | *(none)* | Enables real AI suggestions in the Copilot. Without it, AI returns a stub instead of erroring. |
| `GUARDIO_RATE_LIMIT_PER_MIN` | `120` | HTTP rate limit per IP per minute. |

**Frontend** (`frontend/.env.local`):

| Variable | Default | Description |
| --- | --- | --- |
| `BACKEND_API_URL` | `http://localhost:8000` | Next.js rewrite target for `/api` requests. Set to `https://guardio-production.up.railway.app` when using the Railway backend. |
| `NEXT_PUBLIC_WS_URL` | `ws://localhost:8000/ws` | WebSocket endpoint |
| `NEXT_PUBLIC_API_KEY` | `devkey` | Must match backend `GUARDIO_API_KEY` |

When the backend is deployed on Railway, the frontend should use:

```bash
BACKEND_API_URL=https://guardio-production.up.railway.app
NEXT_PUBLIC_WS_URL=wss://guardio-production.up.railway.app/ws
NEXT_PUBLIC_API_KEY=devkey
```

---

## Docker

```bash
docker compose up --build   # or: make docker-up
```

Starts the backend on port `8000` and Prometheus on `9090`. Start the frontend separately:

```bash
cd frontend && npm install && npm run dev
```

---

## Architecture

```text
frontend/                   Next.js 15, React 19, TypeScript
  src/
    app/                    Root layout + page
    components/
      layout/               AppShell, TopBar, Sidebar
      map/                  ThreatMap — world map + animated arcs
      graph/                InfraGraph — SVG force-directed layout
      timeline/             IncidentTimeline — forensic event log
      intelligence/         ThreatIntelPanel — charts + IDS table
      ai/                   AICopilot — Gemini chat interface
      shared/               AlertFeed (log stream), LiveMetrics
    store/                  Zustand — nodes, arcs, alerts, incidents
    lib/                    WebSocket client with exponential backoff
    types/                  All WebSocket event type definitions

backend/
  main.py                   FastAPI app — all HTTP + WebSocket routes
  simulation.py             Attack engine + 120ms background tick
  topology.py               108-node geo-aware network
  ws_manager.py             Per-client async queue broadcaster
  ids.py                    Sliding-window rate IDS scorer
  defense.py                Firewall blocks, segments, honeypots
  replay.py                 In-memory + SQLite replay store
  db.py                     SQLite with thread-local connections
  auth.py                   Constant-time HMAC API key check
  AI/gemini.py              Gemini 2.0 Flash wrapper + circuit breaker
```

### WebSocket event stream

All events arrive on `ws://localhost:8000/ws`. The frontend connects automatically.

| Event | Description |
| --- | --- |
| `state` | Initial topology snapshot on connect, sim on/off |
| `packet` | Network packet with src/dst lat-lng, protocol, color |
| `attack` | Attack lifecycle: `start` → `update` (phases) → `end` |
| `node_update` | State machine transition with load and coordinates |
| `threat_level` | Global threat 0–100 with status label |
| `alert` | IDS detection — score, level (medium/high/critical), reason |
| `defense_action` | Automated mitigation (quarantine, rate-limit, BGP blackhole…) |
| `dropped` | Packet blocked by firewall or segment isolation |
| `honeypot` | Honeypot triggered |
| `infra_telemetry` | Periodic load metrics for sampled nodes |

---

## API reference

Control endpoints require `X-Api-Key: devkey`. Read-only endpoints have no auth.

```text
POST /start                         Start simulation
POST /stop                          Stop, returns replay_id
POST /attack  { "name": "ddos" }    Launch named attack
GET  /simulation/status
GET  /topology

POST   /defense/firewall/block    { "host": "host-1" }
POST   /defense/firewall/unblock  { "host": "host-1" }
POST   /defense/segment           { "name": "prod", "hosts": ["srv-1"] }
DELETE /defense/segment/{name}
POST   /defense/honeypot          { "host": "iot-3" }
GET    /defense/status

GET  /replays
GET  /replay/{id}

POST /ai/suggest        { event object }
POST /ai/summarize/{id}
POST /AI/generate       { "prompt": "..." }

GET /health
GET /status
GET /metrics
GET /metrics/prometheus
```

---

## Development

```bash
make test      # pytest suite (33 tests)
make lint      # mypy type check
make clean     # remove venv + cache
make help      # all targets
```

---

## Tech stack

**Backend:** Python 3.11 · FastAPI · uvicorn · asyncio · SQLite · google-genai · prometheus-client

**Frontend:** Next.js 15 · React 19 · TypeScript · Tailwind CSS v3 · Zustand · Framer Motion · react-simple-maps · recharts · d3-geo

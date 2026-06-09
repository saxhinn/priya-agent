# Priya — AI Outbound Voice Agent 

Priya is an automated AI voice calling agent that:
- Auto-calls Meta ad leads via **Exotel**
- Conducts natural conversations in **Hindi/Marathi**
- Qualifies leads, captures exchange interest, and schedules showroom visits
- Logs all outcomes back to **Google Sheets**

Built for **Shelar TVS**, a TVS bike dealership in Pune.

---

## Architecture

```
Meta Ad Lead
     │
     ▼
Google Sheets (lead row added)
     │
     ▼
n8n Workflow ("Priya - Real-Time Lead Caller")
     │  — detects new row, picks up lead data
     │  — calls Exotel API to initiate outbound call
     ▼
Exotel
     │  — dials customer
     │  — connects WebSocket to agent.py
     ▼
agent.py  ←──────────────────────────────────────────────────┐
     │  — receives 8kHz PCM audio chunks                       │
     │  — VAD (silence detection) to detect end-of-speech     │
     │  — Sarvam STT (saaras:v3) → transcript                 │
     │  — GPT-4o-mini intent classifier                        │
     │  — conversation state machine (Greet→Exchange→Visit…)  │
     │  — Sarvam TTS (bulbul:v3) → audio sent back           │
     └──────────────────────── logs result to Google Sheets ──┘
```

---

## Stack

| Component | Technology |
|-----------|-----------|
| Telephony | Exotel (WebSocket streaming) |
| STT | Sarvam AI `saaras:v3` |
| TTS | Sarvam AI `bulbul:v3` |
| Intent classification | OpenAI `gpt-4o-mini` |
| Orchestration | n8n |
| Lead data | Google Sheets |
| Tunneling (local dev) | ngrok (static domain) |

---

## Files

| File | Description |
|------|-------------|
| `agent.py` | Core WebSocket voice agent (v5.3) |
| `sheet_logger.py` | Google Sheets call outcome logger |
| `test_local.py` | Local test harness (no Exotel needed) |
| `priya_n8n_workflow.json` | n8n workflow export — import this into your n8n instance |
| `setup.md` | Full infrastructure setup guide |
| `PROJECT_BRIEF.md` | Project overview and design decisions |

---

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/YOUR_USERNAME/priya-agent.git
cd priya-agent
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

Required keys:
- `OPENAI_API_KEY` — OpenAI (for intent classification)
- `SARVAM_API_KEY` — Sarvam AI (STT + TTS)
- `GSHEET_ID` — your Google Sheet ID
- `GSHEET_CREDS` — path to your service account JSON (see `setup.md`)

### 3. Start the agent

```bash
python agent.py
# Listens on port 5000 by default
```

### 4. Expose via ngrok (local dev)

```bash
ngrok http --domain=YOUR_STATIC_DOMAIN.ngrok-free.app 5000
```

### 5. Import n8n workflow

Import `priya_n8n_workflow.json` into your n8n instance and re-link credentials (Google Sheets OAuth).

---

## Conversation Flow

```
Greet (by name + bike)
    │
    ▼
Exchange interest? (purana bike hai?)
    │
    ├─ Yes → note exchange interest
    └─ No  → continue
    │
    ▼
Showroom visit? (showroom aa sakte ho?)
    │
    ├─ Yes → capture day + time
    └─ No  → polite close
    │
    ▼
Confirm + Close
```

---

## Key Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVER_PORT` | `5000` | WebSocket server port |
| `SILENCE_THRESH` | `500` | VAD silence threshold (RMS) |
| `ECHO_COOLDOWN` | `0.8` | Seconds to ignore audio after TTS playback |
| `TTS_PACE` | `1.20` | TTS speech rate |
| `GREETING_LEAD_IN` | `1.3` | Silence before first greeting (seconds) |

---

## Google Sheets Setup

See `setup.md` for full instructions. Required sheet tabs:
- `ShelarTVS_Bikes_General_LeadForm_v2` — main lead sheet (with `priya_*` columns AE–AM)
- `priya_call_queue` — QUERY mirror of leads awaiting calls
- `priya_call_log` — per-call outcome log
- `priya_dial_log` — Exotel dial attempts log

---

## Deployment

For production, deploy `agent.py` to a cloud VM in India for low latency:
- AWS `ap-south-1` (Mumbai)
- GCP `asia-south1` (Mumbai)
- DigitalOcean Bangalore

Replace ngrok with the VM's public IP in your Exotel applet configuration.

---

## License

MIT

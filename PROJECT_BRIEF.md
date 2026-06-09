# Shelar TVS — Priya Voice Agent — Project Brief

**Last updated:** June 1, 2026
**Owner:** Haemoglobbinn (digital marketing agency, Pune)
**Client:** Shelar TVS (multi-branch TVS motorcycle dealership, Pune)
**Project codename:** Priya v5.3 (lead-aware, n8n-triggered)

---

## What this project is

An automated AI telecaller named **Priya** that calls inbound Meta lead-ad leads for Shelar TVS, qualifies them on a 5-stage conversation flow in Hindi (auto-switches to Marathi), and logs results to a Google Sheet for the sales team to follow up.

The full pipeline: **Meta lead form → LeadSync → Google Sheet → n8n (5min wait) → Exotel Connect API → Voicebot Applet → Priya Python server (Sarvam STT/TTS + OpenAI GPT) → Google Sheet log.**

---

## Current status (as of session end)

- ✅ `agent.py` v5.3 written, syntax verified, 8/8 simulation paths pass
- ✅ `sheet_logger.py` v5.3 (extended fields) written, fails safely if not configured
- ✅ `priya_n8n_workflow.json` written, JSON validated
- ✅ Agent running cleanly on user's Windows machine (Python 3.14, venv)
- ✅ ngrok URL stable: `wss://resolute-oyster-quartered.ngrok-free.dev`
- ✅ Exotel credentials confirmed (key, token, SID, exophone, applet ID)
- ⏳ User needs to: create `priya_call_queue` + `priya_call_log` tabs in Google Sheet, paste mirror formula, set up n8n credentials, set up Google service account for sheet logging
- ⏳ User has NOT yet made a real test call on v5.3 — first call test is pending

---

## Tech stack

| Layer | Tech |
|---|---|
| Phone telephony | Exotel (SIP/voicebot applet) |
| Speech-to-text | Sarvam STT (`saaras:v3`) via REST |
| Text-to-speech | Sarvam TTS (`bulbul:v3`, speaker "priya") via REST |
| Brain (intent + extraction) | OpenAI `gpt-4o-mini` |
| Audio pipeline | Python 3.14 asyncio, websockets, audioop-lts, httpx pooled |
| Server | Python WebSocket on port 5000, exposed via ngrok |
| Automation | n8n Cloud (workflow auto-dials new leads) |
| Lead pipeline | Meta lead ads → LeadSync → Google Sheet |
| Result log | gspread → Google Sheet (`priya_call_log` tab) |

---

## Conversation flow (v5.3)

5 stages, each with cached audio for instant playback (greeting is dynamic per-customer, generated at call time):

1. **STAGE_GREETING** — dynamic, uses customer name + bike from form
   `"नमस्कार [name], मैं प्रिया Shelar TVS से. क्या आपने [bike] के लिए enquiry की थी?"`
2. **STAGE_EXCHANGE** — yes/no on exchange offer
3. **STAGE_EXCHANGE_BIKE** — if yes to 2, ask which old bike (GPT extracts model)
4. **STAGE_SHOWROOM** — yes/no on showroom visit
5. **STAGE_VISIT_TIME** — if yes to 4, ask day+time (GPT extracts)

**Off-ramps at every stage:** `wrong_enquiry`, `busy`, `not_interested`, `asking_price` (deflects then continues), `offtopic` (1 redirect, then close).

---

## File inventory

| File | Purpose | Where it lives |
|---|---|---|
| `agent.py` v5.3 | Main bot — WebSocket server, audio pipeline, conversation routing | `priya-v3/agent.py` |
| `sheet_logger.py` v5.3 | Google Sheets logger (gspread) — fails safely if not configured | `priya-v3/sheet_logger.py` |
| `agent_v51.py` | v5.1 fallback (if v5.3 has issues) | `priya-v3/agent_v51.py` |
| `priya_n8n_workflow.json` | n8n workflow JSON | Import to n8n |
| `test_local.py` | Local test harness (no Exotel needed) | `priya-v3/test_local.py` |
| `setup.md` | Step-by-step deployment guide | Reference |
| `.env` | Secrets + config | `priya-v3/.env` |
| `gsheet_credentials.json` | Google service account key (TO BE CREATED) | `priya-v3/gsheet_credentials.json` |

---

## Critical config (`.env`)

```
OPENAI_API_KEY=sk-...
SARVAM_API_KEY=sk_...
SARVAM_VOICE=priya
TTS_PACE=1.20
TTS_TEMP=0.85
GPT_MODEL=gpt-4o-mini
GREETING_LEAD_IN=1.3
GSHEET_ID=1o7521binEtS76d-DMmbQ7ItNJbwSUyXPRlUmbFkLPFw
GSHEET_CREDS=gsheet_credentials.json
GSHEET_TAB=priya_call_log
```

---

## Exotel credentials (rotate after public exposure)

- **API Key:** `95abb5be9b148dae5ada0033c619ce9d659bfb2e9e119b7f`
- **API Token:** `6b039b09415520b0671310ebeb457229ecac98c0644f34cf`
- **SID:** `shelartvs1`
- **Exophone CallerId:** `09513886363`
- **Voicebot Applet ID:** `1255817` (must point to ngrok WebSocket)

**Security note:** These were shared in chat; rotate after launch.

---

## Google Sheet structure

**Sheet ID:** `1o7521binEtS76d-DMmbQ7ItNJbwSUyXPRlUmbFkLPFw`

| Tab | Purpose | Source |
|---|---|---|
| `ShelarTVS_Bikes_General_LeadForm_v1` | Original LeadSync output — DO NOT TOUCH | LeadSync (auto-populated) |
| `priya_call_queue` | Clean mirror of leads (filters out test rows) — n8n watches this | Sheet formula |
| `priya_call_log` | Call results, one row per call | `sheet_logger.py` (auto-populated) |

Original tab columns: `A=id, B=created_time, K=is_organic, L=platform, M=branch, N=bike, O=when_buying, P=exchange_yn, Q=payment_pref, R=exchange_bike, S=full_name, T=phone (with p: prefix), U=lead_status`

**Mirror formula** (paste in `priya_call_queue` A1):
```
={"lead_id","created_at","is_organic","platform","branch","bike","when_buying","exchange_yn","payment_pref","exchange_bike_form","full_name","phone","lead_status";IFERROR(QUERY(ShelarTVS_Bikes_General_LeadForm_v1!A2:U,"SELECT A, B, K, L, M, N, O, P, Q, R, S, T, U WHERE A IS NOT NULL AND K = FALSE AND T IS NOT NULL", 0), {"","","","","","","","","","","","",""})}
```

---

## n8n workflow design

5 nodes connected sequentially:

1. **Google Sheets Trigger** — watches `priya_call_queue` for new rows (1-min poll)
2. **Code: Clean & Validate Lead** — strips `p:` prefix from phone, normalizes to `+91...` format
3. **Wait** — 5 minutes hard delay
4. **HTTP Request: Trigger Exotel Call** — POSTs to Exotel Connect API with custom parameters
5. **Code: Log Dial Attempt** — for audit only

**Custom parameters passed to Exotel → Priya:** `customer_name`, `bike`, `branch`

**Not implemented (intentional):**
- Concurrency cap (Exotel rate-limits; 25 leads/day doesn't need it)
- Retry on no-answer (user said "stop after 1")

---

## Key technical decisions made along the way

1. **Cached fixed lines + dynamic greeting** — fixed lines pre-rendered at startup (~50s), instant playback; greeting TTS'd per call (different name/bike).
2. **Hybrid classifier** — keyword `fast_classify()` handles 63-74% of replies in 0ms; GPT only for ambiguous (~25%). Saves ~1.5s per turn.
3. **Sequential REST architecture (not streaming)** — chose ship-now over rebuild. Pipecat / Sarvam WebSocket streaming flagged for future client #2.
4. **Sheets logging fails safely** — agent runs fine without sheet credentials configured. Logs warning, continues.
5. **Defensive extraction fallbacks** — if GPT can't parse bike name or visit time on first try, retry once; if still fails, accept raw text rather than trapping caller.
6. **Sarvam bulbul:v3 returns 22050Hz despite request for 8kHz** — must resample on receive (was making Priya play at 36% speed).
7. **`speak_done_at`** = time + remaining playback duration (not when sending finishes) — prevents capturing Priya's tail as caller speech.
8. **Garble filter strengthened to catch n-gram repetition** — Sarvam STT occasionally hallucinates "में भोतों" repeated 40 times.

---

## Known limits / things to watch

- **STT accuracy on Hindi** is the main source of misroutes — Sarvam occasionally mistranscribes real "हाँ" answers as "नहीं" or invents words. Not fixable in our code; needs Sarvam improvements or model change.
- **Echo on speakerphone** — handled via text-based heuristic (`is_echo()`), works for typical cases but not perfect. Real fix is software AEC (deferred).
- **~3-second per-turn latency** is the architectural floor for REST-based pipeline. Sub-1-second requires Pipecat rewrite (4-6 hour build).
- **n8n cloud free trial is 14 days** — user needs to convert to paid (~₹200/mo) or self-host on Render before trial ends.

---

## What to do in the next session

When resuming this work, the agent should:
1. Read this brief first
2. Check `agent.py` for the current version (v5.3 baseline)
3. If user is reporting issues, ask for terminal logs / sheet screenshots before guessing
4. If user wants to add features (new client bot, new flow), use this codebase as the proven template
5. If user wants the streaming/Pipecat rewrite, that's a fresh 4-6 hour build — scope it before coding

**Do NOT regenerate the bike normalization dicts, the GPT prompts, the conversation routing logic, or the audio pipeline from scratch.** They're tuned through many real-call iterations. Edit in place.

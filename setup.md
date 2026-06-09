# Priya v5.3 — Setup & Deployment Guide

Read this top-to-bottom before doing anything. The order matters.

---

## Prerequisites (one-time, ~5 min)

- Windows / Mac with Python 3.10+ installed
- A working `priya-v3/` folder with `venv` set up
- ngrok account (free tier OK)
- Exotel account (Big plan or any active plan)
- n8n Cloud account (sign up at https://n8n.cloud — free trial)
- Google account that owns your lead sheet

---

## Step 1 — Drop the new files in place

In your `priya-v3/` folder, replace these:
- `agent.py` (v5.3)
- `sheet_logger.py`

Keep these as backups (don't delete):
- `agent_v51.py` (fallback if v5.3 breaks)

---

## Step 2 — Update `.env`

Open `priya-v3/.env` and ensure these lines exist:

```env
OPENAI_API_KEY=sk-...your-key...
SARVAM_API_KEY=sk_...your-key...
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

## Step 3 — Install gspread (if not already)

```bash
pip install gspread
```

---

## Step 4 — Set up Google service account (for sheet logging)

1. Go to https://console.cloud.google.com/
2. Create new project: `priya-bot`
3. Enable APIs: **Google Sheets API** and **Google Drive API** (search & enable each)
4. Navigate to: APIs & Services → Credentials → Create credentials → Service account
5. Name: `priya-bot-writer` → Done
6. Click the new service account → Keys tab → Add Key → Create new key → JSON
7. A JSON file downloads. **Rename it to `gsheet_credentials.json`** and put it in `priya-v3/`
8. Open the JSON, find `"client_email"` (looks like `priya-bot-writer@priya-bot-XXXX.iam.gserviceaccount.com`). **Copy it.**

---

## Step 5 — Set up Google Sheet tabs

Open your sheet: https://docs.google.com/spreadsheets/d/1o7521binEtS76d-DMmbQ7ItNJbwSUyXPRlUmbFkLPFw

**Share access:**
- Click "Share" (top right) → paste the service account email from Step 4.8 → give it **Editor** access → Send.

**Create the two new tabs:**

1. At the bottom of the sheet, click the `+` button to add a new tab.
2. Double-click the new tab name and rename to: `priya_call_queue`
3. Click cell A1 of this new tab and paste this formula:

```
={"lead_id","created_at","is_organic","platform","branch","bike","when_buying","exchange_yn","payment_pref","exchange_bike_form","full_name","phone","lead_status";IFERROR(QUERY(ShelarTVS_Bikes_General_LeadForm_v1!A2:U,"SELECT A, B, K, L, M, N, O, P, Q, R, S, T, U WHERE A IS NOT NULL AND K = FALSE AND T IS NOT NULL", 0), {"","","","","","","","","","","","",""})}
```

4. Press Enter. You should see ~25 clean rows populate.

5. Click `+` again to add another tab. Rename to: `priya_call_log`. Leave it empty.

---

## Step 6 — Start agent.py

In terminal, in `priya-v3/`:

```bash
python agent.py
```

You should see:
```
Shelar TVS — Priya v5.3 (lead-aware, n8n-triggered)
...
cached 30 fixed lines in ~50s
Ready. Waiting for Exotel calls...
```

Plus one of:
- `Sheets logging ENABLED → priya_call_log` ✓ (good — Step 4 worked)
- `Sheets logging DISABLED: ...` (still fine, but Step 4 not complete)

Leave this terminal running.

---

## Step 7 — Start ngrok

In a NEW terminal:

```bash
ngrok http 5000
```

If your URL is the persistent free-tier one (`resolute-oyster-quartered.ngrok-free.dev`), great. If not, copy the new HTTPS URL.

---

## Step 8 — Verify Exotel voicebot applet points to your ngrok

Log into Exotel dashboard → App Bazaar → find Voicebot Applet `1255817` → URL field should be:
```
wss://resolute-oyster-quartered.ngrok-free.dev
```

If different, update it and save.

---

## Step 9 — Set up n8n workflow

1. Sign in to https://n8n.cloud
2. Workflows → Import from File → upload `priya_n8n_workflow.json`
3. You'll see 5 nodes connected.

**Set up Google Sheets credential:**
- Click "New Lead Row" node
- Credentials section → Create new → Sign in with Google → use the same account that owns the sheet
- Save

**Set up Exotel HTTP Basic Auth credential:**
- Click "Trigger Exotel Call" node
- Authentication: Generic Credential → HTTP Basic Auth → Create new
  - User: `95abb5be9b148dae5ada0033c619ce9d659bfb2e9e119b7f` (API key)
  - Password: `6b039b09415520b0671310ebeb457229ecac98c0644f34cf` (API token)
  - Name: `Exotel API Auth`
- Save

**DO NOT activate yet.**

---

## Step 10 — Test call (on YOUR phone first)

This is the critical test before going live. Test on yourself, not a real lead.

1. In your lead sheet's original tab (`ShelarTVS_Bikes_General_LeadForm_v1`), add a test row at the bottom. Fill columns:
   - A (id): test_001
   - K (is_organic): `FALSE`
   - M (branch): kothrud
   - N (bike): tvs_jupiter
   - S (full_name): YourFirstName
   - T (phone): `p:+91...your-phone...`

2. The mirror formula in `priya_call_queue` should pick it up instantly.

3. In n8n, click "Execute Workflow" (manual trigger) so the workflow runs once.

4. Wait 5 minutes. Your phone should ring with Priya speaking.

5. Test the conversation. Verify it ends cleanly.

6. Check the `priya_call_log` tab — should have a new row with your call data.

---

## Step 11 — Go live

If Step 10 worked:
- In n8n, toggle the workflow to **Active** (top right)
- From now on, every new lead in the sheet triggers a call after 5 minutes

If Step 10 didn't work:
- Don't activate
- Check the terminal where `agent.py` is running — what error?
- Check n8n execution log — what failed?
- Roll back to `agent_v51.py` if needed (rename it to `agent.py` and restart)

---

## Daily operations

- Keep `agent.py` and `ngrok` running (use Windows Task Scheduler or PM2 to auto-restart)
- Monitor `priya_call_log` tab daily for new qualified leads
- Filter `outcome = completed` AND `exchange_interested = yes` AND `can_visit_showroom = yes` for hottest leads

---

## Troubleshooting

| Symptom | Probable cause | Fix |
|---|---|---|
| Agent crashes on startup with `audioop` error | Python 3.13+ removed builtin | `pip install audioop-lts` |
| Sheets log row not appearing | Service account doesn't have access | Re-share sheet with service account email as Editor |
| Calls trigger in n8n but never ring | Exotel applet not pointing at ngrok | Update applet URL in Exotel dashboard |
| Calls ring but Priya silent | ngrok URL changed (free tier rotates) | Restart ngrok, update applet URL |
| Priya talks but caller voice not picked up | Sarvam STT key issue or Exotel media not arriving | Check terminal for "STT" log lines per turn |
| Bot misroutes real "yes" as "no" | Sarvam STT misheard caller | Not fixable in code; mention in log review |

---

## Rotating secrets after launch

The Exotel API key + token were shared in chat. **Rotate them after first successful launch:**

1. Exotel dashboard → Settings → API Settings → Regenerate
2. Update n8n HTTP Basic Auth credential with new values
3. Update this guide

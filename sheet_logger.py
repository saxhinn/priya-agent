#!/usr/bin/env python3
"""
Google Sheets logger for Priya call results.

Writes one row per completed call. Fails SAFELY — if gspread isn't installed,
credentials are missing, or the network is down, it logs the error but does
NOT crash the agent. The agent keeps working even if sheets logging breaks.

SETUP (one-time, ~5 minutes):
  1. Go to https://console.cloud.google.com/
  2. Create a new project (or pick existing). Name it e.g. "priya-bot".
  3. Enable two APIs for that project:
     - Google Sheets API
     - Google Drive API
  4. Go to "Credentials" → "Create credentials" → "Service account"
     - Name: priya-bot-writer
     - Skip the optional steps, click "Done"
  5. Click on the new service account → "Keys" tab → "Add Key" → "Create new key"
     - Type: JSON  → Create
     - A JSON file downloads. Rename it to: gsheet_credentials.json
     - Move it to your priya-v3 folder (next to agent.py).
  6. Open the JSON. Find "client_email" — looks like
     priya-bot-writer@priya-bot-xxxxx.iam.gserviceaccount.com
     COPY that email.
  7. Create a new Google Sheet (or use existing). Click "Share". Paste the
     service account email, give it "Editor" access. Send.
  8. Copy the Sheet ID from the URL:
     https://docs.google.com/spreadsheets/d/<THIS_PART>/edit
  9. Add to your .env:
       GSHEET_ID=<the id you copied>
       GSHEET_CREDS=gsheet_credentials.json
       GSHEET_TAB=Sheet1
 10. Install gspread:  pip install gspread

That's it. Priya will start appending rows on every call.

COLUMNS WRITTEN (one row per call):
  timestamp | call_sid | caller_number | duration_sec | language |
  enquiry_confirmed | exchange_interested | can_visit_showroom | outcome
"""
import os, time, logging, threading
from datetime import datetime

log = logging.getLogger("priya-sheets")

GSHEET_ID    = os.getenv("GSHEET_ID", "")
GSHEET_CREDS = os.getenv("GSHEET_CREDS", "gsheet_credentials.json")
GSHEET_TAB   = os.getenv("GSHEET_TAB", "Sheet1")

_worksheet = None
_init_failed = False
_lock = threading.Lock()


def _init():
    """Open the worksheet once. Returns None on any failure (logs the reason)."""
    global _worksheet, _init_failed
    if _worksheet is not None: return _worksheet
    if _init_failed: return None

    if not GSHEET_ID:
        log.info("Sheets logging DISABLED (GSHEET_ID not set in .env)")
        _init_failed = True; return None

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        log.warning("Sheets logging DISABLED: gspread not installed. Run: pip install gspread")
        _init_failed = True; return None

    if not os.path.exists(GSHEET_CREDS):
        log.warning(f"Sheets logging DISABLED: credentials file '{GSHEET_CREDS}' not found")
        _init_failed = True; return None

    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(GSHEET_CREDS, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(GSHEET_ID)
        try:
            ws = sh.worksheet(GSHEET_TAB)
        except Exception:
            # Tab doesn't exist — create it
            ws = sh.add_worksheet(title=GSHEET_TAB, rows=1000, cols=10)
        # If sheet is empty, write header row
        try:
            first_row = ws.row_values(1)
        except Exception:
            first_row = []
        if not first_row:
            ws.append_row(["timestamp","call_sid","caller_number","duration_sec",
                           "language","customer_name","bike_form","branch_form",
                           "enquiry_confirmed","exchange_interested","exchange_bike",
                           "can_visit_showroom","visit_day","visit_time","outcome"])
        _worksheet = ws
        log.info(f"Sheets logging ENABLED → {GSHEET_TAB} (sheet {GSHEET_ID[:12]}...)")
        return ws
    except Exception as e:
        log.error(f"Sheets init failed: {e}")
        _init_failed = True
        return None


def log_call(*, call_sid, caller_number, duration_sec, language,
             enquiry_confirmed, exchange_interested, can_visit_showroom,
             outcome,
             # v5.3 extended fields (optional, default empty so v5.0 callers still work)
             customer_name="", bike_form="", branch_form="",
             exchange_bike="", visit_day="", visit_time=""):
    """Append one row. Fails silently (logs error) — never crashes the agent."""
    with _lock:
        ws = _init()
        if ws is None:
            return  # disabled or init failed; agent continues normally
        try:
            row = [
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                call_sid or "",
                caller_number or "",
                round(duration_sec, 1),
                language or "",
                customer_name or "",
                bike_form or "",
                branch_form or "",
                "" if enquiry_confirmed is None else ("yes" if enquiry_confirmed else "no"),
                "" if exchange_interested is None else ("yes" if exchange_interested else "no"),
                exchange_bike or "",
                "" if can_visit_showroom is None else ("yes" if can_visit_showroom else "no"),
                visit_day or "",
                visit_time or "",
                outcome or "",
            ]
            ws.append_row(row, value_input_option="USER_ENTERED")
            log.info(f"Sheets row logged: {caller_number} | {outcome} | name={customer_name}")
        except Exception as e:
            log.error(f"Sheets append failed: {e}")

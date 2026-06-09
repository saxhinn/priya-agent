#!/usr/bin/env python3
"""
Shelar TVS — Priya Voice Agent v5.3  (lead-aware, n8n-triggered)
═══════════════════════════════════════════════════════════════
New in v5.3:
  • Lead-aware: greets customer by NAME, mentions their chosen BIKE & BRANCH
    (passed by n8n → Exotel custom_parameters → us)
  • New flow: Greet → Exchange Y/N → Showroom Y/N → (if yes) Visit Day+Time → Close
  • Visit day/time captured as free-text via GPT extraction
  • Off-topic redirect once, then polite close
  • Price-question deflection: "executive will share details"
  • Hybrid classifier kept (fast keyword + GPT fallback)
  • Google Sheet logging extended with: customer_name, bike, branch, visit_day, visit_time
"""
import asyncio, websockets, json, logging, base64, time, os, struct, audioop, httpx, re
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("priya-v53")

try:
    import sheet_logger
except Exception as _e:
    sheet_logger = None
    log.info(f"sheet_logger not loaded: {_e}")

HTTP: Optional["httpx.AsyncClient"] = None

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
SARVAM_VOICE   = os.getenv("SARVAM_VOICE", "priya")
SERVER_HOST    = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT    = int(os.getenv("SERVER_PORT", "5000"))
GPT_MODEL      = os.getenv("GPT_MODEL", "gpt-4o-mini")

TARGET_SR      = 8000
BYTES_PER_SEC  = TARGET_SR * 2
SILENCE_THRESH = 1000
SILENCE_SECS   = 0.45
MIN_AUDIO_SECS = 0.5
ECHO_COOLDOWN  = 0.8
TTS_PACE       = float(os.getenv("TTS_PACE", "1.20"))
TTS_TEMP       = float(os.getenv("TTS_TEMP", "0.85"))
TTS_MODEL      = os.getenv("TTS_MODEL", "bulbul:v3")
MAX_RETRIES    = 3
MAX_OFFTOPIC   = 1  # redirect once, then close

GREETING_LEAD_IN = float(os.getenv("GREETING_LEAD_IN", "1.3"))
LEAD_SILENCE     = b'\x00\x00' * int(TARGET_SR * 0.4)


# ── Bike name normalization (form values → human-readable) ──────────────────
BIKE_DISPLAY = {
    "tvs_jupiter":    "TVS Jupiter",
    "tvs_sport":      "TVS Sport",
    "tvs_star_city":  "TVS StaR City Plus",
    "tvs_radeon":     "TVS Radeon",
    "tvs_ronin":      "TVS Ronin",
    "tvs_raider":     "TVS Raider",
    "apache_rr_310":  "Apache RR 310",
    "apache_rtr_310": "Apache RTR 310",
    "rtr_180":        "Apache RTR 180",
    "rtr_160_4v":     "Apache RTR 160 4V",
    "rtr_160":        "Apache RTR 160",
    "rtr_200_4v":     "Apache RTR 200 4V",
    "rr_310":         "Apache RR 310",
}
BRANCH_DISPLAY = {
    "swargate": "Swargate", "balaji_nagar": "Balaji Nagar", "kothrud": "Kothrud",
    "sinhgad_road": "Sinhgad Road", "paud_road": "Paud Road", "kondhwa": "Kondhwa",
    "narhe": "Narhe", "kharadi": "Kharadi",
}

def bike_label(raw):
    if not raw: return "TVS bike"
    key = raw.lower().strip().replace(" ", "_")
    return BIKE_DISPLAY.get(key, raw.replace("_", " ").title())

def branch_label(raw):
    if not raw: return ""
    key = raw.lower().strip().replace(" ", "_")
    return BRANCH_DISPLAY.get(key, raw.replace("_", " ").title())

def first_name(full):
    if not full: return "सर"
    full = full.strip()
    if not full: return "सर"
    return full.split()[0]


# ── Cached fixed lines ──────────────────────────────────────────────────────
# NOTE: greeting is now DYNAMIC (per-customer), cannot pre-cache.
# Everything else is fixed and cached.

Q_EXCHANGE = {
    "hindi":   "जी सर, क्या आप exchange offer में interested हैं?",
    "marathi": "जी सर, तुम्ही exchange offer मध्ये interested आहात का?",
}
Q_EXCHANGE_BIKE = {
    "hindi":   "Great सर, exchange के लिए आपके पास कौनसी पुरानी two-wheeler है?",
    "marathi": "छान सर, exchange साठी तुमच्याकडे कुठली जुनी two-wheeler आहे?",
}
Q_NO_EXCHANGE_ACK = {
    "hindi":   "ठीक है सर, no problem.",
    "marathi": "ठीक आहे सर, काही हरकत नाही.",
}
Q_SHOWROOM = {
    "hindi":   "क्या आप अपने nearest Shelar TVS showroom visit कर सकते हैं?",
    "marathi": "तुम्ही तुमच्या nearest Shelar TVS showroom ला visit करू शकता का?",
}
Q_VISIT_TIME = {
    "hindi":   "Perfect सर, आप कौनसे दिन visit कर सकते हैं? और कौनसे time पर?",
    "marathi": "Perfect सर, तुम्ही कुठल्या दिवशी visit करू शकता? आणि कुठल्या वेळी?",
}
VISIT_NOTED = {
    "hindi":   "ठीक है सर, मैंने आपका preferred visit time note कर लिया है.",
    "marathi": "ठीक आहे सर, मी तुमची preferred visit वेळ note केली आहे.",
}
CLOSE_LINES = {
    "hindi":   "धन्यवाद सर. आपको Shelar TVS executive से जल्द ही call आ जाएगा. Have a good day.",
    "marathi": "धन्यवाद सर. तुम्हाला Shelar TVS executive कडून लवकरच call येईल. Have a good day.",
}
NO_VISIT_CLOSE = {
    "hindi":   "ठीक है सर, no problem. हमारा Shelar TVS executive आपको call करके offer और vehicle details share करेगा. धन्यवाद सर, Have a good day.",
    "marathi": "ठीक आहे सर, काही हरकत नाही. आमचा Shelar TVS executive तुम्हाला call करून offer आणि vehicle details share करेल. धन्यवाद सर, Have a good day.",
}
WRONG_ENQUIRY_LINES = {
    "hindi":   "ठीक है सर, कदाचित enquiry mistake से submit हुई होगी. Sorry for the disturbance. धन्यवाद सर, Have a good day.",
    "marathi": "ठीक आहे सर, बहुतेक enquiry चुकून submit झाली असेल. Sorry for the disturbance. धन्यवाद सर, Have a good day.",
}
BUSY_LINES = {
    "hindi":   "जी बिल्कुल सर, मैं समझ सकती हूँ. हमारी team आपको बाद में call कर लेगी सर. धन्यवाद.",
    "marathi": "जी अगदी बरोबर सर, मी समजू शकते. आमची team तुम्हाला नंतर call करेल सर. धन्यवाद.",
}
NOT_INTERESTED_LINES = {
    "hindi":   "जी ठीक है सर, बिल्कुल कोई बात नहीं. आपका time देने के लिए धन्यवाद सर.",
    "marathi": "जी ठीक आहे सर, अजिबात हरकत नाही. तुमचा वेळ दिल्याबद्दल धन्यवाद सर.",
}
NO_RESPONSE_LINES = {
    "hindi":   "सर, शायद आवाज़ clear नहीं आ रही है. हमारी team आपको दोबारा call करेगी. धन्यवाद सर.",
    "marathi": "सर, बहुतेक आवाज clear येत नाहीये. आमची team तुम्हाला परत call करेल सर. धन्यवाद.",
}
REPEAT_LINES = {
    "hindi":   "सर माफ़ कीजिये, थोड़ा सा दोबारा बता दीजिये.",
    "marathi": "सर माफ करा, थोडंसं पुन्हा सांगाल का.",
}
PRICE_DEFLECT = {
    "hindi":   "सर, exact price और offer details branch और variant के हिसाब से change हो सकते हैं. इसलिए हमारे executive आपको सही details share करेंगे.",
    "marathi": "सर, exact price आणि offer details branch आणि variant नुसार change होऊ शकतात. म्हणून आमचे executive तुम्हाला योग्य details share करतील.",
}
REDIRECT_LINES = {
    "hindi":   "सर, मैं सिर्फ आपकी TVS enquiry confirm करने के लिए call कर रही हूँ. Shelar TVS executive आपको पूरी details के लिए call करेगा.",
    "marathi": "सर, मी फक्त तुमची TVS enquiry confirm करण्यासाठी call केली आहे. Shelar TVS executive तुम्हाला पूर्ण details साठी call करेल.",
}


def build_dynamic_greeting(name, bike, lang):
    """Build the personalized greeting (cannot pre-cache; different per customer)."""
    n = first_name(name)
    b = bike_label(bike)
    if lang == "marathi":
        return f"नमस्कार {n}, मी प्रिया बोलतेय Shelar TVS कडून. तुम्ही {b} साठी enquiry केली होती ना?"
    return f"नमस्कार {n}, मैं प्रिया बोल रही हूँ Shelar TVS से. क्या आपने {b} के लिए enquiry की थी?"


# ── GPT prompts ─────────────────────────────────────────────────────────────
CLASSIFY_PROMPT = """You classify a customer's spoken reply during an outbound TVS dealership lead-qualification call.

The previous question Priya asked was at stage: {stage}
Stages:
- GREETING: confirmed they enquired about the bike (waiting for yes/no)
- Q_EXCHANGE: asked if interested in exchange offer (waiting for yes/no)
- Q_EXCHANGE_BIKE: asked which old bike they have (waiting for a bike name in any form)
- Q_SHOWROOM: asked if they can visit nearest showroom (waiting for yes/no)
- Q_VISIT_TIME: asked which day and time to visit (waiting for free-text day/time)

Customer's reply: "{reply}"

Classify as ONE of:
- "yes" — affirmed (हाँ/जी/हो/correct/yes/sure/ok/अच्छा/bilkul)
- "no" — refused (नहीं/नही/no/नको/not needed)
- "wrong_enquiry" — they didn't enquire / wrong number (only meaningful at GREETING)
- "busy" — they're busy, call later
- "not_interested" — they want to end the call entirely
- "asking_price" — they're asking about price/discount/EMI/offer details
- "offtopic" — they're talking about something irrelevant (not answering, not yes/no, not price, just chatter)
- "data" — they're providing the FREE-TEXT answer expected at this stage (a bike name at Q_EXCHANGE_BIKE, a day/time at Q_VISIT_TIME)
- "unclear" — garbled / can't tell

Return ONLY JSON: {{"intent":"<one of above>"}}"""

EXTRACT_VISIT_TIME_PROMPT = """The customer was asked when they can visit the Shelar TVS showroom. Extract day and time from their reply.

Customer said: "{reply}"

Return JSON with:
- "day": the day they mentioned (e.g., "tomorrow", "Saturday", "today evening", "sometime next week") — keep original meaning
- "time": time of day they mentioned (e.g., "11 AM", "evening", "after 6 PM") — empty string if not given
- "is_clear": true if day OR time was given, false if reply is too vague

Reply ONLY with JSON: {{"day":"<...>","time":"<...>","is_clear":true|false}}"""

EXTRACT_BIKE_PROMPT = """Extract the old two-wheeler model the customer mentioned for exchange.

Customer said: "{reply}"

Return JSON with:
- "bike": the bike model + year if given (e.g., "Hero Splendor 2015", "Honda Activa 2018"). 
  Lowercase normalization NOT needed — keep as spoken.
  If no specific bike named, return empty string.

Reply ONLY with JSON: {{"bike":"<...>"}}"""


# ── Audio helpers ───────────────────────────────────────────────────────────
def pcm_rms(b):
    if len(b) < 2: return 0.0
    s = struct.unpack(f'<{len(b)//2}h', b[:len(b)//2*2])
    if not s: return 0.0
    return (sum(x*x for x in s)/len(s))**0.5

def pcm_to_wav(b, sr=8000):
    nc, bps = 1, 16
    br = sr*nc*bps//8; ba = nc*bps//8; ds = len(b)
    h = struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 36+ds, b'WAVE', b'fmt ', 16, 1, nc, sr, br, ba, bps, b'data', ds)
    return h + b

def resample_pcm(b, src, dst):
    if src == dst: return b
    out, _ = audioop.ratecv(b, 2, 1, src, dst, None)
    return out

def parse_wav_sr(b):
    if len(b) < 44 or b[:4] != b'RIFF': return 0
    try: return struct.unpack('<I', b[24:28])[0]
    except: return 0

def trim_trailing_silence(pcm, frame=320, keep_ms=200):
    if len(pcm) < frame * 2: return pcm
    end = len(pcm)
    while end > frame:
        chunk = pcm[end-frame:end]
        try:
            s = struct.unpack(f'<{len(chunk)//2}h', chunk[:len(chunk)//2*2])
            rms = (sum(x*x for x in s)/len(s))**0.5 if s else 0
        except Exception: break
        if rms > 350: break
        end -= frame
    keep = int(TARGET_SR * (keep_ms/1000.0)) * 2
    return pcm[:min(len(pcm), end + keep)]

def is_garbled(text):
    if not text: return True
    c = text.strip()
    if len(c) < 2: return True
    for ul in (2,3,4):
        if len(c) >= ul*4:
            unit = c[:ul]; reps = c.count(unit)
            if reps >= 4 and (reps*ul) > len(c)*0.6: return True
    ns = c.replace(" ","")
    if len(ns) > 12 and len(set(ns)) <= 4: return True
    words = c.split()
    if len(words) >= 6:
        from collections import Counter
        for n in (1, 2, 3):
            ngrams = [" ".join(words[i:i+n]) for i in range(len(words)-n+1)]
            if not ngrams: continue
            _, count = Counter(ngrams).most_common(1)[0]
            if count >= 4 and (count * n) > len(words) * 0.5:
                return True
    return False

def _norm(s):
    return re.sub(r'[^\w]', '', s.lower())

def is_echo(heard, priya_said):
    if not heard or not priya_said: return False
    hw = [w for w in heard.split() if len(w) > 1]
    if len(hw) < 6: return False
    pw = set(_norm(w) for w in priya_said.split() if len(w) > 1)
    hwn = set(_norm(w) for w in hw)
    if not hwn: return False
    return (len(hwn & pw) / len(hwn)) >= 0.7


# ── Fast keyword classifier ─────────────────────────────────────────────────
_YES_WORDS = {"हाँ","हां","हा","जी","ho","haan","han","ha","haa","yes","yeah","yep",
              "yup","ya","yass","yess","ji","correct","right","ok","okay","okk","okie",
              "sure","बिल्कुल","बरोबर","ठीक","अच्छा","हम्म","hmm","hmmm"}
_NO_WORDS = {"नहीं","नही","ना","nahi","nahin","nahein","naa","nah","nai","no","nope","nay","नको"}
_YES_PHRASES = {"जी हाँ","जी हां","ho ho","हो हो","ठीक है","किया था","किया है","की थी","ji haan","ji ha"}
_NO_PHRASES = {"नहीं चाहिए","नही चाहिए","नको आहे"}
_WRONG_PHRASES = {"wrong number","गलत नंबर","wrong call","गलत call","मैंने नहीं किया",
                  "मैंने नहीं की","नहीं की enquiry","did not enquire","didn't enquire",
                  "नहीं enquiry","कोई और","किसी और","i didn't"}
_BUSY_PHRASES = {"busy","बिजी","व्यस्त","बाद में","call later","बाद में call",
                 "meeting में","meeting मे","abhi nahi","abhi nahin","later"}
_NOT_INT_PHRASES = {"not interested","interested नहीं","interested nahin",
                    "नहीं चाहिए मुझे","don't call","do not call","मत करो call","remove me"}
_PRICE_PHRASES = {"price","कीमत","cost","कितने का","kitna","kitne","emi कितनी",
                  "discount","offer","रेट","rate"}

def _tokenize_for_kw(s):
    return re.split(r'[\s।,.!?\-\u0964\u0965]+', s.lower().strip())

def fast_classify(text, stage):
    """Returns confident intent or None. NEVER misroutes."""
    if not text or len(text.strip()) < 2: return None
    tokens = _tokenize_for_kw(text)
    low = text.lower()

    # data-collection stages: free text should NOT be classified by fast path
    if stage in ("Q_EXCHANGE_BIKE", "Q_VISIT_TIME"):
        # Only catch obvious off-ramps; let GPT extract the data
        if any(p in low for p in _BUSY_PHRASES): return "busy"
        if any(p in low for p in _NOT_INT_PHRASES): return "not_interested"
        return None

    has_wrong = any(p in low for p in _WRONG_PHRASES)
    has_busy = any(p in low for p in _BUSY_PHRASES)
    has_not_int = any(p in low for p in _NOT_INT_PHRASES)
    has_price = any(p in low for p in _PRICE_PHRASES)

    if stage == "GREETING" and has_wrong: return "wrong_enquiry"
    if has_busy: return "busy"
    if has_not_int: return "not_interested"
    if has_price: return "asking_price"

    has_yes = any(t in _YES_WORDS for t in tokens if t) or any(p in low for p in _YES_PHRASES)
    has_no = any(t in _NO_WORDS for t in tokens if t) or any(p in low for p in _NO_PHRASES)

    if has_yes and not has_no: return "yes"
    if has_no and not has_yes: return "no"
    return None


# ── Sarvam STT / TTS ───────────────────────────────────────────────────────
async def sarvam_stt(wav, hint="hi-IN"):
    try:
        cl = HTTP or httpx.AsyncClient(timeout=15)
        r = await cl.post("https://api.sarvam.ai/speech-to-text",
            headers={"api-subscription-key": SARVAM_API_KEY},
            files={"file": ("audio.wav", wav, "audio/wav")},
            data={"model":"saaras:v3","language_code":hint,"with_timestamps":"false"})
        if r.status_code != 200:
            log.error(f"STT {r.status_code}: {r.text[:200]}"); return "", hint
        j = r.json()
        return j.get("transcript","").strip(), j.get("language_code", hint)
    except Exception as e:
        log.error(f"STT exc: {e}"); return "", hint

async def sarvam_tts(text, language="hindi"):
    lc = "hi-IN" if language == "hindi" else "mr-IN"
    try:
        cl = HTTP or httpx.AsyncClient(timeout=15)
        r = await cl.post("https://api.sarvam.ai/text-to-speech",
            headers={"api-subscription-key": SARVAM_API_KEY, "Content-Type":"application/json"},
            json={"text":text,"target_language_code":lc,"speaker":SARVAM_VOICE,
                  "model":TTS_MODEL,"pace":TTS_PACE,"temperature":TTS_TEMP,
                  "audio_format":"wav","sample_rate":8000})
        if r.status_code != 200:
            log.error(f"TTS {r.status_code}: {r.text[:300]}"); return b''
        a = r.json().get("audios",[None])[0]
        if not a: return b''
        wav = base64.b64decode(a)
        sr = parse_wav_sr(wav)
        pcm = wav[44:] if len(wav) > 44 else b''
        if sr and sr != TARGET_SR: pcm = resample_pcm(pcm, sr, TARGET_SR)
        ch, st = 160, 0
        while st < len(pcm)-ch:
            s = struct.unpack(f'<{ch//2}h', pcm[st:st+ch])
            if (sum(x*x for x in s)/len(s))**0.5 > 50: break
            st += ch
        return pcm[st:]
    except Exception as e:
        log.error(f"TTS exc: {e}"); return b''


async def gpt_classify(reply, stage):
    try:
        cl = HTTP or httpx.AsyncClient(timeout=8)
        prompt = CLASSIFY_PROMPT.format(reply=reply, stage=stage)
        r = await cl.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization":f"Bearer {OPENAI_API_KEY}","Content-Type":"application/json"},
            json={"model":GPT_MODEL,"messages":[{"role":"user","content":prompt}],
                  "max_tokens":30,"temperature":0,"response_format":{"type":"json_object"}})
        obj = json.loads(r.json()["choices"][0]["message"]["content"].strip())
        intent = obj.get("intent","unclear").strip().lower()
        valid = ("yes","no","wrong_enquiry","busy","not_interested","asking_price",
                 "offtopic","data","unclear")
        if intent not in valid: intent = "unclear"
        return intent
    except Exception as e:
        log.error(f"GPT classify error: {e}"); return "unclear"


async def gpt_extract_visit(reply):
    """Extract day/time from caller reply."""
    try:
        cl = HTTP or httpx.AsyncClient(timeout=8)
        prompt = EXTRACT_VISIT_TIME_PROMPT.format(reply=reply)
        r = await cl.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization":f"Bearer {OPENAI_API_KEY}","Content-Type":"application/json"},
            json={"model":GPT_MODEL,"messages":[{"role":"user","content":prompt}],
                  "max_tokens":60,"temperature":0,"response_format":{"type":"json_object"}})
        obj = json.loads(r.json()["choices"][0]["message"]["content"].strip())
        return obj.get("day","").strip(), obj.get("time","").strip(), bool(obj.get("is_clear", False))
    except Exception as e:
        log.error(f"GPT extract visit error: {e}"); return "", "", False


async def gpt_extract_bike(reply):
    try:
        cl = HTTP or httpx.AsyncClient(timeout=8)
        prompt = EXTRACT_BIKE_PROMPT.format(reply=reply)
        r = await cl.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization":f"Bearer {OPENAI_API_KEY}","Content-Type":"application/json"},
            json={"model":GPT_MODEL,"messages":[{"role":"user","content":prompt}],
                  "max_tokens":40,"temperature":0,"response_format":{"type":"json_object"}})
        obj = json.loads(r.json()["choices"][0]["message"]["content"].strip())
        return obj.get("bike","").strip()
    except Exception as e:
        log.error(f"GPT extract bike error: {e}"); return ""


def detect_language(code, txt):
    if code == "mr-IN": return "marathi"
    mk = ["आहे","आहेत","नाही","करता","करतो","तुम्ही","मला","होय","बरं","ठीक आहे","का?","नको"]
    if any(m in txt for m in mk): return "marathi"
    return "hindi"


# ── Stages ──────────────────────────────────────────────────────────────────
STAGE_GREETING       = "GREETING"
STAGE_EXCHANGE       = "Q_EXCHANGE"
STAGE_EXCHANGE_BIKE  = "Q_EXCHANGE_BIKE"
STAGE_SHOWROOM       = "Q_SHOWROOM"
STAGE_VISIT_TIME     = "Q_VISIT_TIME"
STAGE_DONE           = "DONE"


@dataclass
class CallContext:
    enquiry_confirmed: Optional[bool] = None
    exchange_interested: Optional[bool] = None
    exchange_bike: str = ""
    can_visit_showroom: Optional[bool] = None
    visit_day: str = ""
    visit_time: str = ""
    language: str = "hindi"


LINE_CACHE = {}
def cache_key(text, lang): return (text, lang)


class PriyaSession:
    def __init__(self, sid, ws, caller_number="", call_sid="",
                 customer_name="", bike_form="", branch_form=""):
        self.stream_sid = sid; self.exotel_ws = ws; self.ctx = CallContext()
        self.audio_buffer = bytearray(); self.seq = 0
        self.alive = True; self.processing = False; self.silence_count = 0
        self.has_audio = False; self.is_speaking = False; self.speak_done_at = 0.0
        self.call_start = time.time(); self.retry_count = 0; self.done = False
        self.last_priya = ""
        self.stage = STAGE_GREETING
        self.caller_number = caller_number
        self.call_sid = call_sid
        self.customer_name = customer_name or ""
        self.bike_form = bike_form or ""
        self.branch_form = branch_form or ""
        self.outcome = ""
        self.offtopic_count = 0
        log.info(f"[{sid}] Session created — name={customer_name!r} bike={bike_form!r} branch={branch_form!r}")

    def _ws_open(self):
        try:
            if hasattr(self.exotel_ws, 'state'):
                from websockets.protocol import State as WS
                return self.exotel_ws.state == WS.OPEN
            return not self.exotel_ws.closed
        except: return False

    async def start(self):
        await asyncio.sleep(GREETING_LEAD_IN)
        if not self.alive or not self._ws_open(): return
        line = build_dynamic_greeting(self.customer_name, self.bike_form, self.ctx.language)
        self.last_priya = line
        log.info(f"[{self.stream_sid}] PRIYA → {line}")
        # Greeting is dynamic, cannot pre-cache — TTS each call
        pcm = await sarvam_tts(line, self.ctx.language)
        if pcm:
            await self._play_pcm(LEAD_SILENCE + pcm)

    async def on_media(self, data):
        if self.is_speaking or self.done: return
        if time.time()-self.speak_done_at < ECHO_COOLDOWN: return
        if self.processing: return
        pl = data.get("media",{}).get("payload","")
        if not pl: return
        chunk = base64.b64decode(pl); rms = pcm_rms(chunk)
        if rms > SILENCE_THRESH:
            self.audio_buffer.extend(chunk); self.silence_count=0; self.has_audio=True
            if len(self.audio_buffer)/BYTES_PER_SEC > 8.0:
                await self._process_turn()
        else:
            if self.has_audio:
                self.silence_count += 1; self.audio_buffer.extend(chunk)
                if self.silence_count >= int(SILENCE_SECS*50):
                    secs = len(self.audio_buffer)/BYTES_PER_SEC
                    if secs < MIN_AUDIO_SECS:
                        self.audio_buffer.clear(); self.has_audio=False; self.silence_count=0; return
                    await self._process_turn()

    async def _process_turn(self):
        if self.processing or not self.has_audio or not self.alive or self.done: return
        self.processing = True
        audio = bytes(self.audio_buffer); self.audio_buffer.clear()
        self.has_audio = False; self.silence_count = 0
        try:
            raw_secs = len(audio)/BYTES_PER_SEC
            audio = trim_trailing_silence(audio)
            secs = len(audio)/BYTES_PER_SEC
            hint = "hi-IN" if self.ctx.language == "hindi" else "mr-IN"
            _t0 = time.time()
            text, det = await sarvam_stt(pcm_to_wav(audio), hint)
            log.info(f"[{self.stream_sid}] STT {time.time()-_t0:.1f}s | audio {raw_secs:.1f}s→{secs:.1f}s")
            if not text or not text.strip():
                log.info(f"[{self.stream_sid}] Empty STT — waiting"); self.processing=False; return
            if is_garbled(text):
                log.info(f"[{self.stream_sid}] Garbled STT: '{text[:30]}'")
                await self._handle_retry(); return
            if is_echo(text, self.last_priya):
                log.info(f"[{self.stream_sid}] Echo discarded: '{text[:40]}'")
                self.processing=False; return
            log.info(f"[{self.stream_sid}] CALLER → {text}  [stage={self.stage}]")

            nl = detect_language(det, text)
            if nl != self.ctx.language:
                log.info(f"[{self.stream_sid}] Language → {nl}")
                self.ctx.language = nl

            # Fast classifier first; GPT fallback
            intent = fast_classify(text, self.stage)
            if intent is not None:
                log.info(f"[{self.stream_sid}] FAST → intent={intent}")
            else:
                _g0 = time.time()
                intent = await gpt_classify(text, self.stage)
                log.info(f"[{self.stream_sid}] GPT classify {time.time()-_g0:.1f}s → intent={intent}")

            if intent not in ("unclear","offtopic"):
                self.retry_count = 0

            await self._route(intent, text)
        except Exception as e:
            log.error(f"[{self.stream_sid}] Pipeline error: {e}")
        finally:
            self.processing = False

    async def _route(self, intent, raw_text):
        # Universal off-ramps
        if intent == "busy":
            await self._close_with(BUSY_LINES); return
        if intent == "not_interested":
            await self._close_with(NOT_INTERESTED_LINES); return
        if intent == "asking_price":
            await self._ask(PRICE_DEFLECT)
            # After deflection, re-ask the current stage's question
            await self._ask_current_stage()
            return
        if intent == "offtopic":
            self.offtopic_count += 1
            if self.offtopic_count > MAX_OFFTOPIC:
                # Redirected once already, now close politely
                await self._close_with(REDIRECT_LINES); return
            await self._ask(REDIRECT_LINES)
            await self._ask_current_stage()
            return

        # Stage-specific routing
        if self.stage == STAGE_GREETING:
            if intent == "yes":
                self.ctx.enquiry_confirmed = True
                self.stage = STAGE_EXCHANGE
                await self._ask(Q_EXCHANGE); return
            if intent in ("no","wrong_enquiry"):
                self.ctx.enquiry_confirmed = False
                await self._close_with(WRONG_ENQUIRY_LINES); return
            await self._handle_retry(); return

        if self.stage == STAGE_EXCHANGE:
            if intent == "yes":
                self.ctx.exchange_interested = True
                self.stage = STAGE_EXCHANGE_BIKE
                await self._ask(Q_EXCHANGE_BIKE); return
            if intent == "no":
                self.ctx.exchange_interested = False
                # Soft ack then move to showroom
                await self._ask(Q_NO_EXCHANGE_ACK)
                self.stage = STAGE_SHOWROOM
                await self._ask(Q_SHOWROOM); return
            await self._handle_retry(); return

        if self.stage == STAGE_EXCHANGE_BIKE:
            # Expect a bike name; extract with GPT
            bike = await gpt_extract_bike(raw_text)
            if bike:
                self.ctx.exchange_bike = bike
                log.info(f"[{self.stream_sid}] Captured exchange_bike: {bike!r}")
                self.stage = STAGE_SHOWROOM
                await self._ask(Q_SHOWROOM); return
            # Couldn't extract a clean bike name — if we've already retried once,
            # accept the raw reply so we don't trap the customer. Executive can clean it up.
            if self.retry_count >= 1:
                self.ctx.exchange_bike = raw_text.strip()[:80]  # cap length
                log.info(f"[{self.stream_sid}] Accepted raw bike reply after retry: {raw_text!r}")
                self.stage = STAGE_SHOWROOM
                await self._ask(Q_SHOWROOM); return
            await self._handle_retry(); return

        if self.stage == STAGE_SHOWROOM:
            if intent == "yes":
                self.ctx.can_visit_showroom = True
                self.stage = STAGE_VISIT_TIME
                await self._ask(Q_VISIT_TIME); return
            if intent == "no":
                self.ctx.can_visit_showroom = False
                await self._close_with(NO_VISIT_CLOSE); return
            await self._handle_retry(); return

        if self.stage == STAGE_VISIT_TIME:
            day, t, is_clear = await gpt_extract_visit(raw_text)
            if is_clear:
                self.ctx.visit_day = day
                self.ctx.visit_time = t
                log.info(f"[{self.stream_sid}] Captured visit day={day!r} time={t!r}")
                await self._ask(VISIT_NOTED)
                self.stage = STAGE_DONE
                await self._close_with(CLOSE_LINES); return
            # Couldn't extract — if we've already retried, accept raw text so executive has something
            if self.retry_count >= 1:
                self.ctx.visit_day = raw_text.strip()[:80]
                log.info(f"[{self.stream_sid}] Accepted raw visit reply after retry: {raw_text!r}")
                await self._ask(VISIT_NOTED)
                self.stage = STAGE_DONE
                await self._close_with(CLOSE_LINES); return
            await self._handle_retry(); return

    async def _ask_current_stage(self):
        """Re-ask the question for the current stage (after a deflection)."""
        mapping = {
            STAGE_GREETING:      None,  # never re-greet
            STAGE_EXCHANGE:      Q_EXCHANGE,
            STAGE_EXCHANGE_BIKE: Q_EXCHANGE_BIKE,
            STAGE_SHOWROOM:      Q_SHOWROOM,
            STAGE_VISIT_TIME:    Q_VISIT_TIME,
        }
        q = mapping.get(self.stage)
        if q: await self._ask(q)

    async def _ask(self, line_set):
        line = line_set[self.ctx.language]
        self.last_priya = line
        log.info(f"[{self.stream_sid}] PRIYA → {line}")
        pcm = LINE_CACHE.get(cache_key(line, self.ctx.language))
        if pcm:
            await self._play_pcm(pcm)
        else:
            await self._speak_uncached(line)

    async def _close_with(self, line_set):
        line = line_set[self.ctx.language]
        if line_set is CLOSE_LINES:               self.outcome = "completed"
        elif line_set is NO_VISIT_CLOSE:          self.outcome = "completed_no_visit"
        elif line_set is WRONG_ENQUIRY_LINES:     self.outcome = "wrong_enquiry"
        elif line_set is BUSY_LINES:              self.outcome = "busy"
        elif line_set is NOT_INTERESTED_LINES:    self.outcome = "not_interested"
        elif line_set is NO_RESPONSE_LINES:       self.outcome = "no_response"
        elif line_set is REDIRECT_LINES:          self.outcome = "offtopic_closed"
        else:                                      self.outcome = "unknown"
        self.last_priya = line
        log.info(f"[{self.stream_sid}] PRIYA (close/{self.outcome}) → {line}")
        pcm = LINE_CACHE.get(cache_key(line, self.ctx.language))
        if pcm:
            dur = await self._play_pcm(pcm)
        else:
            dur = await self._speak_uncached(line)
        self._log_summary()
        await self._hangup(dur)

    async def _handle_retry(self):
        self.retry_count += 1
        log.info(f"[{self.stream_sid}] Retry {self.retry_count}/{MAX_RETRIES} at stage {self.stage}")
        if self.retry_count >= MAX_RETRIES:
            await self._close_with(NO_RESPONSE_LINES); return
        await self._ask(REPEAT_LINES)
        # And re-ask current stage so customer knows what's being asked
        await self._ask_current_stage()

    async def _speak_uncached(self, text):
        if not self.alive or not self._ws_open(): return 0.0
        pcm = await sarvam_tts(text, self.ctx.language)
        if not pcm: log.error(f"[{self.stream_sid}] TTS empty"); return 0.0
        return await self._play_pcm(pcm)

    async def _play_pcm(self, pcm):
        if not self.alive or not self._ws_open(): return 0.0
        duration = len(pcm)/BYTES_PER_SEC
        self.is_speaking = True; self.audio_buffer.clear()
        self.has_audio = False; self.silence_count = 0
        send_start = time.time()
        try:
            cs = 3200
            for i in range(0, len(pcm), cs):
                if not self.alive or not self._ws_open(): self.alive=False; break
                if not await self._send_audio(pcm[i:i+cs]): break
                await asyncio.sleep(0.02)
        finally:
            self.is_speaking = False
            send_elapsed = time.time() - send_start
            remaining_playback = max(0.0, duration - send_elapsed)
            self.speak_done_at = time.time() + remaining_playback
            self.audio_buffer.clear(); self.has_audio=False; self.silence_count=0
        return duration

    async def _send_audio(self, b):
        if not self.alive or not self._ws_open(): return False
        try:
            self.seq += 1
            await self.exotel_ws.send(json.dumps({"event":"media","streamSid":self.stream_sid,
                "media":{"payload":base64.b64encode(b).decode(),
                         "timestamp":str(int(time.time()*1000)),
                         "sequenceNumber":str(self.seq)}}))
            return True
        except websockets.exceptions.ConnectionClosed:
            self.alive=False; return False
        except Exception as e:
            log.error(f"[{self.stream_sid}] Send error: {e}"); self.alive=False; return False

    async def _hangup(self, last_audio_secs=0.0):
        wait = max(0.8, last_audio_secs + 0.6)
        log.info(f"[{self.stream_sid}] Draining {wait:.1f}s before hangup (line {last_audio_secs:.1f}s)")
        await asyncio.sleep(wait)
        self.done = True
        for evt in ("clear","stop"):
            try:
                if self._ws_open():
                    await self.exotel_ws.send(json.dumps({"event": evt, "streamSid": self.stream_sid}))
                    log.info(f"[{self.stream_sid}] Sent Exotel '{evt}'")
            except Exception as e:
                log.info(f"[{self.stream_sid}] '{evt}' send failed: {e}")
        try:
            await asyncio.sleep(0.3)
            if self._ws_open():
                await self.exotel_ws.close(code=1000, reason="call complete")
                log.info(f"[{self.stream_sid}] WS closed by Priya — call ended")
        except Exception as e:
            log.info(f"[{self.stream_sid}] WS close error: {e}")
        self.alive = False

    async def on_stop(self): await self.cleanup()
    async def cleanup(self):
        self.alive = False
        log.info(f"[{self.stream_sid}] Session ended | duration={round(time.time()-self.call_start,1)}s")

    def _log_summary(self):
        log.info(f"[{self.stream_sid}] ═══ CALL SUMMARY ═══")
        log.info(f"[{self.stream_sid}]   customer_name:       {self.customer_name}")
        log.info(f"[{self.stream_sid}]   caller_number:       {self.caller_number}")
        log.info(f"[{self.stream_sid}]   bike_form:           {self.bike_form}")
        log.info(f"[{self.stream_sid}]   branch_form:         {self.branch_form}")
        log.info(f"[{self.stream_sid}]   enquiry_confirmed:   {self.ctx.enquiry_confirmed}")
        log.info(f"[{self.stream_sid}]   exchange_interested: {self.ctx.exchange_interested}")
        log.info(f"[{self.stream_sid}]   exchange_bike:       {self.ctx.exchange_bike}")
        log.info(f"[{self.stream_sid}]   can_visit_showroom:  {self.ctx.can_visit_showroom}")
        log.info(f"[{self.stream_sid}]   visit_day:           {self.ctx.visit_day}")
        log.info(f"[{self.stream_sid}]   visit_time:          {self.ctx.visit_time}")
        log.info(f"[{self.stream_sid}]   language:            {self.ctx.language}")
        log.info(f"[{self.stream_sid}]   outcome:             {self.outcome}")
        if sheet_logger is not None:
            try:
                sheet_logger.log_call(
                    call_sid=self.call_sid,
                    caller_number=self.caller_number,
                    duration_sec=time.time()-self.call_start,
                    language=self.ctx.language,
                    enquiry_confirmed=self.ctx.enquiry_confirmed,
                    exchange_interested=self.ctx.exchange_interested,
                    can_visit_showroom=self.ctx.can_visit_showroom,
                    outcome=self.outcome,
                    # extended fields v5.3
                    customer_name=self.customer_name,
                    bike_form=self.bike_form,
                    branch_form=self.branch_form,
                    exchange_bike=self.ctx.exchange_bike,
                    visit_day=self.ctx.visit_day,
                    visit_time=self.ctx.visit_time,
                )
            except Exception as e:
                log.error(f"[{self.stream_sid}] Sheet log failed: {e}")


SESSIONS = {}

async def handle_exotel(websocket, path=None):
    stream_sid="pending"; session=None
    try:
        async for raw in websocket:
            try:
                data=json.loads(raw); event=data.get("event","")
                sid=(data.get("streamSid") or data.get("stream_sid")
                     or data.get("start",{}).get("streamSid"))
                if sid: stream_sid=sid
                if event=="connected":
                    log.info(f"[{stream_sid}] Exotel connected")
                elif event=="start":
                    s=data.get("start",{}); stream_sid=s.get("streamSid",stream_sid)
                    log.info(f"[{stream_sid}] Stream started | call={s.get('callSid','')}")
                    log.info(f"[{stream_sid}] Full start payload: {json.dumps(s, ensure_ascii=False)[:500]}")
                    # Extract caller info
                    caller_number = (s.get("from") or s.get("caller") or s.get("callerId")
                                     or s.get("customParameters",{}).get("from")
                                     or s.get("customParameters",{}).get("to") or "")
                    call_sid = s.get("callSid","")
                    # n8n passes customer data via Exotel custom_parameters
                    cp = s.get("customParameters", {}) or s.get("custom_parameters", {}) or {}
                    customer_name = cp.get("customer_name","") or cp.get("name","")
                    bike_form     = cp.get("bike","") or cp.get("vehicle","")
                    branch_form   = cp.get("branch","")
                    session = PriyaSession(stream_sid, websocket,
                                           caller_number=caller_number, call_sid=call_sid,
                                           customer_name=customer_name,
                                           bike_form=bike_form,
                                           branch_form=branch_form)
                    SESSIONS[stream_sid] = session
                    asyncio.create_task(session.start())
                elif event=="media":
                    if session: await session.on_media(data)
                elif event=="stop":
                    if session: await session.on_stop()
                    break
            except json.JSONDecodeError: pass
            except Exception as e: log.error(f"[{stream_sid}] Event error: {e}")
    except websockets.exceptions.ConnectionClosed:
        log.info(f"[{stream_sid}] WS closed")
    except Exception as e:
        log.error(f"[{stream_sid}] Unexpected: {e}")
    finally:
        if session: await session.cleanup()
        SESSIONS.pop(stream_sid, None)


async def main():
    global HTTP
    miss=[]
    if not OPENAI_API_KEY: miss.append("OPENAI_API_KEY")
    if not SARVAM_API_KEY: miss.append("SARVAM_API_KEY")
    if miss: raise ValueError(f"Missing in .env: {', '.join(miss)}")
    HTTP = httpx.AsyncClient(timeout=15,
        limits=httpx.Limits(max_keepalive_connections=20, keepalive_expiry=300), http2=False)
    log.info("="*60)
    log.info("  Shelar TVS — Priya v5.3 (lead-aware, n8n-triggered)")
    log.info(f"  TTS Voice: Sarvam {TTS_MODEL} ({SARVAM_VOICE})")
    log.info(f"  Pace={TTS_PACE}  Temp={TTS_TEMP}")
    log.info(f"  Brain    : {GPT_MODEL}")
    log.info(f"  Flow     : greet(name,bike) → exchange → bike → showroom → time → close")
    log.info(f"  Server   : ws://{SERVER_HOST}:{SERVER_PORT}")
    log.info("="*60)
    log.info("Caching fixed lines (Hindi + Marathi)...")
    try:
        t0 = time.time()
        sets = [Q_EXCHANGE, Q_EXCHANGE_BIKE, Q_NO_EXCHANGE_ACK, Q_SHOWROOM, Q_VISIT_TIME,
                VISIT_NOTED, CLOSE_LINES, NO_VISIT_CLOSE, WRONG_ENQUIRY_LINES,
                BUSY_LINES, NOT_INTERESTED_LINES, NO_RESPONSE_LINES, REPEAT_LINES,
                PRICE_DEFLECT, REDIRECT_LINES]
        for s in sets:
            for lang, text in s.items():
                key = cache_key(text, lang)
                if key in LINE_CACHE: continue
                pcm = await sarvam_tts(text, lang)
                if pcm: LINE_CACHE[key] = pcm
        log.info(f"  cached {len(LINE_CACHE)} fixed lines in {time.time()-t0:.1f}s")
        log.info(f"  (greeting is dynamic per-customer, generated at call time)")
    except Exception as e:
        log.error(f"Cache failed: {e}")
    async with websockets.serve(handle_exotel, SERVER_HOST, SERVER_PORT):
        log.info("Ready. Waiting for Exotel calls...")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())

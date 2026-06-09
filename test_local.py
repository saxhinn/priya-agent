#!/usr/bin/env python3
"""
Local test harness for Priya v5.0 — NO EXOTEL NEEDED.

Exercises the real pipeline (real Sarvam + OpenAI API calls) so you can hear
Priya's voice, verify STT picks up your speech, confirm the classifier returns
the right intents, and walk through the entire conversation flow — all from
your laptop, no phone, no Exotel credits.

What this CAN test (with real APIs):
  ✓ Server boot + line caching (does it cache all 16 lines cleanly?)
  ✓ Voice quality (saves Priya's lines as .wav so you can play them)
  ✓ STT accuracy on YOUR voice (record yourself, see the transcript)
  ✓ Classifier accuracy (does "haan" → "yes", "nahi" → "no", etc.)
  ✓ Full conversation routing (simulated end-to-end without Exotel)

What this CANNOT test:
  ✗ Live Exotel WebSocket
  ✗ Real-time phone audio behaviour / echo
  ✗ Exotel hangup
  → Those still need a real phone call.

USAGE:
  python test_local.py           # interactive menu
  python test_local.py voice     # generate all Priya audio files
  python test_local.py classify  # test classifier with sample replies
  python test_local.py flow      # walk through full conversation logic
  python test_local.py stt PATH  # transcribe a wav/mp3 you recorded
"""
import asyncio, sys, os, time, json
sys.path.insert(0, os.path.dirname(__file__))
import agent  # the v5 agent module

OUTDIR = os.path.join(os.path.dirname(__file__), "test_output")
os.makedirs(OUTDIR, exist_ok=True)


def pcm_to_wav_file(pcm, path, sr=8000):
    """Write 8kHz mono 16-bit PCM to a playable .wav file."""
    import struct
    nc, bps = 1, 16
    ds = len(pcm); br = sr*nc*bps//8; ba = nc*bps//8
    h = struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 36+ds, b'WAVE', b'fmt ', 16, 1, nc, sr, br, ba, bps, b'data', ds)
    with open(path, "wb") as f:
        f.write(h); f.write(pcm)


async def setup_http():
    import httpx
    agent.HTTP = httpx.AsyncClient(
        timeout=15,
        limits=httpx.Limits(max_keepalive_connections=20, keepalive_expiry=300),
        http2=False)


async def teardown_http():
    if agent.HTTP:
        await agent.HTTP.aclose()


# ─── TEST 1: generate Priya's voice files ──────────────────────────────────────
async def test_voice():
    """Generate every cached line as a .wav file you can play."""
    print("\n=== TEST 1: Voice quality ===")
    print(f"Generating Priya's audio for all 16 fixed lines...")
    print(f"Output: {OUTDIR}/")
    await setup_http()
    lines = []
    for lang in ("hindi", "marathi"):
        lines.append(("01_greeting", lang, agent.GREETING[lang](agent.VEHICLE_NAME)))
    for label, lineset in [("02_q_exchange", agent.Q_EXCHANGE),
                            ("03_q_showroom", agent.Q_SHOWROOM),
                            ("04_close", agent.CLOSE_LINES),
                            ("05_wrong_enquiry", agent.WRONG_ENQUIRY_LINES),
                            ("06_busy", agent.BUSY_LINES),
                            ("07_not_interested", agent.NOT_INTERESTED_LINES),
                            ("08_no_response", agent.NO_RESPONSE_LINES),
                            ("09_repeat", agent.REPEAT_LINES)]:
        for lang, text in lineset.items():
            lines.append((label, lang, text))
    t0 = time.time()
    for label, lang, text in lines:
        _t = time.time()
        pcm = await agent.sarvam_tts(text, lang)
        if not pcm:
            print(f"  ✗ FAILED  {label}_{lang}.wav  ({text[:30]}...)")
            continue
        path = os.path.join(OUTDIR, f"{label}_{lang}.wav")
        pcm_to_wav_file(pcm, path)
        secs = len(pcm) / (8000 * 2)
        print(f"  ✓ {label}_{lang}.wav  ({time.time()-_t:.1f}s gen, {secs:.1f}s audio) — {text[:50]}")
    print(f"\nDONE in {time.time()-t0:.1f}s. Open the .wav files to hear Priya.")
    print(f"Play them on your computer or phone — does she sound right?")
    await teardown_http()


# ─── TEST 2: classifier accuracy ───────────────────────────────────────────────
async def test_classify():
    """Test GPT classifier with sample customer replies at each stage."""
    print("\n=== TEST 2: GPT classifier accuracy ===")
    await setup_http()
    cases = [
        # (reply, stage, expected_intent)
        ("जी हाँ",                  "GREETING",   "yes"),
        ("haan bola tha",            "GREETING",   "yes"),
        ("नहीं मैंने नहीं किया",       "GREETING",   "wrong_enquiry"),
        ("wrong number hai",         "GREETING",   "wrong_enquiry"),
        ("हाँ है मेरे पास",            "Q_EXCHANGE", "yes"),
        ("नहीं नहीं चाहिए",            "Q_EXCHANGE", "no"),
        ("interested नहीं हूँ",        "Q_EXCHANGE", "no"),
        ("हाँ आ सकता हूँ",             "Q_SHOWROOM", "yes"),
        ("नहीं नहीं हो पाएगा",         "Q_SHOWROOM", "no"),
        ("अभी busy हूँ",              "Q_EXCHANGE", "busy"),
        ("बाद में call करना",         "GREETING",   "busy"),
        ("not interested",           "Q_SHOWROOM", "not_interested"),
        ("मम्म",                     "Q_EXCHANGE", "unclear"),
        ("क्या बोल रहे हो",           "GREETING",   "unclear"),
    ]
    pass_count = 0
    for reply, stage, expected in cases:
        _t = time.time()
        intent = await agent.gpt_classify(reply, stage)
        elapsed = time.time() - _t
        ok = "✓" if intent == expected else "✗"
        if intent == expected: pass_count += 1
        print(f"  {ok}  '{reply[:30]}'  [{stage}]  →  {intent}  (want {expected})  [{elapsed:.1f}s]")
    print(f"\n{pass_count}/{len(cases)} cases correct ({pass_count*100//len(cases)}%)")
    print("Note: classifier accuracy is more important than speed for routing decisions.")
    await teardown_http()


# ─── TEST 3: full conversation flow simulation ────────────────────────────────
async def test_flow():
    """Walk through full call routing with mocked audio but real classifier."""
    print("\n=== TEST 3: full conversation flow (real classifier, no audio) ===")
    await setup_http()
    
    class FakeWS:
        def __init__(self): self._closed=False
        @property
        def state(self):
            from websockets.protocol import State
            return State.CLOSED if self._closed else State.OPEN
        async def send(self, m): pass
        async def close(self, code=1000, reason=""): self._closed=True

    # Use real classifier, but mock STT (we'll feed transcripts directly) and
    # skip TTS playback (no real audio to send to a fake websocket)
    async def fake_tts(text, language="hindi"):
        return b'\x40\x40' * (8000 * max(1, len(text)//12))
    agent.sarvam_tts = fake_tts
    # Pre-fill cache so the session doesn't try to TTS during the test
    for lang in ("hindi","marathi"):
        agent.LINE_CACHE[(agent.GREETING[lang](agent.VEHICLE_NAME), lang)] = b'\x40\x40'*8000
    for ls in [agent.Q_EXCHANGE, agent.Q_SHOWROOM, agent.CLOSE_LINES, agent.WRONG_ENQUIRY_LINES,
               agent.BUSY_LINES, agent.NOT_INTERESTED_LINES, agent.NO_RESPONSE_LINES, agent.REPEAT_LINES]:
        for lang, text in ls.items():
            agent.LINE_CACHE[(text, lang)] = b'\x40\x40'*8000

    scenarios = [
        ("Happy path: YES / YES / YES",
         [("जी हाँ बोला था", None), ("हाँ है मेरे पास", None), ("हाँ कर सकता हूँ", None)]),
        ("YES / NO / NO",
         [("हाँ", None), ("नहीं चाहिए", None), ("नहीं अभी", None)]),
        ("Wrong number at greeting",
         [("नहीं मैंने नहीं किया enquiry", None)]),
        ("Busy at exchange",
         [("जी हाँ", None), ("अभी busy हूँ", None)]),
    ]
    for name, turns in scenarios:
        print(f"\n  Scenario: {name}")
        ws = FakeWS()
        s = agent.PriyaSession("t", ws)
        # feed transcripts in order via mocked STT
        transcripts = iter(t[0] for t in turns)
        async def fake_stt(wav, hint="hi-IN"):
            try: return next(transcripts), "hi-IN"
            except StopIteration: return "", "hi-IN"
        agent.sarvam_stt = fake_stt
        await s.start()
        for transcript, _ in turns:
            if s.done: break
            s.audio_buffer = bytearray(b'\x40\x40'*8000); s.has_audio=True; s.silence_count=999
            await s._process_turn()
        print(f"    enquiry={s.ctx.enquiry_confirmed}  exchange={s.ctx.exchange_interested}  visit={s.ctx.can_visit_showroom}  done={s.done}")
    await teardown_http()


# ─── TEST 4: transcribe a wav file (record yourself, test STT) ─────────────────
async def test_stt(path):
    """Transcribe a wav file with Sarvam STT."""
    print(f"\n=== TEST 4: STT on your recording ===")
    print(f"File: {path}")
    if not os.path.exists(path):
        print("  ✗ File not found.")
        print("  To make a recording on Windows:")
        print("    1. Open the 'Voice Recorder' app")
        print("    2. Record yourself saying 'जी हाँ' (or any reply)")
        print("    3. Export as .wav or .m4a")
        print("    4. Run: python test_local.py stt path\\to\\file.wav")
        return
    await setup_http()
    with open(path, "rb") as f:
        wav = f.read()
    _t = time.time()
    text, lang = await agent.sarvam_stt(wav, "hi-IN")
    print(f"  Transcript: {text!r}")
    print(f"  Language:   {lang}")
    print(f"  Time:       {time.time()-_t:.1f}s")
    if text:
        print(f"\n  Classifier test on this transcript:")
        for stage in ("GREETING","Q_EXCHANGE","Q_SHOWROOM"):
            intent = await agent.gpt_classify(text, stage)
            print(f"    stage={stage} → intent={intent}")
    await teardown_http()


# ─── Main menu ─────────────────────────────────────────────────────────────────
async def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        print("\nAvailable tests:")
        print("  1) voice    - Generate all Priya audio files (.wav, hear her voice)")
        print("  2) classify - Test classifier accuracy on sample replies")
        print("  3) flow     - Walk through full conversation routing")
        print("  4) stt PATH - Transcribe a .wav file (record yourself, test STT)")
        print("\nRun: python test_local.py <test>")
        return
    cmd = args[0].lower()
    if cmd == "voice":     await test_voice()
    elif cmd == "classify": await test_classify()
    elif cmd == "flow":     await test_flow()
    elif cmd == "stt":
        if len(args) < 2:
            print("Usage: python test_local.py stt path\\to\\your_recording.wav")
            return
        await test_stt(args[1])
    else:
        print(f"Unknown test: {cmd}")

if __name__ == "__main__":
    asyncio.run(main())

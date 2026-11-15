import asyncio
import os
import re
import subprocess
import sys
import requests
from faster_whisper import WhisperModel
import edge_tts
from pydub import AudioSegment

VIDEO_INPUT = sys.argv[1]
CHOICE = sys.argv[2]

VOICE_MAP = {
    "الدارجة المغربية - صوت رجالي (جمال)": ("ar-MA-JamalNeural", True),
    "الدارجة المغربية - صوت نسائي (منى)": ("ar-MA-MounaNeural", True),
    "العربية الفصحى - صوت رجالي (حامد)": ("ar-SA-HamedNeural", False),
    "العربية الفصحى - صوت نسائي (سلمى)": ("ar-EG-SalmaNeural", False),
}

VOICE, IS_DARIJA = VOICE_MAP.get(CHOICE, ("ar-MA-JamalNeural", True))

DARIJA_RULES = [
    (r"\bسوف\b", "غادي"),
    (r"\bسأ", "غادي ن"),
    (r"\bسن", "غادي ن"),
    (r"\bسي", "غادي ي"),
    (r"\bست", "غادي ت"),
    (r"\bالآن\b", "دابا"),
    (r"\bلماذا\b", "علاش"),
    (r"\bكيف\b", "كيفاش"),
    (r"\bماذا\b", "شنو"),
    (r"\bما هذا\b", "شنو هادشي"),
    (r"\bهذا\b", "هاد"),
    (r"\bهذه\b", "هاد"),
    (r"\bهؤلاء\b", "هادو"),
    (r"\bذلك\b", "داك"),
    (r"\bتلك\b", "ديك"),
    (r"\bهنا\b", "هنايا"),
    (r"\bهناك\b", "تما"),
    (r"\bكثيرا\b", "بزاف"),
    (r"\bكثير\b", "بزاف ديال"),
    (r"\bقليلا\b", "شوية"),
    (r"\bجدا\b", "بزاف"),
    (r"\bأريد\b", "بغيت"),
    (r"\bنريد\b", "بغينا"),
    (r"\bيريد\b", "بغى"),
    (r"\bتريد\b", "بغيتي"),
    (r"\bأستطيع\b", "نقدر"),
    (r"\bلا أستطيع\b", "ما نقدرش"),
    (r"\bلا يمكن\b", "ما يمكنش"),
    (r"\bيمكنك\b", "تقدر"),
    (r"\bيمكننا\b", "نقدرو"),
    (r"\bيجب\b", "خاص"),
    (r"\bربما\b", "واقيلا"),
    (r"\bأيضا\b", "حتى هو"),
    (r"\bفقط\b", "غير"),
    (r"\bلكن\b", "ولكن"),
    (r"\bلأن\b", "حيت"),
    (r"\bعندما\b", "فاش"),
    (r"\bحين\b", "ملي"),
    (r"\bنحن\b", "حنا"),
    (r"\bأنتم\b", "نتوما"),
    (r"\bهم\b", "هوما"),
    (r"\bأين\b", "فين"),
    (r"\bمتى\b", "فوقاش"),
    (r"\bمن أجل\b", "باش"),
    (r"\bلكي\b", "باش"),
    (r"\bلا يوجد\b", "ما كاينش"),
    (r"\bيوجد\b", "كاين"),
    (r"\bتوجد\b", "كاينة"),
    (r"\bنعم\b", "إيه"),
    (r"\bحسنا\b", "واخا"),
    (r"\bطبعا\b", "بطبيعة الحال"),
]

def to_darija(text):
    for pattern, replacement in DARIJA_RULES:
        text = re.sub(pattern, replacement, text)
    return text

def gtx_translate(text, target="ar"):
    url = "https://translate.googleapis.com/translate_a/single"
    params = {"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": text}
    for proxies in [None, {"http": "socks5h://127.0.0.1:40000", "https": "socks5h://127.0.0.1:40000"}]:
        try:
            r = requests.get(url, params=params, proxies=proxies, timeout=15)
            if r.status_code == 200:
                data = r.json()
                return "".join(part[0] for part in data[0] if part[0])
        except Exception:
            continue
    return text

def translate_all_segments(texts, batch_size=20):
    translated = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        joined = "\n".join(batch)
        res = gtx_translate(joined, "ar")
        lines = [line.strip() for line in res.split("\n")]
        if len(lines) == len(batch):
            translated.extend(lines)
        else:
            for item in batch:
                translated.append(gtx_translate(item, "ar").strip())
    return translated

async def generate_tts(text, filename, voice, sem):
    async with sem:
        for attempt in range(3):
            try:
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(filename)
                if os.path.exists(filename) and os.path.getsize(filename) > 0:
                    return True
            except Exception:
                await asyncio.sleep(1)
        return False

def match_duration(audio_path, target_ms):
    sound = AudioSegment.from_file(audio_path)
    current_ms = len(sound)
    if current_ms > target_ms and target_ms > 500:
        ratio = min(current_ms / target_ms, 1.45)
        fast_path = audio_path.replace(".mp3", "_fast.mp3")
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", audio_path,
            "-filter:a", f"atempo={ratio}",
            "-vn", fast_path
        ], check=False)
        if os.path.exists(fast_path):
            return AudioSegment.from_file(fast_path)
    return sound

async def main():
    os.makedirs("temp_tts", exist_ok=True)
    
    print("1. استخراج الصوت والتعرف على اللغة والنص...")
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments_gen, info = model.transcribe(VIDEO_INPUT, beam_size=5)
    raw_segments = [s for s in segments_gen if s.text.strip()]
    print(f"اللغة المكتشفة: {info.language} | عدد الجمل: {len(raw_segments)} | الاختيار: {CHOICE}")

    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", VIDEO_INPUT, "-vn", "original_audio.wav"
    ], check=True)
    base_audio = AudioSegment.from_file("original_audio.wav") - 18
    dubbed_track = AudioSegment.silent(duration=len(base_audio))

    print("2. ترجمة الجمل دفعة واحدة لتفادي الحظر...")
    original_texts = [s.text.strip() for s in raw_segments]
    translated_texts = translate_all_segments(original_texts)

    if IS_DARIJA:
        translated_texts = [to_darija(t) for t in translated_texts]

    print("3. توليد الصوت وموازنة التوقيت...")
    sem = asyncio.Semaphore(5)
    tasks = []
    for i, text in enumerate(translated_texts):
        tts_file = f"temp_tts/seg_{i}.mp3"
        tasks.append(generate_tts(text, tts_file, VOICE, sem))
    results = await asyncio.gather(*tasks)

    for i, (seg, text, ok) in enumerate(zip(raw_segments, translated_texts, results)):
        if not ok or not text:
            continue
        start_ms = int(seg.start * 1000)
        end_ms = int(seg.end * 1000)
        slot_duration = end_ms - start_ms
        tts_file = f"temp_tts/seg_{i}.mp3"
        seg_audio = match_duration(tts_file, slot_duration)
        dubbed_track = dubbed_track.overlay(seg_audio, position=start_ms)
        print(f"[{seg.start:.1f}s -> {seg.end:.1f}s] {text}")

    print("4. دمج الصوت المدبلج مع الفيديو النهائي...")
    final_audio = base_audio.overlay(dubbed_track)
    final_audio.export("final_dubbed.wav", format="wav")

    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", VIDEO_INPUT,
        "-i", "final_dubbed.wav",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        "dubbed_video.mp4"
    ], check=True)
    print("تم إنتاج الفيديو المدبلج بنجاح!")

if __name__ == "__main__":
    asyncio.run(main())

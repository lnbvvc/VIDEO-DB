import asyncio
import os
import re
import subprocess
import sys
from curl_cffi import requests as cffi_requests
from faster_whisper import WhisperModel
import edge_tts
from pydub import AudioSegment, silence

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
    (r"\bمرحبا بكم في\b", "مرحبا بيكم ف"),
    (r"\bأهلا بكم في\b", "مرحبا بيكم ف"),
    (r"\bاليوم نحن نعمل على\b", "اليوم غادي نخدمو على"),
    (r"\bنحن نعمل على\b", "كنخدمو على"),
    (r"\bنعمل على\b", "كنخدمو على"),
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

def has_arabic(text):
    return bool(re.search(r"[\u0600-\u06FF]", text))

def translate_text(text, target="ar"):
    url = "https://translate.googleapis.com/translate_a/single"
    params = {"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": text}
    proxies_list = [
        {"http": "socks5h://127.0.0.1:40000", "https": "socks5h://127.0.0.1:40000"},
        None
    ]
    for proxies in proxies_list:
        try:
            r = cffi_requests.get(url, params=params, proxies=proxies, impersonate="chrome", timeout=15)
            if r.status_code == 200:
                data = r.json()
                result = "".join(part[0] for part in data[0] if part and part[0])
                if has_arabic(result):
                    return result
        except Exception as e:
            print(f"تحذير في محاولة الترجمة: {e}")
    return text

def translate_all_segments(texts, batch_size=12):
    translated = []
    sep = " ||| "
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        joined = "\n".join(batch)
        res = translate_text(joined, "ar")
        lines = [line.strip() for line in res.split("\n")]
        if len(lines) == len(batch) and any(has_arabic(l) for l in lines):
            translated.extend(lines)
        else:
            for item in batch:
                translated.append(translate_text(item, "ar").strip())
    return translated

def trim_silence(audio_seg):
    non_silent = silence.detect_nonsilent(audio_seg, min_silence_len=80, silence_thresh=-42)
    if non_silent:
        start_trim = max(0, non_silent[0][0] - 30)
        end_trim = min(len(audio_seg), non_silent[-1][1] + 30)
        return audio_seg[start_trim:end_trim]
    return audio_seg

async def generate_tts(text, filename, voice, sem):
    async with sem:
        for attempt in range(3):
            try:
                communicate = edge_tts.Communicate(text, voice, rate="+5%")
                await communicate.save(filename)
                if os.path.exists(filename) and os.path.getsize(filename) > 0:
                    return True
            except Exception:
                await asyncio.sleep(1)
        return False

def fit_audio_to_slot(audio_path, max_duration_ms):
    sound = trim_silence(AudioSegment.from_file(audio_path))
    current_ms = len(sound)
    if current_ms > max_duration_ms and max_duration_ms > 400:
        ratio = min(current_ms / max_duration_ms, 1.38)
        if ratio > 1.03:
            trimmed_path = audio_path.replace(".mp3", "_trim.wav")
            fast_path = audio_path.replace(".mp3", "_fast.wav")
            sound.export(trimmed_path, format="wav")
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error", "-i", trimmed_path,
                "-filter:a", f"atempo={ratio:.2f}",
                "-vn", fast_path
            ], check=False)
            if os.path.exists(fast_path):
                return trim_silence(AudioSegment.from_file(fast_path))
    return sound

def merge_close_segments(segments):
    merged = []
    for s in segments:
        text = s.text.strip()
        if not text:
            continue
        start = s.words[0].start if getattr(s, "words", None) else s.start
        end = s.words[-1].end if getattr(s, "words", None) else s.end
        if merged and (start - merged[-1]["end"] < 0.35) and (end - merged[-1]["start"] < 7.0):
            merged[-1]["text"] += " " + text
            merged[-1]["end"] = end
        else:
            merged.append({"start": start, "end": end, "text": text})
    return merged

async def main():
    os.makedirs("temp_tts", exist_ok=True)
    
    print("1. استخراج الصوت مع عزل المقدمة الموسيقية وتحديد بداية الكلام بدقة...")
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments_gen, info = model.transcribe(
        VIDEO_INPUT,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=400, speech_pad_ms=100),
        word_timestamps=True
    )
    raw_segments = merge_close_segments(list(segments_gen))
    print(f"اللغة المكتشفة: {info.language} | عدد الجمل المدمجة: {len(raw_segments)} | الاختيار: {CHOICE}")

    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", VIDEO_INPUT, "-vn", "original_audio.wav"
    ], check=True)
    full_original = AudioSegment.from_file("original_audio.wav")
    dubbed_track = AudioSegment.silent(duration=len(full_original))

    print("2. ترجمة الجمل عبر بروكسي WARP والتحقق من اللغة العربية...")
    original_texts = [s["text"] for s in raw_segments]
    translated_texts = translate_all_segments(original_texts)

    if IS_DARIJA:
        translated_texts = [to_darija(t) for t in translated_texts]

    print("3. توليد الصوت وموازنة التوقيت بدون تداخل...")
    sem = asyncio.Semaphore(5)
    tasks = []
    for i, text in enumerate(translated_texts):
        tts_file = f"temp_tts/seg_{i}.mp3"
        tasks.append(generate_tts(text, tts_file, VOICE, sem))
    results = await asyncio.gather(*tasks)

    last_end_ms = 0
    for i, (seg, text, ok) in enumerate(zip(raw_segments, translated_texts, results)):
        if not ok or not text:
            continue

        orig_start_ms = int(seg["start"] * 1000)
        orig_end_ms = int(seg["end"] * 1000)

        # منع تداخل الجملة الحالية مع الجملة السابقة
        actual_start_ms = max(orig_start_ms, last_end_ms)

        # حساب الوقت المتاح قبل بدء الجملة الموالية
        if i + 1 < len(raw_segments):
            next_start_ms = int(raw_segments[i + 1]["start"] * 1000)
            available_ms = max(orig_end_ms - actual_start_ms, next_start_ms - actual_start_ms - 80)
        else:
            available_ms = max(orig_end_ms - actual_start_ms, 4000)

        tts_file = f"temp_tts/seg_{i}.mp3"
        seg_audio = fit_audio_to_slot(tts_file, available_ms)

        # خفض صوت الفيديو الأصلي فقط أثناء وجود كلام، وترك موسيقى المقدمة طبيعية
        duck_start = max(0, min(orig_start_ms, actual_start_ms) - 100)
        duck_end = min(len(full_original), max(orig_end_ms, actual_start_ms + len(seg_audio)) + 150)
        full_original = (
            full_original[:duck_start]
            + (full_original[duck_start:duck_end] - 20)
            + full_original[duck_end:]
        )

        dubbed_track = dubbed_track.overlay(seg_audio, position=actual_start_ms)
        last_end_ms = actual_start_ms + len(seg_audio) + 60
        print(f"[{actual_start_ms/1000:.1f}s -> {last_end_ms/1000:.1f}s] {text}")

    print("4. دمج الصوت المدبلج مع الفيديو النهائي...")
    final_audio = full_original.overlay(dubbed_track)
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

import asyncio
import os
import re
import subprocess
import sys
from faster_whisper import WhisperModel
from deep_translator import GoogleTranslator
import edge_tts
from pydub import AudioSegment

VIDEO_INPUT = sys.argv[1]
CHOICE = sys.argv[2]

# تحديد الصوت واللهجة بناء على اختيارك من القائمة
VOICE_MAP = {
    "الدارجة المغربية - صوت رجالي (جمال)": ("ar-MA-JamalNeural", True),
    "الدارجة المغربية - صوت نسائي (منى)": ("ar-MA-MounaNeural", True),
    "العربية الفصحى - صوت رجالي (حامد)": ("ar-SA-HamedNeural", False),
    "العربية الفصحى - صوت نسائي (سلمى)": ("ar-EG-SalmaNeural", False),
}

VOICE, IS_DARIJA = VOICE_MAP.get(CHOICE, ("ar-MA-JamalNeural", True))

# قاموس تحويل سريع من الفصحى للدارجة المغربية
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

async def generate_tts(text, filename, voice):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(filename)

def match_duration(audio_path, target_ms):
    sound = AudioSegment.from_file(audio_path)
    current_ms = len(sound)
    if current_ms > target_ms and target_ms > 500:
        ratio = min(current_ms / target_ms, 1.45)
        fast_path = audio_path.replace(".mp3", "_fast.mp3")
        subprocess.run([
            "ffmpeg", "-y", "-i", audio_path,
            "-filter:a", f"atempo={ratio}",
            "-vn", fast_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return AudioSegment.from_file(fast_path)
    return sound

async def main():
    os.makedirs("temp_tts", exist_ok=True)
    
    print("1. استخراج الصوت والتعرف على اللغة والنص...")
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, info = model.transcribe(VIDEO_INPUT, beam_size=5)
    print(f"اللغة المكتشفة: {info.language} | الاختيار: {CHOICE}")

    subprocess.run([
        "ffmpeg", "-y", "-i", VIDEO_INPUT, "-vn", "original_audio.wav"
    ], check=True)
    base_audio = AudioSegment.from_file("original_audio.wav") - 18
    dubbed_track = AudioSegment.silent(duration=len(base_audio))

    translator = GoogleTranslator(source="auto", target="ar")

    print("2. ترجمة الجمل وتوليد الصوت...")
    for i, seg in enumerate(segments):
        original_text = seg.text.strip()
        if not original_text:
            continue
            
        translated_text = translator.translate(original_text)
        if not translated_text:
            continue

        if IS_DARIJA:
            translated_text = to_darija(translated_text)

        start_ms = int(seg.start * 1000)
        end_ms = int(seg.end * 1000)
        slot_duration = end_ms - start_ms

        tts_file = f"temp_tts/seg_{i}.mp3"
        await generate_tts(translated_text, tts_file, VOICE)
        
        seg_audio = match_duration(tts_file, slot_duration)
        dubbed_track = dubbed_track.overlay(seg_audio, position=start_ms)
        print(f"[{seg.start:.1f}s -> {seg.end:.1f}s] {translated_text}")

    print("3. دمج الصوت المدبلج مع الفيديو النهائي...")
    final_audio = base_audio.overlay(dubbed_track)
    final_audio.export("final_dubbed.wav", format="wav")

    subprocess.run([
        "ffmpeg", "-y",
        "-i", VIDEO_INPUT,
        "-i", "final_dubbed.wav",
        "-c:v", "copy",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        "dubbed_video.mp4"
    ], check=True)
    print("تم إنتاج الفيديو المدبلج بنجاح!")

if __name__ == "__main__":
    asyncio.run(main())

import html
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ASR_MODEL = os.environ.get("ASR_MODEL", "small")
ASR_ENABLED = os.environ.get("ASR_ENABLED", "1") == "1"
MEDIA_TIMEOUT = int(os.environ.get("MEDIA_TIMEOUT", "600"))

_whisper_model = None


class ExtractionError(Exception):
    pass


def which_ffmpeg():
    return shutil.which("ffmpeg")


def decode_text(content):
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def docx_text(content):
    try:
        with zipfile.ZipFile(_bytes_io(content)) as archive:
            xml = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ExtractionError(f"Не удалось прочитать DOCX: {exc}")
    root = ET.fromstring(xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        parts = [node.text or "" for node in paragraph.iter(f"{namespace}t")]
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs).strip()


def _bytes_io(content):
    import io

    return io.BytesIO(content)


def html_text(content):
    text = decode_text(content)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def extract_text(filename, content):
    name = (filename or "").lower()
    if name.endswith(".docx"):
        return docx_text(content)
    if name.endswith((".html", ".htm")):
        return html_text(content)
    if name.endswith(".pdf"):
        raise ExtractionError("PDF не поддерживается напрямую: скопируйте текст или загрузите DOCX/TXT.")
    return decode_text(content).strip()


def run_ffmpeg(args, error_message):
    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        raise ExtractionError("ffmpeg не найден в системе — обработка аудио и видео недоступна.")
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        timeout=MEDIA_TIMEOUT,
    )
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", errors="replace")[-400:]
        raise ExtractionError(f"{error_message}: {tail or 'неизвестная ошибка ffmpeg'}")


def extract_audio(media_path):
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "audio.wav"
        run_ffmpeg(
            ["-y", "-i", str(media_path), "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(output)],
            "Не удалось извлечь аудио",
        )
        return output.read_bytes()


def extract_frames(media_path, interval=5, max_frames=12):
    with tempfile.TemporaryDirectory() as tmp:
        pattern = str(Path(tmp) / "frame_%03d.jpg")
        run_ffmpeg(
            ["-y", "-i", str(media_path), "-vf", f"fps=1/{max(1, interval)}", "-frames:v", str(max_frames), pattern],
            "Не удалось извлечь кадры",
        )
        frames = []
        for path in sorted(Path(tmp).glob("frame_*.jpg")):
            frames.append(path.read_bytes())
        return frames


def get_whisper():
    global _whisper_model
    if not ASR_ENABLED:
        raise ExtractionError("Распознавание речи отключено (ASR_ENABLED=0).")
    if _whisper_model is not None:
        return _whisper_model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ExtractionError(
            "faster-whisper не установлен. Установите: python -m pip install faster-whisper"
        )
    _whisper_model = WhisperModel(ASR_MODEL, device="cpu", compute_type="int8")
    return _whisper_model


def transcribe(audio_bytes):
    model = get_whisper()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "audio.wav"
        path.write_bytes(audio_bytes)
        segments, _ = model.transcribe(str(path), language="ru", vad_filter=True)
        text = " ".join(segment.text.strip() for segment in segments)
    return text.strip()


def extract_full(filename, content):
    name = (filename or "").lower()
    audio_ext = (".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".opus")
    video_ext = (".mp4", ".mov", ".avi", ".mkv", ".webm")
    if name.endswith(audio_ext) or name.endswith(video_ext):
        with tempfile.TemporaryDirectory() as tmp:
            media_path = Path(tmp) / (name or "media.bin")
            media_path.write_bytes(content)
            audio = extract_audio(media_path)
            text = transcribe(audio)
            frames = []
            if name.endswith(video_ext):
                try:
                    frames = extract_frames(media_path)
                except ExtractionError:
                    frames = []
        return {"text": text, "kind": "video" if name.endswith(video_ext) else "audio", "frames": frames}
    text = extract_text(filename, content)
    return {"text": text, "kind": "text", "frames": []}

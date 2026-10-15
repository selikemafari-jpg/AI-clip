from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import math
import shutil
import subprocess
import tempfile
import uuid
import wave

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse


# ============================================================
# CLIPSTREAM AI — BACKEND CONFIGURATION
# ============================================================

app = FastAPI(
    title="ClipStream AI API",
    description="Video clipping, highlight detection, transcription and export.",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
CLIPS_DIR = BASE_DIR / "clips"
TEMP_DIR = BASE_DIR / "temp"

for directory in (UPLOAD_DIR, CLIPS_DIR, TEMP_DIR):
    directory.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
MAX_CLIP_SECONDS = 300
MAX_GENERATED_CLIPS = 20

_whisper_model = None


# ============================================================
# GENERAL HELPERS
# ============================================================

def get_ffmpeg() -> str:
    """Find FFmpeg on the computer."""

    ffmpeg = shutil.which("ffmpeg")

    if not ffmpeg:
        raise HTTPException(
            status_code=500,
            detail=(
                "FFmpeg was not found. Install FFmpeg and ensure "
                "its bin folder is available in PATH."
            ),
        )

    return ffmpeg


def get_ffprobe() -> Optional[str]:
    """Find FFprobe if it is installed."""

    return shutil.which("ffprobe")


def safe_clip_path(clip_id: str) -> Path:
    """Return a validated path for a generated clip."""

    if not clip_id.isalnum():
        raise HTTPException(
            status_code=400,
            detail="Invalid clip ID.",
        )

    path = CLIPS_DIR / f"{clip_id}.mp4"

    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Clip not found.",
        )

    return path


def run_command(
    command: list[str],
    timeout: int = 600,
) -> subprocess.CompletedProcess:
    """Run a command and report useful errors."""

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=408,
            detail="The video operation timed out.",
        )

    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not start the video operation: {exc}",
        )

    return result


def run_ffmpeg(
    arguments: list[str],
    timeout: int = 600,
) -> None:
    """Execute FFmpeg and raise a readable error if it fails."""

    command = [get_ffmpeg(), "-hide_banner", "-y", *arguments]

    result = run_command(command, timeout=timeout)

    if result.returncode != 0:
        error_message = (result.stderr or result.stdout or "").strip()

        print("FFmpeg error:")
        print(error_message[-5000:])

        raise HTTPException(
            status_code=422,
            detail=(
                "FFmpeg could not process this video. "
                "Check the backend terminal for details."
            ),
        )


async def save_upload(file: UploadFile) -> Path:
    """Save an uploaded video without exceeding the size limit."""

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="No filename was provided.",
        )

    suffix = Path(file.filename).suffix.lower()

    allowed_extensions = {
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
        ".avi",
        ".m4v",
    }

    if suffix not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="Unsupported video format.",
        )

    destination = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    total_bytes = 0

    try:
        with destination.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)

                if not chunk:
                    break

                total_bytes += len(chunk)

                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="The video exceeds the 500 MB upload limit.",
                    )

                output.write(chunk)

    except Exception:
        destination.unlink(missing_ok=True)
        raise

    finally:
        await file.close()

    if total_bytes == 0:
        destination.unlink(missing_ok=True)

        raise HTTPException(
            status_code=400,
            detail="The uploaded file is empty.",
        )

    return destination


def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds using FFprobe."""

    ffprobe = get_ffprobe()

    if not ffprobe:
        raise HTTPException(
            status_code=500,
            detail="FFprobe was not found. Check your FFmpeg installation.",
        )

    result = run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        timeout=60,
    )

    if result.returncode != 0:
        raise HTTPException(
            status_code=422,
            detail="The uploaded file could not be read as a video.",
        )

    try:
        duration = float(result.stdout.strip())
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=422,
            detail="Could not determine the video duration.",
        )

    if not math.isfinite(duration) or duration <= 0:
        raise HTTPException(
            status_code=422,
            detail="The video has an invalid duration.",
        )

    return duration


def validate_clip_times(
    start_seconds: float,
    end_seconds: float,
    duration: float,
) -> float:
    """Validate a requested clip range."""

    if start_seconds < 0:
        raise HTTPException(
            status_code=400,
            detail="The start time cannot be negative.",
        )

    if end_seconds <= start_seconds:
        raise HTTPException(
            status_code=400,
            detail="The end time must be after the start time.",
        )

    if end_seconds > duration:
        raise HTTPException(
            status_code=400,
            detail="The end time exceeds the video duration.",
        )

    clip_duration = end_seconds - start_seconds

    if clip_duration > MAX_CLIP_SECONDS:
        raise HTTPException(
            status_code=400,
            detail="Clips cannot exceed five minutes.",
        )

    return clip_duration


def extract_audio_wav(
    video_path: Path,
    wav_path: Path,
) -> None:
    """Extract mono, 16 kHz audio for analysis and transcription."""

    run_ffmpeg(
        [
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        ],
        timeout=600,
    )

    if not wav_path.is_file() or wav_path.stat().st_size == 0:
        raise HTTPException(
            status_code=422,
            detail="Could not extract audio from this video.",
        )


def get_whisper_model():
    """Load Whisper only when transcription is requested."""

    global _whisper_model

    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel

            _whisper_model = WhisperModel(
                "base",
                device="cpu",
                compute_type="int8",
            )

        except Exception as exc:
            print(f"Whisper initialization failed: {exc}")

            raise HTTPException(
                status_code=500,
                detail=(
                    "Whisper could not start. Check that "
                    "faster-whisper is installed."
                ),
            )

    return _whisper_model


def transcribe_audio(wav_path: Path) -> list[dict]:
    """Transcribe extracted audio with Whisper."""

    model = get_whisper_model()

    try:
        segments, info = model.transcribe(
            str(wav_path),
            beam_size=5,
            vad_filter=True,
        )

        transcript = []

        for segment in segments:
            text = segment.text.strip()

            if not text:
                continue

            transcript.append(
                {
                    "start": round(float(segment.start), 2),
                    "end": round(float(segment.end), 2),
                    "text": text,
                }
            )

        return transcript

    except HTTPException:
        raise

    except Exception as exc:
        print(f"Transcription failed: {exc}")

        raise HTTPException(
            status_code=422,
            detail="Whisper could not transcribe this video's audio.",
        )


def create_clip(
    source_path: Path,
    start_seconds: float,
    end_seconds: float,
) -> tuple[str, Path]:
    """Create a new MP4 clip from a source video."""

    duration = end_seconds - start_seconds

    if duration <= 0 or duration > MAX_CLIP_SECONDS:
        raise HTTPException(
            status_code=400,
            detail="Invalid clip duration.",
        )

    clip_id = uuid.uuid4().hex
    output_path = CLIPS_DIR / f"{clip_id}.mp4"

    try:
        run_ffmpeg(
            [
                "-ss",
                str(start_seconds),
                "-i",
                str(source_path),
                "-t",
                str(duration),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            timeout=900,
        )

        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise HTTPException(
                status_code=422,
                detail="FFmpeg did not produce a valid clip.",
            )

        return clip_id, output_path

    except Exception:
        output_path.unlink(missing_ok=True)
        raise


def calculate_audio_energy(
    wav_path: Path,
) -> list[tuple[float, float]]:
    """Calculate approximate one-second audio RMS energy."""

    results = []

    try:
        with wave.open(str(wav_path), "rb") as audio:
            sample_rate = audio.getframerate()
            sample_width = audio.getsampwidth()
            channels = audio.getnchannels()

            if sample_width != 2:
                raise HTTPException(
                    status_code=422,
                    detail="Audio must be 16-bit PCM for analysis.",
                )

            frames_per_second = sample_rate
            second_index = 0

            while True:
                raw = audio.readframes(frames_per_second)

                if not raw:
                    break

                sample_count = len(raw) // 2

                if sample_count == 0:
                    second_index += 1
                    continue

                import array

                samples = array.array("h")
                samples.frombytes(raw)

                # Account for multi-channel audio if encountered.
                if channels > 1:
                    samples = samples[::channels]

                if not samples:
                    second_index += 1
                    continue

                mean_square = sum(
                    sample * sample for sample in samples
                ) / len(samples)

                rms = math.sqrt(mean_square)

                results.append(
                    (
                        float(second_index),
                        float(rms),
                    )
                )

                second_index += 1

    except HTTPException:
        raise

    except Exception as exc:
        print(f"Audio analysis failed: {exc}")

        raise HTTPException(
            status_code=422,
            detail="Could not analyze this video's audio.",
        )

    return results


def choose_loud_windows(
    energy: list[tuple[float, float]],
    duration: float,
    max_clips: int = 20,
    clip_length: int = 30,
) -> list[tuple[float, float, float]]:
    """Select loud, reasonably separated candidate clip windows."""

    if not energy:
        return []

    candidates = []

    for second, rms in energy:
        start = max(0.0, second - clip_length / 2)
        end = min(duration, start + clip_length)

        if end - start < 5:
            continue

        candidates.append((start, end, rms))

    candidates.sort(
        key=lambda item: item[2],
        reverse=True,
    )

    selected = []

    for start, end, score in candidates:
        overlaps = any(
            not (
                end <= existing_start
                or start >= existing_end
            )
            for existing_start, existing_end, _ in selected
        )

        if overlaps:
            continue

        selected.append((start, end, score))

        if len(selected) >= max_clips:
            break

    selected.sort(key=lambda item: item[0])

    return selected


# ============================================================
# HEALTH AND DASHBOARD
# ============================================================

@app.get("/")
def root():
    return {
        "message": "Your ClipStream AI backend is running successfully!",
        "version": app.version,
    }


@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/dashboard")
def dashboard():
    clips = list(CLIPS_DIR.glob("*.mp4"))

    return {
        "status": "online",
        "message": "ClipStream AI backend is running.",
        "clips_ready": len(clips),
        "detected": len(clips),
        "failed": 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================
# MANUAL VIDEO TRIMMING
# ============================================================

@app.post("/api/clips/trim")
async def trim_clip(
    file: UploadFile = File(...),
    start_seconds: float = Form(...),
    end_seconds: float = Form(...),
):
    source_path = await save_upload(file)

    try:
        duration = get_video_duration(source_path)

        validate_clip_times(
            start_seconds,
            end_seconds,
            duration,
        )

        clip_id, output_path = create_clip(
            source_path,
            start_seconds,
            end_seconds,
        )

        return {
            "success": True,
            "clip_id": clip_id,
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
            "duration": round(end_seconds - start_seconds, 2),
            "download_url": f"/api/clips/{clip_id}/download",
            "preview_url": f"/api/clips/{clip_id}/preview",
            "file_size": output_path.stat().st_size,
        }

    finally:
        source_path.unlink(missing_ok=True)


# ============================================================
# AUDIO-BASED HIGHLIGHT DETECTION
# ============================================================

@app.post("/api/clips/highlights")
async def generate_highlights(
    file: UploadFile = File(...),
    max_clips: int = Form(10),
    clip_length: int = Form(30),
):
    max_clips = max(1, min(max_clips, MAX_GENERATED_CLIPS))
    clip_length = max(5, min(clip_length, 90))

    source_path = await save_upload(file)
    wav_path = TEMP_DIR / f"{uuid.uuid4().hex}.wav"

    created_clips = []

    try:
        duration = get_video_duration(source_path)

        extract_audio_wav(
            source_path,
            wav_path,
        )

        energy = calculate_audio_energy(wav_path)

        windows = choose_loud_windows(
            energy,
            duration,
            max_clips=max_clips,
            clip_length=clip_length,
        )

        for start, end, score in windows:
            try:
                clip_id, output_path = create_clip(
                    source_path,
                    start,
                    end,
                )

                created_clips.append(
                    {
                        "clip_id": clip_id,
                        "start": round(start, 2),
                        "end": round(end, 2),
                        "duration": round(end - start, 2),
                        "audio_score": round(score, 2),
                        "download_url": (
                            f"/api/clips/{clip_id}/download"
                        ),
                        "preview_url": (
                            f"/api/clips/{clip_id}/preview"
                        ),
                    }
                )

            except HTTPException as exc:
                print(f"Could not create highlight: {exc.detail}")

        return {
            "success": True,
            "method": "audio_energy",
            "total_clips": len(created_clips),
            "clips": created_clips,
            "note": (
                "Loudness helps locate possible highlights, "
                "but does not guarantee that a moment is funny "
                "or interesting."
            ),
        }

    finally:
        source_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)


# ============================================================
# VIDEO TRANSCRIPTION
# ============================================================

@app.post("/api/clips/transcribe")
async def transcribe_video(
    file: UploadFile = File(...),
):
    source_path = await save_upload(file)
    wav_path = TEMP_DIR / f"{uuid.uuid4().hex}.wav"

    try:
        extract_audio_wav(
            source_path,
            wav_path,
        )

        transcript = transcribe_audio(wav_path)

        return {
            "success": True,
            "segments": transcript,
            "total_segments": len(transcript),
        }

    finally:
        source_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)


# ============================================================
# STREAMER-FOCUSED HIGHLIGHT DETECTION
# ============================================================

@app.post("/api/clips/highlights-v2")
async def generate_streamer_highlights(
    file: UploadFile = File(...),
    max_clips: int = Form(10),
):
    max_clips = max(1, min(max_clips, MAX_GENERATED_CLIPS))

    source_path = await save_upload(file)
    wav_path = TEMP_DIR / f"{uuid.uuid4().hex}.wav"

    created_clips = []

    # Phrases commonly associated with reactions or excitement.
    streamer_phrases = [
        "oh my god",
        "no way",
        "let's go",
        "lets go",
        "bro what",
        "what just happened",
        "that's crazy",
        "thats crazy",
        "are you serious",
        "you are joking",
        "i can't believe",
        "i cannot believe",
        "what the hell",
        "what is happening",
        "this is insane",
        "that is insane",
        "i'm dead",
        "im dead",
        "chat",
        "look at this",
        "listen",
        "help me",
        "get out of here",
        "no no no",
    ]

    cue_words = {
        "crazy",
        "insane",
        "screaming",
        "scream",
        "laughing",
        "laugh",
        "hilarious",
        "wild",
        "unbelievable",
        "clutch",
        "bruh",
        "bro",
        "lmao",
        "wow",
        "damn",
        "wait",
        "what",
        "no",
        "yes",
        "finally",
        "destroyed",
        "amazing",
        "ridiculous",
        "legendary",
        "craziest",
    }

    try:
        duration = get_video_duration(source_path)

        extract_audio_wav(
            source_path,
            wav_path,
        )

        transcript = transcribe_audio(wav_path)

        energy = calculate_audio_energy(wav_path)

        # Keep moments with their start/end times and scores.
        candidates = []

        for segment in transcript:
            text = segment["text"].lower()
            tokens = set(
                text.replace("!", " ")
                .replace("?", " ")
                .replace(".", " ")
                .replace(",", " ")
                .split()
            )

            phrase_hits = sum(
                1
                for phrase in streamer_phrases
                if phrase in text
            )

            word_hits = sum(
                1
                for word in tokens
                if word in cue_words
            )

            # Reward expressive language and energetic phrases.
            score = (
                phrase_hits * 4.0
                + word_hits * 1.5
            )

            # Skip ordinary transcript segments with no cue hits.
            if score <= 0:
                continue

            start = max(0.0, segment["start"] - 8.0)
            end = min(duration, segment["end"] + 10.0)

            if end - start < 5:
                continue

            # Add an audio-energy bonus for energetic moments.
            nearby_energy = [
                rms
                for second, rms in energy
                if start <= second <= end
            ]

            if nearby_energy:
                score += (
                    sum(nearby_energy) / len(nearby_energy)
                ) / 5000.0

            candidates.append(
                {
                    "start": start,
                    "end": end,
                    "score": score,
                    "transcript": segment["text"],
                }
            )

        # If dialogue cues are unavailable, use audio peaks.
        if not candidates:
            for start, end, score in choose_loud_windows(
                energy,
                duration,
                max_clips=max_clips,
                clip_length=30,
            ):
                candidates.append(
                    {
                        "start": start,
                        "end": end,
                        "score": score,
                        "transcript": "",
                    }
                )

        candidates.sort(
            key=lambda candidate: candidate["score"],
            reverse=True,
        )

        selected = []

        for candidate in candidates:
            overlaps = any(
                not (
                    candidate["end"] <= existing["start"]
                    or candidate["start"] >= existing["end"]
                )
                for existing in selected
            )

            if overlaps:
                continue

            selected.append(candidate)

            if len(selected) >= max_clips:
                break

        selected.sort(key=lambda candidate: candidate["start"])

        for candidate in selected:
            try:
                clip_id, output_path = create_clip(
                    source_path,
                    candidate["start"],
                    candidate["end"],
                )

                created_clips.append(
                    {
                        "clip_id": clip_id,
                        "start": round(candidate["start"], 2),
                        "end": round(candidate["end"], 2),
                        "duration": round(
                            candidate["end"] - candidate["start"],
                            2,
                        ),
                        "score": round(candidate["score"], 2),
                        "transcript": candidate["transcript"],
                        "download_url": (
                            f"/api/clips/{clip_id}/download"
                        ),
                        "preview_url": (
                            f"/api/clips/{clip_id}/preview"
                        ),
                    }
                )

            except HTTPException as exc:
                print(
                    "Could not create streamer highlight: "
                    f"{exc.detail}"
                )

        return {
            "success": True,
            "method": "streamer_dialogue_and_audio",
            "total_clips": len(created_clips),
            "clips": created_clips,
            "note": (
                "These are candidate highlights based on "
                "transcript cues and audio energy. They still "
                "need testing against real streamer videos."
            ),
        }

    finally:
        source_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)


# ============================================================
# LIST GENERATED CLIPS
# ============================================================

@app.get("/api/clips")
def list_clips():
    clips = []

    for path in sorted(
        CLIPS_DIR.glob("*.mp4"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        clip_id = path.stem

        clips.append(
            {
                "clip_id": clip_id,
                "filename": path.name,
                "file_size": path.stat().st_size,
                "created_at": datetime.fromtimestamp(
                    path.stat().st_mtime,
                    tz=timezone.utc,
                ).isoformat(),
                "download_url": (
                    f"/api/clips/{clip_id}/download"
                ),
                "preview_url": (
                    f"/api/clips/{clip_id}/preview"
                ),
            }
        )

    return {
        "success": True,
        "total_clips": len(clips),
        "clips": clips,
    }


# ============================================================
# CLIP FORMAT AND CAPTION EXPORT
# ============================================================

@app.post("/api/clips/{clip_id}/format")
def format_clip(
    clip_id: str,
    aspect_ratio: str = Form(...),
    caption: str = Form(""),
):
    """
    Export an existing clip in portrait, landscape or square format.

    Supported aspect ratios:
        9:16 — portrait
        16:9 — landscape
        1:1  — square
    """

    source_path = safe_clip_path(clip_id)

    # IMPORTANT:
    # This dictionary must remain OUTSIDE the file-existence check.
    # Otherwise, valid clips will trigger an UnboundLocalError.
    format_filters = {
        "9:16": (
            "scale=1080:1920:"
            "force_original_aspect_ratio=increase,"
            "crop=1080:1920"
        ),
        "16:9": (
            "scale=1920:1080:"
            "force_original_aspect_ratio=increase,"
            "crop=1920:1080"
        ),
        "1:1": (
            "scale=1080:1080:"
            "force_original_aspect_ratio=increase,"
            "crop=1080:1080"
        ),
    }

    if aspect_ratio not in format_filters:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported aspect ratio. Choose 9:16, 16:9 or 1:1."
            ),
        )

    output_id = uuid.uuid4().hex
    output_path = CLIPS_DIR / f"{output_id}.mp4"

    video_filter = format_filters[aspect_ratio]
    caption_file = None

    try:
        cleaned_caption = caption.strip()

        if cleaned_caption:
            # Using a text file avoids many FFmpeg quoting issues
            # caused by punctuation in captions.
            caption_file = (
                TEMP_DIR / f"{uuid.uuid4().hex}.txt"
            )

            caption_file.write_text(
                cleaned_caption,
                encoding="utf-8",
            )

            # FFmpeg filter paths need escaping for special characters.
            caption_path = caption_file.resolve().as_posix()
            caption_path = caption_path.replace("\\", "/")
            caption_path = caption_path.replace(":", r"\:")
            caption_path = caption_path.replace("'", r"\'")

            font_path = Path("C:/Windows/Fonts/arial.ttf")

            if font_path.is_file():
                escaped_font = font_path.as_posix()
                escaped_font = escaped_font.replace(":", r"\:")

                font_option = (
                    f"fontfile='{escaped_font}':"
                )
            else:
                font_option = ""

            video_filter += (
                f",drawtext={font_option}"
                f"textfile='{caption_path}':"
                "fontcolor=white:"
                "fontsize=48:"
                "borderw=3:"
                "bordercolor=black:"
                "box=1:"
                "boxcolor=black@0.45:"
                "boxborderw=12:"
                "x=(w-text_w)/2:"
                "y=h-text_h-80"
            )

        run_ffmpeg(
            [
                "-i",
                str(source_path),
                "-vf",
                video_filter,
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            timeout=900,
        )

        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise HTTPException(
                status_code=422,
                detail="The formatted clip could not be exported.",
            )

        return {
            "success": True,
            "original_clip_id": clip_id,
            "clip_id": output_id,
            "aspect_ratio": aspect_ratio,
            "caption_added": bool(cleaned_caption),
            "file_size": output_path.stat().st_size,
            "download_url": (
                f"/api/clips/{output_id}/download"
            ),
            "preview_url": (
                f"/api/clips/{output_id}/preview"
            ),
        }

    except Exception:
        output_path.unlink(missing_ok=True)
        raise

    finally:
        if caption_file:
            caption_file.unlink(missing_ok=True)


# ============================================================
# CLIP PREVIEW AND DOWNLOAD
# ============================================================

@app.get("/api/clips/{clip_id}/preview")
def preview_clip(clip_id: str):
    path = safe_clip_path(clip_id)

    return FileResponse(
        path=path,
        media_type="video/mp4",
        filename=path.name,
    )


@app.get("/api/clips/{clip_id}/download")
def download_clip(clip_id: str):
    path = safe_clip_path(clip_id)

    return FileResponse(
        path=path,
        media_type="video/mp4",
        filename=path.name,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{path.name}"'
            ),
        },
    )
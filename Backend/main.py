from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import uuid
import math
import struct
import wave
from faster_whisper import WhisperModel

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI(
    title="ClipStream AI API",
    description="Backend for the AI-powered streamer clipping platform",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
CLIPS_DIR = BASE_DIR / "clips"

UPLOAD_DIR.mkdir(exist_ok=True)
CLIPS_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB
MAX_CLIP_SECONDS = 300  # 5 minutes


def get_ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise HTTPException(
            status_code=500,
            detail="FFmpeg was not found. Check your PATH.",
        )
    return executable

# Whisper speech-recognition model
_whisper_model = None


def get_whisper_model():
    global _whisper_model

    if _whisper_model is None:
        print("Loading Whisper Base model...")

        _whisper_model = WhisperModel(
            "base",
            device="cpu",
            compute_type="int8",
        )

        print("Whisper Base model loaded!")

    return _whisper_model

@app.get("/")
def home():
    return {
        "app": "ClipStream API",
        "message": "Your backend is running successfully!",
        "status": "online",
    }


@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/dashboard")
def dashboard():
    return {
        "message": "Waiting for a Twitch stream connection",
        "stream_status": "offline",
        "clips_ready": sum(1 for p in CLIPS_DIR.glob("*.mp4")),
        "clips_detected": 0,
        "clips_failed": 0,
    }


@app.post("/api/clips/trim")
async def trim_video(
    video: UploadFile = File(...),
    start_seconds: float = Form(...),
    end_seconds: float = Form(...),
):
    if not video.filename:
        raise HTTPException(status_code=400, detail="Choose a video file.")

    if not video.content_type or not video.content_type.startswith("video/"):
        raise HTTPException(
            status_code=400,
            detail="Upload a video file with a recognized video content type.",
        )

    if (
        start_seconds < 0
        or end_seconds <= start_seconds
        or end_seconds - start_seconds > MAX_CLIP_SECONDS
    ):
        raise HTTPException(
            status_code=400,
            detail="Choose valid start and end times. Clips must be 5 minutes or shorter.",
        )

    clip_id = uuid.uuid4().hex
    input_path = UPLOAD_DIR / f"{clip_id}_source"
    output_path = CLIPS_DIR / f"{clip_id}.mp4"

    total_bytes = 0

    try:
        with input_path.open("wb") as destination:
            while True:
                chunk = await video.read(1024 * 1024)
                if not chunk:
                    break

                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Video exceeds the 500 MB upload limit.",
                    )

                destination.write(chunk)

        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")

        command = [
            get_ffmpeg(),
            "-y",
            "-ss", str(start_seconds),
            "-i", str(input_path),
            "-t", str(end_seconds - start_seconds),
            "-map", "0:v:0",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(output_path),
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )

        if result.returncode != 0 or not output_path.exists():
            raise HTTPException(
                status_code=422,
                detail="FFmpeg could not process this video. Try a different video file.",
            )

        return {
            "message": "Clip created successfully.",
            "clip_id": clip_id,
            "duration_seconds": end_seconds - start_seconds,
            "download_url": f"/api/clips/{clip_id}/download",
        }

    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=408,
            detail="Video processing took too long. Try a shorter video.",
        )
    finally:
        await video.close()
        input_path.unlink(missing_ok=True)

# Automatic Highlight Detection
@app.post("/api/clips/highlights")
async def detect_highlights(
    video: UploadFile = File(...)
):
    if not video.filename:
        raise HTTPException(
            status_code=400,
            detail="Please choose a video file."
        )

    if not video.content_type or not video.content_type.startswith("video/"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a valid video file."
        )

    job_id = uuid.uuid4().hex

    input_path = UPLOAD_DIR / f"{job_id}_source"
    audio_path = UPLOAD_DIR / f"{job_id}_audio.wav"

    total_bytes = 0

    try:
        # Save the uploaded video
        with input_path.open("wb") as destination:
            while True:
                chunk = await video.read(1024 * 1024)

                if not chunk:
                    break

                total_bytes += len(chunk)

                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Video exceeds the 500 MB upload limit."
                    )

                destination.write(chunk)

        if total_bytes == 0:
            raise HTTPException(
                status_code=400,
                detail="The uploaded video is empty."
            )

        # Extract audio using FFmpeg
        command = [
            get_ffmpeg(),
            "-y",
            "-i", str(input_path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(audio_path)
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            check=False
        )

        if result.returncode != 0 or not audio_path.exists():
            raise HTTPException(
                status_code=422,
                detail="Could not extract audio. The video may not contain audio."
            )

        # Analyze audio intensity
        with wave.open(str(audio_path), "rb") as audio:
            sample_rate = audio.getframerate()
            sample_width = audio.getsampwidth()
            channels = audio.getnchannels()
            total_frames = audio.getnframes()

            if sample_width != 2:
                raise HTTPException(
                    status_code=422,
                    detail="Unsupported audio format."
                )

            if total_frames == 0:
                raise HTTPException(
                    status_code=422,
                    detail="The video contains no usable audio."
                )

            duration = total_frames / sample_rate

            if duration < 3:
                raise HTTPException(
                    status_code=422,
                    detail="The video must be at least 3 seconds long."
                )

            energies = []
            frames_per_second = sample_rate

            while True:
                raw_audio = audio.readframes(frames_per_second)

                if not raw_audio:
                    break

                sample_count = len(raw_audio) // 2

                if sample_count == 0:
                    continue

                samples = struct.unpack(
                    "<" + ("h" * sample_count),
                    raw_audio[:sample_count * 2]
                )

                squared_total = sum(
                    sample * sample for sample in samples
                )

                rms = math.sqrt(
                    squared_total / len(samples)
                )

                energies.append(rms)

        # Find the loudest non-overlapping moments
        clip_duration = min(15, max(3, int(duration / 3)))
        max_clips = 20

        candidates = []

        for start in range(
            0,
            max(1, len(energies) - clip_duration + 1)
        ):
            end = min(
                start + clip_duration,
                len(energies)
            )

            average_energy = sum(
                energies[start:end]
            ) / (end - start)

            candidates.append({
                "start_seconds": start,
                "end_seconds": min(
                    start + clip_duration,
                    int(duration)
                ),
                "score": average_energy
            })

        candidates.sort(
            key=lambda item: item["score"],
            reverse=True
        )

        selected = []

        for candidate in candidates:
            overlaps = any(
                candidate["start_seconds"] < item["end_seconds"]
                and candidate["end_seconds"] > item["start_seconds"]
                for item in selected
            )

            if not overlaps:
                selected.append(candidate)

            if len(selected) >= max_clips:
                break

        # Create clips from the selected moments
        created_clips = []

        for moment in selected:
            clip_id = uuid.uuid4().hex
            output_path = CLIPS_DIR / f"{clip_id}.mp4"

            clip_command = [
                get_ffmpeg(),
                "-y",
                "-ss", str(moment["start_seconds"]),
                "-i", str(input_path),
                "-t", str(
                    moment["end_seconds"] - moment["start_seconds"]
                ),
                "-map", "0:v:0",
                "-map", "0:a?",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-c:a", "aac",
                "-movflags", "+faststart",
                str(output_path)
            ]

            clip_result = subprocess.run(
                clip_command,
                capture_output=True,
                text=True,
                timeout=600,
                check=False
            )

            if clip_result.returncode == 0 and output_path.exists():
                created_clips.append({
                    "clip_id": clip_id,
                    "start_seconds": moment["start_seconds"],
                    "end_seconds": moment["end_seconds"],
                    "download_url": f"/api/clips/{clip_id}/download"
                })

        if not created_clips:
            raise HTTPException(
                status_code=422,
                detail="No clips could be created from this video."
            )

        return {
            "message": "Automatic highlight analysis completed.",
            "method": "Audio intensity analysis",
            "clips_created": len(created_clips),
            "highlights": created_clips
        }

    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=408,
            detail="Video analysis took too long. Try a shorter video."
        )

    finally:
        await video.close()
        input_path.unlink(missing_ok=True)
        audio_path.unlink(missing_ok=True)

        # ClipStream AI V2: Speech Transcription
@app.post("/api/clips/transcribe")
async def transcribe_video(video: UploadFile = File(...)):
    if not video.filename:
        raise HTTPException(
            status_code=400,
            detail="Please choose a video file."
        )

    if not video.content_type or not video.content_type.startswith("video/"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a video file."
        )

    job_id = uuid.uuid4().hex

    input_path = UPLOAD_DIR / f"{job_id}_source"
    audio_path = UPLOAD_DIR / f"{job_id}_audio.wav"

    total_bytes = 0

    try:
        # Save the uploaded video
        with input_path.open("wb") as destination:
            while True:
                chunk = await video.read(1024 * 1024)

                if not chunk:
                    break

                total_bytes += len(chunk)

                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Video exceeds the 500 MB upload limit."
                    )

                destination.write(chunk)

        if total_bytes == 0:
            raise HTTPException(
                status_code=400,
                detail="The uploaded video is empty."
            )

        # Extract audio for speech recognition
        command = [
            get_ffmpeg(),
            "-y",
            "-i", str(input_path),
            "-map", "0:a:0",
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(audio_path)
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            check=False
        )

        if result.returncode != 0 or not audio_path.exists():
            raise HTTPException(
                status_code=422,
                detail="Could not extract audio. Check that the video contains an audio track."
            )

        # Load the Whisper model and transcribe the audio
        model = get_whisper_model()

        segments, info = model.transcribe(
            str(audio_path),
            beam_size=5,
            vad_filter=True
        )

        transcript_segments = []

        for segment in segments:
            transcript_segments.append({
                "start_seconds": round(segment.start, 2),
                "end_seconds": round(segment.end, 2),
                "text": segment.text.strip()
            })

        full_transcript = " ".join(
            segment["text"]
            for segment in transcript_segments
        )

        return {
            "message": "Speech transcription completed.",
            "language": info.language,
            "language_probability": round(
                info.language_probability, 3
            ),
            "segments_detected": len(transcript_segments),
            "transcript": full_transcript,
            "segments": transcript_segments
        }

    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=408,
            detail="Audio extraction took too long. Try a shorter video."
        )

    finally:
        await video.close()
        input_path.unlink(missing_ok=True)
        audio_path.unlink(missing_ok=True)

        # ClipStream AI V2: Dialogue-Based Highlight Detection
@app.post("/api/clips/highlights-v2")
async def detect_highlights_v2(video: UploadFile = File(...)):
    if not video.filename:
        raise HTTPException(
            status_code=400,
            detail="Please choose a video file."
        )

    if not video.content_type or not video.content_type.startswith("video/"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a video file."
        )

    job_id = uuid.uuid4().hex

    input_path = UPLOAD_DIR / f"{job_id}_source"
    audio_path = UPLOAD_DIR / f"{job_id}_audio.wav"

    total_bytes = 0
    created_paths = []

    try:
        # Save the uploaded video
        with input_path.open("wb") as destination:
            while True:
                chunk = await video.read(1024 * 1024)

                if not chunk:
                    break

                total_bytes += len(chunk)

                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Video exceeds the 500 MB upload limit."
                    )

                destination.write(chunk)

        if total_bytes == 0:
            raise HTTPException(
                status_code=400,
                detail="The uploaded video is empty."
            )

        # Extract audio for transcription
        command = [
            get_ffmpeg(),
            "-y",
            "-i", str(input_path),
            "-map", "0:a:0",
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(audio_path)
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            check=False
        )

        if result.returncode != 0 or not audio_path.exists():
            raise HTTPException(
                status_code=422,
                detail="Could not extract audio. Make sure the video contains an audio track."
            )

        # Get the audio duration
        with wave.open(str(audio_path), "rb") as audio:
            sample_rate = audio.getframerate()
            total_frames = audio.getnframes()

            if sample_rate <= 0 or total_frames <= 0:
                raise HTTPException(
                    status_code=422,
                    detail="The video contains no usable audio."
                )

            duration = total_frames / sample_rate

        # Transcribe dialogue using Whisper
        model = get_whisper_model()

        whisper_segments, info = model.transcribe(
            str(audio_path),
            beam_size=5,
            vad_filter=True
        )

        transcript_segments = []

        for segment in whisper_segments:
            text = segment.text.strip()

            if text:
                transcript_segments.append({
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "text": text
                })

        if not transcript_segments:
            raise HTTPException(
                status_code=422,
                detail="No recognizable speech was found in this video."
            )

        # Phrases that may signal an important dramatic exchange
        dramatic_phrases = [
            "tell me the truth",
            "you lied to me",
            "how could you",
            "what did you do",
            "we need to talk",
            "i love you",
            "i hate you",
            "i am sorry",
            "i'm sorry",
            "don't leave me",
            "leave me alone",
            "who are you",
            "what happened",
            "are you serious",
            "i swear",
            "i promise",
            "it's over",
            "it is over",
            "the truth",
            "you don't understand",
            "you do not understand",
            "i know what you did",
            "i am pregnant",
            "i'm pregnant",
            "you killed"
        ]

        cue_words = {
            "truth", "secret", "lied", "lie", "pregnant",
            "divorce", "cheated", "betrayal", "revenge",
            "arrest", "dead", "died", "killed", "sorry",
            "hate", "promise", "forgive", "married",
            "husband", "wife", "please", "never"
        }
                # Streamer-focused highlight detection
        # Combines dialogue cues, audio intensity, and surrounding context.

        streamer_phrases = [
            "oh my god",
            "no way",
            "what just happened",
            "are you serious",
            "you've got to be kidding",
            "you gotta be kidding",
            "let's go",
            "lets go",
            "bro what",
            "that's crazy",
            "that is crazy",
            "that's insane",
            "that is insane",
            "what the hell",
            "what the fuck",
            "i can't believe",
            "i'm dead",
            "im dead",
            "that's wild",
            "that was crazy",
            "that was insane",
            "you are crazy",
            "he's cheating",
            "she's cheating",
            "we actually won",
            "we actually lost",
            "no no no",
            "what are you doing",
            "get out of here",
            "shut up",
            "come on",
            "i swear",
            "i did not expect that",
        ]

        streamer_words = {
            "bro",
            "bruh",
            "chat",
            "crazy",
            "insane",
            "wild",
            "laughing",
            "screaming",
            "lmao",
            "clutch",
            "cheating",
            "scared",
            "jumped",
            "surprise",
            "surprised",
            "wow",
            "damn",
            "no",
            "help",
            "run",
            "win",
            "won",
            "lost",
            "rage",
            "bruh",
            "unbelievable",
        }

        # Measure audio intensity in one-second intervals.
        # The extracted WAV is mono, 16-bit PCM audio.
        audio_energy = []

        with wave.open(str(audio_path), "rb") as audio:
            sample_rate = audio.getframerate()
            sample_width = audio.getsampwidth()

            if sample_width != 2:
                raise HTTPException(
                    status_code=422,
                    detail="Unsupported audio format for analysis."
                )

            while True:
                raw_audio = audio.readframes(sample_rate)

                if not raw_audio:
                    break

                sample_count = len(raw_audio) // 2

                if sample_count == 0:
                    continue

                samples = struct.unpack(
                    "<" + ("h" * sample_count),
                    raw_audio[:sample_count * 2]
                )

                squared_total = sum(
                    sample * sample for sample in samples
                )

                rms = math.sqrt(
                    squared_total / sample_count
                )

                audio_energy.append(rms)

        # Find relatively loud moments compared with the video's audio.
        loud_seconds = []

        if audio_energy:
            ordered_energy = sorted(audio_energy)

            percentile_index = min(
                len(ordered_energy) - 1,
                int(len(ordered_energy) * 0.85)
            )

            loudness_threshold = max(
                700.0,
                ordered_energy[percentile_index]
            )

            loud_seconds = [
                second
                for second, energy in enumerate(audio_energy)
                if energy >= loudness_threshold and energy > 700
            ]

        loud_seconds_set = set(loud_seconds)

        # Score transcript segments for possible streamer highlights.
        seeds = []

        for segment in transcript_segments:
            text = segment["text"].strip()
            lower_text = text.lower()

            tokens = {
                word.strip(".,!?;:\"'()[]{}")
                for word in lower_text.split()
            }

            dramatic_phrase_hits = sum( 1
                for phrase in dramatic_phrases
                if phrase in lower_text
            )
            
            streamer_phrase_hits = sum(
                1
                for phrase in streamer_phrases
                if phrase in lower_text
            )
            
            phrase_hits = dramatic_phrase_hits + streamer_phrase_hits
            
        
            word_hits = sum(          
                1
                for word in tokens
                    if word in cue_words or word in streamer_words

             )
        
        

        
            
   
            score = (
                phrase_hits * 2.2
                + min(word_hits, 4) * 0.7
            )

            if "!" in text:
                score += 0.5

            if "?" in text:
                score += 0.3

            segment_start = max(
                0, int(segment["start"]) - 1
            )

            segment_end = min(
                len(audio_energy),
                int(segment["end"]) + 2
            )

            has_loud_audio = any(
                second in loud_seconds_set
                for second in range(segment_start, segment_end)
            )

            if has_loud_audio:
                score += 1.2

            if score >= 1.5:
                seeds.append({
                    "start": segment["start"],
                    "end": segment["end"],
                    "score": score,
                })

        # Audio peaks can also be candidates when a reaction contains
        # little or no recognizable speech.
        for second in loud_seconds:
            seeds.append({
                "start": float(second),
                "end": min(duration, float(second + 1)),
                "score": 1.8,
            })

        # Fallback: if no strong cues were found, consider longer
        # spoken segments instead of returning nothing immediately.
        if not seeds:
            for segment in transcript_segments:
                word_count = len(segment["text"].split())

                if word_count >= 8:
                    seeds.append({
                        "start": segment["start"],
                        "end": segment["end"],
                        "score": 0.8,
                    })

        if not seeds:
            raise HTTPException(
                status_code=422,
                detail=(
                    "No suitable candidate moments were detected. "
                    "Try a video with clearer speech or stronger reactions."
                )
            )

        # Group nearby signals into possible events.
        # Nearby signals may belong to the same joke, reaction, or exchange.
        seeds.sort(key=lambda item: item["start"])

        event_groups = []

        for seed in seeds:
            start_new_group = (
                not event_groups
                or seed["start"] - event_groups[-1]["end"] > 12
                or seed["start"] - event_groups[-1]["start"] > 65
            )

            if start_new_group:
                event_groups.append({
                    "start": seed["start"],
                    "end": seed["end"],
                    "score": seed["score"],
                })
            else:
                group = event_groups[-1]

                group["end"] = max(
                    group["end"],
                    seed["end"]
                )

                group["score"] += seed["score"]

        # Choose adaptive boundaries.
        # Short reactions stay short; longer sequences receive more context.
        MIN_CLIP_SECONDS = min(10.0, duration)
        MAX_CLIP_SECONDS = min(90.0, duration)

        candidates = []

        for group in event_groups:
            # Include context before and after the main event.
            start = max(
                0.0,
                group["start"] - 6.0
            )

            end = min(
                duration,
                group["end"] + 8.0
            )

            # Ensure the clip isn't unnecessarily short.
            if end - start < MIN_CLIP_SECONDS:
                missing_time = MIN_CLIP_SECONDS - (end - start)

                start = max(
                    0.0,
                    start - missing_time / 2
                )

                end = min(
                    duration,
                    end + missing_time / 2
                )

            # Never exceed the maximum duration.
            if end - start > MAX_CLIP_SECONDS:
                center = (start + end) / 2

                start = max(
                    0.0,
                    center - MAX_CLIP_SECONDS / 2
                )

                end = min(
                    duration,
                    start + MAX_CLIP_SECONDS
                )

                # Adjust the start if the video ends before the
                # full permitted duration has been reached.
                start = max(
                    0.0,
                    end - MAX_CLIP_SECONDS
                )

            if end <= start:
                continue

            # Include the dialogue from the selected interval.
            window_segments = [
                segment
                for segment in transcript_segments
                if segment["start"] < end
                and segment["end"] > start
            ]

            transcript_excerpt = " ".join(
                segment["text"]
                for segment in window_segments
            ).strip()

            # Give more context-rich candidates a small ranking bonus.
            excerpt_word_count = len(
                transcript_excerpt.split()
            )

            ranking_score = (
                group["score"]
                + min(excerpt_word_count / 50, 2.0)
            )

            candidates.append({
                "start_seconds": round(start, 2),
                "end_seconds": round(end, 2),
                "score": round(ranking_score, 2),
                "transcript_excerpt": transcript_excerpt[:400],
            })

        # Rank the candidates and avoid duplicate or overlapping clips.
        candidates.sort(
            key=lambda item: item["score"],
            reverse=True
        )

        max_clips = 10
        selected = []

        for candidate in candidates:
            overlaps = any(
                candidate["start_seconds"] < item["end_seconds"]
                and candidate["end_seconds"] > item["start_seconds"]
                for item in selected
            )

            if not overlaps:
                selected.append(candidate)

            if len(selected) >= max_clips:
                break

        if not selected:
            raise HTTPException(
                status_code=422,
                detail="No usable highlight clips could be selected."
            )

        # Create the selected clips with FFmpeg
        created_clips = []

        for moment in selected:
            clip_id = uuid.uuid4().hex
            output_path = CLIPS_DIR / f"{clip_id}.mp4"

            clip_command = [
                get_ffmpeg(),
                "-y",
                "-ss", str(moment["start_seconds"]),
                "-i", str(input_path),
                "-t", str(
                    moment["end_seconds"] - moment["start_seconds"]
                ),
                "-map", "0:v:0",
                "-map", "0:a?",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-c:a", "aac",
                "-movflags", "+faststart",
                str(output_path)
            ]

            clip_result = subprocess.run(
                clip_command,
                capture_output=True,
                text=True,
                timeout=600,
                check=False
            )

            if clip_result.returncode == 0 and output_path.is_file():
                created_paths.append(output_path)

                created_clips.append({
                    "clip_id": clip_id,
                    "start_seconds": moment["start_seconds"],
                    "end_seconds": moment["end_seconds"],
                    "ranking_score": moment["score"],
                    "transcript_excerpt": moment["transcript_excerpt"],
                    "download_url": f"/api/clips/{clip_id}/download"
                })

            else:
                output_path.unlink(missing_ok=True)

        if not created_clips:
            raise HTTPException(
                status_code=422,
                detail="No candidate clips could be generated."
            )

        # Display clips in chronological order
        created_clips.sort(
            key=lambda clip: clip["start_seconds"]
        )

        return {
            "message": "Dialogue-based highlight analysis completed.",
            "analysis_method": "Whisper transcription and dialogue-cue ranking",
            "detected_language": info.language,
            "clips_created": len(created_clips),
            "highlights": created_clips
        }

    except subprocess.TimeoutExpired:
        for path in created_paths:
            path.unlink(missing_ok=True)

        raise HTTPException(
            status_code=408,
            detail="Processing took too long. Try a shorter video."
        )

    finally:
        await video.close()
        input_path.unlink(missing_ok=True)
        audio_path.unlink(missing_ok=True)

@app.get("/api/clips/{clip_id}/download")
def download_clip(clip_id: str):
    if not clip_id.isalnum():
        raise HTTPException(status_code=400, detail="Invalid clip ID.")

    clip_path = CLIPS_DIR / f"{clip_id}.mp4"

    if not clip_path.is_file():
        raise HTTPException(status_code=404, detail="Clip not found.")

    return FileResponse(
        path=clip_path,
        media_type="video/mp4",
        filename=f"clip_{clip_id}.mp4",
    )
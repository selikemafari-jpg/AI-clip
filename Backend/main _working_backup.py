from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import uuid

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
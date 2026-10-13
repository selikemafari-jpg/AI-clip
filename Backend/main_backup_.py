
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="ClipStream AI API",
    description="Backend for the AI-powered streamer clipping platform",
    version="0.1.0",
)

# Allow the local frontend to communicate with the backend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


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
        "clips_ready": 0,
        "clips_detected": 0,
        "clips_failed": 0,
    }


from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
import whisper
import ffmpeg
import uuid
import os
import json
import re

app = FastAPI(title="ClipForge API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store (Railway keeps this alive)
jobs = {}

DOWNLOAD_DIR = "/tmp/clipforge"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ── MODELS ──────────────────────────────────────────────
class ProcessRequest(BaseModel):
    url: str
    num_clips: int = 5
    clip_duration: int = 60   # seconds
    style: str = "shorts"

class ClipSuggestion(BaseModel):
    num: int
    title: str
    hook: str
    timestamp_start: str
    timestamp_end: str
    why_viral: str
    viral_score: int
    clip_type: str

# ── HELPERS ─────────────────────────────────────────────
def ts_to_seconds(ts: str) -> float:
    """Convert '1:23' or '1:23:45' to seconds."""
    parts = ts.strip().split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return float(parts[0])

def find_best_clips(transcript: str, num_clips: int, duration: int, style: str) -> list:
    """
    Use Whisper transcript to find best clip moments.
    Scores segments by density of keywords, sentence energy,
    questions, and exclamations — no external API needed.
    """
    sentences = re.split(r'(?<=[.!?])\s+', transcript)
    scored = []

    hype_words = {
        "shorts": ["secret", "never", "actually", "wait", "insane", "crazy",
                   "you won't", "nobody", "first", "best", "worst", "revealed",
                   "truth", "real", "must", "stop", "listen"],
        "educational": ["important", "key", "remember", "critical", "note",
                        "essential", "step", "how to", "why", "because"],
        "funny": ["literally", "bro", "wait", "nah", "no way", "dead",
                  "can't", "actually", "omg", "lol"],
    }
    target_words = hype_words.get(style.split("/")[0].strip(), hype_words["shorts"])

    words = transcript.lower().split()
    total_words = max(len(words), 1)
    words_per_second = total_words / max(1, len(sentences)) * 2.5  # rough estimate

    current_second = 0.0
    for i, sentence in enumerate(sentences):
        sl = sentence.lower()
        score = 0
        score += sum(2 for w in target_words if w in sl)
        score += 3 if sentence.endswith("?") else 0
        score += 2 if sentence.endswith("!") else 0
        score += 1 if len(sentence.split()) > 15 else 0

        word_count = len(sentence.split())
        seg_duration = word_count / max(words_per_second, 1)

        scored.append({
            "sentence": sentence,
            "score": score,
            "start_sec": current_second,
            "end_sec": current_second + seg_duration,
        })
        current_second += seg_duration

    # Sort by score, pick top N non-overlapping windows
    scored_sorted = sorted(scored, key=lambda x: x["score"], reverse=True)
    selected = []
    used_ranges = []

    for seg in scored_sorted:
        if len(selected) >= num_clips:
            break
        start = max(0, seg["start_sec"] - 5)
        end = start + duration

        # Check overlap
        overlap = any(not (end <= r[0] or start >= r[1]) for r in used_ranges)
        if not overlap:
            used_ranges.append((start, end))
            selected.append(seg)

    # Sort selected by start time
    selected.sort(key=lambda x: x["start_sec"])

    clips = []
    for i, seg in enumerate(selected):
        start = max(0, seg["start_sec"] - 5)
        end = start + duration
        viral_score = min(99, 60 + seg["score"] * 4)

        def fmt(s):
            m = int(s // 60)
            sec = int(s % 60)
            return f"{m}:{sec:02d}"

        # Build a title from first 8 words of the sentence
        words_title = seg["sentence"].split()[:8]
        title = " ".join(words_title).rstrip(".,!?") + "..."

        clips.append(ClipSuggestion(
            num=i + 1,
            title=title,
            hook=seg["sentence"][:100],
            timestamp_start=fmt(start),
            timestamp_end=fmt(end),
            why_viral="High energy moment" if seg["score"] > 4 else "Key content moment",
            viral_score=viral_score,
            clip_type="hook" if i == 0 else ("value" if i % 2 == 0 else "story"),
        ))

    return clips

# ── BACKGROUND JOB ──────────────────────────────────────
def process_video(job_id: str, req: ProcessRequest):
    job_dir = os.path.join(DOWNLOAD_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    try:
        # ── STEP 1: Download audio only (much faster than full video) ──
        jobs[job_id]["status"] = "downloading"
        audio_path = os.path.join(job_dir, "audio.mp3")

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(job_dir, "audio.%(ext)s"),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "96",
            }],
            "quiet": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=True)
            video_title = info.get("title", "Video")
            jobs[job_id]["video_title"] = video_title

        # ── STEP 2: Transcribe with Whisper ──
        jobs[job_id]["status"] = "transcribing"
        model = whisper.load_model("base")  # fast + accurate enough
        result = model.transcribe(audio_path, fp16=False)
        transcript = result["text"]
        jobs[job_id]["transcript_length"] = len(transcript.split())

        # ── STEP 3: Find best clip moments ──
        jobs[job_id]["status"] = "analyzing"
        clips = find_best_clips(
            transcript,
            req.num_clips,
            req.clip_duration,
            req.style,
        )

        # ── STEP 4: Download full video & cut clips ──
        jobs[job_id]["status"] = "cutting"
        video_path = os.path.join(job_dir, "video.mp4")

        ydl_opts_video = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": video_path,
            "quiet": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts_video) as ydl:
            ydl.download([req.url])

        clip_files = []
        for clip in clips:
            start_sec = ts_to_seconds(clip.timestamp_start)
            end_sec = ts_to_seconds(clip.timestamp_end)
            out_path = os.path.join(job_dir, f"clip_{clip.num}.mp4")

            (
                ffmpeg
                .input(video_path, ss=start_sec, to=end_sec)
                .output(out_path, vcodec="libx264", acodec="aac", preset="fast")
                .overwrite_output()
                .run(quiet=True)
            )
            clip_files.append(f"/clips/{job_id}/clip_{clip.num}.mp4")

        jobs[job_id].update({
            "status": "done",
            "clips": [c.dict() for c in clips],
            "clip_files": clip_files,
            "video_topic": video_title,
        })

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)

# ── ROUTES ──────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "ClipForge API running 🔥"}

@app.post("/process")
def start_process(req: ProcessRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "queued", "url": req.url}
    background_tasks.add_task(process_video, job_id, req)
    return {"job_id": job_id}

@app.get("/status/{job_id}")
def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.get("/clips/{job_id}/{filename}")
def get_clip(job_id: str, filename: str):
    from fastapi.responses import FileResponse
    path = os.path.join(DOWNLOAD_DIR, job_id, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Clip not found")
    return FileResponse(path, media_type="video/mp4")

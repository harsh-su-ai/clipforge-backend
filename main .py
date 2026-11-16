from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi
import yt_dlp
import ffmpeg
import uuid
import os
import re

app = FastAPI(title="ClipForge API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs = {}
DOWNLOAD_DIR = "/tmp/clipforge"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

class ProcessRequest(BaseModel):
    url: str
    num_clips: int = 5
    clip_duration: int = 60
    style: str = "shorts"

def extract_video_id(url: str) -> str:
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11})',
        r'youtu\.be\/([0-9A-Za-z_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    raise ValueError("Could not extract video ID")

def find_best_clips(transcript_data: list, num_clips: int, duration: int, style: str) -> list:
    hype_words = {
        "shorts": ["secret","never","actually","wait","insane","crazy","best","worst","truth","must","stop","revealed","nobody","first"],
        "educational": ["important","key","remember","critical","essential","step","how","why","because"],
        "funny": ["literally","bro","wait","no way","dead","actually","omg"],
    }
    target = hype_words.get(style.split("/")[0].strip(), hype_words["shorts"])

    # Score each transcript segment
    scored = []
    for i, seg in enumerate(transcript_data):
        text = seg.get("text","").lower()
        score = sum(2 for w in target if w in text)
        score += 3 if "?" in text else 0
        score += 2 if "!" in text else 0
        scored.append({
            "index": i,
            "start": seg["start"],
            "text": seg["text"],
            "score": score,
        })

    # Pick top non-overlapping windows
    sorted_segs = sorted(scored, key=lambda x: x["score"], reverse=True)
    selected = []
    used = []

    for seg in sorted_segs:
        if len(selected) >= num_clips:
            break
        start = max(0, seg["start"] - 3)
        end = start + duration
        overlap = any(not (end <= r[0] or start >= r[1]) for r in used)
        if not overlap:
            used.append((start, end))
            selected.append(seg)

    selected.sort(key=lambda x: x["start"])

    def fmt(s):
        m = int(s // 60)
        sec = int(s % 60)
        return f"{m}:{sec:02d}"

    clips = []
    for i, seg in enumerate(selected):
        start = max(0, seg["start"] - 3)
        end = start + duration
        viral_score = min(99, 60 + seg["score"] * 5)
        title = " ".join(seg["text"].split()[:8]).rstrip(".,!?") + "..."
        clips.append({
            "num": i + 1,
            "title": title,
            "hook": seg["text"][:120],
            "timestamp_start": fmt(start),
            "timestamp_end": fmt(end),
            "start_sec": start,
            "end_sec": end,
            "why_viral": "High energy" if seg["score"] > 3 else "Key moment",
            "viral_score": viral_score,
            "clip_type": ["hook","value","story","reaction","tutorial"][i % 5],
        })
    return clips

def process_video(job_id: str, req: ProcessRequest):
    job_dir = os.path.join(DOWNLOAD_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    try:
        # Step 1: Get transcript (no download needed!)
        jobs[job_id]["status"] = "transcribing"
        video_id = extract_video_id(req.url)

        transcript_data = YouTubeTranscriptApi.get_transcript(video_id)
        full_text = " ".join([s["text"] for s in transcript_data])
        jobs[job_id]["transcript_words"] = len(full_text.split())

        # Step 2: Find best moments
        jobs[job_id]["status"] = "analyzing"
        clips = find_best_clips(transcript_data, req.num_clips, req.clip_duration, req.style)

        # Step 3: Get video title
        jobs[job_id]["status"] = "downloading"
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(req.url, download=False)
            video_title = info.get("title", "Video")
            jobs[job_id]["video_title"] = video_title

        # Step 4: Download video
        video_path = os.path.join(job_dir, "video.mp4")
        ydl_opts = {
            "format": "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": video_path,
            "quiet": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([req.url])

        # Step 5: Cut clips with FFmpeg
        jobs[job_id]["status"] = "cutting"
        clip_files = []
        for clip in clips:
            out_path = os.path.join(job_dir, f"clip_{clip['num']}.mp4")
            (
                ffmpeg
                .input(video_path, ss=clip["start_sec"], to=clip["end_sec"])
                .output(out_path, vcodec="libx264", acodec="aac", preset="fast")
                .overwrite_output()
                .run(quiet=True)
            )
            clip_files.append(f"/clips/{job_id}/clip_{clip['num']}.mp4")

        jobs[job_id].update({
            "status": "done",
            "clips": clips,
            "clip_files": clip_files,
            "video_topic": video_title,
        })

    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)

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
    path = os.path.join(DOWNLOAD_DIR, job_id, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Clip not found")
    return FileResponse(path, media_type="video/mp4")

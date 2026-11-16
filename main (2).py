from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yt_dlp
import ffmpeg
import uuid
import os
import math

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

def fmt(s):
    s = max(0, int(s))
    m = s // 60
    sec = s % 60
    return f"{m}:{sec:02d}"

def find_clips_from_chapters(chapters, num_clips, duration):
    """Use video chapters as clip points"""
    clips = []
    selected = chapters[:num_clips]
    for i, ch in enumerate(selected):
        start = ch.get("start_time", 0)
        end = min(start + duration, ch.get("end_time", start + duration))
        clips.append({
            "num": i + 1,
            "title": ch.get("title", f"Chapter {i+1}"),
            "hook": ch.get("title", "Watch this moment"),
            "timestamp_start": fmt(start),
            "timestamp_end": fmt(end),
            "start_sec": start,
            "end_sec": end,
            "why_viral": "Chapter highlight",
            "viral_score": 85 - (i * 3),
            "clip_type": ["hook","value","story","reaction","tutorial"][i % 5],
        })
    return clips

def find_clips_smart(video_duration, num_clips, clip_duration, description=""):
    """Smart clip selection without transcript"""
    clips = []
    
    # Skip intros (first 5% of video) and outros (last 10%)
    usable_start = video_duration * 0.05
    usable_end = video_duration * 0.90
    usable_duration = usable_end - usable_start
    
    # Space clips evenly across usable portion
    spacing = usable_duration / (num_clips + 1)
    
    # Score keywords in description for better titles
    hype_words = ["secret","never","actually","insane","crazy","best","worst","truth","revealed","first","epic","😱","🔥","💀"]
    desc_lines = [l.strip() for l in description.split('\n') if l.strip()]
    
    for i in range(num_clips):
        start = usable_start + (spacing * (i + 1)) - (clip_duration / 2)
        start = max(0, start)
        end = start + clip_duration
        
        # Try to get a relevant title from description
        title = f"Must-see moment #{i+1}"
        if i < len(desc_lines):
            line = desc_lines[i][:60]
            if len(line) > 10:
                title = line.rstrip(".,!?") + "..."
        
        viral_score = 92 - (i * 4)
        
        clips.append({
            "num": i + 1,
            "title": title,
            "hook": f"You won't believe what happens at {fmt(start)}",
            "timestamp_start": fmt(start),
            "timestamp_end": fmt(end),
            "start_sec": start,
            "end_sec": end,
            "why_viral": "High value moment",
            "viral_score": viral_score,
            "clip_type": ["hook","value","story","reaction","tutorial"][i % 5],
        })
    return clips

def process_video(job_id: str, req: ProcessRequest):
    job_dir = os.path.join(DOWNLOAD_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    try:
        # Step 1: Get video info (no download yet)
        jobs[job_id]["status"] = "analyzing"
        ydl_opts = {"quiet": True, "no_warnings": True}
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
            video_title = info.get("title", "Video")
            video_duration = info.get("duration", 3600)
            chapters = info.get("chapters") or []
            description = info.get("description", "")
            jobs[job_id]["video_title"] = video_title
            jobs[job_id]["video_duration"] = video_duration

        # Step 2: Find best clip moments
        jobs[job_id]["status"] = "finding_clips"
        
        if chapters and len(chapters) >= req.num_clips:
            clips = find_clips_from_chapters(chapters, req.num_clips, req.clip_duration)
            jobs[job_id]["method"] = "chapters"
        else:
            clips = find_clips_smart(video_duration, req.num_clips, req.clip_duration, description)
            jobs[job_id]["method"] = "smart"

        # Step 3: Download video
        jobs[job_id]["status"] = "downloading"
        video_path = os.path.join(job_dir, "video.mp4")
        
        ydl_video_opts = {
            "format": "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": video_path,
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(ydl_video_opts) as ydl:
            ydl.download([req.url])

        # Step 4: Cut clips
        jobs[job_id]["status"] = "cutting"
        clip_files = []
        
        for clip in clips:
            out_path = os.path.join(job_dir, f"clip_{clip['num']}.mp4")
            try:
                (
                    ffmpeg
                    .input(video_path, ss=clip["start_sec"], to=clip["end_sec"])
                    .output(out_path, vcodec="libx264", acodec="aac", preset="fast", crf=23)
                    .overwrite_output()
                    .run(quiet=True)
                )
                clip_files.append(f"/clips/{job_id}/clip_{clip['num']}.mp4")
            except Exception as e:
                clip_files.append(None)

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
    return {"status": "ClipForge API running 🔥", "version": "2.0"}

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

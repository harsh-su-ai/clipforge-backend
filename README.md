# ClipForge Backend 🎬

Full backend for ClipForge — downloads videos, transcribes with Whisper AI, finds viral moments, cuts clips with FFmpeg.

## Stack
- **FastAPI** — API server
- **yt-dlp** — download any YouTube video
- **Whisper AI** — transcribe audio to text
- **FFmpeg** — cut video clips
- **Railway.app** — free hosting

---

## Deploy to Railway (FREE) — 5 Steps

### Step 1 — Create GitHub repo
1. Go to github.com → New repository
2. Name it `clipforge-backend`
3. Upload all these files

### Step 2 — Go to Railway
1. Visit railway.app
2. Click "Start a New Project"
3. Click "Deploy from GitHub repo"
4. Connect your GitHub and select `clipforge-backend`

### Step 3 — Railway auto-detects Dockerfile
- It will build automatically
- First build takes ~5 minutes (downloading Whisper model)
- You'll get a URL like: `clipforge-backend-production.up.railway.app`

### Step 4 — Update your ClipForge frontend
Replace `YOUR_BACKEND_URL` in the frontend with your Railway URL.

### Step 5 — Test it
```
GET  https://your-url.railway.app/
POST https://your-url.railway.app/process
GET  https://your-url.railway.app/status/{job_id}
```

---

## API Reference

### POST /process
```json
{
  "url": "https://youtube.com/watch?v=...",
  "num_clips": 5,
  "clip_duration": 60,
  "style": "shorts"
}
```
Returns: `{ "job_id": "abc12345" }`

### GET /status/{job_id}
Returns job status: `queued → downloading → transcribing → analyzing → cutting → done`

When done:
```json
{
  "status": "done",
  "video_topic": "Video title",
  "clips": [...],
  "clip_files": ["/clips/abc12345/clip_1.mp4", ...]
}
```

### GET /clips/{job_id}/{filename}
Downloads the actual cut MP4 clip file.

---

## Free Tier Limits (Railway)
- $5 free credit/month
- ~500 minutes of compute
- Enough for ~50-100 clips/month for free

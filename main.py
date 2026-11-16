from fastapi import FastAPI,BackgroundTasks,HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yt_dlp,ffmpeg,uuid,os
app=FastAPI()
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])
jobs={}
os.makedirs("/tmp/cf",exist_ok=True)
class R(BaseModel):
 url:str;num_clips:int=5;clip_duration:int=60;style:str="shorts"
def fmt(s):
 s=max(0,int(s));return f"{s//60}:{s%60:02d}"
def clips(dur,n,cd,ch=None,desc=""):
 out=[]
 if ch and len(ch)>=n:
  for i,c in enumerate(ch[:n]):
   s=c.get("start_time",0);e=min(s+cd,c.get("end_time",s+cd))
   out.append({"num":i+1,"title":c.get("title",f"Clip {i+1}"),"hook":c.get("title","Watch"),"timestamp_start":fmt(s),"timestamp_end":fmt(e),"start_sec":s,"end_sec":e,"why_viral":"Chapter","viral_score":90-i*3,"clip_type":"hook"})
 else:
  sp=(dur*0.85)/(n+1);dl=[l.strip() for l in desc.split('\n') if len(l.strip())>10]
  for i in range(n):
   s=max(0,dur*0.05+sp*(i+1)-cd/2);e=min(s+cd,dur)
   t=dl[i][:55]+"..." if i<len(dl) else f"Best moment #{i+1}"
   out.append({"num":i+1,"title":t,"hook":f"Don't miss {fmt(s)}","timestamp_start":fmt(s),"timestamp_end":fmt(e),"start_sec":s,"end_sec":e,"why_viral":"Key moment","viral_score":92-i*4,"clip_type":"hook"})
 return out
def run(job_id,req):
 d=f"/tmp/cf/{job_id}";os.makedirs(d,exist_ok=True)
 try:
  jobs[job_id]["status"]="analyzing"
  with yt_dlp.YoutubeDL({"quiet":True}) as y:
   i=y.extract_info(req.url,download=False)
   t=i.get("title","Video");dur=i.get("duration",3600)
   ch=i.get("chapters") or [];desc=i.get("description","")
   jobs[job_id]["video_title"]=t
  jobs[job_id]["status"]="finding_clips"
  cl=clips(dur,req.num_clips,req.clip_duration,ch,desc)
  jobs[job_id]["status"]="downloading"
  vp=f"{d}/v.mp4"
  with yt_dlp.YoutubeDL({"format":"bestvideo[ext=mp4][height<=720]+bestaudio/best","outtmpl":vp,"quiet":True}) as y:
   y.download([req.url])
  jobs[job_id]["status"]="cutting";cf=[]
  for c in cl:
   op=f"{d}/clip_{c['num']}.mp4"
   try:
    ffmpeg.input(vp,ss=c["start_sec"],to=c["end_sec"]).output(op,vcodec="libx264",acodec="aac",preset="fast").overwrite_output().run(quiet=True)
    cf.append(f"/clips/{job_id}/clip_{c['num']}.mp4")
   except:cf.append(None)
  jobs[job_id].update({"status":"done","clips":cl,"clip_files":cf,"video_topic":t})
 except Exception as e:
  jobs[job_id].update({"status":"error","error":str(e)})
@app.get("/")
def root():return{"status":"ok"}
@app.post("/process")
def start(req:R,bt:BackgroundTasks):
 j=str(uuid.uuid4())[:8];jobs[j]={"status":"queued"}
 bt.add_task(run,j,req);return{"job_id":j}
@app.get("/status/{j}")
def stat(j:str):
 job=jobs.get(j)
 if not job:raise HTTPException(404,"nf")
 return job
@app.get("/clips/{j}/{f}")
def clip(j:str,f:str):
 p=f"/tmp/cf/{j}/{f}"
 if not os.path.exists(p):raise HTTPException(404,"nf")
 return FileResponse(p,media_type="video/mp4")

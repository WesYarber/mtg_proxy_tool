from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
import uuid
import os
from engine import ProxyEngine

app = FastAPI()

# Mount static files (Frontend)
app.mount("/static", StaticFiles(directory="../static"), name="static")

# In-memory job store
jobs = {}

class JobStatus:
    def __init__(self):
        self.status = "pending"
        self.messages = []
        self.progress = 0
        self.result_files = []

    def update(self, message):
        self.messages.append(message)
        # Simple heuristic progress update
        self.progress = min(99, self.progress + 2)
    
    def complete(self, files):
        self.status = "completed"
        self.progress = 100
        self.result_files = files

    def fail(self, error):
        self.status = "failed"
        self.messages.append(f"Error: {str(error)}")

class GenerateRequest(BaseModel):
    url: str
    format: str = "smart"
    padding: float = 0.0
    include_sideboard: bool = False
    include_maybeboard: bool = False
    cut_line_color: str = "#000000"
    cut_line_thickness: float = 0.2

def run_engine_task(job_id: str, request: GenerateRequest):
    job = jobs[job_id]
    job.status = "running"
    
    def callback(msg):
        job.update(msg)
        
    engine = ProxyEngine(progress_callback=callback)
    
    try:
        # Output directory relative to where we run
        output_dir = os.path.join(os.getcwd(), "Output")
        files = engine.run_job(
            input_str=request.url,
            output_dir=output_dir,
            format_mode=request.format,
            padding_mm=request.padding,
            include_sideboard=request.include_sideboard,
            include_maybeboard=request.include_maybeboard,
            cut_line_color=request.cut_line_color,
            cut_line_thickness=request.cut_line_thickness
        )
        job.complete(files)
    except Exception as e:
        job.fail(e)

@app.get("/")
async def read_root():
    return FileResponse("../static/index.html")

@app.post("/api/generate")
async def generate_proxies(request: GenerateRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    jobs[job_id] = JobStatus()
    background_tasks.add_task(run_engine_task, job_id, request)
    return {"job_id": job_id}

@app.post("/api/preview")
async def preview_proxies(request: GenerateRequest):
    engine = ProxyEngine()
    try:
        data = engine.get_deck_structure(
            input_str=request.url,
            format_mode=request.format,
            include_sideboard=request.include_sideboard,
            include_maybeboard=request.include_maybeboard
        )
        return {"decks": data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[job_id]
    return {
        "status": job.status,
        "progress": job.progress,
        "messages": job.messages,
        "files": [os.path.basename(f) for f in job.result_files] if job.result_files else []
    }

@app.get("/api/download/{filename}")
async def download_file(filename: str):
    output_root = os.path.join(os.getcwd(), "Output")
    # Walk to find file
    for root, dirs, files in os.walk(output_root):
        if filename in files:
            return FileResponse(os.path.join(root, filename), filename=filename)
    raise HTTPException(status_code=404, detail="File not found")

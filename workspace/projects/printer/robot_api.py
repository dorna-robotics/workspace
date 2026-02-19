# robot_api.py
from __future__ import annotations

import queue
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# --- import/create your workspace here ---
# from workspace.workspace import Workspace
# workspace = Workspace(...)
# core = workspace.components["core"]  # or however you get core
# IMPORTANT: start your gate thread somewhere after workspace/core created
# from job import start_job_thread
# start_job_thread(workflow_fn, workspace=workspace, core=core)

app = FastAPI(title="Workspace Control API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later if you want
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# Simple in-memory log buffer
# ----------------------------
LOG_Q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=2000)

def log(msg: str, level: str = "info") -> None:
    item = {"t": time.time(), "level": level, "msg": msg}
    try:
        LOG_Q.put_nowait(item)
    except queue.Full:
        # drop oldest by draining one
        try:
            LOG_Q.get_nowait()
        except queue.Empty:
            pass
        try:
            LOG_Q.put_nowait(item)
        except Exception:
            pass

# ----------------------------
# Helpers (YOU MUST wire these)
# ----------------------------
def get_rt():
    return workspace.rt  # noqa: F821

def get_core():
    return core          # noqa: F821

# ----------------------------
# API endpoints
# ----------------------------
@app.get("/status")
def status() -> Dict[str, Any]:
    rt = get_rt()
    st = rt.status
    return {
        "state": str(st.state),
        "last_error": st.last_error,
        "job_runs": st.job_runs,
        "job_pauses": st.job_pauses,
        "job_resumes": st.job_resumes,
        "kills": getattr(st, "kills", None),
    }

@app.post("/start")
def start() -> Dict[str, Any]:
    get_rt().start()
    log("start()")
    return {"ok": True}

@app.post("/pause")
def pause() -> Dict[str, Any]:
    get_rt().pause()
    log("pause()")
    return {"ok": True}

@app.post("/resume")
def resume() -> Dict[str, Any]:
    get_rt().resume()
    log("resume()")
    return {"ok": True}

@app.post("/kill")
def kill() -> Dict[str, Any]:
    get_rt().kill()
    log("kill()", level="warn")
    return {"ok": True}

@app.post("/reset")
def reset() -> Dict[str, Any]:
    # your Runtime.reset() clears killed flag so you can start a fresh gate thread
    get_rt().reset()
    log("reset()")
    return {"ok": True}

@app.get("/logs")
def logs(max_items: int = 200) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    for _ in range(max_items):
        try:
            items.append(LOG_Q.get_nowait())
        except queue.Empty:
            break
    return {"items": items}

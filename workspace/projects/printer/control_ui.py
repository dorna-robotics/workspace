# control_ui.py
from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Workspace Control", layout="wide")

# -----------------------
# CSS (only for colors + small cosmetics)
# -----------------------
st.markdown(
    """
<style>
/* Make buttons look like proper controls */
button[data-testid="baseButton-start_btn"]{
  background:#22c55e !important; color:#fff !important;
  border:1px solid #16a34a !important;
}
button[data-testid="baseButton-start_btn"]:hover{ background:#16a34a !important; }

button[data-testid="baseButton-pause_btn"]{
  background:#f59e0b !important; color:#fff !important;
  border:1px solid #d97706 !important;
}
button[data-testid="baseButton-pause_btn"]:hover{ background:#d97706 !important; }

button[data-testid="baseButton-stop_btn"]{
  background:#ef4444 !important; color:#fff !important;
  border:1px solid #dc2626 !important;
}
button[data-testid="baseButton-stop_btn"]:hover{ background:#dc2626 !important; }

/* Give all buttons the same height */
.stButton > button{
  height:48px;
  border-radius:10px;
  font-weight:700;
}

/* Logs height */
pre { max-height: 320px; overflow: auto; }
</style>
    """,
    unsafe_allow_html=True,
)

# -----------------------
# Sidebar
# -----------------------
API = st.sidebar.text_input("Robot API base URL", "http://127.0.0.1:8000").rstrip("/")
VIEWER_URL = st.sidebar.text_input("3D Viewer URL", "http://127.0.0.1:5000").rstrip("/")
AUTO = st.sidebar.checkbox("Auto refresh", True)
REFRESH_SEC = st.sidebar.slider("Refresh (sec)", 0.2, 2.0, 0.5, 0.1)
VIEW_H = st.sidebar.slider("Viewer height (px)", 500, 1400, 900, 50)

# -----------------------
# HTTP helpers
# -----------------------
def _req(method: str, path: str, *, params: Optional[dict] = None, timeout: float = 2.5) -> Dict[str, Any]:
    url = f"{API}{path}"
    try:
        r = requests.request(method, url, params=params, timeout=timeout)
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        if r.status_code >= 400:
            return {"ok": False, "error": data}
        return {"ok": True, "data": data}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": str(e)}

def api_post(path: str) -> Dict[str, Any]:
    return _req("POST", path)

def api_get(path: str, params: Optional[dict] = None) -> Dict[str, Any]:
    return _req("GET", path, params=params)

# -----------------------
# Layout
# -----------------------
left, right = st.columns([1, 2], gap="large")

with left:
    # Buttons row (native stretch)
    b1, b2, b3 = st.columns(3)
    err = None

    if b1.button("Start / Resume", key="start_btn", width="stretch"):
        res = api_post("/start")
        if not res["ok"]:
            err = f"Start/Resume failed: {res['error']}"

    if b2.button("Pause", key="pause_btn", width="stretch"):
        res = api_post("/pause")
        if not res["ok"]:
            err = f"Pause failed: {res['error']}"

    if b3.button("Stop", key="stop_btn", width="stretch"):
        res = api_post("/kill")
        if not res["ok"]:
            err = f"Stop failed: {res['error']}"

    if err:
        st.error(err)

    # Status (no title)
    st_res = api_get("/status")
    if not st_res["ok"]:
        st.error(f"/status failed: {st_res['error']}")
    else:
        st.json(st_res["data"])

    # Logs (no title)
    if "log_buf" not in st.session_state:
        st.session_state.log_buf = []

    lg_res = api_get("/logs", params={"max_items": 200})
    if lg_res["ok"]:
        items = lg_res["data"].get("items", [])
        st.session_state.log_buf.extend(items)
        st.session_state.log_buf = st.session_state.log_buf[-1500:]
    else:
        st.warning(f"/logs failed: {lg_res['error']}")

    lines = []
    for it in st.session_state.log_buf[-250:]:
        t = time.strftime("%H:%M:%S", time.localtime(it.get("t", time.time())))
        lvl = str(it.get("level", "info")).upper()
        msg = str(it.get("msg", ""))
        lines.append(f"[{t}] {lvl:5s} {msg}")

    st.code("\n".join(lines) if lines else "(no logs yet)", language="text")

with right:
    components.iframe(VIEWER_URL, height=int(VIEW_H), scrolling=False)

# -----------------------
# Auto refresh
# -----------------------
if AUTO:
    time.sleep(float(REFRESH_SEC))
    st.rerun()

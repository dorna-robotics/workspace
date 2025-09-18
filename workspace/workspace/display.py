# workspace/display.py
import time, json, threading
import socketio

class Display:
    def __init__(self, workspace, server_url="http://127.0.0.1:5000", fps=60, debug=False):
        self.workspace = workspace
        self.SERVER = server_url
        self.fps = max(1, int(fps))
        self._period = 1.0 / self.fps

        self._thread = None
        self._stop_event = threading.Event()
        self._state_lock = threading.RLock()   # protects _inflight/_pending

        self.sio = socketio.Client(
            reconnection=True,
            logger=bool(debug),
            engineio_logger=bool(debug)
        )
        self._connected_evt = threading.Event()

        @self.sio.event
        def connect():
            self._connected_evt.set()
            # full snapshot on (re)connect
            self._emit_update(self._build_snapshot())

        @self.sio.event
        def disconnect():
            self._connected_evt.clear()

        @self.sio.on("request_snapshot")
        def _on_request_snapshot(_data=None):
            self._emit_update(self._build_snapshot())

        # ACK/backpressure state
        self._inflight = False
        self._pending = None

        # (optional) last payload size to skip redundant huge frames
        self._last_size = 0

    # ---------- public utilities ----------
    def set_fps(self, fps:int):
        """Change streaming FPS on the fly."""
        with self._state_lock:
            self.fps = max(1, int(fps))
            self._period = 1.0 / self.fps

    def send_snapshot(self):
        """Force a full snapshot now."""
        self._emit_update(self._build_snapshot())

    # ---------- payload builders ----------
    def _build_snapshot(self):
        """meshUrl + pose + visible for each solid."""
        try:
            poses = self.workspace.compute_world_poses()
        except Exception:
            poses = {}

        batch = {}
        try:
            # Walk components (need solid.type or solid.name)
            for comp_name, comp in getattr(self.workspace, "components", {}).items():
                assembly = getattr(comp, "assembly", {}) or {}
                for solid_name, solid in assembly.items():
                    key = f"{comp_name}_{solid_name}"
                    pose = poses.get(key, [[1,0,0,0],[0,1,0,0],[0,0,1,0]])  # fallback identity-ish
                    mesh_id = getattr(solid, "type", getattr(solid, "name", solid_name))
                    batch[key] = {
                        "meshUrl": f"/static/CAD/{mesh_id}.glb",
                        "pose": pose,
                        "visible": True,
                    }
        except Exception:
            # If anything goes wrong, return what we have (or empty dict)
            pass

        return batch

    def _build_pose_frame(self):
        """pose + visible only (lightweight per-frame)."""
        try:
            poses = self.workspace.compute_world_poses()
        except Exception:
            poses = {}

        return {name: {"pose": p, "visible": True} for name, p in poses.items()}

    # ---------- emit / loop ----------
    def _emit_update(self, payload: dict):
        if not payload:
            return
        if not self.sio.connected:
            return

        # (optional) micro-opt: skip if payload size identical to last large full snapshot
        try:
            encoded = json.dumps(payload)
        except Exception:
            return

        with self._state_lock:
            if self._inflight:
                # coalesce to most recent
                self._pending = payload
                return
            self._inflight = True

        def ack_cb(_ok=None):
            with self._state_lock:
                self._inflight = False
                next_payload = self._pending
                self._pending = None
            if next_payload is not None:
                self._emit_update(next_payload)

        self._last_size = len(encoded)
        # Avoid passing unsupported kwargs (e.g., compress) — rely on server defaults
        self.sio.emit("upstream_update", payload, callback=ack_cb)

    def _run(self):
        # Drift-resistant frame timer
        period = self._period
        next_t = time.perf_counter()
        while not self._stop_event.is_set():
            try:
                self._emit_update(self._build_pose_frame())
            except Exception:
                # Don’t let one bad frame kill the thread
                pass

            next_t += period
            # If we’re far behind (system sleep, GC pause etc), reset the schedule
            now = time.perf_counter()
            delay = next_t - now
            if delay < -period:
                next_t = now + period
                delay = period
            if delay > 0:
                time.sleep(delay)
            # pick up any fps changes
            period = self._period

    # ---------- lifecycle ----------
    def start(self):
        # Already running?
        if self._thread and self._thread.is_alive():
            return

        # Try to connect (websocket preferred)
        try:
            self.sio.connect(self.SERVER, transports=["websocket"], wait=True, wait_timeout=5,socketio_path="/socket.io/")
        except Exception:
            return

        # Only start loop if we actually connected
        if not self._connected_evt.wait(timeout=2.0):
            # Give up cleanly if connect didn’t happen
            try:
                self.sio.disconnect()
            except Exception:
                pass
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        t = self._thread
        self._thread = None
        if t and t.is_alive():
            # Don’t hang forever on exit
            t.join(timeout=2.0)
        try:
            self.sio.disconnect()
        except Exception:
            pass

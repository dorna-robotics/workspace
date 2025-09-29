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

        self._last_size = 0

    # ---------- public utilities ----------
    def set_fps(self, fps:int):
        with self._state_lock:
            self.fps = max(1, int(fps))
            self._period = 1.0 / self.fps

    def send_snapshot(self):
        self._emit_update(self._build_snapshot())

    # ---------- helpers ----------
    def _extract_anchors(self, solid):
        """
        Return a JSON-serializable anchors dict:
            { "name": [x, y, z, rx_deg, ry_deg, rz_deg], ... }
        If nothing found, return None.
        """
        candidates = [
            getattr(solid, "anchors", None),
            getattr(solid, "anchor_dict", None),
            getattr(solid, "anchor", None),
        ]
        src = None
        for c in candidates:
            if isinstance(c, dict) and c:
                src = c
                break
        if not src:
            return None

        out = {}
        for k, v in src.items():
            if isinstance(v, (list, tuple)) and len(v) == 6:
                try:
                    out[k] = [float(v[0]), float(v[1]), float(v[2]),
                              float(v[3]), float(v[4]), float(v[5])]
                except Exception:
                    continue
        return out or None

    # ---------- payload builders ----------
    def _build_snapshot(self):
        """meshUrl + pose + visible (+ anchors, names) for each solid."""
        try:
            poses = self.workspace.compute_world_poses()
        except Exception:
            poses = {}

        batch = {}
        try:
            for comp_name, comp in getattr(self.workspace, "components", {}).items():
                assembly = getattr(comp, "assembly", {}) or {}
                for solid_name, solid in assembly.items():
                    key = f"{comp_name}_{solid_name}"
                    pose = poses.get(key, [[1,0,0,0],[0,1,0,0],[0,0,1,0]])  # fallback identity-ish
                    mesh_id = getattr(solid, "type", getattr(solid, "name", solid_name))

                    item = {
                        "meshUrl": f"/static/CAD/{mesh_id}.glb",
                        "pose": pose,
                        "visible": True,
                        # NEW: names so UI can label/decorate
                        "componentName": comp_name,
                        "solidName": solid_name,
                    }

                    anchors = self._extract_anchors(solid)
                    if anchors:
                        item["anchors"] = anchors

                    batch[key] = item
        except Exception:
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
        if not payload or not self.sio.connected:
            return
        try:
            encoded = json.dumps(payload)
        except Exception:
            return

        with self._state_lock:
            if self._inflight:
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
        self.sio.emit("upstream_update", payload, callback=ack_cb)

    def _run(self):
        period = self._period
        next_t = time.perf_counter()
        while not self._stop_event.is_set():
            try:
                self._emit_update(self._build_pose_frame())
            except Exception:
                pass

            next_t += period
            now = time.perf_counter()
            delay = next_t - now
            if delay < -period:
                next_t = now + period
                delay = period
            if delay > 0:
                time.sleep(delay)
            period = self._period

    # ---------- lifecycle ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        try:
            self.sio.connect(
                self.SERVER,
                transports=["websocket"],
                wait=True,
                wait_timeout=5,
                socketio_path="/socket.io/",
            )
        except Exception:
            return

        if not self._connected_evt.wait(timeout=2.0):
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
            t.join(timeout=2.0)
        try:
            self.sio.disconnect()
        except Exception:
            pass

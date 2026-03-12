# workspace/display.py
import time
import json
import threading
import socketio


class Display:
    def __init__(self, workspace, port=8000, fps=60, debug=False):
        """
        Non-blocking display:
        - Does NOT block Workspace.__init__()
        - Connects to server in a background thread
        - Sends pose frames with backpressure (ACK-based)
        """
        self.workspace = workspace
        self.SERVER = f"http://127.0.0.1:{port}"

        # target FPS for pose sending
        self.fps = max(1, int(fps))
        self._period = 1.0 / self.fps

        # threading / state
        self._thread = None
        self._stop_event = threading.Event()
        self._state_lock = threading.RLock()  # protects _inflight/_pending

        # backpressure
        self._inflight = False
        self._pending = None

        # socket.io client
        self.sio = socketio.Client(
            reconnection=True,
            logger=bool(debug),
            engineio_logger=bool(debug),
        )

        # ------------------------
        # SOCKET.IO EVENT HANDLERS
        # ------------------------
        @self.sio.event
        def connect():
            print("[Display] socket.io connected")
            # On connect: send a full snapshot once
            try:
                snap = self._build_snapshot()
                print(f"[Display] sending initial snapshot ({len(snap)} items)")
                self._emit_update(snap)
            except Exception as e:
                print("[Display] error building initial snapshot:", e)

        @self.sio.event
        def disconnect():
            print("[Display] socket.io disconnected")

        # server can call "request_snapshot" with or without payload
        @self.sio.on("request_snapshot")
        def _req_snap(_data=None):
            try:
                snap = self._build_snapshot()
                self._emit_update(snap)
            except Exception as e:
                print("[Display] error in request_snapshot:", e)

    # ----------------------------------------------------
    # Public utilities
    # ----------------------------------------------------
    def set_fps(self, fps: int):
        with self._state_lock:
            self.fps = max(1, int(fps))
            self._period = 1.0 / self.fps

    def send_snapshot(self):
        """Force a full snapshot send (manual)."""
        try:
            snap = self._build_snapshot()
            self._emit_update(snap)
        except Exception as e:
            print("[Display] error in send_snapshot:", e)

    # ----------------------------------------------------
    # Payload builders
    # ----------------------------------------------------
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
                    out[k] = [
                        float(v[0]), float(v[1]), float(v[2]),
                        float(v[3]), float(v[4]), float(v[5]),
                    ]
                except Exception:
                    continue
        return out or None

    def _build_snapshot(self):
        """meshUrl + pose + visible (+ anchors, names) for each solid."""
        try:
            poses = self.workspace.compute_world_poses()
        except Exception as e:
            print("[Display] compute_world_poses() failed in snapshot:", e)
            poses = {}

        world_boxes_by_solid, flange_boxes_by_solid = self._collision_boxes_by_solid()

        batch = {}
        try:
            for comp_name, comp in getattr(self.workspace, "components", {}).items():
                assembly = getattr(comp, "assembly", {}) or {}
                for solid_name, solid in assembly.items():
                    key = f"{comp_name}_{solid_name}"
                    pose = poses.get(key, [0, 0, 0, 0, 0, 0])  # fallback
                    mesh_id = getattr(solid, "type", getattr(solid, "name", solid_name))

                    item = {
                        "meshUrl": f"/static/CAD/{mesh_id}.glb",
                        "pose": pose,
                        "visible": True,
                        "componentName": comp_name,
                        "solidName": solid_name,
                        "type": getattr(solid, "type", None),   # ← ADD THIS
                    }

                    anchors = self._extract_anchors(solid)
                    if anchors:
                        item["anchors"] = anchors

                    key_boxes = (comp_name, solid_name)
                    # Always include collision arrays so consumers can clear
                    # stale visuals when a solid no longer has any boxes.
                    item["collisionWorld"] = world_boxes_by_solid.get(key_boxes, [])
                    item["collisionFlange"] = flange_boxes_by_solid.get(key_boxes, [])

                    batch[key] = item
        except Exception as e:
            print("[Display] error building snapshot batch:", e)
        return batch

    def _build_pose_frame(self):
        """Only pose + visible; DO NOT delete meshUrl."""
        try:
            poses = self.workspace.compute_world_poses()
        except Exception as e:
            print("[Display] compute_world_poses() failed in frame:", e)
            poses = {}

        world_boxes_by_solid, flange_boxes_by_solid = self._collision_boxes_by_solid()

        out = {}
        for comp_name, comp in getattr(self.workspace, "components", {}).items():
            assembly = getattr(comp, "assembly", {}) or {}
            for solid_name, _solid in assembly.items():
                key = f"{comp_name}_{solid_name}"
                p = poses.get(key, [0, 0, 0, 0, 0, 0])
                item = {
                    "pose": p,
                    # DO NOT send meshUrl
                    # DO NOT send componentName / solidName
                    "visible": True
                }
                key_boxes = (comp_name, solid_name)
                # Always send collision arrays (possibly empty) so stale
                # collision meshes get removed on detach/topology changes.
                item["collisionWorld"] = world_boxes_by_solid.get(key_boxes, [])
                item["collisionFlange"] = flange_boxes_by_solid.get(key_boxes, [])

                out[key] = item
        return out

    def _collision_boxes_by_solid(self, padding=0.0):
        if not hasattr(self.workspace, "compute_collision_boxes"):
            return {}, {}

        try:
            collision_world, collision_flange = self.workspace.compute_collision_boxes(padding)
        except Exception as e:
            print("[Display] compute_collision_boxes() failed:", e)
            return {}, {}

        world_map = {}
        for box in collision_world:
            comp = box.get("componentName")
            solid = box.get("solidName")
            if comp is None or solid is None:
                continue
            world_map.setdefault((comp, solid), []).append({
                "pose": box.get("pose"),
                "scale": box.get("scale"),
            })

        flange_map = {}
        for box in collision_flange:
            comp = box.get("componentName")
            solid = box.get("solidName")
            if comp is None or solid is None:
                continue
            flange_map.setdefault((comp, solid), []).append({
                "pose": box.get("pose"),
                "scale": box.get("scale"),
            })

        return world_map, flange_map
    # ----------------------------------------------------
    # Emit with backpressure
    # ----------------------------------------------------
    def _emit_update(self, payload: dict):
        if not payload:
            return
        if not self.sio.connected:
            # No connection yet; drop silently
            return

        try:
            # ensure JSON serializable / small
            encoded = json.dumps(payload)
        except Exception as e:
            print("[Display] json.dumps failed:", e)
            return

        with self._state_lock:
            if self._inflight:
                # Replace any previous pending update
                self._pending = payload
                return
            self._inflight = True

        def ack_cb(_ok=None):
            # Called by socket.io when server handler (upstream_update) returns
            with self._state_lock:
                self._inflight = False
                next_payload = self._pending
                self._pending = None
            if next_payload is not None:
                # Immediately send the latest pending
                self._emit_update(next_payload)

        self.sio.emit("upstream_update", payload, callback=ack_cb)

    # ----------------------------------------------------
    # Main loop
    # ----------------------------------------------------
    def _loop(self):
        """Background loop: send pose-only frames at target FPS."""
        print(f"[Display] Running at {self.fps} fps")
        next_t = time.perf_counter()
        period = self._period

        while not self._stop_event.is_set():
            try:
                if not self._inflight and self.sio.connected:
                    frame = self._build_pose_frame()
                    self._emit_update(frame)
            except Exception as e:
                print("[Display] error in main loop:", e)

            next_t += period
            delay = next_t - time.perf_counter()

            if delay < -period:
                # we fell behind; reset schedule
                next_t = time.perf_counter() + period
                delay = period
            if delay > 0:
                time.sleep(delay)

    def _connect_bg(self):
        """Connect to socket.io in a background thread (non-blocking)."""
        first = True
        while not self._stop_event.is_set():
            try:
                # transports only here, NOT in Client()
                self.sio.connect(
                    self.SERVER,
                    transports=["websocket"],
                    socketio_path="/socket.io/",
                )
                # If connect succeeds, break; reconnection is handled by the client
                return
            except Exception as e:
                if first:
                    print("[Display] connect failed, retrying in background:", e)
                    first = False
                time.sleep(2.0)

    # ----------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------
    def start(self):
        self._stop_event.clear()

        # Connect socket first
        threading.Thread(target=self._connect_bg, daemon=True).start()

        # Delay frame loop so snapshot can send first
        time.sleep(0.2)

        # Send snapshot explicitly
        self.send_snapshot()

        # Now start pose updates
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Cleanly stop background threads and close any resources."""
        self._stop_event.set()
        t = self._thread
        self._thread = None
        if t and t.is_alive():
            t.join(timeout=2.0)
        try:
            self.sio.disconnect()
        except Exception:
            pass

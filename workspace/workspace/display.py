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

        # delta compression: cache last sent pose per object
        self._last_sent = {}  # key → (pose_tuple, collision_hash)

    
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
        """Force a full snapshot send (manual). Clears the delta cache.

        Also flags any object that VANISHED since the last send with
        ``delete: true``. A plain snapshot only *omits* a removed object,
        which the viewer treats as "unchanged" and leaves the stale mesh
        on screen — so a runtime ``remove_component`` wouldn't visually
        disappear. We diff the previously-sent keys against the new
        snapshot and append a delete marker for the gone ones, in the
        SAME payload (a separate emit could be dropped by the
        inflight-replace path)."""
        try:
            snap = self._build_snapshot()
            # _last_sent doubles as the delete-diff base and is also
            # written by the 60fps frame thread — read+clear atomically so
            # a concurrent frame can neither tear the diff nor erase the
            # knowledge that a now-removed object was ever on screen.
            with self._state_lock:
                prev_keys = set(self._last_sent.keys())
                self._last_sent.clear()
            for k in prev_keys - set(snap.keys()):
                snap[k] = {"delete": True}
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
            # list(): see _build_pose_frame — the workflow thread mutates
            # components concurrently.
            for comp_name, comp in list(getattr(self.workspace, "components", {}).items()):
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
            markers = self._hover_markers()
            if markers:
                batch["__hover_markers__"] = {"markers": list(markers.values())}
        except Exception as e:
            print("[Display] error building snapshot batch:", e)
        return batch

    def _hover_markers(self):
        """Live hover-plane markers, recomputed from the CURRENT stacks
        so pins track what the ops would actually do (a tube entering
        the decapper lifts its pin by the tube height). Dedupes recipes
        sharing a component/anchor/padding."""
        out = {}
        for r in list(getattr(self.workspace, "hover_marker_recipes", []) or []):
            try:
                m = r._hover_marker(*r._hover_marker_args)
                if m is not None:
                    out[f"{r.component.name}:{m['anchor']}:{round(m['padding'])}"] = m
            except Exception:
                continue
        return out

    def _build_pose_frame(self):
        """Only pose + visible; DO NOT delete meshUrl. Delta: skip unchanged objects."""
        try:
            poses = self.workspace.compute_world_poses()
        except Exception as e:
            print("[Display] compute_world_poses() failed in frame:", e)
            poses = {}

        world_boxes_by_solid, flange_boxes_by_solid = self._collision_boxes_by_solid()

        out = {}
        total = 0
        # list(): the workflow thread adds/removes components concurrently;
        # iterating the live dict raises "changed size during iteration"
        # and silently drops the whole frame.
        for comp_name, comp in list(getattr(self.workspace, "components", {}).items()):
            assembly = getattr(comp, "assembly", {}) or {}
            for solid_name, _solid in assembly.items():
                key = f"{comp_name}_{solid_name}"
                total += 1
                p = poses.get(key, [0, 0, 0, 0, 0, 0])

                key_boxes = (comp_name, solid_name)
                cw = world_boxes_by_solid.get(key_boxes, [])
                cf = flange_boxes_by_solid.get(key_boxes, [])

                # Delta check: skip if pose and collision unchanged.
                # Locked — send_snapshot (workflow thread) reads + clears
                # this cache as its delete-diff base.
                pose_t = tuple(p) if isinstance(p, list) else p
                col_sig = (len(cw), len(cf))
                with self._state_lock:
                    prev = self._last_sent.get(key)
                    if prev is not None and prev[0] == pose_t and prev[1] == col_sig:
                        continue  # unchanged — skip
                    self._last_sent[key] = (pose_t, col_sig)
                out[key] = {
                    "pose": p,
                    "visible": True,
                    "collisionWorld": cw,
                    "collisionFlange": cf,
                }

        # Hover markers register when recipes load — AFTER boot and any
        # already-sent snapshot — and their heights track the live
        # stacks, so frames carry them whenever the content changes.
        markers = self._hover_markers()
        if markers:
            sig = tuple(sorted((k, round(m["pose"][2], 1), round(m["base"][2], 1))
                               for k, m in markers.items()))
            with self._state_lock:
                if self._last_sent.get("__hover_markers__") != sig:
                    self._last_sent["__hover_markers__"] = sig
                    out["__hover_markers__"] = {"markers": list(markers.values())}

        return out

    # The viewer shows the PLANNER's envelope: boxes inflated by the
    # same default padding core.motion_plan plans with (and only the
    # padding_enabled boxes — the planner's own opt-in rule). What the
    # operator sees is what the planner enforces. The scene builder
    # keeps rendering the raw boxes — padding is not a scene concept.
    PLAN_PADDING = 10.0

    def _collision_boxes_by_solid(self, padding=None):
        if padding is None:
            padding = self.PLAN_PADDING
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
            encoded = json.dumps(payload)
        except Exception as e:
            print("[Display] json.dumps failed:", e)
            return


        with self._state_lock:
            if self._inflight:
                # MERGE into the pending payload (key-wise, newest spec per
                # key wins) instead of replacing it. Replacing dropped the
                # one-shot ``{delete: true}`` markers whenever two scene
                # changes landed inside one ack window — the viewer never
                # heard about the removal and kept the stale mesh forever
                # (the "ghost disc in the rack" bug).
                if self._pending is not None:
                    merged = dict(self._pending)
                    merged.update(payload)
                    self._pending = merged
                else:
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
                    if frame:  # delta: skip emit if nothing changed
                        self._emit_update(frame)
            except Exception as e:
                print("[Display] error in main loop:", e)

            next_t += period
            delay = next_t - time.perf_counter()

            if delay < -period:
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



// --- DEBUG OVERLAY (auto) ---
(function(){
  const mk = () => {
    let el = document.getElementById("__ws_error_overlay");
    if (el) return el;
    el = document.createElement("div");
    el.id="__ws_error_overlay";
    el.style.cssText="position:fixed;left:0;right:0;bottom:0;max-height:45vh;overflow:auto;z-index:999999;background:#200;color:#fdd;font:12px/1.35 monospace;padding:10px;border-top:2px solid #f55;white-space:pre-wrap;";
    el.innerHTML="Workspace Builder crashed. Open DevTools console for full context.\n";
    document.body.appendChild(el);
    return el;
  };
  const show = (msg) => { try { mk().textContent += "\n" + msg; } catch(e){} };
  window.addEventListener("error", (e) => {
    const msg = (e?.error?.stack) ? e.error.stack : `${e.message} @ ${e.filename}:${e.lineno}:${e.colno}`;
    show(msg);
  });
  window.addEventListener("unhandledrejection", (e) => {
    const r = e.reason;
    const msg = (r && r.stack) ? r.stack : ("Unhandled rejection: " + String(r));
    show(msg);
  });
  console.log("[builder] debug overlay enabled");
})();
// --- END DEBUG OVERLAY ---

    import * as THREE from "three";
    import { OrbitControls } from "three/addons/controls/OrbitControls.js";
    import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
    import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
    import io from "/vendor/socket.io.esm.min.js";

    // Shared Draco decoder — used by every GLTFLoader instance in this
    // module. Path is local (no internet). Handles both Draco-compressed
    // and uncompressed GLBs transparently.
    const _sbDracoLoader = new DRACOLoader();
    _sbDracoLoader.setDecoderPath("/vendor/three-addons/draco/");
    function makeGltfLoader() {
      const ldr = new GLTFLoader();
      ldr.setDRACOLoader(_sbDracoLoader);
      return ldr;
    }

    // -------- API base path --------
    const SB_API = "/scene-builder/api";

    // -------- cache-busting --------
    function versioned(url) {
      const v = (window.__CONFIG_VERSION__ || Date.now());
      const sep = url.includes("?") ? "&" : "?";
      return `${url}${sep}v=${v}`;
    }
    async function getVersion() {
      try {
        const r = await fetch(SB_API + "/config_version", { cache: "no-store" });
        const j = await r.json();
        window.__CONFIG_VERSION__ = j?.version || String(Date.now());
      } catch {
        window.__CONFIG_VERSION__ = String(Date.now());
      }
    }


// ── WebGL context guard ─────────────────────────────────────────────
// A browser caps live WebGL contexts per process (~16 in Chrome), and
// this GUI can hold several at once — main viewer, view cube, builder
// thumbnails, plus one set per open tab. Past the cap the browser
// REFUSES a context ("Web page caused context loss and was blocked"),
// and an uncaught throw here used to take the whole page down with it.
//
// Readers degrade, they never crash: on failure the 3D area explains
// itself and everything else on the page keeps working.
function makeRenderer(opts, mountEl) {
  try {
    return new THREE.WebGLRenderer(opts);
  } catch (err) {
    console.error("WebGL unavailable:", err);
    const note = document.createElement("div");
    note.style.cssText =
      "position:absolute;inset:0;display:flex;align-items:center;justify-content:center;" +
      "text-align:center;padding:24px;font:14px/1.6 system-ui,sans-serif;color:#8e8e93";
    note.innerHTML =
      "<div><b style='display:block;font-size:15px;margin-bottom:6px;color:inherit'>" +
      "3D view unavailable</b>The browser refused a WebGL context — usually too many " +
      "open views.<br>Close some tabs with a 3D view (or reload this one) to get it back." +
      "<br>Everything else on this page keeps working.</div>";
    try { (mountEl || document.body).appendChild(note); } catch (_) {}
    return null;
  }
}

    async function boot() {
      // ---- Viewer container (right-side div, not full window) ----
      const viewerEl = document.getElementById("viewerArea");

      // --- Scene / Camera / Renderer ---
      const THEME_KEY  = "orch_theme";
      const DARK_BG    = new THREE.Color("#080c12");
      const LIGHT_BG   = new THREE.Color("#f0f4f8");
      const DARK_GRID  = { minor: 0x182030, major: 0x253548 };
      const LIGHT_GRID = { minor: 0xbbbbbb, major: 0x888888 };

      const scene = new THREE.Scene();

      const camera = new THREE.PerspectiveCamera(
        60,
        viewerEl.clientWidth / viewerEl.clientHeight,
        0.1,
        100000
      );
      camera.up.set(0,0,1);
      // True isometric: equal (1,1,1) direction → equal angles on all axes.
      camera.position.set(1600, 1600, 1600);

      // Pixel ratio capped at 1.5 for FPS on Retina/4K. Antialias stays
      // on for clean edges. See orchestrator/index.html.
      const renderer = makeRenderer(
        { antialias: true, powerPreference: "high-performance" }, viewerEl);
      if (!renderer) return;              // page stays alive without 3D
      // WebGL renders on the VIEWING machine's GPU (not the Pi) — cap
      // at 2x, not 1.5x: the old cap under-resolved the canvas on
      // scaled displays and read as aliasing on every mesh edge.
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setSize(viewerEl.clientWidth, viewerEl.clientHeight);
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 0.9;
      viewerEl.appendChild(renderer.domElement);

      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = false;
      controls.target.set(0,0,0);
      controls.minDistance = 5;
      controls.maxDistance = 50000;

      // Pan mode: right-drag always pans; when panMode is ON, left-drag pans too.
      function applyPanMode(){
        controls.enablePan = true;
        controls.mouseButtons.LEFT = (window.builderState.panMode) ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE;
        controls.mouseButtons.RIGHT = THREE.MOUSE.PAN;
        controls.mouseButtons.MIDDLE = THREE.MOUSE.DOLLY;
      }

      // --- Environment — procedural, longitude-symmetric gradient ---
      // Replaces light.hdr: the studio HDR had a measured 14x luminance
      // swing across compass directions, so identical materials rendered
      // visibly darker on one side of the bench than the other. A vertical
      // gradient (bright zenith → mid horizon → dim floor) gives the same
      // soft image-based light with ZERO left/right bias. The sphere is
      // rotated so the gradient's zenith aligns with world +Z (Z-up scene).
      const pmrem = new THREE.PMREMGenerator(renderer);
      {
        const envCanvas = document.createElement("canvas");
        envCanvas.width = 64; envCanvas.height = 256;
        const g = envCanvas.getContext("2d");
        const grad = g.createLinearGradient(0, 0, 0, envCanvas.height);
        grad.addColorStop(0.0, "#ffffff");   // zenith
        grad.addColorStop(0.5, "#b9bfc9");   // horizon
        grad.addColorStop(1.0, "#6d737b");   // floor bounce
        g.fillStyle = grad;
        g.fillRect(0, 0, envCanvas.width, envCanvas.height);
        const envTex = new THREE.CanvasTexture(envCanvas);
        envTex.colorSpace = THREE.SRGBColorSpace;
        const envScene = new THREE.Scene();
        const envMesh = new THREE.Mesh(
          new THREE.SphereGeometry(100, 64, 64),
          new THREE.MeshBasicMaterial({ map: envTex, side: THREE.BackSide })
        );
        envMesh.rotation.x = Math.PI / 2;
        envScene.add(envMesh);
        scene.environment = pmrem.fromScene(envScene).texture;
        scene.environmentIntensity = 0.9;
      }

      // --- Helpers (grid/axes) ---
      function makeRectGrid(width, height, step, major,
                            colorMinor = 0xaaaaaa,
                            colorMajor = 0xdddddd) {
        const geom = new THREE.BufferGeometry();
        const verts = [];
        const colors = [];
        const halfW = width / 2;
        const halfH = height / 2;
        const cMinor = new THREE.Color(colorMinor);
        const cMajor = new THREE.Color(colorMajor);

        for (let x = -halfW; x <= halfW + 1e-6; x += step) {
          const c = Math.abs(x % major) < 1e-6 ? cMajor : cMinor;
          verts.push(x, -halfH, 0,  x, halfH, 0);
          colors.push(c.r, c.g, c.b,  c.r, c.g, c.b);
        }
        for (let y = -halfH; y <= halfH + 1e-6; y += step) {
          const c = Math.abs(y % major) < 1e-6 ? cMajor : cMinor;
          verts.push(-halfW, y, 0,  halfW, y, 0);
          colors.push(c.r, c.g, c.b,  c.r, c.g, c.b);
        }

        geom.setAttribute("position",
          new THREE.Float32BufferAttribute(verts, 3));
        geom.setAttribute("color",
          new THREE.Float32BufferAttribute(colors, 3));

        return new THREE.LineSegments(
          geom,
          new THREE.LineBasicMaterial({ vertexColors: true, toneMapped: false })
        );
      }

      // --- Render on demand (declared early so markDirty is available) ---
      let _needsRender = true;
      let _lastRenderMs = 0;
      const IDLE_RENDER_INTERVAL = 500;
      function markDirty() { _needsRender = true; }

      // grid: 6000 × 6000, theme-aware
      let _sbGridMesh = makeRectGrid(6000, 6000, 50, 500, DARK_GRID.minor, DARK_GRID.major);
      _sbGridMesh.visible = true;   // grid on by default
      scene.add(_sbGridMesh);

      let _sbCurrentTheme = localStorage.getItem(THEME_KEY) || "dark";
      function sbApplyTheme(theme) {
        _sbCurrentTheme = theme;
        const isLight = theme === "light";
        scene.background = isLight ? LIGHT_BG : DARK_BG;
        scene.remove(_sbGridMesh);
        _sbGridMesh.geometry.dispose();
        _sbGridMesh.material.dispose();
        const gc = isLight ? LIGHT_GRID : DARK_GRID;
        _sbGridMesh = makeRectGrid(6000, 6000, 50, 500, gc.minor, gc.major);
        _sbGridMesh.visible = _sbShowGrid;   // preserve toggle state across theme rebuild
        scene.add(_sbGridMesh);
        markDirty();
      }
      let _sbShowGrid = true;   // grid on by default
      sbApplyTheme(_sbCurrentTheme);
      window.addEventListener("storage", (e) => {
        if (e.key === THEME_KEY) sbApplyTheme(e.newValue || "dark");
      });

      // Expose grid toggle for btnGrid (wired in ensureBuilderBar)
      window.__sbToggleGrid = () => {
        _sbShowGrid = !_sbShowGrid;
        _sbGridMesh.visible = _sbShowGrid;
        const btn = document.getElementById("btnGrid");
        if (btn) btn.classList.toggle("active", _sbShowGrid);
      };
      // Reflect the grid state on the button (on by default)
      const _initGridBtn = document.getElementById("btnGrid");
      if (_initGridBtn) _initGridBtn.classList.toggle("active", _sbShowGrid);

      // Per-object edge outlines (the black lines around each component).
      // On by default; addEdgeOverlay seeds new objects from this flag.
      let _sbShowEdges = true;
      function _applyEdgesVisible() {
        scene.traverse(o => {
          const el = o.userData && o.userData.__edgeLines;
          if (el) el.visible = _sbShowEdges;
        });
        const btn = document.getElementById("btnEdges");
        if (btn) btn.classList.toggle("active", _sbShowEdges);
        markDirty();
      }
      window.__sbToggleEdges = () => {
        _sbShowEdges = !_sbShowEdges;
        _applyEdgesVisible();
      };
      // Edges start active
      const _initEdgesBtn = document.getElementById("btnEdges");
      if (_initEdgesBtn) _initEdgesBtn.classList.add("active");

      function axisLine(start, end, color) {
        const geom = new THREE.BufferGeometry();
        geom.setAttribute(
          "position",
          new THREE.Float32BufferAttribute(
            [start.x, start.y, start.z, end.x, end.y, end.z],
            3
          )
        );
        return new THREE.Line(
          geom,
          new THREE.LineBasicMaterial({ color, toneMapped: false })
        );
      }
      /*
      scene.add(axisLine(new THREE.Vector3(0,0,0),
                         new THREE.Vector3(1500,0,0), 0xff0000));
      scene.add(axisLine(new THREE.Vector3(0,0,0),
                         new THREE.Vector3(0,500,0), 0x00ff00));
      scene.add(axisLine(new THREE.Vector3(0,0,0),
                         new THREE.Vector3(0,0,500), 0x0000ff));
      */
      // --- Lights ---
      scene.add(new THREE.HemisphereLight(0xdde8ff, 0x39414f, 0.85));
      const dir = new THREE.DirectionalLight(0xfff4e0, 0.65);
      dir.position.set(1200, 900, 1500); scene.add(dir);
      const fill = new THREE.DirectionalLight(0xb0ccff, 0.4);
      fill.position.set(-800, -600, 600); scene.add(fill);

      // --- GLTF + object management ---
      const gltfLoader = makeGltfLoader();
      // Parse-once GLB cache. A project import spawns the same tiny
      // mesh many times (bd: 96 tips, 20 tubes, 19 caps) — without the
      // cache every instance re-fetched and re-ran the Draco decode.
      // Same 4-arg shape as gltfLoader.load, so callsites only change
      // the function name; callers get a fresh clone per call.
      const __gltfParseCache = new Map();
      function loadGltfCached(url, onLoad, _onProgress, onError) {
        let pr = __gltfParseCache.get(url);
        if (!pr) {
          pr = new Promise((resolve, reject) =>
            gltfLoader.load(url, g => resolve(g.scene), undefined, reject));
          __gltfParseCache.set(url, pr);
        }
        pr.then(scene => onLoad({ scene: scene.clone(true) }))
          .catch(e => { __gltfParseCache.delete(url); if (onError) onError(e); });
      }
      const objectsByName = new Map();
      window.objectsByName = objectsByName;


      // --- Collision visuals (local boxes) ---
      // Collision meshes are parented under each solid holder so they follow drag/moves.
      let showCollisionBoxes = false;

      function makeCollisionMeshLocal(pose, scale, boxForGrip) {
        const geom = new THREE.BoxGeometry(scale[0], scale[1], scale[2]);
        const mat = new THREE.MeshBasicMaterial({
          color: boxForGrip ? 0x3388ff : 0xff3344,
          transparent: true,
          opacity: 0.28,
          depthWrite: false
        });
        const mesh = new THREE.Mesh(geom, mat);
        const [x, y, z, rx, ry, rz] = pose;
        mesh.position.set(x, y, z);
        mesh.quaternion.copy(rodriguesDegToQuaternion(rx, ry, rz));
        mesh.renderOrder = 20;
        mesh.userData = mesh.userData || {};
        mesh.userData.__isCollisionBox = true;
        return mesh;
      }

      function clearCollisionGroup(group) {
        if (!group) return;
        while (group.children.length) {
          const c = group.children[0];
          group.remove(c);
          try { c.geometry?.dispose?.(); } catch (e) {}
          try { c.material?.dispose?.(); } catch (e) {}
        }
      }

      function fillCollisionGroup(group, boxes, boxForGrip) {
        clearCollisionGroup(group);
        if (!Array.isArray(boxes)) return;
        for (const box of boxes) {
          if (!box?.pose || !box?.scale) continue;
          const mesh = makeCollisionMeshLocal(box.pose, box.scale, boxForGrip);
          group.add(mesh);
        }
      }

      function setCollisionVisible(v) {
        showCollisionBoxes = !!v;
        for (const obj of objectsByName.values()) {
          obj.traverse((o) => {
            if (o?.userData?.__isCollisionGroup) o.visible = showCollisionBoxes;
          });
        }
      }

      function base64ToArrayBuffer(b64) {
        const bin = atob(b64);
        const len = bin.length;
        const bytes = new Uint8Array(len);
        for (let i = 0; i < len; i++) {
          bytes[i] = bin.charCodeAt(i);
        }
        return bytes.buffer;
      }

      function rodriguesDegToQuaternion(rx, ry, rz) {
        const ang = Math.hypot(rx, ry, rz);
        if (ang === 0) return new THREE.Quaternion();
        const ax = rx / ang;
        const ay = ry / ang;
        const az = rz / ang;
        const q = new THREE.Quaternion();
        q.setFromAxisAngle(new THREE.Vector3(ax, ay, az), ang * Math.PI / 180);
        return q;
      }
      window.rodriguesDegToQuaternion = rodriguesDegToQuaternion;


      // Collision toggle is now in the top builder bar (collisionTopBtn)

      // Convert a quaternion to a Rodrigues rotation vector in *degrees*.
      // A Rodrigues vector is axis * angle, where angle is in degrees.
      // This is the inverse of rodriguesDegToQuaternion (up to the usual
      // axis/angle sign ambiguity).
      function quaternionToRodriguesDeg(qIn) {
        const q = qIn.clone().normalize();
        // Clamp to avoid NaNs from acos.
        const w = Math.max(-1, Math.min(1, q.w));
        let angle = 2 * Math.acos(w); // radians, in [0, pi]
        const s = Math.sqrt(1 - w*w);
        let ax = 0, ay = 0, az = 0;
        if (s < 1e-8) {
          // If angle is ~0, axis is arbitrary.
          ax = 1; ay = 0; az = 0;
          angle = 0;
        } else {
          ax = q.x / s;
          ay = q.y / s;
          az = q.z / s;
        }
        const angleDeg = angle * 180 / Math.PI;
        return [ax * angleDeg, ay * angleDeg, az * angleDeg];
      }
      window.quaternionToRodriguesDeg = quaternionToRodriguesDeg;

      // ===== Picking + sticky anchor gizmos =====
      const raycaster = new THREE.Raycaster();
      const pointer   = new THREE.Vector2();
      const pickableMeshes = new Set();    // only meshes

      // Active binding:
      // { obj, items: [{name,pLocal,qLocal,axes,label}] }
      let activeAnchors = null;

      const anchorsLayer = new THREE.Group();
      anchorsLayer.frustumCulled = false;
      scene.add(anchorsLayer);

      function makeTextSprite(
        text,
        fontPx = 72,
        bg = "#000",
        fg = "#fff",
        alpha = 0.7,
        pad = 10,
        bold = true
      ) {
        const canvas = document.createElement("canvas");
        const ctx = canvas.getContext("2d");

        ctx.font = `${bold ? "bold " : ""}${fontPx}px sans-serif`;
        const metrics = ctx.measureText(text);
        canvas.width  = Math.ceil(metrics.width + pad * 2);
        canvas.height = Math.ceil(fontPx + pad * 2);

        ctx.font = `${bold ? "bold " : ""}${fontPx}px sans-serif`;
        ctx.textAlign = "left";
        ctx.textBaseline = "top";

        ctx.fillStyle = bg;
        ctx.globalAlpha = alpha;
        ctx.fillRect(0,0,canvas.width,canvas.height);

        ctx.globalAlpha = 1.0;
        ctx.fillStyle = fg;
        ctx.fillText(text, pad, pad);

        const tex = new THREE.CanvasTexture(canvas);
        tex.colorSpace = THREE.SRGBColorSpace;

        const mat = new THREE.SpriteMaterial({
          map: tex,
          depthTest: false
        });
        const spr = new THREE.Sprite(mat);

        const scale = 0.1;
        spr.scale.set(canvas.width * scale, canvas.height * scale, 1);
        spr.renderOrder = 999;
        spr.frustumCulled = false;
        return spr;
      }

      function makeAnchorLabel(text, color = "#ffffff", fontPx = 72) {
        const canvas = document.createElement("canvas");
        const ctx = canvas.getContext("2d");
        const font = `bold ${fontPx}px -apple-system, BlinkMacSystemFont, sans-serif`;
        ctx.font = font;
        const metrics = ctx.measureText(text);
        const pad = 12;
        canvas.width  = Math.ceil(metrics.width + pad * 2);
        canvas.height = Math.ceil(fontPx * 1.3 + pad * 2);

        ctx.font = font;
        ctx.textAlign = "left";
        ctx.textBaseline = "top";

        // Fill color — no outline
        ctx.fillStyle = color;
        ctx.fillText(text, pad, pad);

        const tex = new THREE.CanvasTexture(canvas);
        tex.colorSpace = THREE.SRGBColorSpace;
        const mat = new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true });
        const spr = new THREE.Sprite(mat);
        const scale = 0.09;
        spr.scale.set(canvas.width * scale, canvas.height * scale, 1);
        spr.renderOrder = 999;
        spr.frustumCulled = false;
        return spr;
      }

      function makeAxesHelperAlwaysOnTop(size) {
        const axes = new THREE.AxesHelper(size);
        axes.traverse(child => {
          if (child.material && child.material.isMaterial) {
            child.material.depthTest = false;
            child.renderOrder = 999;
          }
        });
        axes.renderOrder = 999;
        axes.frustumCulled = false;
        return axes;
      }

      // ---- CLEAR ANCHORS (updated: also hides bottom-left UI) ----
      function clearAnchors() {
        if (activeAnchors) {
          activeAnchors.items.forEach(it => {
            anchorsLayer.remove(it.axes);
            if (it.label) anchorsLayer.remove(it.label);
            if (it.dot) {
              anchorsLayer.remove(it.dot);
              if (it.dot.geometry) it.dot.geometry.dispose();
              if (it.dot.material) it.dot.material.dispose();
            }
            if (it.ring) {
              anchorsLayer.remove(it.ring);
              if (it.ring.geometry) it.ring.geometry.dispose();
              if (it.ring.material) it.ring.material.dispose();
            }
            if (it.pick) {
              anchorsLayer.remove(it.pick);
              pickableMeshes.delete(it.pick);
              if (it.pick.geometry) it.pick.geometry.dispose();
              if (it.pick.material) it.pick.material.dispose();
            }
          });
        }
        activeAnchors = null;

        const bar = document.getElementById("infoBar");
        if (bar) {
          bar.style.display = "none";
        }
      }

      // ---- BUILD ANCHORS (updated: uses bottom-left UI for Name/Type/Solid) ----
      function buildAnchorsFor(obj) {
        clearAnchors();

        // Support both legacy `anchors` and newer multi-solid `anchorsBySolid` storage.
        // IMPORTANT: for multi-solid assemblies (like core), show anchors for ALL solids,
        // not just the current/active one (otherwise you only see e.g. A0).
        const ud = obj?.userData || {};
        const ab = (ud.anchorsBySolid && typeof ud.anchorsBySolid === "object") ? ud.anchorsBySolid : null;

        // Convert an anchor defined in a solid's local frame into the root component's local frame.
        function __solidAnchorToRoot(obj, solidKey, pLocal, qLocal) {
          try {
            if (!solidKey) return { p: pLocal, q: qLocal };
            const holder = obj.getObjectByName ? obj.getObjectByName(String(solidKey)) : null;
            if (!holder || holder === obj) return { p: pLocal, q: qLocal };
            // Position: holderLocal -> world -> objLocal
            const pW = holder.localToWorld(pLocal.clone());
            const pRoot = obj.worldToLocal(pW.clone());
            // Orientation: holderLocal -> world -> objLocal
            const hW = new THREE.Quaternion();
            const oW = new THREE.Quaternion();
            holder.getWorldQuaternion(hW);
            obj.getWorldQuaternion(oW);
            const qW = hW.clone().multiply(qLocal);
            const qRoot = oW.clone().invert().multiply(qW);
            return { p: pRoot, q: qRoot };
          } catch (e) {
            return { p: pLocal, q: qLocal };
          }
        }

        // Build a flat list of anchors to display.
        // Each entry can optionally carry a `solidKey` for correct snapping when clicked.
        const flat = [];

        // 1) If anchorsBySolid exists with multiple solids, show all.
        if (ab && Object.keys(ab).length) {
          const solidKeys = Object.keys(ab);
          for (const solidKey of solidKeys) {
            const anchors = ab[solidKey] || {};
            for (const [name, arr] of Object.entries(anchors)) {
              if (!Array.isArray(arr) || arr.length !== 6) continue;
              flat.push({ solidKey, name, arr });
            }
          }
        } else {
          // 2) Legacy anchors
          const anchors = (ud.anchors && typeof ud.anchors === "object") ? ud.anchors : {};
          for (const [name, arr] of Object.entries(anchors)) {
            if (!Array.isArray(arr) || arr.length !== 6) continue;
            flat.push({ solidKey: null, name, arr });
          }
        }

        if (!flat.length) return;

        // De-dupe display names (same anchor names across different solids like "center").
        // Prefer prefixing with solidKey when needed.
        const nameCounts = new Map();
        for (const it of flat) {
          const k = String(it.name);
          nameCounts.set(k, (nameCounts.get(k) || 0) + 1);
        }

        const items = [];
        for (const it of flat) {
          const { solidKey, name, arr } = it;
          const [ax, ay, az, rx, ry, rz] = arr;
          const pSolid = new THREE.Vector3(ax, ay, az);
          const qSolid = rodriguesDegToQuaternion(rx, ry, rz);
          const xform = __solidAnchorToRoot(obj, solidKey, pSolid, qSolid);
          const pLocal = xform.p;
          const qLocal = xform.q;

          const displayName = (solidKey && (nameCounts.get(String(name)) || 0) > 1)
            ? `${solidKey}:${name}`
            : String(name);

          const size = 7;
          const axes = makeAxesHelperAlwaysOnTop(size);

          // Determine anchor color based on builder mode
          const mode = window.builderState?.mode || "IDLE";
          const isChildMode = mode === "PICK_CHILD_ANCHOR";
          const isTargetMode = mode === "PICK_TARGET_ANCHOR";
          const anchorColor = isChildMode ? 0x0a84ff : isTargetMode ? 0x34c759 : 0x0a84ff;
          const anchorColorCSS = isChildMode ? "#0a84ff" : isTargetMode ? "#34c759" : "#0a84ff";
          const labelBg = isChildMode ? "#0a84ff" : isTargetMode ? "#34c759" : "#000";

          const label = makeAnchorLabel(displayName, "#000000");
          label.material.opacity = 0.9;
          label.userData.__baseScale = label.scale.clone();

          anchorsLayer.add(axes);
          anchorsLayer.add(label);

          // Visible anchor dot — small by default, grows on hover
          const dotGeo = new THREE.SphereGeometry(1.5, 16, 16);
          const dotMat = new THREE.MeshBasicMaterial({
            color: anchorColor,
            transparent: true,
            opacity: 0.6,
            depthTest: false,
          });
          const dot = new THREE.Mesh(dotGeo, dotMat);
          dot.renderOrder = 997;
          dot.frustumCulled = false;
          anchorsLayer.add(dot);

          // Glow ring — hidden by default, appears on hover
          const ringGeo = new THREE.RingGeometry(4, 7, 24);
          const ringMat = new THREE.MeshBasicMaterial({
            color: anchorColor,
            transparent: true,
            opacity: 0.0,
            depthTest: false,
            side: THREE.DoubleSide,
          });
          const ring = new THREE.Mesh(ringGeo, ringMat);
          ring.renderOrder = 996;
          ring.frustumCulled = false;
          anchorsLayer.add(ring);

          // Clickable pick sphere (larger than dot for easy clicking)
          const pickGeo = new THREE.SphereGeometry(8, 12, 12);
          const pickMat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.01, depthTest: false });
          const pick = new THREE.Mesh(pickGeo, pickMat);
          pick.renderOrder = 998;
          pick.frustumCulled = false;
          pick.userData.__isAnchorPick = true;
          pick.userData.anchorName = name;
          pick.userData.ownerName = obj?.name || "";
          if (solidKey) pick.userData.solidKey = solidKey;
          // Store refs for hover effect
          pick.userData.__dotMesh = dot;
          pick.userData.__ringMesh = ring;
          pick.userData.__labelSprite = label;
          pick.userData.__dotColor = anchorColor;
          anchorsLayer.add(pick);

          // register for raycast picking
          pickableMeshes.add(pick);

          items.push({ name: displayName, rawName: name, solidKey, pLocal, qLocal, axes, label, pick, dot, ring });
        }

        // Extract data
        const comp  = obj.userData?.componentName || "--";
        const type  = obj.userData?.typeName      || obj.userData?.type || "--";
        const solid = obj.userData?.solidName     || (ab ? "multi" : "--");

        // Update bottom-left display
        const bar = document.getElementById("infoBar");
        const nameEl  = document.getElementById("infoName");
        const typeEl  = document.getElementById("infoType");
        const solidEl = document.getElementById("infoSolid");

        if (nameEl)  nameEl.textContent  = `Name: ${comp}`;
        if (typeEl)  typeEl.textContent  = `Type: ${type}`;
        if (solidEl) solidEl.textContent = `Solid: ${solid}`;
        if (bar)     bar.style.display   = "flex";

        activeAnchors = { obj, items };
        updateAnchorsNow(); // position immediately once
      }
      // Exposed so the sidebar Objects list can mirror a viewport click
      // (selection + anchors + info bar) — see updateObjectList().
      window.__buildAnchorsFor = buildAnchorsFor;

      // ---- UPDATE ANCHORS (updated: no 3D comp/solid labels anymore) ----
      function updateAnchorsNow() {
        if (!activeAnchors) return;
        const { obj, items } = activeAnchors;
        if (!obj.parent) {
          clearAnchors();
          return;
        }

        const objWorldQ = new THREE.Quaternion();
        obj.getWorldQuaternion(objWorldQ);

        for (const it of items) {
          const pWorld = obj.localToWorld(it.pLocal.clone());
          const qWorld = objWorldQ.clone().multiply(it.qLocal);
          it.axes.position.copy(pWorld);
          it.axes.quaternion.copy(qWorld);
          if (it.pick) { it.pick.position.copy(pWorld); it.pick.quaternion.copy(qWorld); }
          if (it.dot) { it.dot.position.copy(pWorld); }
          if (it.ring) { it.ring.position.copy(pWorld); it.ring.lookAt(camera.position); }
          if (it.label) {
            it.label.position.copy(pWorld).add(new THREE.Vector3(0,0,18));
          }
        }
      }

      function setPointerFromEvent(event) {
        const rect = renderer.domElement.getBoundingClientRect();
        pointer.x = ((event.clientX - rect.left) / rect.width)  * 2 - 1;
        pointer.y = ((event.clientY - rect.top)  / rect.height) * -2 + 1;
      }

      // Anchor "hit zones" (nearly-invisible spheres) are useful when the user is
      // explicitly picking anchors (joints / pattern second anchor, etc.).
      // But they should NOT steal clicks during normal object selection.
      function wantAnchorHitZones() {
        const m = window.builderState?.mode || "IDLE";
        // Only allow clicking the anchor hit-zones when we're in a mode that
        // expects an anchor click.
        return (
          m === "PICK_TARGET_ANCHOR" ||
          m === "PICK_CHILD_ANCHOR" ||
          m === "RECTPATTERN_PICK_SECOND_ANCHOR" ||
          m === "COLLISIONBOX_PICK_TARGET" ||
          // any future modes can opt-in by containing "_ANCHOR"
          (typeof m === "string" && m.includes("_ANCHOR"))
        );
      }

      function pickFirstMesh() {
        raycaster.setFromCamera(pointer, camera);
        const targets = pickableMeshes.size
          ? Array.from(pickableMeshes)
          : scene.children;
        const hits = raycaster.intersectObjects(targets, true);
        if (!hits.length) return null;

        const allowAnchor = wantAnchorHitZones();
        for (const h of hits) {
          if (!h.object?.isMesh) continue;
          const isAnchorPick = !!(h.object.userData && h.object.userData.__isAnchorPick);
          if (isAnchorPick && !allowAnchor) continue;
          return h;
        }
        return null;
      }

      // ---- Quick-click filter (left button only) ----
      const CLICK_MS = 250;
      const CLICK_PX = 6;
      let downInfo = null; // {button,x,y,time}

      renderer.domElement.addEventListener("pointerdown", (e) => {
        downInfo = {
          button: e.button,
          x: e.clientX,
          y: e.clientY,
          time: performance.now()
        };
        if (e.button === 2) {
          e.preventDefault();
          clearAnchors();
        }
      });

      renderer.domElement.addEventListener("pointerup", (e) => {
        if (!downInfo) return;
        const dt = performance.now() - downInfo.time;
        const dx = Math.abs(e.clientX - downInfo.x);
        const dy = Math.abs(e.clientY - downInfo.y);
        const moved = (dx > CLICK_PX || dy > CLICK_PX);

        if (downInfo.button === 0 &&
            e.button === 0 &&
            dt <= CLICK_MS &&
            !moved) {
          setPointerFromEvent(e);
          const hit = pickFirstMesh();
          if (hit && hit.object?.isMesh) {
            // RectPattern: interactive selection (object + second point) handled here
            // ----------------------------
            // Rectangular Pattern: pick seed object
            // ----------------------------
            if (window.builderState?.mode === "RECTPATTERN_PICK_OBJECT") {
              try {
                // Robustly resolve the clicked mesh to a builder component root.
                const name = (function(){
                  let o = hit.object;
                  while (o) {
                    const cand = (o.userData && o.userData.componentName) ? o.userData.componentName : o.name;
                    if (cand && ((objectsByName && objectsByName.has && objectsByName.has(cand)) || (window.builderState && window.builderState.components && window.builderState.components[cand]))) return cand;
                    o = o.parent;
                  }
                  return null;
                })();
                const ui = window.builderState.rectPatternUi;
                if (!name || !ui) { showToast("Select a valid object."); downInfo=null; return; }

                // Cap→tube redirection: if the user clicks a cap, use its parent tube as the seed instead.
                // This way patterning a cap behaves identically to patterning its tube.
                let resolvedName = name;
                {
                  const clickedMeta = window.builderState.components?.[name];
                  if (clickedMeta && String(clickedMeta.type || "").startsWith("cap_") && clickedMeta.attach?.parent_name) {
                    const parentName = clickedMeta.attach.parent_name;
                    const parentMeta = window.builderState.components?.[parentName];
                    if (parentMeta && String(parentMeta.type || "").startsWith("tube_")) {
                      resolvedName = parentName;
                    }
                  }
                }

                // Determine whether object is anchored (attach metadata may be in builderState or in userData)
                const meta = (window.builderState.components && window.builderState.components[resolvedName]) ? window.builderState.components[resolvedName] : null;
                const obj = objectsByName.get(resolvedName);
                const attach = meta?.attach || obj?.userData?.builderAttach || obj?.userData?.attach || null;

                // Anchor A strategy:
                // - If object is anchored and we can read that anchor => use it.
                // - Otherwise allow free objects too (including ones created by a previous pattern)
                //   and use the object's current world position as Anchor A.
                let aWorld = null;
                let aOwner = null;
                let aName = null;
                const hasAttach = !!(attach && attach.parent_name && attach.parent_anchor);
                if (hasAttach) {
                  aWorld = __rpAnchorWorldPose(attach.parent_name, attach.parent_anchor);
                  if (aWorld && aWorld.pos) {
                    aOwner = attach.parent_name;
                    aName = attach.parent_anchor;
                  } else {
                    // If attach exists but anchor isn't readable, fall back to free mode.
                    aWorld = null;
                  }
                }

                // Store seed world pose and the offset from Anchor A to the seed object.
                const seedWorldPos = new THREE.Vector3();
                const seedWorldQuat = new THREE.Quaternion();
                obj.getWorldPosition(seedWorldPos);
                obj.getWorldQuaternion(seedWorldQuat);
                const aPos = (aWorld && aWorld.pos) ? aWorld.pos.clone() : seedWorldPos.clone();
                const seedOffset = seedWorldPos.clone().sub(aPos);

                window.builderState.rectPattern = {
                  seedName: resolvedName,
                  seedType: (meta && meta.type) ? meta.type : (obj?.userData?.typeName || ""),
                  seedOptions: (function(){
                    const o = (meta && typeof meta === "object") ? Object.assign({}, meta) : {};
                    delete o.type; delete o.attach;
                    return o;
                  })(),
                  childAnchor: (attach && attach.child_anchor) ? attach.child_anchor : "center",
                  seedWorldQuat,
                  attach: hasAttach ? attach : null,
                  aAnchor: {
                    ownerName: aOwner,
                    anchorName: aName,
                    pos: aPos.clone()
                  },
                  seedOffset,
                  bAnchor: null,
                  delta: null,
                  secondOwnerName: null
                };

                ui.seedName = resolvedName;
                ui.seedBox.dataset.state = "ok";
                ui.seedBox.textContent = hasAttach ? `${resolvedName} ✓` : `${resolvedName} ✓ (free)`;
                ui.seedBox.style.borderColor = "rgba(0,140,0,0.55)";
                ui.seedBox.style.background = "rgba(0,140,0,0.08)";
                ui.seedClear.style.display = "inline-flex";

                // reset point box
                ui.pointBox.dataset.state = "empty";
                ui.pointBox.textContent = "Select second object…";
                ui.pointBox.style.borderColor = "rgba(0,0,0,0.18)";
                ui.pointBox.style.background = "rgba(0,0,0,0.02)";
                ui.pointClear.style.display = "none";

                window.builderState.mode = "IDLE";
                try { window.hideBanner(); } catch(e) {}
                if (!hasAttach) {
                  showToast("First object set (free). Now pick second object.");
                } else {
                  showToast("First object set. Now pick second object.");
                }
                downInfo=null;
                return;
              } catch (e) {
                console.error(e);
                showToast("RectPattern error: " + (e?.message||e));
                window.builderState.mode = "IDLE";
                try { window.hideBanner(); } catch(e) {}
                downInfo=null;
                return;
              }
            }

            // ----------------------------
            // Rectangular Pattern: pick 2nd anchor (first pick object, then pick anchor)
            // ----------------------------
            if (window.builderState?.mode === "RECTPATTERN_PICK_SECOND_OBJECT") {
              try {
                const rp = window.builderState.rectPattern;
                const ui = window.builderState.rectPatternUi;
                if (!rp || !ui || !rp.seedName) { showToast("Pick the first object first."); downInfo=null; return; }

                const targetName = (function(){
                  let o = hit.object;
                  while (o) {
                    const cand = (o.userData && o.userData.componentName) ? o.userData.componentName : o.name;
                    if (cand && ((objectsByName && objectsByName.has && objectsByName.has(cand)) || (window.builderState && window.builderState.components && window.builderState.components[cand]))) return cand;
                    o = o.parent;
                  }
                  return null;
                })();
                if (!targetName) { showToast("Select a valid target object."); downInfo=null; return; }

                rp.secondOwnerName = targetName;
                window.builderState.mode = "RECTPATTERN_PICK_SECOND_ANCHOR";
                try { window.showBanner?.("Now click an anchor on the target object."); } catch(e) {}
                try {
                  const root = objectsByName.get(targetName);
                  if (root) buildAnchorsFor(root);
                } catch(e) {}

                // Update UI (object chosen, anchor pending)
                ui.pointBox.dataset.state = "empty";
                ui.pointBox.textContent = `Target: ${targetName} (pick anchor…)`;
                ui.pointBox.style.borderColor = "rgba(60,130,255,0.7)";
                ui.pointBox.style.background = "rgba(60,130,255,0.08)";
                ui.pointClear.style.display = "inline-flex";

                // Populate the inline anchor list in the panel
                try { if (ui.populateAnchorList) ui.populateAnchorList(targetName); } catch(e) { console.warn(e); }

                downInfo=null;
                return;
              } catch (e) {
                console.error(e);
                showToast("RectPattern error: " + (e?.message||e));
                window.builderState.mode = "IDLE";
                try { window.hideBanner(); } catch(e) {}
                downInfo=null;
                return;
              }
            }

            // (Legacy free-point mode kept for safety but not used by the UI)
            if (window.builderState?.mode === "RECTPATTERN_PICK_SECOND") {
              try {
                const rp = window.builderState.rectPattern;
                const ui = window.builderState.rectPatternUi;
                if (!rp || !ui || !rp.aPos) { showToast("Pick the first object first."); downInfo=null; return; }

                const bPos = hit.point.clone();
                rp.bPos = bPos;
                rp.delta = bPos.clone().sub(rp.aPos);

                // Update UI
                ui.pointBox.dataset.state = "ok";
                ui.pointBox.textContent = `Second point ✓ (${bPos.x.toFixed(2)}, ${bPos.y.toFixed(2)}, ${bPos.z.toFixed(2)})`;
                ui.pointBox.style.borderColor = "rgba(0,140,0,0.55)";
                ui.pointBox.style.background = "rgba(0,140,0,0.08)";
                ui.pointClear.style.display = "inline-flex";

                // Disable axes with ~0 delta
                const eps = 1e-6;
                const dx = rp.delta.x, dy = rp.delta.y, dz = rp.delta.z;
                ui.nx.disabled = Math.abs(dx) < eps;
                ui.ny.disabled = Math.abs(dy) < eps;
                ui.nz.disabled = Math.abs(dz) < eps;
                if (ui.nx.disabled) ui.nx.value = "1";
                if (ui.ny.disabled) ui.ny.value = "1";
                if (ui.nz.disabled) ui.nz.value = "1";
                if (ui.hint) ui.hint.textContent = `Δ = (${dx.toFixed(2)}, ${dy.toFixed(2)}, ${dz.toFixed(2)}).`;

                window.builderState.mode = "IDLE";
                try { window.hideBanner(); } catch(e) {}
                showToast("Second point set.");
                downInfo=null;
                return;
              } catch (e) {
                console.error(e);
                showToast("RectPattern error: " + (e?.message||e));
                window.builderState.mode = "IDLE";
                try { window.hideBanner(); } catch(e) {}
                downInfo=null;
                return;
              }
            }

if (hit.object.userData && hit.object.userData.__isAnchorPick) {
              // Route anchor clicks based on current builder mode
              if (window.builderState?.mode === "COLLISIONBOX_PICK_TARGET" && window.builderState._colBoxPanelSetAnchor) {
                // 3D anchor click feeds into the panel
                try {
                  const anchorName = hit.object.userData.anchorName;
                  const solidKey = hit.object.userData.solidKey || null;
                  window.builderState._colBoxPanelSetAnchor(anchorName, solidKey);
                } catch (e) { console.error(e); }
                downInfo = null;
                return;
              } else if (window.builderState?.mode === "RECTPATTERN_PICK_SECOND_ANCHOR") {
                try { rectPatternHandleSecondAnchor(hit.object.userData.ownerName, hit.object.userData.anchorName); } catch (e) { console.error(e); showToast("RectPattern error: " + (e?.message||e)); }
              } else if (window.builderState?.mode === "PICK_CHILD_ANCHOR" && window.builderState._childAnchorCallback) {
                // 3D click on child object anchor during source anchor selection
                try {
                  const cb = window.builderState._childAnchorCallback;
                  const solidKey = hit.object.userData.solidKey || null;
                  const anchorName = hit.object.userData.anchorName;
                  if (solidKey) window.builderState._childAnchorSetSolid?.(solidKey);
                  cb(anchorName);
                } catch (e) { console.error(e); }
              } else {
                handleAnchorPick(hit.object.userData.ownerName, hit.object.userData.anchorName, hit.object.userData.solidKey || null);
              }
              downInfo = null;
              return;
            }
            // Resolve clicked mesh to a builder component root reliably (works for nested/anchored objects)
            const resolvedName = (function(){
              let o = hit.object;
              while (o) {
                const cand = (o.userData && o.userData.componentName) ? o.userData.componentName : o.name;
                if (cand && ((objectsByName && objectsByName.has && objectsByName.has(cand)) || (window.builderState && window.builderState.components && window.builderState.components[cand]))) return cand;
                o = o.parent;
              }
              return null;
            })();
            let node = resolvedName ? objectsByName.get(resolvedName) : null;
            const compName = resolvedName;
            if (!node || !compName) { showToast("Select a valid object."); downInfo=null; return; }
if (node) {
              if (window.builderState.mode === "PICK_TARGET_OBJECT" && window.builderState.pending) {
                // Route through the attach modal: highlights the pick in its
                // Parent-object list and reveals the anchor section — the
                // modal's callback owns targetName/mode. Fallback keeps the
                // 3D-only flow working if no modal is open.
                const cb = window.builderState._targetObjectCallback;
                if (cb) {
                  try { cb(compName); } catch (e) { console.warn(e); }
                } else {
                  window.builderState.targetName = compName;
                  window.builderState.mode = "PICK_TARGET_ANCHOR";
                  try { buildAnchorsFor(node); } catch (e) {}
                }
                showToast("Pick a target anchor (click in 3D or use the list).");
              }

              if (window.builderState.mode === "COLLISIONBOX_PICK_TARGET" && window.builderState._colBoxPanelSetTarget) {
                window.builderState._colBoxPanelSetTarget(compName);
              }

              if (window.builderState.mode === "COLLISIONBOX_PICK_Z_OBJ" && window.builderState._colBoxZCallback) {
                try { window.builderState._colBoxZCallback(compName); } catch(e) { console.error(e); }
                downInfo = null;
                return;
              }

              if (window.builderState.mode === "PATTERN_PICK_SOURCE_OBJECT" && window.builderState.pattern) {
                const srcName = compName;
                if (srcName === window.builderState.pattern.plateName) {
                  showToast("Invalid: cannot pattern the plate into itself.");
                } else {
                  // anchorsBySolid: prefer full mapping if available
                  const ab = node.userData.anchorsBySolid || { "solid_0": (node.userData.anchors || {}) };
                  openPatternSourceAnchorMenu(srcName, ab);
                  showToast("Choose the source anchor to place into each hole.");
                }
              }
	buildAnchorsFor(node);

	// While mid-joint, keep the *pending child* as the primary selection.
	// We still allow clicking other objects to inspect anchors/joints,
	// but Delete/Cancel should always refer to the object being jointed.
	if ((window.builderState.mode === "PICK_TARGET_OBJECT" || window.builderState.mode === "PICK_TARGET_ANCHOR") && window.builderState.pending?.name) {
	  try { setSelected(window.builderState.pending.name); } catch(e) {}
	}
            }
              if (window.builderState.mode === "IDLE") { const sel = compName; if (sel) setSelected(sel); }

          }
        }
        downInfo = null;
      });

      renderer.domElement.addEventListener("contextmenu", (e) => {
        e.preventDefault();
      });

      // ---- Hover highlight ----
      let __hoveredName = null;
      const __HOVER_EDGE_BLUE = new THREE.Color(0x0a84ff);
      const __HOVER_EDGE_GREEN = new THREE.Color(0x34c759);
      const __DEFAULT_EDGE_COLOR = new THREE.Color(0x000000);

      function __setHoverHighlight(name, on) {
        const root = name ? objectsByName.get(name) : null;
        if (!root) return;
        const mode = window.builderState?.mode || "IDLE";
        const isTargetMode = mode === "PICK_TARGET_OBJECT" || mode === "PICK_TARGET_ANCHOR";
        const hoverColor = isTargetMode ? __HOVER_EDGE_GREEN : __HOVER_EDGE_BLUE;
        const color = on ? hoverColor : __DEFAULT_EDGE_COLOR;
        root.traverse(o => {
          if (o.isMesh && o.userData.__edgeLines) {
            o.userData.__edgeLines.material.color.copy(color);
          }
        });
      }

      function __resolveComponentName(hit) {
        if (!hit || !hit.object) return null;
        let o = hit.object;
        while (o) {
          const cand = (o.userData && o.userData.componentName) ? o.userData.componentName : o.name;
          if (cand && objectsByName.has(cand)) return cand;
          o = o.parent;
        }
        return null;
      }

      // ── Collision-box resize handles ──────────────────────────────────────
      // Three draggable spheres on the selected box's +X / +Y / +Z faces
      // (colored to match the Size inputs: X red, Y green, Z blue). Dragging
      // resizes that dimension live — X/Y grow symmetrically about the box
      // center, Z from its base — and lands in meta.size like a typed edit.
      const __CBH_COLORS = { x: 0xe5534b, y: 0x34c759, z: 0x4f9cf9 };
      const __cbh = { name: null, group: null, handles: [], dragging: null };

      function __cbhHide() {
        if (__cbh.group) scene.remove(__cbh.group);
        __cbh.group = null; __cbh.handles = []; __cbh.name = null; __cbh.dragging = null;
      }

      function __cbhShow(name) {
        if (__cbh.dragging) return;                        // never rebuild mid-drag
        if (__cbh.name === name && __cbh.group) { __cbhUpdate(); return; }
        __cbhHide();
        const obj = objectsByName.get(name);
        const meta = window.builderState?.components?.[name];
        if (!obj || !meta || meta.type !== "collision_box") return;
        __cbh.name = name;
        const group = new THREE.Group();
        for (const ax of ["x", "y", "z"]) {
          const h = new THREE.Mesh(
            new THREE.SphereGeometry(9, 16, 16),
            new THREE.MeshBasicMaterial({
              color: __CBH_COLORS[ax], depthTest: false,
              transparent: true, opacity: 0.9,
            })
          );
          h.renderOrder = 995;
          h.userData.__cbhAxis = ax;
          group.add(h);
          __cbh.handles.push(h);
        }
        scene.add(group);
        __cbh.group = group;
        __cbhUpdate();
      }

      function __cbhFrame(obj) {
        const viz = obj.getObjectByName("__colBoxViz__");
        return viz ? viz.parent : obj;
      }

      function __cbhUpdate() {
        if (!__cbh.group || !__cbh.name) return;
        const obj = objectsByName.get(__cbh.name);
        const meta = window.builderState?.components?.[__cbh.name];
        if (!obj || !meta) { __cbhHide(); return; }
        const sz = Array.isArray(meta.size) ? meta.size : [100, 100, 100];
        const frame = __cbhFrame(obj);
        frame.updateMatrixWorld(true);
        const local = {
          x: new THREE.Vector3(sz[0] / 2, 0, sz[2] / 2),
          y: new THREE.Vector3(0, sz[1] / 2, sz[2] / 2),
          z: new THREE.Vector3(0, 0, sz[2]),
        };
        for (const h of __cbh.handles) {
          h.position.copy(frame.localToWorld(local[h.userData.__cbhAxis].clone()));
        }
      }

      // Param t on the axis line (origin + t·dir) closest to the pointer ray.
      function __cbhAxisT(origin, dir, ray) {
        const w0 = origin.clone().sub(ray.origin);
        const a = dir.dot(dir), b = dir.dot(ray.direction), c = ray.direction.dot(ray.direction);
        const d = dir.dot(w0), e2 = ray.direction.dot(w0);
        const denom = a * c - b * b;
        if (Math.abs(denom) < 1e-9) return 0;
        return (b * e2 - c * d) / denom;
      }

      renderer.domElement.addEventListener("pointerdown", (e) => {
        if (!__cbh.group || e.button !== 0) return;
        setPointerFromEvent(e);
        raycaster.setFromCamera(pointer, camera);
        const hits = raycaster.intersectObjects(__cbh.handles, false);
        if (!hits.length) return;
        e.stopImmediatePropagation();
        e.preventDefault();
        controls.enabled = false;
        __cbh.dragging = {
          axis: hits[0].object.userData.__cbhAxis,
          prevMeta: __deepClone(window.builderState.components[__cbh.name]),
        };
      }, true);

      renderer.domElement.addEventListener("pointermove", (e) => {
        if (!__cbh.dragging) return;
        e.stopImmediatePropagation();
        setPointerFromEvent(e);
        raycaster.setFromCamera(pointer, camera);
        const obj = objectsByName.get(__cbh.name);
        const meta = window.builderState?.components?.[__cbh.name];
        if (!obj || !meta) return;
        const frame = __cbhFrame(obj);
        frame.updateMatrixWorld(true);
        const ax = __cbh.dragging.axis;
        const dirLocal = ax === "x" ? new THREE.Vector3(1, 0, 0)
                       : ax === "y" ? new THREE.Vector3(0, 1, 0)
                       : new THREE.Vector3(0, 0, 1);
        const origin = frame.getWorldPosition(new THREE.Vector3());
        const dirWorld = dirLocal.transformDirection(frame.matrixWorld).normalize();
        const t = __cbhAxisT(origin, dirWorld, raycaster.ray);
        const sz = (Array.isArray(meta.size) ? meta.size : [100, 100, 100]).slice();
        if (ax === "x") sz[0] = Math.max(1, Math.round(Math.abs(t) * 2));
        else if (ax === "y") sz[1] = Math.max(1, Math.round(Math.abs(t) * 2));
        else sz[2] = Math.max(1, Math.round(t));
        meta.size = sz;
        try { __rebuildColBoxViz(__cbh.name); } catch (err) {}
        __cbhUpdate();
        try { if (window.__builderUpdateSizeRow) window.__builderUpdateSizeRow(__cbh.name); } catch (err) {}
      }, true);

      const __cbhEnd = (e) => {
        if (!__cbh.dragging) return;
        e.stopImmediatePropagation();
        controls.enabled = true;
        const prevMeta = __cbh.dragging.prevMeta;
        __cbh.dragging = null;
        try {
          const name = __cbh.name;
          const obj = objectsByName.get(name);
          const r = window.quaternionToRodriguesDeg && obj ? window.quaternionToRodriguesDeg(obj.quaternion) : [0, 0, 0];
          const pose = obj ? [obj.position.x, obj.position.y, obj.position.z, r[0] || 0, r[1] || 0, r[2] || 0] : null;
          __pushUndo({ kind: "transform", name, prevMeta, prevPose: pose, nextMeta: __deepClone(window.builderState.components[name]), nextPose: pose });
        } catch (err) {}
        try { if (window.__updateConfigPreview) window.__updateConfigPreview(); } catch (err) {}
      };
      renderer.domElement.addEventListener("pointerup", __cbhEnd, true);
      renderer.domElement.addEventListener("pointercancel", __cbhEnd, true);

      window.__colBoxHandlesShow = __cbhShow;
      window.__colBoxHandlesHide = __cbhHide;
      window.__colBoxHandlesUpdate = __cbhUpdate;

      // ── Ghost preview for anchor attachment ──────────────────────────────
      let __ghostObj = null;

      function __clearGhost() {
        if (__ghostObj) { scene.remove(__ghostObj); __ghostObj = null; }
      }

      function __showGhostPreview(parentAnchorName, parentSolidKey) {
        __clearGhost();
        const pending = window.builderState?.pending;
        if (!pending || window.builderState.mode !== "PICK_TARGET_ANCHOR") return;

        const childName = pending.name;
        const childObj = objectsByName.get(childName);
        const targetName = window.builderState.targetName;
        const parentObj = targetName ? objectsByName.get(targetName) : null;
        if (!childObj || !parentObj) return;

        try {
          // Compute snap position using the same math as __snapChildToParentAnchor
          const childSolid = pending.childSolid || null;
          const childAnchor = pending.sourceAnchor;
          const parentSolid = parentSolidKey || window.builderState.pending?.parentSolid || null;

          // Anchor lookup that survives multi-solid components: prefer the
          // requested solid, then flat anchors, then EVERY solid — some
          // items (tools, core, shakers) keep their anchors on sub-solids,
          // and missing them here is why the ghost never appeared for them.
          const __anchorFromAnySolid = (obj, want, preferredSolid) => {
            const ud = obj?.userData || {};
            const ab = (ud.anchorsBySolid && typeof ud.anchorsBySolid === "object") ? ud.anchorsBySolid : null;
            if (preferredSolid && ab && ab[preferredSolid] && ab[preferredSolid][want])
              return { arr: ab[preferredSolid][want], solidKey: preferredSolid };
            if (ud.anchors && ud.anchors[want]) return { arr: ud.anchors[want], solidKey: null };
            if (ab) {
              for (const [sk, d] of Object.entries(ab)) {
                if (d && d[want]) return { arr: d[want], solidKey: sk };
              }
            }
            return null;
          };
          // Solid-local anchor → component-root local frame (same math as
          // handleAnchorPick's __solidAnchorToRoot).
          const __toRoot = (obj, res) => {
            const p = new THREE.Vector3(res.arr[0], res.arr[1], res.arr[2]);
            const q = rodriguesDegToQuaternion(res.arr[3] || 0, res.arr[4] || 0, res.arr[5] || 0);
            const holder = res.solidKey && obj.getObjectByName ? obj.getObjectByName(String(res.solidKey)) : null;
            if (!holder || holder === obj) return { p, q };
            const pRoot = obj.worldToLocal(holder.localToWorld(p.clone()));
            const hW = new THREE.Quaternion(); holder.getWorldQuaternion(hW);
            const oW = new THREE.Quaternion(); obj.getWorldQuaternion(oW);
            return { p: pRoot, q: oW.clone().invert().multiply(hW.multiply(q)) };
          };

          const srcRes = __anchorFromAnySolid(childObj, childAnchor, childSolid);
          const dstRes = __anchorFromAnySolid(parentObj, parentAnchorName, parentSolid);
          if (!srcRes || !dstRes || srcRes.arr.length < 6 || dstRes.arr.length < 6) { __clearGhost(); return; }

          const { p: srcPL, q: srcQL } = __toRoot(childObj, srcRes);
          const { p: dstPL, q: dstQL } = __toRoot(parentObj, dstRes);

          const dstWorldPos = parentObj.localToWorld(dstPL.clone());
          const parentWorldQ = new THREE.Quaternion();
          parentObj.getWorldQuaternion(parentWorldQ);
          let dstWorldQ = parentWorldQ.clone().multiply(dstQL);
          // Mirror handleAnchorPick: a re-anchored item keeps its previous
          // rotation offset (offset[3..5]) — the ghost must preview it too,
          // or the translucent copy shows the unrotated pose.
          const __meta = window.builderState.components?.[childName];
          const __off = (__meta && __meta.attach && Array.isArray(__meta.attach.offset))
            ? __meta.attach.offset : null;
          if (__off && (__off[3] || __off[4] || __off[5])) {
            dstWorldQ = dstWorldQ.multiply(rodriguesDegToQuaternion(__off[3] || 0, __off[4] || 0, __off[5] || 0));
          }
          const newChildQ = dstWorldQ.clone().multiply(srcQL.clone().invert());
          const srcWorldOffset = srcPL.clone().applyQuaternion(newChildQ);
          const newChildPos = dstWorldPos.clone().sub(srcWorldOffset);

          // Ghost the WHOLE attached subtree, not just the moved item:
          // children (racks on adapters, tubes in racks, caps on tubes)
          // are separate scene objects positioned by attach metadata, so
          // apply the item's old→new delta transform to a clone of each.
          childObj.updateMatrixWorld(true);
          const newRoot = new THREE.Matrix4().compose(
            newChildPos, newChildQ, new THREE.Vector3(1, 1, 1));
          const delta = newRoot.clone().multiply(childObj.matrixWorld.clone().invert());

          const comps = window.builderState?.components || {};
          const subtree = [childName];
          const seen = new Set(subtree);
          for (let i = 0; i < subtree.length; i++) {
            for (const [n, m] of Object.entries(comps)) {
              if (!seen.has(n) && m?.attach?.parent_name === subtree[i]) {
                seen.add(n);
                subtree.push(n);
              }
            }
          }

          const group = new THREE.Group();
          for (const n of subtree) {
            const src = objectsByName.get(n);
            if (!src) continue;
            const g = src.clone(true);
            const drop = [];
            g.traverse(c => {
              if (c.isLine) { drop.push(c); return; }   // skip edge overlays
              if (c.isMesh && c.material) {
                c.material = c.material.clone();
                c.material.transparent = true;
                c.material.opacity = 0.3;
                c.material.depthWrite = false;
                c.material.color?.setHex(0x0a84ff);
              }
            });
            drop.forEach(c => c.parent && c.parent.remove(c));
            src.updateMatrixWorld(true);
            const m = delta.clone().multiply(src.matrixWorld);
            m.decompose(g.position, g.quaternion, g.scale);
            group.add(g);
          }
          group.renderOrder = 900;
          scene.add(group);
          __ghostObj = group;

        } catch (e) {
          __clearGhost();
        }
      }

      let __hoveredAnchorPick = null;
      let __hoverPickAt = 0;
      renderer.domElement.addEventListener("pointermove", (e) => {
        setPointerFromEvent(e);
        // Hover picking raycasts the full mesh set (1.4M triangles on a
        // big bench) — during a camera drag the cursor cue is useless
        // and this raycast was throttling the render loop to ~15 fps.
        // Skip while any button is held; rate-limit to ~20 Hz idle.
        if (e.buttons !== 0) return;
        const __now = performance.now();
        if (__now - __hoverPickAt < 50) return;
        __hoverPickAt = __now;
        const hit = pickFirstMesh();
        const isAnchorHit = hit?.object?.userData?.__isAnchorPick;
        renderer.domElement.style.cursor =
          isAnchorHit ? "pointer" : (hit && hit.object?.isMesh) ? "pointer" : "default";

        // Anchor hover effect
        if (wantAnchorHitZones()) {
          const newHover = isAnchorHit ? hit.object : null;
          if (newHover !== __hoveredAnchorPick) {
            // Unhover previous — shrink back, dim label
            if (__hoveredAnchorPick) {
              const d = __hoveredAnchorPick.userData.__dotMesh;
              const r = __hoveredAnchorPick.userData.__ringMesh;
              const l = __hoveredAnchorPick.userData.__labelSprite;
              if (d) { d.scale.set(1, 1, 1); d.material.opacity = 0.6; }
              if (r) { r.material.opacity = 0.0; }
              if (l && l.userData.__baseScale) { l.material.opacity = 0.9; l.scale.copy(l.userData.__baseScale); }
            }
            // Hover new — grow dot, glow ring, enlarge label
            if (newHover) {
              const d = newHover.userData.__dotMesh;
              const r = newHover.userData.__ringMesh;
              const l = newHover.userData.__labelSprite;
              if (d) { d.scale.set(2.5, 2.5, 2.5); d.material.opacity = 1.0; }
              if (r) { r.material.opacity = 0.4; }
              if (l && l.userData.__baseScale) { l.material.opacity = 1.0; l.scale.copy(l.userData.__baseScale).multiplyScalar(1.5); }
              if (window.builderState?.mode === "PICK_TARGET_ANCHOR") {
                __showGhostPreview(newHover.userData.anchorName, newHover.userData.solidKey || null);
              }
            } else {
              __clearGhost();
            }
            __hoveredAnchorPick = newHover;
          }
        } else if (__hoveredAnchorPick) {
          const d = __hoveredAnchorPick.userData.__dotMesh;
          const r = __hoveredAnchorPick.userData.__ringMesh;
          const l = __hoveredAnchorPick.userData.__labelSprite;
          if (d) { d.scale.set(1, 1, 1); d.material.opacity = 0.6; }
          if (r) { r.material.opacity = 0.0; }
          if (l && l.userData.__baseScale) { l.material.opacity = 0.9; l.scale.copy(l.userData.__baseScale); }
          __hoveredAnchorPick = null;
          __clearGhost();
        }

        // Hover highlight
        const name = (hit && hit.object?.isMesh && !isAnchorHit) ? __resolveComponentName(hit) : null;
        if (name !== __hoveredName) {
          if (__hoveredName) __setHoverHighlight(__hoveredName, false);
          __hoveredName = name;
          if (__hoveredName) __setHoverHighlight(__hoveredName, true);
        }
      });

      renderer.domElement.addEventListener("pointerleave", () => {
        if (__hoveredName) { __setHoverHighlight(__hoveredName, false); __hoveredName = null; }
      });

      function registerPickables(node) {
        node.traverse(o => {
          if (o.isMesh) pickableMeshes.add(o);
        });
      }


      function unregisterPickables(node) {
        node.traverse(o => {
          if (o.isMesh) pickableMeshes.delete(o);
        });
      }

      function disposeNode(node) {
        node.traverse(o => {
          if (!o.isMesh) return;
          if (o.geometry) { try { o.geometry.dispose(); } catch(e) {} }
          const mat = o.material;
          if (Array.isArray(mat)) {
            for (const m of mat) { if (m && m.dispose) { try { m.dispose(); } catch(e) {} } }
          } else if (mat && mat.dispose) {
            try { mat.dispose(); } catch(e) {}
          }
        });
      }

      // ---- Add edge overlay to all meshes (updated: uses userData) ----
      // source BufferGeometry -> EdgesGeometry (see comment in addEdgeOverlay)
      const __edgeGeoCache = new WeakMap();
      function addEdgeOverlay(node, edgeColor) {
        const _edgeColor = edgeColor || 0x555555;
        node.traverse(obj => {
          if (!obj.isMesh) return;

          // store original
          if (!obj.userData.__originalMat) {
            obj.userData.__originalMat = obj.material;
          }
          obj.material = obj.userData.__originalMat;

          // remove old if exists (geometry is cache-shared — never
          // disposed here; see __edgeGeoCache)
          if (obj.userData.__edgeLines) {
            obj.remove(obj.userData.__edgeLines);
            obj.userData.__edgeLines.material.dispose();
            obj.userData.__edgeLines = null;
          }

          // add new edge overlay. EdgesGeometry is the single most
          // expensive call in a project import, and instances of the
          // same component type SHARE their BufferGeometry (clone()
          // copies nodes, not buffers) — so cache the result per
          // source geometry: 168 tubes cost one computation, not 168.
          let edgesGeo = __edgeGeoCache.get(obj.geometry);
          if (!edgesGeo) {
            edgesGeo = new THREE.EdgesGeometry(obj.geometry, 75);
            __edgeGeoCache.set(obj.geometry, edgesGeo);
          }
          const edgesMat = new THREE.LineBasicMaterial({
            color: _edgeColor,
            toneMapped: false
          });
          const edgeLines = new THREE.LineSegments(edgesGeo, edgesMat);
          // Edge overlays are CHILDREN of their mesh, so the recursive
          // pointer-move raycast descends into them — and Line raycast
          // tests every segment. At bna_test scale that's millions of
          // segment checks per mouse move (the orbit/hover lag). They
          // are decoration, never pick targets: opt out entirely.
          edgeLines.raycast = () => {};
          edgeLines.renderOrder = 10;
          // Respect the current toggle so objects added while edges are
          // hidden don't pop back in with outlines.
          edgeLines.visible = _sbShowEdges;
          obj.add(edgeLines);
          obj.userData.__edgeLines = edgeLines;
        });
      }

      // Exposed for out-of-scope rebuilds (collision-box size edits).
      window.__addEdgeOverlay = addEdgeOverlay;

      // --- upsert pipeline ---
      function upsertObject(name, spec) {
        if (spec.delete) {
          const prev = objectsByName.get(name);
          if (prev) {
            if (activeAnchors &&
                (activeAnchors.obj === prev ||
                 (prev.isAncestorOf && prev.isAncestorOf(activeAnchors.obj)))) {
              clearAnchors();
            }
            prev.traverse(o => {
              if (o.isMesh) pickableMeshes.delete(o);
            });
            scene.remove(prev);
            objectsByName.delete(name);
            try { delete window.builderState.specs[name]; } catch (e) {}
          }
          return;
        }

        // Track last known spec so Undo/Redo can replay exact state.
        try {
          const prev = window.builderState.specs[name] || {};
          // merge shallow (pose/anchors/meshes etc are replaced atomically)
          window.builderState.specs[name] = __deepClone(Object.assign({}, prev, spec));
        } catch (e) {}

        let root = objectsByName.get(name);
        if (!root) {
          root = new THREE.Group();
          root.name = name;
          root.userData = root.userData || {};
          root.userData.componentName = name;

          scene.add(root);
          objectsByName.set(name, root);
        }

        // Multi-mesh support (simulation-style assemblies)
        // spec.meshes: [{ meshUrl, pose:[x,y,z,a,b,c], solidName }]
        if (Array.isArray(spec.meshes) && spec.meshes.length && spec.__tweenPoses) {
          // Pose-only update: animate the EXISTING solid holders to the
          // new poses (position lerp + quaternion slerp, eased) instead
          // of rebuilding meshes — used by the recipes panel so the
          // robot visibly moves to a solved pose. Purely visual; scene
          // state untouched.
          const dur = 650;
          for (const m of spec.meshes) {
            const holder = root.children.find(c => c.name === String(m.solidName));
            if (!holder || !Array.isArray(m.pose) || m.pose.length !== 6) continue;
            const p0 = holder.position.clone();
            const q0 = holder.quaternion.clone();
            const [x, y, z, rx, ry, rz] = m.pose;
            const p1 = new THREE.Vector3(x, y, z);
            const q1 = rodriguesDegToQuaternion(rx, ry, rz);
            holder.userData.__poseAnim = (holder.userData.__poseAnim || 0) + 1;
            const myId = holder.userData.__poseAnim;
            const t0 = performance.now();
            (function anim() {
              if (holder.userData.__poseAnim !== myId) return; // superseded
              const t = Math.min(1, (performance.now() - t0) / dur);
              const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
              holder.position.lerpVectors(p0, p1, e);
              holder.quaternion.slerpQuaternions(q0, q1, e);
              if (t < 1) requestAnimationFrame(anim);
            })();
          }
          root.userData.meshesSig = null; // next real load must rebuild
          return;
        }
        if (Array.isArray(spec.meshes) && spec.meshes.length) {
          const sig = JSON.stringify(spec.meshes.map(m => [m.meshUrl, ...(m.pose||[]), m.solidName||"", JSON.stringify(m.collisionLocal||[])]));
          if (root.userData.meshesSig !== sig) {
            root.userData.meshesSig = sig;
            root.userData.meshUrl = null;

            // clear old children
            while (root.children.length) {
              root.children[0].traverse(o => { if (o.isMesh) pickableMeshes.delete(o); });
              root.remove(root.children[0]);
            }

            for (const m of spec.meshes) {
              if (!m || !m.meshUrl) continue;
              const holder = new THREE.Group();
              holder.name = m.solidName ? String(m.solidName) : "solid";
              if (Array.isArray(m.pose) && m.pose.length === 6) {
                const [x,y,z,rx,ry,rz] = m.pose;
                holder.position.set(x,y,z);
                holder.quaternion.copy(rodriguesDegToQuaternion(rx,ry,rz));
              }

              // Collision boxes for this solid (local to the holder)
              const __col = new THREE.Group();
              __col.name = "__collision__";
              __col.visible = showCollisionBoxes;
              __col.userData = __col.userData || {};
              __col.userData.__isCollisionGroup = true;
              holder.add(__col);
              fillCollisionGroup(__col, m.collisionLocal || [], m.boxForGrip);

              root.add(holder);

              loadGltfCached(
                versioned(m.meshUrl),
                (gltf) => {
                  gltf.scene.traverse(o => {
                    if (o.isMesh) {
                      const mats = Array.isArray(o.material) ? o.material : [o.material];
                      mats.forEach(mat => { if (mat && "side" in mat) mat.side = THREE.DoubleSide; });
                    }
                  });
                  addEdgeOverlay(gltf.scene);
                  // Use root.name (not closure `name`) so renames during async load are picked up
                  try { const cn = root.name; gltf.scene.traverse(o=>{ if(!o.userData) o.userData={}; o.userData.componentName = cn; }); } catch(e) {}
                  // collision_box: keep GLB at original size (small red cube as handle),
                  // add a separate transparent wireframe box sized to match the collision
                  if (spec.type === "collision_box") {
                    const comp = window.builderState.components[root.name];
                    const sz = (comp && comp.size) || [100, 100, 100];
                    // Add procedural transparent box with edge overlay
                    const boxGeom = new THREE.BoxGeometry(sz[0], sz[1], sz[2]);
                    const boxMat = new THREE.MeshBasicMaterial({
                      color: 0xaaaaaa, transparent: true, opacity: 0.06,
                      depthWrite: false, side: THREE.DoubleSide
                    });
                    const boxMesh = new THREE.Mesh(boxGeom, boxMat);
                    boxMesh.position.set(0, 0, sz[2] / 2);
                    const vizGroup = new THREE.Group();
                    vizGroup.name = "__colBoxViz__";
                    vizGroup.userData.__colBoxViz = true;
                    vizGroup.add(boxMesh);
                    addEdgeOverlay(vizGroup);
                    holder.add(vizGroup);
                  }
                  holder.add(gltf.scene);
                  registerPickables(gltf.scene);
                },
                undefined,
                () => { /* ignore */ }
              );
            }
          }
        }

        // Single mesh support
        if (!spec.meshes && spec.meshUrl) {
          if (!root.userData.meshUrl || root.userData.meshUrl !== spec.meshUrl) {
            root.userData.meshUrl = spec.meshUrl;
            root.userData.meshesSig = null;

            // clear old children
            while (root.children.length) {
              root.children[0].traverse(o => {
                if (o.isMesh) pickableMeshes.delete(o);
              });
              root.remove(root.children[0]);
            }

            loadGltfCached(
              versioned(spec.meshUrl),
              (gltf) => {
                gltf.scene.traverse(o => {
                  if (o.isMesh) {
                    const mats = Array.isArray(o.material) ? o.material : [o.material];
                    mats.forEach(m => { if (m && "side" in m) m.side = THREE.DoubleSide; });
                  }
                });

                addEdgeOverlay(gltf.scene);

                // Tag all child nodes so click-picking can resolve back to the component root.
                // Use root.name (not closure `name`) so renames during async load are picked up.
                try {
                  const cn = root.name;
                  gltf.scene.traverse(o => {
                    if (!o.userData) o.userData = {};
                    o.userData.componentName = cn;
                  });
                } catch (e) {}

                root.add(gltf.scene);

                // collision_box: keep GLB at original size, add procedural transparent box
                if (spec.type === "collision_box") {
                  const comp = window.builderState.components[root.name];
                  const sz = (comp && comp.size) || [100, 100, 100];
                  const boxGeom = new THREE.BoxGeometry(sz[0], sz[1], sz[2]);
                  const boxMat = new THREE.MeshBasicMaterial({
                    color: 0xaaaaaa, transparent: true, opacity: 0.06,
                    depthWrite: false, side: THREE.DoubleSide
                  });
                  const boxMesh = new THREE.Mesh(boxGeom, boxMat);
                  boxMesh.position.set(0, 0, sz[2] / 2);
                  const vizGroup = new THREE.Group();
                  vizGroup.name = "__colBoxViz__";
                  vizGroup.userData.__colBoxViz = true;
                  vizGroup.add(boxMesh);
                  addEdgeOverlay(vizGroup);
                  root.add(vizGroup);
                }

                // Collision boxes (single-solid components)
                if (Array.isArray(spec.collisionLocal)) {
                  let __col = root.getObjectByName("__collision__");
                  if (!__col) {
                    __col = new THREE.Group();
                    __col.name = "__collision__";
                    __col.visible = showCollisionBoxes;
                    __col.userData = __col.userData || {};
                    __col.userData.__isCollisionGroup = true;
                    root.add(__col);
                  }
                  fillCollisionGroup(__col, spec.collisionLocal, spec.boxForGrip);
                }

                registerPickables(gltf.scene);
              },
              undefined,
              () => { /* ignore errors for now */ }
            );
          }
        }

        // Also support optional base64 GLB "mesh" (not used by current Display)
        if (spec.mesh) {
          const buf = base64ToArrayBuffer(spec.mesh);
          gltfLoader.parse(
            buf, "",
            (gltf) => {
              while (root.children.length) {
                root.children[0].traverse(o => { if (o.isMesh) pickableMeshes.delete(o); });
                root.remove(root.children[0]);
              }

              gltf.scene.traverse(o => {
                if (o.isMesh) {
                  const mats = Array.isArray(o.material) ? o.material : [o.material];
                  mats.forEach(m => { if (m && "side" in m) m.side = THREE.DoubleSide; });
                }
              });

              addEdgeOverlay(gltf.scene);

              // Tag all child nodes so click-picking can resolve back to the component root.
              // Use root.name (not closure `name`) so renames during async load are picked up.
              try {
                const cn = root.name;
                gltf.scene.traverse(o => {
                  if (!o.userData) o.userData = {};
                  o.userData.componentName = cn;
                });
              } catch (e) {}

              root.add(gltf.scene);
              registerPickables(gltf.scene);
            },
            () => { /* ignore parse errors */ }
          );
        }

        // No-mesh objects (e.g. standalone collision boxes): add collision group directly
        if (!spec.meshUrl && !spec.meshes && !spec.mesh && Array.isArray(spec.collisionLocal) && spec.collisionLocal.length) {
          let __col = root.getObjectByName("__collision__");
          if (!__col) {
            __col = new THREE.Group();
            __col.name = "__collision__";
            __col.visible = true;  // always visible for mesh-less objects
            __col.userData = __col.userData || {};
            __col.userData.__isCollisionGroup = true;
            root.add(__col);
          }
          fillCollisionGroup(__col, spec.collisionLocal, spec.boxForGrip);
          // Make the collision meshes pickable so we can click/select the object
          __col.traverse(o => { if (o.isMesh) { o.userData.componentName = root.name; pickableMeshes.add(o); } });
        }

        if (Array.isArray(spec.pose) && spec.pose.length === 6) {
          // Skip echo pose updates for objects recently positioned by the builder
          // (e.g., pattern-fill cap clones). The server echoes the spawn's default
          // pose which would overwrite the correct snapped position.
          const __guard = root.userData?.__builderPoseGuard;
          if (!__guard || performance.now() - __guard > 3000) {
            const [x, y, z, rx, ry, rz] = spec.pose;
            root.position.set(x, y, z);
            root.quaternion.copy(rodriguesDegToQuaternion(rx, ry, rz));
          }
        }

        if (typeof spec.visible === "boolean") {
          // an eye-hidden object stays hidden through server echoes
          root.visible = spec.visible && !__hiddenObjects.has(name);
        }

        if (spec.anchors && typeof spec.anchors === "object") {
          root.userData.anchors = spec.anchors;
        }
	        // NEW: preserve multi-solid anchors (required for plates and many assemblies)
	        if (spec.anchorsBySolid && typeof spec.anchorsBySolid === "object") {
	          root.userData.anchorsBySolid = spec.anchorsBySolid;
	        }

        // NEW: preserve builder attach metadata from server/config
        if (spec.builder && spec.builder.attach && typeof spec.builder.attach === "object") {
          root.userData.builderAttach = spec.builder.attach;
          try {
            try {
              // Keep builder state synced when objects are injected/updated from upstream.
              // Never clobber an existing type/options; only fill missing pieces.
              const __cur = window.builderState.components[name] || {};
              const __t = __cur.type || root.userData.typeName || spec.typeName;
              window.builderState.components[name] = Object.assign({}, __cur, (__t ? { type: __t } : {}), { attach: spec.builder.attach });
            } catch (e) {}
          } catch (e) {}
        }

        if (spec.componentName) {
          root.userData.componentName = spec.componentName;
        }
        if (spec.solidName) {
          root.userData.solidName = spec.solidName;
        }
        // NEW: store type/typeName so the UI can display it
        if (spec.typeName) {
          root.userData.typeName = spec.typeName;
        }
        if (spec.type) {
          root.userData.typeName = spec.type;
        }
      }

      // --- Socket.IO hookup ---
      const socket = io({
        path: "/scene-builder/socket.io/",
        transports: ["websocket"],
        forceNew: true,
        timeout: 10000
      });

      // Expose socket for actions outside boot()
      window.socket = socket;

      socket.on("scene_update", (payload) => {
        if (!payload || typeof payload !== "object") return;
        for (const [n, s] of Object.entries(payload)) {
          // Per-entry isolation: one bad entry must not strand the
          // rest of the replay (unrendered objects, unregistered
          // components — the "no core object" class of bug).
          try {
            upsertObject(n, s || {});
          } catch (e) {
            console.error("scene_update: upsert failed for", n, e);
            continue;
          }
          // Sync builder state from server so object list + config work after refresh
          if (s && !s.delete && s.type) {
            if (!window.builderState.components[n]) {
              window.builderState.components[n] = { type: s.type };
              if (!window.builderState.placedOrder.includes(n)) {
                window.builderState.placedOrder.push(n);
              }
              if (s.type === "fixture_plate" && !window.builderState.lastFixturePlate) {
                window.builderState.lastFixturePlate = n;
              }
            }
          } else if (s && s.delete) {
            delete window.builderState.components[n];
            window.builderState.placedOrder = window.builderState.placedOrder.filter(x => x !== n);
          }
        }
      window.upsertObject = upsertObject;
      window.__hasObject = (n) => objectsByName.has(n);
      // TCP drag support: viewer internals + a fast world-pose setter
      // (drag solves return WORLD solid poses; holders are core-local).
      window.__three = { scene, camera, renderer, controls, THREE };
      window.__setSolidPosesWorld = (name, solids) => {
        const root = objectsByName.get(name);
        if (!root) return;
        root.updateMatrixWorld(true);
        const inv = root.matrixWorld.clone().invert();
        for (const [sn, p] of Object.entries(solids || {})) {
          const holder = root.children.find(c => c.name === sn);
          if (!holder || !Array.isArray(p) || p.length !== 6) continue;
          const M = new THREE.Matrix4().compose(
            new THREE.Vector3(p[0], p[1], p[2]),
            rodriguesDegToQuaternion(p[3], p[4], p[5]),
            new THREE.Vector3(1, 1, 1));
          const L = inv.clone().multiply(M);
          L.decompose(holder.position, holder.quaternion, holder.scale);
          holder.userData.__poseAnim = (holder.userData.__poseAnim || 0) + 1;
        }
        root.userData.meshesSig = null;
      };
        try { if (window.updateObjectList) window.updateObjectList(); } catch(e) {}
        try { if (window.__updateConfigPreview) window.__updateConfigPreview(); } catch(e) {}
        markDirty();
      });

// =========================
// Builder (insert + snap + save config)
// =========================
const builderState = {
  next: { fixture_plate: 1, sbs_adapter: 1 },
  lastFixturePlate: null,
  placedOrder: [],
  selectedName: null,
  mode: "IDLE", // IDLE | PICK_TARGET_OBJECT | PICK_TARGET_ANCHOR
  pending: null, // { name, type, sourceAnchor, childSolid }
  targetName: null,
  components: {}, // name -> {type, attach?, offset?, __file?}
  panMode: false,

  // --- Multi-file config ---
  // The scene is one merged object (so cross-file references resolve),
  // but each component is tagged with the file it belongs to via
  // ``components[name].__file``. ``files`` is the ordered list of
  // output files (merge order = array order, like the launcher's
  // ``scene: [base.j2, layout.j2]``). ``activeFile`` is the checked
  // target — new components get tagged with it. Defaults to a single
  // file; use "+ add file" to split the scene.
  files: ["scene.j2"],
  activeFile: "scene.j2",

  // --- Undo / Redo ---
  // We track atomic actions (create, attach, pattern-batch) so:
  // - undo attach => object remains, becomes unanchored
  // - undo again => deletes created object (if it was created last)
  // - undo pattern => deletes all created instances from that pattern run
  undoStack: [],
  redoStack: [],
  // Keep last known scene spec for each object (what we send to upsertObject)
  specs: {}
  ,suspendUndo: false
};
window.builderState = builderState;

// Simple deep-clone for small JSON-like objects
function __deepClone(obj) {
  try { return JSON.parse(JSON.stringify(obj)); } catch (e) { return obj; }
}

// -----------------
// Undo / Redo stack
// -----------------
function __pushUndo(action) {
  try {
    if (window.builderState.suspendUndo) return;
    if (!action) return;
    window.builderState.undoStack.push(action);
    window.builderState.redoStack.length = 0; // clear redo on new action
  } catch (e) {}
}

// Push an undo action even if suspendUndo is currently true (used for atomic operations like pattern).
function __pushUndoForce(action) {
  try {
    if (!action) return;
    const st = window.builderState;
    st.undoStack.push(action);
    st.redoStack.length = 0;
  } catch (e) {}
}

// Merge a just-created object + its first attach into a single atomic action.
// Desired behavior:
// - Spawn -> Joint -> Undo  => deletes the object (not just un-attach)
// - Redo restores it with the joint.
function __maybeMergeCreateAttach(attachAction) {
  try {
    const st = window.builderState;
    if (!attachAction || attachAction.kind !== "attach") return attachAction;
    const name = attachAction.name;
    const prev = st.undoStack.length ? st.undoStack[st.undoStack.length - 1] : null;
    if (!prev || prev.kind !== "create") return attachAction;
    if (!prev.names || prev.names.length !== 1 || prev.names[0] !== name) return attachAction;

    // Pop the create action and replace with a combined action.
    st.undoStack.pop();
    return {
      kind: "create_attach",
      name,
      // creation payload
      specs: prev.specs || {},
      metas: prev.metas || {},
      // attach delta payload
      prevMeta: attachAction.prevMeta,
      prevPose: attachAction.prevPose,
      nextMeta: attachAction.nextMeta,
      nextPose: attachAction.nextPose
    };
  } catch (e) {
    return attachAction;
  }
}

function __applyUpsert(name, spec) {
  try { window.socket?.emit?.("upstream_update", { [name]: spec }); } catch (e) {}
  try { window.upsertObject?.(name, spec); } catch (e) {}
  try { if (window.updateObjectList) window.updateObjectList(); } catch (e) {}
}

function __applyDelete(name) {
  try { window.socket?.emit?.("upstream_update", { [name]: { delete: true } }); } catch (e) {}
  try { window.upsertObject?.(name, { delete: true }); } catch (e) {}
  try { if (window.__updateConfigPreview) window.__updateConfigPreview(); } catch (e) {}
  try { if (window.updateObjectList) window.updateObjectList(); } catch (e) {}
}


function __undo() {
  const st = window.builderState;

  // Pattern undo priority (robust):
  // If the top undo action would delete the seed (create / create_attach), but a pattern action
  // exists *anywhere below it* that was created using that seed, undo the pattern first.
  // This fixes cases where internal fill helpers accidentally leave a seed-create action above
  // the pattern batch, causing Ctrl+Z to delete the seed instead of undoing the fill.
  let act = st.undoStack.pop();
  if (!act) { showToast("Nothing to undo"); return; }

  try {
    const seedName =
      (act.kind === "create_attach" && act.name) ? act.name :
      (act.kind === "create" && Array.isArray(act.names) && act.names.length === 1) ? act.names[0] :
      null;

    if (seedName) {
      // Find the most recent pattern action in the remaining stack that references this seed.
      let idx = -1;
      for (let i = st.undoStack.length - 1; i >= 0; i--) {
        const a = st.undoStack[i];
        if (!a || a.kind !== "pattern") continue;
        const match = (a.seedName === seedName) ||
                      (Array.isArray(a.seedNames) && a.seedNames.includes(seedName));
        if (match) { idx = i; break; }
      }

      if (idx >= 0) {
        // Put the seed action back on top so it can be undone on the *next* undo,
        // and undo the pattern batch now.
        st.undoStack.push(act);
        act = st.undoStack.splice(idx, 1)[0];
      }
    }
  } catch (e) {}

  st.redoStack.push(act);

  // Helper: restore pose/meta for an existing object
  function __restorePoseMeta(name, meta, pose) {
    if (meta) st.components[name] = __deepClone(meta);
    else delete st.components[name];

    const spec = __deepClone(st.specs[name] || {});
    if (pose) spec.pose = __deepClone(pose);
    if (meta && meta.attach) spec.builder = { attach: __deepClone(meta.attach) };
    else spec.builder = { attach: null };
    __applyUpsert(name, spec);

    // Collision boxes render a procedural wireframe sized from meta.size —
    // rebuild it so undo/redo of a size edit shows the right box.
    if (meta && meta.type === "collision_box") {
      try { __rebuildColBoxViz(name); } catch (e) {}
    }
  }

  // Move (re-attach) should revert, not delete.
  if (act.kind === "attach" && act.op === "move") {
    __restorePoseMeta(act.name, act.prevMeta, act.prevPose);
    clearAnchors(); setSelected(null);
    showToast(`Undid move: ${act.name}`);
    return;
  }

  // Anchoring (attach) should simply delete the anchored object.
  if (act.kind === "attach") {
    const name = act.name;
    delete st.components[name];
    delete st.specs[name];
    __applyDelete(name);
    clearAnchors(); setSelected(null);
    showToast(`Undid anchor (deleted): ${name}`);
    return;
  }

  // A merged create+attach is always undone by deleting the object.
  if (act.kind === "create_attach") {
    const name = act.name;
    delete st.components[name];
    delete st.specs[name];
    __applyDelete(name);
    clearAnchors(); setSelected(null);
    showToast(`Undid joint (deleted): ${name}`);
    return;
  }

  // Transform (move/rotate/flip) should revert pose+meta.
  if (act.kind === "transform") {
    __restorePoseMeta(act.name, act.prevMeta, act.prevPose);
    try { __propagateAnchoredSubtree(act.name); } catch(e) {}
    clearAnchors(); setSelected(null);
    showToast(`Undid transform: ${act.name}`);
    return;
  }

  if (act.kind === "create") {
    for (const n of (act.names || [])) {
      delete st.components[n];
      delete st.specs[n];
      __applyDelete(n);
    }
    clearAnchors(); setSelected(null);
    showToast(`Undid create (${(act.names||[]).length})`);
    return;
  }


if (act.kind === "pattern") {
  // Prefer deleting everything created in this pattern batch (more reliable than name heuristics).
  const toDelete = new Set();
  try {
    if (act.batchId) {
      for (const [n, m] of Object.entries(st.components || {})) {
        if (m && m.__patternBatch === act.batchId) toDelete.add(n);
      }
    }
  } catch (e) {}
  for (const n of (act.names || [])) if (n) toDelete.add(n);

  // Never delete the seed if provided.
  if (act.seedName) toDelete.delete(act.seedName);

  // Delete all created instances.
  const __names = Array.from(toDelete);
  for (const n of __names) {
    delete st.components[n];
    delete st.specs[n];
    __applyDelete(n);
  }
  clearAnchors(); setSelected(null);
  showToast(`Undid pattern (${__names.length})`);
  return;
}


  if (act.kind === "delete") {
    // Undo delete => restore all deleted objects
    for (const n of (act.names || [])) {
      if (act.metas && act.metas[n]) st.components[n] = __deepClone(act.metas[n]);
      const spec = act.specs && act.specs[n] ? __deepClone(act.specs[n]) : null;
      if (spec) {
        st.specs[n] = __deepClone(spec);
        __applyUpsert(n, spec);
      }
    }
    clearAnchors(); setSelected(null);
    showToast(`Undid delete (${(act.names||[]).length})`);
    return;
  }
}

function __redo() {
  const st = window.builderState;
  const act = st.redoStack.pop();
  if (!act) { showToast("Nothing to redo"); return; }
  st.undoStack.push(act);

  function __applyPoseMeta(name, meta, pose) {
    if (meta) st.components[name] = __deepClone(meta);
    const spec = __deepClone(st.specs[name] || {});
    if (pose) spec.pose = __deepClone(pose);
    if (meta && meta.attach) spec.builder = { attach: __deepClone(meta.attach) };
    __applyUpsert(name, spec);
  }

  if (act.kind === "attach" && act.op === "move") {
    __applyPoseMeta(act.name, act.nextMeta, act.nextPose);
    clearAnchors(); setSelected(null);
    showToast(`Redid move: ${act.name}`);
    return;
  }

  if (act.kind === "attach") {
    // Redo anchor: recreate the object and apply the joint if we captured specs/metas
    const name = act.name;
    if (act.spec0) {
      st.specs[name] = __deepClone(act.spec0);
      __applyUpsert(name, __deepClone(act.spec0));
    }
    if (act.nextMeta) st.components[name] = __deepClone(act.nextMeta);
    __applyPoseMeta(name, act.nextMeta, act.nextPose);
    clearAnchors(); setSelected(null);
    showToast(`Redid anchor: ${name}`);
    return;
  }

  if (act.kind === "create_attach") {
    const name = act.name;
    const spec0 = (act.specs && act.specs[name]) ? __deepClone(act.specs[name]) : null;
    const meta0 = (act.metas && act.metas[name]) ? __deepClone(act.metas[name]) : null;
    if (meta0) st.components[name] = meta0;
    if (spec0) {
      st.specs[name] = __deepClone(spec0);
      __applyUpsert(name, spec0);
    }
    // Re-apply attach
    if (act.nextMeta) st.components[name] = __deepClone(act.nextMeta);
    const spec = __deepClone(st.specs[name] || {});
    if (act.nextPose) spec.pose = __deepClone(act.nextPose);
    if (act.nextMeta && act.nextMeta.attach) spec.builder = { attach: __deepClone(act.nextMeta.attach) };
    __applyUpsert(name, spec);
    clearAnchors(); setSelected(null);
    showToast(`Redid joint: ${name}`);
    return;
  }

  if (act.kind === "transform") {
    __applyPoseMeta(act.name, act.nextMeta, act.nextPose);
    try { __propagateAnchoredSubtree(act.name); } catch(e) {}
    clearAnchors(); setSelected(null);
    showToast(`Redid transform: ${act.name}`);
    return;
  }

  if (act.kind === "create") {
    for (const n of (act.names || [])) {
      if (act.metas && act.metas[n]) st.components[n] = __deepClone(act.metas[n]);
      const spec = act.specs && act.specs[n] ? __deepClone(act.specs[n]) : __deepClone(st.specs[n] || {});
      if (spec) {
        st.specs[n] = __deepClone(spec);
        __applyUpsert(n, spec);
      }
    }
    clearAnchors(); setSelected(null);
    showToast(`Redid create (${(act.names||[]).length})`);
    return;
  }


if (act.kind === "pattern") {
  // Recreate all instances recorded for this pattern batch.
  const __names = (act.names || []).filter(n => n && (!act.seedName || n !== act.seedName));
  for (const n of __names) {
    if (act.metas && act.metas[n]) st.components[n] = __deepClone(act.metas[n]);
    const spec = act.specs && act.specs[n] ? __deepClone(act.specs[n]) : null;
    if (spec) {
      st.specs[n] = __deepClone(spec);
      __applyUpsert(n, spec);
    }
  }
  clearAnchors(); setSelected(null);
  showToast(`Redid pattern (${__names.length})`);
  return;
}

  if (act.kind === "delete") {
    // Redo delete => delete them again
    for (const n of (act.names || [])) {
      delete st.components[n];
      delete st.specs[n];
      __applyDelete(n);
    }
    clearAnchors(); setSelected(null);
    showToast(`Redid delete (${(act.names||[]).length})`);
    return;
  }
}

window.__undo = __undo;
window.__redo = __redo;



function showToast(msg, type="") {
  const area = document.getElementById("toastArea");
  if (area) {
    const t = document.createElement("div");
    t.className = "toast" + (type ? " " + type : "");
    t.textContent = msg;
    area.appendChild(t);
    setTimeout(() => t.remove(), 2500);
    return;
  }
  // Fallback: use legacy builderToast
  let t = document.getElementById("builderToast");
  if (!t) {
    t = document.createElement("div");
    t.id = "builderToast";
    t.style.cssText = "position:fixed;top:12px;left:50%;transform:translateX(-50%);background:#111;color:#fff;padding:10px 14px;font-family:system-ui;font-size:14px;border-radius:10px;box-shadow:0 8px 20px rgba(0,0,0,0.2);z-index:9999;pointer-events:none;";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.display = "block";
  clearTimeout(t.__hide);
  t.__hide = setTimeout(() => { t.style.display = "none"; }, 2200);
}

// Expose for any non-module callbacks / inline handlers.
window.showToast = showToast;
function showBanner(text, onCancel) {
  let b = document.getElementById("builderBanner");
  if (!b) {
    b = document.createElement("div");
    b.id = "builderBanner";
    b.style.cssText = "position:absolute;bottom:18px;left:50%;transform:translateX(-50%);background:rgba(10,14,24,0.85);color:#e6edf3;padding:10px 14px;border-radius:10px;border:1px solid rgba(255,255,255,0.12);backdrop-filter:blur(6px);font-size:13px;z-index:200;display:flex;align-items:center;gap:10px;white-space:nowrap;";
    const msg = document.createElement("div");
    msg.id = "builderBannerMsg";
    b.appendChild(msg);
    const cancel = document.createElement("button");
    cancel.textContent = "Cancel";
    cancel.className = "btn btn-sm btn-ghost";
    cancel.onclick = () => { if (onCancel) onCancel(); };
    b.appendChild(cancel);
    const viewerEl = document.getElementById("viewerArea");
    (viewerEl || document.body).appendChild(b);
  }
  const msgEl = document.getElementById("builderBannerMsg");
  if (msgEl) msgEl.textContent = text;
  const cancelBtn = b.querySelector("button");
  if (cancelBtn) cancelBtn.onclick = () => { if (onCancel) onCancel(); };
  b.style.display = "flex";
}
function hideBanner() {
  const b = document.getElementById("builderBanner");
  if (b) b.style.display = "none";
}

window.showBanner = showBanner;
window.hideBanner = hideBanner;


function ensureBuilderBar() {
  // Wire up static HTML sidebar controls (defined in index.html)
  const plus = document.getElementById("btnInsert");
  if (plus) plus.addEventListener("click", () => openInsertMenu());

  const save = document.getElementById("btnSave");
  if (save) save.addEventListener("click", () => saveConfig());

  const undoB = document.getElementById("btnUndo");
  if (undoB) undoB.addEventListener("click", () => { try { window.__undo?.(); } catch(e) {} });

  const redoB = document.getElementById("btnRedo");
  if (redoB) redoB.addEventListener("click", () => { try { window.__redo?.(); } catch(e) {} });

  const newBtn = document.getElementById("btnNew");
  if (newBtn) newBtn.addEventListener("click", async () => {
    // Show project path modal
    const path = await _showNewSceneModal();
    if (path === null) return; // cancelled

    // Clear server scene state. Await the HTTP reset so world_state is
    // definitely cleared before we reload — a fire-and-forget socket
    // emit can be dropped when the page tears down, leaving the old
    // scene to come back on reconnect.
    try { await fetch(SB_API + "/reset", { method: "POST" }); } catch(e) {}
    try { window.socket?.emit?.("reset_scene"); } catch(e) {}
    // New Scene = the explicit fresh start — drop persisted builder
    // state (file slots, tags, hidden set) for every project.
    try {
      for (const k of Object.keys(localStorage)) {
        if (k.startsWith("sb_persist::")) localStorage.removeItem(k);
      }
    } catch(e) {}

    // Set project path (can be empty for no project)
    try {
      await fetch(SB_API + "/set_project", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: path }),
      });
    } catch(e) {}

    setTimeout(() => window.location.reload(), 200);
  });

  // New Scene modal — styled like Parameters modal
  function _showNewSceneModal() {
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      // ``modal-overlay`` → covers the navbar (see vendor/nav.css).
      overlay.className = "modal-overlay";
      overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:50000;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);padding:20px;";
      overlay.innerHTML = `
        <div style="width:min(480px,100%);background:var(--surface);border-radius:18px;box-shadow:var(--shadow-lg);overflow:hidden;animation:confirmIn 0.25s cubic-bezier(0.2,0.9,0.3,1) forwards;">
          <div style="padding:16px 20px;border-bottom:1px solid var(--border2);display:flex;align-items:center;">
            <h3 style="font-size:16px;font-weight:600;letter-spacing:-0.2px;">New Scene</h3>
            <div class="spacer"></div>
            <button class="btn btn-ghost btn-sm btn-icon" id="nsClose" title="Close"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
          </div>
          <div style="padding:20px;display:flex;flex-direction:column;gap:14px;">
            <div style="font-size:13px;color:var(--muted);line-height:1.5;">Start a new scene. Set a project path to include custom components and CAD files from that project.</div>
            <div style="display:flex;flex-direction:column;gap:5px;">
              <label style="font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;">Project Path <span style="font-weight:400;text-transform:none;letter-spacing:0;">(optional)</span></label>
              <input class="input" id="newScenePath" type="text" placeholder="/home/dorna/.../projects/my_project"/>
              <div style="font-size:12px;color:var(--muted);margin-top:2px;">Leave empty for library components only.</div>
            </div>
          </div>
          <div style="padding:14px 20px;border-top:1px solid var(--border2);display:flex;gap:8px;justify-content:flex-end;">
            <button class="btn" id="nsCancel">Cancel</button>
            <button class="btn btn-primary" id="nsStart">Start</button>
          </div>
        </div>`;
      document.body.appendChild(overlay);

      const input = overlay.querySelector("#newScenePath");
      const cancelBtn = overlay.querySelector("#nsCancel");
      const closeBtn = overlay.querySelector("#nsClose");
      const startBtn = overlay.querySelector("#nsStart");

      // Pre-fill with current project path
      try {
        fetch(SB_API + "/set_project").then(r => r.json()).then(j => {
          if (j.path) input.value = j.path;
        }).catch(() => {});
      } catch(e) {}

      setTimeout(() => input.focus(), 100);

      function cleanup(result) {
        overlay.remove();
        resolve(result);
      }

      cancelBtn.addEventListener("click", () => cleanup(null));
      closeBtn.addEventListener("click", () => cleanup(null));
      startBtn.addEventListener("click", () => cleanup(input.value.trim()));
      overlay.addEventListener("click", (e) => { if (e.target === overlay) cleanup(null); });
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); cleanup(input.value.trim()); } });
      document.addEventListener("keydown", function onKey(e) {
        if (e.key === "Escape") { document.removeEventListener("keydown", onKey); cleanup(null); }
      });
    });
  }

  const gridBtn = document.getElementById("btnGrid");
  if (gridBtn) {
    gridBtn.addEventListener("click", () => {
      try { window.__sbToggleGrid?.(); } catch(e) {}
    });
  }

  let collisionActive = false;
  const collisionBtn = document.getElementById("btnCollision");
  if (collisionBtn) {
    collisionBtn.addEventListener("click", () => {
      collisionActive = !collisionActive;
      collisionBtn.classList.toggle("active", collisionActive);
      try { setCollisionVisible(collisionActive); } catch(e) {}
    });
  }

  const edgesBtn = document.getElementById("btnEdges");
  if (edgesBtn) {
    edgesBtn.addEventListener("click", () => {
      try { window.__sbToggleEdges?.(); } catch(e) {}
    });
  }

  try { applyPanMode(); } catch(e) {}

  // --- Action buttons in sidebar #sbActions ---
  function mkAction(svgMarkup, onClick, titleText="") {
    const b = document.createElement("button");
    b.className = "btn btn-sm btn-ghost";
    b.title = titleText;
    b.innerHTML = svgMarkup;
    const svg = b.querySelector("svg");
    if (svg) { svg.setAttribute("width","14"); svg.setAttribute("height","14"); }
    b.addEventListener("click", onClick);
    return b;
  }

  const actions = document.getElementById("sbActions");
  if (!actions) return;

  const btnRemove = mkAction(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg> Delete`, () => removeSelected(), "Delete selected");
  const btnEdit   = mkAction(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg> Edit`, () => moveSelected(), "Re-attach / edit anchoring");
  const btnFlipX  = mkAction(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 16l-4-4 4-4"/><path d="M17 8l4 4-4 4"/><line x1="3" y1="12" x2="21" y2="12" opacity=".3"/></svg> Flip X`, () => flipSelectedAxis("x"), "Flip 90° X");
  const btnFlipY  = mkAction(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 7l-4 4 4 4"/><path d="M16 7l4 4-4 4"/><line x1="12" y1="3" x2="12" y2="21" opacity=".3"/></svg> Flip Y`, () => flipSelectedAxis("y"), "Flip 90° Y");
  const btnFlipZ  = mkAction(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8V4m0 16v-4M4 12H0m20 0h-4"/><circle cx="12" cy="12" r="4" opacity=".4"/></svg> Flip Z`, () => flipSelectedAxis("z"), "Flip 90° Z");

  const btnHide = mkAction(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg> Hide`, () => {
    const sel = window.builderState?.selectedName;
    if (sel) { __toggleObjectVisibility(sel); __updateHideBtn(); }
  }, "Hide/show in scene");
  function __updateHideBtn() {
    const sel = window.builderState?.selectedName;
    const hidden = sel && __hiddenObjects.has(sel);
    btnHide.innerHTML = hidden
      ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/></svg> Show'
      : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg> Hide';
  }
  window.__builderUpdateHideBtn = __updateHideBtn;

  // Row 1: Delete + Edit + Hide
  for (const b of [btnRemove, btnEdit, btnHide]) actions.appendChild(b);

  // Row 2: Flip X / Y / Z on one line
  const flipRow = document.createElement("div");
  flipRow.style.cssText = "display:flex;gap:4px;margin-top:2px;max-width:240px";
  for (const b of [btnFlipX, btnFlipY, btnFlipZ]) {
    b.style.flex = "1";
    flipRow.appendChild(b);
  }
  actions.appendChild(flipRow);

  // Row 3: Move to the adjacent parent anchor ("next hole") — X± / Y± / Z±
  // in the parent's local frame. Complements the flips: flips rotate in
  // place, these step the attach anchor across the parent's grid.
  const nudgeBtns = [];
  const nudgeRow = document.createElement("div");
  nudgeRow.style.cssText = "display:flex;gap:4px;margin-top:2px;max-width:240px";
  for (const [label, axis, sign] of [
    ["X−", "x", -1], ["X+", "x", 1],
    ["Y−", "y", -1], ["Y+", "y", 1],
    ["Z−", "z", -1], ["Z+", "z", 1],
  ]) {
    const b = mkAction(label, () => nudgeSelectedAnchor(axis, sign),
                       `Move to the next parent anchor ${label}`);
    b.style.flex = "1";
    b.style.minWidth = "0";
    nudgeBtns.push(b);
    nudgeRow.appendChild(b);
  }
  actions.appendChild(nudgeRow);

  // Row 4 (collision boxes only): X/Y/Z size in mm — live-updates the
  // wireframe and the config. Hidden for every other component type.
  const sizeRow = document.createElement("div");
  sizeRow.style.cssText = "display:none;gap:6px;margin-top:2px;align-items:center;max-width:240px";
  const AXIS_COLORS = { x: "#e5534b", y: "#34c759", z: "#4f9cf9" };
  const sizeInputs = {};
  for (const ax of ["x", "y", "z"]) {
    const cell = document.createElement("label");
    cell.style.cssText = "display:flex;align-items:center;gap:4px;flex:1;min-width:0";
    const tag = document.createElement("span");
    tag.textContent = ax.toUpperCase();
    tag.style.cssText = `font-size:11px;font-weight:700;color:${AXIS_COLORS[ax]};flex:0 0 auto`;
    const inp = document.createElement("input");
    inp.type = "number";
    inp.min = "1";
    inp.step = "1";
    inp.title = `Collision box ${ax.toUpperCase()} length (mm) — or drag the ${ax.toUpperCase()} handle in 3D`;
    _sbStyleInput(inp);
    inp.style.flex = "1";
    inp.style.minWidth = "0";
    inp.style.width = "100%";
    inp.style.padding = "4px 6px";
    inp.style.fontSize = "12px";
    inp.addEventListener("change", __commitColBoxSize);
    sizeInputs[ax] = inp;
    cell.appendChild(tag);
    cell.appendChild(inp);
    sizeRow.appendChild(cell);
  }
  actions.appendChild(sizeRow);

  function __commitColBoxSize() {
    const name = window.builderState?.selectedName;
    const meta = name && window.builderState.components[name];
    const obj = name && objectsByName.get(name);
    if (!meta || meta.type !== "collision_box") return;
    const prevMeta = __deepClone(meta);
    const cur = Array.isArray(meta.size) ? meta.size : [100, 100, 100];
    const sz = ["x", "y", "z"].map((ax, i) => {
      const v = parseFloat(sizeInputs[ax].value);
      return Number.isFinite(v) && v > 0 ? v : (cur[i] || 100);
    });
    meta.size = sz;
    ["x", "y", "z"].forEach((ax, i) => { sizeInputs[ax].value = sz[i]; });
    try { __rebuildColBoxViz(name); } catch (e) { console.warn(e); }
    try {
      const r = window.quaternionToRodriguesDeg && obj ? window.quaternionToRodriguesDeg(obj.quaternion) : [0, 0, 0];
      const pose = obj ? [obj.position.x, obj.position.y, obj.position.z, r[0] || 0, r[1] || 0, r[2] || 0] : null;
      __pushUndo({ kind: "transform", name, prevMeta, prevPose: pose, nextMeta: __deepClone(meta), nextPose: pose });
    } catch (e) {}
    try { if (window.__updateConfigPreview) window.__updateConfigPreview(); } catch (e) {}
    showToast(`${name}: size ${sz.join(" × ")} mm`);
  }

  window.__builderUpdateSizeRow = (name) => {
    const meta = name && window.builderState?.components?.[name];
    const isBox = !!(meta && meta.type === "collision_box");
    sizeRow.style.display = isBox ? "flex" : "none";
    if (isBox) {
      const sz = Array.isArray(meta.size) ? meta.size : [100, 100, 100];
      sizeInputs.x.value = sz[0];
      sizeInputs.y.value = sz[1];
      sizeInputs.z.value = sz[2];
      // 3D drag handles follow the panel: shown while a box is selected
      // (idempotent — __cbhShow no-ops for the same box and mid-drag).
      try { if (window.__colBoxHandlesShow) window.__colBoxHandlesShow(name); } catch (e) {}
    } else {
      try { if (window.__colBoxHandlesHide) window.__colBoxHandlesHide(); } catch (e) {}
    }
  };

  window.__builderSetActionsEnabled = (enabled) => {
    const sec = document.getElementById("sbPropsSection");
    if (sec) sec.style.display = enabled ? "" : "none";
    for (const b of [btnRemove, btnEdit, btnFlipX, btnFlipY, btnFlipZ, ...nudgeBtns]) {
      b.disabled = !enabled;
      b.style.opacity = enabled ? "1" : "0.4";
    }
  };

  window.__builderSetRectPatternEnabled = () => {};
}

// Shared theme variables for all dynamic panels
// Apply consistent .input styling to dynamically created form elements
function _sbStyleInput(el) {
  el.className = (el.className ? el.className + " " : "") + "input";
  el.style.width = "";  // let CSS handle it
  return el;
}

function _sbPanelTheme() {
  // Use CSS custom properties so panels auto-update when the theme toggles.
  const isLight = (document.documentElement.getAttribute("data-theme") || "dark") === "light";
  return {
    isLight,
    panelBg:    "var(--panel-bg)",
    panelBord:  "1px solid var(--panel-bord)",
    color:      "var(--panel-color)",
    inputBg:    "var(--panel-input-bg)",
    inputBord:  "1px solid var(--panel-input-bord)",
    rowBg:      "var(--panel-row-bg)",
    rowBord:    "1px solid var(--panel-row-bord)",
    listBord:   "1px solid var(--panel-list-bord)",
    listBg:     "var(--panel-list-bg)",
    itemBord:   "1px solid var(--panel-item-bord)",
    cancelBg:   "var(--panel-cancel-bg)",
  };
}

function openModal(title, buttons) {
  // buttons: [{label, onClick}]
  const th = _sbPanelTheme();
  let bg = document.getElementById("builderModalBg");
  if (bg) bg.remove();
  bg = document.createElement("div");
  bg.id = "builderModalBg";
  // ``modal-overlay`` → covers the navbar (opts out of the nav's
  // sibling margin-shift, see vendor/nav.css).
  bg.className = "modal-overlay";
  bg.style.position = "fixed";
  bg.style.inset = "0";
  bg.style.background = "rgba(0,0,0,0.25)";
  bg.style.zIndex = "10000";
  bg.addEventListener("click", (e) => { if (e.target === bg) bg.remove(); });

  const panel = document.createElement("div");
  panel.style.position = "absolute";
  panel.style.left = "12px";
  panel.style.top = "60px";
  panel.style.maxHeight = "70vh";
  panel.style.overflowY = "auto";
  panel.style.paddingBottom = "10px";
  panel.style.width = "260px";
  panel.style.background = th.panelBg;
  panel.style.borderRadius = "16px";
  panel.style.border = th.panelBord;
  panel.style.boxShadow = "0 16px 40px rgba(0,0,0,0.45)";
  panel.style.padding = "12px";
  panel.style.fontFamily = "system-ui, -apple-system, Segoe UI, Roboto, Arial";
  panel.style.color = th.color;

  const h = document.createElement("div");
  h.textContent = title;
  h.style.fontWeight = "700";
  h.style.marginBottom = "10px";
  panel.appendChild(h);

  for (const btn of buttons) {
    const b = document.createElement("button");
    b.textContent = btn.label;
    b.style.width = "100%";
    b.style.padding = "10px 10px";
    b.style.borderRadius = "12px";
    b.style.border = th.rowBord;
    b.style.background = th.rowBg;
    b.style.color = th.color;
    b.style.cursor = "pointer";
    b.style.marginBottom = "8px";
    b.addEventListener("click", () => {
      try { btn.onClick(); } finally { bg.remove(); }
    });
    panel.appendChild(b);
  }

  bg.appendChild(panel);
  document.body.appendChild(bg);
}

function buildFixturePlateAnchors() {
  const anchors = {};
  const plate_pitch = 25.0;
  const plate_x_start = -237.5;
  const plate_y_start = 112.5;
  const rows = "ABCDEFGHIJ".split("");
  const cols = Array.from({length:20}, (_,i)=>i+1);
  for (let rIdx=0; rIdx<rows.length; rIdx++) {
    const r = rows[rIdx];
    const y = plate_y_start - rIdx * plate_pitch;
    for (const c of cols) {
      const x = plate_x_start + (c-1)*plate_pitch;
      anchors[`${r}${c}`] = [x,y,7,0,0,0];
    }
  }
  anchors["corner_0"] = [-250, 125, 7, 0,0,0];
  anchors["corner_1"] = [ 250, 125, 7, 0,0,0];
  anchors["corner_2"] = [ 250,-125, 7, 0,0,0];
  anchors["corner_3"] = [-250,-125, 7, 0,0,0];
  anchors["center"] = [0,0,7,0,0,0];
  return anchors;
}

function buildSbsAdapterAnchors() {
  return {
    "center": [0,0,0,0,0,0],
    "place":  [0,0,4.5,0,0,0],
    "top":    [0,0,8,0,0,0],
    "front":  [0,0,4.5,0,0,180],
    "back":   [0,0,4.5,0,0,0]
  };
}

async function spawnComponent(type, meta=null, options=null, customName=null) {
  // Generic spawn for any other component type. Uses /api/type_meta for anchors/options.

// Instance naming (collision-safe):
// - Most components use "<type>_<n>"
// - For "core", the first instance is "core" (if free), then "core_2", ...
if (!window.builderState.next[type]) window.builderState.next[type] = 1;

function __nameExists(n) {
  try {
    if (window.builderState.components && window.builderState.components[n]) return true;
    if (typeof objectsByName !== "undefined" && objectsByName && objectsByName.has && objectsByName.has(n)) return true;
  } catch (e) {}
  return false;
}

let name;

// Use custom name if provided and available
if (customName && customName.trim() && !__nameExists(customName.trim())) {
  name = customName.trim();
} else {
  let i = window.builderState.next[type];

  // Propose candidate based on type + index
  function __candidate(t, idx) {
    if (t === "core") {
      return (idx === 1) ? "core" : `core_${idx}`;
    }
    return `${t}_${idx}`;
  }

  name = __candidate(type, i);
  while (__nameExists(name)) {
    i += 1;
    name = __candidate(type, i);
  }
  // Next free index for this type
  window.builderState.next[type] = i + 1;
}


  // Instantiate component server-side (simulation-style) so anchors match exactly.
  let blueprint = null;
  try {
    const res = await fetch(SB_API + "/instantiate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type, options: (options && typeof options === "object") ? options : {} })
    });
    const js = await res.json();
    if (js && js.ok) blueprint = js.blueprint;
  } catch (e) {
    console.warn("instantiate failed", e);
  }

  // Build anchorsBySolid + meshes from blueprint
  let anchorsBySolid = {};
  let meshes = null;
  let glb = (meta && meta.glb) ? meta.glb : `/static/CAD/${type}.glb`;
  let collisionLocalSingle = [];
  let boxForGripSingle = false;
  if (blueprint && Array.isArray(blueprint.solids) && blueprint.solids.length) {
    try { collisionLocalSingle = blueprint.solids[0].collisionLocal || []; } catch (e) { collisionLocalSingle = []; }
    try { boxForGripSingle = !!blueprint.solids[0].boxForGrip; } catch (e) {}
    meshes = [];
    for (const s of blueprint.solids) {
      if (s && s.solid && s.anchors && Object.keys(s.anchors).length) {
        anchorsBySolid[s.solid] = s.anchors;
      }
      if (s && s.glb) {
        meshes.push({ meshUrl: s.glb, pose: s.pose || [0,0,0,0,0,0], solidName: s.solid, collisionLocal: s.collisionLocal || [], boxForGrip: !!s.boxForGrip });
      }
    }
    // If we got at least one mesh, do multi-mesh rendering.
    if (!meshes.length) {
      meshes = null;
      // No solid has a GLB — clear glb so the no-mesh branch in upsertObject handles it
      const anyGlb = blueprint.solids.some(s => s && s.glb);
      if (!anyGlb) glb = null;
    }
    // For single-mesh fallback, pick the first available GLB
    if (!glb) {
      for (const s of blueprint.solids) {
        if (s && s.glb) { glb = s.glb; break; }
      }
    }
  }

  // pick first solid for anchor UI
  let solidName = "solid_0";
  let anchors = {};
  const solids = Object.keys(anchorsBySolid || {});
  if (solids.length) {
    solidName = solids[0];
    anchors = anchorsBySolid[solidName] || {};
  }

  const spec = {
    meshUrl: meshes ? null : glb,
    meshes: meshes,
    collisionLocal: meshes ? null : (collisionLocalSingle || []),
    boxForGrip: meshes ? false : (boxForGripSingle || false),
    pose: [0, 0, ((type === "fixture_plate" && !window.builderState.lastFixturePlate) ? 0 : 375), 0, 0, 0],
    visible: true,
    anchors,
    anchorsBySolid,
    type,
    componentName: name,
    solidName
  };

  // include create options (stored for config save)
  const optionsOut = (options && typeof options === "object") ? options : {};
  window.builderState.components[name] = { type, ...optionsOut };

  try { socket.emit("upstream_update", { [name]: spec }); } catch (e) {}
  try { upsertObject(name, spec); } catch (e) {}
  window.builderState.placedOrder.push(name);

  // Undo step #1: creation (so undo after an attach will first un-attach, then delete)
  try {
    __pushUndo({
      kind: "create",
      names: [name],
      specs: { [name]: __deepClone(spec) },
      metas: { [name]: __deepClone(window.builderState.components[name]) }
    });
  } catch (e) {}

  // Special-case: the very first fixture plate has nothing to attach to.
  // Spawn it as the base plate immediately (no anchor selection flow).
  if (type === "fixture_plate" && !window.builderState.lastFixturePlate) {
    window.builderState.lastFixturePlate = name;
    window.builderState.components[name] = Object.assign({}, window.builderState.components[name]||{}, { type: "fixture_plate" });
    try { if (window.updateObjectList) window.updateObjectList(); } catch(e) {}
    try { if (window.__updateConfigPreview) window.__updateConfigPreview(); } catch(e) {}
    showToast(`Spawned ${name}. (Base fixture plate)`);
    return name;
  }

  if (!Object.keys(anchors||{}).length) {
    showToast(`Spawned ${name}. (No anchors present)`);
    return name;
  }

  // Show a menu to choose which anchor on the new object to mount from
  try {
    openChildAnchorMenu(name, anchorsBySolid);
  } catch (e) {
    console.warn(e);
    // fallback: default to first anchor and go to target selection
    const a0 = Object.keys(anchors||{})[0];
    window.builderState.pending = { name, childSolid: solidName, childAnchor: a0 };
    window.builderState.mode = "PICK_TARGET_OBJECT";
    window.builderState.targetName = null;
    showToast("Click a target object, then click its anchor.");
  }
  return name;
}


window.spawnComponent = spawnComponent;

// Spawn without opening the child-anchor picker UI. Used for automated patterning.
// It mirrors spawnComponent() but skips any modal flow.
async function spawnComponentSilent(type, meta=null, options=null, customName=null, extra=null) {
  if (!window.builderState.next[type]) window.builderState.next[type] = 1;

  function __nameExists(n) {
    try {
      if (window.builderState.components && window.builderState.components[n]) return true;
      if (typeof objectsByName !== "undefined" && objectsByName && objectsByName.has && objectsByName.has(n)) return true;
    } catch (e) {}
    return false;
  }

  let name;
  if (customName) {
    name = customName;
  } else if (type === "core") {
    const alreadyHasCore = !!(window.builderState.components && window.builderState.components["core"]);
    if (!alreadyHasCore && window.builderState.next[type] === 1) {
      name = "core";
      window.builderState.next[type] = 2;
    } else {
      name = `core_${window.builderState.next[type]++}`;
      while (__nameExists(name)) name = `core_${window.builderState.next[type]++}`;
    }
  } else {
    name = `${type}_${window.builderState.next[type]++}`;
    while (__nameExists(name)) name = `${type}_${window.builderState.next[type]++}`;
  }
  // Instantiate server-side so anchors/options match exactly. A bulk
  // import prefetches every blueprint in ONE /instantiate_batch call
  // and hands it in via ``extra`` — no per-component round-trip.
  let blueprint = (extra && extra.blueprint) || null;
  if (!blueprint) {
    try {
      const res = await fetch(SB_API + "/instantiate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type, options: (options && typeof options === "object") ? options : {} })
      });
      const js = await res.json();
      if (js && js.ok) blueprint = js.blueprint;
    } catch (e) {
      console.warn("instantiate failed", e);
    }
  }

  let anchorsBySolid = {};
  let meshes = null;
  let glb = (meta && meta.glb) ? meta.glb : `/static/CAD/${type}.glb`;
  let collisionLocalSingle = [];
  let boxForGripSingle = false;
  if (blueprint && Array.isArray(blueprint.solids) && blueprint.solids.length) {
    try { collisionLocalSingle = blueprint.solids[0].collisionLocal || []; } catch (e) { collisionLocalSingle = []; }
    try { boxForGripSingle = !!blueprint.solids[0].boxForGrip; } catch (e) {}
    meshes = [];
    for (const s of blueprint.solids) {
      if (s && s.solid && s.anchors && Object.keys(s.anchors).length) {
        anchorsBySolid[s.solid] = s.anchors;
      }
      if (s && s.glb) {
        meshes.push({ meshUrl: s.glb, pose: s.pose || [0,0,0,0,0,0], solidName: s.solid, collisionLocal: s.collisionLocal || [], boxForGrip: !!s.boxForGrip });
      }
    }
    if (!meshes.length) {
      meshes = null;
      // No solid has a GLB — clear glb so the no-mesh branch in upsertObject handles it
      const anyGlb = blueprint.solids.some(s => s && s.glb);
      if (!anyGlb) glb = null;
    }
    if (!glb) {
      for (const s of blueprint.solids) {
        if (s && s.glb) { glb = s.glb; break; }
      }
    }
  }

  let solidName = "solid_0";
  let anchors = {};
  const solids = Object.keys(anchorsBySolid || {});
  if (solids.length) {
    solidName = solids[0];
    anchors = anchorsBySolid[solidName] || {};
  }

  const spec = {
    meshUrl: meshes ? null : glb,
    meshes: meshes,
    collisionLocal: meshes ? null : (collisionLocalSingle || []),
    boxForGrip: meshes ? false : (boxForGripSingle || false),
    pose: [0, 0, ((type === "fixture_plate" && !window.builderState.lastFixturePlate) ? 0 : 375), 0, 0, 0],
    visible: true,
    anchors,
    anchorsBySolid,
    type,
    componentName: name,
    solidName
  };

  const optionsOut = (options && typeof options === "object") ? options : {};
  window.builderState.components[name] = { type, ...optionsOut };

  if (extra && extra.collect) extra.collect[name] = spec;
  else { try { socket.emit("upstream_update", { [name]: spec }); } catch (e) {} }
  try { upsertObject(name, spec); } catch (e) {}
  window.builderState.placedOrder.push(name);

  // Undo: creation (silent spawns still behave like normal spawns for
  // Ctrl+Z). A bulk project import is NOT an edit — no undo entries.
  if (!(extra && extra.noUndo)) try {
    __pushUndo({
      kind: "create",
      names: [name],
      specs: { [name]: __deepClone(spec) },
      metas: { [name]: __deepClone(window.builderState.components[name]) }
    });
  } catch (e) {}

// Visual-only QoL: some models (notably fixture_plate) have their origin at mid-height,
// so spawning at z=0 makes them appear to "float" in the builder even though simulation
// may place them differently. If a component is NOT attached, we can auto-drop it so its
// lowest point rests on the ground plane (z=0) in the builder view.
try {
  if (type === "fixture_plate") {
    // Fire-and-forget: wait for GLTF to finish, then drop to ground.
    (async () => {
      const t0 = performance.now();
      while (performance.now() - t0 < 2500) {
        const obj = window.objectsByName && window.objectsByName.get(name);
        // Wait until the object exists and has geometry.
        if (obj && obj.isObject3D && obj.children && obj.children.length) {
          const metaNow = window.builderState.components && window.builderState.components[name];
          if (metaNow && !metaNow.attach) {
            const box = new THREE.Box3().setFromObject(obj);
            const minZ = box.min.z;
            if (isFinite(minZ) && Math.abs(minZ) > 1e-6) {
              obj.position.z -= minZ; // put bottom on z=0
              // Keep server / shared state in sync so it doesn't "snap back".
              const rv = (window.quatToRodriguesDeg) ? window.quatToRodriguesDeg(obj.quaternion) : [0,0,0];
              try { socket.emit("upstream_update", { [name]: { pose: [obj.position.x, obj.position.y, obj.position.z, rv[0], rv[1], rv[2]] } }); } catch(e) {}
            }
          }
          break;
        }
        await new Promise(r => requestAnimationFrame(r));
      }
    })();
  }
} catch (e) {}

return name;
}

window.spawnComponentSilent = spawnComponentSilent;

// =====================
// Anchor/Joints: searchable side-panel picker (pattern-style)
// =====================
function closeAnchorPickPanel() {
  const el = document.getElementById("anchorPickPanel");
  if (el) el.remove();
  // Also close the unified attach modal if open
  const am = document.getElementById("attachModal");
  if (am) am.remove();
}

function openAnchorPickPanel(opts) {
  // opts: {
  //   panelId, title, subtitle,
  //   solids: string[], currentSolid?, getCurrentSolid?(), setCurrentSolid?(solid), onSolidChange?(solid)
  //   getAnchorNames(solid)->string[], onPick?(name, solid), onPickAnchor?(name, solid), onCancel?()
  // }
  const th = _sbPanelTheme();
  closeAnchorPickPanel();
  const panel = document.createElement("div");
  panel.id = opts.panelId || "anchorPickPanel";
  panel.style.position = "fixed";
  panel.style.right = "340px";
  panel.style.top = "18px";
  panel.style.width = "360px";
  panel.style.maxHeight = "85vh";
  panel.style.overflow = "hidden";
  panel.style.background = th.panelBg;
  panel.style.border = th.panelBord;
  panel.style.borderRadius = "16px";
  panel.style.boxShadow = "0 18px 60px rgba(0,0,0,0.45)";
  panel.style.padding = "14px";
  panel.style.zIndex = "10006";
  panel.style.fontFamily = "system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif";
  panel.style.color = th.color;

  const title = document.createElement("div");
  title.textContent = opts.title || "Anchors";
  title.style.fontWeight = "800";
  title.style.fontSize = "16px";
  title.style.marginBottom = "6px";
  panel.appendChild(title);

  if (opts.subtitle) {
    const sub = document.createElement("div");
    sub.textContent = opts.subtitle;
    sub.style.fontSize = "12px";
    sub.style.opacity = "0.75";
    sub.style.marginBottom = "10px";
    panel.appendChild(sub);
  }

  let currentSolid = (opts.getCurrentSolid ? opts.getCurrentSolid() : opts.currentSolid) || (opts.solids && opts.solids.length ? opts.solids[0] : null);

  if (opts.solids && opts.solids.length > 1) {
    const solidRow = document.createElement("div");
    solidRow.style.display = "flex";
    solidRow.style.alignItems = "center";
    solidRow.style.gap = "10px";
    solidRow.style.marginBottom = "10px";

    const solidLabel = document.createElement("div");
    solidLabel.textContent = "Solid";
    solidLabel.style.fontWeight = "700";
    solidLabel.style.width = "60px";
    solidLabel.style.fontSize = "12px";

    const solidSelect = document.createElement("select");
    _sbStyleInput(solidSelect);
    solidSelect.style.flex = "1";
    for (const s of opts.solids) {
      const optEl = document.createElement("option");
      optEl.value = s;
      optEl.textContent = s;
      solidSelect.appendChild(optEl);
    }
    if (currentSolid) solidSelect.value = currentSolid;
    solidSelect.onchange = () => {
      currentSolid = solidSelect.value;
      try { opts.setCurrentSolid && opts.setCurrentSolid(currentSolid); } catch(_) {}
      try { opts.onSolidChange && opts.onSolidChange(currentSolid); } catch(_) {}
      render();
    };

    solidRow.appendChild(solidLabel);
    solidRow.appendChild(solidSelect);
    panel.appendChild(solidRow);
  }

  const search = document.createElement("input");
  search.type = "text";
  search.placeholder = "Search anchors…";
  search.style.width = "100%";
  search.style.boxSizing = "border-box";
  search.style.padding = "10px 10px";
  search.style.borderRadius = "12px";
  search.style.border = th.inputBord;
  search.style.background = th.inputBg;
  search.style.color = th.color;
  search.style.marginBottom = "10px";
  panel.appendChild(search);

  const list = document.createElement("div");
  list.style.maxHeight = "55vh";
  list.style.overflow = "auto";
  list.style.border = th.listBord;
  list.style.borderRadius = "12px";
  list.style.background = th.listBg;
  panel.appendChild(list);

  function sortAnchors(arr) {
    return (arr || []).slice().sort((a,b)=>a.localeCompare(b, undefined, {numeric:true}));
  }

  function render() {
    list.innerHTML = "";
    const all = sortAnchors((opts.getAnchorNames ? opts.getAnchorNames(currentSolid) : []) || []);
    const q = (search.value || "").trim().toLowerCase();
    const shown = q ? all.filter(n => n.toLowerCase().includes(q)) : all;
    if (!shown.length) {
      const none = document.createElement("div");
      none.textContent = "No matching anchors.";
      none.style.padding = "10px";
      none.style.fontSize = "12px";
      none.style.opacity = "0.7";
      list.appendChild(none);
      return;
    }
    let firstEl = null;
    for (const n of shown) {
      const item = document.createElement("div");
      item.textContent = n;
      item.style.padding = "10px 10px";
      item.style.cursor = "pointer";
      item.style.userSelect = "none";
      item.style.fontSize = "12px";
      item.style.borderBottom = "1px solid var(--panel-item-bord)";
      item.style.color = "var(--panel-color)";
      item.onmouseenter = () => item.style.background = "var(--panel-hover)";
      item.onmouseleave = () => item.style.background = "transparent";
      item.onclick = () => {
        try {
          if (opts.onPick) opts.onPick(n, currentSolid);
          else if (opts.onPickAnchor) opts.onPickAnchor(n, currentSolid);
        } catch(e) { console.error(e); }
      };
      if (!firstEl) firstEl = item;
      list.appendChild(item);
    }
    // If user is typing, scroll to first result.
    if (q && firstEl) {
      try { firstEl.scrollIntoView({block:"nearest"}); } catch(_) {}
    }
  }

  search.addEventListener("input", () => render());
  search.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      try { opts.onCancel && opts.onCancel(); } catch(_) {}
      return;
    }
    if (e.key === "Enter") {
      // pick the first visible item
      const first = list.querySelector("div");
      if (first && first.textContent) {
        try {
          if (opts.onPick) opts.onPick(first.textContent, currentSolid);
          else if (opts.onPickAnchor) opts.onPickAnchor(first.textContent, currentSolid);
        } catch(_) {}
      }
    }
  });

  const bottom = document.createElement("div");
  bottom.style.display = "flex";
  bottom.style.justifyContent = "flex-end";
  bottom.style.gap = "8px";
  bottom.style.marginTop = "10px";

  const cancel = document.createElement("button");
  cancel.textContent = "Cancel";
  cancel.style.padding = "10px 14px";
  cancel.style.borderRadius = "12px";
  cancel.style.border = th.panelBord;
  cancel.style.background = th.cancelBg;
  cancel.style.color = th.color;
  cancel.style.fontWeight = "800";
  cancel.style.cursor = "pointer";
  cancel.onclick = () => { try { opts.onCancel && opts.onCancel(); } catch(_) {} };
  bottom.appendChild(cancel);
  panel.appendChild(bottom);

  document.body.appendChild(panel);
  render();
  // focus search after paint
  setTimeout(() => { try { search.focus(); search.select(); } catch(_) {} }, 0);
}


// ─── Unified 2-step attach modal ──────────────────────────────────────────────
// Replaces openChildAnchorMenu + openTargetObjectPickPanel + openTargetAnchorMenu.
// Step 1: pick anchor on the child (new/re-attaching) object.
// Step 2: pick parent object, then pick parent anchor — all in one panel.
function closeAttachModal() {
  const el = document.getElementById("attachModal");
  if (el) el.remove();
  // close any legacy panels that might still be open
  const ap = document.getElementById("anchorPickPanel");
  if (ap) ap.remove();
  const tp = document.getElementById("targetObjectPickPanel");
  if (tp) tp.remove();
  // drop the 3D-click → modal routes
  try {
    delete window.builderState._targetObjectCallback;
    delete window.builderState._childAnchorCallback;
    delete window.builderState._childAnchorSetSolid;
  } catch (_) {}
}

function openAttachModal(childName, anchorsBySolid, opts = {}) {
  const th = _sbPanelTheme();
  closeAttachModal();

  const SIDEBAR_W = (() => {
    try { return Math.round(document.querySelector(".sidebar").getBoundingClientRect().width); }
    catch (e) { return 340; }
  })();
  const HEADER_H  = 52;
  const MODAL_W   = 380;
  const DIM       = "var(--panel-dim)";
  const DIVIDER   = "var(--panel-divider)";

  const modal = document.createElement("div");
  modal.id = "attachModal";
  Object.assign(modal.style, {
    position: "fixed",
    left: (SIDEBAR_W + 14) + "px",
    top: (HEADER_H + 12) + "px",
    width: MODAL_W + "px",
    maxHeight: "80vh",
    overflow: "hidden",
    display: "flex",
    flexDirection: "column",
    background: th.panelBg,
    border: th.panelBord,
    borderRadius: "14px",
    boxShadow: "0 16px 56px rgba(0,0,0,0.55), 0 2px 8px rgba(0,0,0,0.3)",
    fontFamily: "system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif",
    color: th.color,
    colorScheme: "light dark",
    zIndex: "10010",
    animation: "attachModalIn 0.2s cubic-bezier(0.34,1.56,0.64,1) forwards",
  });

  // ── State ─────────────────────────────────────────────────────────────────
  let step        = 1;
  let childSolid  = null;
  let childAnchor = null;
  let targetName  = null;
  let targetSolid = null;

  // ── Cancel ────────────────────────────────────────────────────────────────
  function doCancel() {
    if (opts.op !== "move") {
      try {
        const meta = window.builderState?.components?.[childName];
        if (meta && !meta.attach) {
          __deleteComponentByName(childName);
          showToast("Cancelled " + childName);
        }
      } catch(e) {}
    }
    try { window.builderState.pending = null; window.builderState.targetName = null; window.builderState.mode = "IDLE"; } catch(_) {}
    try { clearAnchors(); } catch(_) {}
    try { setSelected(null); } catch(_) {}
    if (opts.onCancel) try { opts.onCancel(); } catch(_) {}
    closeAttachModal();
  }

  // ── Shared helpers ────────────────────────────────────────────────────────
  function mkStepHeader(stepNum, totalSteps, title, subtitle) {
    const wrap = document.createElement("div");
    Object.assign(wrap.style, {
      padding: "16px 18px 14px",
      borderBottom: th.panelBord,
      background: th.panelBg,
      flexShrink: "0",
    });
    // Progress bar dots
    const dots = document.createElement("div");
    Object.assign(dots.style, { display: "flex", alignItems: "center", gap: "6px", marginBottom: "12px" });
    for (let i = 1; i <= totalSteps; i++) {
      const d = document.createElement("div");
      const active = i === stepNum, done = i < stepNum;
      if (active) d.style.cssText = "width:20px;height:5px;border-radius:3px;background:#4f9cf9";
      else if (done) d.style.cssText = "width:8px;height:5px;border-radius:3px;background:#4f9cf9;opacity:0.55";
      else d.style.cssText = `width:8px;height:5px;border-radius:3px;background:${DIVIDER}`;
      dots.appendChild(d);
      if (i < totalSteps) {
        const line = document.createElement("div");
        line.style.cssText = `flex:1;height:1px;background:${done ? "#4f9cf9" : DIVIDER};opacity:${done ? "0.5" : "1"}`;
        dots.appendChild(line);
      }
    }
    wrap.appendChild(dots);
    const lbl = document.createElement("div");
    lbl.textContent = "Step " + stepNum + " of " + totalSteps;
    lbl.style.cssText = `font-size:10px;font-weight:600;color:${DIM};letter-spacing:0.06em;text-transform:uppercase;margin-bottom:3px`;
    wrap.appendChild(lbl);
    const ttl = document.createElement("div");
    ttl.textContent = title;
    ttl.style.cssText = `font-size:15px;font-weight:700;color:${th.color}`;
    wrap.appendChild(ttl);
    if (subtitle) {
      const sub = document.createElement("div");
      sub.textContent = subtitle;
      sub.style.cssText = `font-size:12px;color:${DIM};margin-top:2px`;
      wrap.appendChild(sub);
    }
    return wrap;
  }

  function mkSearchList(items, onSelect, placeholder, selectedVal) {
    const wrap = document.createElement("div");
    wrap.style.cssText = "display:flex;flex-direction:column;gap:6px";
    const inputWrap = document.createElement("div");
    inputWrap.style.cssText = "position:relative";
    inputWrap.innerHTML = `<svg style="position:absolute;left:10px;top:50%;transform:translateY(-50%);pointer-events:none;opacity:0.38" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="${th.color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`;
    const inp = document.createElement("input");
    inp.type = "text";
    inp.placeholder = placeholder || "Search…";
    Object.assign(inp.style, {
      width: "100%", boxSizing: "border-box", padding: "8px 12px 8px 30px",
      borderRadius: "8px", border: th.inputBord, background: th.inputBg,
      color: th.color, fontSize: "13px", outline: "none",
    });
    inputWrap.appendChild(inp);
    wrap.appendChild(inputWrap);
    const list = document.createElement("div");
    Object.assign(list.style, {
      overflowY: "auto", border: th.listBord, borderRadius: "8px",
      background: th.listBg, maxHeight: "200px",
    });
    wrap.appendChild(list);
    function rebuild() {
      list.innerHTML = "";
      const q = inp.value.trim().toLowerCase();
      const shown = q ? items.filter(n => n.toLowerCase().includes(q)) : items;
      if (!shown.length) {
        const none = document.createElement("div");
        none.textContent = "No matches.";
        none.style.cssText = `padding:10px 12px;font-size:12px;color:${DIM}`;
        list.appendChild(none);
        return;
      }
      for (const n of shown) {
        const row = document.createElement("div");
        const isSel = n === selectedVal;
        Object.assign(row.style, {
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "9px 12px", cursor: "pointer", fontSize: "13px",
          color: th.color,
          borderBottom: th.itemBord,
          background: isSel ? "rgba(79,156,249,0.15)" : "",
        });
        const rowLabel = document.createElement("span");
        rowLabel.textContent = n;
        const rowArrow = document.createElement("span");
        rowArrow.innerHTML = "›";
        rowArrow.style.cssText = `opacity:${isSel ? "1" : "0"};font-size:16px;line-height:1;color:#4f9cf9;transition:opacity 0.1s;flex-shrink:0`;
        row.appendChild(rowLabel);
        row.appendChild(rowArrow);
        row.onmouseenter = () => { if (!isSel) row.style.background = "var(--panel-hover)"; rowArrow.style.opacity = "1"; };
        row.onmouseleave = () => { if (!isSel) row.style.background = ""; rowArrow.style.opacity = isSel ? "1" : "0"; };
        row.onclick = () => onSelect(n);
        list.appendChild(row);
      }
    }
    rebuild();
    inp.addEventListener("input", rebuild);
    inp.addEventListener("keydown", e => {
      if (e.key === "Escape") { doCancel(); return; }
      if (e.key === "Enter") {
        const first = list.querySelector("div");
        const firstName = first?.querySelector("span")?.textContent;
        if (firstName && items.includes(firstName)) onSelect(firstName);
      }
    });
    wrap._input = inp;
    return wrap;
  }

  function mkBtn(label, primary) {
    const b = document.createElement("button");
    b.className = primary ? "btn btn-primary" : "btn btn-ghost";
    b.innerHTML = label;
    return b;
  }

  function mkSectionLabel(text) {
    const d = document.createElement("div");
    d.textContent = text;
    d.style.cssText = `font-size:11px;font-weight:600;color:${DIM};text-transform:uppercase;letter-spacing:0.05em`;
    return d;
  }

  function mkSolidSelect(solidKeys, currentSolid, onChange) {
    if (solidKeys.length <= 1) return null;
    const row = document.createElement("div");
    row.style.cssText = "display:flex;align-items:center;gap:8px";
    const lbl = document.createElement("div");
    lbl.textContent = "Solid";
    lbl.style.cssText = `font-size:12px;font-weight:600;color:${th.color};white-space:nowrap`;
    const sel = document.createElement("select");
    _sbStyleInput(sel);
    sel.style.flex = "1";
    for (const s of solidKeys) {
      const opt = document.createElement("option"); opt.value = s; opt.textContent = s;
      sel.appendChild(opt);
    }
    sel.value = currentSolid || solidKeys[0];
    sel.onchange = () => onChange(sel.value);
    row.appendChild(lbl); row.appendChild(sel);
    return row;
  }

  // ── Step 1: pick anchor on child ──────────────────────────────────────────
  function renderStep1() {
    modal.innerHTML = "";
    const solidKeys = Object.keys(anchorsBySolid || {});
    let curSolid = solidKeys[0] || null;

    modal.appendChild(mkStepHeader(1, 2, "Anchor on: " + childName, "Pick the mount anchor on the new object"));

    const body = document.createElement("div");
    Object.assign(body.style, { padding: "14px 18px", overflowY: "auto", flex: "1", display: "flex", flexDirection: "column", gap: "10px", background: th.panelBg });

    // Solid selector
    const solidSel = mkSolidSelect(solidKeys, curSolid, s => { curSolid = s; rebuildList(); });
    if (solidSel) body.appendChild(solidSel);

    // Enable 3D anchor clicking for child
    window.builderState.mode = "PICK_CHILD_ANCHOR";
    window.builderState._childAnchorCallback = (anchor, solid) => {
      childSolid = solid || curSolid;
      childAnchor = anchor;
      renderStep2();
    };
    window.builderState._childAnchorSetSolid = s => { curSolid = s; };
    try { const co = objectsByName.get(childName); if (co) buildAnchorsFor(co); } catch(e) {}

    let listWrap = null;
    function rebuildList() {
      if (listWrap) listWrap.remove();
      const aObj = (curSolid && anchorsBySolid[curSolid]) ? anchorsBySolid[curSolid] : {};
      const names = Object.keys(aObj).sort((a,b) => a.localeCompare(b, undefined, {numeric:true}));
      listWrap = mkSearchList(names, anchor => {
        childSolid = curSolid;
        childAnchor = anchor;
        renderStep2();
      }, "Search anchors…");
      body.appendChild(listWrap);
      setTimeout(() => { try { listWrap._input.focus(); } catch(_) {} }, 0);
    }
    rebuildList();

    modal.appendChild(body);

    const footer = document.createElement("div");
    Object.assign(footer.style, { padding: "12px 18px", borderTop: th.panelBord, display: "flex", justifyContent: "flex-end", flexShrink: "0", background: th.panelBg });
    const cancelBtn = mkBtn("Cancel");
    cancelBtn.onclick = doCancel;
    footer.appendChild(cancelBtn);
    modal.appendChild(footer);
  }

  // ── Step 2: pick parent object then parent anchor ─────────────────────────
  function renderStep2() {
    modal.innerHTML = "";
    window.builderState.pending = {
      name: childName, childSolid, sourceAnchor: childAnchor,
      ...(opts.op === "move" ? { op: "move" } : {})
    };
    window.builderState.mode = "PICK_TARGET_OBJECT";
    window.builderState.targetName = null;

    const blocked = __collectDescendants(childName);
    blocked.add(childName);
    const candidates = Object.keys(window.builderState.components || {})
      .filter(n => n && !blocked.has(n) && objectsByName.get(n))
      .sort((a,b) => a.localeCompare(b, undefined, {numeric:true}));

    modal.appendChild(mkStepHeader(2, 2, "Attach to parent", childName + " · " + childAnchor));

    const body = document.createElement("div");
    Object.assign(body.style, { padding: "14px 18px", overflowY: "auto", flex: "1", display: "flex", flexDirection: "column", gap: "10px", background: th.panelBg });

    // ── Object section ──────────────────────────────────────────────────────
    body.appendChild(mkSectionLabel("Parent object"));
    let anchorSection = null;

    function onObjSelect(name) {
      targetName = name;
      window.builderState.targetName = name;
      window.builderState.mode = "PICK_TARGET_ANCHOR";
      try { const to = objectsByName.get(name); if (to) buildAnchorsFor(to); } catch(e) {}
      // Rebuild object list to show new selection, then show anchor section
      rebuildObjList(name);
      buildAnchorSection(name);
    }

    let objListWrap = null;
    function rebuildObjList(selName) {
      if (objListWrap) objListWrap.remove();
      objListWrap = mkSearchList(candidates, onObjSelect, "Search objects…", selName);
      body.insertBefore(objListWrap, anchorSection || null);
    }
    rebuildObjList(null);

    // 3D-click route: clicking an object in the viewport selects it HERE —
    // highlights it in this list and reveals its anchor section — instead
    // of dead-ending (the old path called a menu that no longer exists).
    window.builderState._targetObjectCallback = (name) => {
      if (blocked.has(name)) { showToast("Can't attach to itself or its own children"); return; }
      if (!candidates.includes(name)) { showToast("Not a valid parent: " + name); return; }
      onObjSelect(name);
    };

    // ── Anchor section (shown after object selected) ────────────────────────
    function buildAnchorSection(name) {
      if (anchorSection) anchorSection.remove();
      anchorSection = document.createElement("div");
      anchorSection.style.cssText = "display:flex;flex-direction:column;gap:10px";

      const divider = document.createElement("div");
      divider.style.cssText = `height:1px;background:${DIVIDER};margin:0 -18px`;
      anchorSection.appendChild(divider);

      const tObj = objectsByName.get(name);
      const ud = tObj?.userData || {};
      const ab = (ud.anchorsBySolid && typeof ud.anchorsBySolid === "object") ? ud.anchorsBySolid : null;
      const tSolids = ab ? Object.keys(ab) : [ud.solidName || "solid_0"];
      targetSolid = tSolids[0] || null;

      anchorSection.appendChild(mkSectionLabel("Anchor on: " + name));

      const solidSel2 = mkSolidSelect(tSolids, targetSolid, s => { targetSolid = s; rebuildAnchorList(); });
      if (solidSel2) anchorSection.appendChild(solidSel2);

      let aListWrap = null;
      function rebuildAnchorList() {
        if (aListWrap) aListWrap.remove();
        let aObj = {};
        if (ab && targetSolid && ab[targetSolid]) aObj = ab[targetSolid];
        else if (ud.anchors && typeof ud.anchors === "object") aObj = ud.anchors;
        else if (ab) aObj = ab[tSolids[0]] || {};
        const names = Object.keys(aObj).sort((a,b) => a.localeCompare(b, undefined, {numeric:true}));
        aListWrap = mkSearchList(names, anchor => {
          try { if (window.builderState.pending) window.builderState.pending.parentSolid = targetSolid; } catch(_) {}
          try { handleAnchorPick(targetName, anchor); } catch(e) { console.error(e); }
          closeAttachModal();
        }, "Search anchors…");
        anchorSection.appendChild(aListWrap);
        setTimeout(() => { try { aListWrap._input.focus(); } catch(_) {} }, 0);
      }
      rebuildAnchorList();

      body.appendChild(anchorSection);
    }

    modal.appendChild(body);

    const footer = document.createElement("div");
    Object.assign(footer.style, { padding: "12px 18px", borderTop: th.panelBord, display: "flex", justifyContent: "space-between", flexShrink: "0", background: th.panelBg });
    const backBtn = mkBtn("← Back");
    backBtn.onclick = () => {
      window.builderState.mode = "IDLE";
      try { clearAnchors(); } catch(_) {}
      renderStep1();
    };
    const cancelBtn = mkBtn("Cancel");
    cancelBtn.onclick = doCancel;
    footer.appendChild(backBtn);
    footer.appendChild(cancelBtn);
    modal.appendChild(footer);
  }

  document.body.appendChild(modal);
  renderStep1();
}

// Legacy alias so any remaining call sites still work
function openChildAnchorMenu(childName, anchorsBySolid) {
  openAttachModal(childName, anchorsBySolid);
}

function closeTargetObjectPickPanel() { closeAttachModal(); }

function openTargetObjectPickPanel(childName) {
  // Legacy: now handled by openAttachModal step 2.
  // This is kept for any direct 3D-click-triggered mid-flow calls.
  // In that case, the modal should already be open from step 1.
  // If somehow called standalone (e.g. from moveSelected fallback), show step 2.
  if (!document.getElementById("attachModal")) {
    const meta = window.builderState?.components?.[childName];
    const obj = window.objectsByName?.get(childName);
    const anchorsBySolid = obj?.userData?.anchorsBySolid || (obj?.userData?.anchors ? { [obj.userData.solidName||"solid_0"]: obj.userData.anchors } : {});
    openAttachModal(childName, anchorsBySolid, { op: "move" });
    return;
  }
  // Modal already open: no-op (step 2 renders itself when child anchor is picked)
}



function placePlateRelative(name, spec, lastName, dx, dy) {
  // place pose relative by offsetting from last plate pose
  const lastObj = objectsByName.get(lastName);
  let basePose = [0,0,0,0,0,0];
  if (lastObj && lastObj.position) {
    basePose = [lastObj.position.x, lastObj.position.y, lastObj.position.z, 0,0,0];
  }
  spec.pose = [basePose[0] + dx, basePose[1] + dy, basePose[2], 0,0,0];

  if (isOccupiedPlatePose(spec.pose[0], spec.pose[1], spec.pose[2])) {
    showToast("That spot already has a fixture plate.");
    return;
  }

  // record attach (for config generation)
  const attach = {
    parent_name: lastName,
    parent_solid: "fixture_plate",
    parent_anchor: "center",
    child_solid: "fixture_plate",
    child_anchor: "center",
    offset: [dx, dy, 0, 0, 0, 0]
  };
  spec.builder = { attach };

  try { socket.emit("upstream_update", { [name]: spec }); } catch (e) {}
  try { upsertObject(name, spec); } catch (e) {}

  window.builderState.lastFixturePlate = name;
  window.builderState.components[name] = { type: "fixture_plate", attach };
  window.builderState.placedOrder.push(name);
  showToast("Placed fixture plate relative to last.");
}

function isFixturePlateName(n) {
  return typeof n === "string" && n.startsWith("fixture_plate_");
}

function isOccupiedPlatePose(x, y, z=0) {
  const EPS = 1e-6;
  for (const [n, meta] of Object.entries(window.builderState.components)) {
    if (!meta || meta.type !== "fixture_plate") continue;
    const obj = objectsByName.get(n);
    if (!obj) continue;
    if (Math.abs(obj.position.x - x) < EPS &&
        Math.abs(obj.position.y - y) < EPS &&
        Math.abs(obj.position.z - z) < EPS) return true;
  }
  return false;
}

const __hiddenObjects = new Set();

function __toggleObjectVisibility(name) {
  const obj = objectsByName.get(name);
  if (!obj) return;
  // Hidden = the root is INVISIBLE — no ghost, no wireframe, and it
  // covers content that streams in later (GLB callbacks, box viz)
  // because children inherit root visibility.
  if (__hiddenObjects.has(name)) {
    __hiddenObjects.delete(name);
    obj.visible = true;
    obj.traverse(o => { if (o.isMesh) pickableMeshes.add(o); });
  } else {
    __hiddenObjects.add(name);
    obj.visible = false;
    obj.traverse(o => { if (o.isMesh) pickableMeshes.delete(o); });
  }
  markDirty();
  updateObjectList();
}

function __showAllHidden() {
  for (const name of [...__hiddenObjects]) {
    __toggleObjectVisibility(name);
  }
}


let __lastScrolledSel = null;

function updateObjectList() {
  const list = document.getElementById("sbObjectList");
  if (!list) return;
  const searchEl = document.getElementById("sbObjectSearch");
  const filter = (searchEl?.value || "").trim().toLowerCase();

  // Show/hide the "Show all hidden" button with count
  const showAllBtn = document.getElementById("sbShowAll");
  if (showAllBtn) {
    const n = __hiddenObjects.size;
    showAllBtn.style.display = n ? "" : "none";
    showAllBtn.textContent = `Show all hidden (${n})`;
    if (!showAllBtn.__wired) {
      showAllBtn.__wired = true;
      showAllBtn.addEventListener("click", __showAllHidden);
    }
  }
  const comps = window.builderState?.components || {};
  const names = Object.keys(comps).sort((a, b) => a.localeCompare(b, undefined, { numeric: true })).filter(n => !filter || n.toLowerCase().includes(filter) || (comps[n]?.type || "").toLowerCase().includes(filter));
  if (!names.length) {
    list.innerHTML = '<div class="sb-empty">' + (filter ? "No matches" : "No components yet") + '</div>';
    return;
  }
  const selected = window.builderState?.selectedName;
  list.innerHTML = "";
  for (const name of names) {
    const isHidden = __hiddenObjects.has(name);

    const item = document.createElement("div");
    item.className = "sb-object-item" + (name === selected ? " active" : "");
    if (isHidden) item.style.opacity = "0.4";

    const label = document.createElement("span");
    label.className = "sb-object-label";
    label.textContent = name;
    label.title = name;

    const eyeBtn = document.createElement("button");
    eyeBtn.className = "sb-eye-btn" + (isHidden ? " is-hidden" : "");
    eyeBtn.title = isHidden ? "Show" : "Hide";
    eyeBtn.innerHTML = isHidden
      ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/></svg>'
      : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
    eyeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      __toggleObjectVisibility(name);
    });

    item.appendChild(label);
    item.appendChild(eyeBtn);
    item.addEventListener("click", () => {
      try { setSelected(name); } catch(e) {}
      // Mirror a viewport click: show the object's anchors + info bar,
      // so picking from the list behaves like clicking the 3D object.
      try {
        const node = objectsByName.get(name);
        if (node && window.__buildAnchorsFor) window.__buildAnchorsFor(node);
      } catch(e) {}
    });
    list.appendChild(item);
  }

  // When the selection came from a viewport click, bring its row into
  // view — only on selection CHANGE, so routine re-renders never yank
  // the scroll position around.
  if (selected && selected !== __lastScrolledSel) {
    __lastScrolledSel = selected;
    const act = list.querySelector(".sb-object-item.active");
    if (act) act.scrollIntoView({ block: "nearest" });
  }
}
window.updateObjectList = updateObjectList;

// Wire up search input (bind once, retry if element not ready)
(function __wireObjectSearch() {
  const el = document.getElementById("sbObjectSearch");
  if (el && !el.__wired) {
    el.__wired = true;
    el.addEventListener("input", () => updateObjectList());
  } else if (!el) {
    setTimeout(__wireObjectSearch, 100);
  }
})();

function updateSidebarProps(name) {
  const nameEl = document.getElementById("sbPropName");
  const typeEl = document.getElementById("sbPropType");
  if (!name) return;
  if (nameEl) nameEl.textContent = name;
  if (typeEl) {
    const meta = window.builderState?.components?.[name];
    typeEl.textContent = meta?.type || "—";
  }
}

// ── Collision-box viz rebuild ──────────────────────────────────────────────
// The builder renders a collision_box as a small GLB handle plus a
// procedural transparent box sized from meta.size. After a size edit
// (or undo of one), swap that box for one with the new dimensions.
function __rebuildColBoxViz(name) {
  const obj = objectsByName.get(name);
  const meta = window.builderState?.components?.[name];
  if (!obj || !meta || meta.type !== "collision_box") return;
  const sz = Array.isArray(meta.size) ? meta.size : [100, 100, 100];
  const old = obj.getObjectByName("__colBoxViz__");
  const parent = old ? old.parent : obj;
  if (old) {
    // Free GPU buffers — a drag rebuilds this many times per second.
    old.traverse(o => {
      try { if (o.geometry) o.geometry.dispose(); } catch (e) {}
      try { if (o.material) o.material.dispose(); } catch (e) {}
    });
    parent.remove(old);
  }
  const boxMesh = new THREE.Mesh(
    new THREE.BoxGeometry(sz[0] || 1, sz[1] || 1, sz[2] || 1),
    new THREE.MeshBasicMaterial({
      color: 0xaaaaaa, transparent: true, opacity: 0.06,
      depthWrite: false, side: THREE.DoubleSide,
    })
  );
  boxMesh.position.set(0, 0, (sz[2] || 1) / 2);
  const vizGroup = new THREE.Group();
  vizGroup.name = "__colBoxViz__";
  vizGroup.userData.__colBoxViz = true;
  vizGroup.add(boxMesh);
  try { if (window.__addEdgeOverlay) window.__addEdgeOverlay(vizGroup); } catch (e) {}
  parent.add(vizGroup);
  try { if (window.__colBoxHandlesUpdate) window.__colBoxHandlesUpdate(); } catch (e) {}
  try { markDirty(); } catch (e) {}
}

// ── Rename a component everywhere ──────────────────────────────────────────
// One rename touches every trace: the components map (insertion order kept —
// it is the config output order), all attach/capParent/patternParent
// references, specs, the scene object + name maps, hidden set, selection,
// and the server-side cache (tombstone the old key, re-upsert the new).
// Undo/redo stacks reference names by string, so they are cleared rather
// than left pointing at a name that no longer exists.
function renameComponent(oldName, newName) {
  const bs = window.builderState;
  newName = (newName || "").trim();
  if (!newName || newName === oldName) return false;
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(newName)) {
    showToast("Invalid name — letters/digits/underscore, no leading digit");
    return false;
  }
  if (bs.components[newName] || objectsByName.has(newName)) {
    showToast("Name already in use: " + newName);
    return false;
  }
  if (!bs.components[oldName]) return false;

  // components map, order preserved
  const rebuilt = {};
  for (const [k, v] of Object.entries(bs.components)) rebuilt[k === oldName ? newName : k] = v;
  bs.components = rebuilt;

  // every reference
  for (const meta of Object.values(bs.components)) {
    if (!meta) continue;
    if (meta.attach?.parent_name === oldName) meta.attach.parent_name = newName;
    if (meta.capParent === oldName) meta.capParent = newName;
    if (meta.patternParent === oldName) meta.patternParent = newName;
  }

  // specs cache
  if (bs.specs && bs.specs[oldName]) { bs.specs[newName] = bs.specs[oldName]; delete bs.specs[oldName]; }

  // scene object + name maps
  const obj = objectsByName.get(oldName);
  if (obj) {
    objectsByName.delete(oldName);
    objectsByName.set(newName, obj);
    obj.name = newName;
    obj.traverse(o => {
      if (o.userData && o.userData.componentName === oldName) o.userData.componentName = newName;
    });
  }

  // hidden set + live selection state
  if (__hiddenObjects.has(oldName)) { __hiddenObjects.delete(oldName); __hiddenObjects.add(newName); }
  if (bs.selectedName === oldName) bs.selectedName = newName;
  if (bs.targetName === oldName) bs.targetName = newName;
  if (bs.pending?.name === oldName) bs.pending.name = newName;

  // undo entries hold the old name — drop them instead of corrupting
  bs.undoStack.length = 0;
  bs.redoStack.length = 0;

  // server cache: old key becomes a tombstone, new key re-upserted
  try {
    if (window.socket && socket?.emit) {
      const payload = { [oldName]: { delete: true } };
      if (obj) {
        const rod = window.quaternionToRodriguesDeg ? window.quaternionToRodriguesDeg(obj.quaternion) : [0, 0, 0];
        const upsert = { pose: [obj.position.x, obj.position.y, obj.position.z, rod[0] || 0, rod[1] || 0, rod[2] || 0] };
        if (bs.components[newName]?.attach) upsert.builder = { attach: bs.components[newName].attach };
        payload[newName] = upsert;
      }
      socket.emit("upstream_update", payload);
    }
  } catch (e) {}

  updateObjectList();
  updateSidebarProps(newName);
  try { if (window.__updateConfigPreview) window.__updateConfigPreview(); } catch (e) {}
  showToast(`Renamed ${oldName} → ${newName}`);
  return true;
}

// Rename affordance: double-click the Name value in the Selection panel.
(function __wireRename() {
  const el = document.getElementById("sbPropName");
  if (!el) { setTimeout(__wireRename, 200); return; }
  if (el.__renameWired) return;
  el.__renameWired = true;
  el.style.cursor = "pointer";
  el.title = "Double-click to rename";
  el.addEventListener("dblclick", async () => {
    const oldName = window.builderState?.selectedName;
    if (!oldName) return;
    const next = await __sbTextModal({
      title: "Rename component",
      label: "Component name",
      value: oldName,
      placeholder: oldName,
      okLabel: "Rename",
      hint: "All references (attaches, caps, patterns) follow the new name.",
    });
    if (next === null) return;
    renameComponent(oldName, next);
  });
})();

function setSelected(name) {
  window.builderState.selectedName = name;

  // Selection can come from multi-solid meshes. Prefer builderState meta, but fall back to scene userdata.
  const meta = (name && window.builderState.components[name]) ? window.builderState.components[name] : null;
  const sceneObj = name ? objectsByName.get(name) : null;
  const typeName = (meta && meta.type) ? meta.type : (sceneObj?.userData?.typeName || "");
  window.builderState.selectedTypeName = typeName || "";

  updateObjectList();
  updateSidebarProps(name);
  try { if (window.__builderUpdateHideBtn) window.__builderUpdateHideBtn(); } catch(e) {}
  try { if (window.__builderUpdateSizeRow) window.__builderUpdateSizeRow(name); } catch(e) {}

  const enabled = !!(name && (meta || sceneObj));
  if (window.__builderSetActionsEnabled) window.__builderSetActionsEnabled(enabled);

  try {
    const isPlate = (typeName && typeName.startsWith("plate_"));
    if (window.__builderSetPatternEnabled) window.__builderSetPatternEnabled(!!(enabled && isPlate));
  } catch (e) {}

  // Rectangular pattern: enabled for any selected component (anchored or free).
  try {
    // Rectangular pattern does NOT depend on current selection; it starts a workflow
    // where the user picks the source object/anchor next. Keep it available even
    // when nothing is selected.
    if (window.__builderSetRectPatternEnabled) window.__builderSetRectPatternEnabled(true);
  } catch (e) {}
}


function __collectDescendants(rootName) {
  // Returns a Set of descendant names (supports .has() and .length via .size).
  const out = new Set();
  const q = [rootName];
  const seen = new Set([rootName]);
  while (q.length) {
    const cur = q.shift();
    for (const [n, meta] of Object.entries(window.builderState.components || {})) {
      if (!meta || seen.has(n)) continue;
      const parentName = meta.attach?.parent_name || meta.patternParent || null;
      if (parentName === cur) {
        seen.add(n); out.add(n); q.push(n);
      }
    }
  }
  return out;
}


function __deleteComponentByName(name) {
  if (!name) return;

  // Undo: treat delete as an atomic action (can be undone/redone)
  try {
    const st = window.builderState;
    const specSnap = __deepClone(st.specs?.[name] || null);
    const metaSnap = __deepClone(st.components?.[name] || null);
    if (specSnap) {
      __pushUndo({ kind: "delete", names: [name], specs: { [name]: specSnap }, metas: { [name]: metaSnap } });
    }
  } catch(e) {}

  // remove anchors UI if showing for this object
  try { clearAnchors(); } catch(e) {}

  // broadcast deletion (and let upsertObject handle scene disposal)
  try { __applyDelete(name); } catch(e) {}

  // remove from builder state
  try { delete window.builderState.components[name]; } catch(e) {}
  try { delete window.builderState.specs[name]; } catch(e) {}
  try {
    window.builderState.placedOrder = (window.builderState.placedOrder || []).filter(n => n !== name);
    if (window.builderState.lastFixturePlate === name) {
      const rev = [...window.builderState.placedOrder].reverse();
      window.builderState.lastFixturePlate = rev.find(n => window.builderState.components[n]?.type === "fixture_plate") || null;
    }
  } catch(e) {}
}


function removeSelected() {
  const name = window.builderState.selectedName;
  if (!name) return;

  const children = __collectDescendants(name);
  if (!children.size) {
    __deleteComponentByName(name);
    setSelected(null);
    showToast("Removed " + name);
    return;
  }

  // Confirmation modal: delete parent only, or delete parent + children.
  const th = _sbPanelTheme();
  const old = document.getElementById("deleteConfirmMenu");
  if (old) old.remove();

  const overlay = document.createElement("div");
  overlay.id = "deleteConfirmMenu";
  overlay.style.position = "fixed";
  overlay.style.inset = "0";
  overlay.style.background = "rgba(0,0,0,0.45)";
  overlay.style.zIndex = "10006";
  overlay.style.display = "flex";
  overlay.style.alignItems = "center";
  overlay.style.justifyContent = "center";
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const card = document.createElement("div");
  card.style.cssText = `width:min(480px,94vw);max-height:min(86vh,640px);overflow:auto;background:${th.panelBg};border-radius:16px;padding:24px;box-shadow:0 18px 46px rgba(0,0,0,0.5);font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;color:${th.color};animation:attachModalIn 0.2s cubic-bezier(0.34,1.56,0.64,1) forwards`;

  const title = document.createElement("div");
  title.textContent = "Delete object?";
  title.style.fontWeight = "900";
  title.style.fontSize = "18px";
  title.style.marginBottom = "6px";

  const msg = document.createElement("div");
  msg.textContent = `"${name}" has ${children.size} child object(s) anchored to it.`;
  msg.style.cssText = `font-size:13px;color:var(--panel-dim);margin-bottom:16px`;

  const row = document.createElement("label");
  row.style.display = "flex";
  row.style.alignItems = "center";
  row.style.gap = "10px";
  row.style.padding = "10px 12px";
  row.style.borderRadius = "14px";
  row.style.border = "1px solid rgba(248,81,73,0.25)";
  row.style.background = "rgba(255,0,0,0.10)";
  row.style.cursor = "pointer";
  row.style.userSelect = "none";

  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = true;

  const cbText = document.createElement("div");
  const childArr = [...children];
  cbText.innerHTML = `<div style="font-weight:800;">Delete child objects too</div>
                      <div style="font-size:12px;opacity:.75;">This will delete ${children.size} dependent object(s).</div>`;

  row.appendChild(cb);
  row.appendChild(cbText);

  const list = document.createElement("div");
  list.style.cssText = `margin-top:10px;font-size:12px;opacity:0.7;max-height:140px;overflow:auto;border:${th.listBord};border-radius:10px;padding:10px 12px;background:${th.listBg};font-family:ui-monospace,monospace`;
  list.textContent = childArr.join(", ");

  const actions = document.createElement("div");
  actions.style.display = "flex";
  actions.style.justifyContent = "flex-end";
  actions.style.gap = "10px";
  actions.style.marginTop = "14px";

  const cancel = document.createElement("button");
  cancel.className = "btn btn-ghost";
  cancel.textContent = "Cancel";
  cancel.onclick = () => overlay.remove();

  const del = document.createElement("button");
  del.className = "btn btn-danger";
  del.textContent = "Delete";
  del.onclick = () => {
    const deleteChildren = !!cb.checked;

    const namesToDelete = [name].concat(deleteChildren ? childArr : []);
    const specs = {};
    const metas = {};
    for (const n of namesToDelete) {
      try { specs[n] = __deepClone(window.builderState.specs?.[n] || null); } catch(e) {}
      try { metas[n] = __deepClone(window.builderState.components?.[n] || null); } catch(e) {}
    }

    const __prevSuspend = !!window.builderState.suspendUndo;
    window.builderState.suspendUndo = true;

    if (!deleteChildren) {
      // If keeping children, detach them so they don't reference a deleted parent.
      for (const childName of childArr) {
        const meta = window.builderState.components[childName];
        if (meta && meta.attach && meta.attach.parent_name === name) {
          delete meta.attach;
          const obj = objectsByName.get(childName);
          if (obj && obj.userData) delete obj.userData.builderAttach;
          try { socket.emit("upstream_update", { [childName]: { builder: { attach: null } } }); } catch(e) {}
        }
      }
    }

    for (const n of (deleteChildren ? childArr : [])) {
      try { __applyDelete(n); } catch(e) {}
      try { delete window.builderState.components[n]; delete window.builderState.specs[n]; } catch(e) {}
    }
    try { __applyDelete(name); } catch(e) {}
    try { delete window.builderState.components[name]; delete window.builderState.specs[name]; } catch(e) {}

    window.builderState.suspendUndo = __prevSuspend;

    try {
      __pushUndo({ kind: "delete", names: namesToDelete, specs, metas });
    } catch(e) {}

    setSelected(null);
    overlay.remove();
    showToast(deleteChildren ? `Removed ${name} (+${children.size} child)` : `Removed ${name}`);
  };

  actions.appendChild(cancel);
  actions.appendChild(del);

  card.appendChild(title);
  card.appendChild(msg);
  card.appendChild(row);
  card.appendChild(list);
  card.appendChild(actions);

  overlay.appendChild(card);
  document.body.appendChild(overlay);
}

// Allow deleting/cancelling unanchored spawns during the pick flow.
// - Delete/Backspace: delete selected object, or cancel pending spawn if nothing selected
// - Escape: cancel pending spawn (if still unanchored)
document.addEventListener("keydown", (e) => {
  try {
    const tag = (document.activeElement && document.activeElement.tagName) ? document.activeElement.tagName.toLowerCase() : "";
    if (tag === "input" || tag === "textarea" || tag === "select") return;

    // Undo/Redo (Ctrl/Cmd+Z, Ctrl/Cmd+Y, Ctrl/Cmd+Shift+Z)
    const key = String(e.key || "").toLowerCase();
    const mod = (e.ctrlKey || e.metaKey);
    if (mod && key === "z") {
      if (e.shiftKey) window.__redo?.(); else window.__undo?.();
      e.preventDefault();
      return;
    }
    if (mod && key === "y") {
      window.__redo?.();
      e.preventDefault();
      return;
    }

    const k = e.key;
    const isDel = (k === "Delete" || k === "Backspace");
    const isEsc = (k === "Escape");
    if (!isDel && !isEsc) return;

    // Cancel Auto Z pick mode (just exits Z pick, keeps panel open)
    if (isEsc && window.builderState.mode === "COLLISIONBOX_PICK_Z_OBJ") {
      window.builderState.mode = "IDLE";
      window.builderState._colBoxZCallback = null;
      showToast("Auto Z cancelled.");
      e.preventDefault();
      return;
    }

    // Cancel collision box tool
    if (isEsc && window.builderState.mode === "COLLISIONBOX_PICK_TARGET") {
      try { __closeCollisionBoxPanel(); } catch(e) {}
      try { clearAnchors(); } catch(e) {}
      showToast("Cancelled collision box.");
      e.preventDefault();
      return;
    }

    // Escape: close any open modals first
    if (isEsc) {
      const bg = document.getElementById("builderModalBg");
      if (bg) { bg.remove(); e.preventDefault(); return; }
      const cp = document.getElementById("createPanel");
      if (cp) { cp.remove(); e.preventDefault(); return; }
      const am = document.getElementById("attachModal");
      if (am) { try { closeAttachModal(); } catch(_) {} e.preventDefault(); return; }
      const dc = document.getElementById("deleteConfirmMenu");
      if (dc) { dc.remove(); e.preventDefault(); return; }
    }

    const pendingName = window.builderState?.pending?.name || null;
    if (isEsc && pendingName) {
      const meta = window.builderState.components?.[pendingName];
      const isAnchored = !!(meta && meta.attach);
      if (!isAnchored) {
        __deleteComponentByName(pendingName);
        window.builderState.pending = null;
        window.builderState.mode = "IDLE";
        setSelected(null);
        showToast(`Cancelled ${pendingName}`);
      } else {
        window.builderState.pending = null;
        window.builderState.mode = "IDLE";
      }
      e.preventDefault();
      return;
    }

    if (isDel) {
      // If we're mid-attach (spawned a child but haven't finished picking anchors),
      // always allow deleting that pending child even if another object is selected.
      if (pendingName) {
        const meta = window.builderState.components?.[pendingName];
        const isAnchored = !!(meta && meta.attach);
        if (!isAnchored) {
          __deleteComponentByName(pendingName);
          window.builderState.pending = null;
          window.builderState.mode = "IDLE";
          setSelected(null);
          clearAnchors();
          showToast(`Cancelled ${pendingName}`);
          e.preventDefault();
          return;
        }
      }

      if (window.builderState?.selectedName) {
        removeSelected();
        e.preventDefault();
        return;
      }
    }

    // Escape with nothing pending: deselect
    if (isEsc && window.builderState?.selectedName) {
      setSelected(null);
      try { clearAnchors(); } catch(_) {}
      e.preventDefault();
      return;
    }
  } catch (err) {
    try { console.warn(err); } catch(_) {}
  }
});


function getPoseABC(meta) {
  // returns [rx,ry,rz] in degrees (axis-angle Rodrigues)
  if (!meta) return [0,0,0];
  if (meta.attach && Array.isArray(meta.attach.offset)) {
    const o = meta.attach.offset;
    return [o[3]||0, o[4]||0, o[5]||0];
  }
  // Free-standing: use meta.offset[3..5]; fall back to legacy poseABC/poseYaw
  if (Array.isArray(meta.offset) && meta.offset.length >= 6) {
    return [meta.offset[3]||0, meta.offset[4]||0, meta.offset[5]||0];
  }
  // Legacy migration
  if (Array.isArray(meta.poseABC)) return [meta.poseABC[0]||0, meta.poseABC[1]||0, meta.poseABC[2]||0];
  return [0, 0, meta.poseYaw||0];
}

// Round degree values: snap to integer if within 1e-6, otherwise round to 4 decimals.
function __snapDeg(v) {
  const r = Math.round(v);
  return Math.abs(v - r) < 1e-4 ? r : Math.round(v * 10000) / 10000;
}

function setPoseABC(meta, abc) {
  if (!meta) return;
  const s = [__snapDeg(abc[0]), __snapDeg(abc[1]), __snapDeg(abc[2])];
  if (meta.attach) {
    if (!Array.isArray(meta.attach.offset)) meta.attach.offset = [0,0,0,0,0,0];
    meta.attach.offset[3]=s[0]; meta.attach.offset[4]=s[1]; meta.attach.offset[5]=s[2];
  } else {
    // Free-standing: store rotation in meta.offset[3..5]
    if (!Array.isArray(meta.offset) || meta.offset.length < 6) meta.offset = [0,0,0,0,0,0];
    meta.offset[3]=s[0]; meta.offset[4]=s[1]; meta.offset[5]=s[2];
    // Remove legacy keys if present
    delete meta.poseABC;
    delete meta.poseYaw;
  }
}

// Recompute poses for any objects anchored to `rootName` (and recursively those anchored to them).
// This mirrors simulation behavior where rotating/moving a parent updates the entire anchored subtree.
function __propagateAnchoredSubtree(rootName) {
  const comps = window.builderState?.components || {};
  const visited = new Set();

  function walk(parentName) {
    if (!parentName || visited.has(parentName)) return;
    visited.add(parentName);

    for (const [childName, meta] of Object.entries(comps)) {
      const at = meta?.attach;
      if (!at) continue;
      if (at.parent_name !== parentName) continue;

      try {
        __snapChildToParentAnchor(
          childName,
          at.parent_name,
          at.parent_anchor,
          at.child_solid,
          at.child_anchor,
          Array.isArray(at.offset) ? at.offset : [0,0,0,0,0,0],
          at.parent_solid
        );
      } catch (e) {
        console.warn("propagate: resnap failed for", childName, e);
        continue;
      }

      // recurse
      walk(childName);
    }
  }

  walk(rootName);
}


function rotateSelected(deg) {
  const name = window.builderState.selectedName;
  if (!name) return;
  const obj = objectsByName.get(name);
  if (!obj) return;

  const meta = window.builderState.components[name];
  if (!meta) return;

  // Undo: capture state before
  const __prevMeta = __deepClone(meta || null);
  const __prevPose = (function(){
    try {
      const r = window.quaternionToRodriguesDeg ? window.quaternionToRodriguesDeg(obj.quaternion) : [0,0,0];
      return [obj.position.x, obj.position.y, obj.position.z, r[0]||0, r[1]||0, r[2]||0];
    } catch (e) { return null; }
  })();

  const __applyUndoSnapshot = () => {
    try {
      const __nextMeta = __deepClone(window.builderState.components[name] || null);
      const r2 = window.quaternionToRodriguesDeg ? window.quaternionToRodriguesDeg(obj.quaternion) : [0,0,0];
      const __nextPose = [obj.position.x, obj.position.y, obj.position.z, r2[0]||0, r2[1]||0, r2[2]||0];
      __pushUndo({ kind: "transform", name, prevMeta: __prevMeta, prevPose: __prevPose, nextMeta: __nextMeta, nextPose: __nextPose });
    } catch(e) {}
  };

  // Anchored: update attach offset + resnap
  if (meta.attach) {
    if (!Array.isArray(meta.attach.offset)) meta.attach.offset = [0,0,0,0,0,0];

    // NOTE: attach.offset[3..5] is a Rodrigues rotation vector in degrees
    // (axis * angle). Compose rotations via quaternions.
    try {
      const qOff = rodriguesDegToQuaternion(meta.attach.offset[3]||0, meta.attach.offset[4]||0, meta.attach.offset[5]||0);
      const qDelta = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,0,1), (deg*Math.PI)/180); // anchor-frame Z
      const qNew = qOff.clone().multiply(qDelta); // post-multiply => rotate in anchor frame
      const rod = window.quaternionToRodriguesDeg ? window.quaternionToRodriguesDeg(qNew) : [0,0,0];
      meta.attach.offset[3] = __snapDeg(rod[0]||0); meta.attach.offset[4] = __snapDeg(rod[1]||0); meta.attach.offset[5] = __snapDeg(rod[2]||0);
    } catch (e) {
      // fallback to legacy behavior if something goes wrong
      meta.attach.offset[5] = (meta.attach.offset[5] || 0) + deg;
    }
try {
      __snapChildToParentAnchor(
        name,
        meta.attach.parent_name,
        meta.attach.parent_anchor,
        meta.attach.child_solid,
        meta.attach.child_anchor,
        meta.attach.offset,
        meta.attach.parent_solid
      );
    } catch (e) {
      console.warn("rotate: resnap failed", e);
      showToast("Rotate failed (see console)");
      // rollback meta offset on failure
      try { window.builderState.components[name] = __prevMeta; } catch(_) {}
      return;
    }

    try { __propagateAnchoredSubtree(name); } catch (e) {}
    __applyUndoSnapshot();
    showToast("Rotated " + name);
    return;
  }

  // Free: rotate around its own origin
  obj.rotateZ((deg * Math.PI) / 180);
  const abc = getPoseABC(meta);
  abc[2] = (abc[2] || 0) + deg;
  setPoseABC(meta, abc);

  try { __propagateAnchoredSubtree(name); } catch (e) {}

  if (window.socket && socket?.emit) {
    socket.emit("upstream_update", {
      [name]: { pose: [obj.position.x, obj.position.y, obj.position.z, abc[0], abc[1], abc[2]] }
    });
  }

  __applyUndoSnapshot();
  showToast("Rotated " + name);
}



function flipSelected() {
  const name = window.builderState.selectedName;
  if (!name) return;
  const obj = objectsByName.get(name);
  if (!obj) return;

  const meta = window.builderState.components[name];
  if (!meta) return;

  // Undo: capture state before
  const __prevMeta = __deepClone(meta || null);
  const __prevPose = (function(){
    try {
      const r = window.quaternionToRodriguesDeg ? window.quaternionToRodriguesDeg(obj.quaternion) : [0,0,0];
      return [obj.position.x, obj.position.y, obj.position.z, r[0]||0, r[1]||0, r[2]||0];
    } catch (e) { return null; }
  })();

  const __applyUndoSnapshot = () => {
    try {
      const __nextMeta = __deepClone(window.builderState.components[name] || null);
      const r2 = window.quaternionToRodriguesDeg ? window.quaternionToRodriguesDeg(obj.quaternion) : [0,0,0];
      const __nextPose = [obj.position.x, obj.position.y, obj.position.z, r2[0]||0, r2[1]||0, r2[2]||0];
      __pushUndo({ kind: "transform", name, prevMeta: __prevMeta, prevPose: __prevPose, nextMeta: __nextMeta, nextPose: __nextPose });
    } catch(e) {}
  };

  // Anchored: flip about anchor frame
  if (meta.attach) {
    if (!Array.isArray(meta.attach.offset)) meta.attach.offset = [0,0,0,0,0,0];

    // NOTE: attach.offset[3..5] is a Rodrigues rotation vector in degrees
    // (axis * angle). Compose the flip (180° about anchor-frame X) via quaternions.
    try {
      const qOff = rodriguesDegToQuaternion(meta.attach.offset[3]||0, meta.attach.offset[4]||0, meta.attach.offset[5]||0);
      const qFlip = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0), Math.PI); // anchor-frame X
      const qNew = qOff.clone().multiply(qFlip);
      const rod = window.quaternionToRodriguesDeg ? window.quaternionToRodriguesDeg(qNew) : [0,0,0];
      meta.attach.offset[3] = __snapDeg(rod[0]||0); meta.attach.offset[4] = __snapDeg(rod[1]||0); meta.attach.offset[5] = __snapDeg(rod[2]||0);
    } catch (e) {
      meta.attach.offset[3] = (meta.attach.offset[3] || 0) + 180;
    }
try {
      __snapChildToParentAnchor(
        name,
        meta.attach.parent_name,
        meta.attach.parent_anchor,
        meta.attach.child_solid,
        meta.attach.child_anchor,
        meta.attach.offset,
        meta.attach.parent_solid
      );
    } catch (e) {
      console.warn("flip: resnap failed", e);
      showToast("Flip failed (see console)");
      try { window.builderState.components[name] = __prevMeta; } catch(_) {}
      return;
    }
    try { __propagateAnchoredSubtree(name); } catch (e) {}
    __applyUndoSnapshot();
    showToast("Flipped " + name);
    return;
  }

  // Free: flip about its own origin
  obj.rotateX(Math.PI);

  const abc = getPoseABC(meta);
  abc[0] = (abc[0] || 0) + 180;
  setPoseABC(meta, abc);

  try { __propagateAnchoredSubtree(name); } catch (e) {}

  if (window.socket && socket?.emit) {
    socket.emit("upstream_update", {
      [name]: { pose: [obj.position.x, obj.position.y, obj.position.z, abc[0], abc[1], abc[2]] }
    });
  }

  __applyUndoSnapshot();
  showToast("Flipped " + name);
}

function flipSelectedAxis(axis) {
  const name = window.builderState.selectedName;
  if (!name) return;
  const obj = objectsByName.get(name);
  if (!obj) return;

  const meta = window.builderState.components[name];
  if (!meta) return;

  // Undo: capture state before
  const __prevMeta = __deepClone(meta || null);
  const __prevPose = (function(){
    try {
      const r = window.quaternionToRodriguesDeg ? window.quaternionToRodriguesDeg(obj.quaternion) : [0,0,0];
      return [obj.position.x, obj.position.y, obj.position.z, r[0]||0, r[1]||0, r[2]||0];
    } catch (e) { return null; }
  })();

  const __applyUndoSnapshot = () => {
    try {
      const __nextMeta = __deepClone(window.builderState.components[name] || null);
      const r2 = window.quaternionToRodriguesDeg ? window.quaternionToRodriguesDeg(obj.quaternion) : [0,0,0];
      const __nextPose = [obj.position.x, obj.position.y, obj.position.z, r2[0]||0, r2[1]||0, r2[2]||0];
      __pushUndo({ kind: "transform", name, prevMeta: __prevMeta, prevPose: __prevPose, nextMeta: __nextMeta, nextPose: __nextPose });
    } catch(e) {}
  };

  const axisVec = axis === "x" ? new THREE.Vector3(1,0,0) : axis === "y" ? new THREE.Vector3(0,1,0) : new THREE.Vector3(0,0,1);
  const deg90 = Math.PI / 2;

  // Anchored: flip about anchor frame
  if (meta.attach) {
    if (!Array.isArray(meta.attach.offset)) meta.attach.offset = [0,0,0,0,0,0];

    try {
      const qOff = rodriguesDegToQuaternion(meta.attach.offset[3]||0, meta.attach.offset[4]||0, meta.attach.offset[5]||0);
      const qFlip = new THREE.Quaternion().setFromAxisAngle(axisVec, deg90);
      const qNew = qOff.clone().multiply(qFlip);
      const rod = window.quaternionToRodriguesDeg ? window.quaternionToRodriguesDeg(qNew) : [0,0,0];
      meta.attach.offset[3] = __snapDeg(rod[0]||0); meta.attach.offset[4] = __snapDeg(rod[1]||0); meta.attach.offset[5] = __snapDeg(rod[2]||0);
    } catch (e) {
      // fallback
      if (axis === "x") meta.attach.offset[3] = (meta.attach.offset[3] || 0) + 90;
      else if (axis === "y") meta.attach.offset[4] = (meta.attach.offset[4] || 0) + 90;
      else meta.attach.offset[5] = (meta.attach.offset[5] || 0) + 90;
    }
    try {
      __snapChildToParentAnchor(
        name,
        meta.attach.parent_name,
        meta.attach.parent_anchor,
        meta.attach.child_solid,
        meta.attach.child_anchor,
        meta.attach.offset,
        meta.attach.parent_solid
      );
    } catch (e) {
      console.warn("flip axis: resnap failed", e);
      showToast("Flip failed (see console)");
      try { window.builderState.components[name] = __prevMeta; } catch(_) {}
      return;
    }
    try { __propagateAnchoredSubtree(name); } catch (e) {}
    __applyUndoSnapshot();
    showToast("Flipped " + name + " (" + axis.toUpperCase() + " 90°)");
    return;
  }

  // Free: flip about its own local axis via quaternion composition
  const __freeAbc = getPoseABC(meta);
  const qCur  = rodriguesDegToQuaternion(__freeAbc[0]||0, __freeAbc[1]||0, __freeAbc[2]||0);
  const qFlip = new THREE.Quaternion().setFromAxisAngle(axisVec, deg90);
  const qNew  = qCur.clone().multiply(qFlip);
  const rod   = window.quaternionToRodriguesDeg ? window.quaternionToRodriguesDeg(qNew) : [0,0,0];
  const newAbc = [__snapDeg(rod[0]||0), __snapDeg(rod[1]||0), __snapDeg(rod[2]||0)];
  obj.quaternion.copy(qNew);
  setPoseABC(meta, newAbc);

  try { __propagateAnchoredSubtree(name); } catch (e) {}

  if (window.socket && socket?.emit) {
    socket.emit("upstream_update", {
      [name]: { pose: [obj.position.x, obj.position.y, obj.position.z, newAbc[0], newAbc[1], newAbc[2]] }
    });
  }

  __applyUndoSnapshot();
  showToast("Flipped " + name + " (" + axis.toUpperCase() + " 90°)");
}


// ── Nudge: re-anchor the selected item to the NEXT parent anchor in a
//    parent-local direction (X±/Y±/Z±) — "move to the next hole". Works
//    on any anchor grid: fixture plates (A1..), adapters (hole_0..),
//    hotels (vertical levels via Z±). The candidate set is filtered to
//    anchors named like the current one (A9 → letter+number only,
//    hole_1 → hole_* only) so plate edges/corners are never picked. ──
function nudgeSelectedAnchor(axis, sign) {
  const name = window.builderState.selectedName;
  if (!name) return;
  const obj = objectsByName.get(name);
  const meta = window.builderState.components[name];
  if (!obj || !meta) return;
  if (!meta.attach || !meta.attach.parent_name || !meta.attach.parent_anchor) {
    showToast("Move: only anchored items can step between holes");
    return;
  }
  const parentObj = objectsByName.get(meta.attach.parent_name);
  if (!parentObj) { showToast("Move: parent object not found"); return; }

  // Parent anchors — prefer the attach's solid, else flat, else first solid.
  const ud = parentObj.userData || {};
  const ab = (ud.anchorsBySolid && typeof ud.anchorsBySolid === "object") ? ud.anchorsBySolid : null;
  let anchors = null;
  const solidKey = meta.attach.parent_solid;
  if (ab && solidKey && ab[solidKey]) anchors = ab[solidKey];
  else if (ud.anchors && Object.keys(ud.anchors).length) anchors = ud.anchors;
  else if (ab) anchors = Object.values(ab)[0];
  if (!anchors) { showToast("Move: parent has no anchors"); return; }

  const curName = meta.attach.parent_anchor;
  const cur = anchors[curName];
  if (!cur) { showToast(`Move: anchor ${curName} not found on parent`); return; }

  // Only consider anchors named like the current one — keeps the walk on
  // the same grid family (A9 stays on letter+number, hole_1 on hole_*).
  const pat = /^[A-Za-z]+\d+$/.test(curName) && !/^hole/i.test(curName) ? /^[A-Za-z]+\d+$/
            : /^hole[_-]?\d+$/i.test(curName) ? /^hole[_-]?\d+$/i
            : null;

  const ai = axis === "x" ? 0 : axis === "y" ? 1 : 2;
  let bestScore = null, bestKey = null;
  for (const [k, arr] of Object.entries(anchors)) {
    if (k === curName || !Array.isArray(arr) || arr.length < 3) continue;
    if (pat && !pat.test(k)) continue;
    const d = [arr[0] - cur[0], arr[1] - cur[1], arr[2] - cur[2]];
    const along = d[ai] * sign;
    if (along < 1e-6) continue;                                   // wrong side
    const lateral = Math.hypot(d[(ai + 1) % 3], d[(ai + 2) % 3]);
    if (lateral > along * 0.35 + 1e-6) continue;                  // off-row
    const score = along + lateral * 4;                            // nearest in-line
    if (bestScore === null || score < bestScore) { bestScore = score; bestKey = k; }
  }
  if (!bestKey) {
    showToast(`Move: no anchor ${axis.toUpperCase()}${sign > 0 ? "+" : "−"} of ${curName}`);
    return;
  }

  // Undo capture (same shape as flipSelectedAxis).
  const __prevMeta = __deepClone(meta || null);
  const __prevPose = (function () {
    try {
      const r = window.quaternionToRodriguesDeg ? window.quaternionToRodriguesDeg(obj.quaternion) : [0, 0, 0];
      return [obj.position.x, obj.position.y, obj.position.z, r[0] || 0, r[1] || 0, r[2] || 0];
    } catch (e) { return null; }
  })();

  meta.attach.parent_anchor = bestKey;
  try {
    __snapChildToParentAnchor(
      name,
      meta.attach.parent_name,
      bestKey,
      meta.attach.child_solid,
      meta.attach.child_anchor,
      meta.attach.offset,
      meta.attach.parent_solid
    );
  } catch (e) {
    console.warn("nudge: resnap failed", e);
    showToast("Move failed (see console)");
    try { window.builderState.components[name] = __prevMeta; } catch (_) {}
    return;
  }
  try { __propagateAnchoredSubtree(name); } catch (e) {}

  try {
    const __nextMeta = __deepClone(window.builderState.components[name] || null);
    const r2 = window.quaternionToRodriguesDeg ? window.quaternionToRodriguesDeg(obj.quaternion) : [0, 0, 0];
    const __nextPose = [obj.position.x, obj.position.y, obj.position.z, r2[0] || 0, r2[1] || 0, r2[2] || 0];
    __pushUndo({ kind: "transform", name, prevMeta: __prevMeta, prevPose: __prevPose, nextMeta: __nextMeta, nextPose: __nextPose });
  } catch (e) {}

  try { if (window.__updateConfigPreview) window.__updateConfigPreview(); } catch (e) {}
  showToast(`${name}: ${curName} → ${bestKey}`);
}


function moveSelected() {
  const name = window.builderState.selectedName;
  if (!name) return;
  const obj = objectsByName.get(name);
  if (!obj) return;

  const meta = window.builderState.components?.[name] || {};
  const type = meta.type || obj.userData?.typeName || obj.userData?.type || "unknown";

  // Prefer full anchorsBySolid (multi-solid components like toolchanger/grippers),
  // fall back to legacy obj.userData.anchors.
  const __abRaw = (obj.userData?.anchorsBySolid && typeof obj.userData.anchorsBySolid === "object") ? obj.userData.anchorsBySolid : null;
  const ab = (__abRaw && Object.keys(__abRaw).length) ? __abRaw : null;
  const solids = ab ? Object.keys(ab) : [obj.userData?.solidName || "solid_0"];
  let currentSolid = (obj.userData?.solidName && solids.includes(obj.userData.solidName)) ? obj.userData.solidName : (solids[0] || null);

  const getAnchorsForSolid = (solidKey) => {
    if (ab && solidKey && ab[solidKey]) return ab[solidKey];
    return obj.userData?.anchors || {};
  };

  const hasAny = solids.some(s => Object.keys(getAnchorsForSolid(s) || {}).length);
  if (!hasAny) {
    showToast("No anchors available on " + name);
    return;
  }

  // Build anchorsBySolid map expected by openAttachModal
  const anchorsBySolid = {};
  for (const s of solids) anchorsBySolid[s] = getAnchorsForSolid(s) || {};
  openAttachModal(name, anchorsBySolid, { op: "move" });
}



// Thumbnails: single shared WebGL renderer, JPEG for localStorage (small), PNG in memory (crisp).
// Fixes Chrome's ~16 WebGL context limit and ~5MB localStorage quota.
const THUMB_SIZE = 256;
const THUMB_VERSION = "edge_v7";
const _thumbCache = new Map();

// Shared offscreen renderer (1 WebGL context for all thumbnails)
let _thumbRenderer = null;
let _thumbIdleTimer = null;
const THUMB_IDLE_MS = 30000;

function _releaseThumbRenderer() {
  if (!_thumbRenderer) return;
  try {
    _thumbRenderer.forceContextLoss();   // hand the context back NOW
    _thumbRenderer.dispose();
  } catch (_) {}
  _thumbRenderer = null;
}
let _thumbCanvas = null;
function _getThumbRenderer() {
  // Idle release: this renderer only draws catalog icons, but a WebGL
  // context it keeps alive counts against the browser's per-process cap
  // (~16) — shared with the main viewer, the view cube, and every other
  // open tab. Past the cap the browser refuses new contexts outright.
  // It is already lazy, so dropping it when idle costs nothing but a
  // re-create on the next icon.
  clearTimeout(_thumbIdleTimer);
  _thumbIdleTimer = setTimeout(_releaseThumbRenderer, THUMB_IDLE_MS);
  if (_thumbRenderer) return _thumbRenderer;
  _thumbCanvas = document.createElement("canvas");
  _thumbRenderer = new THREE.WebGLRenderer({ canvas: _thumbCanvas, antialias: true, preserveDrawingBuffer: true });
  _thumbRenderer.setPixelRatio(1);
  _thumbRenderer.setSize(THUMB_SIZE, THUMB_SIZE, false);
  _thumbRenderer.outputColorSpace = THREE.SRGBColorSpace;
  return _thumbRenderer;
}

// Shared scene setup for edge-style thumbnails
function _prepareThumbModel(glbScene) {
  const meshes = [];
  glbScene.traverse((n) => { if (n.isMesh) meshes.push(n); });
  for (const m of meshes) {
    m.material = new THREE.MeshPhongMaterial({ color: new THREE.Color("#e8e8e8"), specular: new THREE.Color("#ffffff"), shininess: 15 });
    try {
      const edges = new THREE.EdgesGeometry(m.geometry, 75);
      const lineSegs = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color: 0x333333, linewidth: 1 }));
      lineSegs.name = "__edge";
      lineSegs.raycast = () => {};
      lineSegs.position.copy(m.position);
      lineSegs.rotation.copy(m.rotation);
      lineSegs.scale.copy(m.scale);
      m.parent.add(lineSegs);
    } catch (e) {}
  }
}

function _getCleanBounds(root) {
  root.updateMatrixWorld(true);
  const box = new THREE.Box3();
  box.makeEmpty();
  root.traverse((n) => {
    if (!n.isMesh || !n.geometry) return;
    const nm = (n.name || "").toLowerCase();
    if (nm === "__edge" || nm.includes("collider") || nm.includes("collision") || nm.includes("helper")) return;
    const g = n.geometry;
    if (!g.boundingBox) g.computeBoundingBox();
    if (!g.boundingBox) return;
    const b = g.boundingBox.clone();
    b.applyMatrix4(n.matrixWorld);
    box.union(b);
  });
  if (box.isEmpty()) box.setFromObject(root);
  return box;
}

// Core render: load model, apply edges, render from a given camera direction
// Returns a data URL. Serializes via a queue so only 1 render runs at a time.
let _thumbQueue = Promise.resolve();

function _renderThumbFromDir(modelKey, glbUrl, dirArr) {
  const dirKey = dirArr.map(v => v.toFixed(2)).join(",");
  const key = "builder_thumb_" + THUMB_VERSION + "_" + modelKey + "_" + dirKey;

  // Check memory cache
  if (_thumbCache.has(key)) return Promise.resolve(_thumbCache.get(key));
  // Check localStorage
  try {
    const cached = localStorage.getItem(key);
    if (cached && cached.startsWith("data:image/")) { _thumbCache.set(key, cached); return Promise.resolve(cached); }
  } catch (e) {}

  // Queue the render so we never have >1 context active
  _thumbQueue = _thumbQueue.then(async () => {
    // Re-check after waiting in queue (might have been rendered by earlier queue item)
    if (_thumbCache.has(key)) return _thumbCache.get(key);

    try {
      const obj = await _loadThumbModel(modelKey, glbUrl);
      const r = _getThumbRenderer();
      const scn = new THREE.Scene();
      scn.background = new THREE.Color("#ffffff");
      scn.add(new THREE.AmbientLight(0xffffff, 0.95));
      const kL = new THREE.DirectionalLight(0xffffff, 0.5);
      kL.position.set(3, 4, 5);
      scn.add(kL);

      _prepareThumbModel(obj);
      scn.add(obj);

      // Center
      obj.updateMatrixWorld(true);
      const rawBox = _getCleanBounds(obj);
      const center = new THREE.Vector3();
      rawBox.getCenter(center);
      obj.position.sub(center);
      obj.updateMatrixWorld(true);

      const box = _getCleanBounds(obj);
      const sz = new THREE.Vector3();
      box.getSize(sz);
      const maxDim = Math.max(sz.x, sz.y, sz.z) || 1;
      const pad = maxDim * 0.15;
      const halfW = (maxDim / 2) + pad;

      const cam = new THREE.OrthographicCamera(-halfW, halfW, halfW, -halfW, 0.01, maxDim * 20);
      cam.up.set(0, 0, 1);
      const cd = new THREE.Vector3(dirArr[0], dirArr[1], dirArr[2]).normalize();
      cam.position.copy(cd.clone().multiplyScalar(maxDim * 3));
      cam.lookAt(0, 0, 0);
      cam.updateMatrixWorld(true);
      cam.updateProjectionMatrix();

      r.setClearColor(0xffffff, 1);
      r.render(scn, cam);

      // PNG in memory (crisp), JPEG in localStorage (small ~15-30KB vs ~200-400KB)
      const pngUrl = _thumbCanvas.toDataURL("image/png");
      _thumbCache.set(key, pngUrl);
      try {
        const jpgUrl = _thumbCanvas.toDataURL("image/jpeg", 0.7);
        localStorage.setItem(key, jpgUrl);
      } catch (e) {}

      // Cleanup Three.js objects to free memory
      scn.traverse((n) => {
        if (n.isMesh) {
          if (n.geometry) n.geometry.dispose();
          if (n.material) {
            const mats = Array.isArray(n.material) ? n.material : [n.material];
            mats.forEach(mt => { if (mt && mt.dispose) mt.dispose(); });
          }
        }
      });

      return pngUrl;
    } catch (e) {
      return null;
    }
  });

  return _thumbQueue;
}

// Multi-part component types that need /api/instantiate for proper assembly poses
const _thumbMultiPart = new Set(["core"]);

// Load a GLB (or fetch blueprint for multi-part components) into a single scene root
async function _loadThumbModel(modelKey, glbUrl) {
  if (_thumbMultiPart.has(modelKey)) {
    // Fetch the blueprint to get proper world poses for each solid
    try {
      const res = await fetch(SB_API + "/instantiate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: modelKey, options: {} })
      });
      const js = await res.json();
      if (js && js.ok && js.blueprint && Array.isArray(js.blueprint.solids)) {
        const root = new THREE.Group();
        const solids = js.blueprint.solids.filter(s => s && s.glb);
        const loads = solids.map(s => new Promise((resolve) => {
          makeGltfLoader().load(s.glb, (gltf) => resolve({ scene: gltf.scene, pose: s.pose || [0,0,0,0,0,0] }), undefined, () => resolve(null));
        }));
        const results = await Promise.all(loads);
        for (const r of results) {
          if (!r) continue;
          const holder = new THREE.Group();
          const [x, y, z, rx, ry, rz] = r.pose;
          holder.position.set(x, y, z);
          holder.quaternion.copy(rodriguesDegToQuaternion(rx, ry, rz));
          holder.add(r.scene);
          root.add(holder);
        }
        return root;
      }
    } catch (e) {
      console.warn("_loadThumbModel: instantiate failed for", modelKey, e);
    }
  }
  // Single GLB fallback
  return new Promise((resolve, reject) => {
    makeGltfLoader().load(glbUrl, (gltf) => resolve(gltf.scene), undefined, reject);
  });
}

async function getOrCreateThumbnail(modelKey, glbUrl) {
  return _renderThumbFromDir(modelKey, glbUrl, [1, 0.8, 1]);
}

async function renderThumbnailFromAngle(modelKey, glbUrl, dirArr) {
  return _renderThumbFromDir(modelKey, glbUrl, dirArr);
}

function openInsertMenu() {
  // Icon tile menu (Fusion-ish) - centered on screen with search + categories
  const _isLight = (document.documentElement.getAttribute("data-theme") || "dark") === "light";

  let bg = document.getElementById("builderModalBg");
  if (bg) bg.remove();
  bg = document.createElement("div");
  bg.id = "builderModalBg";
  // ``modal-overlay`` → covers the navbar (opts out of the nav's
  // sibling margin-shift, see vendor/nav.css).
  bg.className = "modal-overlay";
  bg.style.position = "fixed";
  bg.style.inset = "0";
  bg.style.background = _isLight ? "rgba(0,0,0,0.20)" : "rgba(0,0,0,0.35)";
  bg.style.zIndex = "9998";
  bg.style.display = "flex";
  bg.style.alignItems = "center";
  bg.style.justifyContent = "center";
  bg.addEventListener("click", (e) => { if (e.target === bg) bg.remove(); });

  const box = document.createElement("div");
  box.className = "sb-insert-box";

  const header = document.createElement("div");
  header.className = "sb-insert-title";
  header.textContent = "Insert Component";
  box.appendChild(header);

  // Search input with icon
  const searchWrap = document.createElement("div");
  searchWrap.className = "sb-insert-search-wrap";
  searchWrap.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`;
  const searchInput = document.createElement("input");
  searchInput.className = "sb-insert-search";
  searchInput.type = "text";
  searchInput.placeholder = "Search components…";
  searchWrap.appendChild(searchInput);
  box.appendChild(searchWrap);

  // Scrollable content area
  const scrollArea = document.createElement("div");
  scrollArea.style.overflowY = "auto";
  scrollArea.style.overflowX = "hidden";
  scrollArea.style.flex = "1";
  scrollArea.style.paddingRight = "4px";
  box.appendChild(scrollArea);

  // Server-driven categories (populated by /api/categories fetch below)
  let _serverCategories = [];

  function categorize(items) {
    const groups = {};
    const assigned = new Set();
    for (const cat of _serverCategories) {
      const matches = items.filter(t => !assigned.has(t) && cat.items.includes(t));
      if (matches.length) {
        groups[cat.name] = matches;
        matches.forEach(t => assigned.add(t));
      }
    }
    const leftover = items.filter(t => !assigned.has(t));
    if (leftover.length) groups["other"] = leftover;
    return groups;
  }

  const mkTile = ({name, iconSvg, onClick}) => {
    const t = document.createElement("button");
    t.className = "sb-tile";
    t.dataset.componentType = name;
    t.addEventListener("click", () => { bg.remove(); onClick(); });

    const icon = document.createElement("div");
    icon.innerHTML = iconSvg;
    icon.style.cssText = "width:40px;height:40px;display:grid;place-items:center";

    const lbl = document.createElement("div");
    lbl.className = "sb-tile-label";
    lbl.textContent = displayName(name);

    t.appendChild(icon);
    t.appendChild(lbl);
    return t;
  };


  function mkThumbIcon(modelKey, glbUrl) {
    const wrap = document.createElement("div");
    wrap.style.width = "96px";
    wrap.style.height = "96px";
    wrap.style.borderRadius = "12px";
    wrap.style.background = "var(--surface3)";
    wrap.style.display = "flex";
    wrap.style.alignItems = "center";
    wrap.style.justifyContent = "center";
    wrap.style.overflow = "hidden";

    const img = document.createElement("img");
    img.alt = modelKey;
    img.style.width = "100%";
    img.style.height = "100%";
    img.style.objectFit = "contain";
    img.style.imageRendering = "auto";
    wrap.appendChild(img);

    // placeholder while generating
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(`
      <svg xmlns="http://www.w3.org/2000/svg" width="44" height="44">
        <rect x="0" y="0" width="44" height="44" rx="12" fill="rgba(0,0,0,0.04)"/>
        <path d="M22 10a12 12 0 1 0 0.01 0" fill="none" stroke="rgba(0,0,0,0.25)" stroke-width="3" stroke-linecap="round"/>
      </svg>`);

    getOrCreateThumbnail(modelKey, glbUrl).then((url) => { img.src = url; }).catch(() => {});
    return wrap;
  }

  // Populate catalog automatically from /api/catalog (scans static/CAD/*.glb)
  let allTiles = [];
  let allCategoryEls = [];

  function renderCatalog(filterText) {
    scrollArea.innerHTML = "";
    const q = (filterText || "").trim().toLowerCase();
    const filteredItems = allTiles.filter(t => !q || t.type.toLowerCase().includes(q));
    const filteredTypes = filteredItems.map(t => t.type);

    if (!filteredTypes.length) {
      const empty = document.createElement("div");
      empty.textContent = "No components match your search.";
      empty.style.padding = "20px";
      empty.style.opacity = "0.6";
      empty.style.textAlign = "center";
      scrollArea.appendChild(empty);
      return;
    }

    const groups = categorize(filteredTypes);
    for (const [catName, types] of Object.entries(groups)) {
      const catHeader = document.createElement("div");
      catHeader.className = "sb-cat-header";
      catHeader.textContent = catName;
      scrollArea.appendChild(catHeader);

      const grid = document.createElement("div");
      grid.style.display = "grid";
      grid.style.gridTemplateColumns = "repeat(3, minmax(0, 1fr))";
      grid.style.gap = "10px";
      grid.style.marginBottom = "8px";
      scrollArea.appendChild(grid);

      for (const type of types) {
        const entry = allTiles.find(t => t.type === type);
        if (entry && entry.tile) {
          grid.appendChild(entry.tile);
        }
      }
    }
  }

  (async () => {
    let items = [];
    try {
      const res = await fetch(SB_API + "/categories", { cache: "no-store" });
      const js = await res.json();
      if (js && js.ok && Array.isArray(js.categories) && js.categories.length) {
        _serverCategories = js.categories;
        items = js.categories.flatMap(c => c.items);
      }
    } catch (e) {}
    if (!items.length) {
      try {
        const res = await fetch(SB_API + "/catalog", { cache: "no-store" });
        const js = await res.json();
        if (js && js.ok && Array.isArray(js.items)) items = js.items;
      } catch (e) {}
    }
    if (!items.length) items = ["fixture_plate", "sbs_adapter"];

    items = items.slice().sort((a,b) => {
      if (a === "fixture_plate") return -1;
      if (b === "fixture_plate") return 1;
      return a.localeCompare(b);
    });

    for (const type of items) {
      const glb = "/static/CAD/" + type + ".glb";
      const tile = mkTile({ name: type, iconSvg: "", onClick: () => openCreatePanel(type) });
      tile.querySelector("div").replaceWith(mkThumbIcon(type, glb));
      allTiles.push({ type, tile });
    }

    renderCatalog("");
  })();

  searchInput.addEventListener("input", () => renderCatalog(searchInput.value));
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { bg.remove(); }
  });

  bg.appendChild(box);
  document.body.appendChild(bg);
  setTimeout(() => { try { searchInput.focus(); } catch(_) {} }, 0);
}

// Simple "Create" panel.
// (We can auto-detect options later; for now, this guarantees spawning works again.)
// Format internal type names for display: "gripper_4_finger" → "Gripper 4 Finger"
function displayName(s) {
  return String(s).split("_").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

async function openCreatePanel(typeName) {
  const old = document.getElementById("createPanel");
  if (old) old.remove();
  const _isLight = (document.documentElement.getAttribute("data-theme") || "dark") === "light";
  const _cardBg   = _isLight ? "#ffffff"  : "#0d1117";
  const _cardClr  = _isLight ? "#1f2328"  : "#e6edf3";
  const _rowBg    = _isLight ? "#f6f8fa"  : "#161b22";
  const _rowBord  = _isLight ? "1px solid rgba(0,0,0,0.08)"  : "1px solid rgba(255,255,255,0.08)";
  const _inputBg  = _isLight ? "#ffffff"  : "#1a2535";
  const _inputBord= _isLight ? "1px solid rgba(0,0,0,0.12)" : "1px solid rgba(255,255,255,0.12)";
  const _cancelBg = _isLight ? "#f6f6f6"  : "#1e2430";
  const _cancelBord= _isLight ? "1px solid rgba(0,0,0,0.12)" : "1px solid rgba(255,255,255,0.12)";

  const isCollisionBox = (typeName === "collision_box");

  // fetch metadata (anchors + options + glb guess)
  let meta = { type: typeName, options: [], anchors: {}, glb: null };
  try {
    const res = await fetch(SB_API + "/type_meta?type=" + encodeURIComponent(typeName), { cache: "no-store" });
    const js = await res.json();
    if (js && js.ok && js.meta) meta = js.meta;
  } catch (e) {}

  // --- Auto-detect matching cap for tube types ---
  let __capType = null;   // e.g. "cap_autosampler_2ml"
  let __capMeta = null;
  if (String(typeName).startsWith("tube_")) {
    const candidate = "cap_" + typeName.slice(5);
    try {
      const cr = await fetch(SB_API + "/type_meta?type=" + encodeURIComponent(candidate), { cache: "no-store" });
      const cj = await cr.json();
      if (cj && cj.ok && cj.meta) {
        // Check tube has "place" anchor and cap has "center" anchor (in any solid)
        const tubeHasPlace = Object.values(meta.anchors || {}).some(a => a && a["place"]);
        const capHasCenter = Object.values(cj.meta.anchors || {}).some(a => a && a["center"]);
        if (tubeHasPlace && capHasCenter) {
          __capType = candidate;
          __capMeta = cj.meta;
        }
      }
    } catch (e) {}
  }

  const panel = document.createElement("div");
  panel.id = "createPanel";
  panel.style.cssText = "position:fixed;inset:0;background:rgba(10,10,10,0.65);z-index:10002;display:flex;align-items:center;justify-content:center";
  panel.addEventListener("click", (e) => { if (e.target === panel) panel.remove(); });

  const card = document.createElement("div");
  card.style.width = "min(780px, 92vw)";
  card.style.maxHeight = "min(86vh, 820px)";
  card.style.overflow = "auto";
  card.style.background = _cardBg;
  card.style.borderRadius = "18px";
  card.style.padding = "20px";
  card.style.boxShadow = _isLight ? "0 18px 46px rgba(0,0,0,0.12)" : "0 18px 46px rgba(0,0,0,0.55)";
  card.style.fontFamily = "system-ui, -apple-system, Segoe UI, Roboto, Arial";
  card.style.color = _cardClr;
  card.style.animation = "attachModalIn 0.2s cubic-bezier(0.34,1.56,0.64,1) forwards";

  const title = document.createElement("div");
  title.textContent = "Add: " + displayName(typeName);
  title.style.fontWeight = "800";
  title.style.fontSize = "18px";
  title.style.marginBottom = "6px";

  const hint = document.createElement("div");
  hint.textContent = "Set options (if any), then click Add.";
  hint.style.opacity = "0.7";
  hint.style.fontSize = "13px";
  hint.style.marginBottom = "14px";

  // Instance name field (editable)
  const nameRow = document.createElement("div");
  nameRow.style.display = "flex";
  nameRow.style.alignItems = "center";
  nameRow.style.gap = "10px";
  nameRow.style.marginBottom = "14px";
  nameRow.style.padding = "10px 12px";
  nameRow.style.border = _rowBord;
  nameRow.style.borderRadius = "12px";
  nameRow.style.background = _rowBg;

  const nameLabel = document.createElement("div");
  nameLabel.textContent = "Name";
  nameLabel.style.fontSize = "13px";
  nameLabel.style.fontWeight = "650";
  nameLabel.style.opacity = "0.9";
  nameLabel.style.whiteSpace = "nowrap";

  // Compute default name
  function __computeDefaultName(type) {
    if (!window.builderState.next[type]) window.builderState.next[type] = 1;
    let i = window.builderState.next[type];
    function __candidate(t, idx) {
      if (t === "core") return (idx === 1) ? "core" : `core_${idx}`;
      return `${t}_${idx}`;
    }
    let name = __candidate(type, i);
    while (window.builderState.components && window.builderState.components[name]) {
      i++;
      name = __candidate(type, i);
    }
    return name;
  }

  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.value = __computeDefaultName(typeName);
  _sbStyleInput(nameInput);
  nameInput.style.flex = "1";
  nameInput.style.fontFamily = "var(--mono)";

  nameRow.appendChild(nameLabel);
  nameRow.appendChild(nameInput);

  // options form (auto)
  const form = document.createElement("div");
  form.style.display = "grid";
  form.style.gridTemplateColumns = "1fr 1fr";
  form.style.gap = "10px 14px";
  form.style.marginBottom = "14px";

  const fields = {};
  const optsAll = Array.isArray(meta.options) ? meta.options : [];
  // Do not surface internal toggles in the Builder UI.
  // `simulation` is always True; `has_motion_plan` is always True for core.
  // `has_rail` is replaced by a rail dropdown for core.
  const hiddenOpts = ["simulation"];
  const isCore = String(typeName || "").toLowerCase() === "core";
  if (isCore) { hiddenOpts.push("has_motion_plan"); hiddenOpts.push("has_rail"); }
  const opts = optsAll.filter(o => o && o.kind === "bool" && !hiddenOpts.includes(String(o.name || o.key || "")));

  // --- Rail dropdown for core components ---
  let __railSelect = null;
  if (isCore) {
    const railWrap = document.createElement("div");
    railWrap.style.display = "flex";
    railWrap.style.alignItems = "center";
    railWrap.style.justifyContent = "space-between";
    railWrap.style.gap = "10px";
    railWrap.style.border = _rowBord;
    railWrap.style.borderRadius = "12px";
    railWrap.style.padding = "10px 12px";
    railWrap.style.background = _rowBg;
    railWrap.style.gridColumn = "1 / -1";

    const railLab = document.createElement("div");
    railLab.textContent = "Rail";
    railLab.style.fontSize = "13px";
    railLab.style.fontWeight = "650";
    railLab.style.opacity = "0.9";

    __railSelect = document.createElement("select");
    _sbStyleInput(__railSelect);

    // Start with "No Rail" + loading placeholder
    const noRailOpt = document.createElement("option");
    noRailOpt.value = "none";
    noRailOpt.textContent = "No Rail";
    __railSelect.appendChild(noRailOpt);

    // Fetch available rails from server and populate dropdown
    (async () => {
      try {
        const res = await fetch(SB_API + "/rails");
        const data = await res.json();
        if (data.ok && Array.isArray(data.rails)) {
          for (const r of data.rails) {
            const o = document.createElement("option");
            o.value = r.type;
            o.textContent = r.label;
            __railSelect.appendChild(o);
          }
          // Default to No Rail
        }
      } catch(e) { console.warn("Failed to fetch rails", e); }
    })();

    railWrap.appendChild(railLab);
    railWrap.appendChild(__railSelect);
    form.appendChild(railWrap);
  }

  if (!opts.length && !isCore) {
    const none = document.createElement("div");
    none.textContent = "";
    none.style.gridColumn = "1 / -1";
    none.style.opacity = "0.7";
    none.style.fontSize = "13px";
    form.appendChild(none);
  }
  for (const opt of opts) {
    const wrap = document.createElement("div");
    wrap.style.display = "flex";
    wrap.style.alignItems = "center";
    wrap.style.justifyContent = "space-between";
    wrap.style.gap = "10px";
    wrap.style.border = _rowBord;
    wrap.style.borderRadius = "12px";
    wrap.style.padding = "10px 12px";
    wrap.style.background = _rowBg;

    const lab = document.createElement("div");
    lab.textContent = opt.name;
    lab.style.fontSize = "13px";
    lab.style.fontWeight = "650";
    lab.style.opacity = "0.9";

    let input;
    if (opt.kind === "bool") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = !!opt.default;
      input.style.transform = "scale(1.25)";
    } else {
      input = document.createElement("input");
      input.type = "text";
      input.value = (opt.default === undefined || opt.default === null) ? "" : (typeof opt.default === "object" ? JSON.stringify(opt.default) : String(opt.default));
      _sbStyleInput(input);
      input.style.flex = "1";
    }
    fields[opt.name] = { opt, input };
    wrap.appendChild(lab);
    wrap.appendChild(input);
    form.appendChild(wrap);
  }

  // --- Size fields for collision_box ---
  let __sizeXInput = null, __sizeYInput = null, __sizeZInput = null;
  if (isCollisionBox) {
    const sizeLab = document.createElement("div");
    sizeLab.textContent = "SIZE (mm)";
    sizeLab.style.cssText = "grid-column:1/-1;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;opacity:0.5;margin-top:4px;";
    form.appendChild(sizeLab);
    for (const axis of ["X", "Y", "Z"]) {
      const wrap = document.createElement("div");
      wrap.style.display = "flex";
      wrap.style.alignItems = "center";
      wrap.style.justifyContent = "space-between";
      wrap.style.gap = "10px";
      wrap.style.border = _rowBord;
      wrap.style.borderRadius = "12px";
      wrap.style.padding = "10px 12px";
      wrap.style.background = _rowBg;
      const lab = document.createElement("div");
      lab.textContent = axis;
      lab.style.fontSize = "13px";
      lab.style.fontWeight = "650";
      lab.style.opacity = "0.9";
      const inp = document.createElement("input");
      inp.type = "number"; inp.value = "100"; inp.min = "1"; inp.step = "0.1";
      inp.style.cssText = `flex:1;padding:8px 10px;border-radius:10px;border:${_inputBord};background:${_inputBg};color:${_cardClr};font-size:13px;font-weight:600;`;
      wrap.appendChild(lab); wrap.appendChild(inp);
      form.appendChild(wrap);
      if (axis === "X") __sizeXInput = inp;
      else if (axis === "Y") __sizeYInput = inp;
      else __sizeZInput = inp;
    }
  }

  // --- has_cap checkbox (only for tube types with a matching cap) ---
  let __capCheckbox = null;
  if (__capType) {
    const capWrap = document.createElement("div");
    capWrap.style.display = "flex";
    capWrap.style.alignItems = "center";
    capWrap.style.justifyContent = "space-between";
    capWrap.style.gap = "10px";
    capWrap.style.border = "1px solid rgba(60,130,255,0.18)";
    capWrap.style.borderRadius = "12px";
    capWrap.style.padding = "10px 12px";
    capWrap.style.background = "rgba(60,130,255,0.04)";
    capWrap.style.marginBottom = "10px";

    const capLab = document.createElement("div");
    capLab.textContent = "has_cap";
    capLab.style.fontSize = "13px";
    capLab.style.fontWeight = "650";
    capLab.style.opacity = "0.9";

    __capCheckbox = document.createElement("input");
    __capCheckbox.type = "checkbox";
    __capCheckbox.checked = false;
    __capCheckbox.style.transform = "scale(1.25)";

    capWrap.appendChild(capLab);
    capWrap.appendChild(__capCheckbox);
    form.appendChild(capWrap);
  }

  // preview: 3 views (isometric, front, side) in a horizontal strip — skip for collision_box (no GLB)
  const prev = document.createElement("div");
  prev.style.display = "flex";
  prev.style.gap = "8px";
  prev.style.marginBottom = "12px";

  const viewAngles = [
    { label: "Isometric", dir: [1, 0.8, 1] },
    { label: "Front",     dir: [0, -1, 0.3] },
    { label: "Side",      dir: [1, 0, 0.3] },
  ];

  const previewImgs = [];
  for (const va of viewAngles) {
    const wrap = document.createElement("div");
    wrap.style.flex = "1";
    wrap.style.border = _rowBord;
    wrap.style.borderRadius = "12px";
    wrap.style.background = _rowBg;
    wrap.style.overflow = "hidden";
    wrap.style.position = "relative";

    const lbl = document.createElement("div");
    lbl.textContent = va.label;
    lbl.style.position = "absolute";
    lbl.style.top = "6px";
    lbl.style.left = "8px";
    lbl.style.fontSize = "10px";
    lbl.style.fontWeight = "600";
    lbl.style.opacity = "0.45";
    lbl.style.textTransform = "uppercase";
    lbl.style.letterSpacing = "0.3px";

    const img = document.createElement("img");
    img.style.width = "100%";
    img.style.height = "140px";
    img.style.objectFit = "contain";
    img.alt = va.label;

    wrap.appendChild(lbl);
    wrap.appendChild(img);
    prev.appendChild(wrap);
    previewImgs.push({ img, dir: va.dir, label: va.label });
  }

  // Render all 3 views after paint
  setTimeout(async () => {
    try {
      const glb = meta.glb || ("/static/CAD/" + typeName + ".glb");
      for (const pv of previewImgs) {
        try {
          pv.img.src = await renderThumbnailFromAngle(typeName, glb, pv.dir);
        } catch (e) {}
      }
    } catch (e) {}
  }, 0);

  const row = document.createElement("div");
  row.style.display = "flex";
  row.style.gap = "10px";
  row.style.justifyContent = "flex-end";

  const cancel = document.createElement("button");
  cancel.className = "btn btn-ghost";
  cancel.textContent = "Cancel";
  cancel.onclick = () => panel.remove();

  const create = document.createElement("button");
  create.className = "btn btn-primary";
  create.textContent = "Add";
  create.onclick = async () => {
    // collect values
    const optsOut = {};
    for (const k in fields) {
      const { opt, input } = fields[k];
      if (opt && opt.kind === "bool") optsOut[k] = !!input.checked;
    }
    // Collision box size
    if (isCollisionBox && __sizeXInput && __sizeYInput && __sizeZInput) {
      const _q = v => Math.round(v * 10) / 10;
      optsOut.size = [
        _q(parseFloat(__sizeXInput.value) || 100),
        _q(parseFloat(__sizeYInput.value) || 100),
        _q(parseFloat(__sizeZInput.value) || 100)
      ];
    }
    // Rail dropdown -> has_rail + rail_cfg
    if (__railSelect) {
      const railVal = __railSelect.value;
      if (railVal === "none") {
        optsOut.has_rail = false;
      } else {
        optsOut.has_rail = true;
        optsOut.rail_cfg = {
          type: railVal, axis: 6, offset: 0,
          usem: 1, pprm: 4000, tprm: 75,
          usee: 1, ppre: 4000, tpre: 75,
          p: 0.01, i: 0.0001, d: 0,
          duration: 100, threshold: 100
        };
      }
    }
    const wantCap = !!(__capCheckbox && __capCheckbox.checked && __capType);
    // Get custom name - pass directly into spawnComponent so it's used from the start
    const customName = (nameInput.value || "").trim();
    panel.remove();
    try {
      const tubeName = await spawnComponent(typeName, meta, optsOut, customName || null);

      // Auto-spawn cap if requested
      if (wantCap && tubeName) {
        try {
          const capName = await spawnComponentSilent(__capType);
          // Wait for cap object to appear in scene
          const t0 = performance.now();
          await new Promise((resolve) => {
            (function tick() {
              if (objectsByName.get(capName) || performance.now() - t0 > 6000) return resolve();
              requestAnimationFrame(tick);
            })();
          });
          // Snap cap center → tube place
          __snapChildToParentAnchor(capName, tubeName, "place", "body", "center", [0,0,0,0,0,0], "body");
          // Store attach metadata
          const capAttach = {
            parent_name: tubeName, parent_solid: "body", parent_anchor: "place",
            child_solid: "body", child_anchor: "center",
            offset: [0,0,0,0,0,0]
          };
          window.builderState.components[capName] = Object.assign(
            {}, window.builderState.components[capName] || {}, { attach: capAttach, capParent: tubeName }
          );
          const capObj = objectsByName.get(capName);
          if (capObj) capObj.userData.builderAttach = capAttach;
          socket.emit("upstream_update", { [capName]: { builder: { attach: capAttach } } });
        } catch (ce) { console.warn("Cap auto-spawn failed:", ce); }
      }

      try { if (window.__updateConfigPreview) window.__updateConfigPreview(); } catch(e) {}
    }
    catch (e) { console.error(e); showToast("Add failed: " + (e?.message||e)); }
  };

  row.appendChild(cancel);
  row.appendChild(create);

  card.appendChild(title);
  card.appendChild(hint);
  card.appendChild(nameRow);
  card.appendChild(prev);
  card.appendChild(form);
  card.appendChild(row);
  panel.appendChild(card);
  document.body.appendChild(panel);
}

	// Pick a valid solid key for a multi-solid component.
	// IMPORTANT: Never default to "solid_0" unless it actually exists; otherwise
	// configs can reference missing solids and crash Workspace() with KeyError.
	function __resolveSolidKey(obj, preferredSolid=null) {
	  const ud = obj?.userData || {};
	  const ab = (ud.anchorsBySolid && typeof ud.anchorsBySolid === "object") ? ud.anchorsBySolid : null;
	  const keys = ab ? Object.keys(ab) : [];
	  if (!keys.length) return (ud && ud.solidName) ? ud.solidName : null;

	  function pickFromWant(want) {
	    if (!want) return null;
	    if (ab[want]) return want;
	    const w = String(want).toLowerCase();
	    const exactCI = keys.find(k => String(k).toLowerCase() === w);
	    if (exactCI) return exactCI;
	    const contains = keys.find(k => String(k).toLowerCase().includes(w) || w.includes(String(k).toLowerCase()));
	    if (contains) return contains;
	    return null;
	  }

	  return (
	    pickFromWant(preferredSolid) ||
	    pickFromWant(ud.solidName) ||
	    (keys.includes("solid_0") ? "solid_0" : keys[0])
	  );
	}

	function handleAnchorPick(ownerName, anchorName, pickedSolidKey=null) {
  // Only meaningful during builder snapping
  if (window.builderState.mode !== "PICK_TARGET_ANCHOR" || !window.builderState.pending) return;
  if (!window.builderState.targetName || ownerName !== window.builderState.targetName) return;

  const childName = window.builderState.pending.name;
  const childObj = objectsByName.get(childName);
  const targetObj = objectsByName.get(ownerName);
  if (!childObj || !targetObj) return;

  // Undo support: capture state before we change anything
  const __prevMeta = __deepClone(window.builderState.components[childName] || null);
  const __prevPose = (function(){
    try {
      const r = window.quaternionToRodriguesDeg ? window.quaternionToRodriguesDeg(childObj.quaternion) : [0,0,0];
      return [childObj.position.x, childObj.position.y, childObj.position.z, r[0]||0, r[1]||0, r[2]||0];
    } catch (e) { return null; }
  })();

  // Some objects (notably plates) store anchors under anchorsBySolid instead of anchors.
  // IMPORTANT: honor the user-selected solid (e.g. tool changer) when snapping.
	  // Pick a valid solid key for a multi-solid component.
	  // IMPORTANT: Never default to "solid_0" unless it actually exists; otherwise
	  // configs can reference missing solids and crash Workspace() with KeyError.
	  function __resolveSolidKey(obj, preferredSolid=null) {
	    const ud = obj?.userData || {};
	    const ab = (ud.anchorsBySolid && typeof ud.anchorsBySolid === "object") ? ud.anchorsBySolid : null;
	    const keys = ab ? Object.keys(ab) : [];
	    if (!keys.length) return (ud && ud.solidName) ? ud.solidName : null;
	
	    function pickFromWant(want) {
	      if (!want) return null;
	      if (ab[want]) return want;
	      const w = String(want).toLowerCase();
	      const exactCI = keys.find(k => String(k).toLowerCase() === w);
	      if (exactCI) return exactCI;
	      const contains = keys.find(k => String(k).toLowerCase().includes(w) || w.includes(String(k).toLowerCase()));
	      if (contains) return contains;
	      return null;
	    }
	
	    return (
	      pickFromWant(preferredSolid) ||
	      pickFromWant(ud.solidName) ||
	      (keys.includes("solid_0") ? "solid_0" : keys[0])
	    );
	  }

	  function __getAnchorsForObj(obj, preferredSolid=null) {
    const ud = obj?.userData || {};
    const ab = (ud.anchorsBySolid && typeof ud.anchorsBySolid === "object") ? ud.anchorsBySolid : null;
    // If UI selected a solid, use that solid's anchors even if ud.anchors exists.
    if (preferredSolid && ab) {
      if (ab[preferredSolid]) return ab[preferredSolid];
      const want = String(preferredSolid).toLowerCase();
      const keys = Object.keys(ab);
      const exactCI = keys.find(k => String(k).toLowerCase() === want);
      if (exactCI && ab[exactCI]) return ab[exactCI];
      const contains = keys.find(k => String(k).toLowerCase().includes(want) || want.includes(String(k).toLowerCase()));
      if (contains && ab[contains]) return ab[contains];
    }
    if (ud.anchors && typeof ud.anchors === "object" && Object.keys(ud.anchors).length) return ud.anchors;
    if (!ab) return {};
    const solid = (ud.solidName && ab[ud.solidName]) ? ud.solidName : (ab["solid_0"] ? "solid_0" : Object.keys(ab)[0]);
    return (solid && ab[solid]) ? ab[solid] : {};
  }


  const childAnchors = __getAnchorsForObj(childObj, window.builderState.pending.childSolid);
  // If the user clicked a specific anchor that belongs to a particular solid within a
  // multi-solid assembly, honor that for the snap math.
  const __prefParentSolid = pickedSolidKey || window.builderState.pending.parentSolid || null;
  if (pickedSolidKey && window.builderState && window.builderState.pending) {
    try { window.builderState.pending.parentSolid = pickedSolidKey; } catch(_) {}
  }
  const targetAnchors = __getAnchorsForObj(targetObj, __prefParentSolid);
  const srcArr = childAnchors[window.builderState.pending.sourceAnchor];
  const dstArr = targetAnchors[anchorName];
  if (!srcArr || !dstArr) return;

  const srcPL = new THREE.Vector3(srcArr[0], srcArr[1], srcArr[2]);
  const srcQL = rodriguesDegToQuaternion(srcArr[3], srcArr[4], srcArr[5]);

  // If the chosen anchor belongs to a specific solid within a multi-solid assembly,
  // convert from that solid's local frame into the component root's local frame.
  function __solidAnchorToRoot(obj, solidKey, pLocal, qLocal) {
    try {
      if (!solidKey) return { p: pLocal, q: qLocal };
      const holder = obj.getObjectByName ? obj.getObjectByName(String(solidKey)) : null;
      if (!holder || holder === obj) return { p: pLocal, q: qLocal };
      // Position: holderLocal -> world -> objLocal
      const pW = holder.localToWorld(pLocal.clone());
      const pRoot = obj.worldToLocal(pW.clone());
      // Orientation: holderLocal -> world -> objLocal
      const hW = new THREE.Quaternion();
      const oW = new THREE.Quaternion();
      holder.getWorldQuaternion(hW);
      obj.getWorldQuaternion(oW);
      const qW = hW.clone().multiply(qLocal);
      const qRoot = oW.clone().invert().multiply(qW);
      return { p: pRoot, q: qRoot };
    } catch (e) {
      return { p: pLocal, q: qLocal };
    }
  }
  const __srcX = __solidAnchorToRoot(childObj, window.builderState.pending.childSolid, srcPL, srcQL);
  const srcPL_root = __srcX.p;
  const srcQL_root = __srcX.q;

  const dstPL = new THREE.Vector3(dstArr[0], dstArr[1], dstArr[2]);
  const dstQL = rodriguesDegToQuaternion(dstArr[3], dstArr[4], dstArr[5]);

  const __dstX = __solidAnchorToRoot(targetObj, __prefParentSolid || targetObj.userData?.solidName, dstPL, dstQL);
  const dstPL_root = __dstX.p;
  const dstQL_root = __dstX.q;

  const dstWorldPos = targetObj.localToWorld(dstPL_root.clone());
  const targetWorldQ = new THREE.Quaternion();
  targetObj.getWorldQuaternion(targetWorldQ);
  let dstWorldQ = targetWorldQ.clone().multiply(dstQL_root);

  // Moving to a new anchor must NOT wipe a user-set rotation: carry the
  // previous attach's rotation offset (offset[3..5], anchor-frame Rodrigues
  // degrees) into the new attach. Fresh items have no prior attach → [0,0,0].
  const __prevOff = (__prevMeta && __prevMeta.attach && Array.isArray(__prevMeta.attach.offset))
    ? __prevMeta.attach.offset : null;
  const __keepRot = [__prevOff?.[3] || 0, __prevOff?.[4] || 0, __prevOff?.[5] || 0];
  if (__keepRot[0] || __keepRot[1] || __keepRot[2]) {
    dstWorldQ = dstWorldQ.multiply(rodriguesDegToQuaternion(__keepRot[0], __keepRot[1], __keepRot[2]));
  }

  const newChildQ = dstWorldQ.clone().multiply(srcQL_root.clone().invert());
  childObj.quaternion.copy(newChildQ);

  const srcWorldOffset = srcPL_root.clone().applyQuaternion(newChildQ);
  const newChildPos = dstWorldPos.clone().sub(srcWorldOffset);
  childObj.position.copy(newChildPos);

  // push to server
  // IMPORTANT: downstream pose updates may overwrite the renderer orientation.
  // Therefore we must include the *actual* rotation in the pose, not zeros.
  const rod = window.quaternionToRodriguesDeg ? window.quaternionToRodriguesDeg(childObj.quaternion) : [0,0,0];
  const pose = [childObj.position.x, childObj.position.y, childObj.position.z, rod[0]||0, rod[1]||0, rod[2]||0];
  // (we keep rx/ry/rz at 0 because renderer uses quaternion; Display uses rodrigues; ok for builder)
	  const attach = {
	    parent_name: ownerName,
	    parent_solid: __resolveSolidKey(targetObj, __prefParentSolid) || targetObj.userData?.solidName || null,
	    parent_anchor: anchorName,
	    child_solid: __resolveSolidKey(childObj, window.builderState.pending.childSolid) || childObj.userData?.solidName || null,
	    child_anchor: window.builderState.pending.sourceAnchor,
	    offset: [0, 0, 0, __keepRot[0], __keepRot[1], __keepRot[2]]
	  };

  // Preserve existing metadata (especially type/options) when creating an attach.
  // IMPORTANT: pending.type can be undefined in some flows; never overwrite a known type with undefined.
  const __curMeta = window.builderState.components[childName] || {};
  const __t = window.builderState.pending?.type || __curMeta.type;
  window.builderState.components[childName] = Object.assign({}, __curMeta, (__t ? { type: __t } : {}), { attach: attach });

  // Undo step: attaching
  const __nextMeta = __deepClone(window.builderState.components[childName] || null);
  const __nextPose = pose ? __deepClone(pose) : null;
  const __op = (window.builderState.pending && window.builderState.pending.op) ? window.builderState.pending.op : "anchor";
  const __spec0 = __deepClone(window.builderState.specs?.[childName] || {});
  try {
    const merged = __maybeMergeCreateAttach({ kind: "attach", op: __op, name: childName, prevMeta: __prevMeta, prevPose: __prevPose, nextMeta: __nextMeta, nextPose: __nextPose, spec0: __spec0 });
    __pushUndo(merged);
  } catch (e) {}
socket.emit("upstream_update", {
    [childName]: {
      pose,
      builder: { attach }
    }
  });

  // Builder-only visual fix:
  // Some parent/child sub-solid transforms (and sometimes the child's mesh) finalize
  // a frame later, especially for robot-mounted tools. The simulation is correct,
  // but the initial Builder snap can look slightly "floating" until any later
  // transform (like rotate) forces a re-snap.
  //
  // Re-apply the snap on the next frame (and once more) using the recorded attach
  // solids/anchors so the very first render lands in the correct place.
  try {
    const __a = attach;
    const __cn = childName;
    const __pn = ownerName;
    const __resnap = () => {
      try {
        __snapChildToParentAnchor(
          __cn,
          __pn,
          __a.parent_anchor,
          __a.child_solid,
          __a.child_anchor,
          __a.offset,
          __a.parent_solid
        );
      } catch (e) {}
    };
    // two frames covers late-loaded meshes and any post-attach parent transform update
    requestAnimationFrame(() => {
      __resnap();
      requestAnimationFrame(() => {
        __resnap();
        // Propagate move to any children (e.g. caps on tubes)
        try { __propagateAnchoredSubtree(__cn); } catch(e) {}
      });
    });
  } catch (e) {}

  // Immediate propagate for children already in scene
  try { __propagateAnchoredSubtree(childName); } catch(e) {}

  if (window.builderState.components[childName]?.type === "fixture_plate") {
    window.builderState.lastFixturePlate = childName;
  }

  window.builderState.mode = "IDLE";
  window.builderState.pending = null;
  window.builderState.targetName = null;
  showToast("Snapped!");
  try { if (window.__updateConfigPreview) window.__updateConfigPreview(); } catch(e) {}
  // After completing a joint, hide anchors + clear selection to reduce confusion.
  try { clearAnchors(); } catch(e) {}
  try { setSelected(null); } catch(e) {}
  try { closeAnchorPickPanel(); } catch(e) {}
  try { closeAttachModal(); } catch(e) {}

}

window.handleAnchorPick = handleAnchorPick;

// Internal: snap a child component to a parent anchor WITHOUT going through UI state machine.
// Uses the same math as handleAnchorPick().
// NOTE: Some objects (especially plates) store anchors per-solid (anchorsBySolid). When we are
// snapping, we must look up anchors on the *intended* solid, not just the object's current
// userData.anchors (which is usually the first solid).
function __snapChildToParentAnchor(childName, parentName, parentAnchor, childSolid, childAnchor, offsetArr=null, parentSolid=null) {
  const childObj = objectsByName.get(childName);
  const parentObj = objectsByName.get(parentName);
  if (!childObj || !parentObj) throw new Error("snap: missing child/parent object");

  // Some CADs name anchors inconsistently (e.g. "A4" vs "hole_A4" vs "hole-A4").
  // We accept the canonical name in config ("A4") but resolve to the actual key at snap-time.
  function __canonKey(s) {
    if (!s) return null;
    const t = String(s).trim();
    // strip common prefixes
    const cleaned = t.replace(/^hole[ _-]?/i, "").replace(/^anchor[ _-]?/i, "");
    const m = cleaned.match(/^([A-Za-z])[ _-]?(\d{1,4})$/);
    if (!m) return null;
    const L = m[1].toUpperCase();
    const N = parseInt(m[2], 10);
    if (!(L >= "A" && L <= "Z")) return null;
    if (!(N >= 0 && N <= 500)) return null;
    return `${L}${N}`;
  }
  function __resolveAnchorKey(anchors, desiredName) {
    if (!anchors || typeof anchors !== "object") return { key: desiredName, arr: null };
    if (anchors[desiredName]) return { key: desiredName, arr: anchors[desiredName] };

    // try common variants
    const d = String(desiredName);
    const variants = [
      `hole_${d}`,
      `hole-${d}`,
      `hole${d}`,
      `anchor_${d}`,
      `anchor-${d}`
    ];
    for (const v of variants) if (anchors[v]) return { key: v, arr: anchors[v] };

    // try canonical match against all keys
    const want = __canonKey(desiredName);
    if (want) {
      for (const k of Object.keys(anchors)) {
        if (__canonKey(k) === want) return { key: k, arr: anchors[k] };
      }
    }
    return { key: desiredName, arr: null };
  }

    function __getAnchorsForObj(obj, preferredSolid=null) {
    const ud = obj?.userData || {};
    const ab = (ud.anchorsBySolid && typeof ud.anchorsBySolid === "object") ? ud.anchorsBySolid : null;
    // If UI selected a solid, use that solid's anchors even if ud.anchors exists.
    if (preferredSolid && ab) {
      if (ab[preferredSolid]) return ab[preferredSolid];
      const want = String(preferredSolid).toLowerCase();
      const keys = Object.keys(ab);
      const exactCI = keys.find(k => String(k).toLowerCase() === want);
      if (exactCI && ab[exactCI]) return ab[exactCI];
      const contains = keys.find(k => String(k).toLowerCase().includes(want) || want.includes(String(k).toLowerCase()));
      if (contains && ab[contains]) return ab[contains];
    }
    if (ud.anchors && typeof ud.anchors === "object" && Object.keys(ud.anchors).length) return ud.anchors;
    if (!ab) return {};
    const solid = (ud.solidName && ab[ud.solidName]) ? ud.solidName : (ab["solid_0"] ? "solid_0" : Object.keys(ab)[0]);
    return (solid && ab[solid]) ? ab[solid] : {};
  }


	  // Resolve actual solid keys so attach records a real solid name.
	  const __childSolidResolved = __resolveSolidKey(childObj, childSolid) || childObj.userData?.solidName || null;
	  const __parentSolidResolved = __resolveSolidKey(parentObj, parentSolid) || parentObj.userData?.solidName || null;
	
	  const childAnchors = __getAnchorsForObj(childObj, __childSolidResolved);
	  const parentAnchors = __getAnchorsForObj(parentObj, __parentSolidResolved);

  const srcHit = __resolveAnchorKey(childAnchors, childAnchor);
  const dstHit = __resolveAnchorKey(parentAnchors, parentAnchor);

  // Persist resolved anchor keys so subsequent rotate/snap uses the same exact anchor key
  // (prevents jumping between equivalent keys like "A1" vs "hole_A1").
  const __childAnchorKey = srcHit.key || childAnchor;
  const __parentAnchorKey = dstHit.key || parentAnchor;

  const srcArr = srcHit.arr;
  const dstArr = dstHit.arr;
  if (!srcArr) throw new Error(`snap: child anchor not found (${childAnchor})`);
  if (!dstArr) throw new Error(`snap: parent anchor not found (${parentAnchor})`);

  const srcPL = new THREE.Vector3(srcArr[0], srcArr[1], srcArr[2]);
  const srcQL = rodriguesDegToQuaternion(srcArr[3], srcArr[4], srcArr[5]);
  const dstPL = new THREE.Vector3(dstArr[0], dstArr[1], dstArr[2]);
  const dstQL = rodriguesDegToQuaternion(dstArr[3], dstArr[4], dstArr[5]);

  // If anchors are defined in a specific solid's local frame (multi-solid assemblies),
  // convert them into the component-root local frame using the solid holder transform.
  function __solidAnchorToRoot(obj, solidKey, pLocal, qLocal) {
    try {
      if (!solidKey) return { p: pLocal, q: qLocal };
      const holder = obj.getObjectByName ? obj.getObjectByName(String(solidKey)) : null;
      if (!holder || holder === obj) return { p: pLocal, q: qLocal };
      const pW = holder.localToWorld(pLocal.clone());
      const pRoot = obj.worldToLocal(pW.clone());
      const hW = new THREE.Quaternion();
      const oW = new THREE.Quaternion();
      holder.getWorldQuaternion(hW);
      obj.getWorldQuaternion(oW);
      const qW = hW.clone().multiply(qLocal);
      const qRoot = oW.clone().invert().multiply(qW);
      return { p: pRoot, q: qRoot };
    } catch (e) {
      return { p: pLocal, q: qLocal };
    }
  }

	  const __srcX = __solidAnchorToRoot(childObj, __childSolidResolved, srcPL, srcQL);
  const srcPL_root = __srcX.p;
  const srcQL_root = __srcX.q;

	  const __dstX = __solidAnchorToRoot(parentObj, __parentSolidResolved, dstPL, dstQL);
  const dstPL_root = __dstX.p;
  const dstQL_root = __dstX.q;

  const dstWorldPos = parentObj.localToWorld(dstPL_root.clone());
  const parentWorldQ = new THREE.Quaternion();
  parentObj.getWorldQuaternion(parentWorldQ);

  // Base target orientation: parentWorld * parentAnchorLocal
  let dstWorldQ = parentWorldQ.clone().multiply(dstQL_root);

  // IMPORTANT: Config "offset" rotations are defined about the *anchor frame*, not the
  // object's center. That means rotation should happen around the anchor point.
  // We implement this by composing the offset rotation into the target anchor frame
  // BEFORE solving for the child quaternion.
  //
  // offsetArr = [x,y,z,rx,ry,rz] where the last three are treated like the rest of the
  // system (Rodrigues vector in degrees).
  if (Array.isArray(offsetArr) && offsetArr.length >= 6) {
    const qOff = rodriguesDegToQuaternion(offsetArr[3]||0, offsetArr[4]||0, offsetArr[5]||0);
    dstWorldQ = dstWorldQ.clone().multiply(qOff);
  }

  const newChildQ = dstWorldQ.clone().multiply(srcQL_root.clone().invert());
  childObj.quaternion.copy(newChildQ);

  const srcWorldOffset = srcPL_root.clone().applyQuaternion(newChildQ);
  const newChildPos = dstWorldPos.clone().sub(srcWorldOffset);
  childObj.position.copy(newChildPos);

  // apply optional offset in parent frame (XYZ + RPY) if provided
  if (Array.isArray(offsetArr) && offsetArr.length >= 3) {
    const off = new THREE.Vector3(offsetArr[0]||0, offsetArr[1]||0, offsetArr[2]||0);
    // offset is in parent local frame
    const offW = off.applyQuaternion(parentWorldQ);
    childObj.position.add(offW);
  }

  // Downstream updates can overwrite orientation, so include real rotation.
  const rod = (window.quaternionToRodriguesDeg)
    ? window.quaternionToRodriguesDeg(childObj.quaternion)
    : [0,0,0];
  const pose = [childObj.position.x, childObj.position.y, childObj.position.z, rod[0]||0, rod[1]||0, rod[2]||0];
	  const attach = {
    parent_name: parentName,
	    parent_solid: __parentSolidResolved || parentSolid || parentObj.userData?.solidName || null,
    parent_anchor: parentAnchor,
	    child_solid: __childSolidResolved || childSolid || childObj.userData?.solidName || null,
    child_anchor: childAnchor,
    offset: Array.isArray(offsetArr) ? offsetArr.slice(0,6) : [0,0,0,0,0,0]
  };

  // persist in builder state + on object
  window.builderState.components[childName] = Object.assign({}, window.builderState.components[childName]||{}, { attach });
  if (childObj.userData) childObj.userData.builderAttach = attach;

  try {
    socket.emit("upstream_update", {
      [childName]: { pose, builder: { attach } }
    });
  } catch (e) {
    console.warn("snap: upstream_update failed", e);
  }

  // Collision-box resize handles are world-anchored — refresh after a snap.
  try { if (window.__colBoxHandlesUpdate) window.__colBoxHandlesUpdate(); } catch (e) {}

  return attach;
}

// Helper: normalize anchor keys like "A_1" / "a-1" -> "A1".
function __normalizeAlphaNumAnchorKey(s) {
  if (!s) return null;
  const t = String(s).trim();
  // extract first letter + trailing number
  const m = t.match(/([A-Za-z])\s*[-_ ]*\s*(\d{1,4})/);
  if (!m) return null;
  const L = m[1].toUpperCase();
  const N = parseInt(m[2], 10);
  if (!Number.isFinite(N)) return null;
  if (L < "A" || L > "Z") return null;
  if (N < 1 || N > 500) return null;
  return `${L}${N}`;
}

function __alphaToIdx(ch) { return ch.charCodeAt(0) - 65; }
function __idxToAlpha(i) { return String.fromCharCode(65 + i); }


function toYamlString(obj) {
  // Small YAML serializer (avoids deps)
  // Assumes obj is simple (dict/list/scalars).
  const lines = [];
  function w(key, val, indent) {
    const pad = " ".repeat(indent);
    if (val === null || val === undefined) return;
    if (Array.isArray(val)) {
      lines.push(`${pad}${key}: [${val.map(v => typeof v === "string" ? JSON.stringify(v) : v).join(", ")}]`);
    } else if (typeof val === "object") {
      lines.push(`${pad}${key}:`);
      for (const [k2, v2] of Object.entries(val)) w(k2, v2, indent + 2);
    } else if (typeof val === "string") {
      lines.push(`${pad}${key}: ${JSON.stringify(val)}`);
    } else {
      lines.push(`${pad}${key}: ${val}`);
    }
  }
  const keys = Object.keys(obj);
  for (let i = 0; i < keys.length; i++) {
    if (i > 0) lines.push("");  // blank line between top-level components
    w(keys[i], obj[keys[i]], 0);
  }
  return lines.join("\n") + "\n";
}

// ── Collision Box Tool ──

function __closeCollisionBoxPanel() {
  const el = document.getElementById("colBoxPanel");
  if (el) el.remove();
  if (window.builderState.mode === "COLLISIONBOX_PICK_Z_OBJ" || window.builderState.mode === "COLLISIONBOX_PICK_TARGET" || window.builderState.mode === "COLLISIONBOX_PICK_ANCHOR") {
    window.builderState.mode = "IDLE";
  }
  window.builderState._colBoxTarget = null;
  window.builderState._colBoxZCallback = null;
}

// Get the largest collision box [sx, sy, sz] from an object
function __getParentCollisionSize(parentName) {
  const spec = window.builderState.specs?.[parentName];
  if (!spec) return null;
  const boxes = [];
  if (Array.isArray(spec.collisionLocal)) boxes.push(...spec.collisionLocal);
  if (Array.isArray(spec.meshes)) {
    for (const m of spec.meshes) {
      if (Array.isArray(m.collisionLocal)) boxes.push(...m.collisionLocal);
    }
  }
  let best = null, bestVol = 0;
  for (const b of boxes) {
    if (!b?.scale) continue;
    const v = (b.scale[0] || 0) * (b.scale[1] || 0) * (b.scale[2] || 0);
    if (v > bestVol) { bestVol = v; best = b.scale; }
  }
  return best;
}

// Get the world Z of the highest anchor on an object
function __getTopAnchorWorldZ(objName) {
  const obj = objectsByName.get(objName);
  if (!obj) return null;
  const ud = obj.userData || {};
  const ab = (ud.anchorsBySolid && typeof ud.anchorsBySolid === "object" && Object.keys(ud.anchorsBySolid).length) ? ud.anchorsBySolid : null;
  let maxZ = -Infinity, found = false;
  function check(anchors, solidKey) {
    for (const [, arr] of Object.entries(anchors)) {
      if (!Array.isArray(arr) || arr.length < 3) continue;
      const pL = new THREE.Vector3(arr[0], arr[1], arr[2]);
      let pW;
      if (solidKey) { const h = obj.getObjectByName(String(solidKey)); pW = h ? h.localToWorld(pL.clone()) : obj.localToWorld(pL.clone()); }
      else { pW = obj.localToWorld(pL.clone()); }
      if (pW.z > maxZ) { maxZ = pW.z; found = true; }
    }
  }
  if (ab) { for (const [sk, a] of Object.entries(ab)) check(a, sk); }
  else if (ud.anchors) { check(ud.anchors, null); }
  return found ? maxZ : null;
}

function __openCollisionBoxPanel() {
  const th = _sbPanelTheme();
  __closeCollisionBoxPanel();

  let selectedTarget = null;
  let selectedAnchor = null;
  let selectedSolid = null;

  const panel = document.createElement("div");
  panel.id = "colBoxPanel";
  panel.style.cssText = `position:fixed;right:340px;top:18px;width:360px;max-height:85vh;overflow:hidden;display:flex;flex-direction:column;background:${th.panelBg};border:${th.panelBord};border-radius:16px;box-shadow:0 18px 60px rgba(0,0,0,0.45);padding:14px;z-index:10005;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;color:${th.color};`;

  // Title
  const title = document.createElement("div");
  title.textContent = "Collision Box";
  title.style.cssText = "font-weight:700;font-size:16px;margin-bottom:4px;flex-shrink:0;";
  panel.appendChild(title);

  const hint = document.createElement("div");
  hint.textContent = "Select a target object, pick an anchor, set size, then Add.";
  hint.style.cssText = "font-size:11px;opacity:0.6;margin-bottom:10px;flex-shrink:0;";
  panel.appendChild(hint);

  // ── Section 1: Target Object ──
  const secTarget = document.createElement("div");
  secTarget.style.cssText = "margin-bottom:8px;flex-shrink:0;";
  const targetLabel = document.createElement("div");
  targetLabel.textContent = "TARGET OBJECT";
  targetLabel.style.cssText = "font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;opacity:0.5;margin-bottom:4px;";
  secTarget.appendChild(targetLabel);

  const targetBox = document.createElement("div");
  targetBox.textContent = "Click target object...";
  targetBox.style.cssText = "padding:8px 10px;border-radius:10px;border:2px solid rgba(37,99,235,0.6);background:rgba(37,99,235,0.08);font-size:13px;font-weight:600;color:rgba(37,99,235,0.8);";
  secTarget.appendChild(targetBox);
  panel.appendChild(secTarget);

  // ── Section 2: Anchor list — same style as pattern tool side panel ──
  const anchorListContainer = document.createElement("div");
  anchorListContainer.style.border = th.listBord;
  anchorListContainer.style.borderRadius = "12px";
  anchorListContainer.style.overflow = "hidden";
  anchorListContainer.style.marginBottom = "8px";
  anchorListContainer.style.display = "flex";
  anchorListContainer.style.flexDirection = "column";
  anchorListContainer.style.minHeight = "0";
  anchorListContainer.style.flex = "1";

  const anchorListHeader = document.createElement("div");
  anchorListHeader.style.display = "flex";
  anchorListHeader.style.alignItems = "center";
  anchorListHeader.style.justifyContent = "space-between";
  anchorListHeader.style.fontSize = "12px";
  anchorListHeader.style.fontWeight = "700";
  anchorListHeader.style.padding = "8px 10px";
  anchorListHeader.style.background = th.rowBg;
  anchorListHeader.style.borderBottom = th.itemBord;
  anchorListHeader.style.flexShrink = "0";
  const anchorHeaderTitle = document.createElement("span");
  anchorHeaderTitle.textContent = "Anchors";
  const anchorHeaderCount = document.createElement("span");
  anchorHeaderCount.textContent = "";
  anchorHeaderCount.style.fontWeight = "400";
  anchorHeaderCount.style.opacity = "0.6";
  anchorHeaderCount.style.fontSize = "11px";
  anchorListHeader.appendChild(anchorHeaderTitle);
  anchorListHeader.appendChild(anchorHeaderCount);
  anchorListContainer.appendChild(anchorListHeader);

  const anchorSearch = document.createElement("input");
  anchorSearch.type = "text";
  anchorSearch.placeholder = "Filter anchors...";
  anchorSearch.style.width = "100%";
  anchorSearch.style.boxSizing = "border-box";
  anchorSearch.style.padding = "8px 10px";
  anchorSearch.style.border = "none";
  anchorSearch.style.borderBottom = th.itemBord;
  anchorSearch.style.background = th.inputBg;
  anchorSearch.style.color = th.color;
  anchorSearch.style.fontSize = "12px";
  anchorSearch.style.outline = "none";
  anchorSearch.style.display = "none";
  anchorSearch.style.flexShrink = "0";
  anchorListContainer.appendChild(anchorSearch);

  const anchorListDiv = document.createElement("div");
  anchorListDiv.style.maxHeight = "300px";
  anchorListDiv.style.overflow = "auto";
  anchorListDiv.style.background = th.listBg;
  anchorListDiv.style.flex = "1";
  anchorListContainer.appendChild(anchorListDiv);

  // Empty state
  const anchorEmpty = document.createElement("div");
  anchorEmpty.textContent = "Select a target object to see anchors";
  anchorEmpty.style.padding = "12px 10px";
  anchorEmpty.style.fontSize = "12px";
  anchorEmpty.style.opacity = "0.45";
  anchorEmpty.style.textAlign = "center";
  anchorListDiv.appendChild(anchorEmpty);

  panel.appendChild(anchorListContainer);

  // ── Section 3: Size ──
  const secSize = document.createElement("div");
  secSize.style.cssText = "flex-shrink:0;";
  const sizeLabel = document.createElement("div");
  sizeLabel.textContent = "SIZE (mm)";
  sizeLabel.style.cssText = "font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;opacity:0.5;margin-bottom:6px;";
  secSize.appendChild(sizeLabel);

  function mkSizeRow(label, autoLabel, autoFn) {
    const row = document.createElement("div");
    row.style.cssText = "display:flex;align-items:center;gap:6px;margin-bottom:6px;";
    const lab = document.createElement("div");
    lab.textContent = label;
    lab.style.cssText = "width:18px;font-size:12px;font-weight:700;";
    const inp = document.createElement("input");
    inp.type = "number"; inp.value = "100"; inp.min = "1"; inp.step = "0.1";
    inp.style.cssText = `flex:1;padding:6px 8px;border-radius:8px;border:${th.inputBord};font-size:12px;font-weight:600;background:${th.inputBg};color:${th.color};`;
    const btn = document.createElement("button");
    btn.textContent = autoLabel;
    btn.style.cssText = `padding:5px 8px;border-radius:6px;border:${th.panelBord};background:${th.cancelBg};color:${th.color};cursor:pointer;font-size:10px;font-weight:600;white-space:nowrap;transition:background 0.15s,border 0.15s;`;
    btn.addEventListener("mouseenter", () => { if (!btn.dataset.active) btn.style.background = th.rowBg; });
    btn.addEventListener("mouseleave", () => { if (!btn.dataset.active) btn.style.background = th.cancelBg; });
    btn.addEventListener("click", () => autoFn(inp, btn));
    row.appendChild(lab); row.appendChild(inp); row.appendChild(btn);
    return { row, inp, btn };
  }

  const xRow = mkSizeRow("X", "Auto X", (inp) => {
    if (!selectedTarget) { showToast("Select a target object first."); return; }
    const sz = __getParentCollisionSize(selectedTarget);
    if (sz) inp.value = Math.round(sz[0]);
    else showToast("No collision data on " + selectedTarget);
  });
  secSize.appendChild(xRow.row);

  const yRow = mkSizeRow("Y", "Auto Y", (inp) => {
    if (!selectedTarget) { showToast("Select a target object first."); return; }
    const sz = __getParentCollisionSize(selectedTarget);
    if (sz) inp.value = Math.round(sz[1]);
    else showToast("No collision data on " + selectedTarget);
  });
  secSize.appendChild(yRow.row);

  let autoZActive = false;
  const zRow = mkSizeRow("Z", "Auto Z", (inp, btn) => {
    if (!selectedTarget || !selectedAnchor) { showToast("Select target and anchor first."); return; }
    if (autoZActive) {
      autoZActive = false;
      btn.dataset.active = "";
      btn.style.background = "#f5f5f5";
      btn.style.border = "1px solid rgba(0,0,0,0.12)";
      window.builderState.mode = "IDLE";
      return;
    }
    autoZActive = true;
    btn.dataset.active = "1";
    btn.style.background = "rgba(37,99,235,0.2)";
    btn.style.border = "2px solid rgba(37,99,235,0.6)";
    window.builderState.mode = "COLLISIONBOX_PICK_Z_OBJ";
    window.builderState._colBoxZCallback = (objName) => {
      autoZActive = false;
      btn.dataset.active = "";
      btn.style.background = "#f5f5f5";
      btn.style.border = "1px solid rgba(0,0,0,0.12)";
      window.builderState.mode = "IDLE";
      window.builderState._colBoxZCallback = null;

      const topZ = __getTopAnchorWorldZ(objName);
      if (topZ === null) { showToast("No anchors on " + objName + " \u2014 not valid for Auto Z."); return; }
      const parentObj = objectsByName.get(selectedTarget);
      if (!parentObj) return;
      const ud = parentObj.userData || {};
      let anchorArr = null;
      const abp = (ud.anchorsBySolid && typeof ud.anchorsBySolid === "object" && Object.keys(ud.anchorsBySolid).length) ? ud.anchorsBySolid : null;
      if (abp && selectedSolid && abp[selectedSolid]) anchorArr = abp[selectedSolid]?.[selectedAnchor];
      if (!anchorArr && ud.anchors) anchorArr = ud.anchors[selectedAnchor];
      if (!anchorArr) anchorArr = [0,0,0,0,0,0];
      const aL = new THREE.Vector3(anchorArr[0], anchorArr[1], anchorArr[2]);
      let aW;
      if (selectedSolid) { const h = parentObj.getObjectByName(String(selectedSolid)); aW = h ? h.localToWorld(aL.clone()) : parentObj.localToWorld(aL.clone()); }
      else { aW = parentObj.localToWorld(aL.clone()); }
      const zH = Math.abs(topZ - aW.z);
      inp.value = Math.max(1, Math.round(zH));
      showToast("Auto Z: " + Math.round(zH) + " mm (from " + objName + ")");
    };
    showToast("Click an object to measure top anchor height.");
  });
  secSize.appendChild(zRow.row);
  panel.appendChild(secSize);

  // ── Buttons ──
  const sep = document.createElement("div");
  sep.style.cssText = "height:1px;background:rgba(0,0,0,0.08);margin:10px 0;flex-shrink:0;";
  panel.appendChild(sep);

  const btnRow = document.createElement("div");
  btnRow.style.cssText = "display:flex;gap:8px;justify-content:flex-end;flex-shrink:0;";

  const cancelBtn = document.createElement("button");
  cancelBtn.className = "btn btn-ghost";
  cancelBtn.textContent = "Cancel";
  cancelBtn.addEventListener("click", () => {
    __closeCollisionBoxPanel();
    try { clearAnchors(); } catch(e) {}
  });

  const createBtn = document.createElement("button");
  createBtn.className = "btn btn-primary";
  createBtn.textContent = "Add";
  createBtn.style.cssText = "opacity:0.4;pointer-events:none;";
  createBtn.addEventListener("click", () => {
    if (!selectedTarget || !selectedAnchor) return;
    const _q = v => Math.round(v * 10) / 10;
    const sx = _q(parseFloat(xRow.inp.value) || 100);
    const sy = _q(parseFloat(yRow.inp.value) || 100);
    const sz = _q(parseFloat(zRow.inp.value) || 100);
    __closeCollisionBoxPanel();
    __spawnCollisionBox(selectedTarget, selectedAnchor, selectedSolid, [sx, sy, sz]);
  });

  btnRow.appendChild(cancelBtn);
  btnRow.appendChild(createBtn);
  panel.appendChild(btnRow);

  function updateCreateBtn() {
    if (selectedTarget && selectedAnchor) {
      createBtn.style.opacity = "1";
      createBtn.style.pointerEvents = "auto";
    } else {
      createBtn.style.opacity = "0.4";
      createBtn.style.pointerEvents = "none";
    }
  }

  // ── Anchor list functions — same as pattern tool ──
  let _colBoxAnchorNames = [];
  let _colBoxAnchorSolids = {}; // solidKey -> {anchorName: pose}

  function populateAnchors(ownerName) {
    const owner = objectsByName.get(ownerName);
    if (!owner) return;
    const ud = owner.userData || {};
    const ab = (ud.anchorsBySolid && typeof ud.anchorsBySolid === "object" && Object.keys(ud.anchorsBySolid).length) ? ud.anchorsBySolid : null;

    _colBoxAnchorSolids = {};
    let allNames = [];
    if (ab) {
      for (const [sk, anchors] of Object.entries(ab)) {
        for (const aName of Object.keys(anchors)) {
          if (!allNames.includes(aName)) allNames.push(aName);
          if (!_colBoxAnchorSolids[aName]) _colBoxAnchorSolids[aName] = sk;
        }
      }
    } else if (ud.anchors) {
      allNames = Object.keys(ud.anchors);
    }

    // Sort with center first
    allNames.sort((a,b) => (a==="center" ? -1 : b==="center" ? 1 : a.localeCompare(b)));
    _colBoxAnchorNames = allNames;

    // Style the container to show it's active
    anchorListContainer.style.border = "1px solid rgba(60,130,255,0.25)";
    anchorListContainer.style.background = "rgba(60,130,255,0.02)";
    anchorListHeader.style.background = "rgba(60,130,255,0.06)";
    anchorListHeader.style.borderBottom = "1px solid rgba(60,130,255,0.12)";
    anchorHeaderTitle.textContent = "Anchors on " + displayName(ownerName);
    anchorHeaderCount.textContent = _colBoxAnchorNames.length + " available";
    anchorSearch.style.display = "";
    anchorSearch.value = "";

    renderAnchorItems("");
    setTimeout(() => { try { anchorSearch.focus(); } catch(_) {} }, 50);
  }

  function renderAnchorItems(filterText) {
    anchorListDiv.innerHTML = "";
    const f = (filterText || "").trim().toLowerCase();
    const shown = _colBoxAnchorNames.filter(n => !f || n.toLowerCase().includes(f));
    for (const n of shown) {
      const item = document.createElement("div");
      item.textContent = n;
      item.style.padding = "8px 10px";
      item.style.cursor = "pointer";
      item.style.fontSize = "12px";
      item.style.borderBottom = "1px solid rgba(255,255,255,0.05)";
      item.style.transition = "background 0.1s ease";
      if (selectedAnchor === n) {
        item.style.background = "rgba(60,130,255,0.18)";
        item.style.fontWeight = "700";
      }
      item.addEventListener("mouseenter", () => { item.style.background = "rgba(60,130,255,0.12)"; });
      item.addEventListener("mouseleave", () => { item.style.background = selectedAnchor === n ? "rgba(60,130,255,0.18)" : "transparent"; });
      item.addEventListener("click", () => {
        selectedAnchor = n;
        selectedSolid = _colBoxAnchorSolids[n] || null;
        renderAnchorItems(anchorSearch.value);
        updateCreateBtn();
      });
      anchorListDiv.appendChild(item);
    }
    if (!shown.length) {
      const empty = document.createElement("div");
      empty.textContent = _colBoxAnchorNames.length ? "No matches." : "No anchors available.";
      empty.style.padding = "12px 10px";
      empty.style.fontSize = "12px";
      empty.style.opacity = "0.45";
      empty.style.textAlign = "center";
      anchorListDiv.appendChild(empty);
    }
    anchorHeaderCount.textContent = f ? (shown.length + " of " + _colBoxAnchorNames.length) : (_colBoxAnchorNames.length + " available");
  }

  function resetAnchorList() {
    _colBoxAnchorNames = [];
    _colBoxAnchorSolids = {};
    anchorListContainer.style.border = "1px solid rgba(255,255,255,0.08)";
    anchorListContainer.style.background = "";
    anchorListHeader.style.background = "rgba(255,255,255,0.04)";
    anchorListHeader.style.borderBottom = "1px solid rgba(255,255,255,0.06)";
    anchorHeaderTitle.textContent = "Anchors";
    anchorHeaderCount.textContent = "";
    anchorSearch.style.display = "none";
    anchorSearch.value = "";
    anchorListDiv.innerHTML = "";
    const emptyMsg = document.createElement("div");
    emptyMsg.textContent = "Select a target object to see anchors";
    emptyMsg.style.padding = "12px 10px";
    emptyMsg.style.fontSize = "12px";
    emptyMsg.style.opacity = "0.45";
    emptyMsg.style.textAlign = "center";
    anchorListDiv.appendChild(emptyMsg);
  }

  anchorSearch.addEventListener("input", () => renderAnchorItems(anchorSearch.value));

  // ── Handle 3D clicks: target object + anchor ──
  function setTarget(name) {
    selectedTarget = name;
    selectedAnchor = null;
    selectedSolid = null;
    targetBox.textContent = displayName(name);
    { const _th = _sbPanelTheme();
      targetBox.style.color = _th.color;
      targetBox.style.border = _th.panelBord;
      targetBox.style.background = _th.rowBg; }
    const node = objectsByName.get(name);
    if (node) buildAnchorsFor(node);
    populateAnchors(name);
    updateCreateBtn();
  }

  // Wire into builder modes
  window.builderState.mode = "COLLISIONBOX_PICK_TARGET";
  window.builderState._colBoxTarget = null;

  // Store callbacks so 3D click handlers can reach the panel
  window.builderState._colBoxPanelSetTarget = setTarget;
  window.builderState._colBoxPanelSetAnchor = (anchorName, solidKey) => {
    selectedAnchor = anchorName;
    selectedSolid = solidKey;
    renderAnchorItems(anchorSearch.value);
    updateCreateBtn();
  };

  document.body.appendChild(panel);
}

async function __spawnCollisionBox(targetName, anchorName, solidKey, size) {
  const [sx, sy, sz] = size;

  // Use spawnComponentSilent — goes through /api/instantiate without opening anchor UI
  const name = await spawnComponentSilent("collision_box", null, { size: [sx, sy, sz] });
  if (!name) {
    showToast("Failed to create collision box.");
    return;
  }

  // Store size in component config
  if (window.builderState.components[name]) {
    window.builderState.components[name].size = [sx, sy, sz];
  }

  const targetObj = objectsByName.get(targetName);
  const parentSolid = solidKey || targetObj?.userData?.solidName || null;

  // Wait for the object to be ready (collision meshes loaded from /api/instantiate)
  const t0 = performance.now();
  while (performance.now() - t0 < 3000) {
    const o = objectsByName.get(name);
    if (o && o.children && o.children.length) break;
    await new Promise(r => requestAnimationFrame(r));
  }
  const obj = objectsByName.get(name);

  try {
    __snapChildToParentAnchor(name, targetName, anchorName, "body", "center", [0, 0, 0, 0, 0, 0], parentSolid);
  } catch (e) {
    console.warn("collision box snap failed", e);
  }

  const attach = {
    parent_name: targetName,
    parent_solid: parentSolid,
    parent_anchor: anchorName,
    child_solid: "body",
    child_anchor: "center",
    offset: [0, 0, 0, 0, 0, 0]
  };
  if (window.builderState.components[name]) {
    window.builderState.components[name].attach = attach;
  }

  try {
    if (obj) {
      const rod = quaternionToRodriguesDeg(obj.quaternion);
      socket.emit("upstream_update", {
        [name]: {
          pose: [obj.position.x, obj.position.y, obj.position.z, rod[0] || 0, rod[1] || 0, rod[2] || 0],
          builder: { attach }
        }
      });
    }
  } catch (e) {}

  window.builderState.mode = "IDLE";
  window.builderState._colBoxTarget = null;
  window.builderState._colBoxPanelSetTarget = null;
  window.builderState._colBoxPanelSetAnchor = null;
  try { clearAnchors(); } catch(e) {}
  try { setSelected(name); } catch(e) {}
  try { if (window.__updateConfigPreview) window.__updateConfigPreview(); } catch(e) {}
  showToast("Collision box " + name + " created.");
}

// ── Reusable text-input modal ───────────────────────────────────────
// Matches the New Scene / Parameters modal styling. Resolves to the
// trimmed string on confirm, or null on cancel/escape/backdrop.
function __sbTextModal({ title = "", label = "", value = "", placeholder = "", okLabel = "OK", hint = "" } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    // ``modal-overlay`` opts out of the nav's sibling margin-shift so the
    // backdrop covers the whole viewport (including the navbar) — see
    // vendor/nav.css. Without it the navbar peeks through on the left.
    overlay.className = "modal-overlay";
    overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:50000;backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);padding:20px;";
    overlay.innerHTML = `
      <div style="width:min(420px,100%);background:var(--surface);border-radius:18px;box-shadow:var(--shadow-lg);overflow:hidden;animation:confirmIn 0.25s cubic-bezier(0.2,0.9,0.3,1) forwards;">
        <div style="padding:16px 20px;border-bottom:1px solid var(--border2);display:flex;align-items:center;">
          <h3 style="font-size:16px;font-weight:600;letter-spacing:-0.2px;">${title}</h3>
          <div class="spacer"></div>
          <button class="btn btn-ghost btn-sm btn-icon" id="tmClose" title="Close"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
        </div>
        <div style="padding:20px;display:flex;flex-direction:column;gap:10px;">
          ${label ? `<label style="font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;">${label}</label>` : ""}
          <input class="input" id="tmInput" type="text" placeholder="${placeholder}"/>
          ${hint ? `<div style="font-size:12px;color:var(--muted);margin-top:2px;">${hint}</div>` : ""}
        </div>
        <div style="padding:14px 20px;border-top:1px solid var(--border2);display:flex;gap:8px;justify-content:flex-end;">
          <button class="btn" id="tmCancel">Cancel</button>
          <button class="btn btn-primary" id="tmOk">${okLabel}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    const input = overlay.querySelector("#tmInput");
    input.value = value || "";
    setTimeout(() => { input.focus(); input.select(); }, 80);

    function cleanup(result) { overlay.remove(); resolve(result); }
    overlay.querySelector("#tmCancel").addEventListener("click", () => cleanup(null));
    overlay.querySelector("#tmClose").addEventListener("click", () => cleanup(null));
    overlay.querySelector("#tmOk").addEventListener("click", () => cleanup(input.value.trim()));
    overlay.addEventListener("click", (e) => { if (e.target === overlay) cleanup(null); });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); cleanup(input.value.trim()); } });
    document.addEventListener("keydown", function onKey(e) {
      if (e.key === "Escape") { document.removeEventListener("keydown", onKey); cleanup(null); }
    });
  });
}

// ── Multi-file helpers ──────────────────────────────────────────────
// Lazily tag any component that doesn't have a ``__file`` yet with the
// currently-active file. Called from the preview tick + save, so a
// component created while a given file is "checked" gets bound to it.
function __assignUntaggedToActive() {
  const bs = window.builderState;
  if (!bs || !bs.components) return;
  const active = bs.activeFile || (bs.files && bs.files[0]) || "scene.j2";
  for (const meta of Object.values(bs.components)) {
    if (meta && !meta.__file) meta.__file = active;
  }
}

// Which output file a final config name belongs to. Handles the
// core_1 → core rename that buildConfigObject applies.
function __fileForName(name) {
  const bs = window.builderState;
  const comps = (bs && bs.components) || {};
  const fallback = (bs && bs.activeFile) || (bs && bs.files && bs.files[0]) || "scene.j2";
  if (comps[name] && comps[name].__file) return comps[name].__file;
  if (name === "core" && comps["core_1"] && comps["core_1"].__file) return comps["core_1"].__file;
  return fallback;
}

// Partition the merged config into { filename: {name: out, ...}, ... }.
// Every declared file is represented (even if empty) so the file
// structure is preserved on save.
function buildConfigByFile() {
  __assignUntaggedToActive();
  const merged = buildConfigObject();
  const bs = window.builderState;
  const files = (bs && bs.files && bs.files.length) ? bs.files.slice() : ["scene.j2"];
  const out = {};
  for (const f of files) out[f] = {};
  for (const [name, val] of Object.entries(merged)) {
    let f = __fileForName(name);
    if (!out[f]) out[f] = {};   // component tagged to a file not in the list — keep it
    out[f][name] = val;
  }
  return out;
}

function buildConfigObject() {
  // Build the config from what the user actually spawned/anchored.
  // NOTE: We do NOT inject any default cores/robots. If you want a core,
  // spawn it in the builder so it appears here.
  const cfg = {};

  // Builder components
  const entries = [];
  for (const [name, meta] of Object.entries(window.builderState.components)) {
    if (!meta) continue;
    const out = { type: meta.type };

	// Copy any additional options captured at create-time.
    // Skip internal builder-only keys that should not appear in config output.
    const __builderInternalKeys = new Set(["type", "attach", "capParent", "patternParent", "patternMode", "poseABC", "poseYaw", "offset", "__file"]);
    for (const [k, v] of Object.entries(meta)) {
      if (__builderInternalKeys.has(k)) continue;
      out[k] = v;
    }

    // Free-standing (no attach): emit offset:[x,y,z,rx,ry,rz] from live obj position + stored rotation
    if (!meta.attach) {
      const __freeObj = window.objectsByName && window.objectsByName.get(name);
      const __rx = Array.isArray(meta.offset) && meta.offset.length >= 6 ? (meta.offset[3]||0) : (Array.isArray(meta.poseABC) ? meta.poseABC[0]||0 : 0);
      const __ry = Array.isArray(meta.offset) && meta.offset.length >= 6 ? (meta.offset[4]||0) : (Array.isArray(meta.poseABC) ? meta.poseABC[1]||0 : 0);
      const __rz = Array.isArray(meta.offset) && meta.offset.length >= 6 ? (meta.offset[5]||0) : (meta.poseYaw||0);
      const __px = __freeObj ? __snapDeg(__freeObj.position.x) : (Array.isArray(meta.offset) ? meta.offset[0]||0 : 0);
      const __py = __freeObj ? __snapDeg(__freeObj.position.y) : (Array.isArray(meta.offset) ? meta.offset[1]||0 : 0);
      const __pz = __freeObj ? __snapDeg(__freeObj.position.z) : (Array.isArray(meta.offset) ? meta.offset[2]||0 : 0);
      out.offset = [__px, __py, __pz, __snapDeg(__rx), __snapDeg(__ry), __snapDeg(__rz)];
    }

    // ── Core-specific config injection (ordered output) ──
    if (String(out.type) === "core") {
      const hasRail = !!out.has_rail;
      const hasCam  = !!out.has_camera;
      const hasTC   = (out.has_tool_changer !== undefined) ? !!out.has_tool_changer : !!out.has_toolchanger;

      // Rebuild `out` with deterministic field order matching expected config format.
      // Preserve any extra fields from the original config (ip, etc.).
      // ``rail_offset`` and ``camera_serial_number`` used to live at the top
      // level; they're now nested under ``rail_cfg.offset`` and
      // ``camera_cfg.serial_number`` respectively. Migrate-on-write so older
      // sessions don't keep emitting the legacy keys.
      const ordered = { type: "core" };
      ordered.simulation = true;
      ordered.ip = (out.ip !== undefined && out.ip !== "") ? out.ip : "";
      ordered.has_rail = hasRail;
      const _legacyRailOffset = (out.rail_offset !== undefined) ? out.rail_offset : undefined;
      const _existingRailCfg  = (out.rail_cfg && typeof out.rail_cfg === "object") ? out.rail_cfg : {};
      const _railOffset = (_existingRailCfg.offset !== undefined) ? _existingRailCfg.offset
                        : (_legacyRailOffset !== undefined) ? _legacyRailOffset
                        : 0;
      ordered.rail_cfg = Object.assign({}, _existingRailCfg, { offset: _railOffset });
      ordered.has_tool_changer = hasTC;
      // ``tool_changer_cfg`` carries the attach/detach I/O signal sequences.
      // Older sessions used a stale top-level ``tool_changer:`` block with
      // present/attach/detach keys that the Python side silently ignored —
      // drop those entirely; the new shape is ``output_attach`` /
      // ``output_detach`` and any user override goes inside ``tool_changer_cfg``.
      const _existingTcCfg = (out.tool_changer_cfg && typeof out.tool_changer_cfg === "object") ? out.tool_changer_cfg : {};
      ordered.tool_changer_cfg = Object.assign({}, _existingTcCfg);
      ordered.has_motion_plan = true;
      ordered.has_camera = hasCam;
      const _legacySerial = out.camera_serial_number;
      const _existingCamCfg = (out.camera_cfg && typeof out.camera_cfg === "object") ? out.camera_cfg : {};
      const _serialNumber = (_existingCamCfg.serial_number !== undefined) ? _existingCamCfg.serial_number
                          : (_legacySerial !== undefined ? _legacySerial : "");
      ordered.camera_cfg = Object.assign({}, _existingCamCfg, { serial_number: _serialNumber });
      // Rail-dependent fields
      if (hasRail) {
        ordered.robot_attach = out.robot_attach || {
          rail_carriage_anchor: "hole_1",
          robot_A0_anchor: "hole_0",
          offset: [0, 0, 0, 0, 0, 0]
        };
      }

      // Collect any extra keys from the original that aren't in our ordered set
      // (e.g. future fields from imported configs).
      const orderedKeys = new Set(Object.keys(ordered));
      orderedKeys.add("attach"); // handled separately below
      // Legacy keys folded into rail_cfg / camera_cfg / tool_changer_cfg
      // above — drop them from the extra-fields pass-through so saved
      // configs don't carry both forms. ``tool_changer`` was the stale
      // top-level YAML block with present/attach/detach keys that the
      // Python side ignored.
      const _legacyDropped = new Set([
        "rail_offset",
        "camera_serial_number",
        "tool_changer",
      ]);
      const extraFields = {};
      for (const [k, v] of Object.entries(out)) {
        if (orderedKeys.has(k)) continue;
        if (k === "robot_attach") continue;
        if (_legacyDropped.has(k)) continue;
        extraFields[k] = v;
      }

      // Replace out keys with ordered keys (preserve attach for later)
      const savedAttach = out.attach;
      for (const k of Object.keys(out)) delete out[k];
      Object.assign(out, ordered);
      // Append extra fields after the ordered ones
      Object.assign(out, extraFields);
      if (savedAttach) out.attach = savedAttach;
    }


    if (meta.attach) {
      // Ensure 6DOF offset always exists for consistency
      if (!meta.attach.offset) meta.attach.offset = [0,0,0,0,0,0];
      // Snap offset values to clean integers/decimals
      meta.attach.offset = meta.attach.offset.map(v => __snapDeg(v));
      out.attach = meta.attach;
    }

    // Normalize the first core name to "core" (templates use "core", not "core_1").
// If user has an older config/session where the first core is "core_1", save it as "core".
let saveName = name;
if (String(out.type).toLowerCase() === "core" && saveName === "core_1" && !(window.builderState.components && window.builderState.components["core"])) {
  saveName = "core";
}
entries.push([saveName, out]);
  }

  // Ordering rule: put any cores at the very top of the YAML.
  // This keeps configs clean when you have core_1, core_2, etc.
  // We treat something as a "core" if either:
  //   - name starts with "core" (core, core_1, core2...)
  //   - or type === "core"
  const isCore = (name, out) => {
    const n = String(name || "").toLowerCase();
    const t = String(out?.type || "").toLowerCase();
    return n === "core" || n.startsWith("core_") || n.startsWith("core") || t === "core";
  };

  // Only float cores to the top; preserve spawn order for everything else
  const cores = entries.filter(([n, o]) => isCore(n, o));
  const rest = entries.filter(([n, o]) => !isCore(n, o));
  entries.length = 0;
  entries.push(...cores, ...rest);

  for (const [name, out] of entries) cfg[name] = out;

  return cfg;
}

function __downloadTextFile(filename, text) {
  const blob = new Blob([text], { type: "text/yaml" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function __activeFileName() {
  const bs = window.builderState;
  return (bs && bs.activeFile) || (bs && bs.files && bs.files[0]) || "scene.j2";
}

function saveConfig() {
  // Download the currently selected file (its components only).
  const active = __activeFileName();
  const comps = buildConfigByFile()[active] || {};
  __downloadTextFile(active, toYamlString(comps));
  showToast("Downloaded " + active);
}


ensureBuilderBar();

// --- Config panel — wires to static HTML in sidebar ---
(function initConfigViewer(){
  // Use static HTML elements from index.html
  const pre     = document.getElementById("sbConfigPre");
  const copyBtn = document.getElementById("btnCopyConfig");
  const loadBtn = document.getElementById("btnLoadConfig");
  const fileInput = document.getElementById("configFileInput");

  // --- Copy button ---
  if (copyBtn) {
    const origHtml = copyBtn.innerHTML;
    copyBtn.addEventListener("click", () => {
      try {
        // Copy the currently selected file's text.
        const active = __activeFileName();
        const cfg = buildConfigByFile()[active] || {};
        const yaml = toYamlString(cfg);
        const onSuccess = () => {
          copyBtn.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--green)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> Copied!`;
          setTimeout(() => { copyBtn.innerHTML = origHtml; }, 1500);
        };
        if (navigator.clipboard && window.isSecureContext) {
          navigator.clipboard.writeText(yaml).then(onSuccess).catch(() => fallbackCopy(yaml, onSuccess));
        } else {
          fallbackCopy(yaml, onSuccess);
        }
      } catch(e) { showToast("Copy failed", "bad"); }
    });

    function fallbackCopy(text, onSuccess) {
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.cssText = "position:fixed;top:-9999px;left:-9999px;opacity:0";
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        const ok = document.execCommand("copy");
        document.body.removeChild(ta);
        if (ok) onSuccess(); else showToast("Copy failed", "bad");
      } catch(e) { showToast("Copy failed", "bad"); }
    }
  }

  // --- Load button + file input ---
  // Minimal Jinja preprocessing so .j2 scene files load as-authored:
  //  - {# ... #} comments stripped (multi-line)
  //  - {%- set name = value -%} assignments collected, their lines removed
  //  - {% for v in "ABCD" / range(1, 14+1) / [a, b] %} ... {% endfor %}
  //    unrolled (nested loops included) — loop-authored rack fills render
  //  - {{ expr }} substituted with the collected vars; pure-numeric
  //    expressions (e.g. "-wall_offset", "0+0.8") are evaluated
  // {% if %} is NOT handled — those scenes degrade as before.
  function __preprocessJinja(text) {
    let src = String(text);
    src = src.replace(/\{#[\s\S]*?#\}/g, "");
    const vars = {};
    src = src.replace(/^[ \t]*\{%-?\s*set\s+([A-Za-z_]\w*)\s*=\s*(.+?)\s*-?%\}[ \t]*\r?\n?/gm, (_, name, val) => {
      const num = Number(val);
      vars[name] = Number.isFinite(num) ? num : val.replace(/^["']|["']$/g, "");
      return "";
    });
    // {% for %} unrolling. Each pass expands the first complete
    // outermost loop (matching endfor found by depth counting), then
    // rescans — inner loops materialize on later passes. The loop
    // variable is substituted only inside {{ }} expressions of the
    // body; the final {{ }} pass below resolves what remains.
    function __evalNum(expr) {
      let e = expr;
      for (const [k, v] of Object.entries(vars)) {
        e = e.replace(new RegExp("\\b" + k + "\\b", "g"), String(v));
      }
      if (/^[-+*/(). \d]+$/.test(e)) {
        try {
          const v = Function('"use strict";return (' + e + ")")();
          if (Number.isFinite(v)) return v;
        } catch (_) {}
      }
      return null;
    }
    function __loopValues(iter) {
      iter = iter.trim();
      let m = iter.match(/^["'](.*)["']$/);
      if (m) return m[1].split("");
      m = iter.match(/^range\((.*)\)$/);
      if (m) {
        const args = m[1].split(",").map((a) => __evalNum(a));
        if (!args.length || args.some((a) => a === null)) return null;
        const [start, stop, step] =
          args.length === 1 ? [0, args[0], 1] :
          args.length === 2 ? [args[0], args[1], 1] : args;
        const out = [];
        if (!step) return null;
        for (let i = start; step > 0 ? i < stop : i > stop; i += step) out.push(i);
        return out;
      }
      m = iter.match(/^\[(.*)\]$/);
      if (m) return m[1].split(",").map((s) => s.trim().replace(/^["']|["']$/g, ""));
      return null;
    }
    const TAG = /\{%-?\s*(?:(for)\s+([A-Za-z_]\w*)\s+in\s+(.+?)|(endfor))\s*-?%\}[ \t]*\r?\n?/g;
    for (let guard = 0; guard < 512; guard++) {
      TAG.lastIndex = 0;
      let m, open = null, varName = null, iterExpr = null, depth = 0, expandedOne = false;
      while ((m = TAG.exec(src)) !== null) {
        if (m[1] === "for") {
          if (depth === 0) { open = m; varName = m[2]; iterExpr = m[3]; }
          depth++;
        } else if (depth > 0) {
          depth--;
          if (depth === 0 && open) {
            const body = src.slice(open.index + open[0].length, m.index);
            const values = __loopValues(iterExpr);
            let out = "";
            if (values) {
              for (const v of values) {
                out += body.replace(/\{\{\s*([^}]+?)\s*\}\}/g, (mm, expr) =>
                  "{{ " + expr.replace(new RegExp("\\b" + varName + "\\b", "g"), String(v)) + " }}");
              }
            }
            src = src.slice(0, open.index) + out + src.slice(m.index + m[0].length);
            expandedOne = true;
            break;
          }
        }
      }
      if (!expandedOne) break;
    }
    src = src.replace(/\{\{\s*([^}]+?)\s*\}\}/g, (_, expr) => {
      let e = expr;
      for (const [k, v] of Object.entries(vars)) {
        e = e.replace(new RegExp("\\b" + k + "\\b", "g"), String(v));
      }
      if (/^[-+*/(). \d]+$/.test(e)) {
        try {
          const v = Function('"use strict";return (' + e + ")")();
          if (Number.isFinite(v)) return String(v);
        } catch (_) {}
      }
      return e;
    });
    return src;
  }

  function __parseSimpleYaml(text) {
    text = __preprocessJinja(text);
    const result = {};
    const lines = text.split("\n");
    let currentTop = null;
    let currentSub = null;
    for (const raw of lines) {
      if (!raw.trim() || raw.trim().startsWith("#")) continue;
      const indent = raw.search(/\S/);
      const line = raw.trim();
      if (indent === 0) {
        // Top-level key
        const m = line.match(/^([^:]+):\s*(.*)/);
        if (!m) continue;
        const key = m[1].trim();
        const val = m[2].trim();
        if (val === "" || val === "|") {
          result[key] = {};
          currentTop = key;
          currentSub = null;
        } else {
          result[key] = __parseYamlValue(val);
          currentTop = null;
          currentSub = null;
        }
      } else if (indent >= 2 && currentTop) {
        const m = line.match(/^([^:]+):\s*(.*)/);
        if (!m) continue;
        const key = m[1].trim();
        const val = m[2].trim();
        if (indent >= 4 && currentSub) {
          // Nested sub-key (e.g. attach.parent_name)
          if (typeof result[currentTop][currentSub] !== "object" || Array.isArray(result[currentTop][currentSub])) {
            result[currentTop][currentSub] = {};
          }
          result[currentTop][currentSub][key] = __parseYamlValue(val);
        } else if (val === "" || val === "|") {
          result[currentTop][key] = {};
          currentSub = key;
        } else {
          result[currentTop][key] = __parseYamlValue(val);
          currentSub = null;
        }
      }
    }
    return result;
  }

  function __parseYamlValue(s) {
    if (s === "true") return true;
    if (s === "false") return false;
    if (s === "null" || s === "~") return null;
    // Inline array: [1, 2, 3] or ["a", "b"]
    if (s.startsWith("[") && s.endsWith("]")) {
      try {
        return JSON.parse(s);
      } catch(e) {
        // Try parsing as YAML-style (unquoted strings)
        const inner = s.slice(1, -1).trim();
        if (!inner) return [];
        return inner.split(",").map(v => {
          v = v.trim();
          if (v.startsWith('"') && v.endsWith('"')) return v.slice(1, -1);
          if (v === "true") return true;
          if (v === "false") return false;
          if (v === "null") return null;
          const n = Number(v);
          return isNaN(n) ? v : n;
        });
      }
    }
    // Quoted string
    if ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))) return s.slice(1, -1);
    // Number
    const n = Number(s);
    if (!isNaN(n) && s !== "") return n;
    return s;
  }

  // Clear entire scene
  function __clearScene() {
    const names = Object.keys(window.builderState.components || {});
    for (const name of names) {
      try { __applyDelete(name); } catch(e) {}
    }
    window.builderState.components = {};
    window.builderState.specs = {};
    window.builderState.placedOrder = [];
    window.builderState.lastFixturePlate = null;
    window.builderState.next = {};
    window.builderState.selectedName = null;
    window.builderState.undoStack = [];
    window.builderState.redoStack = [];
    try { clearAnchors(); } catch(e) {}
    try { window.__updateConfigPreview(); } catch(e) {}
  }

  // Load a parsed config into the scene.
  // opts.clear  — wipe the scene first (default true; false to add to it)
  // opts.file   — source filename; each loaded component is tagged with
  //               it so a later save writes it back to the same file.
  async function __loadConfigToScene(cfg, opts = {}) {
    const doClear = opts.clear !== false;
    const srcFile = opts.file || null;
    if (doClear) {
      __clearScene();
      // Small delay to let deletions propagate
      await new Promise(r => setTimeout(r, 200));
    }

    const allEntries = Object.entries(cfg).filter(([, v]) => v && typeof v === "object" && v.type);
    const __collisionToHide = [];

    // Topological sort: parents must be spawned and attached before children.
    const cfgMap = new Map(allEntries);
    const sorted = [];
    const visited = new Set();
    function visit(name) {
      if (visited.has(name)) return;
      visited.add(name);
      const val = cfgMap.get(name);
      if (!val) return;
      const parentRef = val.attach?.parent_name;
      if (parentRef && cfgMap.has(parentRef)) visit(parentRef);
      sorted.push([name, val]);
    }
    for (const [name] of allEntries) visit(name);

    // ── Phase 1: every blueprint in ONE round-trip ────────────────────
    // The old per-component /instantiate walk was O(n) serial fetches
    // that queued behind the GLB downloads on the browser's connection
    // pool — a 172-component project took minutes (or wedged outright:
    // the attach wait polled requestAnimationFrame, which stalls in a
    // background tab).
    const items = sorted.map(([cfgName, cfgVal]) => {
      const options = {};
      for (const [k, v] of Object.entries(cfgVal)) {
        if (k === "type" || k === "attach") continue;
        options[k] = v;
      }
      return { name: cfgName, type: cfgVal.type, options };
    });
    let batch = {};
    try {
      const res = await fetch(SB_API + "/instantiate_batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items }),
      });
      const js = await res.json();
      if (js && js.ok) batch = js.results || {};
    } catch (e) { console.warn("instantiate_batch failed — falling back to per-component", e); }

    // ── Phase 2: spawn everything (meshes load in parallel) ──────────
    const bulkSpecs = {};
    let __spawned = 0;
    for (const [cfgName, cfgVal] of sorted) {
      // Yield a real macrotask every few components: 341 back-to-back
      // spawns froze the tab for the whole import (awaits on resolved
      // promises never let the browser paint) — the page looked hung
      // on whatever was drawn last, usually "solving…".
      if (++__spawned % 5 === 0) {
        __projLoadBanner(`Importing scene — ${__spawned}/${sorted.length} components…`);
        await new Promise(r => setTimeout(r, 0));
      }
      const it = items.find(x => x.name === cfgName);
      const pre = batch[cfgName] || {};
      if (pre.error) { console.warn("loadConfig:", cfgName, pre.error); continue; }
      try {
        await spawnComponentSilent(cfgVal.type, null,
          Object.keys(it.options).length ? it.options : null, cfgName,
          { blueprint: pre.blueprint || null, noUndo: true, collect: bulkSpecs });
      } catch (e) {
        console.warn("loadConfig: failed to spawn", cfgName, e);
        continue;
      }
      if (srcFile && window.builderState.components[cfgName]) {
        window.builderState.components[cfgName].__file = srcFile;
      }
      if (cfgVal.type === "collision_box") __collisionToHide.push(cfgName);
      if (cfgVal.attach && window.builderState.components[cfgName]) {
        window.builderState.components[cfgName].attach = cfgVal.attach;
      }
    }
    // One state push for the whole import instead of one per component.
    try { socket.emit("upstream_update", bulkSpecs); } catch (e) {}

    // ── Phase 3: attach / place, in topo order ───────────────────────
    // Deadline-based mesh wait (setTimeout, NOT requestAnimationFrame —
    // rAF stops in occluded tabs and the old loop hung forever on it).
    // All loads started in phase 2, so total wait ≈ slowest mesh.
    const __meshReady = async (n, ms) => {
      const t0 = performance.now();
      while (performance.now() - t0 < ms) {
        const o = objectsByName.get(n);
        if (o && o.children && o.children.length) return true;
        await new Promise(r => setTimeout(r, 50));
      }
      return false;
    };
    for (const [cfgName, cfgVal] of sorted) {
      if (cfgVal.attach) {
        await __meshReady(cfgName, 5000);
        const att = cfgVal.attach;
        const parentName = att.parent_name;
        if (parentName && objectsByName.has(parentName)) {
          try {
            __snapChildToParentAnchor(
              cfgName,
              parentName,
              att.parent_anchor || "place",
              att.child_solid || "body",
              att.child_anchor || "center",
              att.offset || [0,0,0,0,0,0],
              att.parent_solid || null
            );
            // Guard against server echo overwriting this snapped position
            const snappedObj = objectsByName.get(cfgName);
            if (snappedObj) snappedObj.userData.__builderPoseGuard = performance.now();
          } catch(e) {
            console.warn("loadConfig: failed to attach", cfgName, "->", parentName, e);
          }
        }
      } else if (cfgVal.offset && Array.isArray(cfgVal.offset) && cfgVal.offset.length >= 3) {
        // Free-standing object with saved position — apply it and guard against server echo
        const freeObj = objectsByName.get(cfgName);
        if (freeObj) {
          const [px, py, pz, rx=0, ry=0, rz=0] = cfgVal.offset;
          freeObj.position.set(px, py, pz);
          freeObj.quaternion.copy(rodriguesDegToQuaternion(rx, ry, rz));
          freeObj.userData.__builderPoseGuard = performance.now();
          try { socket.emit("upstream_update", { [cfgName]: { pose: [px, py, pz, rx, ry, rz] } }); } catch(e) {}
        }
      }
    }

    // Collision boxes hide through the objects list's OWN mechanism
    // (ghost + eye toggle + the "Show all hidden (n)" button), so
    // unhiding is one click there. Delayed so their meshes exist.
    // Collision boxes start in the hidden list — root-invisible, so
    // late-loading content inherits it. One pass, no sweeps needed.
    for (const n of __collisionToHide) {
      if (!__hiddenObjects.has(n)) {
        try { __toggleObjectVisibility(n); } catch (_) {}
      }
    }

    try { window.__updateConfigPreview(); } catch(e) {}
    // Toast is shown by the caller (single combined message for a
    // multi-file load).
  }

  // Wire file input to load button. Upload loads a single file INTO the
  // currently selected file slot: it replaces that file's components,
  // leaving the other files' components in the scene (so cross-file
  // references still resolve).
  if (fileInput) {
    fileInput.addEventListener("change", async (e) => {
      const file = (e.target.files || [])[0];
      if (!file) return;
      try {
        const bs = window.builderState;
        const target = __activeFileName();
        const text = await file.text();
        const cfg = __parseSimpleYaml(text);
        if (!cfg || !Object.keys(cfg).length) { showToast("Empty or invalid config file"); fileInput.value = ""; return; }

        // Remove the components currently in the target file, then load
        // the uploaded ones tagged to it. Other files are untouched.
        const toDelete = Object.entries(bs.components)
          .filter(([, m]) => m && ((m.__file || bs.activeFile) === target))
          .map(([n]) => n);
        for (const n of toDelete) { try { __applyDelete(n); } catch(_) {} }
        if (toDelete.length) await new Promise(r => setTimeout(r, 150));

        await __loadConfigToScene(cfg, { clear: false, file: target });
        try { window.__renderFilesList && window.__renderFilesList(); } catch(_) {}
        showToast("Loaded " + file.name + " → " + target);
      } catch(err) {
        console.error("loadConfig error:", err);
        showToast("Failed to load config: " + (err.message || err));
      }
      fileInput.value = "";
    });
  }
  if (loadBtn) loadBtn.addEventListener("click", () => { fileInput?.click(); });

  // ── Project import: scenes + recipes ─────────────────────────────
  // The server resolves the active project's launch.yaml exactly the way
  // main.py does: scene files (contents, merge order) + the recipes
  // listing. Scenes auto-load ONLY into an empty world — right after the
  // New Scene reset — never over work in progress.
  // Loading status in the sidebar's controls row — the same
  // spinner+text idiom the orchestrator project page uses for
  // "Starting…". Text updates per scene file; hidden when done.
  function __projLoadBanner(text) {
    const el = document.getElementById("sbLoadStatus");
    if (!el) return;
    if (!text) { el.style.display = "none"; el.textContent = ""; return; }
    el.textContent = text;
    el.style.display = "";
  }

  // ── Refresh consistency ──────────────────────────────────────────
  // The server holds the components; these CLIENT-side bits — file
  // slots, per-component file tags, the hidden set — die on refresh
  // unless persisted. Saved per project; "New Scene" clears them.
  function __persistKey() {
    return "sb_persist::" +
      ((window.__projectBundle && window.__projectBundle.project) || "none");
  }
  window.__sbPersist = () => {
    try {
      const bs = window.builderState;
      const tags = {};
      for (const [n, m] of Object.entries(bs.components || {})) {
        if (m && m.__file) tags[n] = m.__file;
      }
      localStorage.setItem(__persistKey(), JSON.stringify({
        files: bs.files, activeFile: bs.activeFile,
        tags, hidden: [...__hiddenObjects],
      }));
    } catch (_) {}
  };
  setInterval(() => { try { window.__sbPersist(); } catch (_) {} }, 3000);

  async function __loadProjectBundle() {
    window.__importBusy = true;
    try { return await __loadProjectBundleInner(); }
    finally { window.__importBusy = false; }
  }
  async function __loadProjectBundleInner() {
    let j = null;
    try {
      j = await fetch(SB_API + "/project_bundle").then(r => r.json());
    } catch (e) { return; }
    if (!j || !j.ok || !j.project) return;
    window.__projectBundle = j;
    // Header identity: which project this builder session is editing.
    try {
      const base = j.project_name ||
        String(j.project).replace(/\/+$/, "").split("/").pop();
      const t = document.getElementById("sbHeadTitle");
      if (t && base) {
        t.textContent = "Scene Builder · ";
        const p = document.createElement("span");
        p.className = "proj";
        p.textContent = base;
        t.appendChild(p);
      }
    } catch (_) {}
    const bs = window.builderState;
    const empty = !bs.components || !Object.keys(bs.components).length;
    if (empty && j.scenes && j.scenes.length) {
      const __t0 = performance.now();
      const names = j.scenes.map(s => s.name);
      bs.files = names.slice();
      bs.activeFile = names[names.length - 1];
      try {
        let i = 0;
        for (const s of j.scenes) {
          i += 1;
          __projLoadBanner(`Loading scene — ${s.name} (${i}/${j.scenes.length})…`);
          if (!s.text && !s.cfg) { if (s.error) showToast(s.name + ": " + s.error, "bad"); continue; }
          try {
            // Server-parsed cfg is authoritative (real Jinja+YAML);
            // the client parser is only a fallback.
            const cfg = s.cfg || __parseSimpleYaml(s.text);
            if (cfg && Object.keys(cfg).length)
              await __loadConfigToScene(cfg, { clear: false, file: s.name });
          } catch (e) { console.error("project scene load", s.name, e); }
        }
      } finally {
        __projLoadBanner(null);
      }
      try { window.__renderFilesList && window.__renderFilesList(); } catch(_) {}
      const __ms = Math.round(performance.now() - __t0);
      const __n = Object.keys(bs.components || {}).length;
      console.log(`[import] ${__n} components in ${__ms} ms`);
      window.__importStats = { components: __n, ms: __ms };
      showToast(`Project loaded: ${names.join(" + ")} (${(__ms/1000).toFixed(1)}s)`);
    } else if (!empty) {
      // Refresh / navigate-back: components replayed from the server —
      // restore the client-side state saved for this project.
      try {
        const saved = JSON.parse(localStorage.getItem(__persistKey()) || "null");
        if (saved) {
          if (Array.isArray(saved.files) && saved.files.length) {
            bs.files = saved.files.slice();
            bs.activeFile = saved.activeFile || saved.files[0];
          }
          for (const [n, f] of Object.entries(saved.tags || {})) {
            if (bs.components[n]) bs.components[n].__file = f;
          }
          for (const n of (saved.hidden || [])) {
            if (!__hiddenObjects.has(n)) {
              try { __toggleObjectVisibility(n); } catch (_) {}
            }
          }
          try { window.__renderFilesList && window.__renderFilesList(); } catch (_) {}
        }
      } catch (_) {}
    }
    // The header indicator belongs to SCENE loading only — the recipe
    // IK solve that starts below shows its own per-row "solving…".
    __projLoadBanner(null);
    __renderRecipesList((j.recipes || []));
    // Warm the IK worker in the background so the first recipe select
    // is instant instead of waiting ~10s for the Workspace build.
    if ((j.recipes || []).some(r => r.component)) {
      fetch(SB_API + "/recipe_ik").then(x => x.json())
        .then(r => { if (r && r.ok) window.__ikInfo = r.recipes || {}; })
        .catch(() => {});
    }
  }

  // Pose the scene's core at the given joints — VIEW-only: re-fetch the
  // core blueprint at those joints and update the existing object's
  // solid holders (no socket emit, no scene-state change; a refresh
  // returns the robot to zeros).
  function __coreName() {
    const bs = window.builderState;
    const n = Object.keys(bs.components || {})
      .find(k => (bs.components[k] || {}).type === "core");
    if (n) return n;
    // The registry can lag the viewer after a refresh replay; the
    // platform guarantees the core component is literally named "core".
    return (window.__hasObject && window.__hasObject("core")) ? "core" : null;
  }

  async function __poseCoreAt(joints) {
    const bs = window.builderState;
    const coreName = __coreName();
    if (!coreName) { showToast("No core in the scene to pose"); return; }
    const meta = bs.components[coreName] || {};
    const options = {};
    for (const [k, v] of Object.entries(meta)) {
      if (k !== "type" && !k.startsWith("__")) options[k] = v;
    }
    // The socket-replay path registers components as bare {type} —
    // re-instantiating the core from that loses rail_cfg and the
    // platform defaults to the 500mm rail (the "rail shrinks when I
    // select a recipe" bug). Backfill the core's true config from
    // the project bundle once, and repair the registry so every
    // later caller sees the full meta.
    if (!options.rail_cfg || !options.rail_cfg.type) {
      try {
        if (!window.__coreCfgFromBundle) {
          const pb = await fetch(SB_API + "/project_bundle").then(r => r.json());
          for (const sc of (pb && pb.scenes) || []) {
            for (const [n, spec] of Object.entries(sc.cfg || {})) {
              if (spec && spec.type === "core") { window.__coreCfgFromBundle = spec; break; }
            }
            if (window.__coreCfgFromBundle) break;
          }
        }
        const src = window.__coreCfgFromBundle;
        if (src) {
          for (const [k, v] of Object.entries(src)) {
            if (k === "type" || k === "attach") continue;
            if (!(k in options)) options[k] = v;
          }
          bs.components[coreName] = { type: "core", ...options };
        }
      } catch (_) {}
    }
    options.joints = joints;
    try {
      const js = await fetch(SB_API + "/instantiate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: "core", options }),
      }).then(r => r.json());
      if (!js || !js.ok || !js.blueprint) throw new Error((js && js.error) || "instantiate failed");
      const meshes = [];
      for (const s of (js.blueprint.solids || [])) {
        if (s && s.glb) meshes.push({
          meshUrl: s.glb, pose: s.pose || [0, 0, 0, 0, 0, 0], solidName: s.solid,
          collisionLocal: s.collisionLocal || [], boxForGrip: !!s.boxForGrip,
        });
      }
      if (meshes.length && window.upsertObject)
        window.upsertObject(coreName, { meshes });
    } catch (e) {
      showToast("Robot pose failed: " + (e.message || e), "bad");
    }
  }

  // Recipes panel — one row per recipe from the project's recipes.j2.
  // The solve for ALL recipes starts in the background as soon as the
  // panel renders (one subprocess, cached by file mtimes server-side);
  // rows show a small "solving…" indicator until it lands, then every
  // row permanently shows its 8 joint values in small type. Clicking a
  // row moves the viewer robot to those joints.
  let __refSolve = null;
  let __refSolvePromise = null;
  // Breadcrumbs for the solve lifecycle — rows in /tmp/sb_perf.jsonl
  // alongside the server's start/done, so a stuck "solving…" shows
  // WHERE it died (fetch, store, or row update).
  function __solveCrumb(event, extra) {
    try { console.log("[solve]", event, extra || ""); } catch (_) {}
    try {
      fetch(SB_API + "/perf", { method: "POST", keepalive: true,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ crumb: event, ...(extra || {}) }) }).catch(() => {});
    } catch (_) {}
  }
  window.__importBusy = false;
  async function __ensureRefSolve() {
    // The import storm owns the connection pool; a solve fired into
    // it dies ("Failed to fetch") and costs a retry cycle. Wait for
    // the import to finish first — the solve is server-cached, so
    // this delays nothing real.
    let __waited = 0;
    while (window.__importBusy && __waited < 120000) {
      await new Promise(r => setTimeout(r, 500));
      __waited += 500;
    }
    if (!__refSolvePromise) {
      __solveCrumb("fetch_start");
      const __ctl = new AbortController();
      const __abortT = setTimeout(() => __ctl.abort(), 10000);
      __refSolvePromise = fetch(SB_API + "/solve_ref", { method: "POST", signal: __ctl.signal })
        .finally(() => clearTimeout(__abortT))
        .then(x => x.json())
        .then(res => {
          if (!res || !res.ok) throw new Error((res && res.error) || "solve failed");
          __refSolve = res.recipes || {};
          __solveCrumb("stored", { n: Object.keys(__refSolve).length });
          return __refSolve;
        })
        .catch(e => { __solveCrumb("fetch_failed", { err: String(e && e.message || e).slice(0, 120) });
                      __refSolvePromise = null; throw e; });
    }
    return __refSolvePromise;
  }

  // ── TCP drag: grab the flange, offsets solve through the recipe's
  // own core.IK (persistent worker, ~25ms/solve). Translation-only;
  // the offset's rotation stays the recipe's target_offset rotation.
  const __ikDrag = { recipe: null, info: null, marker: null, busy: false, queued: null };

  function __ikRod(a, b, c) {
    const T = window.__three.THREE;
    const v = new T.Vector3(a, b, c);
    const ang = v.length() * Math.PI / 180;
    const q = new T.Quaternion();
    if (ang > 1e-9) q.setFromAxisAngle(v.normalize(), ang);
    return q;
  }

  // Full readout for a selected recipe — every parameter the recipe's
  // core.IK call uses, plus the live offset/joints. ``joints`` may be
  // an array, a status string, or null (shows …).
  function __ikText(ri, sr, offset, joints, name) {
    // Solve INPUTS live in the editor fields (one place to read AND
    // edit); this readout is outputs only.
    const f = v => Math.round(v * 10) / 10;
    const jl = Array.isArray(joints) ? "[" + joints.map(f).join(", ") + "]" : (joints || "…");
    const lines = [];
    lines.push("anchor  " + (ri.component || "—") + " · " +
      ri.target_solid_name + "/" + ri.target_anchor);
    lines.push("offset  [" + offset.map(f).join(", ") + "]");
    lines.push("joints  " + jl);
    return lines.join("\n");
  }

  // ── Editable recipe parameters (session scratch) ──────────────────
  // Tap "edit" under a selected recipe: base/step/span/left/ref become
  // inputs. Every change re-solves at the CURRENT marker offset and
  // poses the robot. Nothing is written — refresh restores recipes.j2.
  function __ikParamEditor(name, sub) {
    const host = sub.parentElement;
    if (!host) return;
    let box = host.querySelector(".ik-prm-box");
    if (box && box.dataset.recipe === name) return;   // already mounted
    if (box) box.remove();
    const ri = (window.__ikInfo || {})[name];
    if (!ri || ri.error) return;
    box = document.createElement("div");
    box.className = "ik-prm-box";
    box.dataset.recipe = name;
    box.style.cssText = "display:flex;flex-direction:column;gap:3px;" +
      "font-size:11px;font-family:monospace;padding:4px 0 2px;";
    box.addEventListener("click", e => e.stopPropagation());

    const ov = __ikParams[name] || {};
    const val = k => (k in ov) ? ov[k] : ri[k];
    // An overridden (session-scratch) value reads accent; ⟲ resets.
    const markOv = (i, k) => {
      i.style.borderColor = (k in (__ikParams[name] || {}))
        ? "var(--sb-accent,#4f9cf9)" : "var(--sb-border,#8884)";
    };

    const mkNum = (label, key, step) => {
      const w = document.createElement("label");
      w.style.cssText = "display:flex;align-items:center;gap:3px;";
      w.append(label);
      const i = document.createElement("input");
      i.type = "number"; i.step = step; i.value = val(key);
      i.style.cssText = "width:52px;font:inherit;padding:1px 3px;" +
        "background:var(--sb-bg,transparent);color:inherit;" +
        "border:1px solid var(--sb-border,#8884);border-radius:4px;";
      markOv(i, key);
      i.addEventListener("change", () => {
        (__ikParams[name] ||= {})[key] = Number(i.value);
        markOv(i, key);
        __resolveNow();
      });
      w.appendChild(i);
      return w;
    };
    const row1 = document.createElement("div");
    row1.style.cssText = "display:flex;gap:8px;flex-wrap:wrap;align-items:center;";
    row1.appendChild(mkNum("base", "base_distance", "5"));
    row1.appendChild(mkNum("step", "rail_step", "1"));
    row1.appendChild(mkNum("span", "rail_span", "1"));
    const lw = document.createElement("label");
    lw.style.cssText = "display:flex;align-items:center;gap:3px;cursor:pointer;";
    const lc = document.createElement("input");
    lc.type = "checkbox"; lc.checked = !!val("left_approach");
    lc.addEventListener("change", () => {
      (__ikParams[name] ||= {}).left_approach = lc.checked;
      __resolveNow();
    });
    lw.append(lc, "left");
    row1.appendChild(lw);
    box.appendChild(row1);
    lc.addEventListener("change", () => markOv(lc, "left_approach"));
    markOv(lc, "left_approach");

    const row2 = document.createElement("div");
    row2.style.cssText = "display:flex;gap:4px;align-items:center;";
    row2.append("ref");
    const refI = document.createElement("input");
    refI.type = "text";
    const sr = (__refSolve || {})[name] || {};
    const refNow = ("ref_joints" in ov) ? ov.ref_joints : (sr.ref_joints || ri.ref_joints);
    refI.value = Array.isArray(refNow) ? refNow.map(v => Math.round(v * 10) / 10).join(", ") : "";
    refI.placeholder = "j0, j1, … j7";
    refI.style.cssText = "flex:1;font:inherit;padding:1px 4px;min-width:0;" +
      "background:var(--sb-bg,transparent);color:inherit;" +
      "border:1px solid var(--sb-border,#8884);border-radius:4px;";
    markOv(refI, "ref_joints");
    refI.addEventListener("change", () => {
      const js = refI.value.split(/[,\s]+/).filter(Boolean).map(Number);
      if (js.some(isNaN) || !js.length) { showToast("ref: numbers only", "bad"); return; }
      (__ikParams[name] ||= {}).ref_joints = js;
      markOv(refI, "ref_joints");
      __resolveNow();
    });
    row2.appendChild(refI);
    box.appendChild(row2);

    function __resolveNow() {
      // Re-solve at the current marker offset (or the recipe's own).
      if (__ikDrag.recipe !== name) return;
      const T = window.__three && window.__three.THREE;
      let off = ri.target_offset;
      if (T && __ikDrag.marker && __ikDrag.anchorM) {
        const local = __ikDrag.marker.position.clone()
          .applyMatrix4(__ikDrag.anchorM.clone().invert());
        off = [local.x, local.y, local.z,
               __ikDrag.off[3], __ikDrag.off[4], __ikDrag.off[5]];
      }
      __ikSolve(off);
    }

    host.insertBefore(box, sub);
  }

  function __ikDragStop() {
    document.querySelectorAll(".ik-prm-box").forEach(b => b.remove());
    const t = window.__three;
    if (__ikDrag.marker && t) t.scene.remove(__ikDrag.marker);
    if (__ikDrag.cleanup) { try { __ikDrag.cleanup(); } catch (_) {} }
    __ikDrag.recipe = null;
    __ikDrag.marker = null;
    __ikDrag.cleanup = null;
  }

  async function __ikDragStart(name, sub) {
    const t = window.__three;
    if (!t) return;
    __ikDragStop();
    let info = window.__ikInfo;
    if (!info) {
      sub.textContent = "starting IK worker…";
      let res = null;
      try {
        const raw = await fetch(SB_API + "/recipe_ik");
        if (raw.status === 404) throw new Error("endpoint missing — restart gui/server.py");
        res = await raw.json();
      } catch (e) {
        sub.textContent = "IK worker failed: " + (e.message || e);
        return;
      }
      if (!res.ok) { sub.textContent = "IK worker failed: " + (res.error || ""); return; }
      info = window.__ikInfo = res.recipes || {};
    }
    const ri = info[name];
    if (!ri || !ri.anchor_world) { showToast(name + ": no drag target"); return; }
    const T = t.THREE;
    const anchorM = new T.Matrix4().compose(
      new T.Vector3(ri.anchor_world[0], ri.anchor_world[1], ri.anchor_world[2]),
      __ikRod(ri.anchor_world[3], ri.anchor_world[4], ri.anchor_world[5]),
      new T.Vector3(1, 1, 1));
    const off = ri.target_offset.slice();
    // Marker group: free-drag ball + XYZ axis arrows (anchor frame).
    const marker = new T.Group();
    const ball = new T.Mesh(new T.SphereGeometry(9, 20, 14),
      new T.MeshBasicMaterial({ color: 0x4f9cf9, depthTest: false, transparent: true, opacity: 0.9 }));
    ball.renderOrder = 999;
    ball.userData.kind = "ball";
    marker.add(ball);
    const AX = [[1, 0, 0, 0xe05555], [0, 1, 0, 0x4caf50], [0, 0, 1, 0x4f9cf9]];
    const aq = __ikRod(ri.anchor_world[3], ri.anchor_world[4], ri.anchor_world[5]);
    for (const [ax, ay, az, col] of AX) {
      const dirL = new T.Vector3(ax, ay, az);
      const dirW = dirL.clone().applyQuaternion(aq);
      const g = new T.Group();
      const mat = new T.MeshBasicMaterial({ color: col, depthTest: false, transparent: true, opacity: 0.85 });
      const shaft = new T.Mesh(new T.CylinderGeometry(2.5, 2.5, 70, 10), mat);
      shaft.position.y = 44;
      const tip = new T.Mesh(new T.ConeGeometry(7, 18, 12), mat);
      tip.position.y = 88;
      for (const m of [shaft, tip]) { m.renderOrder = 999; m.userData.kind = "axis"; m.userData.axisW = dirW; g.add(m); }
      g.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), dirW);
      marker.add(g);
    }
    marker.position.copy(new T.Vector3(off[0], off[1], off[2]).applyMatrix4(anchorM));
    t.scene.add(marker);
    Object.assign(__ikDrag, { recipe: name, info: ri, marker, ball, anchorM, sub, off });
    // Selection brings the robot to ref_joints — ALWAYS available
    // (solved at boot, or pinned by hand exactly when the default
    // offset has no solution). No solve until the user drags.
    let sr = (__refSolve || {})[name] || {};
    if (!sr.ref_joints) {
      // Click raced the solve (or a failed fetch) — wait for it and
      // refill instead of showing "…" forever.
      if (sub) sub.textContent = "waiting for solve…";
      try { const sol = await __ensureRefSolve(); sr = (sol || {})[name] || {}; } catch (_) {}
      __solveCrumb("selection_late_sr", { name, got: !!sr.ref_joints });
    }
    if (sr.ref_joints) __poseCoreAt(sr.ref_joints);
    if (sub) sub.textContent = __ikText(ri, sr, off, sr.ref_joints, name);
    if (sub) __ikParamEditor(name, sub);

    const el = t.renderer.domElement;
    const ray = new T.Raycaster();
    const ndc = new T.Vector2();
    let dragging = false;
    const plane = new T.Plane();
    function toNdc(e) {
      const r = el.getBoundingClientRect();
      ndc.set(((e.clientX - r.left) / r.width) * 2 - 1,
              -((e.clientY - r.top) / r.height) * 2 + 1);
    }
    let axisDir = null;   // world-space axis lock, null = free plane drag
    let axisOrigin = null;
    const grabOff = new T.Vector3();   // marker − grab point: no jump on grab
    function setDim(active) {
      // visual feedback: the grabbed part stays vivid, the rest dims
      __ikDrag.marker && __ikDrag.marker.traverse(o => {
        if (!o.isMesh) return;
        o.material.opacity = (active === null) ? (o.userData.kind === "ball" ? 0.9 : 0.85)
          : (o.material === active ? 1.0 : 0.3);
      });
    }
    function axisPoint(origin, dir) {
      const w0 = origin.clone().sub(ray.ray.origin);
      const b = dir.dot(ray.ray.direction);
      const d = dir.dot(w0), ee = ray.ray.direction.dot(w0);
      const denom = 1 - b * b;
      if (Math.abs(denom) < 1e-6) return null;
      return origin.clone().add(dir.clone().multiplyScalar((b * ee - d) / denom));
    }
    function down(e) {
      if (!__ikDrag.marker) { cleanup(); return; }
      toNdc(e);
      ray.setFromCamera(ndc, t.camera);
      const hits = ray.intersectObject(__ikDrag.marker, true);
      if (!hits.length) return;
      dragging = true;
      t.controls.enabled = false;
      setDim(hits[0].object.material);
      el.style.cursor = "grabbing";
      const kind = hits[0].object.userData.kind;
      if (kind === "axis") {
        axisDir = hits[0].object.userData.axisW.clone().normalize();
        axisOrigin = __ikDrag.marker.position.clone();
        const p0 = axisPoint(axisOrigin, axisDir);
        grabOff.copy(__ikDrag.marker.position).sub(p0 || __ikDrag.marker.position);
      } else {
        axisDir = null;
        plane.setFromNormalAndCoplanarPoint(
          t.camera.getWorldDirection(new T.Vector3()).negate(),
          __ikDrag.marker.position);
        const h0 = new T.Vector3();
        grabOff.set(0, 0, 0);
        if (ray.ray.intersectPlane(plane, h0))
          grabOff.copy(__ikDrag.marker.position).sub(h0);
      }
      e.stopPropagation();
    }
    let hovered = null;
    function setHover(m) {
      // hover feedback: the part under the cursor goes vivid + a touch
      // bigger; everything else keeps its resting look
      __ikDrag.marker && __ikDrag.marker.traverse(o => {
        if (!o.isMesh) return;
        const hot = o.material === m;
        o.material.opacity = hot ? 1.0 : (o.userData.kind === "ball" ? 0.9 : 0.85);
        o.scale.setScalar(hot ? 1.18 : 1.0);
      });
    }
    function move(e) {
      if (!__ikDrag.marker) return;
      if (!dragging) {
        toNdc(e);
        ray.setFromCamera(ndc, t.camera);
        const h = ray.intersectObject(__ikDrag.marker, true);
        const m = h.length ? h[0].object.material : null;
        if (m !== hovered) {
          hovered = m;
          setHover(m);
          el.style.cursor = m ? "grab" : "";
        }
        return;
      }
      toNdc(e);
      ray.setFromCamera(ndc, t.camera);
      let hit;
      if (axisDir) {
        hit = axisPoint(axisOrigin, axisDir);
        if (!hit) return;
      } else {
        hit = new T.Vector3();
        if (!ray.ray.intersectPlane(plane, hit)) return;
      }
      hit.add(grabOff);
      __ikDrag.marker.position.copy(hit);
      const local = hit.clone().applyMatrix4(__ikDrag.anchorM.clone().invert());
      const o = [local.x, local.y, local.z, __ikDrag.off[3], __ikDrag.off[4], __ikDrag.off[5]];
      __ikSolve(o);
    }
    function up() {
      if (dragging) {
        dragging = false;
        t.controls.enabled = true;
        hovered = null;
        setHover(null);
        el.style.cursor = "";
      }
    }
    function cleanup() {
      el.removeEventListener("pointerdown", down, true);
      el.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      el.style.cursor = "";
    }
    el.addEventListener("pointerdown", down, true);
    el.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    __ikDrag.cleanup = cleanup;
  }

  // Session-only recipe parameter overrides (base/step/span/left/ref).
  // Scratch for experiments: sent with every solve of that recipe,
  // never persisted — a refresh reloads recipes.j2 truth.
  const __ikParams = {};

  async function __ikSolve(offset) {
    if (__ikDrag.busy) { __ikDrag.queued = offset; return; }   // send-latest
    __ikDrag.busy = true;
    try {
      const res = await fetch(SB_API + "/recipe_ik", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recipe: __ikDrag.recipe, offset,
                               params: __ikParams[__ikDrag.recipe] || undefined }),
      }).then(x => x.json());
      const coreName = __coreName();
      if (res.ok && res.solids) {
        if (!coreName) throw new Error("no core object in the scene");
        if (!window.__setSolidPosesWorld) throw new Error("viewer not ready (pose setter missing)");
        window.__setSolidPosesWorld(coreName, res.solids);
        if (__ikDrag.ball) __ikDrag.ball.material.color.set(0x4f9cf9);
        if (__ikDrag.sub) __ikDrag.sub.textContent = __ikText(
          __ikDrag.info, (__refSolve || {})[__ikDrag.recipe], offset, res.joints,
          __ikDrag.recipe);
        __ikParamEditor(__ikDrag.recipe, __ikDrag.sub);
      } else {
        // genuine no-solution from the solver
        if (__ikDrag.ball) __ikDrag.ball.material.color.set(0xff8a2b);
        if (__ikDrag.sub) __ikDrag.sub.textContent = __ikText(
          __ikDrag.info, (__refSolve || {})[__ikDrag.recipe], offset,
          "no solution (status " + (res.status ?? res.error ?? "?") + ")",
          __ikDrag.recipe);
        __ikParamEditor(__ikDrag.recipe, __ikDrag.sub);
      }
    } catch (e) {
      // client-side failure ≠ unreachable — say so, loudly
      console.error("recipe_ik apply failed:", e);
      if (__ikDrag.sub) __ikDrag.sub.textContent = "apply failed: " + (e.message || e);
    }
    __ikDrag.busy = false;
    if (__ikDrag.queued) { const q = __ikDrag.queued; __ikDrag.queued = null; __ikSolve(q); }
  }

  function __renderRecipesList(recipes) {
    const sec = document.getElementById("sbRecipesSection");
    const list = document.getElementById("sbRecipesList");
    if (!sec || !list) return;
    if (!recipes.length) { sec.style.display = "none"; return; }
    sec.style.display = "";
    list.innerHTML = "";
    const subs = {};
    for (const r of recipes) {
      const row = document.createElement("div");
      row.className = "sb-file-row";
      row.style.cssText = "flex-direction:column;align-items:stretch;gap:0;" +
        "padding:3px 8px;border-radius:6px;";
      row.title = (r.class || "") + (r.component ? " · " + r.component : "");
      const head = document.createElement("span");
      head.style.cssText = "display:flex;align-items:center;gap:4px;";
      const nameEl = document.createElement("span");
      nameEl.className = "sb-file-name";
      nameEl.style.cssText = "font-size:11px;flex:1;";
      nameEl.textContent = r.name;
      head.appendChild(nameEl);
      let resetB = null;
      if (r.component) {
        resetB = document.createElement("button");
        resetB.className = "btn btn-ghost btn-sm btn-icon";
        resetB.style.cssText = "width:18px;height:18px;min-height:18px;padding:0;" +
          "font-size:11px;line-height:1;display:none;";
        resetB.title = "Back to the recipe's own pose";
        resetB.textContent = "⟲";
        resetB.addEventListener("click", (e) => {
          e.stopPropagation();
          // Full reset: marker pose AND session parameter scratch —
          // back to recipes.j2 truth (nothing was ever written).
          delete __ikParams[r.name];
          const box = document.querySelector('.ik-prm-box[data-recipe="' + r.name + '"]');
          if (box) box.remove();
          const ri = (window.__ikInfo || {})[r.name];
          const sr = (__refSolve || {})[r.name] || {};
          if (__ikDrag.recipe === r.name && __ikDrag.marker && ri) {
            const T = window.__three.THREE;
            const o = ri.target_offset;
            __ikDrag.marker.position.copy(
              new T.Vector3(o[0], o[1], o[2]).applyMatrix4(__ikDrag.anchorM));
            if (__ikDrag.ball) __ikDrag.ball.material.color.set(0x4f9cf9);
            if (sr.ref_joints) __poseCoreAt(sr.ref_joints);
            if (__ikDrag.sub) __ikDrag.sub.textContent = __ikText(ri, sr, o, sr.ref_joints, r.name);
            __ikParamEditor(r.name, __ikDrag.sub);
          }
        });
        head.appendChild(resetB);
      }
      row.appendChild(head);
      const sub = document.createElement("span");
      sub.style.cssText = "display:none;font-size:12px;font-family:monospace;" +
        "color:var(--fg,inherit);line-height:1.55;white-space:pre-line;" +
        "word-break:break-all;padding-top:3px;";
      row.appendChild(sub);
      subs[r.name] = { sub, row, resetB, recipe: r };
      row.addEventListener("click", () => {
        const sr = (__refSolve || {})[r.name];
        if (sr && sr.error) { showToast(r.name + ": " + sr.error, "bad"); return; }
        if (!sr) { showToast("Still solving — one moment"); return; }
        // Selection model: click selects, click again deselects, one
        // at a time. Station recipes get the drag marker + live IK;
        // componentless ones just show and pose their joints.
        const wasActive = row.classList.contains("active");
        __ikDragStop();
        for (const o of Object.values(subs)) {
          o.row.classList.remove("active");
          o.sub.style.display = "none";
          if (o.resetB) o.resetB.style.display = "none";
        }
        if (wasActive) return;
        row.classList.add("active");
        sub.style.display = "";
        if (r.component) {
          if (resetB) resetB.style.display = "";
          sub.textContent = "offset  …\njoints  …";
          __ikDragStart(r.name, sub);
        } else if (sr.ref_joints) {
          sub.textContent = "joints  [" +
            sr.ref_joints.map(v => Math.round(v * 10) / 10).join(", ") + "]";
          __poseCoreAt(sr.ref_joints);
        } else {
          sub.textContent = "no reference pose";
        }
      });
      list.appendChild(row);
    }
    // solving indicator: header phase + per-row text until the solve
    // lands, then rows go clean (values only on the SELECTED readout).
    const __markSolving = () => {
      for (const o of Object.values(subs)) {
        if (o.recipe.component) {
          o.sub.style.display = "";
          o.sub.textContent = "solving…";
        }
      }
      if (!__refSolve && Object.values(subs).some(o => o.recipe.component)) {
        __projLoadBanner("Solving recipes…");
      }
    };
    // While the import owns the page, the rows say nothing — labeling
    // that window "solving…" made import time read as solve time.
    if (!window.__importBusy) __markSolving();
    else {
      const __t = setInterval(() => {
        if (!window.__importBusy) { clearInterval(__t); __markSolving(); }
      }, 500);
    }
    // The first load's fetch can die mid-import ("Failed to fetch" —
    // the import storm kills the connection while the server solves
    // fine and caches). One failure used to strand the rows on
    // "solving…" until a manual refresh; now it retries — and the
    // retry hits the server's cache, so it lands instantly.
    let __attempts = 0;
    const __attempt = () => {
      __ensureRefSolve().then(sol => {
        __solveCrumb("rows_update", { rows: Object.keys(subs).length });
        __projLoadBanner(null);
        for (const [name, o] of Object.entries(subs)) {
          const sr = (sol || {})[name] || {};
          if (sr.error) {
            o.sub.style.display = "";
            o.sub.textContent = "error: " + sr.error;
          } else if (__ikDrag.recipe !== name) {
            o.sub.style.display = "none";
            o.sub.textContent = "";
          }
          if (o.recipe.component && !sr.error) {
            o.row.style.cursor = "pointer";
            o.row.title += " — click to select; drag the marker to move the robot";
          }
        }
      }).catch(e => {
        __attempts++;
        if (__attempts < 6) {
          __solveCrumb("retry", { attempt: __attempts });
          for (const o of Object.values(subs)) {
            if (o.recipe.component) o.sub.textContent = `solving… (retry ${__attempts})`;
          }
          setTimeout(__attempt, 2500);
          return;
        }
        __projLoadBanner(null);
        for (const o of Object.values(subs)) {
          if (o.recipe.component) {
            o.sub.style.display = "";
            o.sub.textContent = "solve failed: " + (e.message || e);
          }
        }
      });
    };
    __attempt();
  }
  // After the socket has had a chance to replay any existing world state
  // (connect -> scene_update), import the project. The empty-world check
  // inside makes a late/duplicate call harmless.
  setTimeout(__loadProjectBundle, 1500);

  // ── Files list: rows with a radio (active target) + count + delete ──
  const filesListEl = document.getElementById("sbFilesList");
  function renderFilesList() {
    if (!filesListEl) return;
    const bs = window.builderState;
    if (!bs.files || !bs.files.length) bs.files = ["scene.j2"];
    if (!bs.activeFile || !bs.files.includes(bs.activeFile)) bs.activeFile = bs.files[0];

    // Count components per file (lazy-tag first so new ones are counted).
    __assignUntaggedToActive();
    const counts = {};
    for (const f of bs.files) counts[f] = 0;
    for (const meta of Object.values(bs.components)) {
      const f = (meta && meta.__file) || bs.activeFile;
      counts[f] = (counts[f] || 0) + 1;
    }

    filesListEl.innerHTML = "";
    for (const fname of bs.files) {
      const row = document.createElement("div");
      row.className = "sb-file-row" + (fname === bs.activeFile ? " active" : "");
      row.title = "Click to make this the target for new items · double-click the name to rename · drag to reorder";
      row.draggable = true;
      row.dataset.file = fname;

      // Drag handle (grip) — affordance for reordering.
      const grip = document.createElement("span");
      grip.className = "sb-file-grip";
      grip.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg>';
      row.appendChild(grip);

      const radio = document.createElement("span");
      radio.className = "sb-file-radio";
      row.appendChild(radio);

      const nameEl = document.createElement("span");
      nameEl.className = "sb-file-name";
      nameEl.textContent = fname;
      nameEl.title = "Double-click to rename";
      // Double-click the name to rename — updates the file list, the
      // active target if needed, and every component tagged with it.
      nameEl.addEventListener("dblclick", async (e) => {
        e.stopPropagation();
        let next = await __sbTextModal({
          title: "Rename file",
          label: "File name",
          value: fname,
          placeholder: "scene.j2",
          okLabel: "Rename",
          hint: "Components in this file are written here on save.",
        });
        if (next === null) return;
        next = next.trim();
        if (!next || next === fname) return;
        if (!/\.(j2|yaml|yml)$/.test(next)) next += ".j2";
        if (bs.files.includes(next)) { showToast("A file named " + next + " already exists", "bad"); return; }
        const idx = bs.files.indexOf(fname);
        if (idx >= 0) bs.files[idx] = next;
        for (const meta of Object.values(bs.components)) {
          if (meta && meta.__file === fname) meta.__file = next;
        }
        if (bs.activeFile === fname) bs.activeFile = next;
        renderFilesList();
      });
      row.appendChild(nameEl);

      const countEl = document.createElement("span");
      countEl.className = "sb-file-count";
      countEl.textContent = counts[fname] || 0;
      row.appendChild(countEl);

      // Drag-and-drop reorder. File order is the merge order (later
      // files override earlier ones, like ``scene: [base.j2,
      // layout.j2]``). Dropping a row before/after another reorders
      // ``builderState.files``.
      row.addEventListener("dragstart", (e) => {
        row.classList.add("dragging");
        try { e.dataTransfer.setData("text/plain", fname); e.dataTransfer.effectAllowed = "move"; } catch(_) {}
      });
      row.addEventListener("dragend", () => {
        row.classList.remove("dragging");
        filesListEl.querySelectorAll(".sb-file-row").forEach(r => r.classList.remove("drop-above", "drop-below"));
      });
      row.addEventListener("dragover", (e) => {
        e.preventDefault();
        try { e.dataTransfer.dropEffect = "move"; } catch(_) {}
        const rect = row.getBoundingClientRect();
        const below = (e.clientY - rect.top) > rect.height / 2;
        row.classList.toggle("drop-below", below);
        row.classList.toggle("drop-above", !below);
      });
      row.addEventListener("dragleave", () => {
        row.classList.remove("drop-above", "drop-below");
      });
      row.addEventListener("drop", (e) => {
        e.preventDefault();
        let src;
        try { src = e.dataTransfer.getData("text/plain"); } catch(_) { src = null; }
        row.classList.remove("drop-above", "drop-below");
        if (!src || src === fname) return;
        const rect = row.getBoundingClientRect();
        const below = (e.clientY - rect.top) > rect.height / 2;
        const from = bs.files.indexOf(src);
        if (from < 0) return;
        bs.files.splice(from, 1);
        let to = bs.files.indexOf(fname);
        if (below) to += 1;
        bs.files.splice(to, 0, src);
        renderFilesList();
      });

      // Delete (only if more than one file remains). Removing a file
      // DELETES its components from the scene — the old behavior silently
      // moved them to the first file, which read as "nothing happened".
      // The whole batch lands in ONE undo entry, so ctrl-Z restores the
      // components (the file row itself is re-added via rename/add).
      if (bs.files.length > 1) {
        const del = document.createElement("span");
        del.className = "sb-file-del";
        del.textContent = "×";
        del.title = "Remove file and delete its items from the scene";
        del.addEventListener("click", (e) => {
          e.stopPropagation();
          const idx = bs.files.indexOf(fname);
          if (idx < 0) return;

          __assignUntaggedToActive();
          const doomed = Object.entries(bs.components)
            .filter(([, m]) => m && m.__file === fname)
            .map(([n]) => n);

          // One combined undo entry for the whole batch.
          if (doomed.length) {
            try {
              const specs = {}, metas = {};
              for (const n of doomed) {
                specs[n] = __deepClone(bs.specs?.[n] || null);
                metas[n] = __deepClone(bs.components?.[n] || null);
              }
              __pushUndo({ kind: "delete", names: [...doomed], specs, metas });
            } catch (_) {}
          }

          // Delete with per-item undo suppressed — the batch entry above
          // is the single history record.
          bs.suspendUndo = true;
          try {
            for (const n of doomed) {
              try { __deleteComponentByName(n); } catch (_) {}
            }
          } finally {
            bs.suspendUndo = false;
          }

          bs.files.splice(idx, 1);
          if (bs.activeFile === fname) bs.activeFile = bs.files[0];
          renderFilesList();
          try { if (window.__updateConfigPreview) window.__updateConfigPreview(); } catch (_) {}
          showToast(doomed.length
            ? `Removed ${fname} — deleted ${doomed.length} component(s)`
            : `Removed ${fname}`);
        });
        row.appendChild(del);
      }

      row.addEventListener("click", () => {
        bs.activeFile = fname;
        // Full preview refresh (not just the list): in "selected file"
        // scope the text must follow the newly-selected file instantly.
        updateConfigPreview();
      });
      filesListEl.appendChild(row);
    }

    // "+ add file" button.
    const add = document.createElement("button");
    add.className = "sb-file-add";
    add.textContent = "+ add file";
    add.addEventListener("click", async () => {
      let name = await __sbTextModal({
        title: "Add file",
        label: "File name",
        value: "",
        placeholder: "layout.j2",
        okLabel: "Add",
        hint: "New components you add will be written to this file.",
      });
      if (!name) return;
      name = name.trim();
      if (!name) return;
      if (!/\.(j2|yaml|yml)$/.test(name)) name += ".j2";
      if (!bs.files.includes(name)) bs.files.push(name);
      bs.activeFile = name;   // new file becomes the active target
      renderFilesList();
    });
    filesListEl.appendChild(add);
  }
  window.__renderFilesList = renderFilesList;

  // ── Preview scope: the merged config of every file, or only the
  //    selected (active) file's items. Save/download is unaffected —
  //    this only filters what the preview text shows. ──
  let __previewScope = "all";   // "all" | "file"
  const __scopeAllBtn  = document.getElementById("sbScopeAll");
  const __scopeFileBtn = document.getElementById("sbScopeFile");
  function __syncScopeButtons() {
    if (!__scopeAllBtn || !__scopeFileBtn) return;
    __scopeAllBtn.classList.toggle("active", __previewScope === "all");
    __scopeFileBtn.classList.toggle("active", __previewScope === "file");
    // Static tags ("all files" / "selected file"); the file list above
    // already shows which file is selected.
    __scopeFileBtn.title = "Show only " + (window.builderState.activeFile || "the selected file");
  }
  __scopeAllBtn?.addEventListener("click", () => { __previewScope = "all"; updateConfigPreview(); });
  __scopeFileBtn?.addEventListener("click", () => { __previewScope = "file"; updateConfigPreview(); });

  function updateConfigPreview() {
    try {
      renderFilesList();
      __syncScopeButtons();
      const bs = window.builderState;
      const cfg = (__previewScope === "file")
        ? (buildConfigByFile()[bs.activeFile] || {})
        : buildConfigObject();
      const keys = Object.keys(cfg);
      if (!keys.length) {
        pre.textContent = (__previewScope === "file")
          ? "# No components in " + (bs.activeFile || "this file")
          : "# No components yet";
        return;
      }
      // Syntax highlight YAML
      const yaml = toYamlString(cfg);
      // Simple colorize: keys in blue, strings in green, numbers in orange, comments in gray
      const lines = yaml.split("\n");
      let html = "";
      for (const line of lines) {
        if (line.trim().startsWith("#")) {
          html += `<span style="color:rgba(255,255,255,0.35)">${escHtml(line)}</span>\n`;
        } else {
          // colorize key: value
          const m = line.match(/^(\s*)([\w_.-]+)(:)(.*)/);
          if (m) {
            const indent = m[1];
            const key = m[2];
            const colon = m[3];
            let val = m[4];
            let valHtml = escHtml(val);
            // Colorize values
            if (val.trim().startsWith('"') || val.trim().startsWith("'")) {
              valHtml = `<span style="color:#98c379">${escHtml(val)}</span>`;
            } else if (val.trim().startsWith("[")) {
              valHtml = `<span style="color:#d19a66">${escHtml(val)}</span>`;
            } else if (/^\s*-?\d/.test(val)) {
              valHtml = `<span style="color:#d19a66">${escHtml(val)}</span>`;
            } else if (val.trim() === "true" || val.trim() === "false") {
              valHtml = `<span style="color:#e5c07b">${escHtml(val)}</span>`;
            } else if (val.trim() === "null") {
              valHtml = `<span style="color:rgba(255,255,255,0.4)">${escHtml(val)}</span>`;
            }
            html += `${escHtml(indent)}<span style="color:#61afef">${escHtml(key)}</span><span style="color:rgba(255,255,255,0.5)">${escHtml(colon)}</span>${valHtml}\n`;
          } else {
            html += escHtml(line) + "\n";
          }
        }
      }
      pre.innerHTML = html;
    } catch(e) {
      pre.textContent = "# Error generating config\n# " + (e?.message || e);
    }
  }

  function escHtml(s) {
    return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }

  // Safety-net refresh (mutation sites call __updateConfigPreview
  // directly). Serializing a 300+ component bench to YAML costs
  // hundreds of ms, so the sweep must never fire mid-interaction:
  // skip while a pointer button is down (orbit/drag — this WAS the
  // "2s smooth, 0.5s hitch" cycle), while the pane is hidden, or
  // while text is selected in the pre. 5s cadence is plenty for a
  // fallback.
  let __pointerHeld = false;
  let __lastActivity = 0;
  const __touch = () => { __lastActivity = performance.now(); };
  window.addEventListener("pointerdown", () => { __pointerHeld = true; __touch(); }, true);
  window.addEventListener("pointerup",   () => { __pointerHeld = false; __touch(); }, true);
  window.addEventListener("pointercancel", () => { __pointerHeld = false; }, true);
  // Scroll/wheel/moves count as activity too: the sweep serializes a
  // 341-component bench to YAML (~hundreds of ms) and used to fire
  // mid-scroll — the "left panel feels laggy" report.
  window.addEventListener("wheel", __touch, { capture: true, passive: true });
  window.addEventListener("scroll", __touch, { capture: true, passive: true });
  window.addEventListener("pointermove", __touch, { capture: true, passive: true });
  setInterval(() => {
    if (__pointerHeld) return;
    if (performance.now() - __lastActivity < 2000) return;
    if (pre && pre.offsetParent === null) return;
    const sel = window.getSelection();
    if (sel && sel.rangeCount && pre && pre.contains(sel.anchorNode) && !sel.isCollapsed) return;
    updateConfigPreview();
  }, 5000);
  window.__updateConfigPreview = updateConfigPreview;
  // Initial render
  if (pre) updateConfigPreview();
})();

      // --- Home position / snap-to-view ---
      const HOME_POS    = new THREE.Vector3(1600, 1600, 1600);   // true isometric
      const HOME_TARGET = new THREE.Vector3(0, 0, 0);
      const HOME_UP     = new THREE.Vector3(0, 0, 1);
      let snapTarget = null;

      function snapCamera(toPos, toTarget, toUp) {
        const dist = camera.position.distanceTo(controls.target);
        const dir  = toPos.clone().normalize();
        snapTarget = {
          pos:    dir.clone().multiplyScalar(dist),
          target: toTarget ? toTarget.clone() : controls.target.clone(),
          up:     toUp ? toUp.clone() : HOME_UP.clone(),
          t: 0
        };
      }

      function updateSnap() {
        if (!snapTarget) return;
        snapTarget.t = Math.min(1, snapTarget.t + 0.08);
        const t = 1 - Math.pow(1 - snapTarget.t, 3);
        camera.position.lerp(snapTarget.pos, t);
        controls.target.lerp(snapTarget.target, t);
        camera.up.lerp(snapTarget.up, t);
        camera.up.normalize();
        controls.update();
        if (snapTarget.t >= 1) snapTarget = null;
      }

      document.getElementById("btnHome")?.addEventListener("click", () => {
        snapCamera(HOME_POS, HOME_TARGET, HOME_UP);
      });

      // --- ViewCube (matches orchestrator exactly) ---
      const vcCanvas = document.getElementById("viewCubeCanvas");
      const vcRenderer = new THREE.WebGLRenderer({ canvas: vcCanvas, antialias: true, alpha: true, powerPreference: "high-performance" });
      vcRenderer.setPixelRatio(window.devicePixelRatio);
      vcRenderer.setSize(130, 130);
      vcRenderer.setClearColor(0x000000, 0);

      const vcScene  = new THREE.Scene();
      const vcCamera = new THREE.OrthographicCamera(-1.7, 1.7, 1.7, -1.7, 0.1, 100);
      vcCamera.position.set(0, 0, 6);
      vcCamera.lookAt(0, 0, 0);
      vcScene.add(new THREE.AmbientLight(0xffffff, 1.0));

      const VC_HALF = 0.85;
      const VC_ETHR = 0.38;

      function vcRR(ctx, x, y, w, h, r) {
        ctx.moveTo(x+r, y); ctx.lineTo(x+w-r, y);
        ctx.quadraticCurveTo(x+w, y, x+w, y+r); ctx.lineTo(x+w, y+h-r);
        ctx.quadraticCurveTo(x+w, y+h, x+w-r, y+h); ctx.lineTo(x+r, y+h);
        ctx.quadraticCurveTo(x, y+h, x, y+h-r); ctx.lineTo(x, y+r);
        ctx.quadraticCurveTo(x, y, x+r, y); ctx.closePath();
      }

      function makeFaceTex(label, bg, fg = "#1a2840") {
        const c = document.createElement("canvas");
        c.width = c.height = 128;
        const ctx = c.getContext("2d");
        ctx.fillStyle = bg;
        ctx.beginPath(); vcRR(ctx, 4, 4, 120, 120, 10); ctx.fill();
        const zoneFromEdge = Math.round(120 * VC_ETHR / (2 * VC_HALF));
        const z = 4 + zoneFromEdge;
        ctx.strokeStyle = "rgba(0,0,0,0.12)"; ctx.lineWidth = 1;
        ctx.strokeRect(z, z, 128 - 2*z, 128 - 2*z);
        ctx.strokeStyle = "rgba(0,0,0,0.22)"; ctx.lineWidth = 2;
        ctx.beginPath(); vcRR(ctx, 4, 4, 120, 120, 10); ctx.stroke();
        ctx.fillStyle = fg;
        ctx.font = "bold 24px system-ui, sans-serif";
        ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(label, 64, 64);
        const tex = new THREE.CanvasTexture(c);
        tex.colorSpace = THREE.SRGBColorSpace;
        return tex;
      }

      const FACE_BG = "#d8e4f0";
      const TOP_BG  = "#b8d0ea";
      // BoxGeometry face order: +X, -X, +Y, -Y, +Z, -Z
      const vcFaces = [
        { l: "R",     bg: FACE_BG },
        { l: "L",     bg: FACE_BG },
        { l: "BACK",  bg: FACE_BG },
        { l: "FRONT", bg: FACE_BG },
        { l: "TOP",   bg: TOP_BG  },
        { l: "BOT",   bg: FACE_BG },
      ];
      const vcMats = vcFaces.map(f => new THREE.MeshBasicMaterial({ map: makeFaceTex(f.l, f.bg) }));
      const vcCube = new THREE.Mesh(new THREE.BoxGeometry(1.7, 1.7, 1.7), vcMats);

      const VC_CORNER_DIRS = [
        [+1,+1,+1],[+1,+1,-1],[+1,-1,+1],[+1,-1,-1],
        [-1,+1,+1],[-1,+1,-1],[-1,-1,+1],[-1,-1,-1],
      ];
      const vcCornerGeo = new THREE.SphereGeometry(0.22, 10, 10);
      const vcCornerMeshes = VC_CORNER_DIRS.map(d => {
        const m = new THREE.Mesh(vcCornerGeo, new THREE.MeshBasicMaterial({ color: "#6b9ac4" }));
        m.position.set(d[0]*VC_HALF, d[1]*VC_HALF, d[2]*VC_HALF);
        m.userData.vcType = "corner"; m.userData.vcDir = d;
        return m;
      });

      const VC_EDGE_DIRS = [
        [0,+1,+1],[0,+1,-1],[0,-1,+1],[0,-1,-1],
        [+1,0,+1],[+1,0,-1],[-1,0,+1],[-1,0,-1],
        [+1,+1,0],[+1,-1,0],[-1,+1,0],[-1,-1,0],
      ];
      const VC_EBAR_THICK = 0.20;
      const VC_EBAR_LEN   = 1.7 - 2 * 0.30;
      const vcEdgeMeshes = VC_EDGE_DIRS.map(d => {
        const freeX = d[0] === 0, freeY = d[1] === 0, freeZ = d[2] === 0;
        const m = new THREE.Mesh(
          new THREE.BoxGeometry(
            freeX ? VC_EBAR_LEN : VC_EBAR_THICK,
            freeY ? VC_EBAR_LEN : VC_EBAR_THICK,
            freeZ ? VC_EBAR_LEN : VC_EBAR_THICK
          ),
          new THREE.MeshBasicMaterial({ color: "#5a88b0" })
        );
        m.position.set(d[0]*VC_HALF, d[1]*VC_HALF, d[2]*VC_HALF);
        m.userData.vcType = "edge"; m.userData.vcDir = d;
        return m;
      });

      const vcGroup = new THREE.Group();
      vcGroup.add(vcCube);
      vcCornerMeshes.forEach(m => vcGroup.add(m));
      vcEdgeMeshes.forEach(m => vcGroup.add(m));
      vcScene.add(vcGroup);

      const VC_FACE_NORMALS = [
        [1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1]
      ];

      function vcSnapFromDir(dx, dy, dz) {
        const len = Math.sqrt(dx*dx + dy*dy + dz*dz);
        const nx = dx/len, ny = dy/len, nz = dz/len;
        const dist = camera.position.distanceTo(controls.target);
        const t = controls.target;
        const up = Math.abs(nz) > 0.98
          ? new THREE.Vector3(1, 0, 0)
          : new THREE.Vector3(0, 0, 1);
        return [
          new THREE.Vector3(t.x + nx*dist, t.y + ny*dist, t.z + nz*dist),
          t.clone(), up,
        ];
      }

      const VC_FACE_DIM = "#9db0c0";
      function vcResetHighlight() {
        vcMats.forEach(m => m.color.set("#ffffff"));
        vcCornerMeshes.forEach(m => m.material.color.set("#6b9ac4"));
        vcEdgeMeshes.forEach(m => m.material.color.set("#5a88b0"));
      }
      function vcApplyHighlight(hit) {
        vcResetHighlight();
        if (!hit) return;
        const type = hit.object.userData.vcType;
        if (type === "corner") {
          hit.object.material.color.set("#f5a623");
          vcMats.forEach(m => m.color.set(VC_FACE_DIM));
        } else if (type === "edge") {
          hit.object.material.color.set("#4f9cf9");
          vcMats.forEach(m => m.color.set(VC_FACE_DIM));
        } else {
          const fi = hit.face.materialIndex;
          vcMats.forEach((m, i) => m.color.set(i === fi ? "#1a66cc" : VC_FACE_DIM));
        }
      }

      const vcRaycaster = new THREE.Raycaster();
      const vcPointer   = new THREE.Vector2();
      const vcAllMeshes = [vcCube, ...vcCornerMeshes, ...vcEdgeMeshes];

      function vcSetPointer(e) {
        const r = vcCanvas.getBoundingClientRect();
        vcPointer.x =  ((e.clientX - r.left) / r.width)  * 2 - 1;
        vcPointer.y = -((e.clientY - r.top)  / r.height) * 2 + 1;
      }

      let vcHoveredHit = null;
      vcCanvas.addEventListener("mousemove", (e) => {
        vcSetPointer(e);
        vcRaycaster.setFromCamera(vcPointer, vcCamera);
        const hits = vcRaycaster.intersectObjects(vcAllMeshes, false);
        if (hits.length) {
          vcHoveredHit = hits[0]; vcApplyHighlight(hits[0]);
          vcCanvas.style.cursor = "pointer";
        } else {
          vcHoveredHit = null; vcApplyHighlight(null);
          vcCanvas.style.cursor = "default";
        }
      });
      vcCanvas.addEventListener("mouseleave", () => {
        vcHoveredHit = null; vcApplyHighlight(null);
        vcCanvas.style.cursor = "default";
      });
      // ViewCube interaction: click to snap, drag to orbit
      let vcDrag = null;
      vcCanvas.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        e.stopPropagation();
        vcDrag = { x: e.clientX, y: e.clientY, dragged: false };
        vcCanvas.setPointerCapture(e.pointerId);
      });
      vcCanvas.addEventListener("pointermove", (e) => {
        e.preventDefault();
        if (!vcDrag) return;
        const dx = e.clientX - vcDrag.x;
        const dy = e.clientY - vcDrag.y;
        if (!vcDrag.dragged && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) vcDrag.dragged = true;
        if (!vcDrag.dragged) return;
        vcDrag.x = e.clientX;
        vcDrag.y = e.clientY;

        // Map pixel drag on the small cube to camera orbit
        // Larger multiplier = cube feels 1:1 with finger
        const sensitivity = 0.012;
        const dist = camera.position.distanceTo(controls.target);
        const offset = camera.position.clone().sub(controls.target);

        // Horizontal drag → rotate around world Z (azimuth)
        const azimuth = new THREE.Quaternion().setFromAxisAngle(
          new THREE.Vector3(0, 0, 1), -dx * sensitivity
        );
        offset.applyQuaternion(azimuth);
        camera.up.applyQuaternion(azimuth);

        // Vertical drag → rotate around camera's right axis (elevation)
        const right = new THREE.Vector3().crossVectors(camera.up, offset).normalize();
        const elevation = new THREE.Quaternion().setFromAxisAngle(right, -dy * sensitivity);
        const newOffset = offset.clone().applyQuaternion(elevation);
        // Prevent flipping past poles
        const newUp = camera.up.clone().applyQuaternion(elevation);
        if (newUp.dot(new THREE.Vector3(0, 0, 1)) > 0.05) {
          offset.copy(newOffset);
          camera.up.copy(newUp);
        }

        camera.position.copy(controls.target).add(offset);
        camera.lookAt(controls.target);
        controls.update();
        markDirty();
      });
      vcCanvas.addEventListener("pointerup", (e) => {
        if (vcDrag && !vcDrag.dragged) {
          // Click — snap to face/edge/corner
          vcSetPointer(e);
          vcRaycaster.setFromCamera(vcPointer, vcCamera);
          const hits = vcRaycaster.intersectObjects(vcAllMeshes, false);
          if (hits.length) {
            const hit = hits[0];
            const type = hit.object.userData.vcType;
            let d;
            if (type === "corner" || type === "edge") d = hit.object.userData.vcDir;
            else { const n = VC_FACE_NORMALS[hit.face.materialIndex]; d = n; }
            const [pos, tgt, up] = vcSnapFromDir(d[0], d[1], d[2]);
            snapCamera(pos, tgt, up);
          }
        }
        vcDrag = null;
      });

      // --- Render on demand (listeners) ---
      controls.addEventListener("change", markDirty);
      renderer.domElement.addEventListener("pointermove", markDirty);

      // --- Animate (keep anchors & badges sticky) ---
      // Render telemetry — what the OPERATOR'S browser actually
      // measures (the server can't see a remote GPU). Sampled around
      // real renders only, posted every 5s while the tab is visible,
      // fire-and-forget. Rows land in /tmp/sb_perf.jsonl on the Pi.
      const _perf = { renders: 0, ms: 0, worst: 0, lastSend: performance.now() };
      function _perfSend() {
        const now = performance.now();
        if (now - _perf.lastSend < 5000 || !_perf.renders) return;
        if (document.visibilityState !== "visible") { _perf.lastSend = now; return; }
        const body = {
          renders: _perf.renders,
          avg_ms: +(_perf.ms / _perf.renders).toFixed(1),
          worst_ms: +_perf.worst.toFixed(1),
          calls: renderer.info.render.calls,
          tris: renderer.info.render.triangles,
          objects: objectsByName.size,
          dpr: window.devicePixelRatio,
        };
        _perf.renders = 0; _perf.ms = 0; _perf.worst = 0; _perf.lastSend = now;
        try {
          fetch(SB_API + "/perf", { method: "POST", keepalive: true,
            headers: { "content-type": "application/json" },
            body: JSON.stringify(body) }).catch(() => {});
        } catch (_) {}
      }

      function animate() {
        const snapActive = !!snapTarget;
        if (snapActive) { updateSnap(); markDirty(); }
        controls.update();
        if (activeAnchors) { updateAnchorsNow(); markDirty(); }

        const now = performance.now();
        if (_needsRender || now - _lastRenderMs > IDLE_RENDER_INTERVAL) {
          const t0 = performance.now();
          renderer.render(scene, camera);
          vcGroup.quaternion.copy(camera.quaternion).invert();
          vcRenderer.render(vcScene, vcCamera);
          const dt = performance.now() - t0;
          _perf.renders++; _perf.ms += dt;
          if (dt > _perf.worst) _perf.worst = dt;
          _needsRender = false;
          _lastRenderMs = now;
        }
        _perfSend();
        requestAnimationFrame(animate);
      }
      animate();

      window.addEventListener("resize", () => {
        camera.aspect = viewerEl.clientWidth / viewerEl.clientHeight;
        camera.updateProjectionMatrix();
        renderer.setSize(viewerEl.clientWidth, viewerEl.clientHeight);
      });
    }

    await getVersion();
    boot();
  

// --- Pattern Fill (plates) ---
async function patternFromPlate() {
  try { window.showBanner('Pattern: looking for template at A1… (Esc to cancel)', () => { try{window.hideBanner();}catch(e){} }); } catch(e) {}
  console.log('[pattern] start', window.builderState.selectedName, window.builderState.selectedTypeName);
  window.showToast('Pattern start');
  if (typeof window.spawnComponent !== 'function') {
    window.showToast('Pattern error: spawnComponent not ready (page still loading).');
    try { window.hideBanner(); } catch(e) {}
    return;
  }
  const plateName = window.builderState.selectedName;
  const plateType = (window.builderState.selectedTypeName || "");

  if (!plateName) { window.showToast("Select a plate first."); return; }
  if (!(plateType && plateType.startsWith("plate_"))) {
    window.showToast("Select a plate_... object to pattern.");
  try { window.hideBanner(); } catch(e) {}
    return;
  }

  // Require a template object already mounted at A1 (supports nested anchoring chains)
  const TEMPLATE_ANCHOR = "A1";
  let templateName = null;

  function __canonA1(raw) {
    const s = String(raw || "");
    const cleaned = s.replace(/^hole[ _-]?/i, "").replace(/^anchor[ _-]?/i, "");
    const m = cleaned.match(/^([A-Za-z])[ _-]?(\d+)$/);
    return m ? (m[1].toUpperCase() + String(parseInt(m[2], 10))) : s.toUpperCase();
  }

  // If an object is anchored to another anchored object, walk up the attach chain until we
  // hit the plate, and use that plate anchor for template detection.
  function __findPlateAnchorInChain(objName) {
    let cur = objName;
    for (let i = 0; i < 32; i++) {
      const meta = window.builderState.components?.[cur];
      const at = meta && meta.attach ? meta.attach : null;
      if (!at) return null;
      if (at.parent_name === plateName) return at.parent_anchor || null;
      cur = at.parent_name;
      if (!cur) return null;
    }
    return null;
  }

  for (const [n, cfg] of Object.entries(window.builderState.components || {})) {
    const direct = cfg && cfg.attach ? cfg.attach : null;
    const plateAnchor = direct && direct.parent_name === plateName ? direct.parent_anchor : __findPlateAnchorInChain(n);
    if (!plateAnchor) continue;
    if (__canonA1(plateAnchor) === TEMPLATE_ANCHOR) { templateName = n; break; }
  }

  if (!templateName) {
    window.showToast(`No object mounted at ${TEMPLATE_ANCHOR}. Place one there first, then Pattern.`);
    return;
  }

  const plateObj = window.objectsByName.get(plateName);
  if (!plateObj || !plateObj.userData) {
    window.showToast("Plate not found in scene.");
    return;
  }

  // Plates sometimes store anchors in anchorsBySolid instead of anchors.
  // Pull the active solid's anchors if available.
  function __getPlateAnchors(obj) {
    const ud = obj?.userData || {};
    if (ud.anchors && typeof ud.anchors === "object") return ud.anchors;
    const ab = ud.anchorsBySolid && typeof ud.anchorsBySolid === "object" ? ud.anchorsBySolid : null;
    if (!ab) return null;
    const solid = ud.solidName && ab[ud.solidName] ? ud.solidName : (ab["solid_0"] ? "solid_0" : Object.keys(ab)[0]);
    return solid ? (ab[solid] || null) : null;
  }

  const plateAnchorsRaw = __getPlateAnchors(plateObj);
  if (!plateAnchorsRaw) {
    window.showToast("Plate anchors not available (anchors/anchorsBySolid missing)." );
    return;
  }

  // Canonicalize plate hole anchors to "A1".."Z500".
  // We map canonical -> actual key used in the plate anchor dictionary.
  function __canonHoleName(k) {
    if (!k || typeof k !== "string") return null;
    // Accept A1, A_1, A-1, hole_A1, hole-A1, holeA1
    const cleaned = k.replace(/^hole[ _-]?/i, "").replace(/^anchor[ _-]?/i, "");
    const m = cleaned.match(/^([A-Za-z])[ _-]?(\d+)$/);
    if (!m) return null;
    const letter = m[1].toUpperCase();
    const num = parseInt(m[2], 10);
    if (!(letter >= "A" && letter <= "Z")) return null;
    if (!(num >= 1 && num <= 500)) return null;
    return `${letter}${num}`;
  }

  const plateKeyByCanon = new Map();
  for (const k of Object.keys(plateAnchorsRaw)) {
    const c = __canonHoleName(String(k));
    if (!c) continue;
    if (!plateKeyByCanon.has(c)) plateKeyByCanon.set(c, k);
  }

  // Build the exact fill list A1..Z500 but only for anchors that actually exist on this plate.
  const holesCanon = [];
  for (let li = 0; li < 26; li++) {
    const letter = String.fromCharCode(65 + li);
    for (let n = 1; n <= 500; n++) {
      const c = `${letter}${n}`;
      if (plateKeyByCanon.has(c)) holesCanon.push(c);
    }
  }

  if (!holesCanon.length) {
    window.showToast("No plate anchors found in the A1..Z500 range on this plate.");
    return;
  }

  // Determine which plate anchors are already occupied (A1..Z500 only)
  const occupied = new Set();
  for (const [n, cfg] of Object.entries(window.builderState.components || {})) {
    const a = cfg && cfg.attach;
    if (!a) continue;
    if (a.parent_name !== plateName) continue;
    const raw = String(a.parent_anchor || "");
    const canon = __canonHoleName(raw);
    if (!canon) continue;
    occupied.add(raw);
    occupied.add(canon);
  }

  const tmplCfg = window.builderState.components[templateName] || {};
  const tmplType = tmplCfg.type;
  const tmplAttach = tmplCfg.attach || {};
  const childAnchor = tmplAttach.child_anchor || "center";
  const childSolid = tmplAttach.child_solid || "solid_0";

  // carry forward checkbox options from the template (we store options as top-level keys)
  const templateOptions = {};
  for (const [k, v] of Object.entries(tmplCfg)) {
    if (k === "type" || k === "attach" || k === "pose" || k === "builder" || k === "simulation") continue;
    if (typeof v === "boolean") templateOptions[k] = v;
  }

  // Get plate hole anchors (restricted to A1..Z500 that actually exist on this plate)
  const holes = holesCanon.map(c => plateKeyByCanon.get(c)).filter(Boolean);

  // Fill all unoccupied holes (skip the template slot and any already-used)
let placed = 0;
  // NEW behavior: pattern is no longer an atomic undo step.
  // Each spawned instance behaves like a normal spawn+joint:
  // - Undo removes the LAST spawned instance
  // - Repeated undo walks backwards through the pattern instances
  window.showBanner(`Patterning ${tmplType} into ${holes.length} holes…`, null);
  for (const hole of holes) {
    const canon = __canonHoleName(String(hole)) || String(hole).toUpperCase();
    if (canon === TEMPLATE_ANCHOR) continue;
    if (occupied.has(String(hole)) || occupied.has(canon)) continue;

    // spawn a fresh instance (this pushes a normal {kind:"create"} undo entry)
    const newName = await window.spawnComponent(tmplType, null, templateOptions);

    // programmatically snap it using existing joint math
    window.builderState.mode = "PICK_TARGET_ANCHOR";
    window.builderState.pending = { name: newName, type: tmplType, sourceAnchor: childAnchor, childSolid };
    window.builderState.targetName = plateName;

    // this attach is eligible to merge with the create into {kind:"create_attach"}
    window.handleAnchorPick(plateName, hole);

    placed += 1;

    // keep UI responsive
    await new Promise(r => setTimeout(r, 0));
  }
  try { window.hideBanner(); } catch(e) {}
  window.showToast(`Pattern placed ${placed} item(s).`);
window.showToast(`Pattern placed ${placed} item(s).`);
}

function openPatternSourceAnchorMenu(sourceName, anchorsBySolid) {
  try { window.hideBanner(); } catch(e) {}

  // choose which anchor on the source object will be placed into each plate hole
  const old = document.getElementById("patternSourceAnchorMenu");
  if (old) old.remove();

  const menu = document.createElement("div");
  menu.id = "patternSourceAnchorMenu";
  menu.style.position = "fixed";
  menu.style.left = "50%";
  menu.style.top = "50%";
  menu.style.transform = "translate(-50%, -50%)";
  menu.style.width = "520px";
  menu.style.maxWidth = "92vw";
  menu.style.maxHeight = "80vh";
  menu.style.overflowY = "auto";
  menu.style.background = "rgba(20,20,20,0.95)";
  menu.style.border = "1px solid rgba(255,255,255,0.10)";
  menu.style.borderRadius = "16px";
  menu.style.padding = "14px";
  menu.style.zIndex = "10020";
  menu.style.color = "#fff";

  const title = document.createElement("div");
  title.textContent = "Pattern: choose source anchor";
  title.style.fontWeight = "700";
  title.style.marginBottom = "10px";
  menu.appendChild(title);

  const solids = Object.keys(anchorsBySolid || {});
  let solid = solids.length ? solids[0] : "solid_0";
  let anchors = anchorsBySolid?.[solid] || {};

  // if multiple solids, let user pick
  if (solids.length > 1) {
    const solidRow = document.createElement("div");
    solidRow.style.display = "flex";
    solidRow.style.gap = "10px";
    solidRow.style.alignItems = "center";
    solidRow.style.marginBottom = "10px";

    const lab = document.createElement("div");
    lab.textContent = "Solid:";
    lab.style.opacity = "0.85";
    lab.style.width = "60px";
    solidRow.appendChild(lab);

    const sel = document.createElement("select");
    _sbStyleInput(sel);
    sel.style.flex = "1";
    for (const s of solids) {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s;
      sel.appendChild(opt);
    }
    sel.onchange = () => {
      solid = sel.value;
      anchors = anchorsBySolid?.[solid] || {};
      renderAnchorButtons();
    };
    solidRow.appendChild(sel);
    menu.appendChild(solidRow);
  }

  const btnWrap = document.createElement("div");
  btnWrap.style.display = "grid";
  btnWrap.style.gridTemplateColumns = "repeat(2, minmax(0, 1fr))";
  btnWrap.style.gap = "8px";
  menu.appendChild(btnWrap);

  function renderAnchorButtons() {
    btnWrap.innerHTML = "";
    const names = Object.keys(anchors || {});
    if (!names.length) {
      const none = document.createElement("div");
      none.textContent = "No anchors on this object.";
      none.style.opacity = "0.75";
      btnWrap.appendChild(none);
      return;
    }

    // Prefer center if present
    names.sort((a,b) => (a==="center" ? -1 : b==="center" ? 1 : a.localeCompare(b)));

    for (const a of names) {
      const b = document.createElement("button");
      b.textContent = a;
      b.style.padding = "10px 12px";
      b.style.borderRadius = "12px";
      b.style.border = "1px solid rgba(255,255,255,0.12)";
      b.style.background = "rgba(255,255,255,0.06)";
      b.style.color = "#fff";
      b.style.textAlign = "left";
      b.onclick = () => {
        menu.remove();
        const plateName = window.builderState.pattern?.plateName;
        if (!plateName) return;
        window.builderState.pattern.sourceName = sourceName;
        window.builderState.pattern.sourceSolid = solid;
        window.builderState.pattern.sourceAnchor = a;
        runPatternFill();
      };
      btnWrap.appendChild(b);
    }
  }
  renderAnchorButtons();

  const cancel = document.createElement("button");
  cancel.textContent = "Cancel";
  cancel.style.marginTop = "12px";
  cancel.style.width = "100%";
  cancel.style.padding = "10px 12px";
  cancel.style.borderRadius = "12px";
  cancel.style.border = "1px solid rgba(255,255,255,0.12)";
  cancel.style.background = "rgba(0,0,0,0.30)";
  cancel.style.color = "#fff";
  cancel.onclick = () => {
    window.builderState.mode = "IDLE";
    window.builderState.pattern = null;
    menu.remove();
  };
  menu.appendChild(cancel);

  document.body.appendChild(menu);
}


// helper: detect hole-like anchors on plates (A1..Z999, or numeric 0..500, etc.)
function __isPlateHoleAnchor(name) {
  if (!name || typeof name !== "string") return false;
  // common non-hole anchors to ignore
  const bad = ["center","place","tcp","tool_connection"];
  if (bad.includes(name)) return false;
  // letter+number: A1, f15, etc (also allow A_1, A-1)
  if (/^[A-Za-z][_-]?\d+$/.test(name)) return true;
  // numeric: 0,1,2... (limit later)
  if (/^\d+$/.test(name)) return true;
  return false;
}

// sort holes in a stable “human” order: A1..A99..B1.. then numeric
function __anchorSortKey(name) {
  // returns [group, letterIndex, number]
  if (/^[A-Za-z][_-]?\d+$/.test(name)) {
    const m = name.match(/^([A-Za-z])[ _-]?(\d+)$/);
    const li = m ? (m[1].toUpperCase().charCodeAt(0) - 65) : 99;
    const num = m ? parseInt(m[2],10) : 0;
    return [0, li, num];
  }
  if (/^\d+$/.test(name)) {
    return [1, 0, parseInt(name,10)];
  }
  return [2, 0, 0];
}

async function runPatternFill() {
  const p = window.builderState.pattern;
  if (!p?.plateName || !p?.sourceName || !p?.sourceAnchor) return;

  const plateObj = window.objectsByName.get(p.plateName);
  const srcObj = window.objectsByName.get(p.sourceName);
  if (!plateObj || !srcObj) return;

  const plateAnchors = plateObj.userData?.anchors || {};
  const plateHoleNames = Object.keys(plateAnchors)
    .filter(__isPlateHoleAnchor)
    .filter(n => {
      if (/^\d+$/.test(n)) return parseInt(n,10) <= 500;
      const m = n.match(/^[A-Za-z][ _-]?(\d+)$/);
      if (m) return parseInt(m[1],10) <= 500;
      return true;
    });
  plateHoleNames.sort((a,b) => {
    const ka = __anchorSortKey(a), kb = __anchorSortKey(b);
    for (let i=0;i<3;i++) if (ka[i] !== kb[i]) return ka[i] - kb[i];
    return String(a).localeCompare(String(b));
  });

  if (!plateHoleNames.length) {
    window.showToast("No hole-style anchors found on this plate.");
    window.builderState.mode = "IDLE";
    window.builderState.pattern = null;
    return;
  }

  // Determine source type from builder meta OR scene userData
  let srcType = window.builderState.components[p.sourceName]?.type || "";
  if (!srcType) {
    try {
      const so = window.objectsByName.get(p.sourceName);
      srcType = so?.userData?.typeName || so?.userData?.type || "";
    } catch(e) {}
  }
  if (!srcType) { window.showToast("Source object type unknown."); return; }

  // Use same checkbox options as the source object (only bools are stored now)
  const srcOptions = {};
  const meta = window.builderState.components[p.sourceName] || {};
  for (const k in meta) {
    if (k === "type" || k === "attach" || k === "simulation") continue;
    if (typeof meta[k] === "boolean") srcOptions[k] = meta[k];
  }

  // Cache blueprint once for speed
  let blueprint = null;
  try {
    const res = await fetch(SB_API + "/instantiate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: srcType, options: srcOptions })
    });
    const js = await res.json();
    if (js && js.ok) blueprint = js.blueprint;
  } catch(e) {}

  if (!blueprint) {
    window.showToast("Failed to instantiate pattern object.");
    window.builderState.mode = "IDLE";
    window.builderState.pattern = null;
    return;
  }

  // Build meshes + anchorsBySolid from blueprint
  const anchorsBySolid = {};
  const meshes = [];
  for (const s of (blueprint.solids || [])) {
    if (s?.solid && s?.anchors) anchorsBySolid[s.solid] = s.anchors;
    if (s?.glb) meshes.push({ meshUrl: s.glb, pose: s.pose || [0,0,0,0,0,0], solidName: s.solid, collisionLocal: s.collisionLocal || [], boxForGrip: !!s.boxForGrip });
  }
  const childSolid = p.sourceSolid || (Object.keys(anchorsBySolid)[0] || "solid_0");
  const childAnchors = anchorsBySolid[childSolid] || {};
  const srcArr = childAnchors[p.sourceAnchor];
  if (!srcArr) {
    window.showToast("Selected source anchor not found on instantiated object.");
    window.builderState.mode = "IDLE";
    window.builderState.pattern = null;
    return;
  }


  // Build a set of already-occupied plate anchors so we don't double-place.
  const occupied = new Set();
  try {
    for (const [n, m] of Object.entries(window.builderState.components || {})) {
      const at = m?.attach;
      if (!at) continue;
      if (at.parent_name === p.plateName && at.parent_anchor) occupied.add(String(at.parent_anchor));
    }
  } catch(e) {}

  
  // Ensure we never overwrite an existing component name (critical for correct pattern-undo behavior).
  // If the seed is e.g. tube_1 and next[tube] is still 1, the first clone would become tube_1 and overwrite the seed.
  try {
    const comps = window.builderState.components || {};
    let maxIdx = 0;
    const reName = new RegExp("^" + srcType.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "_(\\d+)$");
    for (const k of Object.keys(comps)) {
      const m = String(k).match(reName);
      if (m) maxIdx = Math.max(maxIdx, parseInt(m[1], 10) || 0);
    }
    if (!window.builderState.next) window.builderState.next = {};
    const cur = window.builderState.next[srcType] || 1;
    window.builderState.next[srcType] = Math.max(cur, maxIdx + 1);
  } catch(e) {}

// place many instances
let count = 0;
  // NEW behavior: do NOT record a single atomic pattern undo.
  // Each instance behaves like a normal spawn+joint so Ctrl+Z removes them one-by-one (last created first).
  window.showBanner(`Patterning into ${targets.length} anchors…`, null);

  for (const tgt of targets) {
    try {
      const name = await window.spawnComponent(srcType, null, srcOptions);
      // Snap it using existing anchor pick logic (this attach can merge with the create into {kind:"create_attach"}).
      window.builderState.mode = "PICK_TARGET_ANCHOR";
      window.builderState.pending = { name, type: srcType, sourceAnchor: p.sourceAnchor, childSolid: p.childSolid };
      window.builderState.targetName = p.plateName;
      window.handleAnchorPick(p.plateName, tgt);
      count++;
    } catch (e) {
      console.error(e);
    }
    await new Promise(r => setTimeout(r, 0));
  }

  try { window.hideBanner(); } catch(e) {}
  window.showToast(`Patterned ${count} instances on ${p.plateName}.`);
  window.builderState.mode = "IDLE";
  window.builderState.pattern = null;
}

// ==============================
// Fusion-style Rectangular Pattern
// ==============================

function __rpGetAnchorsForObj(obj) {
  const ud = obj?.userData || {};
  if (ud.anchors && typeof ud.anchors === "object" && Object.keys(ud.anchors).length) return ud.anchors;
  const ab = ud.anchorsBySolid && typeof ud.anchorsBySolid === "object" ? ud.anchorsBySolid : null;
  if (!ab) return {};
  const solid = (ud.solidName && ab[ud.solidName])
    ? ud.solidName
    : (ab["solid_0"] ? "solid_0" : Object.keys(ab)[0]);
  return (solid && ab[solid]) ? ab[solid] : {};
}

function __rpAnchorWorldPose(ownerName, anchorName) {
  const owner = objectsByName.get(ownerName);
  if (!owner) return null;
  const anchors = __rpGetAnchorsForObj(owner);
  const arr = anchors ? anchors[anchorName] : null;
  if (!arr || !Array.isArray(arr) || arr.length !== 6) return null;
  const pLocal = new THREE.Vector3(arr[0], arr[1], arr[2]);
  const qLocal = rodriguesDegToQuaternion(arr[3], arr[4], arr[5]);
  const pWorld = owner.localToWorld(pLocal.clone());
  const ownerQ = new THREE.Quaternion();
  owner.getWorldQuaternion(ownerQ);
  const qWorld = ownerQ.clone().multiply(qLocal);
  return { pos: pWorld, quat: qWorld };
}

function __rpChildAnchorLocalPose(childObj, childAnchorName) {
  const anchors = __rpGetAnchorsForObj(childObj);
  const arr = anchors ? anchors[childAnchorName] : null;
  if (!arr || !Array.isArray(arr) || arr.length !== 6) {
    return { pos: new THREE.Vector3(0,0,0), quat: new THREE.Quaternion() };
  }
  return {
    pos: new THREE.Vector3(arr[0], arr[1], arr[2]),
    quat: rodriguesDegToQuaternion(arr[3], arr[4], arr[5])
  };
}

// RectPattern: second anchor selection handler.
// Called when the user clicks an anchor (or chooses from the list) while in RECTPATTERN_PICK_SECOND_ANCHOR.
function rectPatternHandleSecondAnchor(ownerName, anchorName) {
  const rp = window.builderState?.rectPattern;
  const ui = window.builderState?.rectPatternUi;
  if (!rp || !ui || !rp.seedName) { showToast("Pick the first object first."); return; }
  if (!ownerName || !anchorName) { showToast("Select a valid anchor."); return; }

  const w = __rpAnchorWorldPose(ownerName, anchorName);
  if (!w || !w.pos) { showToast("Anchor not found: " + anchorName); return; }

  rp.bAnchor = {
    ownerName,
    anchorName,
    pos: w.pos.clone(),
    quat: w.quat ? w.quat.clone() : new THREE.Quaternion()
  };
  rp.secondOwnerName = ownerName;

  // Delta is used by the UI validation and also legacy distance-based pattern mode.
  // For grid-fill mode, we still compute this so the "Pick the 2nd anchor" check passes.
  const aPos = (rp.aAnchor && rp.aAnchor.pos) ? rp.aAnchor.pos : null;
  rp.delta = aPos ? rp.bAnchor.pos.clone().sub(aPos) : new THREE.Vector3(0,0,0);

  // UI update
  ui.pointBox.dataset.state = "ok";
  ui.pointBox.textContent = `Second anchor ✓ (${ownerName}:${anchorName})`;
  ui.pointBox.style.borderColor = "rgba(0,140,0,0.55)";
  ui.pointBox.style.background = "rgba(0,140,0,0.08)";
  ui.pointClear.style.display = "inline-flex";

  window.builderState.mode = "IDLE";
  try { window.hideBanner?.(); } catch(e) {}
  try { clearAnchors?.(); } catch(e) {}
  showToast("Second anchor set.");
}

// __rpAutoOpenAnchorList is now replaced by inline populateAnchorList in startRectPattern

function closeRectPatternPanel() {
  try { window.hideBanner?.(); } catch(e) {}
  if (window.builderState) window.builderState.mode = "IDLE";

  const el = document.getElementById("rectPatternPanel");
  if (el) el.remove();
}

function startRectPattern() {
  const th = _sbPanelTheme();
  closeRectPatternPanel();

  // Panel shell
  const panel = document.createElement("div");
  panel.id = "rectPatternPanel";
  panel.style.position = "fixed";
  panel.style.right = "340px";
  panel.style.top = "18px";
  panel.style.width = "360px";
  panel.style.maxHeight = "85vh";
  panel.style.overflow = "auto";
  panel.style.background = th.panelBg;
  panel.style.border = th.panelBord;
  panel.style.borderRadius = "16px";
  panel.style.boxShadow = "0 18px 60px rgba(0,0,0,0.45)";
  panel.style.padding = "14px";
  panel.style.fontFamily = "system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif";
  panel.style.color = th.color;

  const title = document.createElement("div");
  title.textContent = "Rectangular Pattern";
  title.style.fontWeight = "700";
  title.style.fontSize = "16px";
  title.style.marginBottom = "8px";
  panel.appendChild(title);

  const hint = document.createElement("div");
  hint.style.fontSize = "12px";
  hint.style.opacity = "0.75";
  hint.style.marginBottom = "10px";
  hint.textContent = "Pick a first object, then pick a second object + anchor. Enter counts and Fill.";
  panel.appendChild(hint);

  function mkRow(labelText) {
    const row = document.createElement("div");
    row.style.display = "flex";
    row.style.alignItems = "center";
    row.style.gap = "8px";
    row.style.marginBottom = "8px";

    const label = document.createElement("div");
    label.textContent = labelText;
    label.style.width = "110px";
    label.style.fontSize = "12px";
    label.style.opacity = "0.9";
    row.appendChild(label);

    return { row, label };
  }

  function mkSelectBox(placeholder) {
    const wrap = document.createElement("div");
    wrap.style.flex = "1";
    wrap.style.display = "flex";
    wrap.style.alignItems = "center";
    wrap.style.gap = "6px";

    const box = document.createElement("div");
    box.textContent = placeholder;
    box.dataset.state = "empty";
    box.style.flex = "1";
    box.style.cursor = "pointer";
    box.style.userSelect = "none";
    box.style.padding = "10px 10px";
    box.style.borderRadius = "12px";
    box.style.border = th.listBord;
    box.style.background = th.listBg;
    box.style.fontSize = "12px";
    box.style.lineHeight = "1.2";
    box.title = "Click to pick";

    const clearBtn = document.createElement("button");
    clearBtn.textContent = "×";
    clearBtn.style.display = "none";
    clearBtn.style.width = "28px";
    clearBtn.style.height = "28px";
    clearBtn.style.borderRadius = "10px";
    clearBtn.style.border = th.panelBord;
    clearBtn.style.background = th.cancelBg;
    clearBtn.style.color = th.color;
    clearBtn.style.cursor = "pointer";
    clearBtn.title = "Clear";

    wrap.appendChild(box);
    wrap.appendChild(clearBtn);
    return { wrap, box, clearBtn };
  }

  // First object picker
  const r1 = mkRow("First object");
  const seed = mkSelectBox("Select first object…");
  r1.row.appendChild(seed.wrap);
  panel.appendChild(r1.row);

  seed.box.addEventListener("click", () => {
    window.builderState.mode = "RECTPATTERN_PICK_OBJECT";
    // Blue highlight for selection state
    seed.box.style.borderColor = "rgba(60,130,255,0.7)";
    seed.box.style.background = "rgba(60,130,255,0.08)";
    seed.box.textContent = "Click an object in 3D...";
    try { window.showBanner?.("Click a valid object (the one you want to pattern)."); } catch(e) {}
  });
  seed.clearBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    seed.box.dataset.state = "empty";
    seed.box.textContent = "Select first object…";
    seed.box.style.borderColor = "";
    seed.box.style.border = th.listBord;
    seed.box.style.background = th.listBg;
    seed.clearBtn.style.display = "none";
    // also clear rp
    window.builderState.rectPattern = null;
    // reset point
    point.box.dataset.state = "empty";
    point.box.textContent = "Select second object…";
    point.box.style.borderColor = "rgba(0,0,0,0.18)";
    point.box.style.background = "rgba(0,0,0,0.02)";
    point.clearBtn.style.display = "none";
    resetAnchorList();
  });

  // Second object picker
  const r2 = mkRow("Second object");
  const point = mkSelectBox("Select second object…");
  r2.row.appendChild(point.wrap);
  panel.appendChild(r2.row);

  // Permanent inline anchor list (starts empty, populates when second object is picked)
  const anchorListContainer = document.createElement("div");
  anchorListContainer.id = "rpInlineAnchorList";
  anchorListContainer.style.border = th.listBord;
  anchorListContainer.style.borderRadius = "12px";
  anchorListContainer.style.overflow = "hidden";
  anchorListContainer.style.marginBottom = "8px";

  const anchorListHeader = document.createElement("div");
  anchorListHeader.style.display = "flex";
  anchorListHeader.style.alignItems = "center";
  anchorListHeader.style.justifyContent = "space-between";
  anchorListHeader.style.fontSize = "12px";
  anchorListHeader.style.fontWeight = "700";
  anchorListHeader.style.padding = "8px 10px";
  anchorListHeader.style.background = th.rowBg;
  anchorListHeader.style.borderBottom = th.itemBord;
  const anchorHeaderTitle = document.createElement("span");
  anchorHeaderTitle.textContent = "Anchors";
  const anchorHeaderCount = document.createElement("span");
  anchorHeaderCount.textContent = "";
  anchorHeaderCount.style.fontWeight = "400";
  anchorHeaderCount.style.opacity = "0.6";
  anchorHeaderCount.style.fontSize = "11px";
  anchorListHeader.appendChild(anchorHeaderTitle);
  anchorListHeader.appendChild(anchorHeaderCount);
  anchorListContainer.appendChild(anchorListHeader);

  const anchorSearchInput = document.createElement("input");
  anchorSearchInput.type = "text";
  anchorSearchInput.placeholder = "Filter anchors...";
  anchorSearchInput.style.width = "100%";
  anchorSearchInput.style.boxSizing = "border-box";
  anchorSearchInput.style.padding = "8px 10px";
  anchorSearchInput.style.border = "none";
  anchorSearchInput.style.borderBottom = th.itemBord;
  anchorSearchInput.style.background = th.inputBg;
  anchorSearchInput.style.color = th.color;
  anchorSearchInput.style.fontSize = "12px";
  anchorSearchInput.style.outline = "none";
  anchorSearchInput.style.display = "none";
  anchorListContainer.appendChild(anchorSearchInput);

  const anchorListDiv = document.createElement("div");
  anchorListDiv.style.maxHeight = "300px";
  anchorListDiv.style.overflow = "auto";
  anchorListDiv.style.background = th.listBg;
  anchorListContainer.appendChild(anchorListDiv);

  // Empty state
  const anchorEmpty = document.createElement("div");
  anchorEmpty.textContent = "Select a second object to see anchors";
  anchorEmpty.style.padding = "12px 10px";
  anchorEmpty.style.fontSize = "12px";
  anchorEmpty.style.opacity = "0.45";
  anchorEmpty.style.textAlign = "center";
  anchorListDiv.appendChild(anchorEmpty);

  // Function to populate anchors in-place
  let currentAnchorNames = [];
  function populateAnchorList(ownerName) {
    const owner = objectsByName.get(ownerName);
    if (!owner) return;
    const anchors = __rpGetAnchorsForObj(owner);
    // Only show grid-style anchors (letter + number, e.g. A1, H12) since
    // non-grid anchors (center, hole_0, corner_3, etc.) can't be used for pattern fill.
    const __isGridAnchor = (s) => /^[A-Za-z]\d{1,4}$/.test(String(s).trim());
    currentAnchorNames = Object.keys(anchors || {}).filter(__isGridAnchor).sort((a,b)=>a.localeCompare(b, undefined, {numeric:true}));

    // Style the container to show it's active
    anchorListContainer.style.border = "1px solid rgba(60,130,255,0.25)";
    anchorListContainer.style.background = "rgba(60,130,255,0.02)";
    anchorListHeader.style.background = "rgba(60,130,255,0.06)";
    anchorListHeader.style.borderBottom = "1px solid rgba(60,130,255,0.12)";
    anchorHeaderTitle.textContent = `Anchors on ${ownerName}`;
    anchorHeaderCount.textContent = `${currentAnchorNames.length} available`;
    anchorSearchInput.style.display = "";
    anchorSearchInput.value = "";

    renderAnchorItems("");
    setTimeout(() => { try { anchorSearchInput.focus(); } catch(_) {} }, 50);
  }

  function renderAnchorItems(filterText) {
    anchorListDiv.innerHTML = "";
    const f = (filterText||"").trim().toLowerCase();
    const shown = currentAnchorNames.filter(n => !f || n.toLowerCase().includes(f));
    for (const n of shown) {
      const item = document.createElement("div");
      item.textContent = n;
      item.style.padding = "8px 10px";
      item.style.cursor = "pointer";
      item.style.fontSize = "12px";
      item.style.borderBottom = "1px solid rgba(255,255,255,0.05)";
      item.style.transition = "background 0.1s ease";
      item.addEventListener("mouseenter", () => { item.style.background = "rgba(60,130,255,0.12)"; });
      item.addEventListener("mouseleave", () => { item.style.background = "transparent"; });
      item.addEventListener("click", () => {
        const rp = window.builderState.rectPattern;
        const ownerName = rp?.secondOwnerName;
        if (ownerName) {
          rectPatternHandleSecondAnchor(ownerName, n);
        }
      });
      anchorListDiv.appendChild(item);
    }
    if (!shown.length) {
      const empty = document.createElement("div");
      empty.textContent = currentAnchorNames.length ? "No matches." : "No anchors available.";
      empty.style.padding = "12px 10px";
      empty.style.fontSize = "12px";
      empty.style.opacity = "0.45";
      empty.style.textAlign = "center";
      anchorListDiv.appendChild(empty);
    }
    anchorHeaderCount.textContent = f ? `${shown.length} of ${currentAnchorNames.length}` : `${currentAnchorNames.length} available`;
  }

  function resetAnchorList() {
    currentAnchorNames = [];
    anchorListContainer.style.border = "1px solid rgba(255,255,255,0.08)";
    anchorListContainer.style.background = "";
    anchorListHeader.style.background = "rgba(255,255,255,0.04)";
    anchorListHeader.style.borderBottom = "1px solid rgba(255,255,255,0.06)";
    anchorHeaderTitle.textContent = "Anchors";
    anchorHeaderCount.textContent = "";
    anchorSearchInput.style.display = "none";
    anchorSearchInput.value = "";
    anchorListDiv.innerHTML = "";
    const emptyMsg = document.createElement("div");
    emptyMsg.textContent = "Select a second object to see anchors";
    emptyMsg.style.padding = "12px 10px";
    emptyMsg.style.fontSize = "12px";
    emptyMsg.style.opacity = "0.45";
    emptyMsg.style.textAlign = "center";
    anchorListDiv.appendChild(emptyMsg);
  }

  anchorSearchInput.addEventListener("input", () => renderAnchorItems(anchorSearchInput.value));

  panel.appendChild(anchorListContainer);

  // Keep btnAnchorList as a no-op reference for legacy code
  const btnAnchorList = { disabled: true, style: { opacity: "0.6", display: "none" } };

  point.box.addEventListener("click", () => {
    const rp = window.builderState.rectPattern;
    if (!rp || !rp.seedName) { showToast("Pick the first object first."); return; }
    window.builderState.mode = "RECTPATTERN_PICK_SECOND_OBJECT";
    // Blue highlight for selection state
    point.box.style.borderColor = "rgba(60,130,255,0.7)";
    point.box.style.background = "rgba(60,130,255,0.08)";
    point.box.textContent = "Click a second object in 3D...";
    try { window.showBanner?.("Click the second object, then pick an anchor."); } catch(e) {}
  });
  point.clearBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const rp = window.builderState.rectPattern;
    if (rp) { rp.bAnchor = null; rp.delta = null; rp.secondOwnerName = null; }
    point.box.dataset.state = "empty";
    point.box.textContent = "Select second object…";
    point.box.style.borderColor = "rgba(0,0,0,0.18)";
    point.box.style.background = "rgba(0,0,0,0.02)";
    point.clearBtn.style.display = "none";
    resetAnchorList();
  });

  // Counts
  const countsWrap = document.createElement("div");
  countsWrap.style.display = "grid";
  countsWrap.style.gridTemplateColumns = "1fr 1fr 1fr";
  countsWrap.style.gap = "8px";
  countsWrap.style.marginTop = "6px";
  // Grid-fill mode: hide count inputs (anchor-based fill)
  countsWrap.style.display = "none";

  function mkCount(labelText) {
    const c = document.createElement("div");
    c.style.display = "flex";
    c.style.flexDirection = "column";
    c.style.gap = "4px";

    const l = document.createElement("div");
    l.textContent = labelText;
    l.style.fontSize = "12px";
    l.style.opacity = "0.9";

    const input = document.createElement("input");
    input.type = "number";
    input.min = "1";
    input.step = "1";
    input.value = "1";
    input.style.padding = "10px 10px";
    input.style.borderRadius = "12px";
    input.style.border = th.inputBord;
    input.style.background = th.inputBg;
    input.style.color = th.color;
    input.style.fontSize = "12px";

    c.appendChild(l);
    c.appendChild(input);
    return { c, input };
  }

  const cx = mkCount("Count X");
  const cy = mkCount("Count Y");
  const cz = mkCount("Count Z");
  countsWrap.appendChild(cx.c);
  countsWrap.appendChild(cy.c);
  countsWrap.appendChild(cz.c);
  panel.appendChild(countsWrap);

  const actions = document.createElement("div");
  actions.style.display = "flex";
  actions.style.justifyContent = "space-between";
  actions.style.gap = "10px";
  actions.style.marginTop = "12px";

  const btnCancel = document.createElement("button");
  btnCancel.textContent = "Cancel";
  btnCancel.style.flex = "1";
  btnCancel.style.padding = "10px 12px";
  btnCancel.style.borderRadius = "12px";
  btnCancel.style.border = th.panelBord;
  btnCancel.style.background = th.cancelBg;
  btnCancel.style.color = th.color;
  btnCancel.style.cursor = "pointer";

  const btnFill = document.createElement("button");
  btnFill.textContent = "Fill";
  btnFill.style.flex = "1";
  btnFill.style.padding = "10px 12px";
  btnFill.style.borderRadius = "12px";
  btnFill.style.border = "1px solid rgba(79,156,249,0.4)";
  btnFill.style.background = "rgba(79,156,249,0.20)";
  btnFill.style.color = "white";
  btnFill.style.cursor = "pointer";
  btnFill.style.fontWeight = "700";

  actions.appendChild(btnCancel);
  actions.appendChild(btnFill);
  panel.appendChild(actions);

  btnCancel.addEventListener("click", () => closeRectPatternPanel());

  btnFill.addEventListener("click", async () => {
    const rp = window.builderState.rectPattern;
    if (!rp || !rp.seedName || !rp.aAnchor?.pos) { showToast("Pick the first object."); return; }
    if (!rp.bAnchor?.pos || !rp.delta) { showToast("Pick the second anchor."); return; }

    // --- Grid fill mode (A1..Z500 style) ---
    // If the seed is anchored to the same target object as the 2nd anchor, and both anchors
    // look like <Letter><Number> (e.g., A1, H12), we fill the entire rectangular range.
    function __normGridKey(s) {
      if (!s) return "";
      return String(s).trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    }
    function __parseGridKey(s) {
      const k = __normGridKey(s);
      const m = k.match(/^([A-Z])(\d{1,4})$/);
      if (!m) return null;
      const col = m[1].charCodeAt(0) - 65;
      const row = parseInt(m[2], 10);
      if (!(col >= 0 && col <= 25)) return null;
      if (!(row >= 1 && row <= 500)) return null;
      return { col, row, key: `${m[1]}${row}` };
    }
    function __keyFromCR(col, row) {
      const c = String.fromCharCode(65 + col);
      return `${c}${row}`;
    }

    const seedMeta = window.builderState.components?.[rp.seedName] || null;
    const seedObj = objectsByName.get(rp.seedName);
    const seedAttach = seedMeta?.attach || (seedObj?.userData?.builderAttach) || null;

    // If the seed is attached through a chain (tube -> adapter -> plate), find the attach step
    // that directly targets the chosen parent (usually the fixture plate).
    function __findAttachToAncestor(childName, ancestorName) {
      try {
        let cur = childName;
        for (let i = 0; i < 60; i++) {
          const m = window.builderState.components?.[cur] || null;
          const o = objectsByName.get(cur);
          const at = m?.attach || o?.userData?.builderAttach || o?.userData?.attach || null;
          if (!at || !at.parent_name) return null;
          if (at.parent_name === ancestorName) return at;
          cur = at.parent_name;
        }
      } catch(e) {}
      return null;
    }

    // In grid-fill, users often click a seed that is anchored *indirectly* (A1 tube -> adapter -> plate).
    // We allow that by inferring the seed's A1 key from proximity on the chosen target object.
    const gridB = __parseGridKey(rp.bAnchor?.anchorName);

    // The grid we fill is defined by the object you picked the 2nd anchor on.
    // Usually this is the fixture plate. The seed may be attached indirectly to it.
    const targetParentName = rp.bAnchor?.ownerName || null;

    // Try to find the seed's plate-level attachment (seed -> ... -> targetParentName).
    const seedAttachToTarget = (targetParentName && rp.seedName) ? __findAttachToAncestor(rp.seedName, targetParentName) : null;

    let gridA = null;
    if (seedAttachToTarget?.parent_anchor) {
      gridA = __parseGridKey(seedAttachToTarget.parent_anchor);
    } else if (seedAttach?.parent_name && targetParentName && seedAttach.parent_name === targetParentName) {
      // direct attach fallback
      gridA = __parseGridKey(seedAttach?.parent_anchor);
    }
    // If the seed isn't directly anchored to the target parent (or anchor isn't A1-style), infer it.
    if (!gridA && seedObj && targetParentName) {
      try {
        const parentObjGuess = objectsByName.get(targetParentName);
        const parentSolidWanted = (seedAttachToTarget?.parent_solid) || (seedAttach?.parent_solid) || (parentObjGuess?.userData?.solidName) || null;
        const __getAnchorsForObj = (obj, preferredSolid=null) => {
          const ud = obj?.userData || {};
          const ab = (ud.anchorsBySolid && typeof ud.anchorsBySolid === "object") ? ud.anchorsBySolid : null;
          if (preferredSolid && ab) {
            if (ab[preferredSolid]) return { anchors: ab[preferredSolid], solid: preferredSolid };
            const want = String(preferredSolid).toLowerCase();
            const keys = Object.keys(ab);
            const exactCI = keys.find(k => String(k).toLowerCase() === want);
            if (exactCI && ab[exactCI]) return { anchors: ab[exactCI], solid: exactCI };
            const contains = keys.find(k => String(k).toLowerCase().includes(want) || want.includes(String(k).toLowerCase()));
            if (contains && ab[contains]) return { anchors: ab[contains], solid: contains };
          }
          if (ud.anchors && typeof ud.anchors === "object" && Object.keys(ud.anchors).length) {
            return { anchors: ud.anchors, solid: ud.solidName || null };
          }
          if (!ab) return { anchors: {}, solid: null };
          const solid = (ud.solidName && ab[ud.solidName]) ? ud.solidName : (ab["solid_0"] ? "solid_0" : Object.keys(ab)[0]);
          return { anchors: (solid && ab[solid]) ? ab[solid] : {}, solid: solid || null };
        };
        const parentHit = __getAnchorsForObj(parentObjGuess, parentSolidWanted);
        const anchorsMap = parentHit.anchors || {};

        const seedWorldPos = new THREE.Vector3();
        seedObj.getWorldPosition(seedWorldPos);
        let best = null;
        let bestD = Infinity;
        for (const k of Object.keys(anchorsMap)) {
          const ck = (function(s){
            if (!s) return null;
            const t = String(s).trim().replace(/^hole[ _-]?/i, "").replace(/^anchor[ _-]?/i, "");
            const m = t.match(/^([A-Za-z])[ _-]?(\d{1,4})$/);
            if (!m) return null;
            const L = m[1].toUpperCase();
            const N = parseInt(m[2], 10);
            if (!(L >= "A" && L <= "Z")) return null;
            if (!(N >= 1 && N <= 500)) return null;
            return `${L}${N}`;
          })(k);
          if (!ck) continue;
          const w = __rpAnchorWorldPose(targetParentName, k);
          if (!w || !w.pos) continue;
          const d = w.pos.distanceTo(seedWorldPos);
          if (d < bestD) { bestD = d; best = ck; }
        }
        // Tolerance: ~1mm in your units (mm). If it's farther, it's probably not sitting on a grid anchor.
        if (best && bestD <= 1.0) {
          gridA = __parseGridKey(best);
        }
      } catch (e) {}
    }

    const canGridFill = !!(targetParentName && gridA && gridB);

    // Anchor-based grid fill only (no distance-based XYZ counts)
    if (!seedObj) {
      showToast("Seed object missing.");
      return;
    }
    if (!rp.bAnchor || !rp.bAnchor.ownerName || !rp.bAnchor.anchorName) {
      showToast("Pick the second anchor first.");
      return;
    }
    if (!canGridFill) {
      showToast("Grid fill requires A1-style anchors on the chosen target object.");
      return;
    }

    // Spawn clones of the same type/options as the seed
    // seedObj already resolved above
    const type = seedMeta?.type || seedObj?.userData?.typeName || seedObj?.userData?.componentType || seedObj?.userData?.component;
    if (!type) { showToast("Couldn't determine seed type."); return; }

    // Options for spawn (exclude attach so it spawns free)
    const options = {};
    if (seedMeta) {
      for (const [k,v] of Object.entries(seedMeta)) {
        if (k === "type" || k === "attach") continue;
        options[k] = v;
      }
    }

    // Place instances.
    // If grid-fill applies (seed anchored to same parent as the chosen 2nd anchor AND both anchors look like A1..Z500),
    // we *truly anchor* each new instance to each anchor key in the rectangular range.
    // Otherwise, we fall back to even spacing in XYZ between the two picked world points.

    let spawned = 0;
    if (canGridFill) {
      const parentName = targetParentName;
      const parentObj = objectsByName.get(parentName);
      if (!parentObj) { showToast("Grid fill failed: parent object missing."); return; }

      // Treat the entire fill as one atomic Undo/Redo action.
      const __patternNames = [];
      const __patternSpecs = {};
      const __patternMetas = {};
      // (undo is NOT suspended for this pattern; instances undo one-by-one)
      // IMPORTANT: Use the *same* anchor lookup logic/solid choice that snap() will use.
      // Previously, we built anchorsMap using a potentially different solid than the one
      // passed into __snapChildToParentAnchor(), which caused: we "think" A4 exists, but snap
      // looks in another solid and fails -> objects spawn at the default pose.
                        function __getAnchorsForObj(obj, preferredSolid=null) {
              const ud = obj?.userData || {};
              const ab = (ud.anchorsBySolid && typeof ud.anchorsBySolid === "object") ? ud.anchorsBySolid : null;
              // If UI selected a solid, use that solid's anchors even if ud.anchors exists.
              if (preferredSolid && ab) {
                if (ab[preferredSolid]) return { anchors: ab[preferredSolid], solid: preferredSolid };
                const want = String(preferredSolid).toLowerCase();
                const keys = Object.keys(ab);
                const exactCI = keys.find(k => String(k).toLowerCase() === want);
                if (exactCI && ab[exactCI]) return { anchors: ab[exactCI], solid: exactCI };
                const contains = keys.find(k => String(k).toLowerCase().includes(want) || want.includes(String(k).toLowerCase()));
                if (contains && ab[contains]) return { anchors: ab[contains], solid: contains };
              }
              // Otherwise prefer legacy ud.anchors.
              if (ud.anchors && typeof ud.anchors === "object" && Object.keys(ud.anchors).length) {
                return { anchors: ud.anchors, solid: ud.solidName || null };
              }
              if (!ab) return { anchors: {}, solid: null };
              const solid = (ud.solidName && ab[ud.solidName]) ? ud.solidName : (ab["solid_0"] ? "solid_0" : Object.keys(ab)[0]);
              return { anchors: (solid && ab[solid]) ? ab[solid] : {}, solid: solid || null };
            }



      // Resolve parent anchors + actual solid key once.
      const parentSolidWanted = (seedAttachToTarget?.parent_solid) || (seedAttach?.parent_solid) || (parentObj?.userData?.solidName) || null;
      const parentHit = __getAnchorsForObj(parentObj, parentSolidWanted);
      const anchorsMap = parentHit.anchors || {};
      const parentSolidResolved = parentHit.solid || parentSolidWanted || (parentObj?.userData?.solidName) || "solid_0";

      // Build a canonical->actual key map so we can handle keys like "hole_A4".
      // Canonical form is "A4".
      function __canonGridKey(s) {
        if (!s) return null;
        const t = String(s).trim().replace(/^hole[ _-]?/i, "").replace(/^anchor[ _-]?/i, "");
        const m = t.match(/^([A-Za-z])[ _-]?(\d{1,4})$/);
        if (!m) return null;
        const L = m[1].toUpperCase();
        const N = parseInt(m[2], 10);
        if (!(L >= "A" && L <= "Z")) return null;
        if (!(N >= 1 && N <= 500)) return null;
        return `${L}${N}`;
      }
      const parentKeyByCanon = new Map();
      for (const k of Object.keys(anchorsMap || {})) {
        const ck = __canonGridKey(k);
        if (ck && !parentKeyByCanon.has(ck)) parentKeyByCanon.set(ck, k);
      }

      const c0 = Math.min(gridA.col, gridB.col);
      const c1 = Math.max(gridA.col, gridB.col);
      const r0 = Math.min(gridA.row, gridB.row);
      const r1 = Math.max(gridA.row, gridB.row);

      const childAnchor = (seedAttach?.child_anchor) || (rp.childAnchor) || "center";
      const childSolid = (seedAttach?.child_solid) || (seedObj?.userData?.solidName) || "solid_0";

      // Generate rectangle in row-major order: A1,A2,... then next letter.
      const targets = [];
      for (let col=c0; col<=c1; col++) {
        for (let row=r0; row<=r1; row++) {
          const key = __keyFromCR(col,row);
          if (key === __normGridKey(seedAttachToTarget?.parent_anchor || seedAttach?.parent_anchor)) continue; // skip original
          targets.push(key);
        }
      }

      if (!targets.length) {
        showToast("No anchors found in that range on the target object.");
        return;
      }

      showToast(`Grid fill: anchoring ${targets.length} instance(s)…`);

      // Detect cap children of the seed (tube→cap auto-spawn pairs)
      // and capture the cap-relative-to-tube world offset from the seed pair.
      // This avoids needing to look up anchors on clones (which may not have
      // settled yet due to async GLTF loads / server echo race conditions).
      const __seedCapChildren = [];
      for (const [cn, cm] of Object.entries(window.builderState.components || {})) {
        if (!cm || !cm.attach || cm.attach.parent_name !== rp.seedName) continue;
        if (!String(cm.type || "").startsWith("cap_")) continue;
        const seedTubeObj = objectsByName.get(rp.seedName);
        const seedCapObj  = objectsByName.get(cn);
        let relPos = null, relQuat = null;
        if (seedTubeObj && seedCapObj) {
          const tWP = new THREE.Vector3(); seedTubeObj.getWorldPosition(tWP);
          const tWQ = new THREE.Quaternion(); seedTubeObj.getWorldQuaternion(tWQ);
          const cWP = new THREE.Vector3(); seedCapObj.getWorldPosition(cWP);
          const cWQ = new THREE.Quaternion(); seedCapObj.getWorldQuaternion(cWQ);
          const tWQInv = tWQ.clone().invert();
          relPos  = cWP.clone().sub(tWP).applyQuaternion(tWQInv);
          relQuat = tWQInv.clone().multiply(cWQ);
        }
        __seedCapChildren.push({ name: cn, type: cm.type, attach: cm.attach, relPos, relQuat });
      }

      // Spawn is async (server -> client GLTF load). We must wait until the
      // spawned object exists in objectsByName before snapping, otherwise the
      // instance remains at its default pose (often the middle of the scene)
      // and no attach is persisted.
      async function __waitForObject(name, timeoutMs = 8000) {
        const t0 = performance.now();
        return await new Promise((resolve) => {
          function tick() {
            const obj = objectsByName.get(name);
            if (obj) return resolve(obj);
            if (performance.now() - t0 > timeoutMs) return resolve(null);
            requestAnimationFrame(tick);
          }
          tick();
        });
      }
      for (const parentAnchor of targets) {
        try {
          // Skip anchors that truly don't exist on the resolved parent solid.
          // Accept variants like hole_A4 by canonicalizing keys.
          const actualKey = anchorsMap[parentAnchor] ? parentAnchor : (parentKeyByCanon.get(parentAnchor) || null);
          if (!actualKey) continue;
          // Don't create a duplicate on the seed's own anchor.
          try {
            const seedPA = seedAttachToTarget?.parent_anchor || null;
            const seedCanon = __canonGridKey(seedPA);
            if (seedCanon && __canonGridKey(parentAnchor) === seedCanon) continue;
          } catch(e) {}

          const name = await spawnComponentSilent(type, null, options);
          const spawnedObj = await __waitForObject(name);
          if (!spawnedObj) throw new Error(`snap: spawned object not ready (${name})`);
          // Truly anchor using the same UI snap pipeline that manual anchoring uses.
          // This avoids any mismatch between our anchor-resolution code and the live
          // anchor pick/snap path.
          window.builderState.mode = "PICK_TARGET_ANCHOR";
          window.builderState.pending = { name, type, sourceAnchor: childAnchor, childSolid };
          window.builderState.targetName = parentName;
          window.handleAnchorPick(parentName, actualKey);

          // If snapping failed, handleAnchorPick() returns without setting attach.
          const postMeta = window.builderState.components?.[name];
          if (!postMeta?.attach) {
            throw new Error(`snap: failed to attach ${name} to ${parentName}.${actualKey}`);
          }

          // Keep the config clean: store canonical parent anchor (e.g. "A4") even if
          // the underlying CAD key is "hole_A4".
          try {
            const cur = (window.builderState.components && window.builderState.components[name]) ? window.builderState.components[name] : {};
            const at = cur.attach || {};
            const cleanAttach = Object.assign({}, at, {
              parent_name: parentName,
              parent_solid: parentSolidResolved || at.parent_solid,
              parent_anchor: parentAnchor,
              child_solid: childSolid,
              child_anchor: childAnchor,
              offset: [0,0,0,0,0,0]
            });
            window.builderState.components[name] = Object.assign({}, cur, { type: type }, (options||{}), { attach: cleanAttach });
            if (spawnedObj && spawnedObj.userData) spawnedObj.userData.builderAttach = cleanAttach;
            socket.emit("upstream_update", { [name]: { builder: { attach: cleanAttach } } });
          } catch(e) { console.warn("gridfill: attach cleanup failed", e); }

          // Mark pattern lineage so deleting the seed can delete its pattern siblings.
          window.builderState.components[name] = Object.assign({}, window.builderState.components[name]||{}, { patternParent: rp.seedName, patternMode: "GRID_FILL" });

          // Clone cap children (tube→cap pairs) onto this new clone.
          // Instead of calling __snapChildToParentAnchor on the clone (which
          // races with server echo scene_updates that reset the cap position),
          // we use the precomputed world-offset from the seed tube→cap pair
          // and apply it directly to the tube clone's current transform.
          const tubeCloneName = name;
          for (const capChild of __seedCapChildren) {
            try {
              const capCloneName = await spawnComponentSilent(capChild.type);
              const capCloneObj = await __waitForObject(capCloneName);
              if (!capCloneObj) continue;

              const tubeCloneObj = objectsByName.get(tubeCloneName);
              if (tubeCloneObj && capChild.relPos && capChild.relQuat) {
                const tWP = new THREE.Vector3(); tubeCloneObj.getWorldPosition(tWP);
                const tWQ = new THREE.Quaternion(); tubeCloneObj.getWorldQuaternion(tWQ);
                const capWorldPos = capChild.relPos.clone().applyQuaternion(tWQ).add(tWP);
                const capWorldQ   = tWQ.clone().multiply(capChild.relQuat);
                capCloneObj.position.copy(capWorldPos);
                capCloneObj.quaternion.copy(capWorldQ);
                // Guard against server echo overwriting this position
                capCloneObj.userData.__builderPoseGuard = performance.now();
                const rod = window.quaternionToRodriguesDeg
                  ? window.quaternionToRodriguesDeg(capWorldQ) : [0,0,0];
                const pose = [capWorldPos.x, capWorldPos.y, capWorldPos.z, rod[0]||0, rod[1]||0, rod[2]||0];
                socket.emit("upstream_update", { [capCloneName]: { pose } });
              }

              const __pAnchor = capChild.attach.parent_anchor || "place";
              const __cSolid  = capChild.attach.child_solid || "body";
              const __cAnchor = capChild.attach.child_anchor || "center";
              const __pSolid  = capChild.attach.parent_solid || "body";
              const capAttach = {
                parent_name: tubeCloneName, parent_solid: __pSolid,
                parent_anchor: __pAnchor,
                child_solid: __cSolid,
                child_anchor: __cAnchor,
                offset: [0,0,0,0,0,0]
              };
              window.builderState.components[capCloneName] = Object.assign(
                {}, window.builderState.components[capCloneName] || {},
                { attach: capAttach, capParent: tubeCloneName, patternParent: rp.seedName, patternMode: "GRID_FILL" }
              );
              if (capCloneObj.userData) capCloneObj.userData.builderAttach = capAttach;
              socket.emit("upstream_update", { [capCloneName]: { builder: { attach: capAttach } } });
              __patternNames.push(capCloneName);
              try { __patternSpecs[capCloneName] = __deepClone(window.builderState.specs?.[capCloneName] || {}); } catch(e) {}
              try { __patternMetas[capCloneName] = __deepClone(window.builderState.components?.[capCloneName] || {}); } catch(e) {}
            } catch (capErr) { console.warn("gridfill: cap clone failed", capErr); }
          }

          // capture final spec/meta for redo
          __patternNames.push(name);
          try { __patternSpecs[name] = __deepClone(window.builderState.specs?.[name] || {}); } catch(e) {}
          try { __patternMetas[name] = __deepClone(window.builderState.components?.[name] || {}); } catch(e) {}
          spawned++;
        } catch (e) {
          console.error(e);
          // If we couldn't snap, it's better to stop than to keep dumping parts at origin.
          showToast(String(e?.message || e));
          break;
        }
      }
    } else {
      // Even XYZ spacing (Fusion-like)
      // Treat the entire fill as one atomic Undo/Redo action.
      const __patternNames = [];
      const __patternSpecs = {};
      const __patternMetas = {};
      // (undo is NOT suspended for this pattern; instances undo one-by-one)
      for (let iz=0; iz<nz; iz++) {
        for (let iy=0; iy<ny; iy++) {
          for (let ix=0; ix<nx; ix++) {
            if (ix===0 && iy===0 && iz===0) continue;
            const anchorPos = new THREE.Vector3(
              rp.aAnchor.pos.x + ix*sx,
              rp.aAnchor.pos.y + iy*sy,
              rp.aAnchor.pos.z + iz*sz
            );

            // Maintain the seed object's offset from its attachment anchor.
            const pos = anchorPos.clone().add(rp.seedOffset || new THREE.Vector3());

            try {
              const name = await spawnComponentSilent(type, null, options);
              const obj = objectsByName.get(name);
              if (obj) {
              // Mark this instance as coming from a pattern (used for delete-with-children / debugging).
              window.builderState.components[name] = Object.assign({}, window.builderState.components[name]||{}, { patternParent: rp.seedName, patternMode: "XYZ" });
                obj.quaternion.copy(rp.seedWorldQuat || new THREE.Quaternion());
                socket.emit("upstream_update", { [name]: { pose: [pos.x,pos.y,pos.z,0,0,0] } });
              }
              window.builderState.components[name] = Object.assign({}, window.builderState.components[name]||{}, { patternParent: rp.seedName, patternMode: "XYZ" });
              __patternNames.push(name);
              try { __patternSpecs[name] = __deepClone(window.builderState.specs?.[name] || {}); } catch(e) {}
              try { __patternMetas[name] = __deepClone(window.builderState.components?.[name] || {}); } catch(e) {}
              spawned++;
            } catch (e) {
              console.error(e);
          showToast(String(e?.message || e));
          // Stop so we don't litter the scene with unanchored spawns.
          break;
            }
          }
        }
      }
    }

    // Safety: ensure seed object was not accidentally removed during fill
    if (rp.seedName && !objectsByName.has(rp.seedName)) {
      console.warn("Pattern fill: seed object was lost, restoring...");
      const seedSpec = window.builderState.specs?.[rp.seedName];
      if (seedSpec) {
        try { upsertObject(rp.seedName, seedSpec); } catch(e) { console.error(e); }
      }
    }

    // Deferred resnap for cap clones: the server echoes scene_update with the
    // cap's default spawn pose, overwriting our position set inside the loop.
    // Wait for all echoes to settle, then reposition using the seed pair's
    // world-space offset (does NOT depend on clone anchor lookups).
    {
      const __capsToResnap = [];
      for (const [cn, cm] of Object.entries(window.builderState.components || {})) {
        if (!cm?.capParent || !cm?.attach) continue;
        if (cm.patternParent !== rp.seedName) continue;
        __capsToResnap.push({ capName: cn, tubeName: cm.capParent });
      }
      if (__capsToResnap.length) {
        // Compute cap-relative-to-tube offset from the seed pair (still in scene)
        const seedTubeObj = objectsByName.get(rp.seedName);
        let __seedCapName = null;
        for (const [cn, cm] of Object.entries(window.builderState.components || {})) {
          if (cm?.attach?.parent_name === rp.seedName && String(cm.type||"").startsWith("cap_")) { __seedCapName = cn; break; }
        }
        const seedCapObj = __seedCapName ? objectsByName.get(__seedCapName) : null;

        if (seedTubeObj && seedCapObj) {
          const stWP = new THREE.Vector3(); seedTubeObj.getWorldPosition(stWP);
          const stWQ = new THREE.Quaternion(); seedTubeObj.getWorldQuaternion(stWQ);
          const scWP = new THREE.Vector3(); seedCapObj.getWorldPosition(scWP);
          const scWQ = new THREE.Quaternion(); seedCapObj.getWorldQuaternion(scWQ);
          const stWQInv = stWQ.clone().invert();
          const relPos  = scWP.clone().sub(stWP).applyQuaternion(stWQInv);
          const relQuat = stWQInv.clone().multiply(scWQ);

          await new Promise(r => setTimeout(r, 800));
          for (const { capName, tubeName } of __capsToResnap) {
            try {
              const capObj  = objectsByName.get(capName);
              const tubeObj = objectsByName.get(tubeName);
              if (!capObj || !tubeObj) continue;
              const tWP = new THREE.Vector3(); tubeObj.getWorldPosition(tWP);
              const tWQ = new THREE.Quaternion(); tubeObj.getWorldQuaternion(tWQ);
              const capPos = relPos.clone().applyQuaternion(tWQ).add(tWP);
              const capQ   = tWQ.clone().multiply(relQuat);
              capObj.position.copy(capPos);
              capObj.quaternion.copy(capQ);
              capObj.userData.__builderPoseGuard = performance.now();
              const rod = window.quaternionToRodriguesDeg ? window.quaternionToRodriguesDeg(capQ) : [0,0,0];
              const pose = [capPos.x, capPos.y, capPos.z, rod[0]||0, rod[1]||0, rod[2]||0];
              socket.emit("upstream_update", { [capName]: { pose } });
            } catch(e) { console.warn("deferred cap resnap:", capName, e); }
          }
        }
      }
    }

    showToast(`Patterned ${spawned} instance(s).`);
    closeRectPatternPanel();
  });

  // expose ui refs so click handler can update it
  window.builderState.rectPatternUi = {
    seedBox: seed.box,
    seedClear: seed.clearBtn,
    pointBox: point.box,
    pointClear: point.clearBtn,
    anchorListBtn: btnAnchorList,
    populateAnchorList,
    resetAnchorList,
    nx: cx.input, ny: cy.input, nz: cz.input,
    hint
  };

  // initial reset
  seed.clearBtn.style.display = "none";
  point.clearBtn.style.display = "none";

  // mount panel
  document.body.appendChild(panel);
}


  

// ── Sidebar resize: drag the right edge; width persists per browser ──────
// Long component names in the Objects list truncate on the default 264 px —
// widen to taste. The viewer canvas only re-measures on window resize, so
// dispatch one while dragging.
(function () {
  const sidebar = document.querySelector(".sidebar");
  const handle = document.getElementById("sbResizeHandle");
  if (!sidebar || !handle) return;
  const KEY = "sb_sidebar_w";

  function applyW(w) {
    w = Math.max(220, Math.min(560, Math.round(w)));
    // Drive the same CSS variable the first-paint snippet in index.html
    // sets — one source of width, no late-apply jump.
    document.documentElement.style.setProperty("--sbw", w + "px");
    window.dispatchEvent(new Event("resize"));
    return w;
  }

  let dragging = false, startX = 0, startW = 0;
  handle.addEventListener("pointerdown", (e) => {
    dragging = true;
    startX = e.clientX;
    startW = sidebar.getBoundingClientRect().width;
    handle.classList.add("dragging");
    handle.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  handle.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    applyW(startW + (e.clientX - startX));
  });
  const end = () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove("dragging");
    localStorage.setItem(KEY, String(Math.round(sidebar.getBoundingClientRect().width)));
  };
  handle.addEventListener("pointerup", end);
  handle.addEventListener("pointercancel", end);
})();

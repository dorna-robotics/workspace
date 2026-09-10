// Anchor gizmos — the ONE definition of how an anchor looks in a 3D
// viewer (orchestrator viewer, scene builder): small axes, a plain bold
// label, a small dot that grows with a glow ring while the cursor is on
// it, and a larger invisible sphere to hover / click against.
//
// Everything draws with depthTest off — an anchor inside a rack must
// stay visible through the geometry — and picking agrees with that:
// `nearestAnchorHit` chooses by screen distance to the cursor, never by
// depth (see the comment there).
//
// A viewer owns the list of gizmos and the layer they live in; this
// module owns their construction, placement and visual states.

import * as THREE from "three";

export const ANCHOR_COLOR = 0x0a84ff;
const AXES_SIZE = 7;
const LABEL_LIFT = 18;           // label floats this far above the anchor (world z)
const HOVER_DOT_SCALE = 2.5;
const HOVER_LABEL_SCALE = 1.5;
const LABEL_ORDER = 999;
const LABEL_ORDER_FRONT = 1006;  // hovered label draws over its neighbours

// Plain bold text at rest — no plate: a plate on every label turns a
// dense rack into a wall of white. A second, plated texture is built
// here once and swapped in by `labelPlate` while the label is hovered
// or marked, so the one you are pointing at reads over whatever is
// behind it.
export function makeAnchorLabel(text, color = "#000000", fontPx = 72) {
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
  ctx.fillStyle = color;
  ctx.fillText(text, pad, pad);

  const plate = document.createElement("canvas");
  plate.width = canvas.width; plate.height = canvas.height;
  const pctx = plate.getContext("2d");
  const r = 14;
  pctx.beginPath();
  pctx.moveTo(r, 0);
  pctx.arcTo(plate.width, 0, plate.width, plate.height, r);
  pctx.arcTo(plate.width, plate.height, 0, plate.height, r);
  pctx.arcTo(0, plate.height, 0, 0, r);
  pctx.arcTo(0, 0, plate.width, 0, r);
  pctx.closePath();
  pctx.fillStyle = "rgba(232,232,236,0.94)";
  pctx.fill();
  pctx.lineWidth = 3;
  pctx.strokeStyle = "rgba(0,0,0,0.35)";
  pctx.stroke();
  pctx.font = font;
  pctx.textAlign = "left";
  pctx.textBaseline = "top";
  pctx.fillStyle = color;
  pctx.fillText(text, pad, pad);

  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  const texPlate = new THREE.CanvasTexture(plate);
  texPlate.colorSpace = THREE.SRGBColorSpace;
  const mat = new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true });
  const spr = new THREE.Sprite(mat);
  const scale = 0.09;
  spr.scale.set(canvas.width * scale, canvas.height * scale, 1);
  spr.renderOrder = LABEL_ORDER;
  spr.frustumCulled = false;
  spr.userData.__texPlain = tex;
  spr.userData.__texPlate = texPlate;
  spr.userData.__baseScale = spr.scale.clone();
  return spr;
}

export function labelPlate(sprite, on) {
  const u = sprite?.userData; if (!u?.__texPlain) return;
  const want = on ? u.__texPlate : u.__texPlain;
  if (sprite.material.map !== want) {
    sprite.material.map = want;
    sprite.material.needsUpdate = true;
  }
}

export function makeAxesHelperAlwaysOnTop(size) {
  const axes = new THREE.AxesHelper(size);
  axes.traverse(c => {
    if (c.material?.isMaterial) { c.material.depthTest = false; c.renderOrder = 999; }
  });
  axes.renderOrder = 999;
  axes.frustumCulled = false;
  return axes;
}

// One anchor: { axes, label, dot, ring, pick }. `pick` is the hit
// sphere; its userData carries the anchor's identity for the viewer's
// raycast (`__isAnchorPick`, `anchorName`, plus whatever `extra` the
// viewer wants back — owner name, solid key) and back-references to
// the dot / ring / label so hover can find them from the hit alone.
export function makeAnchorGizmo(name, displayName = name, color = ANCHOR_COLOR, extra = {}) {
  const axes = makeAxesHelperAlwaysOnTop(AXES_SIZE);
  const label = makeAnchorLabel(displayName);
  label.material.opacity = 0.9;

  const dot = new THREE.Mesh(
    new THREE.SphereGeometry(1.5, 16, 16),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.6, depthTest: false }));
  dot.renderOrder = 997; dot.frustumCulled = false;

  const ring = new THREE.Mesh(
    new THREE.RingGeometry(4, 7, 24),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.0, depthTest: false, side: THREE.DoubleSide }));
  ring.renderOrder = 996; ring.frustumCulled = false;

  const pick = new THREE.Mesh(
    new THREE.SphereGeometry(8, 12, 12),
    new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.01, depthTest: false }));
  pick.renderOrder = 998; pick.frustumCulled = false;
  Object.assign(pick.userData, extra, {
    __isAnchorPick: true,
    anchorName: name,
    __dotMesh: dot,
    __ringMesh: ring,
    __labelSprite: label,
    __dotColor: color,
  });

  return { axes, label, dot, ring, pick };
}

export function addAnchorGizmo(g, layer, pickableMeshes) {
  layer.add(g.axes, g.label, g.dot, g.ring, g.pick);
  pickableMeshes.add(g.pick);
}

export function removeAnchorGizmo(g, layer, pickableMeshes) {
  layer.remove(g.axes, g.label);
  for (const m of [g.dot, g.ring, g.pick]) {
    layer.remove(m);
    pickableMeshes.delete(m);
    m.geometry.dispose();
    m.material.dispose();
  }
}

// Put the gizmo at a world pose. The ring faces the camera; the label
// floats above the anchor.
export function placeAnchorGizmo(g, pWorld, qWorld, camera) {
  g.axes.position.copy(pWorld); g.axes.quaternion.copy(qWorld);
  g.pick.position.copy(pWorld); g.pick.quaternion.copy(qWorld);
  g.dot.position.copy(pWorld);
  g.ring.position.copy(pWorld); g.ring.lookAt(camera.position);
  g.label.position.copy(pWorld); g.label.position.z += LABEL_LIFT;
}

// Hover state from the pick sphere alone (what a raycast hands back).
// `rest` lets a viewer choose the un-hovered look — the scene builder's
// ruler keeps dots enlarged / recoloured while shift is held.
export function setAnchorHover(pick, on, rest = {}) {
  const d = pick.userData.__dotMesh;
  const r = pick.userData.__ringMesh;
  const l = pick.userData.__labelSprite;
  if (d) {
    d.scale.setScalar(on ? HOVER_DOT_SCALE : (rest.dotScale ?? 1));
    d.material.opacity = on ? 1.0 : 0.6;
    d.material.color.set(on ? (rest.hoverColor ?? pick.userData.__dotColor)
                            : (rest.dotColor ?? pick.userData.__dotColor));
  }
  if (r) r.material.opacity = on ? 0.4 : 0.0;
  if (l) {
    l.material.opacity = on ? 1.0 : 0.9;
    l.scale.copy(l.userData.__baseScale).multiplyScalar(on ? HOVER_LABEL_SCALE : 1);
    // To the front while hovered: every label shares LABEL_ORDER, and in
    // a dense rack the one under the cursor is as likely as not drawn
    // beneath its neighbours. Growing it does not help underneath them.
    l.renderOrder = on ? LABEL_ORDER_FRONT : LABEL_ORDER;
    labelPlate(l, on);
  }
}

// Of the raycast hits, the anchor pick nearest to the CURSOR — not the
// first along the ray. Pick spheres are larger than their dots, so in a
// dense rack several overlap under one cursor and the ray enters a
// neighbour's sphere first whenever that neighbour is nearer the
// camera. Depth is deliberately not consulted: anchors draw through
// geometry, so an anchor you can plainly see must be pickable even
// with a tube in front of it. Returns null when no hit is an anchor.
export function nearestAnchorHit(hits, pointer, camera) {
  let best = null, bestD = Infinity;
  const v = new THREE.Vector3();
  for (const h of hits) {
    if (!h.object?.isMesh || !h.object.userData?.__isAnchorPick) continue;
    h.object.getWorldPosition(v);
    v.project(camera);
    const dx = v.x - pointer.x, dy = v.y - pointer.y;
    const d = dx * dx + dy * dy;
    if (d < bestD) { bestD = d; best = h; }
  }
  return best;
}

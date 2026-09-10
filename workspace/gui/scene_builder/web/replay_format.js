// replay_format.js — the Replay tab's wire format, decoded.
//
//   "DRPL" | uint32 LE header length | header JSON (4-byte padded)
//   | per key, in header order: float32 times[n], float32 poses[n*6]
//
// Pure: no DOM, no three.js — testable in node against the server.
export function parseReplay(buf) {
  const dv = new DataView(buf);
  const magic = String.fromCharCode(dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3));
  if (magic !== "DRPL") throw new Error("not a replay stream");
  const hlen = dv.getUint32(4, true);
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, hlen)));
  let off = 8 + hlen;
  const tl = new Map();
  for (const k of header.keys || []) {
    const t = new Float32Array(buf, off, k.n); off += 4 * k.n;
    const p = new Float32Array(buf, off, 6 * k.n); off += 24 * k.n;
    tl.set(k.name, { t, p });
  }
  return { header, tl };
}

// Index of the last sample at or before ``t`` (binary search).
export function sampleAt(times, t) {
  let lo = 0, hi = times.length - 1, best = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) { best = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return best;
}

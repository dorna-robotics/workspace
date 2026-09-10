// replay_chapters.js — phase chapters of a recording, from the schedule
// events the recorder wrote beside the frames.
//
// A recording can start at any moment of a run, so the recorder also
// wrote the run's schedule so far (events before t=0 carry negative
// times). Each action / swap event is mapped to its phase through the
// plan slices; a phase's chapter runs from its first start to its last
// end, clamped to the recording. Pure: testable without a browser.

export function buildChapters(events, duration) {
  const phaseOf = new Map();     // "replan_id|leaf_name" -> phase name
  for (const row of events || []) {
    const ev = row && row.ev;
    if (!ev || ev.type !== "schedule") continue;
    const rid = ev.replan_id || 0;
    for (const a of ev.actions || []) phaseOf.set(`${rid}|${a.leaf_name}`, ev.phase || null);
    for (const w of ev.swaps || [])   phaseOf.set(`${rid}|${w.leaf_name}`, ev.phase || null);
  }
  const span = new Map();        // phase -> {t0, t1}
  const order = [];
  for (const row of events || []) {
    const ev = row && row.ev;
    if (!ev) continue;
    const isStart = ev.type === "action_start" || ev.type === "swap_start";
    const isEnd = ev.type === "action_end" || ev.type === "swap_end";
    if (!isStart && !isEnd) continue;
    const phase = phaseOf.get(`${ev.replan_id || 0}|${ev.name}`);
    if (!phase) continue;
    const t = Number(row.t) || 0;
    let sp = span.get(phase);
    if (!sp) { sp = { t0: t, t1: t }; span.set(phase, sp); order.push(phase); }
    if (t < sp.t0) sp.t0 = t;
    if (t > sp.t1) sp.t1 = t;
  }
  const out = [];
  for (const phase of order) {
    const sp = span.get(phase);
    const t0 = Math.max(0, sp.t0), t1 = Math.min(duration, sp.t1);
    if (t1 <= 0 || t0 >= duration || t1 <= t0) continue;
    out.push({ phase, t0, t1 });
  }
  out.sort((a, b) => a.t0 - b.t0);
  // Chapters abut: a phase's chapter runs until the next one begins,
  // so the strip has no holes between phases.
  for (let i = 0; i + 1 < out.length; i++) out[i].t1 = Math.max(out[i].t1, out[i + 1].t0);
  if (out.length) out[out.length - 1].t1 = Math.max(out[out.length - 1].t1, duration);
  return out;
}

export function chapterAt(chapters, t) {
  let cur = null;
  for (const c of chapters || []) if (t >= c.t0) cur = c; else break;
  return cur;
}

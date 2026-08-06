// kwargs.js — Shared kwargs form renderer and reader.
// Used by dashboard.js and workspace.js.

const _resetSvg = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>`;
const _infoSvg  = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`;
const _lockSvg  = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>`;

// ── the project's own run-setup screen (``params:``) ─────────────────
//
// Same hosting contract as the pendant screen (hmi-guide §4b) with one
// addition: this one produces VALUES, so it also answers value() and
// may answer validate().
//
//   HTML shape  — data-field="key" on an input/select/checkbox
//   JS shape    — export default {css, mount(root, api), value(), validate()}
//   api         — {schema, values, frozen, theme, onTheme}
//
// Served by the ORCHESTRATOR (same-origin), because Parameters is used
// before launch, when the runtime server is not up yet.

function readBoundFields(root) {
  // HTML shape: collect [data-field] inputs. A project that needs more
  // than plain fields uses the JS shape and returns value() itself.
  const out = {};
  for (const el of root.querySelectorAll("[data-field]")) {
    const key = el.dataset.field;
    if (!key) continue;
    if (el.type === "checkbox") out[key] = el.checked;
    else if (el.type === "number") out[key] = el.value === "" ? null : Number(el.value);
    else out[key] = el.value === "" ? null : el.value;
  }
  return out;
}

async function mountProjectParams(container, schema, values, frozen, wsName) {
  const spec = schema._params || {};
  const base = `/orchestrator/api/workspace/${encodeURIComponent(wsName)}/params/`;
  const holder = document.createElement("div");
  holder.className = "kw-project-params";
  container.appendChild(holder);
  const shadow = holder.attachShadow({ mode: "open" });
  // Fields the screen does not draw keep their declared default (or the
  // value saved from the last run) — a project screen is free to cover
  // only the parameters it cares about.
  const baseValues = {};
  for (const [k, spec] of Object.entries(schema || {})) {
    if (k.startsWith("_") || !spec || typeof spec !== "object") continue;
    if (spec.default !== undefined) baseValues[k] = spec.default;
    if (values?.[k] !== undefined) baseValues[k] = values[k];
  }
  const host = { shadow, module: null, base: baseValues };
  container._paramsHost = host;

  try {
    if (spec.css) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = base + spec.css;
      shadow.appendChild(link);
    }
    const api = {
      get schema() {
        const s = {};
        for (const [k, v] of Object.entries(schema || {})) {
          if (!k.startsWith("_")) s[k] = v;
        }
        return s;
      },
      get values() { return { ...(values || {}) }; },
      frozen: !!frozen,
      get theme() { return document.documentElement.getAttribute("data-theme") || "dark"; },
      onTheme(cb) { (host.themeCbs ||= []).push(cb); },
    };
    if (spec.kind === "js") {
      const mod = await import(/* webpackIgnore: true */ base + spec.src);
      const def = mod.default || mod;
      host.module = def;
      if (def.css) {
        const st = document.createElement("style");
        st.textContent = def.css;
        shadow.appendChild(st);
      }
      if (typeof def.mount === "function") await def.mount(shadow, api);
    } else {
      const res = await fetch(base + spec.src);
      const wrap = document.createElement("div");
      wrap.innerHTML = await res.text();
      shadow.appendChild(wrap);
      // Seed declared fields from current values / schema defaults.
      for (const el of shadow.querySelectorAll("[data-field]")) {
        const key = el.dataset.field;
        const v = values?.[key] !== undefined ? values[key] : schema?.[key]?.default;
        if (v === undefined || v === null) continue;
        if (el.type === "checkbox") el.checked = !!v; else el.value = v;
      }
      if (frozen) {
        for (const el of shadow.querySelectorAll("input,select,textarea,button")) {
          el.disabled = true;
        }
      }
    }
  } catch (err) {
    console.error("project params screen failed to load:", err);
    container._paramsHost = null;
    shadow.innerHTML = "";
    const note = document.createElement("div");
    note.style.cssText = "padding:16px;color:var(--muted);font:14px var(--font,system-ui)";
    note.textContent = "This project's parameters screen failed to load — see the "
                     + "console. Launch is blocked until it loads.";
    shadow.appendChild(note);
  }
}

// Theme toggle reaches a params screen that draws.
new MutationObserver(() => {
  const t = document.documentElement.getAttribute("data-theme") || "dark";
  document.querySelectorAll(".kwargs-form").forEach(c => {
    for (const cb of (c._paramsHost && c._paramsHost.themeCbs) || []) {
      try { cb(t); } catch (_) {}
    }
  });
}).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

export function renderKwargsForm(container, schema, values, frozen = false, wsName = "") {
  // Render-signature cache. Reopening the modal with identical
  // params (most common case) is a no-op now instead of tearing
  // down and rebuilding the whole form + re-attaching listeners.
  // If any input changes (new value from server, frozen flip, etc.)
  // the signature differs and we fall through to the full rebuild.
  const sig = JSON.stringify({ schema, values, frozen, wsName });
  if (container._kwargsSig === sig) return;
  container._kwargsSig = sig;
  container.innerHTML = "";
  // A project that ships its own run-setup screen (``params:``) draws
  // the whole body; the platform keeps the modal chrome, the Start /
  // Launch buttons and schema validation. The generic form below is
  // for everyone else.
  container._paramsHost = null;
  if (schema && schema._params) {
    mountProjectParams(container, schema, values, frozen, wsName);
    return;
  }
  // ``_layout`` is a LAYOUT HINT, not a field: a list of rows, each a
  // list of field keys rendered side by side. Everything not named in
  // it falls through to a stacked row, in declaration order.
  //   _layout: [{row: [tubes, print_label]}]
  const layout = (schema && schema._layout) || null;
  // Underscore keys are reserved for hints, never fields.
  const keys = Object.keys(schema || {}).filter(k => !k.startsWith("_"));
  if (!keys.length) {
    container.innerHTML = `<div class="kwargs-empty">No parameters defined in launch.yaml</div>`;
    return;
  }

  // Banner
  if (frozen) {
    container.insertAdjacentHTML("beforeend",
      `<div class="kwargs-banner frozen">${_lockSvg} Parameters are locked while the workspace is running</div>`);
  } else {
    container.insertAdjacentHTML("beforeend",
      `<div class="kwargs-banner">${_infoSvg} Set parameters before launch. Saved values persist across runs.</div>`);
  }

  // Build the row scaffold declared by _layout; every field lands in
  // its row's column, or in the stacked flow when unlisted.
  const slotFor = {};
  if (Array.isArray(layout)) {
    layout.forEach(entry => {
      // ── row: fields side by side ──────────────────────────────────
      const rowKeys = Array.isArray(entry) ? entry : (entry && entry.row) || [];
      if (!rowKeys.length) return;
      const row = document.createElement("div");
      row.className = "kw-row";
      rowKeys.forEach(k => {
        const col = document.createElement("div");
        col.className = "kw-col";
        row.appendChild(col);
        slotFor[k] = col;
      });
      container.appendChild(row);
    });
  }
  const hostFor = (key) => slotFor[key] || container;

  keys.forEach(key => {
    const spec = schema[key];
    const type = (spec.type || "str").toLowerCase();
    const label = spec.label || key;
    const optional = spec.optional || false;
    const hint = spec.hint || "";
    const defaultVal = spec.default;
    const val = values?.[key] !== undefined ? values[key] : defaultVal;

    const field = document.createElement("div");
    field.className = "kw-field";

    // Label row
    const labelRow = document.createElement("div");
    labelRow.className = "kw-label-row";
    const lbl = document.createElement("span");
    lbl.className = "kw-label";
    lbl.textContent = label;
    labelRow.appendChild(lbl);
    if (optional) {
      const sp = document.createElement("span");
      sp.className = "kw-optional";
      sp.textContent = "(optional)";
      labelRow.appendChild(sp);
    }
    field.appendChild(labelRow);

    // Input row (input + reset button)
    const inputRow = document.createElement("div");
    inputRow.className = "kw-input-row";

    if (type === "file") {
      const fileWrap = document.createElement("div");
      fileWrap.className = "kw-file-wrap";

      const fileLabel = document.createElement("span");
      fileLabel.className = "kw-file-label";
      const currentFile = (val && typeof val === "string") ? val.split("/").pop() : "";
      fileLabel.textContent = currentFile || "No file selected";
      fileLabel.title = val || "";
      fileWrap.appendChild(fileLabel);

      if (!frozen) {
        const fileInput = document.createElement("input");
        fileInput.type = "file";
        fileInput.style.display = "none";
        if (spec.accept) fileInput.accept = spec.accept;

        const chooseBtn = document.createElement("button");
        chooseBtn.className = "btn btn-sm";
        chooseBtn.textContent = currentFile ? "Replace" : "Choose";
        chooseBtn.addEventListener("click", () => fileInput.click());

        fileInput.addEventListener("change", async () => {
          if (!fileInput.files.length) return;
          chooseBtn.disabled = true;
          chooseBtn.textContent = "Uploading…";
          try {
            const fd = new FormData();
            fd.append("file", fileInput.files[0]);
            const resp = await fetch(`/orchestrator/api/workspace/${encodeURIComponent(wsName)}/upload/${encodeURIComponent(key)}`, {
              method: "POST",
              body: fd,
            });
            if (!resp.ok) throw new Error((await resp.json()).error || "Upload failed");
            const result = await resp.json();
            fileLabel.textContent = result.filename;
            fileLabel.title = result.path;
            fileWrap.dataset.kwValue = result.path;
            chooseBtn.textContent = "Replace";
          } catch (err) {
            fileLabel.textContent = "Upload failed";
            chooseBtn.textContent = currentFile ? "Replace" : "Choose";
          } finally {
            chooseBtn.disabled = false;
          }
        });

        fileWrap.appendChild(fileInput);
        fileWrap.appendChild(chooseBtn);
      }

      fileWrap.dataset.kwKey = key;
      fileWrap.dataset.kwType = "file";
      fileWrap.dataset.kwValue = val || "";
      inputRow.appendChild(fileWrap);
      field.appendChild(inputRow);
      if (hint) {
        const h = document.createElement("div");
        h.className = "kw-hint";
        h.textContent = hint;
        field.appendChild(h);
      }
      hostFor(key).appendChild(field);
      return;
    }

    let input;
    if (type === "bool") {
      // Touch toggle, not a bare checkbox — operator surfaces need a
      // 44px target and unambiguous on/off (design-system §8/§10).
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = val === true || val === "true";
      input.dataset.kwKey = key;
      input.dataset.kwType = "bool";
      input.className = "kw-checkbox kw-switch";
      if (frozen) input.disabled = true;
      const sw = document.createElement("label");
      sw.className = "kw-switch-wrap";
      sw.appendChild(input);
      const track = document.createElement("span");
      track.className = "kw-switch-track";
      sw.appendChild(track);
      inputRow.appendChild(sw);
      field.appendChild(inputRow);
      if (hint) {
        const h = document.createElement("div");
        h.className = "kw-hint";
        h.textContent = hint;
        field.appendChild(h);
      }
      hostFor(key).appendChild(field);
      return;
    } else if (type === "textarea") {
      input = document.createElement("textarea");
      input.className = "input";
      input.rows = spec.rows || 4;
      input.value = (val === null || val === undefined) ? "" : (typeof val === "string" ? val : JSON.stringify(val, null, 2));
      input.placeholder = spec.placeholder || (optional ? "empty = null" : "");
      input.dataset.kwKey = key;
      input.dataset.kwType = "textarea";
      input.className += " kw-textarea";
      if (frozen) input.readOnly = true;
    } else if (type === "choice" && Array.isArray(spec.options)) {
      input = document.createElement("select");
      input.className = "input";
      spec.options.forEach(opt => {
        const o = document.createElement("option");
        o.value = opt;
        o.textContent = opt;
        if (String(opt) === String(val)) o.selected = true;
        input.appendChild(o);
      });
      input.dataset.kwKey = key;
      input.dataset.kwType = "choice";
      if (frozen) input.disabled = true;
    } else {
      input = document.createElement("input");
      input.className = "input";
      input.type = (type === "int" || type === "float") ? "number" : "text";
      if (type === "int") input.step = "1";
      if (type === "float") input.step = "any";
      if (spec.min !== undefined && spec.min !== null) input.min = spec.min;
      if (spec.max !== undefined && spec.max !== null) input.max = spec.max;
      input.value = (val === null || val === undefined) ? "" : val;
      input.placeholder = spec.placeholder || (optional ? "empty = null" : "");
      input.dataset.kwKey = key;
      input.dataset.kwType = type;
      if (frozen) input.readOnly = true;
    }
    inputRow.appendChild(input);

    // Per-field reset button
    if (!frozen) {
      const rst = document.createElement("button");
      rst.className = "kw-reset";
      rst.title = `Reset to default (${defaultVal === null ? "null" : defaultVal})`;
      rst.innerHTML = _resetSvg;
      rst.addEventListener("click", () => {
        if (type === "bool") {
          input.checked = defaultVal === true || defaultVal === "true";
        } else if (type === "choice") {
          input.value = defaultVal !== null && defaultVal !== undefined ? String(defaultVal) : "";
        } else if (type === "textarea") {
          input.value = (defaultVal === null || defaultVal === undefined) ? "" : (typeof defaultVal === "string" ? defaultVal : JSON.stringify(defaultVal, null, 2));
        } else {
          input.value = (defaultVal === null || defaultVal === undefined) ? "" : defaultVal;
        }
      });
      inputRow.appendChild(rst);
    }

    field.appendChild(inputRow);

    // Hint text
    if (hint) {
      const h = document.createElement("div");
      h.className = "kw-hint";
      h.textContent = hint;
      field.appendChild(h);
    }

    hostFor(key).appendChild(field);
  });
}

export function validateKwargsForm(container, schema) {
  const errors = [];
  // A project screen draws its own inputs, so the per-field walk below
  // has nothing to walk. Validate what it RETURNS against the schema
  // instead — the schema is the contract, and a project screen is not
  // trusted to enforce it — then let the screen add its own message.
  const host = container._paramsHost;
  if (host) {
    const values = readKwargsForm(container);
    for (const [key, spec] of Object.entries(schema || {})) {
      if (key.startsWith("_") || !spec || typeof spec !== "object") continue;
      const v = values[key];
      const missing = v === undefined || v === null || v === "" ||
                      (Array.isArray(v) && !v.length) ||
                      (v && typeof v === "object" && !Array.isArray(v) &&
                       !Object.keys(v).length);
      if (missing && !spec.optional) {
        errors.push(`${spec.label || key} is required`);
        continue;
      }
      if (missing) continue;
      const t = (spec.type || "").toLowerCase();
      if ((t === "int" || t === "float") && typeof v === "number") {
        if (spec.min !== undefined && v < spec.min) errors.push(`${spec.label || key} must be ≥ ${spec.min}`);
        if (spec.max !== undefined && v > spec.max) errors.push(`${spec.label || key} must be ≤ ${spec.max}`);
      }
    }
    if (host.module && typeof host.module.validate === "function") {
      try {
        const msg = host.module.validate();
        if (msg) errors.push(String(msg));
      } catch (err) { console.error("params validate() threw:", err); }
    }
    return errors;
  }
  container.querySelectorAll("[data-kw-key]").forEach(el => {
    const key = el.dataset.kwKey;
    const type = el.dataset.kwType;
    const spec = schema?.[key] || {};
    const optional = spec.optional || false;
    const field = el.closest(".kw-field") || el.closest(".kw-input-row");

    // Clear previous error
    el.classList.remove("input-error");
    if (field) field.querySelector(".kw-error")?.remove();

    let err = null;

    if (type === "file" || type === "bool" || type === "choice") {
      // no validation needed
    } else if (type === "int") {
      if (el.value !== "" && (isNaN(parseInt(el.value, 10)) || el.value.includes("."))) {
        err = "Must be a whole number";
      } else if (el.value !== "" && spec.min !== undefined && parseInt(el.value, 10) < spec.min) {
        err = `Min: ${spec.min}`;
      } else if (el.value !== "" && spec.max !== undefined && parseInt(el.value, 10) > spec.max) {
        err = `Max: ${spec.max}`;
      } else if (el.value === "" && !optional) {
        err = "Required";
      }
    } else if (type === "float") {
      if (el.value !== "" && isNaN(parseFloat(el.value))) {
        err = "Must be a number";
      } else if (el.value !== "" && spec.min !== undefined && parseFloat(el.value) < spec.min) {
        err = `Min: ${spec.min}`;
      } else if (el.value !== "" && spec.max !== undefined && parseFloat(el.value) > spec.max) {
        err = `Max: ${spec.max}`;
      } else if (el.value === "" && !optional) {
        err = "Required";
      }
    } else if (type === "textarea") {
      // try JSON parse if non-empty
      if (el.value.trim() !== "") {
        try { JSON.parse(el.value); } catch {
          // not JSON — that's fine, stored as string
        }
      }
    } else if (type === "str") {
      if (el.value === "" && !optional) {
        err = "Required";
      }
    }

    if (err) {
      errors.push({ key, message: err });
      el.classList.add("input-error");
      if (field) {
        const errEl = document.createElement("div");
        errEl.className = "kw-error";
        errEl.textContent = err;
        field.appendChild(errEl);
      }
    }
  });
  return errors;
}

/**
 * Load values from a YAML/JSON file into the kwargs form.
 * Only fills fields that exist in the form — everything else is ignored.
 * Returns a Promise that resolves when done.
 */
export function loadKwargsFromFile(container, toastFn) {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".yaml,.yml,.json";
    input.style.display = "none";
    document.body.appendChild(input);

    input.addEventListener("change", async () => {
      const file = input.files[0];
      input.remove();
      if (!file) { resolve(false); return; }
      try {
        const text = await file.text();
        let data;
        if (file.name.endsWith(".json")) {
          data = JSON.parse(text);
        } else {
          data = {};
          for (const line of text.split("\n")) {
            const m = line.match(/^\s*([a-zA-Z_]\w*)\s*:\s*(.+?)\s*$/);
            if (m) {
              let val = m[2].replace(/^["']|["']$/g, "");
              if (val === "true") val = true;
              else if (val === "false") val = false;
              else if (val !== "" && !isNaN(Number(val))) val = Number(val);
              data[m[1]] = val;
            }
          }
        }

        let filled = 0;
        container.querySelectorAll("[data-kw-key]").forEach(el => {
          const key = el.dataset.kwKey;
          if (!(key in data)) return;
          const val = data[key];
          const type = el.dataset.kwType;
          if (type === "bool") {
            el.checked = val === true || val === "true";
          } else if (type === "file") {
            // skip file fields — can't set from yaml
          } else {
            el.value = (val === null || val === undefined) ? "" : val;
          }
          filled++;
        });

        if (toastFn) toastFn(
          filled ? `Loaded ${filled} parameter${filled > 1 ? "s" : ""} from ${file.name}` : "No matching parameters found",
          filled ? "ok" : "warn"
        );
        resolve(true);
      } catch (err) {
        if (toastFn) toastFn(`Failed to parse ${file.name}: ${err.message}`, "bad");
        resolve(false);
      }
    });

    input.click();
  });
}

export function readKwargsForm(container) {
  // A project screen owns its own state; ask it for the values.
  const host = container._paramsHost;
  if (host) {
    try {
      const v = host.module && typeof host.module.value === "function"
        ? host.module.value()
        : readBoundFields(host.shadow);
      // Screen wins per key — a key it returns as {} or [] means the
      // operator emptied it, not "fall back to the default".
      return { ...(host.base || {}), ...((v && typeof v === "object") ? v : {}) };
    } catch (err) {
      console.error("params value() threw:", err);
      return {};
    }
  }
  const kwargs = {};
  container.querySelectorAll("[data-kw-key]").forEach(el => {
    const key = el.dataset.kwKey;
    const type = el.dataset.kwType;
    if (type === "file") {
      kwargs[key] = el.dataset.kwValue || null;
      return;
    } else if (type === "bool") {
      kwargs[key] = el.checked;
    } else if (type === "int") {
      kwargs[key] = el.value === "" ? null : parseInt(el.value, 10);
    } else if (type === "float") {
      kwargs[key] = el.value === "" ? null : parseFloat(el.value);
    } else if (type === "textarea") {
      if (el.value === "") { kwargs[key] = null; }
      else { try { kwargs[key] = JSON.parse(el.value); } catch { kwargs[key] = el.value; } }
    } else {
      kwargs[key] = el.value === "" ? null : el.value;
    }
  });
  return kwargs;
}

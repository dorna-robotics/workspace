// kwargs.js — Shared kwargs form renderer and reader.
// Used by dashboard.js and workspace.js.

const _resetSvg = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>`;
const _infoSvg  = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`;
const _lockSvg  = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>`;

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
  const keys = Object.keys(schema || {});
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
      container.appendChild(field);
      return;
    }

    // ── slots: tap the rack positions to process ────────────────────
    // Grid + slot names come from the SCENE (server-enriched spec), so
    // the operator sees the real rack. Value is a list of slot names.
    if (type === "slots" && Array.isArray(spec.slots) && spec.slots.length) {
      const exclude = new Set(spec.exclude || []);
      const chosen = new Set(Array.isArray(val) ? val
        : (Array.isArray(defaultVal) ? defaultVal : []));
      const wrap = document.createElement("div");
      wrap.className = "kw-slots";
      wrap.dataset.kwKey = key;
      wrap.dataset.kwType = "slots";
      const grid = document.createElement("div");
      grid.className = "kw-slot-grid";
      grid.style.gridTemplateColumns = `repeat(${(spec.cols || []).length || 5}, 56px)`;
      const cells = [];
      const sync = () => {
        // run order = the component's slot order, so the numbers match
        // the sequence the protocol will actually process them in
        const order = spec.slots.filter(n => chosen.has(n));
        cells.forEach(({ el, name, num }) => {
          const on = chosen.has(name);
          el.classList.toggle("on", on);
          if (num && !exclude.has(name)) num.textContent = on ? String(order.indexOf(name) + 1) : "";
        });
        wrap.dataset.kwValue = JSON.stringify([...chosen]);
        countEl.textContent = `${chosen.size} selected`;
      };
      spec.slots.forEach(name => {
        const cell = document.createElement("button");
        cell.type = "button";
        cell.className = "kw-slot" + (exclude.has(name) ? " excluded" : "");
        const idEl = document.createElement("span");
        idEl.className = "kw-slot-id";
        idEl.textContent = name;
        const numEl = document.createElement("span");
        numEl.className = "kw-slot-num";
        numEl.textContent = exclude.has(name) ? (spec.exclude_label || "—") : "";
        cell.appendChild(idEl);
        cell.appendChild(numEl);
        if (exclude.has(name)) {
          cell.disabled = true;
          cell.title = spec.exclude_hint || "Not available for processing";
        } else if (!frozen) {
          cell.addEventListener("click", () => {
            chosen.has(name) ? chosen.delete(name) : chosen.add(name);
            sync();
          });
        } else {
          cell.disabled = true;
        }
        cells.push({ el: cell, name, num: numEl });
        grid.appendChild(cell);
      });
      wrap.appendChild(grid);
      const legend = document.createElement("div");
      legend.className = "kw-slot-legend";
      legend.innerHTML =
        '<span><i class="on"></i>Selected — will be processed</span>' +
        '<span><i></i>Not selected</span>' +
        (exclude.size ? '<span><i class="excluded"></i>' +
          (spec.exclude_hint || "Not available") + '</span>' : "");
      wrap.appendChild(legend);
      const bar = document.createElement("div");
      bar.className = "kw-slot-bar";
      const countEl = document.createElement("span");
      countEl.className = "kw-slot-count";
      bar.appendChild(countEl);
      if (!frozen) {
        const mk = (label, fn) => {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "kw-slot-quick";
          b.textContent = label;
          b.addEventListener("click", () => { fn(); sync(); });
          bar.appendChild(b);
        };
        mk("All", () => spec.slots.forEach(n => { if (!exclude.has(n)) chosen.add(n); }));
        mk("Clear", () => chosen.clear());
        mk("First row", () => {
          chosen.clear();
          const n = (spec.cols || []).length || 5;
          spec.slots.slice(0, n).forEach(s => { if (!exclude.has(s)) chosen.add(s); });
        });
      }
      wrap.appendChild(bar);
      inputRow.appendChild(wrap);
      sync();
      field.appendChild(inputRow);
      if (hint) {
        const h = document.createElement("div");
        h.className = "kw-hint";
        h.textContent = hint;
        field.appendChild(h);
      }
      container.appendChild(field);
      return;
    }

    let input;
    if (type === "bool") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = val === true || val === "true";
      input.dataset.kwKey = key;
      input.dataset.kwType = "bool";
      input.className = "kw-checkbox";
      if (frozen) input.disabled = true;
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

    container.appendChild(field);
  });
}

export function validateKwargsForm(container, schema) {
  const errors = [];
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
    } else if (type === "slots") {
      let picked = [];
      try { picked = JSON.parse(el.dataset.kwValue || "[]"); } catch {}
      if (!picked.length && !optional) err = "Select at least one position";
      else if (spec.max !== undefined && picked.length > spec.max) err = `Max: ${spec.max}`;
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
  const kwargs = {};
  container.querySelectorAll("[data-kw-key]").forEach(el => {
    const key = el.dataset.kwKey;
    const type = el.dataset.kwType;
    if (type === "file") {
      kwargs[key] = el.dataset.kwValue || null;
      return;
    } else if (type === "slots") {
      try { kwargs[key] = JSON.parse(el.dataset.kwValue || "[]"); }
      catch { kwargs[key] = []; }
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

// kwargs.js — Shared kwargs form renderer and reader.
// Used by dashboard.js and workspace.js.

const _resetSvg = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>`;
const _infoSvg  = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`;
const _lockSvg  = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>`;

export function renderKwargsForm(container, schema, values, frozen = false, wsName = "") {
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
      fileWrap.style.cssText = "display:flex;align-items:center;gap:8px;flex:1";

      const fileLabel = document.createElement("span");
      fileLabel.className = "kw-hint";
      fileLabel.style.cssText = "flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-style:normal";
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
            const resp = await fetch(`/workspace/${encodeURIComponent(wsName)}/upload/${encodeURIComponent(key)}`, {
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

    let input;
    if (type === "bool") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = val === true || val === "true";
      input.dataset.kwKey = key;
      input.dataset.kwType = "bool";
      input.style.cssText = "width:auto";
      if (frozen) input.disabled = true;
    } else if (type === "textarea") {
      input = document.createElement("textarea");
      input.className = "input";
      input.rows = spec.rows || 4;
      input.value = (val === null || val === undefined) ? "" : (typeof val === "string" ? val : JSON.stringify(val, null, 2));
      input.placeholder = spec.placeholder || (optional ? "empty = null" : "");
      input.dataset.kwKey = key;
      input.dataset.kwType = "textarea";
      input.style.cssText = "resize:vertical;font-family:var(--mono);font-size:11px";
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

export function readKwargsForm(container) {
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

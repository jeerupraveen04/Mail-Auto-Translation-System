/* LMS Studio — Multilingual Course Mail Auto-Translation frontend
 * Phase 6: debounced auto-sync, placeholder chips, 38-language grid with
 * engine badges + RTL handling, student email simulator, manual override
 * locks. Data flows through POST /api/courses/{id}/templates/auto-translate.
 */

const DEBOUNCE_MS = 1000; // plan: 1,000–1,200ms debounce

const RTL_LOCALES = new Set(["ar", "he"]);
const RTL_LANG_NAMES = { ar: "Arabic", he: "Hebrew" };

const SAMPLE_DATA = {
  student_name: "Aarav Sharma",
  course_title: "Foundations of Data Science",
  start_date: "November 3, 2026",
  portal_url: "https://learn.example.com/courses/fds-2026",
  instructor_name: "Dr. Elena Petrova",
};

const state = {
  locales: [], // routing matrix from /api/languages
  translations: {}, // locale -> {subject, body, engine, ...}
  courseId: document.getElementById("course-id").value,
  templateKey: document.getElementById("template-key").value,
  debounceTimer: null,
  lastSyncedEnglish: { subject: "", body: "" },
  langMap: {}, // language name -> target code for Gemini prompt parity
};

// ---------- API helpers ----------

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
  return resp.json();
}

// ---------- Locale grid ----------

function badgeFor(engine) {
  if (!engine) return "";
  const cls = { DeepL: "deepl", AWS: "aws", Gemini: "gemini" }[engine] || "error";
  return `<span class="badge ${cls}">${engine}</span>`;
}

function renderGrid() {
  const grid = document.getElementById("locale-grid");
  grid.innerHTML = state.locales
    .map((loc) => {
      const t = state.translations[loc.code] || {};
      const rtl = loc.rtl;
      const dir = rtl ? 'dir="rtl"' : "";
      const align = rtl ? "text-align:right;" : "";
      const locked = t.manual_lock;
      const review = t.manual_review;
      const classes = ["locale-card"];
      if (locked) classes.push("locked");
      if (review) classes.push("review-needed");
      const subject = t.subject ?? "";
      const body = t.body ?? "";
      const badges = [
        badgeFor(t.engine),
        review ? '<span class="badge error">needs review</span>' : "",
        locked ? '<span class="badge lock">🔒 Manual</span>' : "",
        locked ? "" : '<span class="badge synced">In Sync</span>',
        t.cached ? '<span class="badge cache">cached</span>' : "",
      ].join("");
      return `
        <div class="${classes.join(" ")}" data-code="${loc.code}">
          <div class="locale-head">
            <span class="locale-name">${loc.name}
              ${rtl ? `<span class="rtl-mark" dir="rtl">RTL</span>` : ""}
            </span>
            <span class="badges">
              ${t.engine ? badges : `<span class="badge error">not translated</span>`}
            </span>
          </div>
          <input class="translation-input subject" ${dir} style="${align}" value="${escapeAttr(subject)}"
                 placeholder="${escapeAttr(`Subject (${loc.name})`)}" data-code="${loc.code}" data-field="subject">
          <textarea class="translation-input body" ${dir} style="${align}" rows="3"
                 placeholder="${escapeAttr(`Body (${loc.name})`)}" data-code="${loc.code}" data-field="body">${escapeHtml(body)}</textarea>
          <div class="locale-actions">
            <button class="btn ghost small act-lock" data-code="${loc.code}">
              ${locked ? "🔓 Reset lock" : "🔒 Lock edit"}
            </button>
          </div>
        </div>`;
    })
    .join("");
}

function escapeHtml(s) {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function escapeAttr(s) {
  return escapeHtml(s).replace(/"/g, "&quot;");
}

// ---------- Auto-translate flow ----------

function currentEnglish() {
  return {
    subject: document.getElementById("subject").value,
    body: document.getElementById("body").value,
  };
}

function setSync(status, text) {
  const el = document.getElementById("sync-indicator");
  el.className = `sync ${status}`;
  const labels = {
    idle: "● Idle",
    pending: "● Waiting for typing to pause…",
    syncing: "● Translating…",
    ok: "● In Sync",
    error: "● Translation error",
  };
  el.textContent = labels[status] || status;
  document.getElementById("timing-info").textContent = text || "";
}

function scheduleAutoSync() {
  clearTimeout(state.debounceTimer);
  setSync("pending");
  state.debounceTimer = setTimeout(() => runAutoTranslate(false), DEBOUNCE_MS);
}

async function runAutoTranslate(force) {
  const { subject, body } = currentEnglish();
  if (!subject.trim() && !body.trim()) return;
  setSync("syncing");
  const t0 = performance.now();
  try {
    const data = await api(
      `/api/courses/${encodeURIComponent(state.courseId)}/templates/auto-translate`,
      {
        method: "POST",
        body: JSON.stringify({
          subject,
          body,
          template_key: state.templateKey,
          force_refresh: !!force,
        }),
      }
    );
    state.translations = data.translations;
    state.lastSyncedEnglish = { subject, body };
    renderGrid();
    const engines = Object.values(data.translations).reduce((acc, t) => {
      acc[t.engine || "error"] = (acc[t.engine || "error"] || 0) + 1;
      return acc;
    }, {});
    const summary = Object.entries(engines)
      .map(([k, v]) => `${v} ${k}`)
      .join(" · ");
    setSync("ok", `${summary} · ${Math.round(performance.now() - t0)} ms`);
  } catch (err) {
    console.error(err);
    setSync("error", String(err));
  }
}

// ---------- Manual override ----------

async function manualSave(code, field, value) {
  const t = state.translations[code] || { subject: "", body: "" };
  const next = { ...t, [field]: value };
  const data = await api(
    `/api/courses/${encodeURIComponent(state.courseId)}/templates/${encodeURIComponent(state.templateKey)}/translations/${encodeURIComponent(code)}`,
    {
      method: "PATCH",
      body: JSON.stringify({ subject: next.subject, body: next.body }),
    }
  );
  state.translations = data.translations;
  renderGrid();
}

async function toggleLock(code) {
  const t = state.translations[code] || {};
  if (t.manual_lock) {
    const data = await api(
      `/api/courses/${encodeURIComponent(state.courseId)}/templates/${encodeURIComponent(state.templateKey)}/translations/${encodeURIComponent(code)}/lock`,
      { method: "DELETE" }
    );
    Object.assign(state.translations, data.translations);
    // Resync this locale against current English by calling auto-translate for it.
    await runAutoTranslateSubset([code]);
  } else {
    await manualSaveSilent(code);
  }
}

async function manualSaveSilent(code) {
  const input = document.querySelector(`.locale-card[data-code="${code}"]`);
  if (!input) return;
  const subjectEl = input.querySelector('[data-field="subject"]');
  const bodyEl = input.querySelector('[data-field="body"]');
  const data = await api(
    `/api/courses/${encodeURIComponent(state.courseId)}/templates/${encodeURIComponent(state.templateKey)}/translations/${encodeURIComponent(code)}`,
    {
      method: "PATCH",
      body: JSON.stringify({ subject: subjectEl.value, body: bodyEl.value }),
    }
  );
  state.translations = data.translations;
  renderGrid();
}

async function runAutoTranslateSubset(codes) {
  const { subject, body } = currentEnglish();
  const data = await api(
    `/api/courses/${encodeURIComponent(state.courseId)}/templates/auto-translate`,
    {
      method: "POST",
      body: JSON.stringify({
        subject,
        body,
        template_key: state.templateKey,
        target_codes: codes,
        force_refresh: true,
      }),
    }
  );
  Object.assign(state.translations, data.translations);
  renderGrid();
}

// ---------- Student Email Simulator ----------

function renderSimulatorTable() {
  document.getElementById("sim-data-table").innerHTML =
    "<tr><th>Placeholder</th><th>Sample value</th></tr>" +
    Object.entries(SAMPLE_DATA)
      .map(([k, v]) => `<tr><td class="mono">{{${k}}}</td><td>${escapeHtml(v)}</td></tr>`)
      .join("");
}

function injectSample(text) {
  return (text || "").replace(/\{\{([a-zA-Z0-9_]+)\}\}/g, (m, name) => SAMPLE_DATA[name] ?? m);
}

function openSimulator() {
  renderSimulatorTable();
  const sel = document.getElementById("sim-language");
  const current = sel.dataset.value || "en";
  sel.innerHTML = `<option value="en">English (source)</option>` +
    state.locales
      .map((l) => `<option value="${l.code}" ${l.code === current ? "selected" : ""}>${l.name}${l.rtl ? " (RTL)" : ""}</option>`)
      .join("");
  renderPreview();
  document.getElementById("simulator-modal").classList.remove("hidden");
}

function renderPreview() {
  const code = document.getElementById("sim-language").value || "en";
  document.getElementById("sim-language").dataset.value = code;
  const isEnglish = code === "en";
  const t = state.translations[code] || {};
  const subject = injectSample(isEnglish ? document.getElementById("subject").value : t.subject);
  const body = injectSample(isEnglish ? document.getElementById("body").value : t.body);
  const rtl = !isEnglish && RTL_LOCALES.has(code);
  const emailBox = document.querySelector(".sim-email");
  emailBox.classList.toggle("rtl", rtl);
  if (rtl) emailBox.setAttribute("dir", "rtl");
  else emailBox.removeAttribute("dir");
  document.getElementById("sim-subject").textContent = subject || "(no subject)";
  document.getElementById("sim-body").textContent = body || "(empty)";
  document.getElementById("sim-date").textContent = new Date().toLocaleString();
}

// ---------- Event wiring ----------

function attachHandlers() {
  const subjectEl = document.getElementById("subject");
  const bodyEl = document.getElementById("body");

  subjectEl.addEventListener("input", scheduleAutoSync);
  bodyEl.addEventListener("input", scheduleAutoSync);

  document.getElementById("btn-translate-all").addEventListener("click", () => runAutoTranslate(true));

  document.getElementById("course-id").addEventListener("change", (e) => {
    state.courseId = e.target.value;
  });
  document.getElementById("template-key").addEventListener("change", (e) => {
    state.templateKey = e.target.value;
  });

  // Placeholder chips: insert at caret in the source fields.
  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const token = `{{${chip.dataset.variable}}}`;
      const field = document.activeElement === bodyEl ? bodyEl : subjectEl;
      const { selectionStart: start, selectionEnd: end } = field;
      field.value = field.value.slice(0, start) + token + field.value.slice(end);
      const caret = start + token.length;
      field.focus();
      field.setSelectionRange(caret, caret);
      scheduleAutoSync();
    });
  });

  // Manual edits per locale card (input = auto manual-save + lock).
  document.getElementById("locale-grid").addEventListener("input", (e) => {
    const input = e.target.closest(".translation-input");
    if (!input) return;
    const code = input.dataset.code;
    const field = input.dataset.field;
    state.translations[code] = state.translations[code] || {};
    state.translations[code][field] = input.value;
    clearTimeout(input.dataset.timer);
    input.dataset.timer = setTimeout(async () => {
      try {
        await manualSave(code, field, input.value);
      } catch (err) {
        console.error("Manual override failed", err);
        setSync("error", String(err));
      }
    }, 600);
  });

  document.getElementById("locale-grid").addEventListener("click", (e) => {
    const btn = e.target.closest(".act-lock");
    if (!btn) return;
    toggleLock(btn.dataset.code).catch((err) => {
      console.error(err);
      setSync("error", String(err));
    });
  });

  // Simulator
  document.getElementById("btn-simulate").addEventListener("click", openSimulator);
  document.getElementById("sim-close").addEventListener("click", () =>
    document.getElementById("simulator-modal").classList.add("hidden")
  );
  document.getElementById("simulator-modal").addEventListener("click", (e) => {
    if (e.target.id === "simulator-modal")
      document.getElementById("simulator-modal").classList.add("hidden");
  });
  document.getElementById("sim-language").addEventListener("change", renderPreview);
}

// ---------- Boot ----------

async function boot() {
  try {
    const data = await api("/api/languages");
    state.locales = data.locales;
    if (!state.locales.length) throw new Error("no locales");
    renderGrid();
    attachHandlers();
    // Auto-translate the empty template's default demo text once at load.
    const subjectEl = document.getElementById("subject");
    const bodyEl = document.getElementById("body");
    if (!subjectEl.value) {
      subjectEl.value = "Welcome {{student_name}} to {{course_title}}!";
      bodyEl.value =
        "Dear {{student_name}},\n\nYour course {{course_title}} starts on {{start_date}}.\nVisit {{portal_url}} to meet {{instructor_name}} before the first session.\n\n— The LMS Team";
      runAutoTranslate(false);
    }
  } catch (err) {
    setSync("error", "Failed to load languages: " + err);
  }
}

boot();

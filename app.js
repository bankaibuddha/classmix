"use strict";

/* ---------- storage ---------- */
const KEY = "classmix.v1";

function load() {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* corrupted -> start fresh */ }
  return { classes: [], selected: [], mode: "count", value: 2, spread: false };
}
function save() { localStorage.setItem(KEY, JSON.stringify(state)); }

const state = load();
const absent = new Set(); // per-session only, resets on reload

const uid = () => crypto.randomUUID();

/* ---------- secure shuffle (Fisher-Yates, crypto RNG) ---------- */
function shuffle(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const buf = new Uint32Array(1);
    crypto.getRandomValues(buf);
    const j = buf[0] % (i + 1);
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/* ---------- dom helpers ---------- */
const $ = (id) => document.getElementById(id);
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

/* ---------- tabs ---------- */
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => {
      b.classList.toggle("active", b === btn);
      b.setAttribute("aria-selected", b === btn ? "true" : "false");
    });
    for (const name of ["teams", "draw", "classes"]) {
      $(`view-${name}`).classList.toggle("hidden", btn.dataset.tab !== name);
    }
    render();
  });
});

/* ---------- teams view ---------- */
function selectedPool() {
  const pool = [];
  for (const c of state.classes) {
    if (!state.selected.includes(c.id)) continue;
    for (const s of c.students) {
      if (!absent.has(s.id)) pool.push({ ...s, classId: c.id });
    }
  }
  return pool;
}

function renderChips() {
  const row = $("class-chips");
  row.replaceChildren();
  if (state.classes.length === 0) {
    row.append(el("p", "hint", "No classes yet — add one in the Classes tab."));
    return;
  }
  for (const c of state.classes) {
    const chip = el("button", "chip", `${c.name} (${c.students.length})`);
    if (state.selected.includes(c.id)) chip.classList.add("selected");
    chip.addEventListener("click", () => {
      const i = state.selected.indexOf(c.id);
      if (i >= 0) state.selected.splice(i, 1);
      else state.selected.push(c.id);
      save();
      render();
    });
    row.append(chip);
  }
}

function renderAbsent() {
  const wrap = $("absent-list");
  wrap.replaceChildren();
  const pool = [];
  for (const c of state.classes) {
    if (state.selected.includes(c.id)) pool.push(...c.students);
  }
  for (const s of pool) {
    const chip = el("button", "absent-chip", s.name);
    if (absent.has(s.id)) chip.classList.add("absent");
    chip.addEventListener("click", () => {
      absent.has(s.id) ? absent.delete(s.id) : absent.add(s.id);
      render();
    });
    wrap.append(chip);
  }
  $("absent-details").classList.toggle("hidden", pool.length === 0);
}

function renderPoolCount() {
  const n = selectedPool().length;
  $("pool-count").textContent = n === 0 ? "" : `${n} students in the mix`;
}

/* mode + stepper */
document.querySelectorAll(".seg").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.mode = btn.dataset.mode;
    save();
    document.querySelectorAll(".seg").forEach((b) =>
      b.classList.toggle("active", b.dataset.mode === state.mode));
  });
});
$("step-down").addEventListener("click", () => {
  state.value = Math.max(2, state.value - 1);
  save();
  $("step-value").textContent = state.value;
});
$("step-up").addEventListener("click", () => {
  state.value = Math.min(99, state.value + 1);
  save();
  $("step-value").textContent = state.value;
});
$("opt-spread").addEventListener("change", (e) => {
  state.spread = e.target.checked;
  save();
});

/* ---------- generate ---------- */
const TEAM_COLORS = [
  "#4f46e5", "#059669", "#d97706", "#dc2626", "#0891b2",
  "#7c3aed", "#db2777", "#65a30d", "#475569", "#b45309",
];

function generate() {
  const pool = selectedPool();
  if (pool.length < 2) {
    alert("Select classes with at least 2 present students first.");
    return;
  }
  const teamCount = state.mode === "count"
    ? Math.min(state.value, pool.length)
    : Math.max(1, Math.ceil(pool.length / state.value));

  const teams = Array.from({ length: teamCount }, () => []);
  let deal = 0;

  if (state.spread) {
    // shuffle within each class, deal class-by-class so every team
    // gets an even slice of each class
    const byClass = new Map();
    for (const s of pool) {
      if (!byClass.has(s.classId)) byClass.set(s.classId, []);
      byClass.get(s.classId).push(s);
    }
    for (const group of byClass.values()) {
      for (const s of shuffle(group)) teams[deal++ % teamCount].push(s);
    }
  } else {
    for (const s of shuffle(pool)) teams[deal++ % teamCount].push(s);
  }

  renderTeams(teams);
}

function renderTeams(teams) {
  const grid = $("team-cards");
  grid.replaceChildren();
  teams.forEach((members, i) => {
    const card = el("div", "team-card");
    card.style.background = TEAM_COLORS[i % TEAM_COLORS.length];
    card.append(el("h3", null, `Team ${i + 1} · ${members.length}`));
    const ul = el("ul");
    for (const m of members) ul.append(el("li", null, m.name));
    card.append(ul);
    grid.append(card);
  });
  $("results").classList.remove("hidden");
  $("results").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

$("btn-generate").addEventListener("click", generate);

/* ---------- shake to reshuffle (iOS needs a permission tap) ---------- */
let lastShake = 0;
function onMotion(e) {
  const a = e.accelerationIncludingGravity;
  if (!a) return;
  const force = Math.abs(a.x) + Math.abs(a.y) + Math.abs(a.z);
  const now = Date.now();
  if (force > 40 && now - lastShake > 1200) {
    lastShake = now;
    if (!$("results").classList.contains("hidden")) generate();
  }
}
if (typeof DeviceMotionEvent !== "undefined") {
  if (typeof DeviceMotionEvent.requestPermission === "function") {
    const btn = $("btn-shake-enable");
    btn.classList.remove("hidden");
    btn.addEventListener("click", async () => {
      const res = await DeviceMotionEvent.requestPermission().catch(() => "denied");
      if (res === "granted") {
        window.addEventListener("devicemotion", onMotion);
        btn.classList.add("hidden");
      }
    });
  } else {
    window.addEventListener("devicemotion", onMotion);
  }
}

/* ---------- classes view ---------- */
let editingId = null;

function renderClassList() {
  const list = $("class-list");
  list.replaceChildren();
  if (state.classes.length === 0) {
    list.append(el("p", "hint", "No classes yet. Tap “New Class” to start."));
  }
  for (const c of state.classes) {
    const row = el("div", "class-row");
    const left = el("div");
    left.append(el("div", null, c.name));
    left.append(el("div", "meta", `${c.students.length} students`));
    row.append(left, el("span", "meta", "›"));
    row.addEventListener("click", () => openEditor(c.id));
    list.append(row);
  }
}

$("btn-add-class").addEventListener("click", () => {
  const c = { id: uid(), name: `Class ${state.classes.length + 1}`, students: [] };
  state.classes.push(c);
  save();
  openEditor(c.id);
});

function openEditor(id) {
  editingId = id;
  const c = state.classes.find((x) => x.id === id);
  $("editor-name").value = c.name;
  renderStudents();
  $("editor").classList.remove("hidden");
  $("student-input").focus();
}

function renderStudents() {
  const c = state.classes.find((x) => x.id === editingId);
  const ul = $("student-list");
  ul.replaceChildren();
  for (const s of c.students) {
    const li = el("li");
    li.append(el("span", null, s.name));
    const rm = el("button", "remove-btn", "✕");
    rm.addEventListener("click", () => {
      c.students = c.students.filter((x) => x.id !== s.id);
      save();
      renderStudents();
    });
    li.append(rm);
    ul.append(li);
  }
}

function addStudents(text) {
  const c = state.classes.find((x) => x.id === editingId);
  // paste-a-list support: split on newlines, commas, semicolons
  const names = text.split(/[\n,;]+/).map((s) => s.trim()).filter(Boolean);
  for (const name of names) c.students.push({ id: uid(), name });
  if (names.length) {
    save();
    renderStudents();
  }
}

$("btn-add-student").addEventListener("click", () => {
  addStudents($("student-input").value);
  $("student-input").value = "";
  $("student-input").focus();
});
$("student-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    addStudents($("student-input").value);
    $("student-input").value = "";
  }
});
$("editor-name").addEventListener("input", (e) => {
  const c = state.classes.find((x) => x.id === editingId);
  c.name = e.target.value;
  save();
});
$("editor-close").addEventListener("click", () => {
  $("editor").classList.add("hidden");
  editingId = null;
  render();
});
$("btn-delete-class").addEventListener("click", () => {
  const c = state.classes.find((x) => x.id === editingId);
  if (!confirm(`Delete “${c.name}” and its ${c.students.length} students?`)) return;
  state.classes = state.classes.filter((x) => x.id !== editingId);
  state.selected = state.selected.filter((id) => id !== editingId);
  save();
  $("editor").classList.add("hidden");
  editingId = null;
  render();
});

/* ---------- backup ---------- */
$("btn-export").addEventListener("click", () => {
  const blob = new Blob([JSON.stringify({ classes: state.classes }, null, 2)],
    { type: "application/json" });
  const a = el("a");
  a.href = URL.createObjectURL(blob);
  a.download = "classmix-backup.json";
  a.click();
  URL.revokeObjectURL(a.href);
});
$("btn-import").addEventListener("click", () => $("import-file").click());
$("import-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    if (!Array.isArray(data.classes)) throw new Error("bad format");
    for (const c of data.classes) {
      if (typeof c.name !== "string" || !Array.isArray(c.students)) throw new Error("bad format");
      state.classes.push({
        id: uid(),
        name: c.name,
        students: c.students
          .filter((s) => typeof s.name === "string")
          .map((s) => ({ id: uid(), name: s.name })),
      });
    }
    save();
    render();
  } catch {
    alert("That file doesn't look like a ClassMix backup.");
  }
  e.target.value = "";
});

/* ---------- initial render ---------- */
function render() {
  renderChips();
  renderAbsent();
  renderPoolCount();
  renderClassList();
  if (typeof renderDraw === "function") renderDraw();
  $("step-value").textContent = state.value;
  $("opt-spread").checked = state.spread;
  document.querySelectorAll(".seg").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === state.mode));
}
render();

/* ---------- PWA ---------- */
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}

"use strict";

/* =========================================================================
   CLUB DRAW — selecting a few out of many, and being able to prove it was fair.
   Loaded after app.js; shares its $ / el / state / save helpers.
   ========================================================================= */

// club-draw settings, merged so a state saved before this feature still loads
state.draw = Object.assign({
  selected: [], spots: 10, seedOn: false, seed: "",
  tiersOn: false, tiers: {}, spreadOn: false, waitlist: true,
}, state.draw || {});

/* Seeded PRNG. Deliberately NOT crypto.getRandomValues: an auditable draw has
   to be REPRODUCIBLE, so the same seed and the same roster give the same
   result on anyone's device. With the seed toggle off we use crypto
   randomness instead — unpredictable, but impossible to re-verify afterwards. */
function xmur3(str) {
  let h = 1779033703 ^ str.length;
  for (let i = 0; i < str.length; i++) {
    h = Math.imul(h ^ str.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  return () => {
    h = Math.imul(h ^ (h >>> 16), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    return (h ^= h >>> 16) >>> 0;
  };
}
function mulberry32(a) {
  return () => {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function cryptoFloat() {
  const b = new Uint32Array(1);
  crypto.getRandomValues(b);
  return b[0] / 4294967296;
}

function drawPool() {
  const pool = [];
  for (const c of state.classes) {
    if (!state.draw.selected.includes(c.id)) continue;
    const tier = state.draw.tiersOn ? (state.draw.tiers[c.id] || 1) : 1;
    for (const s of c.students) {
      pool.push({ id: s.id, name: s.name, classId: c.id, className: c.name, tier });
    }
  }
  return pool;
}

/* The roster fingerprint is the COMMIT half of an auditable draw: it pins down
   WHO was in the hat. Sorted, so it describes the set rather than the order
   names happen to sit in. Publish it BEFORE taking a seed from outside —
   whoever knows both the roster and the seed in advance could otherwise hunt
   for a seed that favours particular people. */
async function rosterFingerprint(pool) {
  const canon = pool.map((p) => `${p.className} ${p.name} ${p.tier}`).sort().join("\n");
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canon));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 24);
}

/* Spread: no class may take more than its share of the spots. Runs over the
   already-drawn order, so it never re-rolls anyone — it only defers. If a
   class runs dry the cap relaxes rather than leaving spots unfilled. */
function spreadOrder(list, spots) {
  const classCount = new Set(list.map((p) => p.classId)).size;
  let cap = Math.ceil(spots / Math.max(1, classCount));
  const out = [], deferred = [], count = {};
  const take = (p) => { out.push(p); count[p.classId] = (count[p.classId] || 0) + 1; };
  for (const p of list) {
    if (out.length < spots && (count[p.classId] || 0) < cap) take(p);
    else deferred.push(p);
  }
  while (out.length < spots && deferred.length) {
    cap++;
    for (let i = 0; i < deferred.length && out.length < spots;) {
      if ((count[deferred[i].classId] || 0) < cap) take(deferred.splice(i, 1)[0]);
      else i++;
    }
  }
  return out.concat(deferred);
}

function computeDraw(pool) {
  const d = state.draw;
  const seeded = d.seedOn && d.seed.trim() !== "";
  const rand = seeded ? mulberry32(xmur3(d.seed.trim())()) : cryptoFloat;
  const keyed = pool.map((p) => ({ ...p, k: rand() }));
  // tier first (1 before 2), then the draw, then id — so a tie never depends
  // on the order students happened to be typed in
  keyed.sort((a, b) => (a.tier - b.tier) || (a.k - b.k) || (a.id < b.id ? -1 : 1));
  const spots = Math.min(d.spots, keyed.length);
  const ordered = d.spreadOn ? spreadOrder(keyed, spots) : keyed;
  return { picked: ordered.slice(0, spots), waiting: ordered.slice(spots), seeded };
}

/* ---- UI ---- */
function renderDrawChips() {
  const row = $("draw-chips");
  row.replaceChildren();
  if (state.classes.length === 0) {
    row.append(el("p", "hint", "No classes yet — add applicants in the Classes tab."));
    return;
  }
  for (const c of state.classes) {
    const chip = el("button", "chip", `${c.name} (${c.students.length})`);
    if (state.draw.selected.includes(c.id)) chip.classList.add("selected");
    chip.addEventListener("click", () => {
      const i = state.draw.selected.indexOf(c.id);
      if (i >= 0) state.draw.selected.splice(i, 1);
      else state.draw.selected.push(c.id);
      save();
      renderDraw();
    });
    row.append(chip);
  }
}

function renderTierList() {
  const box = $("tier-list");
  box.replaceChildren();
  for (const c of state.classes) {
    if (!state.draw.selected.includes(c.id)) continue;
    const row = el("div", "tier-row");
    row.append(el("span", null, c.name));
    const ctl = el("div", "tier-ctl");
    const down = el("button", "tier-btn", "−");
    const num = el("span", "tier-num", String(state.draw.tiers[c.id] || 1));
    const up = el("button", "tier-btn", "+");
    down.addEventListener("click", () => {
      state.draw.tiers[c.id] = Math.max(1, (state.draw.tiers[c.id] || 1) - 1);
      save(); renderTierList();
    });
    up.addEventListener("click", () => {
      state.draw.tiers[c.id] = Math.min(9, (state.draw.tiers[c.id] || 1) + 1);
      save(); renderTierList();
    });
    ctl.append(down, num, up);
    row.append(ctl);
    box.append(row);
  }
}

async function renderDraw() {
  const d = state.draw;
  renderDrawChips();
  const pool = drawPool();
  $("draw-count").textContent = pool.length === 0 ? "" : `${pool.length} applicants`;
  $("spots-value").textContent = d.spots;
  $("spots-note").textContent = pool.length === 0 ? ""
    : pool.length <= d.spots
      ? `Only ${pool.length} applied — everyone gets in, no draw needed.`
      : `${pool.length - d.spots} will not get a spot.`;

  $("opt-seed").checked = d.seedOn;
  $("opt-tiers").checked = d.tiersOn;
  $("opt-draw-spread").checked = d.spreadOn;
  $("opt-waitlist").checked = d.waitlist;
  $("seed-box").classList.toggle("hidden", !d.seedOn);
  $("tiers-box").classList.toggle("hidden", !d.tiersOn);
  $("seed-input").value = d.seed;
  if (d.tiersOn) renderTierList();

  if (d.seedOn && pool.length) {
    $("roster-fp").textContent = `roster fingerprint  ${await rosterFingerprint(pool)}`;
  } else {
    $("roster-fp").textContent = "";
  }
}

function renderDrawResult(res, pool) {
  const list = (items, offset) => {
    const ul = el("ul", "pick-list");
    items.forEach((p, i) => {
      const li = el("li");
      li.append(el("span", "rank", `${i + 1 + offset}.`));
      li.append(el("span", null, p.name));
      li.append(el("span", "from", p.className));
      ul.append(li);
    });
    return ul;
  };

  const r = $("draw-receipt");
  r.replaceChildren();
  const rules = [res.seeded ? "seeded" : "unseeded (not reproducible)"];
  if (state.draw.tiersOn) rules.push("tiers");
  if (state.draw.spreadOn) rules.push("class spread");
  const line1 = el("div");
  line1.append(el("strong", null, `${res.picked.length} of ${pool.length} selected`));
  line1.append(document.createTextNode(` · ${rules.join(" · ")}`));
  r.append(line1);
  if (res.seeded) {
    const s = el("div");
    s.append(document.createTextNode("seed "));
    s.append(el("code", null, state.draw.seed.trim()));
    r.append(s);
    r.append(el("div", null, $("roster-fp").textContent));
    r.append(el("div", "hint", "Same seed + same roster reproduces this exact result."));
  }

  $("draw-picked").replaceChildren(el("div", "list-head in", "Selected"), list(res.picked, 0));

  const wl = $("draw-waitlist");
  wl.replaceChildren();
  if (state.draw.waitlist && res.waiting.length) {
    wl.append(el("div", "list-head", "Waitlist (in order)"), list(res.waiting, res.picked.length));
  }
  $("draw-results").classList.remove("hidden");
  $("draw-results").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

let lastDraw = null;

$("btn-draw").addEventListener("click", async () => {
  const pool = drawPool();
  if (pool.length === 0) { alert("Select at least one class of applicants first."); return; }
  if (state.draw.seedOn && state.draw.seed.trim() === "") {
    alert("Enter the seed you announced, or switch off the auditable draw.");
    return;
  }
  const res = computeDraw(pool);
  lastDraw = { res, pool };
  await renderDraw();
  renderDrawResult(res, pool);
});

$("spots-down").addEventListener("click", () => {
  state.draw.spots = Math.max(1, state.draw.spots - 1); save(); renderDraw();
});
$("spots-up").addEventListener("click", () => {
  state.draw.spots = Math.min(999, state.draw.spots + 1); save(); renderDraw();
});
for (const [id, key] of [["opt-seed", "seedOn"], ["opt-tiers", "tiersOn"],
                         ["opt-draw-spread", "spreadOn"], ["opt-waitlist", "waitlist"]]) {
  $(id).addEventListener("change", (e) => {
    state.draw[key] = e.target.checked; save(); renderDraw();
  });
}
$("seed-input").addEventListener("input", (e) => {
  state.draw.seed = e.target.value; save();
});

$("btn-copy-draw").addEventListener("click", async () => {
  if (!lastDraw) return;
  const { res, pool } = lastDraw;
  const lines = [`Club draw — ${res.picked.length} of ${pool.length} selected`];
  if (res.seeded) lines.push(`seed: ${state.draw.seed.trim()}`, $("roster-fp").textContent);
  lines.push("", "SELECTED");
  res.picked.forEach((p, i) => lines.push(`${i + 1}. ${p.name} (${p.className})`));
  if (state.draw.waitlist && res.waiting.length) {
    lines.push("", "WAITLIST");
    res.waiting.forEach((p, i) =>
      lines.push(`${i + 1 + res.picked.length}. ${p.name} (${p.className})`));
  }
  try {
    await navigator.clipboard.writeText(lines.join("\n"));
    $("btn-copy-draw").textContent = "Copied";
    setTimeout(() => ($("btn-copy-draw").textContent = "Copy"), 1400);
  } catch { alert("Copy failed — select the text manually."); }
});

renderDraw();

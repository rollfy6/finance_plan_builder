// Plan timeline: drag modules along a quarterly-scaled monthly timeline and edit their parameters.
// Vanilla ES module, no build step. The Python side validates the plan, so this
// file only makes edits and does not enforce the full schema.

const PX = 6; // pixels per month (a quarter is 3 * PX)
const LABEL_W = 240;

const ACCOUNT_TYPES = [
  "cash", "taxable", "traditional_401k", "roth_401k", "traditional_ira",
  "roth_ira", "hsa", "real_estate",
];
const EVENT_KINDS = [
  "one_time_income", "one_time_expense", "lump_sum_debt_payment",
  "lump_sum_asset_contribution", "asset_withdrawal",
];
const PCT_KEYS = new Set([
  "apr", "annual_return", "inflation", "cash_return", "annual_raise", "surplus_to_debt",
]);

// window: active from start to end. account: opens at start, persists.
// point: a single month. changes: term changes listed on the account.
const KINDS = {
  strategies: {
    title: "Debt strategies", window: true,
    fields: [
      { k: "label", t: "text" },
      { k: "debt_strategy", t: "select", options: ["avalanche", "snowball", "fixed"] },
      { k: "debt_priority", t: "list", opt: true },
      { k: "surplus_to_debt", t: "pct" },
    ],
  },
  assumptions: {
    title: "Assumptions", window: true,
    fields: [
      { k: "label", t: "text" },
      { k: "inflation", t: "pct", opt: true },
      { k: "cash_return", t: "pct", opt: true },
    ],
  },
  incomes: {
    title: "Incomes", window: true,
    fields: [
      { k: "label", t: "text" },
      { k: "owner", t: "text", opt: true },
      { k: "gross_annual", t: "money" },
      { k: "annual_raise", t: "pct" },
      { k: "pay_frequency", t: "select", options: ["monthly", "biweekly", "semimonthly"] },
    ],
  },
  expenses: {
    title: "Expenses", window: true,
    fields: [
      { k: "label", t: "text" },
      { k: "category", t: "text" },
      { k: "monthly", t: "money" },
      { k: "inflation_linked", t: "bool" },
    ],
  },
  debts: {
    title: "Debts", account: true, changes: ["apr", "minimum_payment"],
    fields: [
      { k: "label", t: "text" },
      { k: "owner", t: "text", opt: true },
      { k: "opening_balance", t: "money" },
      { k: "apr", t: "pct" },
      { k: "minimum_payment", t: "money" },
      { k: "convention", t: "select", options: ["monthly_apr", "daily_apr"] },
    ],
  },
  assets: {
    title: "Assets", account: true, changes: ["annual_return", "monthly_contribution"],
    fields: [
      { k: "label", t: "text" },
      { k: "owner", t: "text", opt: true },
      { k: "account_type", t: "select", options: ACCOUNT_TYPES },
      { k: "opening_balance", t: "money" },
      { k: "annual_return", t: "pct" },
      { k: "monthly_contribution", t: "money" },
    ],
  },
  events: {
    title: "Events", point: true,
    fields: [
      { k: "label", t: "text" },
      { k: "kind", t: "select", options: EVENT_KINDS },
      { k: "amount", t: "money" },
      { k: "target_id", t: "text", opt: true },
    ],
  },
};

// Editable type for each change field (used by the change editor).
const CHANGE_TYPES = {
  apr: "pct", minimum_payment: "money", annual_return: "pct", monthly_contribution: "money",
};

// --- month helpers ---------------------------------------------------------

function idx(m) {
  const [y, mo] = m.split("-").map(Number);
  return y * 12 + (mo - 1);
}

function fmt(i) {
  return `${Math.floor(i / 12)}-${String(((i % 12) + 12) % 12 + 1).padStart(2, "0")}`;
}

// Quarter starts are months with idx % 3 == 0 (Jan, Apr, Jul, Oct).
// Quarter ends are months with idx % 3 == 2 (Mar, Jun, Sep, Dec).
function snapStart(i) {
  return Math.round(i / 3) * 3;
}

function snapEnd(i) {
  return Math.round((i - 2) / 3) * 3 + 2;
}

function findItem(plan, kind, id) {
  return (plan[kind] || []).find((x) => x.id === id);
}

function viewRange(plan) {
  const last = plan.eras[plan.eras.length - 1];
  let hi = last.end ? idx(last.end) : idx(plan.start) + 480;
  for (const k of ["incomes", "expenses", "strategies", "assumptions"]) {
    for (const w of plan[k]) if (w.end) hi = Math.max(hi, idx(w.end));
  }
  for (const e of plan.events) hi = Math.max(hi, idx(e.month));
  for (const k of ["debts", "assets"]) {
    for (const a of plan[k]) for (const c of a.changes || []) hi = Math.max(hi, idx(c.month));
  }
  const lo = Math.floor(idx(plan.start) / 12) * 12;
  return { lo, hi: hi + 12 };
}

// Drag result for a module. Windows snap to whole quarters: start to a quarter
// start, end to a quarter end, and never shorter than one quarter. Moves keep the
// module's length. Point events and account openings snap to a quarter start.
function snapSpan(kind, mode, s0, e0, dm) {
  const cfg = KINDS[kind];
  if (cfg.point || cfg.account) return { s: snapStart(s0 + dm), en: null };
  if (mode === "move") {
    const s = snapStart(s0 + dm);
    if (e0 === null) return { s, en: null };
    return { s, en: Math.max(snapEnd(s + (e0 - s0)), s + 2) };
  }
  if (mode === "l") {
    let s = snapStart(s0 + dm);
    if (e0 !== null) s = Math.min(s, snapStart(e0 - 2));
    return { s, en: e0 };
  }
  // mode "r": move the end edge
  if (e0 === null) return { s: s0, en: null };
  return { s: s0, en: Math.max(snapEnd(e0 + dm), snapStart(s0) + 2) };
}

// --- value formatting --------------------------------------------------------

function money(v) {
  if (v === null || v === undefined) return "-";
  const a = Math.abs(v);
  if (a >= 1e6) return `$${(v / 1e6).toFixed(2).replace(/\.?0+$/, "")}M`;
  if (a >= 1000) return `$${(v / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return `$${Math.round(v)}`;
}

function pct(v) {
  if (v === null || v === undefined) return "-";
  return `${+(v * 100).toFixed(2)}%`;
}

function fieldValue(k, v) {
  return PCT_KEYS.has(k) ? pct(v) : money(v);
}

// Short value summary shown on a bar or marker.
function valueText(kind, it) {
  switch (kind) {
    case "incomes":
      return `${money(it.gross_annual)}/yr${it.annual_raise ? ` +${pct(it.annual_raise)}/yr` : ""}`;
    case "expenses":
      return `${money(it.monthly)}/mo`;
    case "debts":
      return `${money(it.opening_balance)} @ ${pct(it.apr)} · min ${money(it.minimum_payment)}/mo`;
    case "assets":
      return `${money(it.opening_balance)} @ ${pct(it.annual_return)}${
        it.monthly_contribution ? ` · +${money(it.monthly_contribution)}/mo` : ""}`;
    case "strategies":
      return `${it.debt_strategy} · ${pct(it.surplus_to_debt)} to debt`;
    case "assumptions":
      return [
        it.inflation != null ? `inflation ${pct(it.inflation)}` : null,
        it.cash_return != null ? `cash ${pct(it.cash_return)}` : null,
      ].filter(Boolean).join(" · ");
    case "events":
      return `${money(it.amount)} · ${it.kind.replace(/_/g, " ")}`;
    default:
      return "";
  }
}

function changeText(c) {
  return Object.entries(c)
    .filter(([k]) => k !== "month")
    .map(([k, v]) => `${k.replace(/_/g, " ")} ${fieldValue(k, v)}`)
    .join(", ");
}

// --- form conversions ----------------------------------------------------------

function parseField(field, raw) {
  if (field.t === "bool") return raw;
  if (field.t === "list") {
    return raw.split(",").map((s) => s.trim()).filter(Boolean);
  }
  const text = String(raw).trim();
  if (text === "") return field.opt ? null : "";
  if (field.t === "pct") return parseFloat((parseFloat(text) / 100).toFixed(8));
  if (field.t === "money" || field.t === "number") return parseFloat(text);
  return text;
}

function displayField(field, value) {
  if (value === null || value === undefined) return field.t === "bool" ? false : "";
  if (field.t === "bool") return Boolean(value);
  if (field.t === "list") return (value || []).join(", ");
  if (field.t === "pct") return String(Math.round(value * 1e6) / 1e4);
  return String(value);
}

// --- DOM helper ----------------------------------------------------------------

function h(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

// Bar content: module name, then its values.
function barContent(kind, item) {
  const wrap = h("span", "fpb-bar-text");
  wrap.append(h("span", "fpb-bar-name", item.label || item.id));
  const val = valueText(kind, item);
  if (val) wrap.append(h("span", "fpb-bar-val", val));
  return wrap;
}

// --- widget --------------------------------------------------------------------

export function render({ model, el }) {
  el.classList.add("fpb-timeline");
  const errorBox = h("div", "fpb-error");
  const body = h("div", "fpb-body");
  el.append(errorBox, body);

  const st = { plan: model.get("plan"), sel: null, localError: "" };

  const setPlan = (plan) => {
    st.plan = plan;
    model.set("plan", plan);
    model.save_changes();
    draw();
  };

  const select = (kind, id) => {
    st.sel = { kind, id };
    draw();
  };

  const draw = () => {
    body.replaceChildren(timelinePane(), panelPane());
    errorBox.textContent = st.localError || model.get("error") || "";
    errorBox.classList.toggle("on", Boolean(st.localError || model.get("error")));
  };

  model.on("change:plan", () => {
    st.plan = model.get("plan");
    st.localError = "";
    draw();
  });
  model.on("change:error", () => draw());

  // --- timeline ----------------------------------------------------------

  function timelinePane() {
    const plan = st.plan;
    const v = viewRange(plan);
    const width = (v.hi - v.lo) * PX;
    const pane = h("div", "fpb-pane");
    const grid = h("div", "fpb-grid");
    grid.style.width = `${LABEL_W + width}px`;
    // Quarter guide lines, one per quarter start, aligned with the track origin.
    grid.style.backgroundImage = "linear-gradient(to right, rgba(127,127,127,0.22) 1px, transparent 1px)";
    grid.style.backgroundSize = `${3 * PX}px 100%`;
    grid.style.backgroundRepeat = "repeat-x";
    grid.style.backgroundPosition = `${LABEL_W}px 0`;

    // Header: year row and quarter row
    const head = h("div", "fpb-row fpb-head");
    head.append(h("div", "fpb-label", "Quarter"));
    const headTrack = h("div", "fpb-track");
    headTrack.style.width = `${width}px`;
    for (let i = v.lo; i < v.hi; i += 3) {
      const q = h("div", "fpb-q", `Q${((i % 12) / 3) + 1}`);
      q.style.left = `${(i - v.lo) * PX}px`;
      q.style.width = `${3 * PX}px`;
      headTrack.append(q);
      if (i % 12 === 0) {
        const y = h("div", "fpb-year", String(Math.floor(i / 12)));
        y.style.left = `${(i - v.lo) * PX}px`;
        headTrack.append(y);
      }
    }
    head.append(headTrack);
    grid.append(head);

    // Eras: labels only, read-only
    grid.append(sectionRow("Eras (labels only)"));
    for (const era of plan.eras) {
      const row = h("div", "fpb-row");
      row.append(h("div", "fpb-label", era.label));
      const track = h("div", "fpb-track");
      track.style.width = `${width}px`;
      const band = h("div", "fpb-band", era.label);
      const end = era.end ? idx(era.end) : v.hi - 1;
      band.style.left = `${(idx(era.start) - v.lo) * PX}px`;
      band.style.width = `${(end - idx(era.start) + 1) * PX}px`;
      track.append(band);
      row.append(track);
      grid.append(row);
    }

    for (const kind of Object.keys(KINDS)) {
      const cfg = KINDS[kind];
      const items = plan[kind] || [];
      grid.append(sectionRow(cfg.title));
      if (items.length === 0) {
        grid.append(h("div", "fpb-empty", `No ${cfg.title.toLowerCase()}`));
        continue;
      }
      for (const item of items) grid.append(itemRow(kind, cfg, item, v, width));
    }

    pane.append(grid);
    return pane;
  }

  function sectionRow(title) {
    const row = h("div", "fpb-section");
    row.append(h("div", "fpb-label", title));
    return row;
  }

  function itemRow(kind, cfg, item, v, width) {
    const row = h("div", "fpb-row");
    const isSel = st.sel && st.sel.kind === kind && st.sel.id === item.id;
    const label = h("div", "fpb-label" + (isSel ? " sel" : ""), item.label || item.id);
    label.title = item.id;
    label.addEventListener("click", () => select(kind, item.id));
    row.append(label);

    const track = h("div", "fpb-track");
    track.style.width = `${width}px`;

    if (cfg.point) {
      const m = h("div", `fpb-marker ${kind}${isSel ? " sel" : ""}`);
      m.style.left = `${(idx(item.month) - v.lo) * PX - 4}px`;
      m.title = `${item.label} at ${item.month}: ${valueText(kind, item)}`;
      m.append(h("span", "fpb-marker-val", `${item.label} · ${valueText(kind, item)}`));
      m.addEventListener("pointerdown", (e) => startDrag(e, kind, item.id, "move", m, v));
      track.append(m);
    } else if (cfg.window) {
      const s = idx(item.start);
      const en = item.end ? idx(item.end) : v.hi - 1;
      const b = h("div", `fpb-bar ${kind}${item.end ? "" : " open"}${isSel ? " sel" : ""}`);
      b.append(barContent(kind, item));
      b.style.left = `${(s - v.lo) * PX}px`;
      b.style.width = `${(en - s + 1) * PX}px`;
      b.title = `${item.label}: ${item.start} to ${item.end || "open-ended"}\n${valueText(kind, item)}`;
      b.addEventListener("pointerdown", (e) => startDrag(e, kind, item.id, "move", b, v));
      const hl = h("div", "fpb-handle l");
      hl.addEventListener("pointerdown", (e) => startDrag(e, kind, item.id, "l", b, v));
      const hr = h("div", "fpb-handle r");
      hr.addEventListener("pointerdown", (e) => startDrag(e, kind, item.id, "r", b, v));
      b.append(hl, hr);
      track.append(b);
    } else if (cfg.account) {
      const s = idx(item.start);
      const b = h("div", `fpb-bar account ${kind}${isSel ? " sel" : ""}`);
      b.append(barContent(kind, item));
      b.style.left = `${(s - v.lo) * PX}px`;
      b.style.width = `${(v.hi - s) * PX}px`;
      b.title = `${item.label}: opens ${item.start}\n${valueText(kind, item)}`;
      b.addEventListener("pointerdown", (e) => startDrag(e, kind, item.id, "move", b, v));
      track.append(b);
      for (const c of item.changes || []) {
        const tick = h("div", "fpb-change");
        tick.style.left = `${(idx(c.month) - v.lo) * PX - 1}px`;
        tick.title = `change at ${c.month}: ${changeText(c)}`;
        track.append(tick);
      }
    }
    row.append(track);
    return row;
  }

  // Drag: move shifts the whole module, l/r move one edge. The bar snaps to quarter
  // boundaries while dragging and commits on release. A release where nothing
  // changed counts as a click and selects the module.
  function startDrag(e, kind, id, mode, elem, v) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const cfg = KINDS[kind];
    const item = findItem(st.plan, kind, id);
    const s0 = cfg.point ? idx(item.month) : idx(item.start);
    const e0 = cfg.window && item.end ? idx(item.end) : null;
    const x0 = e.clientX;
    let cur = { s: s0, en: e0 };

    const place = (r) => {
      if (cfg.point) {
        elem.style.left = `${(r.s - v.lo) * PX - 4}px`;
      } else if (cfg.window) {
        const endI = r.en === null ? v.hi - 1 : r.en;
        elem.style.left = `${(r.s - v.lo) * PX}px`;
        elem.style.width = `${(endI - r.s + 1) * PX}px`;
      } else {
        elem.style.left = `${(r.s - v.lo) * PX}px`;
      }
    };

    const onMove = (ev) => {
      const dm = Math.round((ev.clientX - x0) / PX);
      cur = snapSpan(kind, mode, s0, e0, dm);
      place(cur);
    };

    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      if (cur.s === s0 && cur.en === e0) {
        select(kind, id);
        return;
      }
      commitSpan(kind, id, cur);
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  function commitSpan(kind, id, r) {
    const cfg = KINDS[kind];
    const plan = structuredClone(st.plan);
    const item = findItem(plan, kind, id);
    if (cfg.point) {
      item.month = fmt(r.s);
    } else {
      item.start = fmt(r.s);
      if (r.en !== null && item.end) item.end = fmt(r.en);
    }
    st.sel = { kind, id };
    setPlan(plan);
  }

  // --- parameter panel -----------------------------------------------------

  function panelPane() {
    const panel = h("div", "fpb-panel");
    if (!st.sel) {
      panel.append(h("p", "fpb-hint",
        "Drag a bar to move it. Drag its edges to change its length. Bars snap to quarters. Click a module to edit its parameters."));
      return panel;
    }
    const cfg = KINDS[st.sel.kind];
    const item = findItem(st.plan, st.sel.kind, st.sel.id);
    if (!item) {
      st.sel = null;
      panel.append(h("p", "fpb-hint", "Select a module."));
      return panel;
    }
    panel.append(h("h3", "", `${cfg.title}: ${item.label || item.id}`));
    panel.append(h("div", "fpb-id", item.id));

    const timing = [];
    if (cfg.window) {
      timing.push({ k: "start", t: "month" }, { k: "end", t: "month", opt: true });
    } else if (cfg.account) {
      timing.push({ k: "start", t: "month" });
    } else if (cfg.point) {
      timing.push({ k: "month", t: "month" });
    }
    for (const f of [...timing, ...cfg.fields]) {
      panel.append(fieldRow(f, item[f.k], (raw) => editField(f, raw)));
    }
    if (cfg.changes) panel.append(changesEditor(cfg, item));
    return panel;
  }

  function editField(field, raw) {
    const kind = st.sel.kind;
    const id = st.sel.id;
    const value = parseField(field, raw);
    const blank = value === null || value === "";
    if (field.t === "month" && !(blank && field.opt) && !/^\d{4}-\d{2}$/.test(value || "")) {
      st.localError = `${field.k}: expected YYYY-MM`;
      draw();
      return;
    }
    const plan = structuredClone(st.plan);
    const item = findItem(plan, kind, id);
    item[field.k] = blank && field.opt ? null : value;
    st.localError = "";
    setPlan(plan);
  }

  function fieldRow(field, value, onCommit) {
    const row = h("label", "fpb-field");
    row.append(h("span", "", field.k.replace(/_/g, " ")));
    let input;
    if (field.t === "select") {
      input = h("select");
      for (const opt of field.options) {
        const o = h("option", "", opt);
        o.value = opt;
        if (opt === value) o.selected = true;
        input.append(o);
      }
      input.addEventListener("change", () => onCommit(input.value));
    } else if (field.t === "bool") {
      input = h("input");
      input.type = "checkbox";
      input.checked = Boolean(value);
      input.addEventListener("change", () => onCommit(input.checked));
    } else {
      input = h("input");
      input.type = "text";
      if (field.t === "money" || field.t === "pct") input.inputMode = "decimal";
      input.value = displayField(field, value);
      input.addEventListener("change", () => onCommit(input.value));
    }
    row.append(input);
    return row;
  }

  // Term changes (refinance, contribution changes) on debts and assets.
  function changesEditor(cfg, item) {
    const wrap = h("div", "fpb-changes");
    wrap.append(h("h4", "", "Term changes"));
    const fieldFor = (name) => ({ k: name, t: CHANGE_TYPES[name] });
    (item.changes || []).forEach((c, i) => {
      const box = h("div", "fpb-change-row");
      const month = h("input");
      month.type = "text";
      month.value = c.month;
      month.title = "month";
      month.addEventListener("change", () => editChange(i, "month", month.value));
      box.append(month);
      for (const name of cfg.changes) {
        const f = fieldFor(name);
        const inp = h("input");
        inp.type = "text";
        inp.placeholder = name.replace(/_/g, " ");
        inp.title = `${name} (blank = unchanged)`;
        inp.value = c[name] === undefined ? "" : displayField(f, c[name]);
        inp.addEventListener("change", () => editChange(i, name, inp.value, f));
        box.append(inp);
      }
      const rm = h("button", "", "remove");
      rm.addEventListener("click", () => removeChange(i));
      box.append(rm);
      wrap.append(box);
    });
    const add = h("button", "fpb-add", "Add term change");
    add.addEventListener("click", () => addChange(cfg, item));
    wrap.append(add);
    return wrap;
  }

  function editChange(i, key, raw, field) {
    const plan = structuredClone(st.plan);
    const item = findItem(plan, st.sel.kind, st.sel.id);
    const change = item.changes[i];
    if (key === "month") {
      if (!/^\d{4}-\d{2}$/.test(String(raw).trim())) {
        st.localError = "change month: expected YYYY-MM";
        draw();
        return;
      }
      change.month = String(raw).trim();
    } else {
      const value = parseField(field || { t: "money", opt: true }, raw);
      if (value === null || value === "") delete change[key];
      else change[key] = value;
    }
    setPlan(plan);
  }

  function addChange(cfg, item) {
    const plan = structuredClone(st.plan);
    const target = findItem(plan, st.sel.kind, st.sel.id);
    const month = fmt(idx(target.start) + 12);
    const first = cfg.changes[0];
    target.changes = [...(target.changes || []), { month, [first]: item[first] }];
    setPlan(plan);
  }

  function removeChange(i) {
    const plan = structuredClone(st.plan);
    const target = findItem(plan, st.sel.kind, st.sel.id);
    target.changes.splice(i, 1);
    setPlan(plan);
  }

  draw();
}

export default { render };

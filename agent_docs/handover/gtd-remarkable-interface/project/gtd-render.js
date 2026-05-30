// ============================================================
// GTD reMarkable Sheet — preview renderer
// Mirrors the Jinja2 partials in generator/template.html.j2
// ============================================================

/* ---------- QR (matches Python qrcode output: same data string) ---------- */
function qrSvg(data, cell) {
  try {
    const qr = qrcode(0, "M");
    qr.addData(String(data));
    qr.make();
    return qr.createSvgTag({ cellSize: cell || 2, margin: 0 });
  } catch (e) {
    return "";
  }
}

/* ---------- small builders ---------- */
const esc = (s) =>
  String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

function cellHTML(gl, label) {
  return `<div class="cell"><div class="box"></div><div class="gl">${gl}</div><div class="cl">${label}</div></div>`;
}
function deferHTML(heading) {
  return `<div class="defer">
    <div class="dh">${heading}</div>
    <div class="trio">
      <div class="dcell"><div class="box"></div><div class="cl">1w</div></div>
      <div class="dcell"><div class="box"></div><div class="cl">1m</div></div>
      <div class="dcell"><div class="box"></div><div class="cl">1q</div></div>
    </div>
  </div>`;
}
function fldCur(label, cur, slot, isTag) {
  const c = isTag
    ? `<span class="cur tag">${esc(cur)}</span>`
    : `<span class="cur">${esc(cur)}</span>`;
  return `<span class="fld"><span class="fl">${label}</span>${c}<span class="arr">→</span><span class="slot ${slot}"></span></span>`;
}
function fldBlank(label, slot) {
  return `<span class="fld"><span class="fl">${label}</span><span class="slot ${slot}"></span></span>`;
}
function fldProminent(label, cur, slot) {
  return `<span class="fld prominent"><span class="fl">${label}</span><span class="cur">${esc(
    cur,
  )}</span><span class="arr">→</span><span class="slot ${slot}"></span></span>`;
}

/* ---------- gutters per category ---------- */
function gutter(cat) {
  if (cat === "inbox") {
    return `<div class="gut">${cellHTML("→", "Next")}${cellHTML(
      "→",
      "Deleg",
    )}${deferHTML("Defer")}${cellHTML("✗", "Drop")}</div>`;
  }
  if (cat === "delegated") {
    return `<div class="gut">${cellHTML("✓", "Done")}${cellHTML(
      "↩",
      "To me",
    )}${deferHTML("Defer")}${cellHTML("✎", "Edit")}</div>`;
  }
  if (cat === "tickler") {
    return `<div class="gut">${cellHTML("→", "Now")}${cellHTML(
      "✓",
      "Done",
    )}${deferHTML("Re-defer")}${cellHTML("✎", "Edit")}</div>`;
  }
  // next actions
  return `<div class="gut">${cellHTML("✓", "Done")}${cellHTML(
    "→",
    "Deleg",
  )}${deferHTML("Defer")}${cellHTML("✎", "Edit")}</div>`;
}

/* ---------- fields per category ---------- */
function fields(cat, t) {
  if (cat === "inbox") {
    return `<div class="fields">
      ${fldBlank("Priority", "n")}${fldBlank("Due", "s")}${fldBlank(
        "Project",
        "m",
      )}${fldBlank("To", "to")}
    </div>`;
  }
  if (cat === "delegated") {
    return `<div class="fields">
      ${fldProminent("To", t.to, "to")}${fldCur(
        "Priority",
        t.pri,
        "n",
      )}${fldCur("Due", t.due, "s")}${fldCur("Project", t.proj, "m", true)}
    </div>`;
  }
  if (cat === "tickler") {
    return ""; // no metadata
  }
  // next actions
  return `<div class="fields">
    ${fldCur("Priority", t.pri, "n")}${fldCur("Due", t.due, "s")}${fldCur(
      "Project",
      t.proj,
      "m",
      true,
    )}${fldBlank("To", "to")}
  </div>`;
}

/* ---------- one task row ---------- */
function rowEl(cat, t) {
  const el = document.createElement("div");
  el.className = "row";
  el.innerHTML = `
    <div class="row-top">
      <div class="rail">
        <span class="qr">${qrSvg(t.id)}</span>
        <span class="code mono">${esc(t.id)}</span>
      </div>
      <div class="act-wrap"><div class="act">${esc(t.act)}</div></div>
      ${gutter(cat)}
    </div>
    ${fields(cat, t)}
  `;
  return el;
}

/* ---------- inbox capture lines ---------- */
function captureBlock(n) {
  const wrap = document.createElement("div");
  wrap.className = "capture";
  let lines = "";
  for (let i = 1; i <= n; i++) {
    lines += `<div class="cap-line"><span class="cn">N${i}</span><span class="cbx"></span></div>`;
  }
  wrap.innerHTML = `<div class="ct">+ Capture — write new items here</div>${lines}`;
  return wrap;
}

/* ---------- band (tickler subsection) ---------- */
function bandEl(title, sub, count) {
  const b = document.createElement("div");
  b.className = "band";
  b.innerHTML = `<span class="bt">${esc(title)}</span><span class="bs">${esc(
    sub,
  )}</span><span class="bc mono">${count}</span>`;
  return b;
}

/* ---------- legend per category ---------- */
function legendHTML(cat) {
  const k = (b, l) =>
    `<span class="k"><span class="kb">${b}</span><span class="kl">${l}</span></span>`;
  let items;
  if (cat === "inbox") {
    items =
      k("→", "to Next Actions") +
      k("→", "to Delegated") +
      k("▢", "Defer to Tickler") +
      k("✗", "Drop");
  } else if (cat === "delegated") {
    items =
      k("✓", "Done") +
      k("↩", "Back to me") +
      k("▢", "Defer 1w/1m/1q") +
      k("✎", "Edited");
  } else if (cat === "tickler") {
    items =
      k("→", "Activate now") +
      k("✓", "Done") +
      k("▢", "Re-defer") +
      k("✎", "Edited");
  } else {
    items =
      k("✓", "Done") +
      k("→", "Delegate") +
      k("▢", "Defer 1w/1m/1q") +
      k("✎", "Edited");
  }
  return `<div class="legend"><span class="lt">Tick to</span>${items}</div>`;
}

/* ---------- header ---------- */
const TODAY = "Saturday 30 May 2026";
const STAMP = "2026-05-30";
function headerHTML(cat, meta, contd) {
  const pageQR = qrSvg(`GTD|${cat}|${STAMP}`, 2);
  return `
    <div class="head">
      <div class="head-l">
        <div class="head-tag">${meta.tag}</div>
        <div>
          <div class="head-title">${meta.title}${
            contd
              ? ' <span style="font-weight:500;font-size:11pt">(cont\u2019d)</span>'
              : ""
          }</div>
          <div class="head-sub">${meta.sub}</div>
        </div>
      </div>
      <div class="head-r">
        <div class="big">${TODAY}</div>
        <div class="mono">${meta.countLabel}</div>
        <div style="display:flex;justify-content:flex-end"><span class="head-qr">${pageQR}</span></div>
      </div>
    </div>`;
}

/* ---------- page shell ---------- */
// One tall, auto-height page per bucket. Blocks (rows / bands / capture)
// all flow into a single .rows — the page grows to fit, never paginates.
function buildPage(cat, meta, blocks) {
  const page = document.createElement("div");
  page.className = "page";
  page.dataset.screenLabel = meta.title;
  page.innerHTML = `
    <div class="reg tl"></div><div class="reg tr"></div><div class="reg bl"></div><div class="reg br"></div>
    ${headerHTML(cat, meta, false)}
    ${legendHTML(cat)}
    <div class="rows"></div>
    <div class="foot">
      <span class="mono">${meta.title} · ${STAMP}</span>
      <span class="mono" data-pageno></span>
    </div>`;
  const rows = page.querySelector(".rows");
  blocks.forEach((b) => rows.appendChild(b));
  return page;
}

/* ---------- build everything ---------- */
function build() {
  const root = document.getElementById("doc");
  root.innerHTML = "";
  const pages = [];

  // ----- INBOX -----
  {
    const meta = {
      tag: "0",
      title: "Inbox",
      sub: "Unprocessed capture — route every item out today",
      countLabel: `${DATA.inbox.length} to process`,
    };
    const blocks = DATA.inbox.map((t) => rowEl("inbox", t));
    blocks.push(captureBlock(6));
    pages.push(buildPage("inbox", meta, blocks));
  }
  // ----- NEXT ACTIONS -----
  {
    const meta = {
      tag: "1",
      title: "Next Actions",
      sub: "On your plate — do, delegate, or defer",
      countLabel: `${DATA.next.length} actions`,
    };
    const blocks = DATA.next.map((t) => rowEl("next", t));
    pages.push(buildPage("next", meta, blocks));
  }
  // ----- DELEGATED -----
  {
    const meta = {
      tag: "2",
      title: "Delegated",
      sub: "Waiting on others — follow up or reclaim",
      countLabel: `${DATA.delegated.length} waiting`,
    };
    const blocks = DATA.delegated.map((t) => rowEl("delegated", t));
    pages.push(buildPage("delegated", meta, blocks));
  }
  // ----- TICKLER -----
  {
    const meta = {
      tag: "3",
      title: "Tickler",
      sub: "Deferred — resurface when the time comes",
      countLabel: `${
        DATA.tickler.week.length +
        DATA.tickler.month.length +
        DATA.tickler.quarter.length
      } parked`,
    };
    const blocks = [];
    const sub = [
      ["Next week", "resurfaces in ~7 days", DATA.tickler.week],
      ["Next month", "resurfaces in ~30 days", DATA.tickler.month],
      ["Next quarter", "resurfaces in ~90 days", DATA.tickler.quarter],
    ];
    for (const [title, s, items] of sub) {
      blocks.push(bandEl(title, s, items.length));
      items.forEach((t) => blocks.push(rowEl("tickler", t)));
    }
    pages.push(buildPage("tickler", meta, blocks));
  }

  pages.forEach((p) => root.appendChild(p));
  const total = pages.length;
  pages.forEach((p, i) => {
    p.querySelector("[data-pageno]").textContent = `${i + 1} / ${total}`;
  });
}

window.addEventListener("load", () => {
  if (typeof qrcode !== "undefined") build();
});

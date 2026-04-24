// Dashboard/build.mjs
// Reads the ESG highlights CSV, filters ESG_or_not=Yes rows,
// and generates a self-contained index.html dashboard.

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const INPUT_CSV_QC = path.resolve(__dirname, "..", "Quality_Check", "8.1_esg_highlights_multi.csv");
const INPUT_CSV_ROOT = path.resolve(__dirname, "..", "8.1_esg_highlights_multi.csv");
const INPUT_EVENTS_CSV = path.resolve(__dirname, "..", "Quality_Check", "9.4_events_all_data.csv");
const INPUT_JOBS_CSV = path.resolve(__dirname, "..", "Quality_Check", "11_job_all_data.csv");
const OUTPUT_HTML = path.resolve(__dirname, "index.html");

function pickLatestFile(preferredPath, fallbackPath) {
  const preferredExists = fs.existsSync(preferredPath);
  const fallbackExists = fs.existsSync(fallbackPath);
  if (!preferredExists && !fallbackExists) return preferredPath;
  if (preferredExists && !fallbackExists) return preferredPath;
  if (!preferredExists && fallbackExists) return fallbackPath;
  const preferredMtime = fs.statSync(preferredPath).mtimeMs;
  const fallbackMtime = fs.statSync(fallbackPath).mtimeMs;
  return fallbackMtime > preferredMtime ? fallbackPath : preferredPath;
}

const INPUT_CSV = pickLatestFile(INPUT_CSV_QC, INPUT_CSV_ROOT);

/* ====== CSV Parser (from html_gen_text_blocks_multi.mjs) ====== */
function parseCSV(text) {
  if (!text) return [];
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
  text = text.replace(/\r\n/g, "\n");

  const out = [];
  let i = 0, field = "", row = [], inQuotes = false;

  while (i < text.length) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i += 2; }
        else { inQuotes = false; i++; }
      } else { field += c; i++; }
      continue;
    }
    if (c === '"') { inQuotes = true; i++; continue; }
    if (c === ",") { row.push(field); field = ""; i++; continue; }
    if (c === "\n") { row.push(field); field = ""; out.push(row); row = []; i++; continue; }
    field += c; i++;
  }
  row.push(field);
  out.push(row);

  const header = (out.shift() || []).map((h) => h.trim());
  return out
    .filter((r) => r.length > 1 || (r.length === 1 && r[0].trim() !== ""))
    .map((r) => {
      const obj = {};
      for (let idx = 0; idx < header.length; idx++) {
        obj[header[idx]] = (r[idx] ?? "").trim();
      }
      return obj;
    });
}

/* ====== Main ====== */
console.log("[dashboard] Reading CSV:", INPUT_CSV);
const csvText = fs.readFileSync(INPUT_CSV, "utf8");
const allRows = parseCSV(csvText);
console.log(`[dashboard] Parsed ${allRows.length} total rows`);

const eventsRows = fs.existsSync(INPUT_EVENTS_CSV)
  ? parseCSV(fs.readFileSync(INPUT_EVENTS_CSV, "utf8"))
  : [];
console.log(`[dashboard] Parsed ${eventsRows.length} event rows`);

const jobsRows = fs.existsSync(INPUT_JOBS_CSV)
  ? parseCSV(fs.readFileSync(INPUT_JOBS_CSV, "utf8"))
  : [];
console.log(`[dashboard] Parsed ${jobsRows.length} job rows`);

const esgRows = allRows.filter((r) => (r["ESG_or_not"] || "").trim() === "Yes");
console.log(`[dashboard] Filtered to ${esgRows.length} ESG=Yes rows`);

const COL = {
  DATE: "Date",
  TITLE: "Title",
  URL: "URL",
  STORY_TYPE: "Story_Type",
  JURISDICTION: "Jurisdiction",
  RELEVANCE: "Relevance",
  ESG_RELEVANCE: "ESG_Relevance",
  OUTPUT_1: "Output 1",
  OUTPUT_2: "Output 2",
  OUTPUT_3: "Output 3",
  OUTPUT_4: "Output 4",
  OUTPUT_5: "Output 5",
  OUTPUT_6: "Output 6",
  HOOK: "Hook",
  ONE_LINER: "One Liner",
};

/* Sanitize malformed Output fields (AI artefacts) */
function sanitizeOutput(val) {
  if (!val) return "";
  let s = val.trim();
  // Detect JSON-wrapped outputs like {"outputs": ["text..."]}...garbage
  if (/^\{.*"outputs"\s*:\s*\[/.test(s) || /^\{\s*"[^"]+"\s*:\s*\[/.test(s)) {
    // Extract text between the first [ and matching ] or end of useful content
    const arrStart = s.indexOf("[");
    if (arrStart >= 0) {
      // Find quoted strings inside the array
      const texts = [];
      const re = /"([^"]+)"/g;
      let m;
      const arrPart = s.slice(arrStart);
      while ((m = re.exec(arrPart)) !== null) texts.push(m[1]);
      if (texts.length) s = texts.join(" ");
    }
  }
  // Strip trailing garbage: repeated " 0" or " 0}" patterns
  s = s.replace(/(?:\s+0[}\s]){5,}.*$/s, "");
  // Strip trailing "# Answer ..." artefacts
  s = s.replace(/['\]}\s]*#\s*Answer.*$/s, "");
  // Strip trailing "# End of response..." artefacts
  s = s.replace(/['\]}\s]*#\s*End of response.*$/s, "");
  // Strip stray trailing JSON chars
  s = s.replace(/['\]}\s]+$/, "");
  // Strip trailing AI JSON/markdown garbage (e.g. ,"name\":... or repeated **]**}** )
  s = s.replace(/,\s*"name\\"?:.*$/s, "");
  s = s.replace(/(\*{1,2}\]?\*{0,2}\}?\*{0,2}){3,}.*$/s, "");
  return s.trim();
}

function normalizeJurisdictionLabel(value) {
  const v = (value || "").trim();
  return v === "National" ? "Australian National Scope" : v;
}

// Slim down the data we embed — only the columns the dashboard needs
const slim = esgRows.map((r, idx) => ({
  id: idx,
  Date: r[COL.DATE] || "",
  Title: r[COL.TITLE] || "",
  URL: r[COL.URL] || "",
  Story_Type: r[COL.STORY_TYPE] || "",
  Jurisdiction: normalizeJurisdictionLabel(r[COL.JURISDICTION] || ""),
  ESG_Relevance: r[COL.RELEVANCE] || r[COL.ESG_RELEVANCE] || "",
  Out1: sanitizeOutput(r[COL.OUTPUT_1]),
  Out2: sanitizeOutput(r[COL.OUTPUT_2]),
  Out3: sanitizeOutput(r[COL.OUTPUT_3]),
  Out4: sanitizeOutput(r[COL.OUTPUT_4]),
  Out5: sanitizeOutput(r[COL.OUTPUT_5]),
  Out6: sanitizeOutput(r[COL.OUTPUT_6]),
  Hook: sanitizeOutput(r[COL.HOOK] || ""),
  OneLiner: sanitizeOutput(r[COL.ONE_LINER] || ""),
}));

const eventsData = eventsRows.map((r, idx) => ({
  id: idx,
  Date: r["Date"] || "",
  Title: r["Title"] || "",
  URL: r["URL"] || "",
  Event_Description: r["Event_Description"] || "",
}));

const jobsData = jobsRows.map((r, idx) => ({
  id: idx,
  Date: r["Date"] || "",
  Title: r["Title"] || "",
  URL: r["URL"] || "",
  Job_Description: r["Job_Description"] || r["Event_Description"] || "",
}));

const dataJSON = JSON.stringify(slim);
const eventsJSON = JSON.stringify(eventsData);
const jobsJSON = JSON.stringify(jobsData);

/* ====== HTML Template ====== */
const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ESG Story Picker Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/file-saver@2.0.5/dist/FileSaver.min.js"><\/script>
<style>
  *, *::before, *::after { box-sizing: border-box; }
  body {
    font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
    background: #3a3f44; color: #222; margin: 0; padding: 20px 20px 100px;
  }
  .container { max-width: 1100px; margin: 0 auto; }
  h1 { font-size: 1.6rem; margin-bottom: 4px; color: #f1f1f1; }
  .counter { font-size: 0.95rem; color: #d0d0d0; margin-bottom: 24px; }
  h2.jurisdiction-header {
    font-size: 1.35rem; margin: 32px 0 8px; padding: 8px 12px;
    background: #37474f; color: #fff; border-radius: 6px;
  }
  .jur-count { font-weight: 400; font-size: 0.9rem; opacity: 0.75; }


  /* Story card */
  .story-card {
    border-radius: 8px; padding: 16px; margin-bottom: 12px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.1); position: relative;
    transition: opacity 0.25s;
  }
  .story-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.18); }
  .card-header { display: flex; align-items: flex-start; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
  .story-type-pill {
    font-size: 0.68rem; font-weight: 600; padding: 2px 10px;
    border-radius: 10px; border: 1px solid rgba(0,0,0,0.15);
    white-space: nowrap; line-height: 1.5;
  }
  .relevance-badge {
    font-size: 0.65rem; font-weight: 700; padding: 2px 8px;
    border-radius: 10px; color: #fff; white-space: nowrap;
    margin-left: auto; line-height: 1.5;
  }
  .rel-high   { background: #e53935; }
  .rel-medium { background: #fb8c00; }
  .rel-low    { background: #9e9e9e; }

  .card-title { font-size: 1rem; font-weight: 600; margin-bottom: 2px; }
  .card-url { font-size: 0.78rem; color: #1565c0; margin-bottom: 8px; word-break: break-all; }
  .card-url a { color: #1565c0; text-decoration: none; }
  .card-url a:hover { text-decoration: underline; }

  .card-body p { margin: 6px 0; font-size: 0.9rem; line-height: 1.5; }
  .card-body .bullet {
    margin-left: 32px; display: flex; align-items: flex-start; gap: 10px;
  }
  .card-body .bullet span:first-child { flex: 0 0 12px; }

  /* Context menu */
  .ctx-menu {
    position: fixed; background: #fff; border: 1px solid #ccc;
    border-radius: 6px; box-shadow: 0 4px 14px rgba(0,0,0,0.18);
    z-index: 9999; padding: 4px 0; min-width: 160px; display: none;
  }
  .ctx-menu div {
    padding: 8px 16px; cursor: pointer; font-size: 0.88rem;
  }
  .ctx-menu div:hover { background: #f0f0f0; }

  /* Generate Doc button */
  .gen-doc-bar {
    position: fixed; bottom: 0; left: 0; right: 0;
    background: #fff; border-top: 1px solid #ddd;
    padding: 12px 24px; text-align: center; z-index: 999;
    box-shadow: 0 -2px 8px rgba(0,0,0,0.08);
  }
  .gen-doc-btn {
    background: #1565c0; color: #fff; border: none;
    padding: 10px 32px; font-size: 1rem; font-weight: 600;
    border-radius: 6px; cursor: pointer;
  }
  .gen-doc-btn:hover { background: #0d47a1; }

  .empty-msg { display: none; color: #888; font-style: italic; margin: 40px 0; text-align: center; }

  .aux-section-title { margin: 36px 0 10px; font-size: 1.25rem; color: #f1f1f1; }
  .aux-root { margin-bottom: 20px; }
  .aux-card {
    background: #fff; border: 1px solid #ddd; border-radius: 8px;
    padding: 14px 16px; margin-bottom: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    transition: opacity 0.25s;
  }
  .aux-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.18); }
  .aux-title { font-size: 1rem; font-weight: 600; margin-bottom: 4px; }
  .aux-date { font-size: 0.82rem; color: #555; margin-bottom: 6px; }
  .aux-url { font-size: 0.8rem; margin-bottom: 8px; word-break: break-all; }
  .aux-url a { color: #1565c0; text-decoration: none; }
  .aux-url a:hover { text-decoration: underline; }
  .aux-desc { font-size: 0.9rem; line-height: 1.5; }
  .drag-selected { outline: 3px solid #1565c0; outline-offset: -3px; }
  .box-selected { outline: 3px solid #0d47a1; outline-offset: -3px; }
</style>
</head>
<body>

<div class="container">
  <h1>ESG Story Picker Dashboard</h1>
  <div class="counter" id="counter"></div>
  <div id="root"></div>
  <div class="empty-msg" id="emptyMsg">No stories remaining.</div>

  <h2 class="aux-section-title">Events</h2>
  <div id="eventsRoot" class="aux-root"></div>

  <h2 class="aux-section-title">Jobs</h2>
  <div id="jobsRoot" class="aux-root"></div>
</div>

<div class="gen-doc-bar">
  <button class="gen-doc-btn" onclick="generateDoc()">Generate Doc</button>
</div>

<!-- Custom right-click menu -->
<div class="ctx-menu" id="ctxMenu">
  <div id="ctxLabel" onclick="removeTargetCard()">Remove Story</div>
</div>

<script>
/* ====== Embedded Data ====== */
const RAW_DATA = ${dataJSON};
const EVENTS_DATA = ${eventsJSON};
const JOBS_DATA = ${jobsJSON};
const STORY_BY_ID = new Map(RAW_DATA.map((s) => [s.id, s]));
const EVENTS_BY_ID = new Map(EVENTS_DATA.map((e) => [e.id, e]));
const JOBS_BY_ID = new Map(JOBS_DATA.map((j) => [j.id, j]));

/* ====== Story-type colour map ====== */
const TYPE_COLORS = {
  "Community, First Nations, and Social Licence Initiatives": "#FFE0B2",
  "Compliance, Oversight, and Enforcement Actions": "#FFCDD2",
  "Consultation and Policy Design Opportunities": "#E1BEE7",
  "Corporate and Institutional ESG Actions": "#BBDEFB",
  "Environmental Protection, Biodiversity, and Nature Policy": "#C8E6C9",
  "Funding and Grant Announcements": "#FFF9C4",
  "Infrastructure, Project Approvals, and EPBC Developments": "#D7CCC8",
  "Legislative and Statutory Developments": "#B2DFDB",
  "Ministerial, Diplomatic, and International Engagements": "#F0F4C3",
  "Misc": "#CFD8DC",
  "Parliamentary and Political Proceedings": "#D1C4E9",
  "Reports, Data Releases, and Analytical Insights": "#B3E5FC",
  "State and Local Government Programs": "#F8BBD0",
};
function typeColor(st) { return TYPE_COLORS[st] || "#E0E0E0"; }

/* Darken a hex colour by a factor (0-1, where 0.7 = 30% darker) */
function darkenColor(hex, factor) {
  const f = factor || 0.65;
  const r = parseInt(hex.slice(1,3),16);
  const g = parseInt(hex.slice(3,5),16);
  const b = parseInt(hex.slice(5,7),16);
  const dr = Math.round(r * f), dg = Math.round(g * f), db = Math.round(b * f);
  return '#' + [dr,dg,db].map(c => c.toString(16).padStart(2,'0')).join('');
}

/* ====== Jurisdiction order ====== */
const JURIS_ORDER = [
  "Australian National Scope","NSW","Victoria","Queensland","Northern Territory",
  "Western Australia","South Australia","Tasmania",
  "Australian Capital Territory","International"
];

/* ====== Relevance sort priority ====== */
const REL_PRIORITY = { High: 0, Medium: 1, Low: 2 };

const TYPE_ORDER = [
  "Legislative and Statutory Developments",
  "Parliamentary and Political Proceedings",
  "Compliance, Oversight, and Enforcement Actions",
  "Consultation and Policy Design Opportunities",
  "Funding and Grant Announcements",
  "Corporate and Institutional ESG Actions",
  "State and Local Government Programs",
  "Infrastructure, Project Approvals, and EPBC Developments",
  "Reports, Data Releases, and Analytical Insights",
  "Ministerial, Diplomatic, and International Engagements",
  "Environmental Protection, Biodiversity, and Nature Policy",
  "Community, First Nations, and Social Licence Initiatives",
  "Misc",
];
const TYPE_RANK = {};
TYPE_ORDER.forEach((t, i) => TYPE_RANK[t] = i);

/* ====== Helpers ====== */
function esc(s) {
  return String(s || "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;")
    .replace(/'/g,"&#39;");
}
function hasLeadingBullet(line) { return /^\\s*[\\u2022*\\-\\u2013\\u2014]/.test(line || ""); }
function stripBullet(line) { return String(line || "").replace(/^\\s*[\\u2022*\\-\\u2013\\u2014]\\s*/, "").trim(); }

function pHtml(text) {
  return '<p>' + esc(text) + '</p>';
}
function bulletHtml(text) {
  return '<p class="bullet"><span>&bull;</span><span>' + esc(text) + '</span></p>';
}
function narrativeHtml(parts) {
  return '<p>' + parts.filter(Boolean).join(' ') + '</p>';
}

/* ====== Build card body per story type (mirrors html_gen_text_blocks_multi.mjs) ====== */
function buildCardBody(s) {
  const outputs = [s.Out1, s.Out2, s.Out3, s.Out4, s.Out5, s.Out6]
    .map(value => String(value || '').trim())
    .filter(Boolean);
  const hook = (s.Hook || "").trim();
  const oneLiner = (s.OneLiner || "").trim();
  if (![...outputs, hook, oneLiner].some(Boolean)) return "";
  let h = "";
  if (outputs.length) {
    const narrative = outputs.map((text, index) => {
      if (outputs.length === 1 && s.URL) return '<a href="' + esc(s.URL) + '" target="_blank">' + esc(text) + '</a>';
      if (index === 0) return '<strong>' + esc(text) + '</strong>';
      if (index === 1 && s.URL) return '<a href="' + esc(s.URL) + '" target="_blank">' + esc(text) + '</a>';
      return esc(text);
    });
    h = narrativeHtml(narrative);
  }
  if (hook || oneLiner) {
    const hookPart = hook ? '<strong>' + esc(hook) + '</strong>' : '';
    const oneLinerPart = oneLiner ? esc(oneLiner) : '';
    h += '<p>' + [hookPart, oneLinerPart].filter(Boolean).join(' ') + '</p>';
  }
  return h;
}

/* ====== Render ====== */
const removedIds = new Set();
const removedEventIds = new Set();
const removedJobIds = new Set();
let storyOrder = [];
let eventOrder = EVENTS_DATA.map((e) => e.id);
let jobOrder = JOBS_DATA.map((j) => j.id);

function initStoryOrder() {
  const grouped = {};
  for (const s of RAW_DATA) {
    const j = s.Jurisdiction || "Other";
    const st = s.Story_Type || "Other";
    if (!grouped[j]) grouped[j] = {};
    if (!grouped[j][st]) grouped[j][st] = [];
    grouped[j][st].push(s);
  }
  for (const j of Object.keys(grouped)) {
    for (const st of Object.keys(grouped[j])) {
      grouped[j][st].sort((a,b) => (REL_PRIORITY[a.ESG_Relevance]??3) - (REL_PRIORITY[b.ESG_Relevance]??3));
    }
  }
  const ordered = [];
  for (const jur of JURIS_ORDER) {
    if (!grouped[jur]) continue;
    const orderedTypes = Object.keys(grouped[jur]).sort((a,b) => (TYPE_RANK[a]??999) - (TYPE_RANK[b]??999));
    for (const st of orderedTypes) {
      for (const s of grouped[jur][st]) ordered.push(s.id);
    }
    delete grouped[jur];
  }
  for (const jur of Object.keys(grouped).sort()) {
    const orderedTypes = Object.keys(grouped[jur]).sort((a,b) => (TYPE_RANK[a]??999) - (TYPE_RANK[b]??999));
    for (const st of orderedTypes) {
      for (const s of grouped[jur][st]) ordered.push(s.id);
    }
  }
  storyOrder = ordered;
}
initStoryOrder();

function refreshStoryCounts() {
  const storyCards = document.querySelectorAll('.story-card').length;
  document.getElementById('counter').textContent = storyCards + ' stories remaining';
  document.querySelectorAll('.jur-section').forEach((sec) => {
    const count = sec.querySelectorAll('.story-card').length;
    const countEl = sec.querySelector('.jur-count');
    if (countEl) countEl.textContent = '(' + count + ')';
  });
}

function syncOrderFromDom() {
  storyOrder = Array.from(document.querySelectorAll('.story-card')).map((el) => Number(el.dataset.id));
  for (const el of document.querySelectorAll('.story-card')) {
    const id = Number(el.dataset.id);
    const jurWrap = el.closest('.jur-cards');
    const s = STORY_BY_ID.get(id);
    if (s && jurWrap && jurWrap.dataset.jur) s.Jurisdiction = jurWrap.dataset.jur;
  }
  eventOrder = Array.from(document.querySelectorAll('#eventsRoot .aux-card[data-aux-type="event"]')).map((el) => Number(el.dataset.auxId));
  jobOrder = Array.from(document.querySelectorAll('#jobsRoot .aux-card[data-aux-type="job"]')).map((el) => Number(el.dataset.auxId));
  refreshStoryCounts();
}

function render() {
  const root = document.getElementById('root');
  const remainingIds = storyOrder.filter((id) => !removedIds.has(id) && STORY_BY_ID.has(id));
  document.getElementById('counter').textContent = remainingIds.length + ' stories remaining';

  if (remainingIds.length === 0) {
    root.innerHTML = '';
    document.getElementById('emptyMsg').style.display = 'block';
    return;
  }
  document.getElementById('emptyMsg').style.display = 'none';

  // Group by jurisdiction while preserving user-managed order
  const grouped = {};
  for (const id of remainingIds) {
    const s = STORY_BY_ID.get(id);
    if (!s) continue;
    const j = s.Jurisdiction || "Other";
    if (!grouped[j]) grouped[j] = [];
    grouped[j].push(s);
  }

  // Count stories per jurisdiction
  const jurCounts = {};
  for (const j of Object.keys(grouped)) {
    jurCounts[j] = grouped[j].length;
  }

  let html = '';
  for (const jur of JURIS_ORDER) {
    if (!grouped[jur]) continue;
    html += '<section class="jur-section" data-jur="' + esc(jur) + '">';
    html += '<h2 class="jurisdiction-header">' + esc(jur) + ' <span class="jur-count">(' + jurCounts[jur] + ')</span></h2>';
    html += '<div class="jur-cards" data-jur="' + esc(jur) + '">';
    for (const s of grouped[jur]) {
      const bg = typeColor(s.Story_Type);
      const pillBg = darkenColor(bg, 0.65);
      const relClass = s.ESG_Relevance === 'High' ? 'rel-high' : s.ESG_Relevance === 'Medium' ? 'rel-medium' : 'rel-low';
      html += '<div class="story-card" draggable="true" data-id="' + s.id + '" style="background:' + bg + ';" oncontextmenu="showCtx(event, ' + s.id + ')">';
      html += '<div class="card-header">';
      html += '<span class="story-type-pill" style="background:' + pillBg + '; color:#fff;">' + esc(s.Story_Type) + '</span>';
      html += '<span class="relevance-badge ' + relClass + '">' + esc(s.ESG_Relevance) + '</span>';
      html += '</div>';
      html += '<div class="card-title">' + esc(s.Title) + '</div>';
      html += '<div class="card-body">' + buildCardBody(s) + '</div>';
      html += '</div>';
    }
    html += '</div></section>';
    delete grouped[jur];
  }
  // Any jurisdictions not in JURIS_ORDER
  for (const jur of Object.keys(grouped).sort()) {
    html += '<section class="jur-section" data-jur="' + esc(jur) + '">';
    html += '<h2 class="jurisdiction-header">' + esc(jur) + ' <span class="jur-count">(' + jurCounts[jur] + ')</span></h2>';
    html += '<div class="jur-cards" data-jur="' + esc(jur) + '">';
    for (const s of grouped[jur]) {
      const bg = typeColor(s.Story_Type);
      const pillBg = darkenColor(bg, 0.65);
      const relClass = s.ESG_Relevance === 'High' ? 'rel-high' : s.ESG_Relevance === 'Medium' ? 'rel-medium' : 'rel-low';
      html += '<div class="story-card" draggable="true" data-id="' + s.id + '" style="background:' + bg + ';" oncontextmenu="showCtx(event, ' + s.id + ')">';
      html += '<div class="card-header">';
      html += '<span class="story-type-pill" style="background:' + pillBg + '; color:#fff;">' + esc(s.Story_Type) + '</span>';
      html += '<span class="relevance-badge ' + relClass + '">' + esc(s.ESG_Relevance) + '</span>';
      html += '</div>';
      html += '<div class="card-title">' + esc(s.Title) + '</div>';
      html += '<div class="card-body">' + buildCardBody(s) + '</div>';
      html += '</div>';
    }
    html += '</div></section>';
  }

  root.innerHTML = html;
}

/* ====== Right-click context menu ====== */
let ctxTargetId = null;
let ctxTargetType = 'story';
const ctxMenu = document.getElementById('ctxMenu');
let selectedCardEl = null;
let draggedEl = null;
let draggedKind = null;

function selectCard(el) {
  if (selectedCardEl && selectedCardEl !== el) selectedCardEl.classList.remove('box-selected');
  selectedCardEl = el || null;
  if (selectedCardEl) selectedCardEl.classList.add('box-selected');
}

function showCtx(e, id, type) {
  e.preventDefault();
  ctxTargetId = id;
  ctxTargetType = type || 'story';
  document.getElementById('ctxLabel').textContent =
    type === 'event' ? 'Remove Event' : type === 'job' ? 'Remove Job' : 'Remove Story';
  ctxMenu.style.left = e.clientX + 'px';
  ctxMenu.style.top = e.clientY + 'px';
  ctxMenu.style.display = 'block';
}

function removeTargetCard() {
  if (ctxTargetId !== null) {
    if (ctxTargetType === 'event') removedEventIds.add(ctxTargetId);
    else if (ctxTargetType === 'job') removedJobIds.add(ctxTargetId);
    else removedIds.add(ctxTargetId);
    const sel = ctxTargetType === 'story'
      ? '.story-card[data-id="' + ctxTargetId + '"]'
      : '.aux-card[data-aux-type="' + ctxTargetType + '"][data-aux-id="' + ctxTargetId + '"]';
    const el = document.querySelector(sel);
    if (el) { el.style.opacity = '0'; setTimeout(() => { render(); renderAux(); }, 260); }
    else { render(); renderAux(); }
  }
  ctxMenu.style.display = 'none';
  ctxTargetId = null;
  ctxTargetType = 'story';
}

document.addEventListener('click', () => { ctxMenu.style.display = 'none'; });
document.addEventListener('contextmenu', (e) => {
  if (!e.target.closest('.story-card') && !e.target.closest('.aux-card')) {
    ctxMenu.style.display = 'none';
  }
});

document.addEventListener('click', (e) => {
  const card = e.target.closest('.story-card, .aux-card');
  if (card) selectCard(card);
  else if (!e.target.closest('.ctx-menu')) selectCard(null);
});

document.addEventListener('dragstart', (e) => {
  const card = e.target.closest('.story-card, .aux-card');
  if (!card) return;
  draggedEl = card;
  draggedKind = card.classList.contains('story-card') ? 'story' : card.dataset.auxType;
  selectCard(card);
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', draggedKind);
  }
});

document.addEventListener('dragover', (e) => {
  if (!draggedEl) return;

  const targetCard = e.target.closest('.story-card, .aux-card');
  if (targetCard && targetCard !== draggedEl) {
    const targetKind = targetCard.classList.contains('story-card') ? 'story' : targetCard.dataset.auxType;
    if (targetKind !== draggedKind) return;
    e.preventDefault();
    const rect = targetCard.getBoundingClientRect();
    const placeAfter = e.clientY > rect.top + rect.height / 2;
    const parent = targetCard.parentElement;
    if (placeAfter) parent.insertBefore(draggedEl, targetCard.nextSibling);
    else parent.insertBefore(draggedEl, targetCard);
    return;
  }

  if (draggedKind === 'story') {
    const jurCards = e.target.closest('.jur-cards');
    if (jurCards && !e.target.closest('.story-card')) {
      e.preventDefault();
      jurCards.appendChild(draggedEl);
      return;
    }
  }

  if (draggedKind === 'event') {
    const eventsRoot = e.target.closest('#eventsRoot');
    if (eventsRoot && !e.target.closest('.aux-card')) {
      e.preventDefault();
      eventsRoot.appendChild(draggedEl);
      return;
    }
  }

  if (draggedKind === 'job') {
    const jobsRoot = e.target.closest('#jobsRoot');
    if (jobsRoot && !e.target.closest('.aux-card')) {
      e.preventDefault();
      jobsRoot.appendChild(draggedEl);
    }
  }
});

document.addEventListener('drop', (e) => {
  if (!draggedEl) return;
  e.preventDefault();
  syncOrderFromDom();
});

document.addEventListener('dragend', () => {
  if (draggedEl) syncOrderFromDom();
  draggedEl = null;
  draggedKind = null;
});

/* ====== Drag-select + Delete-key ====== */
let hoveredCardId = null;
let hoveredAuxType = null, hoveredAuxId = null;
let isDragging = false;
const dragSelected = new Set(); // elements

function clearDragSelection() {
  dragSelected.forEach(el => el.classList.remove('drag-selected'));
  dragSelected.clear();
}

function markCard(el) {
  if (el && !dragSelected.has(el)) { el.classList.add('drag-selected'); dragSelected.add(el); }
}

document.addEventListener('mousedown', (e) => {
  if (e.button !== 0) return; // left-click only
  if (e.target.closest('a, button, .ctx-menu')) return;
  if (e.target.closest('.gen-doc-bar')) return;
  const card = e.target.closest('.story-card, .aux-card');
  clearDragSelection();
  isDragging = true;
  if (card) markCard(card);
});

document.addEventListener('mousemove', (e) => {
  if (!isDragging) return;
  const card = e.target.closest('.story-card, .aux-card');
  if (card) markCard(card);
});

document.addEventListener('mouseup', () => { isDragging = false; });

document.addEventListener('mouseover', (e) => {
  const card = e.target.closest('.story-card');
  hoveredCardId = card ? Number(card.dataset.id) : null;
  const aux = e.target.closest('.aux-card');
  if (aux && aux.dataset.auxType) { hoveredAuxType = aux.dataset.auxType; hoveredAuxId = Number(aux.dataset.auxId); }
  else { hoveredAuxType = null; hoveredAuxId = null; }
});

document.addEventListener('keydown', (e) => {
  if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && selectedCardEl && !e.target.closest('input, textarea, [contenteditable="true"]')) {
    const isStory = selectedCardEl.classList.contains('story-card');
    const kind = isStory ? 'story' : selectedCardEl.dataset.auxType;
    const selector = kind === 'story'
      ? '.story-card'
      : '.aux-card[data-aux-type="' + kind + '"]';
    const cards = Array.from(document.querySelectorAll(selector));
    const idx = cards.indexOf(selectedCardEl);
    if (idx !== -1) {
      if (e.key === 'ArrowUp' && idx > 0) {
        const ref = cards[idx - 1];
        ref.parentElement.insertBefore(selectedCardEl, ref);
        syncOrderFromDom();
      } else if (e.key === 'ArrowDown' && idx < cards.length - 1) {
        const ref = cards[idx + 1];
        ref.parentElement.insertBefore(selectedCardEl, ref.nextSibling);
        syncOrderFromDom();
      }
      e.preventDefault();
    }
    return;
  }

  if (e.key !== 'Delete') return;
  // Batch remove if drag-selection exists
  if (dragSelected.size > 0) {
    dragSelected.forEach(el => {
      el.style.opacity = '0';
      if (el.classList.contains('story-card')) removedIds.add(Number(el.dataset.id));
      else if (el.dataset.auxType === 'event') removedEventIds.add(Number(el.dataset.auxId));
      else if (el.dataset.auxType === 'job') removedJobIds.add(Number(el.dataset.auxId));
    });
    dragSelected.clear();
    setTimeout(() => { render(); renderAux(); }, 260);
    return;
  }
  if (hoveredCardId !== null) {
    removedIds.add(hoveredCardId);
    const el = document.querySelector('.story-card[data-id="' + hoveredCardId + '"]');
    if (el) { el.style.opacity = '0'; setTimeout(() => render(), 260); }
    else { render(); }
    hoveredCardId = null;
  } else if (hoveredAuxId !== null) {
    const set = hoveredAuxType === 'event' ? removedEventIds : removedJobIds;
    set.add(hoveredAuxId);
    const el = document.querySelector('.aux-card[data-aux-type="' + hoveredAuxType + '"][data-aux-id="' + hoveredAuxId + '"]');
    if (el) { el.style.opacity = '0'; setTimeout(() => renderAux(), 260); }
    else { renderAux(); }
    hoveredAuxType = null; hoveredAuxId = null;
  }
});

/* ====== Markdown Generation ====== */
function buildMdBody(s) {
  const outputs = [s.Out1, s.Out2, s.Out3, s.Out4, s.Out5, s.Out6]
    .map(value => String(value || '').trim())
    .filter(Boolean);
  if (!outputs.length) return '';
  return outputs.map((text, index) => {
    if (outputs.length === 1 && s.URL) return '[' + text + '](' + s.URL + ')';
    if (index === 0) return '**' + text + '**';
    if (index === 1 && s.URL) return '[' + text + '](' + s.URL + ')';
    return text;
  }).join(' ');
}

function generateDoc() {
  syncOrderFromDom();
  const remaining = storyOrder
    .filter((id) => !removedIds.has(id) && STORY_BY_ID.has(id))
    .map((id) => STORY_BY_ID.get(id));
  const remEvents = eventOrder
    .filter((id) => !removedEventIds.has(id) && EVENTS_BY_ID.has(id))
    .map((id) => EVENTS_BY_ID.get(id));
  const remJobs = jobOrder
    .filter((id) => !removedJobIds.has(id) && JOBS_BY_ID.has(id))
    .map((id) => JOBS_BY_ID.get(id));
  if (remaining.length === 0 && remEvents.length === 0 && remJobs.length === 0) { alert('No items remaining to export.'); return; }

  let md = '';
  let prevJur = null;
  for (const s of remaining) {
    const jur = s.Jurisdiction || 'Other';
    if (jur !== prevJur) {
      md += '# ' + jur + '\\n\\n';
      prevJur = jur;
    }
    const body = buildMdBody(s);
    if (body) md += body + '\\n\\n';
    if (s.Hook) md += '**' + s.Hook + '** ';
    if (s.OneLiner) md += s.OneLiner;
    if (s.Hook || s.OneLiner) md += '\\n\\n';
    md += '---\\n\\n';
  }

  // Events
  if (remEvents.length > 0) {
    md += '# Events\\n\\n';
    for (const ev of remEvents) {
      if (ev.Title) md += '**' + ev.Title + '**\\n\\n';
      if (ev.Event_Description) md += ev.Event_Description + '\\n\\n';
      if (ev.URL) md += ev.URL + '\\n\\n';
      md += '---\\n\\n';
    }
  }
  // Jobs
  if (remJobs.length > 0) {
    md += '# Jobs\\n\\n';
    for (const jb of remJobs) {
      if (jb.Title) md += '**' + jb.Title + '**\\n\\n';
      if (jb.Job_Description) md += jb.Job_Description + '\\n\\n';
      if (jb.URL) md += jb.URL + '\\n\\n';
      md += '---\\n\\n';
    }
  }

  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
  saveAs(blob, 'selected_stories.md');
}

function renderAuxSection(rootId, rowsById, order, descKey, removedSet, auxType) {
  const root = document.getElementById(rootId);
  if (!root) return;
  const filteredIds = order.filter((id) => !removedSet.has(id) && rowsById.has(id));
  if (!filteredIds || filteredIds.length === 0) {
    root.innerHTML = '<div class="aux-card"><div class="aux-desc">No data available.</div></div>';
    return;
  }

  let html = '';
  for (const id of filteredIds) {
    const row = rowsById.get(id);
    html += '<div class="aux-card" draggable="true" data-aux-type="' + auxType + '" data-aux-id="' + row.id + '" oncontextmenu="showCtx(event,' + row.id + ',\\\'' + auxType + '\\\')">'; 
    html += '<div class="aux-date">' + esc(row.Date) + '</div>';
    html += '<div class="aux-title">' + esc(row.Title) + '</div>';
    html += '<div class="aux-url"><a href="' + esc(row.URL) + '" target="_blank">' + esc(row.URL) + '</a></div>';
    html += '<div class="aux-desc">' + esc(row[descKey] || '') + '</div>';
    html += '</div>';
  }
  root.innerHTML = html;
}

function renderAux() {
  renderAuxSection('eventsRoot', EVENTS_BY_ID, eventOrder, 'Event_Description', removedEventIds, 'event');
  renderAuxSection('jobsRoot', JOBS_BY_ID, jobOrder, 'Job_Description', removedJobIds, 'job');
}

/* ====== Init ====== */
render();
renderAux();
<\/script>
</body>
</html>`;

/* ====== Write output ====== */
fs.writeFileSync(OUTPUT_HTML, html, "utf8");
console.log(`[dashboard] ✅ Wrote ${OUTPUT_HTML}`);
console.log(`[dashboard] Open Dashboard/index.html in a browser to use.`);

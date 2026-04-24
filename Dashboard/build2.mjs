// Dashboard/build2.mjs
// Two-column layout: LEFT (all items) | RIGHT (selected items for export)
// Drag-and-drop, arrow keys, and context menu for smooth UX

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const INPUT_CSV_QC = path.resolve(__dirname, "..", "Quality_Check", "8.1_esg_highlights_multi.csv");
const INPUT_CSV_ROOT = path.resolve(__dirname, "..", "8.1_esg_highlights_multi.csv");
const INPUT_EVENTS_CSV = path.resolve(__dirname, "..", "Quality_Check", "9.4_events_all_data.csv");
const INPUT_JOBS_CSV = path.resolve(__dirname, "..", "Quality_Check", "11_job_all_data.csv");
const OUTPUT_HTML = path.resolve(__dirname, "index2.html");

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

/* ====== CSV Parser ====== */
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
console.log("[dashboard2] Reading CSV:", INPUT_CSV);
const csvText = fs.readFileSync(INPUT_CSV, "utf8");
const allRows = parseCSV(csvText);
console.log(`[dashboard2] Parsed ${allRows.length} total rows`);

const eventsRows = fs.existsSync(INPUT_EVENTS_CSV)
  ? parseCSV(fs.readFileSync(INPUT_EVENTS_CSV, "utf8"))
  : [];
console.log(`[dashboard2] Parsed ${eventsRows.length} event rows`);

const jobsRows = fs.existsSync(INPUT_JOBS_CSV)
  ? parseCSV(fs.readFileSync(INPUT_JOBS_CSV, "utf8"))
  : [];
console.log(`[dashboard2] Parsed ${jobsRows.length} job rows`);

const esgRows = allRows.filter((r) => (r["ESG_or_not"] || "").trim() === "Yes");
console.log(`[dashboard2] Filtered to ${esgRows.length} ESG=Yes rows`);

const COL = {
  DATE: "Date", TITLE: "Title", URL: "URL", STORY_TYPE: "Story_Type",
  JURISDICTION: "Jurisdiction", RELEVANCE: "Relevance", ESG_RELEVANCE: "ESG_Relevance",
  OUTPUT_1: "Output 1", OUTPUT_2: "Output 2", OUTPUT_3: "Output 3",
  OUTPUT_4: "Output 4", OUTPUT_5: "Output 5", OUTPUT_6: "Output 6",
  HOOK: "Hook", ONE_LINER: "One Liner",
};

function sanitizeOutput(val) {
  if (!val) return "";
  let s = val.trim();
  if (/^\{.*"outputs"\s*:\s*\[/.test(s) || /^\{\s*"[^"]+"\s*:\s*\[/.test(s)) {
    const arrStart = s.indexOf("[");
    if (arrStart >= 0) {
      const texts = [];
      const re = /"([^"]+)"/g;
      let m;
      const arrPart = s.slice(arrStart);
      while ((m = re.exec(arrPart)) !== null) texts.push(m[1]);
      if (texts.length) s = texts.join(" ");
    }
  }
  s = s.replace(/(?:\s+0[}\s]){5,}.*$/s, "");
  s = s.replace(/['\]}\s]*#\s*Answer.*$/s, "");
  s = s.replace(/['\]}\s]*#\s*End of response.*$/s, "");
  s = s.replace(/['\]}\s]+$/, "");
  s = s.replace(/,\s*"name\\"?:.*$/s, "");
  s = s.replace(/(\*{1,2}\]?\*{0,2}\}?\*{0,2}){3,}.*$/s, "");
  return s.trim();
}

function normalizeJurisdictionLabel(value) {
  const v = (value || "").trim();
  return v === "National" ? "Australian National Scope" : v;
}

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
  id: idx, Date: r["Date"] || "", Title: r["Title"] || "",
  URL: r["URL"] || "", Event_Description: r["Event_Description"] || "",
}));

const jobsData = jobsRows.map((r, idx) => ({
  id: idx, Date: r["Date"] || "", Title: r["Title"] || "",
  URL: r["URL"] || "", Job_Description: r["Job_Description"] || r["Event_Description"] || "",
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
<title>ESG Story Picker - Two Column</title>
<script src="https://cdn.jsdelivr.net/npm/file-saver@2.0.5/dist/FileSaver.min.js"><\/script>
<style>
  *, *::before, *::after { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; overflow: hidden; }
  body {
    font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
    background: #2d3136; color: #222;
  }
  
  /* Main layout */
  .main-container {
    display: flex; height: 100%; width: 100%;
  }
  .panel {
    flex: 1; display: flex; flex-direction: column;
    overflow: hidden; border-right: 3px solid #1a1d20;
  }
  .panel:last-child { border-right: none; }
  .panel-header {
    background: #37474f; color: #fff; padding: 12px 16px;
    font-size: 1.1rem; font-weight: 600;
    border-bottom: 2px solid #263238;
    display: flex; align-items: center; justify-content: space-between;
  }
  .panel-header .count { font-weight: 400; opacity: 0.8; font-size: 0.9rem; }
  .panel-content {
    flex: 1; overflow-y: auto; padding: 12px;
    background: #3a3f44;
  }
  .panel.right-panel .panel-header { background: #1565c0; }
  .panel.right-panel .panel-content { background: #455a64; }
  
  /* Section headers */
  h2.section-header {
    font-size: 1rem; margin: 16px 0 8px; padding: 6px 10px;
    background: #546e7a; color: #fff; border-radius: 4px;
    position: sticky; top: 0; z-index: 10;
  }
  h2.section-header:first-child { margin-top: 0; }
  .section-count { font-weight: 400; font-size: 0.85rem; opacity: 0.75; }
  
  /* Card styling */
  .card {
    border-radius: 6px; padding: 12px; margin-bottom: 8px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.15); cursor: grab;
    transition: transform 0.15s, box-shadow 0.15s, opacity 0.2s;
    position: relative;
  }
  .card:hover { box-shadow: 0 3px 10px rgba(0,0,0,0.25); }
  .card.dragging { opacity: 0.5; transform: scale(0.98); }
  .card.selected { outline: 3px solid #ffeb3b; outline-offset: -3px; }
  .card.drag-over { outline: 3px dashed #1565c0; outline-offset: -3px; }
  
  .card-header { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; flex-wrap: wrap; }
  .story-type-pill {
    font-size: 0.65rem; font-weight: 600; padding: 2px 8px;
    border-radius: 8px; border: 1px solid rgba(0,0,0,0.15);
    white-space: nowrap;
  }
  .relevance-badge {
    font-size: 0.6rem; font-weight: 700; padding: 2px 6px;
    border-radius: 8px; color: #fff; margin-left: auto;
  }
  .rel-high { background: #e53935; }
  .rel-medium { background: #fb8c00; }
  .rel-low { background: #9e9e9e; }
  
  .card-title { font-size: 0.9rem; font-weight: 600; margin-bottom: 4px; }
  .card-url { font-size: 0.7rem; color: #1565c0; margin-bottom: 6px; word-break: break-all; }
  .card-url a { color: #1565c0; text-decoration: none; }
  .card-url a:hover { text-decoration: underline; }
  .card-body { font-size: 0.8rem; line-height: 1.4; }
  .card-body p { margin: 4px 0; }
  .card-body .bullet { margin-left: 16px; display: flex; gap: 6px; }
  .card-body .bullet span:first-child { flex: 0 0 10px; }
  
  /* Aux cards */
  .aux-card {
    background: #fff; border: 1px solid #ddd; border-radius: 6px;
    padding: 10px 12px; margin-bottom: 8px; cursor: grab;
    transition: transform 0.15s, box-shadow 0.15s;
  }
  .aux-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.18); }
  .aux-card.selected { outline: 3px solid #ffeb3b; outline-offset: -3px; }
  .aux-card.dragging { opacity: 0.5; }
  .aux-title { font-size: 0.9rem; font-weight: 600; margin-bottom: 2px; }
  .aux-date { font-size: 0.75rem; color: #555; margin-bottom: 4px; }
  .aux-url { font-size: 0.7rem; margin-bottom: 6px; word-break: break-all; }
  .aux-url a { color: #1565c0; text-decoration: none; }
  .aux-desc { font-size: 0.8rem; line-height: 1.4; }
  
  /* Drop zone indicator */
  .drop-zone {
    border: 2px dashed #78909c; border-radius: 6px;
    padding: 20px; text-align: center; color: #90a4ae;
    margin: 8px 0; min-height: 60px;
    display: flex; align-items: center; justify-content: center;
  }
  .drop-zone.drag-active { border-color: #1565c0; background: rgba(21,101,192,0.1); color: #1565c0; }
  
  /* Context menu */
  .ctx-menu {
    position: fixed; background: #fff; border: 1px solid #ccc;
    border-radius: 6px; box-shadow: 0 4px 14px rgba(0,0,0,0.2);
    z-index: 9999; padding: 4px 0; min-width: 150px; display: none;
  }
  .ctx-menu div {
    padding: 8px 14px; cursor: pointer; font-size: 0.85rem;
  }
  .ctx-menu div:hover { background: #e3f2fd; }
  .ctx-menu .separator { border-top: 1px solid #eee; margin: 4px 0; padding: 0; }
  
  /* Generate button */
  .gen-bar {
    padding: 12px; background: #263238; border-top: 2px solid #1a1d20;
    text-align: center;
  }
  .gen-btn {
    background: #43a047; color: #fff; border: none;
    padding: 10px 28px; font-size: 0.95rem; font-weight: 600;
    border-radius: 6px; cursor: pointer;
  }
  .gen-btn:hover { background: #388e3c; }
  .gen-btn:disabled { background: #78909c; cursor: not-allowed; }
  .clear-btn {
    background: #e53935; color: #fff; border: none;
    padding: 10px 20px; font-size: 0.95rem; font-weight: 600;
    border-radius: 6px; cursor: pointer; margin-right: 12px;
  }
  .clear-btn:hover { background: #c62828; }
  .clear-btn:disabled { background: #78909c; cursor: not-allowed; }
  
  /* Empty state */
  .empty-state {
    text-align: center; color: #90a4ae; padding: 40px 20px;
    font-style: italic;
  }
  
  /* Keyboard hint */
  .kbd-hint {
    font-size: 0.7rem; color: #90a4ae; padding: 8px 12px;
    background: #2d3136; border-top: 1px solid #37474f;
  }
</style>
</head>
<body>

<div class="main-container">
  <!-- LEFT PANEL: All Items -->
  <div class="panel left-panel">
    <div class="panel-header">
      <span>Available Items</span>
      <span class="count" id="leftCount">0 items</span>
    </div>
    <div class="panel-content" id="leftPanel"></div>
    <div class="kbd-hint">Click to select | Drag or → to include | Right-click for menu</div>
  </div>
  
  <!-- RIGHT PANEL: Selected Items -->
  <div class="panel right-panel">
    <div class="panel-header">
      <span>Selected for Export</span>
      <span class="count" id="rightCount">0 items</span>
    </div>
    <div class="panel-content" id="rightPanel">
      <div class="drop-zone" id="dropZone">Drag items here to include</div>
    </div>
    <div class="gen-bar">
      <button class="clear-btn" id="clearBtn" onclick="clearAll()">Clear All</button>
      <button class="gen-btn" id="genBtn" onclick="generateDoc()">Generate Doc</button>
    </div>
    <div class="kbd-hint">↑/↓ to reorder | ← to exclude | Right-click for menu</div>
  </div>
</div>

<!-- Context Menu -->
<div class="ctx-menu" id="ctxMenu">
  <div id="ctxInclude">Include →</div>
  <div id="ctxExclude">← Exclude</div>
  <div class="separator"></div>
  <div id="ctxMoveUp">Move Up ↑</div>
  <div id="ctxMoveDown">Move Down ↓</div>
</div>

<script>
/* ====== Data ====== */
const RAW_DATA = ${dataJSON};
const EVENTS_DATA = ${eventsJSON};
const JOBS_DATA = ${jobsJSON};
const STORY_BY_ID = new Map(RAW_DATA.map((s) => [s.id, s]));
const EVENTS_BY_ID = new Map(EVENTS_DATA.map((e) => [e.id, e]));
const JOBS_BY_ID = new Map(JOBS_DATA.map((j) => [j.id, j]));

/* ====== Type colors ====== */
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
function darkenColor(hex, factor) {
  const f = factor || 0.65;
  const r = parseInt(hex.slice(1,3),16);
  const g = parseInt(hex.slice(3,5),16);
  const b = parseInt(hex.slice(5,7),16);
  return '#' + [Math.round(r*f),Math.round(g*f),Math.round(b*f)].map(c => c.toString(16).padStart(2,'0')).join('');
}

/* ====== Ordering ====== */
const JURIS_ORDER = [
  "Australian National Scope","Australian Capital Territory","NSW","Northern Territory",
  "Queensland","South Australia","Tasmania","Victoria","Western Australia","International"
];
const JURIS_SHORT = {
  "Australian National Scope": "Australian National",
  "Australian Capital Territory": "ACT",
  "Northern Territory": "NT",
  "South Australia": "SA",
  "Tasmania": "TAS",
  "Victoria": "VIC",
  "Western Australia": "WA",
};
const TYPE_ORDER = [
  "Legislative and Statutory Developments","Parliamentary and Political Proceedings",
  "Compliance, Oversight, and Enforcement Actions","Consultation and Policy Design Opportunities",
  "Funding and Grant Announcements","Corporate and Institutional ESG Actions",
  "State and Local Government Programs","Infrastructure, Project Approvals, and EPBC Developments",
  "Reports, Data Releases, and Analytical Insights","Ministerial, Diplomatic, and International Engagements",
  "Environmental Protection, Biodiversity, and Nature Policy",
  "Community, First Nations, and Social Licence Initiatives","Misc",
];
const TYPE_RANK = {}; TYPE_ORDER.forEach((t,i) => TYPE_RANK[t] = i);
const REL_PRIORITY = { High: 0, Medium: 1, Low: 2 };

/* ====== State ====== */
let leftStories = [];   // IDs in left panel
let rightStories = [];  // IDs in right panel (selected)
let leftEvents = [];
let rightEvents = [];
let leftJobs = [];
let rightJobs = [];
let selectedEl = null;
let draggedEl = null;
let draggedData = null;

function initData() {
  // Sort stories by jurisdiction > type > relevance
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
  leftStories = [];
  for (const jur of JURIS_ORDER) {
    if (!grouped[jur]) continue;
    const types = Object.keys(grouped[jur]).sort((a,b) => (TYPE_RANK[a]??999) - (TYPE_RANK[b]??999));
    for (const st of types) {
      for (const s of grouped[jur][st]) leftStories.push(s.id);
    }
    delete grouped[jur];
  }
  for (const jur of Object.keys(grouped).sort()) {
    const types = Object.keys(grouped[jur]).sort((a,b) => (TYPE_RANK[a]??999) - (TYPE_RANK[b]??999));
    for (const st of types) {
      for (const s of grouped[jur][st]) leftStories.push(s.id);
    }
  }
  leftEvents = EVENTS_DATA.map(e => e.id);
  leftJobs = JOBS_DATA.map(j => j.id);
  rightStories = [];
  rightEvents = [];
  rightJobs = [];
}
initData();

/* ====== Helpers ====== */
function esc(s) {
  return String(s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}
function hasLeadingBullet(line) { return /^\\s*[\\u2022*\\-\\u2013\\u2014]/.test(line || ""); }
function stripBullet(line) { return String(line || "").replace(/^\\s*[\\u2022*\\-\\u2013\\u2014]\\s*/, "").trim(); }
function pHtml(text) { return '<p>' + esc(text) + '</p>'; }
function bulletHtml(text) { return '<p class="bullet"><span>&bull;</span><span>' + esc(text) + '</span></p>'; }
function narrativeHtml(parts) { return '<p>' + parts.filter(Boolean).join(' ') + '</p>'; }

function buildCardBody(s) {
  const outputs = [s.Out1, s.Out2, s.Out3, s.Out4, s.Out5, s.Out6]
    .map(value => String(value || "").trim())
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

function renderStoryCard(s, panel) {
  const bg = typeColor(s.Story_Type);
  const pillBg = darkenColor(bg, 0.65);
  const relClass = s.ESG_Relevance === 'High' ? 'rel-high' : s.ESG_Relevance === 'Medium' ? 'rel-medium' : 'rel-low';
  return '<div class="card" draggable="true" data-type="story" data-id="' + s.id + '" data-panel="' + panel + '" style="background:' + bg + ';">' +
    '<div class="card-header">' +
    '<span class="story-type-pill" style="background:' + pillBg + ';color:#fff;">' + esc(s.Story_Type) + '</span>' +
    '<span class="relevance-badge ' + relClass + '">' + esc(s.ESG_Relevance) + '</span>' +
    '</div>' +
    '<div class="card-title">' + esc(s.Title) + '</div>' +
    '<div class="card-body">' + buildCardBody(s) + '</div>' +
    '</div>';
}

function renderAuxCard(item, type, panel) {
  const descKey = type === 'event' ? 'Event_Description' : 'Job_Description';
  return '<div class="aux-card" draggable="true" data-type="' + type + '" data-id="' + item.id + '" data-panel="' + panel + '">' +
    '<div class="aux-date">' + esc(item.Date) + '</div>' +
    '<div class="aux-title">' + esc(item.Title) + '</div>' +
    '<div class="aux-url"><a href="' + esc(item.URL) + '" target="_blank">' + esc(item.URL) + '</a></div>' +
    '<div class="aux-desc">' + esc(item[descKey] || '') + '</div>' +
    '</div>';
}

function renderLeftPanel() {
  const root = document.getElementById('leftPanel');
  let html = '';
  
  // Stories grouped by jurisdiction
  const grouped = {};
  for (const id of leftStories) {
    const s = STORY_BY_ID.get(id);
    if (!s) continue;
    const j = s.Jurisdiction || "Other";
    if (!grouped[j]) grouped[j] = [];
    grouped[j].push(s);
  }
  
  for (const jur of JURIS_ORDER) {
    if (!grouped[jur] || grouped[jur].length === 0) continue;
    html += '<h2 class="section-header">' + esc(jur) + ' <span class="section-count">(' + grouped[jur].length + ')</span></h2>';
    for (const s of grouped[jur]) {
      html += renderStoryCard(s, 'left');
    }
    delete grouped[jur];
  }
  for (const jur of Object.keys(grouped).sort()) {
    html += '<h2 class="section-header">' + esc(jur) + ' <span class="section-count">(' + grouped[jur].length + ')</span></h2>';
    for (const s of grouped[jur]) {
      html += renderStoryCard(s, 'left');
    }
  }
  
  // Events
  if (leftEvents.length > 0) {
    html += '<h2 class="section-header">Events <span class="section-count">(' + leftEvents.length + ')</span></h2>';
    for (const id of leftEvents) {
      const e = EVENTS_BY_ID.get(id);
      if (e) html += renderAuxCard(e, 'event', 'left');
    }
  }
  
  // Jobs
  if (leftJobs.length > 0) {
    html += '<h2 class="section-header">Jobs <span class="section-count">(' + leftJobs.length + ')</span></h2>';
    for (const id of leftJobs) {
      const j = JOBS_BY_ID.get(id);
      if (j) html += renderAuxCard(j, 'job', 'left');
    }
  }
  
  if (!html) html = '<div class="empty-state">All items have been selected</div>';
  root.innerHTML = html;
  document.getElementById('leftCount').textContent = (leftStories.length + leftEvents.length + leftJobs.length) + ' items';
}

function renderRightPanel() {
  const root = document.getElementById('rightPanel');
  const total = rightStories.length + rightEvents.length + rightJobs.length;
  
  let html = '';
  if (total === 0) {
    html = '<div class="drop-zone" id="dropZone">Drag items here to include</div>';
  } else {
    // Stories grouped by jurisdiction
    const grouped = {};
    for (const id of rightStories) {
      const s = STORY_BY_ID.get(id);
      if (!s) continue;
      const j = s.Jurisdiction || "Other";
      if (!grouped[j]) grouped[j] = [];
      grouped[j].push(s);
    }
    
    let hasStories = false;
    for (const jur of JURIS_ORDER) {
      if (!grouped[jur] || grouped[jur].length === 0) continue;
      hasStories = true;
      html += '<h2 class="section-header">' + esc(jur) + ' <span class="section-count">(' + grouped[jur].length + ')</span></h2>';
      for (const s of grouped[jur]) {
        html += renderStoryCard(s, 'right');
      }
      delete grouped[jur];
    }
    for (const jur of Object.keys(grouped).sort()) {
      hasStories = true;
      html += '<h2 class="section-header">' + esc(jur) + ' <span class="section-count">(' + grouped[jur].length + ')</span></h2>';
      for (const s of grouped[jur]) {
        html += renderStoryCard(s, 'right');
      }
    }
    
    if (rightEvents.length > 0) {
      html += '<h2 class="section-header">Events <span class="section-count">(' + rightEvents.length + ')</span></h2>';
      for (const id of rightEvents) {
        const e = EVENTS_BY_ID.get(id);
        if (e) html += renderAuxCard(e, 'event', 'right');
      }
    }
    
    if (rightJobs.length > 0) {
      html += '<h2 class="section-header">Jobs <span class="section-count">(' + rightJobs.length + ')</span></h2>';
      for (const id of rightJobs) {
        const j = JOBS_BY_ID.get(id);
        if (j) html += renderAuxCard(j, 'job', 'right');
      }
    }
  }
  
  root.innerHTML = html;
  let countText = total + ' items';
  if (total > 0) {
    const parts = [];
    if (rightStories.length > 0) parts.push('Stories: ' + rightStories.length);
    if (rightEvents.length > 0) parts.push('Events: ' + rightEvents.length);
    if (rightJobs.length > 0) parts.push('Jobs: ' + rightJobs.length);
    countText += ' (' + parts.join(', ') + ')';
  }
  document.getElementById('rightCount').textContent = countText;
  document.getElementById('genBtn').disabled = total === 0;
  document.getElementById('clearBtn').disabled = total === 0;
}

function render() {
  renderLeftPanel();
  renderRightPanel();
}

function clearAll() {
  leftStories = leftStories.concat(rightStories);
  leftEvents = leftEvents.concat(rightEvents);
  leftJobs = leftJobs.concat(rightJobs);
  rightStories = [];
  rightEvents = [];
  rightJobs = [];
  render();
}

/* ====== Selection ====== */
function selectCard(el) {
  if (selectedEl) selectedEl.classList.remove('selected');
  selectedEl = el;
  if (el) el.classList.add('selected');
}

document.addEventListener('click', (e) => {
  const card = e.target.closest('.card, .aux-card');
  if (card) {
    selectCard(card);
    e.stopPropagation();
  } else if (!e.target.closest('.ctx-menu')) {
    selectCard(null);
  }
});

/* ====== Drag and Drop ====== */
document.addEventListener('dragstart', (e) => {
  const card = e.target.closest('.card, .aux-card');
  if (!card) return;
  draggedEl = card;
  draggedData = {
    type: card.dataset.type,
    id: parseInt(card.dataset.id),
    panel: card.dataset.panel
  };
  card.classList.add('dragging');
  selectCard(card);
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', JSON.stringify(draggedData));
});

document.addEventListener('dragend', (e) => {
  if (draggedEl) draggedEl.classList.remove('dragging');
  draggedEl = null;
  draggedData = null;
  document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
  document.querySelectorAll('.drag-active').forEach(el => el.classList.remove('drag-active'));
});

document.addEventListener('dragover', (e) => {
  e.preventDefault();
  const rightPanel = document.getElementById('rightPanel');
  const dropZone = document.getElementById('dropZone');
  
  if (rightPanel.contains(e.target)) {
    e.dataTransfer.dropEffect = 'move';
    if (dropZone) dropZone.classList.add('drag-active');
    
    // Reorder within right panel
    const card = e.target.closest('.card, .aux-card');
    if (card && draggedEl && card !== draggedEl && card.dataset.panel === 'right') {
      document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
      card.classList.add('drag-over');
    }
  } else {
    if (dropZone) dropZone.classList.remove('drag-active');
  }
});

document.addEventListener('dragleave', (e) => {
  const dropZone = document.getElementById('dropZone');
  if (dropZone && !document.getElementById('rightPanel').contains(e.relatedTarget)) {
    dropZone.classList.remove('drag-active');
  }
});

document.addEventListener('drop', (e) => {
  e.preventDefault();
  const rightPanel = document.getElementById('rightPanel');
  const dropZone = document.getElementById('dropZone');
  if (dropZone) dropZone.classList.remove('drag-active');
  document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
  
  if (!draggedData) return;
  
  // Dropping on right panel = include
  if (rightPanel.contains(e.target)) {
    if (draggedData.panel === 'left') {
      moveToRight(draggedData.type, draggedData.id);
    } else {
      // Reorder within right
      const targetCard = e.target.closest('.card, .aux-card');
      if (targetCard && targetCard !== draggedEl) {
        reorderInRight(draggedData.type, draggedData.id, parseInt(targetCard.dataset.id), targetCard.dataset.type);
      }
    }
  }
  // Dropping on left panel = exclude
  else if (document.getElementById('leftPanel').contains(e.target)) {
    if (draggedData.panel === 'right') {
      moveToLeft(draggedData.type, draggedData.id);
    }
  }
});

/* ====== Move Functions ====== */
function moveToRight(type, id) {
  if (type === 'story') {
    const idx = leftStories.indexOf(id);
    if (idx > -1) { leftStories.splice(idx, 1); rightStories.push(id); }
  } else if (type === 'event') {
    const idx = leftEvents.indexOf(id);
    if (idx > -1) { leftEvents.splice(idx, 1); rightEvents.push(id); }
  } else if (type === 'job') {
    const idx = leftJobs.indexOf(id);
    if (idx > -1) { leftJobs.splice(idx, 1); rightJobs.push(id); }
  }
  render();
}

function moveToLeft(type, id) {
  if (type === 'story') {
    const idx = rightStories.indexOf(id);
    if (idx > -1) { rightStories.splice(idx, 1); leftStories.push(id); }
  } else if (type === 'event') {
    const idx = rightEvents.indexOf(id);
    if (idx > -1) { rightEvents.splice(idx, 1); leftEvents.push(id); }
  } else if (type === 'job') {
    const idx = rightJobs.indexOf(id);
    if (idx > -1) { rightJobs.splice(idx, 1); leftJobs.push(id); }
  }
  render();
}

function reorderInRight(type, id, targetId, targetType) {
  let arr;
  if (type === 'story') arr = rightStories;
  else if (type === 'event') arr = rightEvents;
  else arr = rightJobs;
  
  const fromIdx = arr.indexOf(id);
  if (fromIdx === -1) return;
  
  // For same type, move to target position
  if (type === targetType) {
    const toIdx = arr.indexOf(targetId);
    if (toIdx === -1) return;
    arr.splice(fromIdx, 1);
    arr.splice(toIdx, 0, id);
    render();
  }
}

function moveUp(type, id) {
  let arr;
  if (type === 'story') arr = rightStories;
  else if (type === 'event') arr = rightEvents;
  else arr = rightJobs;
  
  const idx = arr.indexOf(id);
  if (idx > 0) {
    [arr[idx], arr[idx-1]] = [arr[idx-1], arr[idx]];
    render();
    setTimeout(() => {
      const el = document.querySelector('[data-panel="right"][data-type="' + type + '"][data-id="' + id + '"]');
      if (el) selectCard(el);
    }, 10);
  }
}

function moveDown(type, id) {
  let arr;
  if (type === 'story') arr = rightStories;
  else if (type === 'event') arr = rightEvents;
  else arr = rightJobs;
  
  const idx = arr.indexOf(id);
  if (idx < arr.length - 1 && idx > -1) {
    [arr[idx], arr[idx+1]] = [arr[idx+1], arr[idx]];
    render();
    setTimeout(() => {
      const el = document.querySelector('[data-panel="right"][data-type="' + type + '"][data-id="' + id + '"]');
      if (el) selectCard(el);
    }, 10);
  }
}

/* ====== Context Menu ====== */
const ctxMenu = document.getElementById('ctxMenu');
let ctxData = null;

document.addEventListener('contextmenu', (e) => {
  const card = e.target.closest('.card, .aux-card');
  if (!card) { ctxMenu.style.display = 'none'; return; }
  
  e.preventDefault();
  selectCard(card);
  
  ctxData = {
    type: card.dataset.type,
    id: parseInt(card.dataset.id),
    panel: card.dataset.panel
  };
  
  // Show/hide relevant options
  const isLeft = ctxData.panel === 'left';
  document.getElementById('ctxInclude').style.display = isLeft ? 'block' : 'none';
  document.getElementById('ctxExclude').style.display = isLeft ? 'none' : 'block';
  document.getElementById('ctxMoveUp').style.display = isLeft ? 'none' : 'block';
  document.getElementById('ctxMoveDown').style.display = isLeft ? 'none' : 'block';
  
  ctxMenu.style.left = e.clientX + 'px';
  ctxMenu.style.top = e.clientY + 'px';
  ctxMenu.style.display = 'block';
});

document.addEventListener('click', () => { ctxMenu.style.display = 'none'; });

document.getElementById('ctxInclude').addEventListener('click', () => {
  if (ctxData) moveToRight(ctxData.type, ctxData.id);
  ctxMenu.style.display = 'none';
});

document.getElementById('ctxExclude').addEventListener('click', () => {
  if (ctxData) moveToLeft(ctxData.type, ctxData.id);
  ctxMenu.style.display = 'none';
});

document.getElementById('ctxMoveUp').addEventListener('click', () => {
  if (ctxData) moveUp(ctxData.type, ctxData.id);
  ctxMenu.style.display = 'none';
});

document.getElementById('ctxMoveDown').addEventListener('click', () => {
  if (ctxData) moveDown(ctxData.type, ctxData.id);
  ctxMenu.style.display = 'none';
});

/* ====== Keyboard Navigation ====== */
document.addEventListener('keydown', (e) => {
  if (!selectedEl) return;
  
  const type = selectedEl.dataset.type;
  const id = parseInt(selectedEl.dataset.id);
  const panel = selectedEl.dataset.panel;
  
  switch (e.key) {
    case 'ArrowRight':
      if (panel === 'left') {
        moveToRight(type, id);
        setTimeout(() => {
          const el = document.querySelector('[data-panel="right"][data-type="' + type + '"][data-id="' + id + '"]');
          if (el) selectCard(el);
        }, 10);
      }
      e.preventDefault();
      break;
    case 'ArrowLeft':
      if (panel === 'right') {
        moveToLeft(type, id);
        setTimeout(() => {
          const el = document.querySelector('[data-panel="left"][data-type="' + type + '"][data-id="' + id + '"]');
          if (el) selectCard(el);
        }, 10);
      }
      e.preventDefault();
      break;
    case 'ArrowUp':
      if (panel === 'right') {
        moveUp(type, id);
        e.preventDefault();
      }
      break;
    case 'ArrowDown':
      if (panel === 'right') {
        moveDown(type, id);
        e.preventDefault();
      }
      break;
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
  const total = rightStories.length + rightEvents.length + rightJobs.length;
  if (total === 0) { alert('No items selected for export.'); return; }
  
  let md = '';
  
  // Stories grouped by jurisdiction
  const grouped = {};
  for (const id of rightStories) {
    const s = STORY_BY_ID.get(id);
    if (!s) continue;
    const j = s.Jurisdiction || "Other";
    if (!grouped[j]) grouped[j] = [];
    grouped[j].push(s);
  }
  
  // === HIGHLIGHTS SECTION (Hook + One Liner at top) ===
  let highlights = '';
  for (const jur of JURIS_ORDER) {
    if (!grouped[jur] || grouped[jur].length === 0) continue;
    let jurHighlights = '';
    for (const s of grouped[jur]) {
      if (s.Hook || s.OneLiner) {
        if (s.Hook) jurHighlights += '**' + s.Hook + '** ';
        if (s.OneLiner) jurHighlights += s.OneLiner;
        jurHighlights += '\\n\\n';
      }
    }
    if (jurHighlights) {
      highlights += '## ' + (JURIS_SHORT[jur] || jur) + '\\n\\n' + jurHighlights;
    }
  }
  for (const jur of Object.keys(grouped).sort()) {
    if (JURIS_ORDER.includes(jur)) continue;
    let jurHighlights = '';
    for (const s of grouped[jur]) {
      if (s.Hook || s.OneLiner) {
        if (s.Hook) jurHighlights += '**' + s.Hook + '** ';
        if (s.OneLiner) jurHighlights += s.OneLiner;
        jurHighlights += '\\n\\n';
      }
    }
    if (jurHighlights) {
      highlights += '## ' + (JURIS_SHORT[jur] || jur) + '\\n\\n' + jurHighlights;
    }
  }
  if (highlights) {
    md += '# Highlights\\n\\n' + highlights + '---\\n\\n';
  }
  
  // === MAIN CONTENT (without Hook + One Liner) ===
  for (const jur of JURIS_ORDER) {
    if (!grouped[jur] || grouped[jur].length === 0) continue;
    md += '# ' + (JURIS_SHORT[jur] || jur) + '\\n\\n';
    for (const s of grouped[jur]) {
      const body = buildMdBody(s);
      if (body) md += body + '\\n\\n';
      md += '---\\n\\n';
    }
    delete grouped[jur];
  }
  for (const jur of Object.keys(grouped).sort()) {
    md += '# ' + (JURIS_SHORT[jur] || jur) + '\\n\\n';
    for (const s of grouped[jur]) {
      const body = buildMdBody(s);
      if (body) md += body + '\\n\\n';
      md += '---\\n\\n';
    }
  }
  
  // Events
  if (rightEvents.length > 0) {
    md += '# Events\\n\\n';
    for (const id of rightEvents) {
      const ev = EVENTS_BY_ID.get(id);
      if (!ev) continue;
      if (ev.Event_Description) md += ev.Event_Description + '\\n\\n';
      md += '---\\n\\n';
    }
  }
  
  // Jobs
  if (rightJobs.length > 0) {
    md += '# Jobs\\n\\n';
    for (const id of rightJobs) {
      const jb = JOBS_BY_ID.get(id);
      if (!jb) continue;
      if (jb.Job_Description) md += jb.Job_Description + '\\n\\n';
      md += '---\\n\\n';
    }
  }
  
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
  saveAs(blob, 'selected_stories.md');
}

/* ====== Init ====== */
render();
<\/script>
</body>
</html>`;

/* ====== Write output ====== */
fs.writeFileSync(OUTPUT_HTML, html, "utf8");
console.log(`[dashboard2] ✅ Wrote ${OUTPUT_HTML}`);
console.log(`[dashboard2] Open Dashboard/index2.html in a browser to use.`);

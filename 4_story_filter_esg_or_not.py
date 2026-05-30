import os
import re
import csv
import json
import glob
import time
import chardet
import argparse
import yaml
import asyncio
import aiohttp
from datetime import datetime, timedelta
from dateutil import parser as dateparser
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from aiolimiter import AsyncLimiter  # ✅ rate limiter

# ----------------- CLI -----------------
parser = argparse.ArgumentParser(
    description="Classify .md files concurrently and write a CSV with ESG flag only."
)
group = parser.add_mutually_exclusive_group(required=False)
group.add_argument("--OAI", action="store_true", help="OpenAI, no web")
group.add_argument("--OAIW", action="store_true", help="OpenAI, with web")
group.add_argument("--OLR", action="store_true", help="Ollama reasoning")
group.add_argument("--OLNR", action="store_true", help="Ollama non-reasoning")
args = parser.parse_args()

# ✅ Default to OAI
if args.OAI:
    PROFILE = "OAI"
elif args.OAIW:
    PROFILE = "OAIW"
elif args.OLR:
    PROFILE = "OLR"
elif args.OLNR:
    PROFILE = "OLNR"
else:
    PROFILE = "OAI"

# ----------------- ENV -----------------
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# Paths
SOURCE_DIR = os.getenv(
    "SOURCE_DIR",
    os.path.join(PROJECT_ROOT, "source_md_files_cleaned")
)
OUTPUT_CSV = os.getenv(
    "OUTPUT_CSV",
    os.path.join(PROJECT_ROOT, "4_story_esg_or_not.csv")
)
INPUT_LINKS_CSV = os.getenv(  # NEW: CSV with Date,Title,URL,md_file
    "INPUT_LINKS_CSV",
    os.path.join(PROJECT_ROOT, "3_story_file_name_links.csv")
)  # NEW

cwd_source_dir = os.path.abspath("source_md_files_cleaned")
if "SOURCE_DIR" not in os.environ and os.path.isdir(cwd_source_dir) and glob.glob(os.path.join(cwd_source_dir, "*.md")):
    SOURCE_DIR = cwd_source_dir

cwd_links_csv = os.path.abspath("3_story_file_name_links.csv")
if "INPUT_LINKS_CSV" not in os.environ and os.path.exists(cwd_links_csv):
    INPUT_LINKS_CSV = cwd_links_csv

cwd_output_csv = os.path.abspath("4_story_esg_or_not.csv")
if "OUTPUT_CSV" not in os.environ and os.path.isdir(os.getcwd()):
    OUTPUT_CSV = cwd_output_csv

# API config
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_TIMEOUT_SECONDS = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "180"))

# 🔒 Rate limits (per-minute)
OPENAI_RPM = int(os.getenv("OPENAI_RPM", "10000"))  # Tier-4 default
rpm_limiter = AsyncLimiter(OPENAI_RPM, time_period=60)

# Per-profile configs
def get_env(name, default=None):
    return os.getenv(name, default)

def load_profile_config(profile: str):
    px = profile
    model_series = os.getenv("OPENAI_MODEL_SERIES", "gpt5").strip().lower()
    if model_series not in ("gpt5", "gpt4"):
        model_series = "gpt5"
    if model_series == "gpt4":
        model_name = get_env(f"{px}_MODEL_NAME_GPT4") or get_env(f"{px}_MODEL_NAME")
    else:
        model_name = get_env(f"{px}_MODEL_NAME_GPT5") or get_env(f"{px}_MODEL_NAME")
    cfg = {
        "MODEL_SERIES": model_series,
        "MODEL_NAME": model_name,
        "TEMPERATURE": get_env(f"{px}_TEMPERATURE"),
        "MAX_TOKENS": get_env(f"{px}_MAX_TOKENS"),
        "PROMPT_FILE": get_env(f"{px}_PROMPT_FILE", "4_story_filter_esg_or_not.yaml"),
    }
    if cfg["TEMPERATURE"] is not None:
        cfg["TEMPERATURE"] = float(cfg["TEMPERATURE"])
    if cfg["MAX_TOKENS"] is not None:
        cfg["MAX_TOKENS"] = int(float(cfg["MAX_TOKENS"]))
    return cfg

CFG = load_profile_config(PROFILE)
MODEL_SERIES = CFG["MODEL_SERIES"]
MODEL_NAME = CFG["MODEL_NAME"]
TEMPERATURE = CFG["TEMPERATURE"] if CFG["TEMPERATURE"] is not None else 0.0
MAX_TOKENS = CFG["MAX_TOKENS"] if CFG["MAX_TOKENS"] is not None else 2000
PROMPT_FILE = CFG["PROMPT_FILE"]

GPT5_REASONING_EFFORT = (get_env("OAI_REASONING_EFFORT", "low") or "low").strip().lower()
GPT5_TEXT_VERBOSITY = (get_env("OAI_TEXT_VERBOSITY", "medium") or "medium").strip().lower()

if not MODEL_NAME:
    raise SystemExit(f"[CONFIG ERROR] {PROFILE}_MODEL_NAME is not set in .env")

# ----------------- Prompt loading -----------------
PROMPT_FILE_PATH = os.path.join(PROJECT_ROOT, PROMPT_FILE)

def load_prompt_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    system = data.get("system", "")
    user_template = data.get("user_template", "{{markdown}}")
    hints = (data.get("profile_hints") or {}).get(PROFILE, "")
    return system, user_template, hints

SYSTEM_PROMPT, USER_TEMPLATE, PROFILE_HINT = load_prompt_yaml(PROMPT_FILE_PATH)
def render_user_prompt(md):
    return USER_TEMPLATE.replace("{{markdown}}", md)

# ----------------- Time window (unused) -----------------
TZ = ZoneInfo("Australia/Melbourne")
today_local = datetime.now(TZ).date()
start_date = today_local - timedelta(days=7)
end_date = today_local - timedelta(days=1)

# ----------------- Helpers -----------------
def read_text_file(path):
    with open(path, "rb") as f:
        raw = f.read()
    enc = chardet.detect(raw).get("encoding") or "utf-8"
    return raw.decode(enc, errors="replace")

def strip_code_fences(s: str):
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s.strip(), re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else s

def extract_responses_text(data):
    txt = (data.get("output_text") or "").strip()
    if txt:
        return txt
    out = data.get("output") or []
    chunks = []
    for item in out:
        for c in (item.get("content") or []):
            if c.get("type") in {"output_text", "text"} and c.get("text"):
                chunks.append(c["text"])
    return "\n".join(chunks).strip()

# NEW: load rows in-order from links CSV
def load_links_rows(csv_path):
    rows = []
    if not os.path.exists(csv_path):
        print(f"[WARN] INPUT_LINKS_CSV not found: {csv_path}")
        return rows
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"Date", "Title", "URL", "md_file"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            print("[WARN] INPUT_LINKS_CSV missing required columns: Date, Title, URL, md_file")
            return rows
        for row in reader:
            md = (row.get("md_file") or "").strip()
            rows.append({
                "Date": (row.get("Date") or "").strip(),
                "Title": (row.get("Title") or "").strip(),
                "URL": (row.get("URL") or "").strip(),
                "md_file": md,
                "ESG_or_not": (row.get("ESG_or_not") or "").strip(),
            })
    return rows
# ----------------- Async Calls -----------------
async def call_openai_async(session, markdown_text, with_web, file_name):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing")

    system_block = SYSTEM_PROMPT + ("\n\n" + PROFILE_HINT if PROFILE_HINT else "")
    if with_web:
        system_block += "\n\nNOTE: Web search not enabled."

    user_block = render_user_prompt(markdown_text)

    if MODEL_SERIES == "gpt5":
        url = f"{OPENAI_BASE_URL}/responses"
        payload = {
            "model": MODEL_NAME,
            "input": [
                {"role": "system", "content": system_block},
                {"role": "user", "content": user_block},
            ],
            "reasoning": {"effort": GPT5_REASONING_EFFORT},
            "text": {"verbosity": GPT5_TEXT_VERBOSITY},
            "max_output_tokens": MAX_TOKENS,
        }
    else:
        url = f"{OPENAI_BASE_URL}/chat/completions"
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "system", "content": system_block},
                {"role": "user", "content": user_block},
            ],
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
        }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    print(f"   📤 [{file_name}] Sending request to OpenAI...")

    async with rpm_limiter:  # ✅ global RPM guard
        async with session.post(url, json=payload, headers=headers, timeout=OPENAI_TIMEOUT_SECONDS) as resp:
            if resp.status in (429, 500, 502, 503, 504):
                retry_after = float(resp.headers.get("retry-after", "1"))
                text = await resp.text()
                print(f"   ⏳ [{file_name}] HTTP {resp.status}, retrying in {retry_after}s… [{text[:120]}]")
                await asyncio.sleep(retry_after)
                async with rpm_limiter:
                    async with session.post(url, json=payload, headers=headers, timeout=OPENAI_TIMEOUT_SECONDS) as resp2:
                        resp2.raise_for_status()
                        data = await resp2.json()
                        print(f"   📬 [{file_name}] Response received ({len(json.dumps(data))} bytes).")
                        if MODEL_SERIES == "gpt5":
                            content = extract_responses_text(data)
                        else:
                            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                        return strip_code_fences((content or "").strip())

            resp.raise_for_status()
            data = await resp.json()
            print(f"   📬 [{file_name}] Response received ({len(json.dumps(data))} bytes).")
            if MODEL_SERIES == "gpt5":
                content = extract_responses_text(data)
            else:
                content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return strip_code_fences((content or "").strip())

def extract_esg_only(raw):
    """
    Expecting a JSON object like:
      { "ESG_or_not": "Yes" | "No" }
    """
    text = (raw or "").strip()
    candidates = [text]

    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced and fenced.group(1):
        candidates.append(fenced.group(1).strip())

    obj = re.search(r"\{[\s\S]*?\}", text)
    if obj and obj.group(0):
        candidates.append(obj.group(0).strip())

    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                esg = (data.get("ESG_or_not") or "").strip().lower()
                if esg == "yes":
                    return {"ESG_or_not": "Yes"}
                if esg == "no":
                    return {"ESG_or_not": "No"}
        except:
            continue
    return None

# ----------------- Process one file -----------------
async def process_file_async(session, path, sem, idx, total):
    file_name = os.path.basename(path)
    async with sem:  # local concurrency limiter
        print(f"\n➡️  [{idx}/{total}] Starting: {file_name}")
        md_text = read_text_file(path)
        print(f"   📄 [{file_name}] File loaded ({len(md_text)} chars).")

        try:
            start_time = time.time()
            raw = await call_openai_async(session, md_text, with_web=(PROFILE == "OAIW"), file_name=file_name)
            duration = time.time() - start_time
            print(f"   ✅ [{file_name}] API call completed in {duration:.2f}s.")
        except Exception as e:
            print(f"   ❌ [{file_name}] API failed: {e}")
            return None

        if not raw.strip().startswith("{"):
            print(f"   ⚠️ [{file_name}] Non-JSON-object response snippet:\n{raw[:300]}")

        item = extract_esg_only(raw)
        if item is None:
            print(f"   ⚠️ [{file_name}] Could not parse ESG-only JSON.")
            return None

        print(f"   📊 [{file_name}] Parsed ESG_or_not='{item.get('ESG_or_not')}'.")
        item["File Name"] = file_name
        return item

# ----------------- Main Async -----------------
async def main_async():
    output_dir = os.path.dirname(OUTPUT_CSV)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # NEW: load rows once (preserve original CSV order)
    links_rows = load_links_rows(INPUT_LINKS_CSV)

    # Build API candidate files strictly from links CSV rows that have an existing .md file
    candidate_paths = []
    missing_md_in_links = []
    skipped_cleaned = 0
    for meta in links_rows:
        md_file = (meta.get("md_file") or "").strip()
        if not md_file or md_file.upper() == "CLEANED":
            skipped_cleaned += 1
            continue
        md_path = os.path.join(SOURCE_DIR, md_file)
        if os.path.exists(md_path):
            candidate_paths.append(md_path)
        else:
            missing_md_in_links.append(md_file)

    missing_md_set = set(missing_md_in_links)

    print(f"[PROFILE] {PROFILE} | SERIES={MODEL_SERIES} | MODEL={MODEL_NAME} | TEMP={TEMPERATURE} | MAX_TOKENS={MAX_TOKENS}")
    print(f"[PROMPT] {PROMPT_FILE_PATH}")
    print(f"[INPUT ] {SOURCE_DIR}")
    print(f"[MAP   ] {INPUT_LINKS_CSV}")  # NEW
    print(f"[OUTPUT] {OUTPUT_CSV}")
    print(f"[LIMITS] OPENAI_RPM={OPENAI_RPM} req/min | OPENAI_TIMEOUT_SECONDS={OPENAI_TIMEOUT_SECONDS}s")
    if skipped_cleaned:
        print(f"[INFO] {skipped_cleaned} rows marked md_file='CLEANED' and excluded from API calls.")
    if missing_md_in_links:
        print(f"[INFO] {len(missing_md_in_links)} mapped .md files are missing and will be set to ESG_or_not='NO FILE'.")

    CONCURRENCY_LIMIT = int(os.getenv("OPENAI_CONCURRENCY", os.getenv("LOCAL_CONCURRENCY", "16")))
    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)

    rows = []
    if candidate_paths:
        async with aiohttp.ClientSession() as session:
            tasks = [
                process_file_async(session, path, sem, idx=i + 1, total=len(candidate_paths))
                for i, path in enumerate(candidate_paths)
            ]
            results = await asyncio.gather(*tasks)
        rows = [r for r in results if r is not None]
    else:
        print("[INFO] No mapped .md files found in source directory; rows will default to ESG_or_not='No'.")

    ai_by_md = {r.get("File Name", ""): r.get("ESG_or_not", "") for r in rows if r.get("File Name")}

    print("\n📁 Writing results to CSV...")
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        # NEW: include Date, Title, URL, md_file alongside AI result
        fieldnames = ["Date", "Title", "URL", "md_file", "ESG_or_not"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for meta in links_rows:
            md_file = (meta.get("md_file") or "").strip()
            if not md_file or md_file.upper() == "CLEANED":
                esg_val = "NO FILE"
            elif md_file in missing_md_set:
                esg_val = "NO FILE"
            else:
                esg_val = ai_by_md.get(md_file, "No")
                if esg_val not in ("Yes", "No", "NO FILE"):
                    esg_val = "No"
            writer.writerow({
                "Date": meta.get("Date", ""),
                "Title": meta.get("Title", ""),
                "URL": meta.get("URL", ""),
                "md_file": meta.get("md_file", md_file),
                "ESG_or_not": esg_val,
            })

    print(f"\n✅ [DONE] Wrote {len(links_rows)} rows from {len(candidate_paths)} mapped files → {OUTPUT_CSV}")

if __name__ == "__main__":
    asyncio.run(main_async())

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
load_dotenv()

# Paths
SOURCE_DIR = os.getenv(
    "SOURCE_DIR",
    r"story_md_files"
)
OUTPUT_CSV = os.getenv(
    "OUTPUT_CSV",
    r"4_story_esg_or_not.csv"
)
INPUT_LINKS_CSV = os.getenv(  # NEW: CSV with Date,Title,URL,md_file
    "INPUT_LINKS_CSV",
    r"3_story_file_name_links.csv"
)  # NEW

# API config
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

# 🔒 Rate limits (per-minute)
OPENAI_RPM = int(os.getenv("OPENAI_RPM", "10000"))  # Tier-4 default
rpm_limiter = AsyncLimiter(OPENAI_RPM, time_period=60)

# Per-profile configs
def get_env(name, default=None):
    return os.getenv(name, default)

def load_profile_config(profile: str):
    px = profile
    cfg = {
        "MODEL_NAME": get_env(f"{px}_MODEL_NAME"),
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
MODEL_NAME = CFG["MODEL_NAME"]
TEMPERATURE = CFG["TEMPERATURE"] if CFG["TEMPERATURE"] is not None else 0.0
MAX_TOKENS = CFG["MAX_TOKENS"] if CFG["MAX_TOKENS"] is not None else 2000
PROMPT_FILE = CFG["PROMPT_FILE"]

if not MODEL_NAME:
    raise SystemExit(f"[CONFIG ERROR] {PROFILE}_MODEL_NAME is not set in .env")

# ----------------- Prompt loading -----------------
PROMPT_FILE_PATH = r"4_story_filter_esg_or_not.yaml"

def load_prompt_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    system = data.get("system", "")
    user_template = data.get("user_template", "{{markdown}}")
    hints = (data.get("profile_hints") or {}).get(PROFILE, "")
    return system, user_template, hints

SYSTEM_PROMPT, USER_TEMPLATE, PROFILE_HINT = load_prompt_yaml(PROMPT_FILE_PATH)

# 🔧 Minimal-change override: ESG-only (ignore jurisdiction entirely)
SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + "\n\nOVERRIDE: Ignore any instructions about 'Jurisdiction'. "
      "Only perform task (1) and return STRICT JSON with exactly one key: "
      "{\"ESG_or_not\":\"Yes\"|\"No\"}."
)
def render_user_prompt(md):
    return (
        "---- INPUT MARKDOWN (Begin) ----\n"
        f"{md}\n"
        "---- INPUT MARKDOWN (End) ----\n\n"
        "Return STRICT JSON now with exactly one key:\n"
        "{ \"ESG_or_not\": \"Yes\" | \"No\" }"
    )

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

# NEW: load mapping (md_file -> {Date, Title, URL, md_file})
def load_links_mapping(csv_path):
    mapping = {}
    if not os.path.exists(csv_path):
        print(f"[WARN] INPUT_LINKS_CSV not found: {csv_path}")
        return mapping
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"Date", "Title", "URL", "md_file"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            print("[WARN] INPUT_LINKS_CSV missing required columns: Date, Title, URL, md_file")
            return mapping
        for row in reader:
            md = (row.get("md_file") or "").strip()
            if md:
                mapping[md] = {
                    "Date": (row.get("Date") or "").strip(),
                    "Title": (row.get("Title") or "").strip(),
                    "URL": (row.get("URL") or "").strip(),
                    "md_file": md,
                }
    return mapping
# ----------------- Async Calls -----------------
async def call_openai_async(session, markdown_text, with_web, file_name):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing")

    system_block = SYSTEM_PROMPT + ("\n\n" + PROFILE_HINT if PROFILE_HINT else "")
    if with_web:
        system_block += "\n\nNOTE: Web search not enabled."

    user_block = render_user_prompt(markdown_text)

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
        async with session.post(url, json=payload, headers=headers, timeout=180) as resp:
            if resp.status in (429, 500, 502, 503, 504):
                retry_after = float(resp.headers.get("retry-after", "1"))
                text = await resp.text()
                print(f"   ⏳ [{file_name}] HTTP {resp.status}, retrying in {retry_after}s… [{text[:120]}]")
                await asyncio.sleep(retry_after)
                async with rpm_limiter:
                    async with session.post(url, json=payload, headers=headers, timeout=180) as resp2:
                        resp2.raise_for_status()
                        data = await resp2.json()
                        print(f"   📬 [{file_name}] Response received ({len(json.dumps(data))} bytes).")
                        return strip_code_fences(data["choices"][0]["message"]["content"].strip())

            resp.raise_for_status()
            data = await resp.json()
            print(f"   📬 [{file_name}] Response received ({len(json.dumps(data))} bytes).")
            return strip_code_fences(data["choices"][0]["message"]["content"].strip())

def extract_esg_only(raw):
    """
    Expecting a JSON object like:
      { "ESG_or_not": "Yes" | "No" }
    """
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            esg = (data.get("ESG_or_not") or "").strip()
            if esg in ("Yes", "No"):
                return {"ESG_or_not": esg}
    except:
        pass
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
    md_files = sorted(glob.glob(os.path.join(SOURCE_DIR, "*.md")))
    if not md_files:
        print("[INFO] No .md files found.")
        return

    # NEW: load mapping once
    links_map = load_links_mapping(INPUT_LINKS_CSV)  # NEW

    print(f"[PROFILE] {PROFILE} | MODEL={MODEL_NAME} | TEMP={TEMPERATURE} | MAX_TOKENS={MAX_TOKENS}")
    print(f"[PROMPT] {PROMPT_FILE_PATH}")
    print(f"[INPUT ] {SOURCE_DIR}")
    print(f"[MAP   ] {INPUT_LINKS_CSV}")  # NEW
    print(f"[OUTPUT] {OUTPUT_CSV}")
    print(f"[LIMITS] OPENAI_RPM={OPENAI_RPM} req/min")

    CONCURRENCY_LIMIT = int(os.getenv("LOCAL_CONCURRENCY", "16"))
    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)

    async with aiohttp.ClientSession() as session:
        tasks = [
            process_file_async(session, path, sem, idx=i + 1, total=len(md_files))
            for i, path in enumerate(md_files)
        ]
        results = await asyncio.gather(*tasks)

    rows = [r for r in results if r is not None]

    print("\n📁 Writing results to CSV...")
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        # NEW: include Date, Title, URL, md_file alongside AI result
        fieldnames = ["Date", "Title", "URL", "md_file", "ESG_or_not"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            md_file = r.get("File Name", "")
            meta = links_map.get(md_file, {"Date": "", "Title": "", "URL": "", "md_file": md_file})
            writer.writerow({
                "Date": meta.get("Date", ""),
                "Title": meta.get("Title", ""),
                "URL": meta.get("URL", ""),
                "md_file": meta.get("md_file", md_file),
                "ESG_or_not": r.get("ESG_or_not", ""),
            })

    print(f"\n✅ [DONE] Wrote {len(rows)} rows from {len(md_files)} files → {OUTPUT_CSV}")

if __name__ == "__main__":
    asyncio.run(main_async())

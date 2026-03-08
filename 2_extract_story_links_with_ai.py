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
from aiolimiter import AsyncLimiter  # ✅ NEW
import random

# ----------------- CLI -----------------
parser = argparse.ArgumentParser(
    description="Extract (Date, Title, URL) from .md files concurrently and write a CSV."
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

SOURCE_DIR = os.getenv("SOURCE_DIR", r"source_md_files")
OUTPUT_CSV = os.getenv("OUTPUT_CSV", r"./2_story_links.csv")

# API config
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

# 🔒 Rate limits (requests per minute)
OPENAI_RPM = int(os.getenv("OPENAI_RPM", "10000"))  # Tier-4 default
rpm_limiter = AsyncLimiter(OPENAI_RPM, time_period=60)

# 🔁 Retry config (small + effective)
MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "3"))
BASE_BACKOFF = float(os.getenv("OPENAI_BACKOFF_SECONDS", "1.0"))

# Per-profile configs
def get_env(name, default=None):
    return os.getenv(name, default)

def load_profile_config(profile: str):
    px = profile
    cfg = {
        "MODEL_NAME": get_env(f"{px}_MODEL_NAME"),
        "TEMPERATURE": get_env(f"{px}_TEMPERATURE"),
        "MAX_TOKENS": get_env(f"{px}_MAX_TOKENS"),
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
PROMPT_FILE = "2_extraction_prompt.yaml"

if not MODEL_NAME:
    raise SystemExit(f"[CONFIG ERROR] {PROFILE}_MODEL_NAME is not set in .env")

# ----------------- Prompt loading -----------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPT_FILE_PATH = os.path.join(SCRIPT_DIR, PROMPT_FILE)

def load_prompt_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    system = data.get("system", "")
    user_template = data.get("user_template", "{{markdown}}")
    hints = (data.get("profile_hints") or {}).get(PROFILE, "")
    return system, user_template, hints

SYSTEM_PROMPT, USER_TEMPLATE, PROFILE_HINT = load_prompt_yaml(PROMPT_FILE_PATH)
def render_user_prompt(md): return USER_TEMPLATE.replace("{{markdown}}", md)

# ----------------- Time window -----------------
TZ = ZoneInfo("Australia/Melbourne")
today_local = datetime.now(TZ).date()
start_date = today_local - timedelta(days=7)
end_date = today_local

# ----------------- Helpers -----------------
def read_text_file(path):
    with open(path, "rb") as f:
        raw = f.read()
    enc = chardet.detect(raw).get("encoding") or "utf-8"
    return raw.decode(enc, errors="replace")

def strip_code_fences(s: str):
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s.strip(), re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else s

def normalise_date_safe(date_str):
    if not date_str: return None
    try:
        dt = dateparser.parse(date_str)
        if not dt: return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ).date()
    except: return None

def within_last_week(d): return d and (start_date <= d <= end_date)

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

    # 🔁 Minimal, robust retry loop (429/5xx) with jitter
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"   📤 [{file_name}] Sending request to OpenAI... (attempt {attempt}/{MAX_RETRIES})")
        try:
            async with rpm_limiter:
                async with session.post(url, json=payload, headers=headers, timeout=180) as resp:
                    text = await resp.text()
                    if resp.status in (429, 500, 502, 503, 504):
                        # Retryable server or rate errors
                        print(f"   ⏳ [{file_name}] HTTP {resp.status}. Body: {text[:200]}")
                        if attempt < MAX_RETRIES:
                            # Use Retry-After if present, else exponential backoff with jitter
                            ra = resp.headers.get("retry-after")
                            if ra:
                                delay = float(ra)
                            else:
                                delay = BASE_BACKOFF * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                            print(f"   🔁 [{file_name}] Retrying in {delay:.2f}s…")
                            await asyncio.sleep(delay)
                            continue
                        resp.raise_for_status()

                    resp.raise_for_status()
                    data = json.loads(text)
                    print(f"   📬 [{file_name}] Response received ({len(text)} bytes).")
                    return strip_code_fences(data["choices"][0]["message"]["content"].strip())

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            # Network hiccup: retry if possible
            print(f"   ⚠️  [{file_name}] Network error: {e}")
            if attempt < MAX_RETRIES:
                delay = BASE_BACKOFF * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                print(f"   🔁 [{file_name}] Retrying in {delay:.2f}s…")
                await asyncio.sleep(delay)
                continue
            raise

    # If we get here, all retries failed
    raise RuntimeError(f"[{file_name}] Exhausted retries")

def extract_items_from_text(raw):
    try:
        data = json.loads(raw)
        if isinstance(data, dict): data = data.get("items", [])
        if not isinstance(data, list): return []
        out = []
        for item in data:
            date_ = (item.get("date") or "").strip()
            title = (item.get("title") or "").strip()
            url = (item.get("url") or item.get("link") or "").strip()
            if title and url:
                out.append({"date": date_, "title": title, "url": url})
        return out
    except:
        return []

# ----------------- Process one file -----------------
async def process_file_async(session, path, sem, idx, total):
    file_name = os.path.basename(path)
    async with sem:  # concurrency limiter (local fan-out)
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
            return []

        if not raw.strip().startswith("["):
            print(f"   ⚠️ [{file_name}] Non-JSON response snippet:\n{raw[:300]}")

        items = extract_items_from_text(raw)
        print(f"   📊 [{file_name}] Extracted {len(items)} story items.")
        return items

# ----------------- Main Async -----------------
async def main_async():
    _dir = os.path.dirname(OUTPUT_CSV)
    if _dir:
        os.makedirs(_dir, exist_ok=True)
    md_files = sorted(glob.glob(os.path.join(SOURCE_DIR, "*.md")))
    if not md_files:
        print("[INFO] No .md files found.")
        return

    print(f"[PROFILE] {PROFILE} | MODEL={MODEL_NAME} | TEMP={TEMPERATURE} | MAX_TOKENS={MAX_TOKENS}")
    print(f"[WINDOW] Start: {start_date.isoformat()}  End: {end_date.isoformat()} (AU/Melbourne)")
    print(f"[INFO] Processing {len(md_files)} files concurrently...")
    print(f"[LIMITS] OPENAI_RPM={OPENAI_RPM} req/min | RETRIES={MAX_RETRIES}")

    CONCURRENCY_LIMIT = int(os.getenv("LOCAL_CONCURRENCY", "16"))  # local worker pool
    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)

    # 🔧 Connection pool tuned to concurrency (important for high throughput)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY_LIMIT, limit_per_host=CONCURRENCY_LIMIT)

    results = []
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            asyncio.create_task(process_file_async(session, path, sem, idx=i + 1, total=len(md_files)))
            for i, path in enumerate(md_files)
        ]
        # ✅ Consume as tasks complete (reduces tail latency & memory)
        for coro in asyncio.as_completed(tasks):
            try:
                items = await coro
            except Exception as e:
                print(f"   ❌ Task error: {e}")
                items = []
            results.append(items)

    seen_urls = set()
    rows = []
    for file_items in results:
        for it in file_items:
            n_date = normalise_date_safe(it.get("date"))
            if not within_last_week(n_date):
                continue
            if it["url"] in seen_urls:
                continue
            seen_urls.add(it["url"])
            rows.append({"Date": n_date.isoformat(), "Title": it["title"], "URL": it["url"]})

    print("\n📁 Writing results to CSV...")
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Date", "Title", "URL"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n✅ [DONE] Extracted {len(rows)} rows from {len(md_files)} files → {OUTPUT_CSV}")

if __name__ == "__main__":
    asyncio.run(main_async())

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
args = parser.parse_args()

# ✅ Default to OAI
if args.OAI:
    PROFILE = "OAI"
elif args.OAIW:
    PROFILE = "OAIW"
else:
    PROFILE = "OAI"

# ----------------- ENV -----------------
load_dotenv()

SOURCE_DIR = os.getenv("SOURCE_DIR", r"source_md_files")
OUTPUT_CSV = os.getenv("OUTPUT_CSV", r"./2_story_links.csv")

# API config
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

# 🔒 Rate limits (requests per minute)
OPENAI_RPM = int(os.getenv("OPENAI_RPM", "10000"))  # Tier-4 default
rpm_limiter = AsyncLimiter(OPENAI_RPM, time_period=60)
OPENAI_CONCURRENCY = int(os.getenv("OPENAI_CONCURRENCY", os.getenv("LOCAL_CONCURRENCY", "48")))
OPENAI_TIMEOUT_SECONDS = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "180"))

# 🔁 Retry config (small + effective)
MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "3"))
BASE_BACKOFF = float(os.getenv("OPENAI_BACKOFF_SECONDS", "1.0"))

# Per-profile configs
def get_env(name, default=None):
    return os.getenv(name, default)

def get_profile_env_prefix(profile: str) -> str:
    # Single OpenAI settings block for both OAI and OAIW modes
    if profile in {"OAI", "OAIW"}:
        return "OAI"
    return profile

def load_profile_config(profile: str):
    px = get_profile_env_prefix(profile)
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

def infer_model_series(model_name: str) -> str:
    m = (model_name or "").strip().lower()
    if m.startswith("gpt-5"):
        return "gpt5"
    return "legacy"

# Model series routing (single OpenAI block shared by OAI/OAIW)
ENV_PREFIX = get_profile_env_prefix(PROFILE)
MODEL_SERIES = (
    get_env(f"{ENV_PREFIX}_MODEL_SERIES", get_env("OPENAI_MODEL_SERIES", "auto")) or "auto"
).strip().lower()
if MODEL_SERIES == "gpt4":
    MODEL_SERIES = "legacy"
elif MODEL_SERIES == "gpt5":
    MODEL_SERIES = "gpt5"
if MODEL_SERIES == "auto":
    MODEL_SERIES = infer_model_series(MODEL_NAME)
if MODEL_SERIES not in {"gpt5", "legacy"}:
    raise SystemExit(f"[CONFIG ERROR] {ENV_PREFIX}_MODEL_SERIES / OPENAI_MODEL_SERIES must be one of: auto, gpt5, gpt4, legacy")

# Optional per-series model names (keeps manual switching simple)
if MODEL_SERIES == "gpt5":
    MODEL_NAME = get_env(f"{ENV_PREFIX}_MODEL_NAME_GPT5", MODEL_NAME)
else:
    MODEL_NAME = get_env(f"{ENV_PREFIX}_MODEL_NAME_GPT4", MODEL_NAME)

if not MODEL_NAME:
    raise SystemExit(f"[CONFIG ERROR] {ENV_PREFIX}_MODEL_NAME (or per-series model name) is not set in .env")

# GPT-5-only settings (Responses API)
GPT5_REASONING_EFFORT = (get_env(f"{ENV_PREFIX}_REASONING_EFFORT", "low") or "low").strip().lower()
GPT5_TEXT_VERBOSITY = (get_env(f"{ENV_PREFIX}_TEXT_VERBOSITY", "medium") or "medium").strip().lower()

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

    # 🔁 Minimal, robust retry loop (429/5xx) with jitter
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"   📤 [{file_name}] Sending request to OpenAI... (attempt {attempt}/{MAX_RETRIES})")
        try:
            async with rpm_limiter:
                async with session.post(url, json=payload, headers=headers, timeout=OPENAI_TIMEOUT_SECONDS) as resp:
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
                    if MODEL_SERIES == "gpt5":
                        content = extract_responses_text(data)
                    else:
                        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                    return strip_code_fences((content or "").strip())

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

    print(f"[PROFILE] {PROFILE} | MODEL={MODEL_NAME} | SERIES={MODEL_SERIES} | TEMP={TEMPERATURE} | MAX_TOKENS={MAX_TOKENS}")
    print(f"[WINDOW] Start: {start_date.isoformat()}  End: {end_date.isoformat()} (AU/Melbourne)")
    print(f"[INFO] Processing {len(md_files)} files concurrently...")
    print(f"[LIMITS] OPENAI_RPM={OPENAI_RPM} req/min | CONCURRENCY={OPENAI_CONCURRENCY} | RETRIES={MAX_RETRIES}")

    sem = asyncio.Semaphore(OPENAI_CONCURRENCY)

    # 🔧 Connection pool tuned to concurrency (important for high throughput)
    connector = aiohttp.TCPConnector(
        limit=OPENAI_CONCURRENCY,
        limit_per_host=OPENAI_CONCURRENCY,
        ttl_dns_cache=300,
    )

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

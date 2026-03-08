import os
import re
import csv
import json
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

# Paths
SOURCE_DIR = os.getenv(
    "SOURCE_DIR",
    r"/home/z440/Desktop/Projects/ESG_SNAPSHOT_AUTOMATED/source_md_files_cleaned"
)
INPUT_CSV = os.getenv(
    "INPUT_CSV",
    r"4_story_esg_or_not.csv"
)
OUTPUT_CSV = os.getenv(
    "OUTPUT_CSV",
    r"4.1_story_type.csv"
)

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
        "PROMPT_FILE": get_env(f"{px}_PROMPT_FILE", "4.1_story_type.yaml"),
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

ALLOWED_STORY_TYPES = [
    "Legislative and Statutory Developments",
    "Parliamentary and Political Proceedings",
    "Consultation and Policy Design Opportunities",
    "Funding and Grant Announcements",
    "Infrastructure, Project Approvals, and EPBC Developments",
    "Reports, Data Releases, and Analytical Insights",
    "Ministerial, Diplomatic, and International Engagements",
    "Corporate and Institutional ESG Actions",
    "Environmental Protection, Biodiversity, and Nature Policy",
    "State and Local Government Programs",
    "Community, First Nations, and Social Licence Initiatives",
    "Compliance, Oversight, and Enforcement Actions",
    "Misc",
]
ALLOWED_STORY_TYPES_LOWER = {v.lower(): v for v in ALLOWED_STORY_TYPES}

if not MODEL_NAME:
    raise SystemExit(f"[CONFIG ERROR] {PROFILE}_MODEL_NAME is not set in .env")

# ----------------- Prompt loading -----------------
PROMPT_FILE_PATH = os.path.abspath(
    r"4.1_story_type.yaml"
)

def load_prompt_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    system = data.get("system", "")
    user_template = data.get("user_template", "{{markdown}}")
    hints = (data.get("profile_hints") or {}).get(PROFILE, "")
    call_template = ""
    call_section = data.get("call_template")
    if isinstance(call_section, dict):
        call_template = call_section.get("prompt", "") or ""
    return system, user_template, hints, call_template

SYSTEM_PROMPT, USER_TEMPLATE, PROFILE_HINT, CALL_TEMPLATE = load_prompt_yaml(PROMPT_FILE_PATH)

def render_user_prompt(md):
    template = CALL_TEMPLATE or USER_TEMPLATE or "{{markdown}}"
    if "{{NEWS_MARKDOWN}}" in template:
        return template.replace("{{NEWS_MARKDOWN}}", md)
    if "{{markdown}}" in template:
        return template.replace("{{markdown}}", md)
    if template:
        return f"{template}\n\n{md}"
    return md

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

def extract_story_type(raw):
    """
    Expecting a single-line response containing the chosen story type tag.
    """
    if not raw:
        return "Misc"

    cleaned_raw = strip_code_fences(raw).strip()

    # 1) Try JSON-shaped output first (common when models add structure)
    try:
        obj = json.loads(cleaned_raw)
        if isinstance(obj, dict):
            candidate = (obj.get("story_type") or obj.get("Story_Type") or "").strip()
            if candidate:
                return ALLOWED_STORY_TYPES_LOWER.get(candidate.lower(), "Misc")
    except Exception:
        pass

    # 2) Exact/normalized single-line match
    lines = cleaned_raw.splitlines()
    for line in lines:
        candidate = line.strip().strip("\"'`")
        if not candidate:
            continue
        if candidate in ALLOWED_STORY_TYPES:
            return candidate
        lowered = candidate.lower()
        if lowered in ALLOWED_STORY_TYPES_LOWER:
            return ALLOWED_STORY_TYPES_LOWER[lowered]

    # 3) If model returns extra text, recover by substring match to canonical labels
    lower_raw = cleaned_raw.lower()
    for label in ALLOWED_STORY_TYPES:
        if label.lower() in lower_raw:
            return label

    return "Misc"

# ----------------- Process one row -----------------
async def process_row_async(session, row, sem, idx, total, row_index):
    md_file = (row.get("md_file") or "").strip()
    label = md_file or f"row_{row_index + 1}"

    async with sem:  # local concurrency limiter
        print(f"\n➡️  [{idx}/{total}] Starting: {label}")

        if not md_file:
            print(f"   ⚠️ [{label}] Missing 'md_file' value; skipping.")
            return row_index, None

        md_path = os.path.join(SOURCE_DIR, md_file)
        if not os.path.exists(md_path):
            print(f"   ⚠️ [{label}] Markdown file not found at {md_path}; skipping.")
            return row_index, None

        md_text = read_text_file(md_path)
        print(f"   📄 [{label}] File loaded ({len(md_text)} chars).")

        try:
            start_time = time.time()
            raw = await call_openai_async(session, md_text, with_web=(PROFILE == "OAIW"), file_name=label)
            duration = time.time() - start_time
            print(f"   ✅ [{label}] API call completed in {duration:.2f}s.")
        except Exception as e:
            print(f"   ❌ [{label}] API failed: {e}")
            return row_index, None

        story_type = extract_story_type(raw)
        if story_type == "Misc" and "misc" not in (raw or "").lower():
            print(f"   ⚠️ [{label}] Non-canonical response; coerced to 'Misc'. Snippet:\n{raw[:300]}")

        print(f"   📊 [{label}] Parsed Story_Type='{story_type}'.")
        return row_index, story_type

# ----------------- Main Async -----------------
async def main_async():
    output_dir = os.path.dirname(OUTPUT_CSV)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    if not os.path.exists(INPUT_CSV):
        print(f"[ERROR] Input CSV not found: {INPUT_CSV}")
        return

    with open(INPUT_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = [row for row in reader]

    if not fieldnames:
        print(f"[ERROR] Input CSV has no header: {INPUT_CSV}")
        return

    rows_to_classify = [
        (idx, row)
        for idx, row in enumerate(rows)
        if (row.get("ESG_or_not") or "").strip().lower() == "yes"
    ]

    print(f"[PROFILE] {PROFILE} | SERIES={MODEL_SERIES} | MODEL={MODEL_NAME} | TEMP={TEMPERATURE} | MAX_TOKENS={MAX_TOKENS}")
    print(f"[PROMPT] {PROMPT_FILE_PATH}")
    print(f"[INPUT ] {INPUT_CSV}")
    print(f"[MD DIR] {SOURCE_DIR}")
    print(f"[OUTPUT] {OUTPUT_CSV}")
    print(f"[LIMITS] OPENAI_RPM={OPENAI_RPM} req/min | OPENAI_TIMEOUT_SECONDS={OPENAI_TIMEOUT_SECONDS}s")
    print(f"[COUNT ] Total rows={len(rows)} | ESG='Yes' rows={len(rows_to_classify)}")

    CONCURRENCY_LIMIT = int(os.getenv("OPENAI_CONCURRENCY", os.getenv("LOCAL_CONCURRENCY", "16")))
    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)

    story_type_map = {}

    if rows_to_classify:
        async with aiohttp.ClientSession() as session:
            tasks = [
                process_row_async(session, row, sem, idx=i + 1, total=len(rows_to_classify), row_index=row_idx)
                for i, (row_idx, row) in enumerate(rows_to_classify)
            ]
            results = await asyncio.gather(*tasks)

        story_type_map = {idx: story for idx, story in results if story}
        print(f"\n📊 Parsed story types for {len(story_type_map)}/{len(rows_to_classify)} eligible rows.")
    else:
        print("\n[INFO] No rows marked with ESG_or_not == 'Yes'; nothing to classify.")

    output_fieldnames = list(fieldnames)
    if "Story_Type" not in output_fieldnames:
        output_fieldnames.append("Story_Type")

    print("\n📁 Writing results to CSV...")
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=output_fieldnames)
        writer.writeheader()
        for idx, row in enumerate(rows):
            row_out = dict(row)
            story_type = story_type_map.get(idx, row_out.get("Story_Type", ""))
            row_out["Story_Type"] = story_type or ""
            writer.writerow(row_out)

    print(f"\n✅ [DONE] Wrote {len(rows)} rows → {OUTPUT_CSV}")

if __name__ == "__main__":
    asyncio.run(main_async())

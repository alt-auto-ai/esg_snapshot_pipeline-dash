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
    description="For rows with ESG_or_not='Yes', classify Jurisdiction and write adjacent column."
)
args = parser.parse_args()

# ✅ OAI-only
PROFILE = "OAI"

# ----------------- ENV -----------------
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# Paths
SOURCE_DIR = os.getenv(
    "SOURCE_DIR",
    os.path.join(PROJECT_ROOT, "source_md_files_cleaned")
)
INPUT_CSV = os.getenv(
    "INPUT_CSV",
    os.path.join(PROJECT_ROOT, "4.1_story_type.csv")
)
OUTPUT_CSV = os.getenv(
    "OUTPUT_CSV",
    os.path.join(PROJECT_ROOT, "5_story_jurisdiction.csv")
)

cwd_source_dir = os.path.abspath("source_md_files_cleaned")
if "SOURCE_DIR" not in os.environ and os.path.isdir(cwd_source_dir) and any(name.lower().endswith(".md") for name in os.listdir(cwd_source_dir)):
    SOURCE_DIR = cwd_source_dir

cwd_input_csv = os.path.abspath("4.1_story_type.csv")
if "INPUT_CSV" not in os.environ and os.path.exists(cwd_input_csv):
    INPUT_CSV = cwd_input_csv

cwd_output_csv = os.path.abspath("5_story_jurisdiction.csv")
if "OUTPUT_CSV" not in os.environ:
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
        "PROMPT_FILE": get_env(f"{px}_PROMPT_FILE", "5_story_filter_jurisdiction.yaml"),
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

ALLOWED_JURISDICTIONS = [
    "Australian National Scope",
    "Queensland",
    "NSW",
    "Victoria",
    "Tasmania",
    "Northern Territory",
    "Western Australia",
    "South Australia",
    "Australian Capital Territory",
    "International",
]
ALLOWED_JURISDICTIONS_LOWER = {v.lower(): v for v in ALLOWED_JURISDICTIONS}

if not MODEL_NAME:
    raise SystemExit(f"[CONFIG ERROR] {PROFILE}_MODEL_NAME is not set in .env")

# ----------------- Prompt loading -----------------
PROMPT_FILE_PATH = os.path.join(PROJECT_ROOT, "5_story_filter_jurisdiction.yaml")

def load_prompt_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    system = data.get("system", "")
    user_template = data.get("user_template", "{{markdown}}")
    hints = (data.get("profile_hints") or {}).get(PROFILE, "")
    return system, user_template, hints

SYSTEM_PROMPT, USER_TEMPLATE, PROFILE_HINT = load_prompt_yaml(PROMPT_FILE_PATH)

# ✅ Jurisdiction-only rendering (ESG flag comes from INPUT_CSV)
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

def infer_jurisdiction_hint_from_markdown(markdown_text: str):
    if not markdown_text:
        return None
    m = re.search(r"^Story's url:\s*(\S+)", markdown_text, re.IGNORECASE | re.MULTILINE)
    if not m:
        return None
    url = m.group(1).strip().lower()

    state_patterns = [
        ("Victoria", ["vic.gov.au", "/vic/", "victoria"]),
        ("NSW", ["nsw.gov.au", "/nsw/", "new-south-wales"]),
        ("Queensland", ["qld.gov.au", "/qld/", "queensland"]),
        ("Tasmania", ["tas.gov.au", "/tas/", "tasmania"]),
        ("Northern Territory", ["nt.gov.au", "/nt/", "northern-territory"]),
        ("Western Australia", ["wa.gov.au", "/wa/", "western-australia"]),
        ("South Australia", ["sa.gov.au", "/sa/", "south-australia"]),
        ("Australian Capital Territory", ["act.gov.au", "/act/", "australian-capital-territory"]),
    ]

    for label, hints in state_patterns:
        if any(h in url for h in hints):
            return label

    if ".gov.au" in url:
        return "Australian National Scope"

    return "International"

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

def extract_jurisdiction_only(raw, url_hint=None):
    """
    Expecting JSON object:
      { "Jurisdiction": "<one-of-allowed-values>" }
    """
    if not raw:
        return {"Jurisdiction": url_hint or "International"}

    cleaned_raw = strip_code_fences(raw).strip()

    def normalize_jurisdiction(value: str):
        if not value:
            return None
        value_clean = value.strip().strip("\"'`")
        if not value_clean:
            return None
        value_lower = value_clean.lower()
        if value_lower in {"national", "australian national", "australian national scope", "australia national"}:
            return "Australian National Scope"
        if value_clean in ALLOWED_JURISDICTIONS:
            return value_clean
        return ALLOWED_JURISDICTIONS_LOWER.get(value_lower)

    try:
        data = json.loads(cleaned_raw)
        if isinstance(data, dict):
            j = normalize_jurisdiction((data.get("Jurisdiction") or ""))
            if j:
                return {"Jurisdiction": j}
    except Exception:
        pass

    for line in cleaned_raw.splitlines():
        j = normalize_jurisdiction(line)
        if j:
            return {"Jurisdiction": j}

    lower_raw = cleaned_raw.lower()
    for label in ALLOWED_JURISDICTIONS:
        if label.lower() in lower_raw:
            return {"Jurisdiction": label}

    return {"Jurisdiction": url_hint or "International"}

# ----------------- Process one file -----------------
async def process_file_async(session, md_file, sem, idx, total):
    async with sem:  # local concurrency limiter
        print(f"\n➡️  [{idx}/{total}] Starting: {md_file}")
        md_path = os.path.join(SOURCE_DIR, md_file)
        if not os.path.exists(md_path):
            print(f"   ❌ [{md_file}] File not found at {md_path}")
            return {"md_file": md_file, "Jurisdiction": ""}

        md_text = read_text_file(md_path)
        url_hint = infer_jurisdiction_hint_from_markdown(md_text)
        print(f"   📄 [{md_file}] File loaded ({len(md_text)} chars).")

        try:
            start_time = time.time()
            raw = await call_openai_async(session, md_text, with_web=False, file_name=md_file)
            duration = time.time() - start_time
            print(f"   ✅ [{md_file}] API call completed in {duration:.2f}s.")
        except Exception as e:
            print(f"   ❌ [{md_file}] API failed: {e}")
            return {"md_file": md_file, "Jurisdiction": ""}

        if not raw.strip().startswith("{"):
            print(f"   ⚠️ [{md_file}] Non-JSON-object response snippet:\n{raw[:300]}")

        item = extract_jurisdiction_only(raw, url_hint=url_hint)
        if item is None:
            print(f"   ⚠️ [{md_file}] Could not parse Jurisdiction JSON. Using fallback 'International'.")
            item = {"Jurisdiction": "International"}

        print(f"   📊 [{md_file}] Parsed Jurisdiction='{item.get('Jurisdiction')}'.")
        item["md_file"] = md_file
        return item

# ----------------- Main Async -----------------
async def main_async():
    output_dir = os.path.dirname(OUTPUT_CSV)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # Read input CSV: Date, Title, URL, md_file, ESG_or_not
    if not os.path.exists(INPUT_CSV):
        raise SystemExit(f"[INPUT ERROR] Not found: {INPUT_CSV}")

    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        input_rows = [row for row in reader]

    required_cols = {"Date", "Title", "URL", "md_file", "ESG_or_not", "Story_Type"}
    if not input_rows or not required_cols.issubset(set(fieldnames)):
        raise SystemExit("[INPUT ERROR] CSV must have columns: Date, Title, URL, md_file, ESG_or_not, Story_Type")

    print(f"[PROFILE] {PROFILE} | SERIES={MODEL_SERIES} | MODEL={MODEL_NAME} | TEMP={TEMPERATURE} | MAX_TOKENS={MAX_TOKENS}")
    print(f"[PROMPT]  {PROMPT_FILE_PATH}")
    print(f"[INPUT ]  {INPUT_CSV}")
    print(f"[MD DIR]  {SOURCE_DIR}")
    print(f"[OUTPUT]  {OUTPUT_CSV}")
    print(f"[LIMITS]  OPENAI_RPM={OPENAI_RPM} req/min | OPENAI_TIMEOUT_SECONDS={OPENAI_TIMEOUT_SECONDS}s")

    # Prepare tasks only for ESG_or_not == 'Yes'
    candidates = [r for r in input_rows if (r.get("ESG_or_not") or "").strip().lower() == "yes"]
    md_list = [r.get("md_file", "").strip() for r in candidates if r.get("md_file")]

    CONCURRENCY_LIMIT = int(os.getenv("OPENAI_CONCURRENCY", os.getenv("LOCAL_CONCURRENCY", "16")))
    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)

    jurisdiction_map = {}  # md_file -> jurisdiction
    async with aiohttp.ClientSession() as session:
        tasks = [
            process_file_async(session, md_file, sem, idx=i + 1, total=len(md_list))
            for i, md_file in enumerate(md_list)
        ]
        if tasks:
            results = await asyncio.gather(*tasks)
            for r in results:
                jurisdiction_map[r["md_file"]] = r.get("Jurisdiction", "")
        else:
            print("[INFO] No ESG='Yes' rows to classify.")

    # Merge back: write ALL original columns + Jurisdiction
    print("\n📁 Writing results to CSV...")
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        fieldnames_out = ["Date", "Title", "URL", "md_file", "ESG_or_not", "Story_Type", "Jurisdiction"]
        writer = csv.DictWriter(f, fieldnames=fieldnames_out)
        writer.writeheader()
        for row in input_rows:
            md_file = (row.get("md_file") or "").strip()
            esg = (row.get("ESG_or_not") or "").strip()
            juris = jurisdiction_map.get(md_file, "") if esg.lower() == "yes" else ""
            writer.writerow({
                "Date": (row.get("Date") or "").strip(),
                "Title": (row.get("Title") or "").strip(),
                "URL": (row.get("URL") or "").strip(),
                "md_file": md_file,
                "ESG_or_not": esg,
                "Story_Type": (row.get("Story_Type") or "").strip(),
                "Jurisdiction": juris
            })

    print(f"\n✅ [DONE] Wrote {len(input_rows)} rows → {OUTPUT_CSV}")

if __name__ == "__main__":
    asyncio.run(main_async())

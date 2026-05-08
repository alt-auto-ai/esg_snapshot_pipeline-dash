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
import shutil
from datetime import datetime, timedelta
from dateutil import parser as dateparser
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from aiolimiter import AsyncLimiter  # ✅ rate limiter

# ----------------- CLI -----------------
parser = argparse.ArgumentParser(
    description="For rows with ESG_or_not='Yes', generate two-column highlights (Hook/One Liner) and append columns."
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
SOURCE_DIR = r"/home/z440/Desktop/Projects/ESG_SNAPSHOT_AUTOMATED/source_md_files_cleaned"
INPUT_CSV = r"8_esg_draft_single.csv"
# 🆕 Write to 8.1_esg_highlights.csv (per request)
OUTPUT_CSV = os.getenv(
    "OUTPUT_CSV",
    r"8.1_esg_highlights_multi.csv"
)
# 📎 Also mirror into Quality_Check
QUALITY_CHECK_DIR = os.getenv(
    "QUALITY_CHECK_DIR",
    r"Quality_Check"
)

# API config
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_TIMEOUT_SECONDS = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "180"))

# 🔒 Rate limits (per-minute)
OPENAI_RPM = int(os.getenv("OPENAI_RPM", "10000"))
rpm_limiter = AsyncLimiter(OPENAI_RPM, time_period=60)

# Per-profile configs
def get_env(name, default=None):
    return os.getenv(name, default)

def load_profile_config(profile: str):
    px = profile
    model_series_raw = os.getenv("OPENAI_MODEL_SERIES", "gpt5").strip().lower()
    model_series_norm = model_series_raw.replace("-", "").replace("_", "").replace(" ", "")
    if model_series_norm == "gpt4":
        model_series = "gpt4"
    else:
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
        # prompt filename is ignored; we use absolute path below as before
        "PROMPT_FILE": get_env(f"{px}_PROMPT_FILE", "8.1_esg_highlights.yaml"),
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
# 🔁 Use 8.1_esg_highlights.yaml (per request)
PROMPT_FILE_PATH = r"8.1_esg_highlights.yaml"

def load_prompt_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    system = data.get("system", "")
    user_template = data.get("user_template", "{{markdown}}")
    hints = (data.get("profile_hints") or {}).get(PROFILE, "")
    return system, user_template, hints

SYSTEM_PROMPT, USER_TEMPLATE, PROFILE_HINT = load_prompt_yaml(PROMPT_FILE_PATH)

# Render prompt with the markdown file contents and optional metadata
TZ = ZoneInfo("Australia/Melbourne")
def render_user_prompt(md, meta: dict):
    t = USER_TEMPLATE
    # Support both tags
    t = t.replace("{{markdown}}", md)
    t = t.replace("{{story_text}}", md)
    # Optional helpful context — note: input header uses "ESG Summary" (with space)
    t = t.replace("{{context}}", (meta.get("ESG Summary") or meta.get("ESG_Summary") or "").strip())
    t = t.replace("{{jurisdiction}}", (meta.get("Jurisdiction") or "").strip())
    t = t.replace("{{priority_angle}}", "")
    t = t.replace("{{today}}", datetime.now(TZ).strftime("%d %B %Y"))
    # Leave any other Jinja-like defaults as literal text (harmless)
    return t

# ----------------- Helpers -----------------
def read_text_file(path):
    with open(path, "rb") as f:
        raw = f.read()
    enc = chardet.detect(raw).get("encoding") or "utf-8"
    return raw.decode(enc, errors="replace")

def strip_code_fences(s: str):
    m = re.match(r"^```(?:json|yaml|md|markdown)?\s*(.*?)\s*```$", s.strip(), re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else s

def parse_highlights_payload(raw_content: str):
    cleaned = strip_code_fences(raw_content or "").strip()

    # 1) Strict JSON object
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return {
                "hook": (obj.get("hook") or "").strip(),
                "one_liner": (obj.get("one_liner") or "").strip(),
            }
    except Exception:
        pass

    # 2) YAML object fallback
    try:
        obj = yaml.safe_load(cleaned)
        if isinstance(obj, dict):
            return {
                "hook": (obj.get("hook") or "").strip(),
                "one_liner": (obj.get("one_liner") or obj.get("one liner") or "").strip(),
            }
    except Exception:
        pass

    # 3) Label-based text fallback (Hook:/One Liner:)
    hook_match = re.search(r"(?:^|\n)\s*hook\s*:\s*(.+)", cleaned, flags=re.IGNORECASE)
    one_match = re.search(r"(?:^|\n)\s*one\s*[-_ ]?liner\s*:\s*(.+)", cleaned, flags=re.IGNORECASE)
    if hook_match or one_match:
        return {
            "hook": (hook_match.group(1).strip() if hook_match else ""),
            "one_liner": (one_match.group(1).strip() if one_match else ""),
        }

    # 4) Plain lines fallback
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    if len(lines) >= 2:
        return {"hook": lines[0], "one_liner": lines[1]}
    if len(lines) == 1:
        return {"hook": lines[0], "one_liner": ""}

    return {"hook": "", "one_liner": ""}

# ----------------- Async Calls -----------------
# ✅ Structured outputs schema → now just two fields
STRUCTURED_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "esg_highlights_package",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "hook": {"type": "string"},
                "one_liner": {"type": "string"}
            },
            "required": ["hook", "one_liner"]
        }
    }
}

async def call_openai_async(session, markdown_text, meta, with_web, file_name):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing")

    system_block = SYSTEM_PROMPT + ("\n\n" + PROFILE_HINT if PROFILE_HINT else "")
    if with_web:
        system_block += "\n\nNOTE: Web search not enabled."

    user_block = render_user_prompt(markdown_text, meta)

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
            # 🔑 Ask for structured JSON (strict)
            "response_format": STRUCTURED_RESPONSE_FORMAT,
        }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    print(f"   📤 [{file_name}] Sending request to OpenAI (structured)…")

    def extract_content(data):
        if MODEL_SERIES == "gpt5":
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
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return (content or "").strip()

    async with rpm_limiter:
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
                        content = extract_content(data)
                        print(f"   📬 [{file_name}] Structured response received.")
                        return parse_highlights_payload(content)

            resp.raise_for_status()
            data = await resp.json()
            content = extract_content(data)
            print(f"   📬 [{file_name}] Structured response received.")
            return parse_highlights_payload(content)

# ----------------- Process one file -----------------
async def process_file_async(session, row, sem, idx, total):
    async with sem:
        md_file = (row.get("md_file") or "").strip()
        print(f"\n➡️  [{idx}/{total}] Starting: {md_file}")
        md_path = os.path.join(SOURCE_DIR, md_file)
        if not os.path.exists(md_path):
            print(f"   ❌ [{md_file}] File not found at {md_path}")
            return {"md_file": md_file, "Hook": "", "One Liner": ""}

        md_text = read_text_file(md_path)
        print(f"   📄 [{md_file}] File loaded ({len(md_text)} chars).")

        meta = {
            "Jurisdiction": row.get("Jurisdiction", ""),
            # prefer "ESG Summary" but keep fallback for legacy underscore
            "ESG Summary": row.get("ESG Summary", "") or row.get("ESG_Summary", ""),
        }

        try:
            start_time = time.time()
            obj = await call_openai_async(session, md_text, meta, with_web=(PROFILE == "OAIW"), file_name=md_file)
            duration = time.time() - start_time
            print(f"   ✅ [{md_file}] API call completed in {duration:.2f}s.")
        except Exception as e:
            print(f"   ❌ [{md_file}] API failed: {e}")
            return {"md_file": md_file, "Hook": "", "One Liner": ""}

        # Validate & coerce result
        hook = (obj.get("hook") or "").strip() if isinstance(obj, dict) else ""
        one_liner = (obj.get("one_liner") or "").strip() if isinstance(obj, dict) else ""

        print(f"   📝 [{md_file}] Parsed: hook len={len(hook)}, one_liner len={len(one_liner)}")
        return {"md_file": md_file, "Hook": hook, "One Liner": one_liner}

# ----------------- Main Async -----------------
async def main_async():
    output_dir = os.path.dirname(OUTPUT_CSV)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # Read input CSV: Date,Title,URL,md_file,ESG_or_not,Jurisdiction,ESG Summary,ESG_Relevance,Headline,Point_1,Point_2,Point_3,Explainer
    if not os.path.exists(INPUT_CSV):
        raise SystemExit(f"[INPUT ERROR] Not found: {INPUT_CSV}")

    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        input_rows = [row for row in reader]

    required_cols = {"Date", "Title", "URL", "md_file", "ESG_or_not"}
    if not input_rows or not required_cols.issubset(set(fieldnames)):
        raise SystemExit("[INPUT ERROR] CSV must include: Date, Title, URL, md_file, ESG_or_not")

    qc_output_path = os.path.join(QUALITY_CHECK_DIR, os.path.basename(OUTPUT_CSV))

    print(f"[PROFILE] {PROFILE} | SERIES={MODEL_SERIES} | MODEL={MODEL_NAME} | TEMP={TEMPERATURE} | MAX_TOKENS={MAX_TOKENS}")
    print(f"[PROMPT]  {PROMPT_FILE_PATH}")
    print(f"[INPUT ]  {INPUT_CSV}")
    print(f"[MD DIR]  {SOURCE_DIR}")
    print(f"[OUTPUT]  {OUTPUT_CSV}")
    print(f"[OUTPUT_QC] {qc_output_path}")
    print(f"[LIMITS]  OPENAI_RPM={OPENAI_RPM} req/min | OPENAI_TIMEOUT_SECONDS={OPENAI_TIMEOUT_SECONDS}s")

    # Candidates: Relevance is populated AND target columns empty (so we don't overwrite)
    target_cols = ["Hook", "One Liner"]
    candidates = []
    for r in input_rows:
        relevance = (r.get("Relevance") or "").strip()
        has_md = bool((r.get("md_file") or "").strip())
        # Check if at least one target is empty (so reruns can fill missing bits)
        empties = any(not (r.get(col) or "").strip() for col in target_cols)
        if relevance and has_md and empties:
            candidates.append(r)

    CONCURRENCY_LIMIT = int(os.getenv("OPENAI_CONCURRENCY", os.getenv("LOCAL_CONCURRENCY", "16")))
    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)

    results_map = {}  # md_file -> dict of structured fields
    async with aiohttp.ClientSession() as session:
        tasks = [
            process_file_async(session, row, sem, idx=i + 1, total=len(candidates))
            for i, row in enumerate(candidates)
        ]
        if tasks:
            results = await asyncio.gather(*tasks)
            for r in results:
                results_map[r["md_file"]] = r
        else:
            print("[INFO] No rows to draft (Relevance is blank or outputs already filled).")

    # Merge back: write ALL original columns + Hook/One Liner (append if missing)
    print("\n📁 Writing results to CSV...")
    fieldnames_out = list(fieldnames)
    for col in ["Hook", "One Liner"]:
        if col not in fieldnames_out:
            fieldnames_out.append(col)

    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames_out)
        writer.writeheader()
        for row in input_rows:
            md_file = (row.get("md_file") or "").strip()
            relevance = (row.get("Relevance") or "").strip()
            out_row = {k: (row.get(k) or "").strip() for k in fieldnames}
            out_row["ESG_or_not"] = (row.get("ESG_or_not") or "").strip()

            if md_file in results_map and relevance:
                # Only fill empty cells to avoid overwriting manual edits on reruns
                structured = results_map[md_file]
                for col in ["Hook", "One Liner"]:
                    if not (out_row.get(col) or "").strip():
                        out_row[col] = structured.get(col, "")
            else:
                # Ensure columns exist even if we didn't fill them
                for col in ["Hook", "One Liner"]:
                    out_row[col] = (out_row.get(col) or "").strip()

            writer.writerow(out_row)

    # 📎 Mirror the output into Quality_Check
    try:
        os.makedirs(QUALITY_CHECK_DIR, exist_ok=True)
        shutil.copy(OUTPUT_CSV, qc_output_path)
        print(f"📎 Mirrored CSV to Quality_Check → {qc_output_path}")
    except Exception as e:
        print(f"⚠️  Failed to mirror CSV to Quality_Check: {e}")

    print(f"\n✅ [DONE] Wrote {len(input_rows)} rows → {OUTPUT_CSV}")

if __name__ == "__main__":
    asyncio.run(main_async())

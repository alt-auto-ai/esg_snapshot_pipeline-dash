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
    description="Generate two-line ESG newsletter draft outputs with one prompt across all story types."
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
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# Paths
SOURCE_DIR = os.getenv(
    "SOURCE_DIR",
    os.path.join(PROJECT_ROOT, "source_md_files_cleaned")
)
# 🔁 Read from 7_ESG_Relevance.csv
INPUT_CSV = os.getenv(
    "INPUT_CSV",
    os.path.join(PROJECT_ROOT, "7_esg_relevance.csv")
)
# Write to 8_esg_draft_single.csv
OUTPUT_CSV = os.getenv(
    "OUTPUT_CSV",
    os.path.join(PROJECT_ROOT, "8_esg_draft_single.csv")
)

cwd_source_dir = os.path.abspath("source_md_files_cleaned")
if "SOURCE_DIR" not in os.environ and os.path.isdir(cwd_source_dir) and any(name.lower().endswith(".md") for name in os.listdir(cwd_source_dir)):
    SOURCE_DIR = cwd_source_dir

cwd_input_csv = os.path.abspath("7_esg_relevance.csv")
if "INPUT_CSV" not in os.environ and os.path.exists(cwd_input_csv):
    INPUT_CSV = cwd_input_csv

cwd_output_csv = os.path.abspath("8_esg_draft_single.csv")
if "OUTPUT_CSV" not in os.environ:
    OUTPUT_CSV = cwd_output_csv
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
        "PROMPT_FILE": get_env(f"{px}_PROMPT_FILE", "8_esg_draft_single.yaml"),
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
EXPECTED_OUTPUT_COUNT = 2
OUTPUT_COLUMN_PREFIX = "Output "
MAX_OUTPUT_COLUMNS = EXPECTED_OUTPUT_COUNT
ALL_OUTPUT_COLUMNS = [f"{OUTPUT_COLUMN_PREFIX}{i}" for i in range(1, EXPECTED_OUTPUT_COUNT + 1)]
PROMPT_CACHE = {}

def resolve_prompt_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(PROJECT_ROOT, path)

def load_prompt_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    system = data.get("system", "")
    user_template = data.get("user_template", "{{markdown}}")
    hints = (data.get("profile_hints") or {}).get(PROFILE, "")
    return system, user_template, hints

def get_prompt_bundle():
    prompt_path = resolve_prompt_path(PROMPT_FILE)
    if prompt_path not in PROMPT_CACHE:
        PROMPT_CACHE[prompt_path] = load_prompt_yaml(prompt_path)
    system_prompt, user_template, profile_hint = PROMPT_CACHE[prompt_path]
    return system_prompt, user_template, profile_hint, prompt_path, EXPECTED_OUTPUT_COUNT

def get_required_output_columns(_story_type: str = ""):
    return ALL_OUTPUT_COLUMNS

def is_output_column(name: str) -> bool:
    return bool(re.fullmatch(r"Output\s+\d+", name or ""))

# Render prompt with the markdown file contents and optional metadata
TZ = ZoneInfo("Australia/Melbourne")
def render_user_prompt(md, meta: dict, user_template: str):
    t = user_template
    # Support both tags
    t = t.replace("{{markdown}}", md)
    t = t.replace("{{story_text}}", md)
    # Optional helpful context
    t = t.replace("{{context}}", (meta.get("ESG_Summary") or "").strip())
    t = t.replace("{{jurisdiction}}", (meta.get("Jurisdiction") or "").strip())
    t = t.replace("{{story_type}}", (meta.get("Story_Type") or "").strip())
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

# ----------------- Async Calls -----------------
def build_response_format(expected_outputs: int):
    expected_outputs = max(1, min(expected_outputs, MAX_OUTPUT_COLUMNS))
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "esg_story_outputs",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "outputs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": expected_outputs,
                        "maxItems": expected_outputs
                    }
                },
                "required": ["outputs"]
            }
        }
    }

def _coerce_outputs(obj):
    def _string_blocks(text: str):
        text = text.strip()
        if not text:
            return []
        blocks = [blk.strip() for blk in re.split(r"\n\s*\n", text) if blk.strip()]
        if len(blocks) > 1:
            return blocks
        return [line.strip() for line in text.splitlines() if line.strip()]

    if isinstance(obj, dict):
        raw = obj.get("outputs")
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        if isinstance(raw, str):
            return _string_blocks(raw)
        # If dict without outputs, flatten all values
        values = []
        for value in obj.values():
            if isinstance(value, list):
                values.extend(str(item).strip() for item in value if str(item).strip())
            elif isinstance(value, str):
                values.extend(_string_blocks(value))
            else:
                values.append(str(value).strip())
        return [v for v in values if v]
    if isinstance(obj, list):
        return [str(item).strip() for item in obj if str(item).strip()]
    if isinstance(obj, str):
        return _string_blocks(obj)
    return [str(obj).strip()]

def _extract_response_text(data):
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
    content = ((data.get("choices") or [{}])[0].get("message", {}).get("content", "") or "")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return (content or "").strip()

async def extract_structured_payload(data, file_name):
    content = _extract_response_text(data)
    print(f"   📬 [{file_name}] Structured response received.")
    cleaned = strip_code_fences(content)
    try:
        obj = json.loads(cleaned)
        outputs = _coerce_outputs(obj)
        return {"outputs": outputs}
    except json.JSONDecodeError as e:
        print(f"   ⚠️ [{file_name}] JSON parse failed ({e}); falling back to line split.")
        try:
            obj = yaml.safe_load(cleaned)
            if obj is not None:
                outputs = _coerce_outputs(obj)
                return {"outputs": outputs}
        except yaml.YAMLError as ye:
            print(f"   ⚠️ [{file_name}] YAML parse failed ({ye}); using raw text fallback.")
        outputs = _coerce_outputs(cleaned)
        return {"outputs": outputs}

async def call_openai_async(session, markdown_text, meta, with_web, file_name, system_prompt, user_template, profile_hint, expected_outputs):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing")

    system_block = system_prompt + ("\n\n" + profile_hint if profile_hint else "")
    if with_web:
        system_block += "\n\nNOTE: Web search not enabled."

    user_block = render_user_prompt(markdown_text, meta, user_template)

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
            "response_format": build_response_format(expected_outputs),
        }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    print(f"   📤 [{file_name}] Sending request to OpenAI (structured)…")

    max_attempts = 3
    attempt = 0
    last_error = None
    while attempt < max_attempts:
        attempt += 1
        try:
            async with rpm_limiter:
                async with session.post(url, json=payload, headers=headers, timeout=OPENAI_TIMEOUT_SECONDS) as resp:
                    if resp.status in (429, 500, 502, 503, 504):
                        retry_after = float(resp.headers.get("retry-after", "1"))
                        text = await resp.text()
                        print(f"   ⏳ [{file_name}] HTTP {resp.status} (attempt {attempt}/{max_attempts}), retrying in {retry_after}s… [{text[:120]}]")
                        await asyncio.sleep(retry_after)
                        continue
                    resp.raise_for_status()
                    data = await resp.json()
                    return await extract_structured_payload(data, file_name)
        except Exception as exc:
            last_error = exc
            if attempt < max_attempts:
                wait = min(5, 1 + attempt)
                print(f"   ⚠️ [{file_name}] Attempt {attempt}/{max_attempts} failed: {exc}. Retrying in {wait}s…")
                await asyncio.sleep(wait)
            else:
                print(f"   ❌ [{file_name}] Failed after {max_attempts} attempts: {exc}")
                raise
    raise last_error if last_error else RuntimeError("Unknown error without exception")

# ----------------- Process one file -----------------
async def process_file_async(session, row, sem, idx, total):
    async with sem:
        md_file = (row.get("md_file") or "").strip()
        print(f"\n➡️  [{idx}/{total}] Starting: {md_file}")
        md_path = os.path.join(SOURCE_DIR, md_file)
        if not os.path.exists(md_path):
            print(f"   ❌ [{md_file}] File not found at {md_path}")
            return {"md_file": md_file, "outputs": [], "expected": EXPECTED_OUTPUT_COUNT}

        md_text = read_text_file(md_path)
        print(f"   📄 [{md_file}] File loaded ({len(md_text)} chars).")

        story_type_raw = row.get("Story_Type", "")
        system_prompt, user_template, profile_hint, prompt_path, expected_outputs = get_prompt_bundle()
        story_type_label = story_type_raw.strip() or "missing"
        print(f"   🧭 [{md_file}] Story type '{story_type_label}' → single prompt {prompt_path} (expect {expected_outputs} outputs)")

        meta = {
            "Jurisdiction": row.get("Jurisdiction", ""),
            "ESG_Summary": row.get("ESG_Summary", ""),
            "Story_Type": story_type_raw,
        }

        try:
            start_time = time.time()
            obj = await call_openai_async(
                session,
                md_text,
                meta,
                with_web=(PROFILE == "OAIW"),
                file_name=md_file,
                system_prompt=system_prompt,
                user_template=user_template,
                profile_hint=profile_hint,
                expected_outputs=expected_outputs,
            )
            duration = time.time() - start_time
            print(f"   ✅ [{md_file}] API call completed in {duration:.2f}s.")
        except Exception as e:
            print(f"   ❌ [{md_file}] API failed: {e}")
            return {"md_file": md_file, "outputs": [], "expected": expected_outputs}

        # Validate & coerce result
        outputs = obj.get("outputs") if isinstance(obj, dict) else []
        if not isinstance(outputs, list):
            outputs = []
        outputs = [(o or "").strip() for o in outputs][:expected_outputs]
        if len(outputs) < expected_outputs:
            outputs.extend([""] * (expected_outputs - len(outputs)))
            print(f"   ⚠️ [{md_file}] Expected {expected_outputs} outputs but received fewer; padded with blanks.")

        print(f"   📝 [{md_file}] Parsed outputs count={len(outputs)}.")
        return {"md_file": md_file, "outputs": outputs, "expected": expected_outputs}

# ----------------- Main Async -----------------
async def main_async():
    output_dir = os.path.dirname(OUTPUT_CSV)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # Read input CSV: Date,Title,URL,md_file,ESG_or_not,Jurisdiction,ESG_Summary,ESG_Relevance
    if not os.path.exists(INPUT_CSV):
        raise SystemExit(f"[INPUT ERROR] Not found: {INPUT_CSV}")

    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        input_rows = [row for row in reader]

    required_cols = {"Date", "Title", "URL", "md_file", "ESG_or_not", "Story_Type"}
    if not input_rows or not required_cols.issubset(set(fieldnames)):
        raise SystemExit("[INPUT ERROR] CSV must include: Date, Title, URL, md_file, ESG_or_not, Story_Type")

    print(f"[PROFILE] {PROFILE} | SERIES={MODEL_SERIES} | MODEL={MODEL_NAME} | TEMP={TEMPERATURE} | MAX_TOKENS={MAX_TOKENS}")
    print(f"[PROMPT]  {resolve_prompt_path(PROMPT_FILE)} | expected outputs={EXPECTED_OUTPUT_COUNT}")
    print(f"[INPUT ]  {INPUT_CSV}")
    print(f"[MD DIR]  {SOURCE_DIR}")
    print(f"[OUTPUT]  {OUTPUT_CSV}")
    print(f"[LIMITS]  OPENAI_RPM={OPENAI_RPM} req/min | OPENAI_TIMEOUT_SECONDS={OPENAI_TIMEOUT_SECONDS}s")

    # Candidates: Relevance is populated AND required columns empty (so we don't overwrite)
    candidates = []
    for r in input_rows:
        relevance = (r.get("Relevance") or "").strip()
        if not relevance:
            continue
        has_md = bool((r.get("md_file") or "").strip())
        required_cols_row = get_required_output_columns()
        # Check if at least one target is empty (so reruns can fill missing bits)
        empties = any(not (r.get(col) or "").strip() for col in required_cols_row)
        if has_md and empties:
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

    # Merge back: write original non-output columns plus exactly Output 1 and Output 2.
    print("\n📁 Writing results to CSV...")
    base_fieldnames = [name for name in fieldnames if not is_output_column(name)]
    fieldnames_out = list(base_fieldnames)
    for col in ALL_OUTPUT_COLUMNS:
        if col not in fieldnames_out:
            fieldnames_out.append(col)

    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames_out)
        writer.writeheader()
        for row in input_rows:
            md_file = (row.get("md_file") or "").strip()
            relevance = (row.get("Relevance") or "").strip()
            out_row = {k: (row.get(k) or "").strip() for k in base_fieldnames}
            out_row["ESG_or_not"] = (row.get("ESG_or_not") or "").strip()

            # Ensure all output columns exist on the row
            for col in ALL_OUTPUT_COLUMNS:
                out_row[col] = (row.get(col) or "").strip()

            required_cols_row = get_required_output_columns() if relevance else []

            if md_file in results_map and relevance:
                structured = results_map[md_file]
                outputs = structured.get("outputs", [])
                for idx, col in enumerate(required_cols_row):
                    if idx < len(outputs) and not out_row[col]:
                        out_row[col] = outputs[idx]

            writer.writerow(out_row)

    print(f"\n✅ [DONE] Wrote {len(input_rows)} rows → {OUTPUT_CSV}")

if __name__ == "__main__":
    asyncio.run(main_async())

import os
import re
import csv
import json
import time
import chardet
import yaml
import asyncio
import aiohttp
import argparse
import shutil
from dotenv import load_dotenv
from aiolimiter import AsyncLimiter


# ----------------- CLI -----------------
parser = argparse.ArgumentParser(
    description="Generate short event descriptions from event page markdown files and write to 10_events_data.csv"
)
group = parser.add_mutually_exclusive_group(required=False)
group.add_argument("--OAI", action="store_true", help="OpenAI, no web")
group.add_argument("--OAIW", action="store_true", help="OpenAI, with web")
args = parser.parse_args()

if args.OAI:
    PROFILE = "OAI"
elif args.OAIW:
    PROFILE = "OAIW"
else:
    PROFILE = "OAI"


# ----------------- ENV -----------------
load_dotenv()

INPUT_CSV = r"10.2_job_links.csv"
SOURCE_DIR = r"10_job_md_files"
OUTPUT_CSV = r"11_job_all_data.csv"
QUALITY_CHECK_DIR = os.getenv(
    "QUALITY_CHECK_DIR",
    r"Quality_Check",
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_RPM = int(os.getenv("OPENAI_RPM", "10000"))
OPENAI_TIMEOUT_SECONDS = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "180"))
MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "3"))
BASE_BACKOFF = float(os.getenv("OPENAI_BACKOFF_SECONDS", "1.0"))

rpm_limiter = AsyncLimiter(OPENAI_RPM, time_period=60)


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
MAX_TOKENS = CFG["MAX_TOKENS"] if CFG["MAX_TOKENS"] is not None else 400
GPT5_REASONING_EFFORT = (get_env("OAI_REASONING_EFFORT", "low") or "low").strip().lower()
GPT5_TEXT_VERBOSITY = (get_env("OAI_TEXT_VERBOSITY", "medium") or "medium").strip().lower()
PROMPT_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "11_job_description.yaml")

if not MODEL_NAME:
    raise SystemExit(f"[CONFIG ERROR] {PROFILE}_MODEL_NAME is not set in .env")


def load_prompt_yaml(path: str):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    system = data.get("system", "")
    user_template = data.get("user_template", "{{markdown}}")
    return system, user_template


SYSTEM_PROMPT, USER_TEMPLATE = load_prompt_yaml(PROMPT_FILE_PATH)


def render_user_prompt(markdown_text: str) -> str:
    return USER_TEMPLATE.replace("{{markdown}}", markdown_text)


def read_text_file(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    enc = chardet.detect(raw).get("encoding") or "utf-8"
    return raw.decode(enc, errors="replace")


def strip_code_fences(text: str) -> str:
    m = re.match(r"^```(?:json|yaml|md|markdown|text)?\s*(.*?)\s*```$", text.strip(), re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else text


async def call_openai_async(session: aiohttp.ClientSession, markdown_text: str, file_name: str) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing")

    if MODEL_SERIES == "gpt5":
        url = f"{OPENAI_BASE_URL}/responses"
        payload = {
            "model": MODEL_NAME,
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": render_user_prompt(markdown_text)},
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
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": render_user_prompt(markdown_text)},
            ],
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
        }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"   📤 [{file_name}] Sending request... (attempt {attempt}/{MAX_RETRIES})")
        try:
            async with rpm_limiter:
                async with session.post(url, json=payload, headers=headers, timeout=OPENAI_TIMEOUT_SECONDS) as resp:
                    body_text = await resp.text()
                    if resp.status in (429, 500, 502, 503, 504):
                        if attempt < MAX_RETRIES:
                            await asyncio.sleep(BASE_BACKOFF * (2 ** (attempt - 1)))
                            continue
                        resp.raise_for_status()
                    resp.raise_for_status()
                    data = json.loads(body_text)
                    if MODEL_SERIES == "gpt5":
                        text_out = (data.get("output_text") or "").strip()
                        if not text_out:
                            chunks = []
                            for item in (data.get("output") or []):
                                for c in (item.get("content") or []):
                                    if c.get("type") in {"output_text", "text"} and c.get("text"):
                                        chunks.append(c["text"])
                            text_out = "\n".join(chunks).strip()
                    else:
                        text_out = ((data.get("choices") or [{}])[0].get("message", {}).get("content", "") or "").strip()
                    return strip_code_fences(text_out)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt < MAX_RETRIES:
                print(f"   ⚠️  [{file_name}] {e}. Retrying...")
                await asyncio.sleep(BASE_BACKOFF * (2 ** (attempt - 1)))
                continue
            raise

    raise RuntimeError(f"[{file_name}] Exhausted retries")


def pick_md_filename(row: dict) -> str:
    return (row.get("md_file") or row.get("FileName") or row.get("filename") or "").strip()


async def process_row_async(session: aiohttp.ClientSession, row: dict, sem: asyncio.Semaphore, idx: int, total: int) -> tuple[str, str]:
    md_file = pick_md_filename(row)
    async with sem:
        print(f"\n➡️  [{idx}/{total}] Starting: {md_file or '(missing md file)'}")
        if not md_file:
            return "", ""

        md_path = os.path.join(SOURCE_DIR, md_file)
        if not os.path.exists(md_path):
            print(f"   ❌ [{md_file}] File not found")
            return md_file, ""

        md_text = read_text_file(md_path)
        if not md_text.strip():
            print(f"   ⚠️ [{md_file}] Empty file")
            return md_file, ""

        try:
            start = time.time()
            desc = await call_openai_async(session, md_text, md_file)
            print(f"   ✅ [{md_file}] Done in {time.time() - start:.2f}s")
            return md_file, desc.strip()
        except Exception as e:
            print(f"   ❌ [{md_file}] API failed: {e}")
            return md_file, ""


async def main_async():
    if not os.path.exists(INPUT_CSV):
        raise SystemExit(f"[INPUT ERROR] Not found: {INPUT_CSV}")

    with open(INPUT_CSV, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        input_rows = [row for row in reader]

    if not input_rows:
        raise SystemExit("[INPUT ERROR] Input CSV has no data rows.")

    has_md_ref = any(name in fieldnames for name in ["md_file", "FileName", "filename"])
    if not has_md_ref:
        raise SystemExit("[INPUT ERROR] Input CSV must contain one of: md_file, FileName, filename")

    print(f"[PROFILE] {PROFILE} | SERIES={MODEL_SERIES} | MODEL={MODEL_NAME} | TEMP={TEMPERATURE} | MAX_TOKENS={MAX_TOKENS}")
    print(f"[PROMPT]  {PROMPT_FILE_PATH}")
    print(f"[INPUT ]  {INPUT_CSV}")
    print(f"[MD DIR]  {SOURCE_DIR}")
    print(f"[OUTPUT]  {OUTPUT_CSV}")
    qc_output_path = os.path.join(QUALITY_CHECK_DIR, os.path.basename(OUTPUT_CSV))
    print(f"[OUTPUT_QC] {qc_output_path}")
    print(f"[LIMITS]  OPENAI_RPM={OPENAI_RPM} req/min | OPENAI_TIMEOUT_SECONDS={OPENAI_TIMEOUT_SECONDS}s | RETRIES={MAX_RETRIES}")

    concurrency = int(os.getenv("OPENAI_CONCURRENCY", os.getenv("LOCAL_CONCURRENCY", "12")))
    sem = asyncio.Semaphore(concurrency)

    description_by_md: dict[str, str] = {}
    connector = aiohttp.TCPConnector(limit=concurrency, limit_per_host=concurrency)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            asyncio.create_task(process_row_async(session, row, sem, i + 1, len(input_rows)))
            for i, row in enumerate(input_rows)
        ]
        for task in asyncio.as_completed(tasks):
            md_file, description = await task
            if md_file:
                description_by_md[md_file] = description

    fieldnames_out = list(fieldnames)
    desc_col = "Job_Description"
    if desc_col not in fieldnames_out:
        fieldnames_out.append(desc_col)

    output_dir = os.path.dirname(OUTPUT_CSV)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames_out)
        writer.writeheader()
        for row in input_rows:
            md_file = pick_md_filename(row)
            out_row = {k: (row.get(k) or "") for k in fieldnames}
            out_row[desc_col] = description_by_md.get(md_file, "") if md_file else ""
            writer.writerow(out_row)

    try:
        os.makedirs(QUALITY_CHECK_DIR, exist_ok=True)
        shutil.copy(OUTPUT_CSV, qc_output_path)
        print(f"📎 Mirrored CSV to Quality_Check → {qc_output_path}")
    except Exception as e:
        print(f"⚠️  Failed to mirror CSV to Quality_Check: {e}")

    print(f"\n✅ [DONE] Wrote {len(input_rows)} rows → {OUTPUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main_async())

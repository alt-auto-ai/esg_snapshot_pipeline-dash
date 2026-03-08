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
from dotenv import load_dotenv
from aiolimiter import AsyncLimiter

# ----------------- CLI -----------------
parser = argparse.ArgumentParser(
    description="For rows with ESG_or_not='Yes', classify ESG relevance and write column 'ESG_Relevance'."
)
group = parser.add_mutually_exclusive_group(required=False)
group.add_argument("--OAI", action="store_true", help="OpenAI, no web")
group.add_argument("--OAIW", action="store_true", help="OpenAI, with web")
group.add_argument("--OLR", action="store_true", help="Ollama reasoning")
group.add_argument("--OLNR", action="store_true", help="Ollama non-reasoning")
args = parser.parse_args()

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

SOURCE_DIR = os.getenv(
    "SOURCE_DIR",
    r"story_md_files"
)
INPUT_CSV = os.getenv(
    "INPUT_CSV",
    r"6_ESG_Summary.csv"
)
OUTPUT_CSV = os.getenv(
    "OUTPUT_CSV",
    r"7_esg_relevance.csv"
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_RPM = int(os.getenv("OPENAI_RPM", "10000"))
MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "3"))
BASE_BACKOFF = float(os.getenv("OPENAI_BACKOFF_SECONDS", "1.0"))
rpm_limiter = AsyncLimiter(OPENAI_RPM, time_period=60)


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
MAX_TOKENS = CFG["MAX_TOKENS"] if CFG["MAX_TOKENS"] is not None else 200

if not MODEL_NAME:
    raise SystemExit(f"[CONFIG ERROR] {PROFILE}_MODEL_NAME is not set in .env")

# Prompt path from script only
PROMPT_FILE_PATH = r"7_esg_relevance copy.yaml"


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


def read_text_file(path):
    with open(path, "rb") as f:
        raw = f.read()
    enc = chardet.detect(raw).get("encoding") or "utf-8"
    return raw.decode(enc, errors="replace")


def strip_code_fences(s: str):
    m = re.match(r"^```(?:json|yaml|md|markdown)?\s*(.*?)\s*```$", s.strip(), re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else s


def normalize_relevance(raw: str) -> str:
    text = strip_code_fences(raw or "").strip()
    if not text:
        return ""

    m = re.search(r"\b(High|Low|Medium)\b", text, flags=re.IGNORECASE)
    if not m:
        return ""

    value = m.group(1).lower()
    return value.capitalize()


def resolve_md_path(md_file: str) -> str:
    md_file = (md_file or "").strip()
    if not md_file:
        return ""

    exact_path = os.path.join(SOURCE_DIR, md_file)
    if os.path.exists(exact_path):
        return exact_path

    m = re.match(r"^(\d+)\.md$", md_file, flags=re.IGNORECASE)
    if m:
        prefix = m.group(1)
        candidates = sorted(glob.glob(os.path.join(SOURCE_DIR, f"{prefix}_*.md")))
        if candidates:
            return candidates[0]

    stem = os.path.splitext(md_file)[0]
    candidates = sorted(glob.glob(os.path.join(SOURCE_DIR, f"{stem}*.md")))
    return candidates[0] if candidates else ""


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

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with rpm_limiter:
                async with session.post(url, json=payload, headers=headers, timeout=120) as resp:
                    text = await resp.text()
                    if resp.status in (429, 500, 502, 503, 504):
                        if attempt < MAX_RETRIES:
                            delay = BASE_BACKOFF * (2 ** (attempt - 1))
                            await asyncio.sleep(delay)
                            continue
                        resp.raise_for_status()

                    resp.raise_for_status()
                    data = json.loads(text)
                    return strip_code_fences(data["choices"][0]["message"]["content"].strip())
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt < MAX_RETRIES:
                delay = BASE_BACKOFF * (2 ** (attempt - 1))
                await asyncio.sleep(delay)
                continue
            raise RuntimeError(f"[{file_name}] Exhausted retries")

    raise RuntimeError(f"[{file_name}] Exhausted retries")


async def process_md_file_async(session, md_file, sem, idx, total):
    async with sem:
        print(f"\n➡️  [{idx}/{total}] Starting: {md_file}")
        md_path = resolve_md_path(md_file)
        if not md_path:
            print(f"   ❌ [{md_file}] File not found in {SOURCE_DIR}")
            return md_file, ""

        md_text = read_text_file(md_path)
        print(f"   📄 [{md_file}] File loaded ({len(md_text)} chars).")

        try:
            start_time = time.time()
            raw = await call_openai_async(session, md_text, with_web=(PROFILE == "OAIW"), file_name=md_file)
            duration = time.time() - start_time
            print(f"   ✅ [{md_file}] API call completed in {duration:.2f}s.")
        except Exception as e:
            print(f"   ❌ [{md_file}] API failed: {e}")
            return md_file, ""

        relevance = normalize_relevance(raw)
        print(f"   📊 [{md_file}] ESG_Relevance={relevance or 'EMPTY'}")
        return md_file, relevance


async def main_async():
    output_dir = os.path.dirname(OUTPUT_CSV)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(INPUT_CSV):
        raise SystemExit(f"[INPUT ERROR] Not found: {INPUT_CSV}")

    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = [row for row in reader]

    if not rows:
        raise SystemExit("[INPUT ERROR] Input CSV has no rows.")

    fields_ci = {k.strip().lower(): k for k in fieldnames}
    esg_field = fields_ci.get("esg_or_not")
    md_field = fields_ci.get("md_file")

    if not esg_field or not md_field:
        raise SystemExit("[INPUT ERROR] Required columns 'ESG_or_not' and 'md_file' not found.")

    candidates = [
        r for r in rows
        if (r.get(esg_field) or "").strip().lower() == "yes" and (r.get(md_field) or "").strip()
    ]

    unique_md_files = sorted({(r.get(md_field) or "").strip() for r in candidates})

    print(f"[PROFILE] {PROFILE} | MODEL={MODEL_NAME} | TEMP={TEMPERATURE} | MAX_TOKENS={MAX_TOKENS}")
    print(f"[PROMPT]  {PROMPT_FILE_PATH}")
    print(f"[INPUT ]  {INPUT_CSV}")
    print(f"[MD DIR]  {SOURCE_DIR}")
    print(f"[OUTPUT]  {OUTPUT_CSV}")
    print(f"[INFO]    ESG='Yes' rows: {len(candidates)} | unique md files: {len(unique_md_files)}")

    relevance_map = {}
    concurrency_limit = int(os.getenv("LOCAL_CONCURRENCY", "16"))
    sem = asyncio.Semaphore(concurrency_limit)
    connector = aiohttp.TCPConnector(limit=concurrency_limit, limit_per_host=concurrency_limit)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            asyncio.create_task(process_md_file_async(session, md_file, sem, i + 1, len(unique_md_files)))
            for i, md_file in enumerate(unique_md_files)
        ]

        for coro in asyncio.as_completed(tasks):
            md_file, relevance = await coro
            relevance_map[md_file] = relevance

    fieldnames_out = list(fieldnames)
    if "ESG_Relevance" not in fieldnames_out:
        fieldnames_out.append("ESG_Relevance")

    print("\n📁 Writing results to CSV...")
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames_out)
        writer.writeheader()

        for row in rows:
            out_row = {k: (row.get(k) or "").strip() for k in fieldnames}
            md_file = (row.get(md_field) or "").strip()
            esg = (row.get(esg_field) or "").strip().lower()
            out_row["ESG_Relevance"] = relevance_map.get(md_file, "") if esg == "yes" else ""
            writer.writerow(out_row)

    print(f"\n✅ [DONE] Wrote {len(rows)} rows → {OUTPUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main_async())

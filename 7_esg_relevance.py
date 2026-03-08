import os
import re
import csv
import json
import glob
import time
import chardet
import argparse
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
args = parser.parse_args()

if args.OAI:
    PROFILE = "OAI"
elif args.OAIW:
    PROFILE = "OAIW"
else:
    PROFILE = "OAI"

# ----------------- ENV -----------------
load_dotenv()

SOURCE_DIR = os.getenv(
    "SOURCE_DIR",
    r"/home/z440/Desktop/Projects/ESG_SNAPSHOT_AUTOMATED/source_md_files_cleaned"
)
INPUT_CSV = os.getenv(
    "INPUT_CSV",
    r"5_story_jurisdiction.csv"
)
OUTPUT_CSV = os.getenv(
    "OUTPUT_CSV",
    r"7_esg_relevance.csv"
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
MAX_TOKENS = CFG["MAX_TOKENS"] if CFG["MAX_TOKENS"] is not None else 200
GPT5_REASONING_EFFORT = (get_env("OAI_REASONING_EFFORT", "low") or "low").strip().lower()
GPT5_TEXT_VERBOSITY = (get_env("OAI_TEXT_VERBOSITY", "medium") or "medium").strip().lower()

if not MODEL_NAME:
    raise SystemExit(f"[CONFIG ERROR] {PROFILE}_MODEL_NAME is not set in .env")

SYSTEM_PROMPT = (
    "You are a strict Environmental, Social, and Governance (ESG) relevance rater. The input is a markdown (.md) file containing raw webpage content from a general news story. Identify and evaluate only the main news story in the file, ignoring navigation, headers, footers, repeated sections, and other webpage elements. Do not assume the story is ESG-related. Score only to the extent that the story clearly contains the specified ESG characteristic. Return only a single integer from 0 to 10 (no words, no punctuation, no explanation)."
)

RATING_PROMPTS = {
    "Cross_sector_relevance": "Rate the main story in this markdown file from 0 to 10 for direct relevance across multiple business sectors in the Environmental, Social, and Governance (ESG) landscape; score 0 if the story does not clearly involve ESG-relevant cross-sector business relevance, and score low if relevance is narrow, indirect, political, or non-business; return only one integer from 0 to 10.",
    "Policy_significance": "Rate the main story in this markdown file from 0 to 10 for direct Environmental, Social, and Governance (ESG) policy, regulatory, legislative, or statutory significance; score 0 if the story does not clearly involve ESG policy implications, and score low if it is mainly political, diplomatic, or administrative without clear ESG policy implications; return only one integer from 0 to 10.",
    "Business_risk_opportunity": "Rate the main story in this markdown file from 0 to 10 for clear and material Environmental, Social, and Governance (ESG)-related business risks and/or opportunities; score 0 if the story does not clearly create or signal ESG-related business risks or opportunities, and score low if impacts are vague, indirect, or not clearly relevant to organisations; return only one integer from 0 to 10.",
    "Strategic_ESG_signal": "Rate the main story in this markdown file from 0 to 10 for how strongly it signals a meaningful Environmental, Social, and Governance (ESG) shift, direction, or emerging trend; score 0 if the story does not clearly indicate an ESG-related shift, direction, or trend, and score low if it does not clearly indicate broader ESG change; return only one integer from 0 to 10.",
    "Corporate_governance_relevance": "Rate the main story in this markdown file from 0 to 10 for direct relevance to board, executive, or governance decision-making on Environmental, Social, and Governance (ESG) matters; score 0 if the story is not clearly relevant to ESG-related governance decision-making, and score low if it is operational, political, or not governance-relevant; return only one integer from 0 to 10.",
    "Forward_looking_insight": "Rate the main story in this markdown file from 0 to 10 for clear insight into future Environmental, Social, and Governance (ESG) developments; score 0 if the story does not clearly provide future-oriented ESG insight, and score low if it mainly reports a current event without meaningful future ESG implications; return only one integer from 0 to 10.",
    "Member Relevance": "Rate the main story in this markdown file from 0 to 10 for direct relevance to sectors such as finance, infrastructure, consulting, energy, mobility, forestry, higher education, research, Indigenous business, and diversified holdings; score 0 if the story is not clearly relevant to these sectors in an ESG-relevant way, and score low if sector relevance is weak, indirect, or incidental; return only one integer from 0 to 10.",
}


def read_text_file(path):
    with open(path, "rb") as f:
        raw = f.read()
    enc = chardet.detect(raw).get("encoding") or "utf-8"
    return raw.decode(enc, errors="replace")


def strip_code_fences(s: str):
    m = re.match(r"^```(?:json|yaml|md|markdown)?\s*(.*?)\s*```$", s.strip(), re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else s


def normalize_rating(raw: str) -> str:
    text = strip_code_fences(raw or "").strip()
    if not text:
        return ""
    m = re.search(r"\b(10|[0-9])\b", text)
    if not m:
        return ""
    value = int(m.group(1))
    return str(value) if 0 <= value <= 10 else ""


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

    system_block = SYSTEM_PROMPT
    if with_web:
        system_block += "\n\nNOTE: Web search not enabled."

    def build_payload(prompt_text):
        user_block = f"{prompt_text}\n\n--- INPUT MARKDOWN (Begin) ---\n{markdown_text}\n--- INPUT MARKDOWN (End) ---"
        if MODEL_SERIES == "gpt5":
            return (
                f"{OPENAI_BASE_URL}/responses",
                {
                    "model": MODEL_NAME,
                    "input": [
                        {"role": "system", "content": system_block},
                        {"role": "user", "content": user_block},
                    ],
                    "reasoning": {"effort": GPT5_REASONING_EFFORT},
                    "text": {"verbosity": GPT5_TEXT_VERBOSITY},
                    "max_output_tokens": MAX_TOKENS,
                },
            )
        return (
            f"{OPENAI_BASE_URL}/chat/completions",
            {
                "model": MODEL_NAME,
                "messages": [
                    {"role": "system", "content": system_block},
                    {"role": "user", "content": user_block},
                ],
                "temperature": TEMPERATURE,
                "max_tokens": MAX_TOKENS,
            },
        )

    def extract_text(data):
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
        return ((data.get("choices") or [{}])[0].get("message", {}).get("content", "") or "").strip()
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    ratings = {}
    for col, prompt_text in RATING_PROMPTS.items():
        got = ""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                url, payload = build_payload(prompt_text)
                async with rpm_limiter:
                    async with session.post(url, json=payload, headers=headers, timeout=OPENAI_TIMEOUT_SECONDS) as resp:
                        text = await resp.text()
                        if resp.status in (429, 500, 502, 503, 504):
                            if attempt < MAX_RETRIES:
                                delay = BASE_BACKOFF * (2 ** (attempt - 1))
                                await asyncio.sleep(delay)
                                continue
                            resp.raise_for_status()

                        resp.raise_for_status()
                        data = json.loads(text)
                        got = normalize_rating(extract_text(data))
                        break
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt < MAX_RETRIES:
                    delay = BASE_BACKOFF * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)
                    continue
                raise RuntimeError(f"[{file_name}] Exhausted retries for {col}")
        ratings[col] = got

    return ratings


async def process_md_file_async(session, md_file, sem, idx, total):
    async with sem:
        print(f"\n➡️  [{idx}/{total}] Starting: {md_file}")
        md_path = resolve_md_path(md_file)
        if not md_path:
            print(f"   ❌ [{md_file}] File not found in {SOURCE_DIR}")
            return md_file, {}

        md_text = read_text_file(md_path)
        print(f"   📄 [{md_file}] File loaded ({len(md_text)} chars).")

        try:
            start_time = time.time()
            raw = await call_openai_async(session, md_text, with_web=(PROFILE == "OAIW"), file_name=md_file)
            duration = time.time() - start_time
            print(f"   ✅ [{md_file}] API call completed in {duration:.2f}s.")
        except Exception as e:
            print(f"   ❌ [{md_file}] API failed: {e}")
            return md_file, {}

        print(f"   📊 [{md_file}] Ratings={raw}")
        return md_file, raw


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

    print(f"[PROFILE] {PROFILE} | SERIES={MODEL_SERIES} | MODEL={MODEL_NAME} | TEMP={TEMPERATURE} | MAX_TOKENS={MAX_TOKENS}")
    print(f"[INPUT ]  {INPUT_CSV}")
    print(f"[MD DIR]  {SOURCE_DIR}")
    print(f"[OUTPUT]  {OUTPUT_CSV}")
    print(f"[INFO]    ESG='Yes' rows: {len(candidates)} | unique md files: {len(unique_md_files)}")
    print(f"[LIMITS]  OPENAI_RPM={OPENAI_RPM} req/min | OPENAI_TIMEOUT_SECONDS={OPENAI_TIMEOUT_SECONDS}s")

    relevance_map = {}
    concurrency_limit = int(os.getenv("OPENAI_CONCURRENCY", os.getenv("LOCAL_CONCURRENCY", "16")))
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

    rating_cols = list(RATING_PROMPTS.keys())
    fieldnames_out = [f for f in fieldnames if f not in rating_cols] + rating_cols

    print("\n📁 Writing results to CSV...")
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames_out)
        writer.writeheader()

        for row in rows:
            out_row = {k: (row.get(k) or "").strip() for k in fieldnames if k in fieldnames_out}
            md_file = (row.get(md_field) or "").strip()
            esg = (row.get(esg_field) or "").strip().lower()
            ratings = relevance_map.get(md_file, {}) if esg == "yes" else {}
            for col in rating_cols:
                out_row[col] = ratings.get(col, "")
            writer.writerow(out_row)

    print(f"\n✅ [DONE] Wrote {len(rows)} rows → {OUTPUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main_async())

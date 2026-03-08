# 1_source_crawl.py
# ------------------------------------------------------------
# Two-phase crawler using Crawl4AI:
#  - Phase 1 (Discovery): visit each URL from the 'URL' column and auto-guess a main-content CSS selector.
#  - Phase 2 (Extraction): re-crawl using the guessed selector to extract markdown.
#
# Writes selectors back by adding a single 'URL_selector' column next to the 'URL' column.
#
# Also writes a CSV mapping (Date, Title, URL) -> md filename or 'crawl_failure'.
#
# Requirements:
#   pip install crawl4ai beautifulsoup4
# ------------------------------------------------------------

import os
import csv
import asyncio
import inspect
from typing import List, Optional, Tuple, Dict
from urllib.parse import urlparse
from pathlib import Path

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
    MemoryAdaptiveDispatcher
)
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

from bs4 import BeautifulSoup  # pip install beautifulsoup4

# =========================
# CONFIG
# =========================
CSV_PATH = r"10.1_job_data.csv"
CSV_WITH_SELECTORS_PATH = r"10.2_job_links_with_selectors.csv"

OUTPUT_DIR = r"10_job_md_files"
FAIL_LOG = r"10.2_job_failures.csv"
INVALID_LOG = r"10.2_job_invalid_urls.csv"
NORMALIZED_MAP_LOG = r"10.2_job_normalized_map.csv"
SELECTOR_LOG = r"10.2_job_guessed_selectors.csv"

# NEW: output file mapping input rows to md filenames
STORY_FILENAME_LINKS = r"10.2_job_links.csv"  # NEW

MAX_CONCURRENT = 10
DEDUPE = False  # keep False so you crawl duplicates too

# ---- Phase 1 (Discovery) filtering knobs ----
EXCLUDED_TAGS = ["nav", "footer", "header", "form", "aside"]
WORD_COUNT_THRESHOLD = 10
EXCLUDE_EXTERNAL_LINKS = True
EXCLUDE_SOCIAL_MEDIA_LINKS = True
EXCLUDE_DOMAINS = ["ads.com", "adtrackers.com", "spammynews.org"]
EXCLUDE_SOCIAL_MEDIA_DOMAINS = ["facebook.com", "twitter.com", "linkedin.com"]
EXCLUDE_EXTERNAL_IMAGES = True

# Discovery & extraction delays (if supported by your crawl4ai)
DISCOVERY_DELAY_MS = 2000
EXTRACTION_DELAY_MS = 2000

# ---- Phase 2 (Extraction) clean filters (from your example script) ----
PH2_WORD_COUNT_THRESHOLD = 10
PH2_EXCLUDED_TAGS = ["nav", "footer"]
PH2_EXCLUDE_EXTERNAL_LINKS = True
PH2_EXCLUDE_SOCIAL_MEDIA_LINKS = True
PH2_EXCLUDE_DOMAINS = ["ads.com", "spammytrackers.net"]
PH2_EXCLUDE_EXTERNAL_IMAGES = True
MIN_ACCEPTABLE_MD_CHARS = 800

# =========================
# URL handling
# =========================
def normalize_url(u: str) -> Optional[str]:
    if not u:
        return None
    u = u.strip().strip('"\'')
    if not u or u.lower().startswith(("mailto:", "javascript:", "#", "tel:")):
        return None
    parsed = urlparse(u)
    if not parsed.scheme:
        u = "https://" + u
        parsed = urlparse(u)
    if parsed.scheme in ("http", "https", "file") and (parsed.netloc or parsed.scheme == "file"):
        return u
    return None

def find_url_column(fieldnames: List[str]) -> Optional[str]:
    if not fieldnames:
        return None
    preferred = ["URL", "url", "Url", "Link", "link", "EventURL", "Event_URL", "event_url"]
    for name in preferred:
        if name in fieldnames:
            return name
    normalized = {
        name: "".join(ch for ch in (name or "").strip().lower() if ch.isalnum())
        for name in fieldnames
    }
    for original, norm in normalized.items():
        if norm in {"url", "link", "eventurl", "eventlink"}:
            return original
    return None

# (kept for minimal change compatibility; not used in new URL-only flow)
def sniff_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except Exception:
        class Default(csv.Dialect):
            delimiter = ","
            quotechar = '"'
            doublequote = True
            skipinitialspace = True
            lineterminator = "\n"
            quoting = csv.QUOTE_MINIMAL
        return Default()

# =========================
# Read URLs ONLY from 'URL' column
# =========================
def read_urls_from_csv(csv_path: str) -> Tuple[List[str], List[Tuple[str, str]], List[Tuple[str, str]], str]:
    """
    Returns (urls, invalids[(raw, reason)], normalized_map[(raw, normalized)], url_column).
    """
    urls: List[str] = []
    invalids: List[Tuple[str, str]] = []
    normalized_map: List[Tuple[str, str]] = []

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        url_column = find_url_column(reader.fieldnames or [])
        if not url_column:
            raise ValueError("Input CSV must contain a URL-like column (e.g., URL/url/link).")
        for row in reader:
            raw = (row.get(url_column) or "").strip()
            if not raw:
                continue
            norm = normalize_url(raw)
            if norm:
                urls.append(norm)
                normalized_map.append((raw, norm))
            else:
                looks_urlish = any(tok in raw.lower() for tok in (".com", ".org", ".gov", ".net", "http", "www."))
                if looks_urlish:
                    invalids.append((raw, "could_not_normalize"))
                    normalized_map.append((raw, ""))

    if DEDUPE:
        seen = set()
        deduped = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        urls = deduped

    return urls, invalids, normalized_map, url_column

# =========================
# File helpers
# =========================
def write_csv(path: str, headers: List[str], rows: List[Tuple[str, ...]]):
    if not rows:
        return
    dir_path = os.path.dirname(path)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)
    write_header = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(headers)
        for r in rows:
            w.writerow(list(r))

def sequential_filename(index: int) -> str:
    return f"{index}.md"

def save_markdown(output_dir: str, filename: str, markdown: str):
    os.makedirs(output_dir, exist_ok=True)
    path = Path(output_dir) / filename
    with open(path, "w", encoding="utf-8") as f:
        f.write(markdown)

def append_failure_log(path: str, url: str, error: str):
    write_csv(path, ["url", "error"], [(url, error)])

def markdown_looks_incomplete(markdown: str) -> bool:
    if not markdown:
        return True
    text = markdown.strip()
    if len(text) < MIN_ACCEPTABLE_MD_CHARS:
        return True
    generic_markers = [
        "Register to receive notifications for jobs that interest you",
        "Open Job Search",
    ]
    if len(text) < 2000 and any(marker in text for marker in generic_markers):
        return True
    return False

# =========================
# CrawlerRunConfig builders (version-tolerant)
# =========================
def build_base_config(css_selector: Optional[str] = None, delay_ms: Optional[int] = None) -> CrawlerRunConfig:
    desired_kwargs = {
        "markdown_generator": DefaultMarkdownGenerator(),
        "css_selector": css_selector,
        "word_count_threshold": WORD_COUNT_THRESHOLD,
        "excluded_tags": EXCLUDED_TAGS,
        "exclude_external_links": EXCLUDE_EXTERNAL_LINKS,
        "exclude_social_media_links": EXCLUDE_SOCIAL_MEDIA_LINKS,
        "exclude_domains": EXCLUDE_DOMAINS,
        "exclude_social_media_domains": EXCLUDE_SOCIAL_MEDIA_DOMAINS,
        "exclude_external_images": EXCLUDE_EXTERNAL_IMAGES,
        "cache_mode": CacheMode.BYPASS,
        "stream": False,
    }
    sig = inspect.signature(CrawlerRunConfig.__init__)
    allowed = set(p for p in sig.parameters.keys() if p != "self")
    filtered = {k: v for k, v in desired_kwargs.items() if k in allowed and v is not None}
    cfg = CrawlerRunConfig(**filtered)
    if delay_ms is not None and "delay_before_extract_ms" in allowed:
        setattr(cfg, "delay_before_extract_ms", delay_ms)
    return cfg

def build_extraction_config(css_selector: Optional[str] = None, delay_ms: Optional[int] = None) -> CrawlerRunConfig:
    desired_kwargs = {
        "markdown_generator": DefaultMarkdownGenerator(),
        "css_selector": css_selector,
        "word_count_threshold": PH2_WORD_COUNT_THRESHOLD,
        "excluded_tags": PH2_EXCLUDED_TAGS,
        "exclude_external_links": PH2_EXCLUDE_EXTERNAL_LINKS,
        "exclude_social_media_links": PH2_EXCLUDE_SOCIAL_MEDIA_LINKS,
        "exclude_domains": PH2_EXCLUDE_DOMAINS,
        "exclude_external_images": PH2_EXCLUDE_EXTERNAL_IMAGES,
        "cache_mode": CacheMode.BYPASS,
        "stream": False,
    }
    sig = inspect.signature(CrawlerRunConfig.__init__)
    allowed = set(p for p in sig.parameters.keys() if p != "self")
    filtered = {k: v for k, v in desired_kwargs.items() if k in allowed and v is not None}
    cfg = CrawlerRunConfig(**filtered)
    if delay_ms is not None and "delay_before_extract_ms" in allowed:
        setattr(cfg, "delay_before_extract_ms", delay_ms)
    return cfg

# =========================
# Selector guesser (heuristic)
# =========================
def elem_text_len(elem) -> int:
    for s in elem(["script", "style", "noscript"]):
        s.decompose()
    txt = elem.get_text(separator=" ", strip=True)
    return len(txt)

def link_text_len(elem) -> int:
    total = 0
    for a in elem.find_all("a"):
        total += len(a.get_text(separator=" ", strip=True))
    return total

def build_selector_for_element(el) -> Optional[str]:
    if el.has_attr("id") and el["id"]:
        return f"#{el['id']}"
    classes = el.get("class", [])
    if classes:
        cls = [c for c in classes if c and all(ch.isalnum() or ch in "-_" for ch in c)]
        if cls:
            cls = cls[:2]
            return f"{el.name}." + ".".join(cls)
    if el.parent:
        same_tag_siblings = [c for c in el.parent.find_all(el.name, recursive=False)]
        idx = same_tag_siblings.index(el) + 1
        return f"{el.name}:nth-of-type({idx})"
    return el.name

def guess_main_selector_from_html(html: str) -> Optional[str]:
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["header", "nav", "footer"]):
        tag.decompose()
    candidates = []
    for tag_name in ["main", "article", "section", "div"]:
        candidates.extend(soup.find_all(tag_name))
    if not candidates:
        return None
    KEYWORDS = ["content", "main", "article", "post", "entry", "body", "container", "page", "read", "story"]
    best_score = -1.0
    best_elem = None
    for el in candidates:
        tlen = elem_text_len(el)
        if tlen < 200:
            continue
        raw_html_len = len(str(el)) + 1
        text_density = tlen / raw_html_len
        ltxt = link_text_len(el)
        link_density = ltxt / (tlen + 1)
        h_count = len(el.find_all(["h1", "h2", "h3"]))
        p_count = len(el.find_all("p"))
        attr_text = " ".join([
            el.get("id", "") or "",
            " ".join(el.get("class", []) or [])
        ]).lower()
        keyword_hits = sum(1 for k in KEYWORDS if k in attr_text)
        tag_bonus = 0.0
        if el.name == "main":
            tag_bonus += 0.6
        elif el.name == "article":
            tag_bonus += 0.4
        elif el.name == "section":
            tag_bonus += 0.2
        score = (
            1.0 * (min(tlen, 5000) / 5000.0) +
            1.0 * text_density -
            1.2 * link_density +
            0.1 * h_count +
            0.2 * min(p_count, 30) +
            0.3 * keyword_hits +
            tag_bonus
        )
        if score > best_score:
            best_score = score
            best_elem = el
    if not best_elem:
        return None
    return build_selector_for_element(best_elem)

# =========================
# Crawl phases
# =========================
async def discovery_phase(urls: List[str], browser_config: BrowserConfig) -> Dict[str, Optional[str]]:
    """
    Phase 1: fetch HTML for each URL, guess a main content selector, return mapping url->selector (or None).
    """
    cfg = build_base_config(css_selector=None, delay_ms=DISCOVERY_DELAY_MS)
    dispatcher = MemoryAdaptiveDispatcher(
        memory_threshold_percent=75.0,
        check_interval=1.0,
        max_session_permit=MAX_CONCURRENT
    )

    selectors: Dict[str, Optional[str]] = {}

    async with AsyncWebCrawler(config=browser_config) as crawler:
        results = await crawler.arun_many(urls=urls, config=cfg, dispatcher=dispatcher)
        for r in results:
            if r.success:
                html = getattr(r, "cleaned_html", None) or getattr(r, "raw_html", None) or ""
                sel = guess_main_selector_from_html(html)
                selectors[r.url] = sel
                print(f"[DISCOVERY] {r.url} -> selector: {sel or '(none)'}")
            else:
                selectors[r.url] = None
                print(f"[DISCOVERY ERROR] {r.url} - {r.error_message}")
    return selectors

async def extract_page(
    crawler: AsyncWebCrawler,
    url: str,
    css_selector: Optional[str],
    fail_log: str,
    output_filename: str,
) -> Tuple[str, str]:  # NEW return
    """
    Phase 2 per-URL extraction with retry and 2s wait before crawl.
    Uses the CLEAN filtering profile from your example.
    Returns (url, filename_or_'crawl_failure').
    """
    cfg = build_extraction_config(css_selector=css_selector, delay_ms=EXTRACTION_DELAY_MS)
    fallback_cfg = build_extraction_config(css_selector=None, delay_ms=EXTRACTION_DELAY_MS)

    async def do_crawl(retries: int = 1):
        try:
            await asyncio.sleep(2)  # extra guard before crawl
            return await crawler.arun(url=url, config=cfg)
        except Exception as e:
            if retries > 0:
                print(f"[RETRY] {url} due to error: {e}")
                await asyncio.sleep(2)
                return await do_crawl(retries - 1)
            raise

    try:
        result = await do_crawl(retries=1)
        if result.success:
            markdown_text = result.markdown.raw_markdown
            if css_selector and markdown_looks_incomplete(markdown_text):
                print(f"[FALLBACK] {url} selector '{css_selector}' looked incomplete. Retrying without selector...")
                fallback_result = await crawler.arun(url=url, config=fallback_cfg)
                if fallback_result.success and not markdown_looks_incomplete(fallback_result.markdown.raw_markdown):
                    markdown_text = fallback_result.markdown.raw_markdown
                    print(f"[FALLBACK] {url} improved content captured without selector.")

            save_markdown(OUTPUT_DIR, output_filename, markdown_text)
            print(f"[✓] Extracted: {url}")
            return url, output_filename  # NEW
        else:
            print(f"[✗] Failed: {url} - {result.error_message}")
            append_failure_log(fail_log, url, result.error_message or "unknown")
            return url, "crawl_failure"  # NEW
    except Exception as e:
        print(f"[✗] Exception: {url} - {e}")
        append_failure_log(fail_log, url, str(e))
        return url, "crawl_failure"  # NEW

async def extraction_phase(urls: List[str], selectors: Dict[str, Optional[str]], browser_config: BrowserConfig) -> Dict[str, str]:  # NEW return mapping
    """
    Phase 2: extract markdown with the guessed selectors (clean filter profile).
    Returns mapping: normalized_url -> filename_or_'crawl_failure'
    """
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    results: List[Tuple[str, str]] = []  # NEW

    async with AsyncWebCrawler(config=browser_config) as crawler:
        async def bounded_extract(idx: int, u: str):
            async with sem:
                filename = sequential_filename(idx)
                return await extract_page(crawler, u, selectors.get(u), FAIL_LOG, filename)

        gathered = await asyncio.gather(*(bounded_extract(idx, u) for idx, u in enumerate(urls, start=1)))
        results.extend(gathered)

    return {u: fname for (u, fname) in results}  # NEW

# =========================
# Write selectors next to 'URL' column only
# =========================
def write_augmented_csv_with_selectors(
    input_csv_path: str,
    output_csv_path: str,
    selectors_map: Dict[str, Optional[str]],
    url_column: str,
):
    """
    Reads the original CSV, and writes a new CSV that copies all columns
    and adds a single 'URL_selector' column filled from selectors_map[url].
    """
    with open(input_csv_path, "r", encoding="utf-8-sig", newline="") as f_in:
        reader = csv.DictReader(f_in)
        if not reader.fieldnames or url_column not in reader.fieldnames:
            raise ValueError(f"Input CSV must contain '{url_column}' column.")

        fieldnames = list(reader.fieldnames)
        if "URL_selector" not in fieldnames:
            fieldnames.append("URL_selector")

        out_dir = os.path.dirname(output_csv_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output_csv_path, "w", encoding="utf-8", newline="") as f_out:
            writer = csv.DictWriter(f_out, fieldnames=fieldnames)
            writer.writeheader()
            for row in reader:
                raw = (row.get(url_column) or "").strip()
                norm = normalize_url(raw)
                row["URL_selector"] = selectors_map.get(norm) or ""
                writer.writerow(row)

    print(f"[WRITE] CSV with URL selectors written to:\n  {output_csv_path}")

# NEW: write story_file_name_links.csv
def write_story_filename_links(
    input_csv_path: str,
    output_csv_path: str,
    url_to_file: Dict[str, str],
    url_column: str,
):
    """
    Reads input CSV and writes all original columns plus md_file (or 'crawl_failure')
    using normalized URL lookup in url_to_file.
    """
    rows_out: List[Dict[str, str]] = []
    output_fieldnames: List[str] = []
    with open(input_csv_path, "r", encoding="utf-8-sig", newline="") as f_in:
        reader = csv.DictReader(f_in)
        if not reader.fieldnames or url_column not in reader.fieldnames:
            raise ValueError(f"Input CSV must contain '{url_column}' column.")
        output_fieldnames = list(reader.fieldnames)
        if "md_file" not in output_fieldnames:
            output_fieldnames.append("md_file")
        for row in reader:
            url_raw = (row.get(url_column) or "").strip()
            norm = normalize_url(url_raw)
            md_file = url_to_file.get(norm, "crawl_failure") if norm else "crawl_failure"
            row["md_file"] = md_file
            rows_out.append(row)

    # Write once with headers (overwrite to ensure a clean file)
    out_dir = os.path.dirname(output_csv_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_csv_path, "w", encoding="utf-8", newline="") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=output_fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"[WRITE] Story filename links written to:\n  {output_csv_path}")

# =========================
# Entry point
# =========================
async def main():
    urls, invalids, norm_map, url_column = read_urls_from_csv(CSV_PATH)
    total_rows = sum(1 for _ in open(CSV_PATH, "r", encoding="utf-8-sig"))
    print(f"Input rows (including header/blank): {total_rows}")
    print(f"Parsed URLs (not deduped): {len(urls)}")
    print(f"Invalid/unnormalized URL-like values: {len(invalids)}")

    write_csv(INVALID_LOG, ["raw_value", "reason"], invalids)
    write_csv(NORMALIZED_MAP_LOG, ["original", "normalized"], norm_map)

    if not urls:
        # Still emit the mapping CSV marking all as crawl_failure if there were rows
        try:
            write_story_filename_links(CSV_PATH, STORY_FILENAME_LINKS, {}, url_column)  # everything becomes crawl_failure
        except Exception:
            pass
        print("No URLs found in CSV.")
        return

    browser_config = BrowserConfig(
        headless=True,
        extra_args=["--disable-gpu", "--disable-dev-shm-usage", "--no-sandbox"],
    )

    print("\n=== Phase 1: Discovery (guess main selectors) ===")
    selectors = await discovery_phase(urls, browser_config)

    # Save selector guesses for audit (url -> selector)
    selector_rows = [(u, selectors.get(u) or "") for u in urls]
    write_csv(SELECTOR_LOG, ["url", "guessed_selector"], selector_rows)

    # Write selectors back by adding 'URL_selector' next to 'URL'
    write_augmented_csv_with_selectors(CSV_PATH, CSV_WITH_SELECTORS_PATH, selectors, url_column)

    print("\n=== Phase 2: Extraction (use guessed selectors, clean filters) ===")
    url_to_file = await extraction_phase(urls, selectors, browser_config)  # NEW

    # NEW: emit story_file_name_links.csv using Date, Title, URL + md filename
    write_story_filename_links(CSV_PATH, STORY_FILENAME_LINKS, url_to_file, url_column)  # NEW

if __name__ == "__main__":
    asyncio.run(main())

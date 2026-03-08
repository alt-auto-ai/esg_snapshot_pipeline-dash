import asyncio
import pandas as pd
import os
from datetime import datetime
from urllib.parse import urlparse, urljoin
import re
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig, CacheMode, DisplayMode
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from crawl4ai.async_dispatcher import MemoryAdaptiveDispatcher, CrawlerMonitor

def fix_url(url):
    """Fix URL format if needed"""
    if not url:
        return None
    
    # Remove any whitespace
    url = url.strip()
    
    # If no scheme specified, add https://
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    try:
        parsed = urlparse(url)
        if parsed.netloc:
            return url
        return None
    except Exception:
        return None

def sanitize_url_for_filename(url):
    """Convert URL to filesystem-safe slug, preserving domain and path hints."""
    fixed_url = fix_url(url)
    if not fixed_url:
        return "invalid-url"

    parsed = urlparse(fixed_url)
    base = f"{parsed.netloc}{parsed.path}".strip("/")
    if not base:
        base = parsed.netloc

    base = base.replace("/", "-")
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base)
    base = re.sub(r"-+", "-", base).strip("-._")
    return base or "url"

async def process_url(url, index, crawler, config, fallback_config, total_urls, failures, max_retries=3):
    # Fix URL format first
    fixed_url = fix_url(url)
    if not fixed_url:
        error_msg = f"[{index}/{total_urls}] Invalid URL format: {url}"
        print(error_msg)
        failures.append({
            'index': index,
            'url': url,
            'error': "Invalid URL format",
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
        return

    # Implement retry mechanism with exponential backoff
    for attempt in range(max_retries):
        try:
            run_config = config if attempt == 0 else fallback_config
            result = await crawler.arun(url=fixed_url, config=run_config)
            if result.success:
                # Create output directory if it doesn't exist
                os.makedirs('source_md_files', exist_ok=True)
                
                # Save markdown to file
                url_slug = sanitize_url_for_filename(fixed_url)
                output_file = f'source_md_files//{index}_{url_slug}.md'
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(result.markdown.raw_markdown)
                print(f"[{index}/{total_urls}] Successfully processed: {fixed_url}")
                return
            else:
                # For non-retryable errors, break immediately
                if "Invalid URL" in result.error_message:
                    break
                
                if attempt < max_retries - 1:
                    # Calculate wait time with caps: 1s for first retry, 5s for second, 10s for third
                    wait_time = min(2 ** attempt, 5 if attempt == 1 else 10 if attempt == 2 else 1)
                    print(f"[{index}/{total_urls}] Attempt {attempt + 1}/{max_retries} failed. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    error_msg = f"[{index}/{total_urls}] Error processing: {fixed_url} - {result.error_message}"
                    print(error_msg)
                    failures.append({
                        'index': index,
                        'url': fixed_url,
                        'error': f"After {max_retries} attempts: {result.error_message}",
                        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    })
        
        except Exception as e:
            if attempt < max_retries - 1:
                # Use same capped wait times for consistency
                wait_time = min(2 ** attempt, 5 if attempt == 1 else 10 if attempt == 2 else 1)
                print(f"[{index}/{total_urls}] Unexpected error. Retrying in {wait_time}s... Error: {str(e)}")
                await asyncio.sleep(wait_time)
            else:
                error_msg = f"[{index}/{total_urls}] Fatal error processing: {fixed_url} - {str(e)}"
                print(error_msg)
                failures.append({
                    'index': index,
                    'url': fixed_url,
                    'error': f"Unexpected error after {max_retries} attempts: {str(e)}",
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                })

async def main():
    # 1. Configure browser for lightweight operation
    browser_config = BrowserConfig(
        headless=True,
        text_mode=True,     # Disable images for speed
        light_mode=True,    # Reduce background features
        extra_args=["--disable-extensions"]  # Minimize browser overhead
    )

    # 2. Configure lighter content filtering
    prune_filter = PruningContentFilter(
        threshold=0.45,
        threshold_type="dynamic",
        min_word_threshold=5
    )
    md_generator = DefaultMarkdownGenerator(content_filter=prune_filter)

    # 3. Fast config: prioritize throughput for first attempt
    config = CrawlerRunConfig(
        markdown_generator=md_generator,
        exclude_external_links=True,
        excluded_tags=['nav', 'footer', 'header', 'aside', 'form', 'script', 'style'],  # Additional tags for speed
        
        # Performance optimizations
        wait_until="domcontentloaded",    # Faster first-pass crawl
        page_timeout=30000,               # 30 second timeout
        cache_mode=CacheMode.BYPASS,      # Always fetch fresh page content
        delay_before_return_html=0.6,     # Small settle delay for dynamic list rendering
        
        # Minimal content processmemory-adaptive-dispatchermemory-adaptive-dispatchering
        exclude_all_images=True,          # Skip image processing
        process_iframes=False,            # Skip iframe processing
        capture_network_requests=False,    # Disable network capture
        capture_console_messages=False,    # Disable console capture
        
        # Light dynamic content handling
        scan_full_page=True,
        scroll_delay=0.2                  # Reduced scroll delay
    )

    # 3.1 Fallback config: used only on retries to improve completeness/reliability
    fallback_config = CrawlerRunConfig(
        markdown_generator=md_generator,
        exclude_external_links=True,
        excluded_tags=['nav', 'footer', 'header', 'aside', 'form', 'script', 'style'],
        wait_until="networkidle",
        page_timeout=45000,
        cache_mode=CacheMode.BYPASS,
        delay_before_return_html=1.2,
        exclude_all_images=True,
        process_iframes=False,
        capture_network_requests=False,
        capture_console_messages=False,
        scan_full_page=True,
        scroll_delay=0.2
    )

    # 4. Configure memory-adaptive dispatcher
    dispatcher = MemoryAdaptiveDispatcher(
        memory_threshold_percent=80.0,     # Throttle at 80% memory usage
        max_session_permit=20,            # Max concurrent sessions
        check_interval=0.5,               # Check every 0.5 seconds
        memory_wait_timeout=300.0         # Wait up to 5 minutes for memory
    )

    # Read URLs from CSV
    df = pd.read_csv('0_source_link.csv')
    urls = df.iloc[:, 0].tolist()  # Get first column
    total_urls = len(urls)
    failures = []  # List to track failures
    
    print(f"\nStarting processing of {total_urls} URLs...")
    print("=" * 50)

    # Create crawler with optimized browser config
    async with AsyncWebCrawler(config=browser_config) as crawler:
        # Process URLs in small parallel batches to improve throughput while keeping stability
        batch_size = max(1, int(os.getenv("CRAWL_BATCH_SIZE", "5")))
        for batch_start in range(0, total_urls, batch_size):
            batch_end = min(batch_start + batch_size, total_urls)
            tasks = []

            for idx in range(batch_start, batch_end):
                row_index = idx + 1
                url = urls[idx]
                tasks.append(
                    process_url(url, row_index, crawler, config, fallback_config, total_urls, failures)
                )

            await asyncio.gather(*tasks)
    
    # Write failures to markdown file if any occurred
    if failures:
        failure_file = '1_source_crawl_failures.md'
        with open(failure_file, 'w', encoding='utf-8') as f:
            f.write("# Crawl Failures Log\n\n")
            f.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"Total URLs processed: {total_urls}\n")
            f.write(f"Total failures: {len(failures)}\n\n")
            f.write("## Detailed Failure Logs\n\n")
            
            for failure in failures:
                f.write(f"### URL #{failure['index']}\n")
                f.write(f"- **URL:** {failure['url']}\n")
                f.write(f"- **Timestamp:** {failure['timestamp']}\n")
                f.write(f"- **Error:** {failure['error']}\n\n")
        
        print(f"\nFailure log saved to: {failure_file}")
    
    print("=" * 50)
    print(f"Completed processing {total_urls} URLs")
    print(f"Successful results saved in: source_md_files")
    print(f"Failed URLs: {len(failures)} out of {total_urls}")

if __name__ == "__main__":
    asyncio.run(main())
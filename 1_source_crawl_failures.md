# Crawl Failures Log

Generated on: 2026-05-29 16:48:11

Total URLs processed: 214
Total failures: 1

## Detailed Failure Logs

### URL #207
- **URL:** https://esgnews.com/category/esg/
- **Timestamp:** 2026-05-29 16:48:03
- **Error:** Unexpected error in _crawl_web at line 718 in _crawl_web (../../../miniconda3/envs/esg/lib/python3.11/site-packages/crawl4ai/async_crawler_strategy.py):
Error: Failed on navigating ACS-GOTO:
Page.goto: Timeout 30000ms exceeded.
Call log:
  - navigating to "https://esgnews.com/category/esg/", waiting until "domcontentloaded"


Code context:
 713                                   tag="GOTO",
 714                                   params={"url": url},
 715                               )
 716                               response = None
 717                           else:
 718 →                             raise RuntimeError(f"Failed on navigating ACS-GOTO:\n{str(e)}")
 719   
 720                       # ──────────────────────────────────────────────────────────────
 721                       # Walk the redirect chain.  Playwright returns only the last
 722                       # hop, so we trace the `request.redirected_from` links until the
 723                       # first response that differs from the final one and surface its


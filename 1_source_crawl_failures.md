# Crawl Failures Log

Generated on: 2026-02-27 05:55:05

Total URLs processed: 208
Total failures: 1

## Detailed Failure Logs

### URL #79
- **URL:** https://www.epa.wa.gov.au/media-statements
- **Timestamp:** 2026-02-27 05:50:54
- **Error:** After 3 attempts: Unexpected error in _crawl_web at line 696 in _crawl_web (..\..\..\..\miniconda3\envs\esg\Lib\site-packages\crawl4ai\async_crawler_strategy.py):
Error: Failed on navigating ACS-GOTO:
Page.goto: Timeout 45000ms exceeded.
Call log:
  - navigating to "https://www.epa.wa.gov.au/media-statements", waiting until "networkidle"


Code context:
 691                               tag="GOTO",
 692                               params={"url": url},
 693                           )
 694                           response = None
 695                       else:
 696 →                         raise RuntimeError(f"Failed on navigating ACS-GOTO:\n{str(e)}")
 697   
 698                   await self.execute_hook(
 699                       "after_goto", page, context=context, url=url, response=response, config=config
 700                   )
 701   


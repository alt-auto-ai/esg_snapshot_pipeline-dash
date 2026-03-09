#!/usr/bin/env bash
set -e

TOTAL=26
STEP=0
PIPELINE_START=$(date +%s)

run_step() {
    STEP=$((STEP + 1))
    local cmd="$*"
    local pct=$(( (STEP - 1) * 100 / TOTAL ))
    local bar_len=30
    local filled=$(( pct * bar_len / 100 ))
    local empty=$(( bar_len - filled ))
    local bar=$(printf '%0.s█' $(seq 1 $filled 2>/dev/null))$(printf '%0.s░' $(seq 1 $empty 2>/dev/null))

    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    printf "║  ▶ STEP %2d / %d  %-44s ║\n" "$STEP" "$TOTAL" ""
    printf "║  %-60s ║\n" "$cmd"
    printf "║  [%s] %3d%%%-24s ║\n" "$bar" "$pct" ""
    printf "║  Started: %-50s ║\n" "$(date '+%Y-%m-%d %H:%M:%S')"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""

    local step_start=$(date +%s)
    eval "$cmd"
    local step_end=$(date +%s)
    local elapsed=$(( step_end - step_start ))
    local mins=$(( elapsed / 60 ))
    local secs=$(( elapsed % 60 ))

    printf "  ✔ Step %d/%d completed in %dm %ds\n" "$STEP" "$TOTAL" "$mins" "$secs"
}

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         ESG SNAPSHOT — AUTOMATED PIPELINE                   ║"
printf "║         Started: %-42s ║\n" "$(date '+%Y-%m-%d %H:%M:%S')"
printf "║         Total steps: %-39s ║\n" "$TOTAL"
echo "╚══════════════════════════════════════════════════════════════╝"

run_step python 0_delete_all_md_files.py
run_step python 1_crawl_source_urls.py
run_step python 2_extract_story_links_with_ai.py
run_step python 2.1_delete_story_files.py
run_step python 3_crawl_story_links_with_main_selector.py
run_step python 3.1_story_md_cleaning.py
run_step python 4_story_filter_esg_or_not.py
run_step python 4.1_story_type.py
run_step python 5_story_filter_jurisdiction.py
run_step python 7_esg_relevance.py
run_step python 7.1_esg_relevance_score.py
run_step python 8_esg_draft_multi_prompt.py
run_step python 8.1_esg_highlights.py
run_step python 9_delete_event_mds.py
run_step python 9_delete_event_page_mds.py
run_step python 9.1_crawl_event_source_urls.py
run_step python 9.2_extract_events_links_with_ai.py
run_step python 9.3_crawl_events_with_main_selector.py
run_step python 9.4_events_description.py
run_step python 9.4.1_delete_job_mds.py
run_step python '"9.4.2_delete job_pages_mds.py"'
run_step python 10_crawl_job_source_urls.py
run_step python 10.1_extracting_jobs_with_ai.py
run_step python 10.2_crawl_job_with_main_selector.py
run_step python 11_job_descriptions.py
run_step node Dashboard/build.mjs

PIPELINE_END=$(date +%s)
TOTAL_ELAPSED=$(( PIPELINE_END - PIPELINE_START ))
TOTAL_MINS=$(( TOTAL_ELAPSED / 60 ))
TOTAL_SECS=$(( TOTAL_ELAPSED % 60 ))

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         ✔ PIPELINE COMPLETE                                 ║"
printf "║         Finished: %-41s ║\n" "$(date '+%Y-%m-%d %H:%M:%S')"
printf "║         Total time: %dm %ds%-35s ║\n" "$TOTAL_MINS" "$TOTAL_SECS" ""
echo "║         [██████████████████████████████] 100%%               ║"
echo "╚══════════════════════════════════════════════════════════════╝"

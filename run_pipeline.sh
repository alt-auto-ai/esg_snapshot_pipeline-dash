#!/usr/bin/env bash
set -e

TOTAL=26
STEP=0
PIPELINE_START=$(date +%s)

# Validation: check output file was written/modified by this script execution
validate_output() {
    local script="$1"
    local expected="$2"
    local start_time="$3"
    if [[ -z "$expected" ]]; then
        return 0  # No validation needed for cleanup scripts
    fi
    if [[ ! -f "$expected" ]]; then
        echo ""
        echo "╔══════════════════════════════════════════════════════════════╗"
        echo "║  ✘ VALIDATION FAILED                                        ║"
        printf "║  Script: %-51s ║\n" "$script"
        printf "║  Missing output: %-43s ║\n" "$expected"
        echo "║  Pipeline stopped.                                          ║"
        echo "╚══════════════════════════════════════════════════════════════╝"
        exit 1
    fi
    # Check if file was modified after step started (actually written by this execution)
    local file_mtime=$(stat -c %Y "$expected" 2>/dev/null || stat -f %m "$expected" 2>/dev/null)
    if [[ "$file_mtime" -lt "$start_time" ]]; then
        echo ""
        echo "╔══════════════════════════════════════════════════════════════╗"
        echo "║  ✘ VALIDATION FAILED                                        ║"
        printf "║  Script: %-51s ║\n" "$script"
        printf "║  Output not updated: %-39s ║\n" "$expected"
        echo "║  File exists but was not modified by this execution.        ║"
        echo "║  Pipeline stopped.                                          ║"
        echo "╚══════════════════════════════════════════════════════════════╝"
        exit 1
    fi
    if [[ ! -s "$expected" ]]; then
        echo ""
        echo "╔══════════════════════════════════════════════════════════════╗"
        echo "║  ✘ VALIDATION FAILED                                        ║"
        printf "║  Script: %-51s ║\n" "$script"
        printf "║  Empty output: %-45s ║\n" "$expected"
        echo "║  Pipeline stopped.                                          ║"
        echo "╚══════════════════════════════════════════════════════════════╝"
        exit 1
    fi
}

run_step() {
    STEP=$((STEP + 1))
    local cmd="$1"
    local expected_output="$2"
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
    
    # Validate expected output was written by this execution
    validate_output "$cmd" "$expected_output" "$step_start"
}

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         ESG SNAPSHOT — AUTOMATED PIPELINE                   ║"
printf "║         Started: %-42s ║\n" "$(date '+%Y-%m-%d %H:%M:%S')"
printf "║         Total steps: %-39s ║\n" "$TOTAL"
echo "╚══════════════════════════════════════════════════════════════╝"

run_step "python 0_delete_all_md_files.py" ""
run_step "python 1_crawl_source_urls.py" ""
run_step "python 2_extract_story_links_with_ai.py" "2_story_links.csv"
run_step "python 2.1_delete_story_files.py" ""
run_step "python 3_crawl_story_links_with_main_selector.py" "3_story_links_with_selectors.csv"
run_step "python 3.1_story_md_cleaning.py" ""
run_step "python 4_story_filter_esg_or_not.py" "4_story_esg_or_not.csv"
run_step "python 4.1_story_type.py" "4.1_story_type.csv"
run_step "python 5_story_filter_jurisdiction.py" "5_story_jurisdiction.csv"
run_step "python 7_esg_relevance.py" "7_esg_relevance.csv"
run_step "python 7.1_esg_relevance_score.py" "7_esg_relevance.csv"
run_step "python 8_esg_draft_multi_prompt.py" "8_esg_draft_multi.csv"
run_step "python 8.1_esg_highlights.py" "8.1_esg_highlights_multi.csv"
run_step "python 9_delete_event_mds.py" ""
run_step "python 9_delete_event_page_mds.py" ""
run_step "python 9.1_crawl_event_source_urls.py" ""
run_step "python 9.2_extract_events_links_with_ai.py" "9.2_events_data.csv"
run_step "python 9.3_crawl_events_with_main_selector.py" ""
run_step "python 9.4_events_description.py" "9.4_events_all_data.csv"
run_step "python 9.4.1_delete_job_mds.py" ""
run_step "python '9.4.2_delete job_pages_mds.py'" ""
run_step "python 10_crawl_job_source_urls.py" ""
run_step "python 10.1_extracting_jobs_with_ai.py" "10.1_job_data.csv"
run_step "python 10.2_crawl_job_with_main_selector.py" "10.2_job_links.csv"
run_step "python 11_job_descriptions.py" "11_job_all_data.csv"
run_step "node Dashboard/build.mjs" "Dashboard/index.html"

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

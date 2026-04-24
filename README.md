# ESG Snapshot Automated

Minimal pipeline to crawl web sources, extract and categorize ESG-related stories/events/jobs, and generate a customizable HTML dashboard.

## Quick Start

```bash
pip install -r requirements.txt
crawl4ai-setup
crawl4ai-doctor
node Dashboard/build.mjs
```

## What this project does
- Crawls source websites
- Extracts and filters ESG-relevant content
- Classifies story type/jurisdiction/relevance
- Produces structured CSV outputs
- Builds dashboard at `Dashboard/index.html`

## Local setup (Python)
Run from project root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Crawl4AI official install + verification
After installing requirements, run Crawl4AI setup/check commands:

```bash
crawl4ai-setup
crawl4ai-doctor
```

If browser setup is missing/fails, run:

```bash
python -m playwright install --with-deps chromium
```

(Official Crawl4AI docs also show `pip install -U crawl4ai`, `crawl4ai-setup`, `crawl4ai-doctor`.)

## Run pipeline scripts
You can run scripts individually in order from:
- `0_delete_all_md_files.py`
- through `11_job_descriptions.py`

Then build dashboard:

```bash
node Dashboard/build.mjs
```

See full step-by-step commands in `COMMAND.txt`.

## Docker
Build image:

```bash
docker build -t esg-snapshot-pipeline:latest .
```

Run ephemeral container (recommended, works from any current directory and always mounts project root):

```bash
bash run_docker_pipeline.sh
```

Or run manually from project root only:

```bash
docker run --rm --env-file .env --network host -v "$(pwd)":/app esg-snapshot-pipeline
```

Important:
- Inputs and outputs persist to your local project because the host project folder is bind-mounted to `/app`.
- If you run the manual command from a subfolder (for example `Dashboard`), only that subfolder is mounted and pipeline files outside it will not be visible in the container.

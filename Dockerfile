# ── Base image: Python 3.12 on Debian Bookworm ──────────────────────────
FROM python:3.12-bookworm

# ── System dependencies for crawl4ai (Playwright/Chromium) + Node.js ────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    # Playwright / Chromium runtime deps
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
    libx11-xcb1 libxfixes3 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# ── Install Node.js 20 LTS (for Dashboard/build.mjs) ───────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ─────────────────────────────────────────────────
RUN pip install --no-cache-dir \
    pandas \
    crawl4ai \
    beautifulsoup4 \
    scikit-learn \
    aiohttp \
    pyyaml \
    python-dotenv \
    aiolimiter \
    chardet \
    python-dateutil

# ── crawl4ai setup: install Playwright browsers + OS-level deps ─────────
RUN crawl4ai-setup && \
    python -m playwright install --with-deps chromium && \
    crawl4ai-doctor

# ── Set working directory ───────────────────────────────────────────────
WORKDIR /app

# ── Copy entire codebase ────────────────────────────────────────────────
COPY . .

# ── Make pipeline runner executable ─────────────────────────────────────
RUN chmod +x /app/run_pipeline.sh

CMD ["/app/run_pipeline.sh"]

# ESG Story Picker Dashboard

Interactive HTML dashboard for reviewing, filtering, and exporting ESG stories.

## Prerequisites

- [Node.js](https://nodejs.org/) (v18+)

## Usage

1. Open a terminal in this folder:

   ```
   cd Dashboard
   ```

2. Build the dashboard:

   ```
   node build.mjs
   ```

   This reads the CSV from `Quality_Check/8.1_esg_highlights_multi.csv`, filters ESG-relevant stories, and generates a self-contained `index.html`.

3. Open `index.html` in any modern browser (Chrome, Edge, Firefox).

## Features

- Stories grouped by **Jurisdiction**, then ordered by **Story Type** and **Relevance** (High → Medium → Low).
- Per-jurisdiction and total story counters update live.
- **Right-click** any story card to remove it.
- **Export to DOCX** button generates a Word document with remaining stories.

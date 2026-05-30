import os
import re
import glob
import shutil
import csv
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# -------- CONFIG --------
FOLDER = r"story_md_files"
OUTPUT_FOLDER = r"source_md_files_cleaned"
CSV_FILE = r"3_story_file_name_links.csv"
THRESHOLD = 0.8  # similarity threshold (80%)

# -------- STEP 1: READ ALL FILES --------
def read_markdown_files(folder):
    files = glob.glob(os.path.join(folder, "*.md"))
    contents = {}
    for f in files:
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as file:
                contents[f] = file.read()
        except Exception as e:
            print(f"[!] Could not read {f}: {e}")
    return contents

# -------- STEP 2: COMPUTE SIMILARITY --------
def find_duplicates(contents, threshold):
    file_paths = list(contents.keys())
    texts = list(contents.values())

    if len(texts) < 2:
        return []

    vectorizer = TfidfVectorizer(stop_words='english')
    try:
        tfidf_matrix = vectorizer.fit_transform(texts)
    except ValueError:
        return []
    similarity_matrix = cosine_similarity(tfidf_matrix)

    to_remove = set()

    for i in range(len(file_paths)):
        for j in range(i + 1, len(file_paths)):
            sim = similarity_matrix[i, j]
            if sim >= threshold:
                print(f"[⚠] {os.path.basename(file_paths[i])} and {os.path.basename(file_paths[j])} "
                      f"are {sim*100:.1f}% similar.")
                # Keep the earlier one, mark later as duplicate
                to_remove.add(file_paths[j])

    return list(to_remove)

# -------- STEP 3: REMOVE DUPLICATES --------
def remove_files(file_list):
    removed = []
    for f in file_list:
        try:
            print(f"[↷] Skipped duplicate from cleaned output (kept in source): {os.path.basename(f)}")
            removed.append(os.path.basename(f))
        except Exception as e:
            print(f"[!] Failed to mark duplicate {f}: {e}")
    return removed

# -------- STEP 4: STRIP URLS AND NAV NOTATIONS --------
URL_PATTERN = re.compile(r"(https?://[^\s]+|www\.[^\s]+)", re.IGNORECASE)
DROP_LINE_PATTERNS = [
    re.compile(r'^\]\(.*\]\(', re.IGNORECASE),
    re.compile(r'^\[.*toggle navigation menu.*\]\(', re.IGNORECASE),
    re.compile(r'^\[\s*skip to main content.*\]\(', re.IGNORECASE),
    re.compile(r'\[Skip to content\]\(', re.IGNORECASE),
    re.compile(r'^\[.*close submenu.*\]\(', re.IGNORECASE),
    re.compile(r'^\[.*rss feed.*\]\(', re.IGNORECASE),
    re.compile(r'^\[.*login.*\]\(', re.IGNORECASE),
    re.compile(r'\[.*mywto.*\]\(', re.IGNORECASE),
    re.compile(r'^\[\s*!\[.*\]\(', re.IGNORECASE),
    re.compile(r'^\[!\[.*\]\(', re.IGNORECASE),
    re.compile(r'^\s*!\[.*\]\(.*', re.IGNORECASE),
    re.compile(r'^site search.*\[\]?\(', re.IGNORECASE),
    re.compile(r'^search website', re.IGNORECASE),
    re.compile(r'^keyword\s*$', re.IGNORECASE),
    re.compile(r'^news article\.?', re.IGNORECASE),
    re.compile(r'^\d+\.\s*\d{1,2}\s+\w+\s+\d{4}', re.IGNORECASE),
    re.compile(r'^\*\s*membership.*\[\]?\(', re.IGNORECASE),
]

def should_drop_line(stripped_line):
    if stripped_line.startswith("#"):
        return True
    if stripped_line.startswith("* ["):
        return True
    if re.match(r'^\*\s+[A-Za-z]+$', stripped_line):
        return True
    for pattern in DROP_LINE_PATTERNS:
        if pattern.search(stripped_line):
            return True
    return False

def strip_urls_from_text(text):
    """Remove bare URLs plus known navigation, image, and boilerplate notations."""
    cleaned_lines = []
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if should_drop_line(stripped):
            continue
        cleaned_line = URL_PATTERN.sub("", line)
        if not cleaned_line.strip():
            continue
        cleaned_lines.append(cleaned_line)
    return "".join(cleaned_lines)

def remove_urls_from_remaining_files(folder, removed_files, output_folder):
    removed_set = {os.path.normcase(path) for path in removed_files}
    os.makedirs(output_folder, exist_ok=True)
    for path in glob.glob(os.path.join(folder, "*.md")):
        if os.path.normcase(path) in removed_set:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as file:
                content = file.read()
            cleaned = strip_urls_from_text(content)
            out_path = os.path.join(output_folder, os.path.basename(path))
            try:
                with open(out_path, "w", encoding="utf-8") as file:
                    file.write(cleaned)
            except PermissionError:
                if os.path.exists(out_path):
                    os.remove(out_path)
                with open(out_path, "w", encoding="utf-8") as file:
                    file.write(cleaned)
            print(f"[✂] Wrote cleaned markdown: {os.path.basename(out_path)}")
        except Exception as e:
            print(f"[!] Failed to clean URLs in {path}: {e}")

def remove_blank_markdown_files(folder):
    removed = []
    for path in glob.glob(os.path.join(folder, "*.md")):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as file:
                is_blank = file.read().strip() == ""
            if is_blank:
                os.remove(path)
                removed.append(os.path.basename(path))
                print(f"[🗑] Removed blank file: {os.path.basename(path)}")
        except Exception as e:
            print(f"[!] Failed to check/remove blank file {path}: {e}")
    print(f"[INFO] Blank .md files removed from {folder}: {len(removed)}")
    return removed

def update_story_file_links(csv_path, deleted_md_files):
    if not deleted_md_files:
        print("[INFO] No deleted files to update in CSV.")
        return

    deleted_set = set(deleted_md_files)
    updated_count = 0
    rows = []

    with open(csv_path, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames
        for row in reader:
            if row.get("md_file") in deleted_set:
                row["md_file"] = "CLEANED"
                updated_count += 1
            rows.append(row)

    with open(csv_path, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[INFO] Updated {updated_count} row(s) in {os.path.basename(csv_path)} with CLEANED.")

def prepend_story_url_to_cleaned_files(csv_path, output_folder):
    md_to_url = {}
    with open(csv_path, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            md_file = (row.get("md_file") or "").strip()
            url = (row.get("URL") or "").strip()
            if md_file.endswith(".md") and url:
                md_to_url[md_file] = url

    updated = 0
    for path in glob.glob(os.path.join(output_folder, "*.md")):
        md_name = os.path.basename(path)
        url = md_to_url.get(md_name)
        if not url:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as file:
                content = file.read()
            prefix = f"Story's url: {url}\n\n"
            if content.startswith("Story's url:"):
                body = content.split("\n\n", 1)[1] if "\n\n" in content else ""
                new_content = prefix + body
            else:
                new_content = prefix + content
            with open(path, "w", encoding="utf-8") as file:
                file.write(new_content)
            updated += 1
        except Exception as e:
            print(f"[!] Failed to prepend URL in {md_name}: {e}")
    print(f"[INFO] Prepended story URL in {updated} cleaned markdown file(s).")

# -------- MAIN --------
if __name__ == "__main__":
    contents = read_markdown_files(FOLDER)
    print(f"Found {len(contents)} markdown files.")
    duplicates = find_duplicates(contents, THRESHOLD)
    print(f"\nTotal duplicates to remove: {len(duplicates)}")
    deleted_duplicates = remove_files(duplicates)
    remove_urls_from_remaining_files(FOLDER, duplicates, OUTPUT_FOLDER)
    deleted_blank_cleaned = remove_blank_markdown_files(OUTPUT_FOLDER)
    all_deleted = deleted_duplicates + deleted_blank_cleaned
    if all_deleted:
        print(f"[INFO] Deleted files: {', '.join(all_deleted)}")
    else:
        print("[INFO] No files were deleted.")
    update_story_file_links(CSV_FILE, all_deleted)
    prepend_story_url_to_cleaned_files(CSV_FILE, OUTPUT_FOLDER)
    print("\n✅ Duplicate removal complete; remaining files cleaned of URLs and specified markdown notations.")

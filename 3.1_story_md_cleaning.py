import os
import re
import glob
import shutil
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# -------- CONFIG --------
FOLDER = r"story_md_files"
OUTPUT_FOLDER = r"source_md_files_cleaned"
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
    for f in file_list:
        try:
            os.remove(f)
            print(f"[🗑] Removed: {os.path.basename(f)}")
        except Exception as e:
            print(f"[!] Failed to remove {f}: {e}")

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
            with open(out_path, "w", encoding="utf-8") as file:
                file.write(cleaned)
            print(f"[✂] Wrote cleaned markdown: {os.path.basename(out_path)}")
        except Exception as e:
            print(f"[!] Failed to clean URLs in {path}: {e}")

def remove_blank_markdown_files(folder):
    removed = 0
    for path in glob.glob(os.path.join(folder, "*.md")):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as file:
                if file.read().strip() == "":
                    os.remove(path)
                    removed += 1
                    print(f"[🗑] Removed blank file: {os.path.basename(path)}")
        except Exception as e:
            print(f"[!] Failed to check/remove blank file {path}: {e}")
    print(f"[INFO] Blank .md files removed from {folder}: {removed}")

# -------- MAIN --------
if __name__ == "__main__":
    contents = read_markdown_files(FOLDER)
    print(f"Found {len(contents)} markdown files.")
    duplicates = find_duplicates(contents, THRESHOLD)
    print(f"\nTotal duplicates to remove: {len(duplicates)}")
    remove_files(duplicates)
    remove_urls_from_remaining_files(FOLDER, duplicates, OUTPUT_FOLDER)
    remove_blank_markdown_files(OUTPUT_FOLDER)
    print("\n✅ Duplicate removal complete; remaining files cleaned of URLs and specified markdown notations.")

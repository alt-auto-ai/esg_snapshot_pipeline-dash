import os
from pathlib import Path

# IMPORTANT: This action is irreversible. Ensure these are the correct paths
# before running the script. The 'r' before the string creates a raw string,
# which correctly handles the backslashes in Windows paths.
TARGET_FOLDERS = [
    r"source_md_files"
]

def delete_files_in_folders(folders_to_clean: list[str]):
    """
    Iterates through a list of folders and deletes all files found within them.
    Subdirectories are skipped.
    """
    print("--- File Deletion Utility Started ---")
    total_deleted_count = 0

    for folder_path_str in folders_to_clean:
        folder_path = Path(folder_path_str)
        files_deleted_in_folder = 0

        print(f"\nProcessing folder: {folder_path_str}")

        # Check if the directory exists before proceeding
        if not folder_path.is_dir():
            print(f"  [ERROR] Directory not found or is not a directory. Skipping: {folder_path_str}")
            continue

        try:
            # Iterate over all items (files and directories) in the folder
            for item in folder_path.iterdir():
                
                # We only want to delete files, not subdirectories
                if item.is_file():
                    try:
                        item.unlink()  # Deletes the file
                        print(f"   [DELETED] {item.name}")
                        files_deleted_in_folder += 1
                        total_deleted_count += 1
                    except OSError as e:
                        print(f"   [ERROR] Failed to delete file {item.name}: {e}")
                elif item.is_dir():
                    print(f"   [SKIPPED] Directory (will not delete contents): {item.name}")
            
            print(f"-> Successfully deleted {files_deleted_in_folder} file(s) from {folder_path.name}")

        except Exception as e:
            print(f"An unexpected error occurred while accessing {folder_path_str}: {e}")

    print(f"\n--- Utility Finished. Total files deleted across all folders: {total_deleted_count} ---")


if __name__ == "__main__":
    delete_files_in_folders(TARGET_FOLDERS)

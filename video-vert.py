import json
import subprocess
import re
import os
import tempfile
import shutil
import argparse
from src.utils import *
from src.gemini_functions import *

# --- Configuration ---
# Use dedicated input/output folders
INPUT_DIR = "INPUT_VIDEOS"
OUTPUT_DIR = "OUTPUT_VIDEOS"

# Fixed crop dimensions
CROP_WIDTH = 607
CROP_HEIGHT = 1080
CROP_Y = 0


def main():
    parser = argparse.ArgumentParser(description="Verticalize videos from INPUT_VIDEOS to OUTPUT_VIDEOS using Gemini-generated crop data.")
    parser.add_argument("--all", action="store_true", help="Process all .mp4 files in the INPUT_VIDEOS folder")
    parser.add_argument("--input", "-i", help="Optional single input video name under INPUT_VIDEOS or full path to a file.")
    args = parser.parse_args()

    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Determine mode
    if args.all:
        # Batch mode: process all .mp4 files in INPUT_DIR
        if not os.path.isdir(INPUT_DIR):
            print(f"Error: INPUT directory '{INPUT_DIR}' not found.")
            return 1
        files = [f for f in os.listdir(INPUT_DIR) if f.lower().endswith('.mp4')]
        if not files:
            print(f"No .mp4 files found in {INPUT_DIR}/")
            return 0

        print(f"Found {len(files)} video(s) to process in {INPUT_DIR}/")
        successes = 0
        failures = 0
        for name in sorted(files):
            input_path = os.path.join(INPUT_DIR, name)
            print("\n" + "#" * 60)
            print(f"Processing: {input_path}")
            print("#" * 60)
            if verticalize_one_video(input_path):
                successes += 1
            else:
                failures += 1
        print("\nSummary:")
        print(f"  Success: {successes}")
        print(f"  Failed:  {failures}")
        return 0 if failures == 0 else 2

    # Single-file mode
    if args.input:
        # If a full path is provided, use as-is; otherwise, look under INPUT_DIR
        candidate = args.input
        if not os.path.isabs(candidate):
            candidate = os.path.join(INPUT_DIR, candidate)
        if not os.path.exists(candidate):
            print(f"Error: Input video '{candidate}' not found.")
            return 1
        return 0 if verticalize_one_video(candidate) else 1


# --- Execution ---
if __name__ == "__main__":
    raise SystemExit(main())

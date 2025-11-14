import json
import subprocess
import re
import os
import tempfile
import shutil
import argparse
from src.utils import load_crop_data, get_video_info, timestamp_to_seconds, generate_crop_data_with_gemini, INPUT_DIR, OUTPUT_DIR, CROP_HEIGHT
from src.gemini_functions import *
from src.gcp_transcoder import (
    ensure_bucket,
    upload_to_gcs,
    download_from_gcs,
    create_cropped_segment_job,
    create_concat_job,
)

# --- Configuration ---
# Use dedicated input/output folders
INPUT_DIR = "INPUT_VIDEOS"
OUTPUT_DIR = "OUTPUT_VIDEOS"

# Fixed crop width for 9:16 (height will be the source height)
CROP_WIDTH = 607


def verticalize_one_video(input_video_path: str) -> bool:
    """Verticalize a single video using Google Cloud Transcoder API.

    Steps:
    - Analyze local video
    - Generate crop data JSON with Gemini
    - Upload source to GCS (create bucket if needed)
    - For each crop segment, create a Transcoder job with crop+trim
    - Create a Transcoder concat job over produced segment MP4s
    - Download final MP4 to OUTPUT_DIR
    """
    from dotenv import load_dotenv
    load_dotenv()

    project_id = os.getenv("PROJECT_ID")
    region = os.getenv("GCP_REGION", "us-central1")
    if not project_id:
        print("Error: PROJECT_ID not set in environment. Check your .env file.")
        return False

    # Ensure Transcoder API is enabled before proceeding
    if not ensure_transcoder_enabled(project_id):
        print("Transcoder API could not be enabled automatically. Please enable it and retry.")
        return False

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    base = os.path.splitext(os.path.basename(input_video_path))[0]
    local_output_path = os.path.join(OUTPUT_DIR, f"{base}_vertical_crop_w_framing_pipeline.mp4")
    local_crop_json = os.path.join(OUTPUT_DIR, f"{base}_crop_data_framing_temp.json")

    # Analyze input
    info = get_video_info(input_video_path)
    if not info:
        print("Could not analyze input; continuing with defaults...")
        video_width, video_height = (CROP_WIDTH, CROP_HEIGHT)
    else:
        video_width, video_height = info["width"], info["height"]
        print(f"Input: {video_width}x{video_height}")

    # Generate crop data
    print("Generating crop data with Gemini...")
    if not generate_crop_data_with_gemini(input_video_path, local_crop_json):
        print("Failed to generate crop data.")
        return False
    crop_data = load_crop_data(local_crop_json)
    if not crop_data:
        print("No crop data found.")
        return False

    # Build segments (start, end, x1)
    segments = []
    for i, item in enumerate(crop_data):
        start = timestamp_to_seconds(item["timestamp"])
        end = timestamp_to_seconds(crop_data[i+1]["timestamp"]) if i < len(crop_data)-1 else None
        segments.append({
            "start": start,
            "end": end,
            "x1": int(item["x1"]),
            "reason": item.get("reason", ""),
        })

    # Prepare GCS
    bucket_name = f"{project_id}-video-vert"
    bucket = ensure_bucket(project_id, bucket_name, region)

    # Upload source
    src_blob = f"uploads/{base}.mp4"
    input_gcs_uri = upload_to_gcs(input_video_path, bucket.name, src_blob)
    print(f"Uploaded source to: {input_gcs_uri}")

    # Create segment jobs
    segment_output_uris = []
    for idx, seg in enumerate(segments):
        start = seg["start"]
        duration = (seg["end"] - seg["start"]) if seg["end"] is not None else None
        x1 = seg["x1"]
        out_prefix = f"gs://{bucket.name}/segments/{base}/{idx:03d}/"
        print(f"Creating segment {idx+1}/{len(segments)} | x1={x1} | start={start:.3f}s | duration={duration:.3f}s" if duration else f"Creating segment {idx+1}/{len(segments)} | x1={x1} | start={start:.3f}s | to end")
        seg_uri = create_cropped_segment_job(
            project_id=project_id,
            location=region,
            input_gcs_uri=input_gcs_uri,
            output_gcs_prefix=out_prefix,
            video_width=video_width,
            video_height=video_height,
            crop_width=int(video_height * 9/16),  # ensure 9:16
            x1=x1,
            start_seconds=start,
            duration_seconds=duration,
        )
        segment_output_uris.append(seg_uri)

    # Concat job
    final_prefix = f"gs://{bucket.name}/outputs/{base}/"
    print("Creating concat job for final output...")
    final_gcs_uri = create_concat_job(
        project_id=project_id,
        location=region,
        segment_uris=segment_output_uris,
        output_gcs_prefix=final_prefix,
        width_pixels=int(video_height * 9/16),
        height_pixels=video_height,
    )
    print(f"Final output in GCS: {final_gcs_uri}")

    # Download to local OUTPUT_DIR
    print("Downloading final output locally...")
    download_from_gcs(final_gcs_uri, local_output_path)
    print(f"Saved: {local_output_path}")
    return True


def ensure_transcoder_enabled(project_id: str) -> bool:
    """Check if Transcoder API is enabled; if not, attempt to enable via gcloud.

    Returns True if enabled or successfully enabled; False otherwise.
    """
    # If gcloud isn't available, just skip and instruct user later
    gcloud_path = shutil.which("gcloud")
    if not gcloud_path:
        print("Warning: gcloud not found on PATH. Skipping automatic enable step.")
        return False

    def _run(cmd: list[str]) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        except Exception as e:
            return 1, "", str(e)

    # Check if enabled
    code, out, err = _run([
        gcloud_path, "services", "list", "--enabled",
        "--project", project_id,
        "--filter=transcoder.googleapis.com",
        "--format=value(config.name)",
    ])
    if code == 0 and out.strip() == "transcoder.googleapis.com":
        return True

    print("Transcoder API not enabled. Attempting to enable...")
    code, out, err = _run([
        gcloud_path, "services", "enable", "transcoder.googleapis.com",
        "--project", project_id,
    ])
    if code != 0:
        print("Failed to enable Transcoder API via gcloud.")
        if err:
            print(err)
        return False

    # Verify post-enable
    code, out, err = _run([
        gcloud_path, "services", "list", "--enabled",
        "--project", project_id,
        "--filter=transcoder.googleapis.com",
        "--format=value(config.name)",
    ])
    if code == 0 and out.strip() == "transcoder.googleapis.com":
        print("Transcoder API enabled.")
        return True

    print("Transcoder API still appears disabled after attempt.")
    return False


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
        # if not os.path.isabs(candidate):
        #     try:
        #         candidate = os.path.join(INPUT_DIR, candidate)
        #     except Exception as e:
        #         candidate = candidate
        if not os.path.exists(candidate):
            print(f"Error: Input video '{candidate}' not found.")
            return 1
        return 0 if verticalize_one_video(candidate) else 1

    print("No input specified. Use --input <file> or --all.")
    return 1


# --- Execution ---
if __name__ == "__main__":
    raise SystemExit(main())

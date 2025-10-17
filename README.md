# Fox Verticalization

Convert 16:9 source videos into 9:16 (vertical) crops suitable for Shorts/Reels/TikTok using AI-assisted framing. The pipeline analyzes the video with Google Vertex AI (Gemini) to generate smart crop coordinates, then renders a final verticalized video with FFmpeg.

## Features
- AI-driven crop data generation via Vertex AI (Gemini)
- Segment-based cropping with smooth transitions
- Batch processing: run against all videos in `INPUT_VIDEOS/`
- Deterministic output paths under `OUTPUT_VIDEOS/`

## Repo layout
- `video-vert.py` — CLI entry point for verticalization (single or batch)
- `src/utils.py` — FFmpeg/processing helpers (segment creation, concat, path setup)
- `src/gemini_functions.py` — Gemini prompt and response handling for crop JSON
- `INPUT_VIDEOS/` — Place your input `.mp4` files here
- `OUTPUT_VIDEOS/` — Generated crop JSON and final verticalized `.mp4`
- `requirements.txt` — Python dependencies

## Prerequisites
- macOS or Linux (Windows WSL works as well)
- Python 3.10+ (tested with Python 3.13)
- FFmpeg installed and on PATH
- Google Cloud account and project

### Install FFmpeg
- macOS (Homebrew): `brew install ffmpeg`
- Verify: `ffmpeg -version`

## Setup
1. Create and activate a virtual environment (recommended):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Configure environment variables:
   - Copy the template and fill in your values:
     ```bash
     cp .env.example .env
     ```
   - Edit `.env` with your project settings:
     ```env
     PROJECT_ID=your-gcp-project-id
     GCP_REGION=us-central1
     # Optional: for headless/service account auth
     # GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account-key.json
     ```

## Google Cloud configuration
This project uses two Google Cloud services:
- Vertex AI (Gemini) for video analysis (crop data)
- Optional: Video Intelligence API for transcription (used by `audio-subitiltes.py` and described in `TRANSCRIPTION_SETUP.md`)

### Enable required APIs
Run the following once for your project:
```bash
# Authenticate (opens browser)
gcloud auth application-default login

# Set default project
gcloud config set project YOUR_PROJECT_ID

# Enable Vertex AI and Video Intelligence APIs
gcloud services enable aiplatform.googleapis.com
# Optional (only if you plan to use transcription scripts)
gcloud services enable videointelligence.googleapis.com
```

### Authentication
This repo uses Application Default Credentials (ADC). After you run `gcloud auth application-default login`, the libraries will pick up credentials automatically. Ensure `PROJECT_ID` and `GCP_REGION` are set in `.env` (copied from `.env.example`).

Headless or CI/CD: set `GOOGLE_APPLICATION_CREDENTIALS` in `.env` to point to a service account key JSON and skip interactive login. ADC will use that file automatically.

## Usage
Place your `.mp4` files in `INPUT_VIDEOS/`.

- Process a single file by name (looked up in `INPUT_VIDEOS/`):
  ```bash
  python3 video-vert.py --input YourVideo.mp4
  ```

- Process a single file by absolute path:
  ```bash
  python3 video-vert.py --input /full/path/to/YourVideo.mp4
  ```

- Process all `.mp4` files in `INPUT_VIDEOS/`:
  ```bash
  python3 video-vert.py --all
  ```

Outputs:
- `OUTPUT_VIDEOS/<basename>_crop_data_framing_temp.json` — Gemini-generated crop segments
- `OUTPUT_VIDEOS/<basename>_vertical_crop_w_framing_pipeline.mp4` — final vertical video

## How it works
1. `video-vert.py` enumerates inputs (single or batch) and calls `verticalize_one_video`.
2. `src/gemini_functions.py` invokes Gemini with a structured prompt and requested JSON schema sized to the input video resolution.
3. `src/utils.py` splits the video into segments per crop change, applies `crop` filter, and concatenates them into a final output.

## Troubleshooting
- FFmpeg not found: install via Homebrew or ensure it’s on PATH
- Permission/credentials errors: re-run `gcloud auth application-default login` and verify `PROJECT_ID`
- API not enabled: run the `gcloud services enable ...` commands above
- No `INPUT_VIDEOS/`: create the folder and add `.mp4` files
- Still failing? Run with a single short video and share the console output

## Notes
- The crop width is computed to keep a 9:16 aspect from the source height; defaults (607x1080) are used when resolution is unknown.
- Batch mode continues on individual file errors and prints a summary.

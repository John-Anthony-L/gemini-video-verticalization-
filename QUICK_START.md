# Quick Start Guide

## Authentication Setup (One-time)

1. **Authenticate with gcloud:**
   ```bash
   gcloud auth application-default login
   ```

2. **Enable required APIs:**
   ```bash
   gcloud services enable videointelligence.googleapis.com
   gcloud services enable storage.googleapis.com
   ```

## Usage Examples

### Process a single video:
```bash
python audio-subitiltes.py Krapopolis_FBCKRA308HL_S02E05_VOD_CC_lowres_chapter_1.mp4
```

### Process with Spanish language:
```bash
python audio-subitiltes.py your_video.mp4 --language es-ES
```

### Use a custom bucket:
```bash
python audio-subitiltes.py your_video.mp4 --bucket my-custom-bucket
```

## What the script does:

1. ✅ Uses your existing project ID: `fox-verticalization`
2. ✅ Uses your region: `us-central1`
3. ✅ Authenticates with gcloud (no service account key needed)
4. ✅ Processes one video at a time
5. ✅ Creates a temporary GCS bucket automatically if needed
6. ✅ Saves transcription results to JSON files
7. ✅ Cleans up temporary files

## Output:

The script will create a JSON file with detailed transcription results including:
- Full transcript text
- Confidence scores
- Word-level timestamps
- Multiple alternatives (if available)

Example output file: `Krapopolis_FBCKRA308HL_S02E05_VOD_CC_lowres_chapter_1_transcription_20251015_120345.json`
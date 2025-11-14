import os
import time
from typing import List, Optional

from google.cloud import storage
from google.cloud.video.transcoder_v1 import TranscoderServiceClient
from google.cloud.video.transcoder_v1.types import Job
from google.api_core import exceptions as gapi_exceptions
from google.cloud import resourcemanager_v3


def ensure_bucket(project_id: str, bucket_name: str, location: str = "us-central1") -> storage.Bucket:
    client = storage.Client(project=project_id)
    bucket = client.bucket(bucket_name)
    if not bucket.exists():
        bucket = client.create_bucket(bucket_or_name=bucket_name, location=location)
    return bucket


def upload_to_gcs(local_path: str, bucket_name: str, dest_blob_path: str) -> str:
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(dest_blob_path)
    blob.upload_from_filename(local_path)
    return f"gs://{bucket_name}/{dest_blob_path}"


def download_from_gcs(gcs_uri: str, local_path: str) -> None:
    assert gcs_uri.startswith("gs://"), "Invalid GCS URI"
    _, _, path = gcs_uri.partition("gs://")
    bucket_name, _, blob_name = path.partition("/")
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    blob.download_to_filename(local_path)


def _wait_for_job_completion(client: TranscoderServiceClient, job_name: str, timeout_sec: int = 3600) -> Job:
    start = time.time()
    while True:
        job = client.get_job(name=job_name)
        state = job.state.name if hasattr(job.state, "name") else str(job.state)
        if job.state == Job.ProcessingState.SUCCEEDED:
            return job
        if job.state == Job.ProcessingState.FAILED:
            raise RuntimeError(f"Transcoder job {job_name} ended with state: {state}")
        if time.time() - start > timeout_sec:
            raise TimeoutError(f"Timed out waiting for job {job_name} to complete")
        time.sleep(5)


def _resolve_parent(project_id: str, location: str) -> str:
    """Return Transcoder parent using numeric project number when possible.

    Some APIs prefer project number. We'll resolve it via Cloud Resource Manager; fall back to ID on failure.
    """
    try:
        client = resourcemanager_v3.ProjectsClient()
        proj = client.get_project(name=f"projects/{project_id}")
        # proj.name is like 'projects/1234567890'
        return f"{proj.name}/locations/{location}"
    except Exception:
        # Fallback to ID
        return f"projects/{project_id}/locations/{location}"


def create_cropped_segment_job(
    project_id: str,
    location: str,
    input_gcs_uri: str,
    output_gcs_prefix: str,
    video_width: int,
    video_height: int,
    crop_width: int,
    x1: int,
    start_seconds: Optional[float] = None,
    duration_seconds: Optional[float] = None,
) -> str:
    """
    Create a Transcoder job for a single segment with cropping and optional trim.
    Returns the GCS URI of the produced MP4 (sd.mp4) under the output prefix.
    """
    client = TranscoderServiceClient()
    parent = _resolve_parent(project_id, location)

    # Enforce even dimensions for H.264 and clamp crop to source bounds
    crop_width_even = (int(crop_width) // 2) * 2  # floor to even
    height_even = (int(video_height) // 2) * 2

    # Clamp x1 so that x1 + crop_width_even <= video_width and x1 >= 0
    x1_int = int(x1)
    max_x1 = max(0, int(video_width) - crop_width_even)
    x1_clamped = max(0, min(x1_int, max_x1))

    # Compute crop margins in pixels relative to source
    left_pixels = x1_clamped
    right_pixels = int(max(0, int(video_width) - (x1_clamped + crop_width_even)))
    top_pixels = 0
    bottom_pixels = 0

    # Build job config using dicts (proto will coerce names)
    edit_atom = {"key": "atom0", "inputs": ["input0"]}
    if start_seconds is not None:
        edit_atom["start_time_offset"] = {"seconds": int(start_seconds), "nanos": int((start_seconds % 1) * 1e9)}
    if duration_seconds is not None:
        # end_time_offset is relative to start of input; use start+duration
        end_total = (start_seconds or 0) + duration_seconds
        edit_atom["end_time_offset"] = {"seconds": int(end_total), "nanos": int((end_total % 1) * 1e9)}

    job = {
        "output_uri": output_gcs_prefix,
        "config": {
            "inputs": [
                {
                    "key": "input0",
                    "uri": input_gcs_uri,
                    "preprocessing_config": {
                        "crop": {
                            "top_pixels": top_pixels,
                            "bottom_pixels": bottom_pixels,
                            "left_pixels": left_pixels,
                            "right_pixels": right_pixels,
                        }
                    },
                }
            ],
            "edit_list": [edit_atom],
            "elementary_streams": [
                {
                    "key": "v0",
                    "video_stream": {
                        "h264": {
                            "bitrate_bps": 2500000,
                            "frame_rate": 30,
                            "height_pixels": height_even,
                            "width_pixels": crop_width_even,
                        }
                    },
                },
                {"key": "a0", "audio_stream": {"codec": "aac", "bitrate_bps": 128000}},
            ],
            "mux_streams": [
                {"key": "sd", "container": "mp4", "elementary_streams": ["v0", "a0"]}
            ],
        },
    }

    try:
        resp = client.create_job(parent=parent, job=job)
    except gapi_exceptions.PermissionDenied as e:
        msg = str(e)
        if "SERVICE_DISABLED" in msg or "transcoder.googleapis.com" in msg:
            raise RuntimeError(
                "Transcoder API appears disabled or your credentials lack permission. "
                "Please ensure: (1) API enabled for the project, (2) your active ADC account has roles/transcoder.jobEditor (or admin) and serviceusage.serviceUsageConsumer, "
                "(3) PROJECT_ID matches the project you enabled."
            ) from e
        raise
    _wait_for_job_completion(client, resp.name)
    # By default, MP4 will be named sd.mp4 under output prefix
    return f"{output_gcs_prefix.rstrip('/')}/sd.mp4"


def create_concat_job(
    project_id: str,
    location: str,
    segment_uris: List[str],
    output_gcs_prefix: str,
    width_pixels: int,
    height_pixels: int,
) -> str:
    """
    Create a Transcoder job that concatenates full segment files in order.
    Returns the GCS URI of the produced MP4 (sd.mp4) under the output prefix.
    """
    client = TranscoderServiceClient()
    parent = _resolve_parent(project_id, location)

    inputs = []
    edits = []
    for idx, uri in enumerate(segment_uris):
        key = f"in{idx}"
        inputs.append({"key": key, "uri": uri})
        edits.append({"key": f"atom{idx}", "inputs": [key]})

    # Enforce even dimensions for the final output as well
    width_even = (int(width_pixels) // 2) * 2
    height_even = (int(height_pixels) // 2) * 2

    job = {
        "output_uri": output_gcs_prefix,
        "config": {
            "inputs": inputs,
            "edit_list": edits,
            "elementary_streams": [
                {
                    "key": "v0",
                    "video_stream": {
                        "h264": {
                            "bitrate_bps": 2500000,
                            "frame_rate": 30,
                            "height_pixels": height_even,
                            "width_pixels": width_even,
                        }
                    },
                },
                {"key": "a0", "audio_stream": {"codec": "aac", "bitrate_bps": 128000}},
            ],
            "mux_streams": [
                {"key": "sd", "container": "mp4", "elementary_streams": ["v0", "a0"]}
            ],
        },
    }

    try:
        resp = client.create_job(parent=parent, job=job)
    except gapi_exceptions.PermissionDenied as e:
        msg = str(e)
        if "SERVICE_DISABLED" in msg or "transcoder.googleapis.com" in msg:
            raise RuntimeError(
                "Transcoder API appears disabled or your credentials lack permission. "
                "Please ensure: (1) API enabled for the project, (2) your active ADC account has roles/transcoder.jobEditor (or admin) and serviceusage.serviceUsageConsumer, "
                "(3) PROJECT_ID matches the project you enabled."
            ) from e
        raise
    _wait_for_job_completion(client, resp.name)
    return f"{output_gcs_prefix.rstrip('/')}/sd.mp4"

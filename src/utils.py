import json
import subprocess
import re
import os
import tempfile
import shutil
import argparse

# --- Paths and constants (absolute) ---
# Resolve project root as the parent of this 'src' directory
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
INPUT_DIR = os.path.join(ROOT_DIR, "INPUT_VIDEOS")
OUTPUT_DIR = os.path.join(ROOT_DIR, "OUTPUT_VIDEOS")

# Fixed crop dimensions
CROP_WIDTH = 607
CROP_HEIGHT = 1080
CROP_Y = 0

def load_crop_data(json_file):
    """
    Loads crop data from a JSON file.
    Returns the crop data array or None if there's an error.
    """
    try:
        with open(json_file, 'r') as f:
            data = json.load(f)
        print(f"Loaded {len(data)} crop segments from {json_file}")
        return data
    except FileNotFoundError:
        print(f"Error: Crop data file '{json_file}' not found.")
        print(f"Please make sure the file exists at the specified path.")
        return None
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in '{json_file}': {e}")
        return None
    except Exception as e:
        print(f"Error loading crop data: {e}")
        return None

def generate_crop_data_with_gemini(video_path, output_json_path):
    """
    Generate crop data using Gemini analysis and save to JSON file.
    Uses the video's actual resolution for coordinate generation.
    """
    try:
        from src.gemini_functions import extract_video_crop_data
        
        # Get video resolution first
        video_info = get_video_info(video_path)
        if not video_info:
            print("Could not get video information for Gemini analysis")
            return False
            
        video_width = video_info['width']
        video_height = video_info['height']
        
        print(f"Generating crop data with Gemini for {video_width}x{video_height} video...")
        
        # Generate crop data using the actual video resolution
        crop_data_json = extract_video_crop_data(video_path, video_width, video_height)
        
        if crop_data_json.startswith("Error"):
            print(f"Gemini analysis failed: {crop_data_json}")
            return False
        
        # Save the generated crop data
        with open(output_json_path, 'w') as f:
            f.write(crop_data_json)
        
        print(f"Crop data generated and saved to: {output_json_path}")
        
        # Parse and show summary
        crop_data = json.loads(crop_data_json)
        print(f"Generated {len(crop_data)} crop segments")
        
        return True
        
    except ImportError:
        print("Error: gemini_functions module not available")
        return False
    except Exception as e:
        print(f"Error generating crop data: {e}")
        return False

def get_video_resolution(video_file):
    """
    Gets the resolution of a video file using FFmpeg.
    Returns (width, height) tuple or None if there's an error.
    """
    try:
        # Use ffprobe to get video information in JSON format
        command = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            video_file
        ]
        
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        
        # Find the video stream
        for stream in data.get('streams', []):
            if stream.get('codec_type') == 'video':
                width = stream.get('width')
                height = stream.get('height')
                if width and height:
                    return (width, height)
        
        return None
        
    except subprocess.CalledProcessError as e:
        print(f"Error running ffprobe: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"Error parsing ffprobe output: {e}")
        return None
    except Exception as e:
        print(f"Error getting video resolution: {e}")
        return None

def get_video_info(video_file):
    """
    Gets comprehensive video information using FFmpeg.
    Returns a dictionary with video details or None if there's an error.
    """
    try:
        # Use ffprobe to get comprehensive video information
        command = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json", 
            "-show_format",
            "-show_streams",
            video_file
        ]
        
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        
        video_info = {}
        
        # Get format information
        format_info = data.get('format', {})
        video_info['duration'] = float(format_info.get('duration', 0))
        video_info['size'] = int(format_info.get('size', 0))
        video_info['bitrate'] = int(format_info.get('bit_rate', 0))
        
        # Find video and audio streams
        for stream in data.get('streams', []):
            if stream.get('codec_type') == 'video':
                video_info['width'] = stream.get('width')
                video_info['height'] = stream.get('height')
                video_info['fps'] = eval(stream.get('r_frame_rate', '0/1'))  # Convert fraction to float
                video_info['video_codec'] = stream.get('codec_name')
                video_info['pixel_format'] = stream.get('pix_fmt')
            elif stream.get('codec_type') == 'audio':
                video_info['audio_codec'] = stream.get('codec_name')
                video_info['sample_rate'] = stream.get('sample_rate')
                video_info['channels'] = stream.get('channels')
        
        return video_info
        
    except subprocess.CalledProcessError as e:
        print(f"Error running ffprobe: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"Error parsing ffprobe output: {e}")
        return None
    except Exception as e:
        print(f"Error getting video info: {e}")
        return None

def timestamp_to_seconds(ts_str):
    """Converts MM:SS.ms timestamp string to total seconds (float)."""
    match = re.match(r"(\d{2}):(\d{2})\.(\d{3})", ts_str)
    if match:
        m, s, ms = map(int, match.groups())
        return m * 60 + s + ms / 1000.0
    raise ValueError(f"Invalid timestamp format: {ts_str}")

def create_video_segments(input_file, data, temp_dir, video_resolution=None):
    """
    Creates individual video segments, each with its own crop settings.
    Returns a list of segment file paths.
    """
    # Calculate crop dimensions based on video resolution
    if video_resolution:
        video_width, video_height = video_resolution
        target_aspect_ratio = 9 / 16
        crop_width = int(video_height * target_aspect_ratio)
        print(f"Using crop size: {crop_width}x{video_height} for {video_width}x{video_height} source")
    else:
        # Fallback to default values
        crop_width = CROP_WIDTH
        video_height = CROP_HEIGHT
        print(f"Using default crop size: {crop_width}x{video_height}")
    
    # Pre-process the data into segments with start/end times
    segments = []
    for i, item in enumerate(data):
        start_time = timestamp_to_seconds(item['timestamp'])
        
        # Determine end time (either next segment start or end of video)
        if i < len(data) - 1:
            end_time = timestamp_to_seconds(data[i + 1]['timestamp'])
        else:
            # For the last segment, we'll let FFmpeg handle the end naturally
            end_time = None
            
        segments.append({
            'start': start_time,
            'end': end_time,
            'x1': item['x1'],
            'reason': item['reason']
        })
    
    segment_files = []
    
    for i, segment in enumerate(segments):
        segment_file = os.path.join(temp_dir, f"segment_{i:03d}.mp4")
        
        end_str = f"{segment['end']:.3f}s" if segment['end'] else "end"
        print(f"Processing segment {i+1}/{len(segments)}: {segment['start']:.3f}s - {end_str}")
        print(f"  Crop position: x={segment['x1']}, reason: {segment['reason']}")
        
        # Build FFmpeg command for this segment
        command = [
            "ffmpeg",
            "-i", input_file,
            "-ss", str(segment['start']),  # Start time
            "-vf", f"crop={crop_width}:{video_height}:{segment['x1']}:{CROP_Y}",
            "-c:v", "libx264",
            "-crf", "23",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-avoid_negative_ts", "make_zero",  # Handle timing issues
            "-y",
            segment_file
        ]
        
        # Add duration if not the last segment
        if segment['end'] is not None:
            duration = segment['end'] - segment['start']
            command.insert(-2, "-t")  # Insert before -y and output file
            command.insert(-2, str(duration))
        
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            segment_files.append(segment_file)
            print(f"  Created segment: {segment_file}")
        except subprocess.CalledProcessError as e:
            print(f"  Error creating segment {i}: {e}")
            print(f"  Command: {' '.join(command)}")
            if e.stderr:
                print(f"  FFmpeg error: {e.stderr}")
            return None
    
    return segment_files

def concatenate_segments(segment_files, output_file, temp_dir):
    """
    Concatenates all video segments into a single output file.
    """
    if not segment_files:
        print("No segments to concatenate!")
        return False
    
    # Create a file list for FFmpeg concat demuxer
    concat_file = os.path.join(temp_dir, "concat_list.txt")
    
    with open(concat_file, 'w') as f:
        for segment_file in segment_files:
            f.write(f"file '{segment_file}'\n")
    
    # Ensure output directory exists
    out_dir = os.path.dirname(output_file)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Use concat demuxer to join segments
    command = [
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-c", "copy",  # Copy streams without re-encoding for speed
        "-y",
        output_file
    ]
    
    print(f"\nConcatenating {len(segment_files)} segments...")
    print("--- Concatenation Command ---")
    print(" ".join(command))
    print("-----------------------------")
    
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        print(f"Successfully created final video: {output_file}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error during concatenation: {e}")
        if e.stderr:
            print(f"FFmpeg error: {e.stderr}")
        return False

def process_video_segments(input_file, output_file, data, video_resolution=None):
    """
    Main function that processes video in segments and concatenates them.
    """
    # Create temporary directory for segments
    with tempfile.TemporaryDirectory(prefix="video_segments_") as temp_dir:
        print(f"Working in temporary directory: {temp_dir}")
        
        # Create individual segments
        segment_files = create_video_segments(input_file, data, temp_dir, video_resolution)
        
        if not segment_files:
            print("Failed to create video segments!")
            return False
        
        # Concatenate segments
        success = concatenate_segments(segment_files, output_file, temp_dir)
        
        if success:
            print(f"\nVerticalization complete!")
            print(f"Output saved to: {output_file}")
            if video_resolution:
                crop_width = int(video_resolution[1] * 9/16)
                print(f"Output Resolution: {crop_width}x{video_resolution[1]} (9:16 aspect ratio)")
            else:
                print(f"Output Resolution: {CROP_WIDTH}x{CROP_HEIGHT} (9:16 aspect ratio)")
        
        return success

def run_ffmpeg_crop(input_file, output_file, crop_filter):
    """Executes the FFmpeg command."""
    
    # Ensure audio is included
    # We must re-encode the video (-c:v libx264) because filtering changes the stream data.
    # The aspect ratio is 607:1080 (9:16).
    command = [
        "ffmpeg",
        "-i", input_file,
        "-vf", crop_filter,
        "-c:v", "libx264",
        "-crf", "23", # Quality setting (23 is good default)
        "-preset", "veryfast", # Faster encoding speed
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-y", # Overwrite output file if it exists
        output_file
    ]
    
    print("--- FFmpeg Command ---")
    print(" ".join(f'"{arg}"' if ' ' in arg else arg for arg in command))
    print("----------------------")
    print(f"Filter expression: {crop_filter}")
    print("----------------------")
    
    try:
        subprocess.run(command, check=True)
        print(f"\nSuccessfully cropped and saved to: {output_file}")
        print(f"Output resolution: {CROP_WIDTH}x{CROP_HEIGHT}")
    except subprocess.CalledProcessError as e:
        print(f"\nFFmpeg error: {e}")
    except FileNotFoundError:
        print("\nError: ffmpeg command not found. Ensure FFmpeg is installed and in your PATH.")


def verticalize_one_video(input_video_path: str) -> bool:
    """Verticalize a single video, generating crop data and writing outputs under OUTPUT_DIR.

    Returns True on success, False on failure.
    """
    # Normalize paths and ensure output directory exists
    input_video_path = os.path.abspath(input_video_path)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Resolve names and output paths
    base = os.path.splitext(os.path.basename(input_video_path))[0]
    output_video = os.path.join(OUTPUT_DIR, f"{base}_vertical_crop_w_framing_pipeline.mp4")
    crop_json = os.path.join(OUTPUT_DIR, f"{base}_crop_data_framing_temp.json")

    # Analyze input
    print("Analyzing input video...")
    video_info = get_video_info(input_video_path)
    if video_info:
        print(f"Input Resolution: {video_info['width']}x{video_info['height']}")
        print(f"Duration: {video_info['duration']:.2f} seconds")
        print(f"Frame Rate: {video_info['fps']:.2f} fps")
        print(f"Video Codec: {video_info['video_codec']}")
        if 'audio_codec' in video_info:
            print(f"Audio Codec: {video_info['audio_codec']}")
        print(f"File Size: {video_info['size'] / (1024*1024):.1f} MB")
        print()
        print(f"Target crop size: {int(video_info['height'] * 9/16)}x{video_info['height']} (9:16 aspect ratio)")
        print()
    else:
        print("Could not get video information, proceeding anyway...\n")

    # Generate crop data with Gemini
    print("Generating crop data with Gemini...")
    if not generate_crop_data_with_gemini(input_video_path, crop_json):
        print("Failed to generate crop data.")
        return False

    crop_data = load_crop_data(crop_json)
    if crop_data is None:
        return False

    print("Starting video verticalization process...")
    print(f"Input: {input_video_path}")
    print(f"Output: {output_video}")
    print(f"Crop data: {crop_json}")
    print(f"Processing {len(crop_data)} crop segments")
    print("=" * 50)

    # Determine resolution for dynamic cropping
    video_resolution = None
    if video_info and 'width' in video_info and 'height' in video_info:
        video_resolution = (video_info['width'], video_info['height'])

    success = process_video_segments(input_video_path, output_video, crop_data, video_resolution)
    if not success:
        print("\nVerticalization failed!")
        return False

    return True
import json
import os
import vertexai
from vertexai import generative_models
from vertexai.generative_models import (
    GenerationConfig,
    GenerativeModel,
    Part,
    HarmCategory,
    HarmBlockThreshold
)

from google.cloud import storage
from google.oauth2 import service_account
from dotenv import load_dotenv
from google.cloud import texttospeech

load_dotenv()

PROJECT_ID = os.environ.get("PROJECT_ID")
GCP_REGION = os.environ.get("GCP_REGION")  
LOCATION = GCP_REGION

# Initialize Vertex AI
if PROJECT_ID and LOCATION:
    vertexai.init(project=PROJECT_ID, location=LOCATION)
else:
    print("Warning: PROJECT_ID and/or GCP_REGION not set in environment variables")

def generate(parts, response_schema=None):
    model = GenerativeModel("gemini-2.5-flash")

    safety_settings = {
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_ONLY_HIGH,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
    }
    
    if response_schema==None:
        generation_config = GenerationConfig(
            max_output_tokens=65535,
            temperature=1.2,
            top_p=0.7,
            response_mime_type="application/json",
        )
    else:
        generation_config = GenerationConfig(
            temperature=1.2,
            top_p=0.7,
            max_output_tokens=65535,  # Increase token limit
            response_mime_type="application/json",
            response_schema=response_schema
        )
        
    response = model.generate_content(
        parts,
        generation_config=generation_config,
        safety_settings=safety_settings,
        stream=False,
    )
    
    return response.text

########################################################
# Generating Metadata from video scenes
########################################################
def extract_video_crop_data(video_path, video_width=1920, video_height=1080):
    """
    Extracts crop bounding box data for video verticalization.
    
    Args:
        video_path: Local path to video file
        video_width: Width of the source video (default: 1920)
        video_height: Height of the source video (default: 1080)
    """
    
    try:
        # Create Part from local file
        video_part = Part.from_data(
            data=open(video_path, 'rb').read(),
            mime_type="video/mp4"
        )
        
        # Calculate crop dimensions for 9:16 aspect ratio
        target_aspect_ratio = 9 / 16
        crop_width = int(video_height * target_aspect_ratio)
        max_x1 = video_width - crop_width
        
        prompt = f"""
        **CONTEXT**: You are an AI Video Analysis Engine tasked with generating precise bounding box coordinates for converting a video into a vertically oriented 9:16 format suitable for platforms like TikTok or YouTube Shorts.

        **CORE INSTRUCTION**:
        Analyze the entire video content. For every segment, identify the single most important point of focus (e.g., primary speaker, key action, significant object). Generate a bounding box that defines a {crop_width}-pixel wide by {video_height}-pixel high (9:16 aspect ratio) crop, centered optimally around the identified point of focus.

        COORDINATE SYSTEM DEFINITION:

        Source Frame: {video_width} (Width) x {video_height} (Height).
        Origin: Top-left corner (0, 0).
        Coordinates: Must be expressed in absolute pixel values (integers).
        Crop Dimensions: The required bounding box must maintain a fixed height of {video_height} (y1=0, y2={video_height}) and a fixed width of {crop_width} (x2 - x1 = {crop_width}).
        
        **FRAMING GUIDELINES & BEST PRACTICES**
        When calculating the optimal X-coordinates for the crop, prioritize compositional integrity over mathematical centering:

        Rule of Thirds: The primary point of interest (e.g., the subject's eye-line, a product detail) should ideally align with the vertical third lines of the 9:16 crop, not always the dead center.
        Headroom: Ensure there is adequate but not excessive space above the subject's head. Avoid "chopping off" the top of the head or leaving vast empty space.
        Lookroom/Leadroom: If a subject is looking or moving strongly to the left or right, bias the crop slightly in that direction to leave "room to look into" the frame.
        Action and Gestures: If the primary focus involves hand gestures or interacting with an object, the crop should be wide enough to contain these critical elements if possible.
        Smooth Transitions: When a change in focus necessitates a shift in the bounding box (a "pan"), the transition should be minimized and calculated to anticipate the required movement slightly, rather than reactive jumps.
        Static Scenes: In scenes with little movement or a single static subject, maintain a steady crop position to avoid unnecessary motion.
        Multiple Subjects: If multiple subjects are present, prioritize the one who is most central to the scene's action or narrative at that moment.
        Complex Scenes: In scenes with multiple points of interest, choose the one that best represents the overall context or narrative of the scene.
        Avoid Frequent Shifts: Do not change the crop position too frequently. Aim for stability and only adjust when there is a clear and significant change in the point of focus.

        **OUTPUT REQUIREMENTS**
        The bounding box should only shift its position (change the X coordinates) when the point of focus moves significantly or a new focus point emerges.
        The timestamp field must be formatted as a string representing MM:SS.ms (Minute:Second.millisecond) and specifies the precise moment when the subsequent bounding box configuration should take effect.
        
        Your response must be a valid JSON array conforming to the following schema:
        """
        
        response_schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp": {
                        "type": "string",
                        "description": "Start time in MM:SS.ms format when this crop configuration takes effect"
                    },
                    "reason": {
                        "type": "string", 
                        "description": "Brief explanation of why this crop position was chosen"
                    },
                    "x1": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": max_x1,
                        "description": "Top-left X coordinate of the crop box"
                    },
                    "y1": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 0,
                        "description": "Top-left Y coordinate (always 0 for full height crop)"
                    },
                    "x2": {
                        "type": "integer", 
                        "minimum": crop_width,
                        "maximum": video_width,
                        "description": f"Bottom-right X coordinate of the crop box (x1 + {crop_width})"
                    },
                    "y2": {
                        "type": "integer",
                        "minimum": video_height,
                        "maximum": video_height,
                        "description": f"Bottom-right Y coordinate (always {video_height} for full height crop)"
                    }
                },
                "required": ["timestamp", "reason", "x1", "y1", "x2", "y2"]
            }
        }

        response = generate([prompt, video_part], response_schema)

        try:
            crop_data = json.loads(response)
            # Validate that it's an array
            if not isinstance(crop_data, list):
                return f"Error: Expected array response, got {type(crop_data)}"
            
            # Validate each item has required fields
            for i, item in enumerate(crop_data):
                required_fields = ["timestamp", "reason", "x1", "y1", "x2", "y2"]
                for field in required_fields:
                    if field not in item:
                        return f"Error: Missing required field '{field}' in item {i}"
            
            return json.dumps(crop_data, indent=2)

        except json.JSONDecodeError as e:
            print(f"JSON decode error: {e}")
            print(f"Full response length: {len(response)}")
            print(f"Response ends with: ...{response[-100:]}")
            return f"Error decoding JSON response: {response}"

    except Exception as e:
        return f"Error processing video: {e}"
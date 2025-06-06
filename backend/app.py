# backend/app.py

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os
import shutil
import uuid
from deepgram import Deepgram
from dotenv import load_dotenv
from fastapi.responses import StreamingResponse
import json
import asyncio
from enum import Enum
import time
import subprocess
import requests
import re
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
from supadata import Supadata, SupadataError
import PyPDF2
import numpy as np
from sentence_transformers import SentenceTransformer
from auth import router as auth_router
from student_modeling import (
    extract_learning_styles,
    update_knowledge_trace,
    save_student_profile,
    get_student_profile,
    get_knowledge_state,
    LearningStyleProfile,
    KnowledgeState
)

app = FastAPI()

# Add CORS middleware to allow frontend to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the authentication router
app.include_router(auth_router)

load_dotenv()


# Get API key from environment variable
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
if not DEEPGRAM_API_KEY:
    raise ValueError("Missing DEEPGRAM_API_KEY environment variable")

# Get Groq API key from environment variable
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    print("Warning: GROQ_API_KEY environment variable not set. Summary generation will be limited.")

SUPADATA_API_KEY = os.getenv("SUPADATA_API_KEY")
if not SUPADATA_API_KEY:
    raise ValueError("Missing SUPADATA_API_KEY environment variable")

supadata = Supadata(api_key=SUPADATA_API_KEY)


# Initialize Groq client if API key is available
groq_client = None
if GROQ_API_KEY:
    try:
        from groq import Groq
        groq_client = Groq(api_key=GROQ_API_KEY)
    except ImportError:
        print("Warning: Groq package not installed. Install with: pip install groq")

# Create uploads directory if it doesn't exist
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Define the teaching modes
class TeachingMode(str, Enum):
    SOCRATIC = "socratic"
    FIVE_YEAR_OLD = "five_year_old"
    HIGH_SCHOOL = "high_school"
    COLLEGE = "college"
    PHD = "phd"

# endpoint returns hello world
@app.get("/")
async def root():
    return {"message": "Hello World"}

async def transcribe_audio(file_path):
    if not DEEPGRAM_API_KEY:
        # Return mock transcription with timestamps for development
        return {
            "transcript": "This is a mock transcription for development purposes. Please set the DEEPGRAM_API_KEY environment variable for actual transcription.",
            "words": [
                {"word": "This", "start": 0.0, "end": 0.3},
                {"word": "is", "start": 0.3, "end": 0.5},
                {"word": "a", "start": 0.5, "end": 0.6},
                {"word": "mock", "start": 0.6, "end": 0.9},
                {"word": "transcription", "start": 0.9, "end": 1.5},
                {"word": "for", "start": 1.5, "end": 1.8},
                {"word": "development", "start": 1.8, "end": 2.5},
                {"word": "purposes", "start": 2.5, "end": 3.1}
            ]
        }
    
    try:
        
        
        # Initialize the Deepgram client
        deepgram = Deepgram(DEEPGRAM_API_KEY)
        
        # Open the audio file
        with open(file_path, 'rb') as buffer_data:
            # Configure transcription options
            payload = { 'buffer': buffer_data }
            options = {
                'smart_format': True,
                'model': "nova-2",
                'language': "en-US",
                'utterances': True,  # Enable utterances to get paragraph breaks
                'detect_topics': True,  # Detect topic changes
                'punctuate': True,
                'diarize': True,  # Speaker diarization if multiple speakers
            }
            
            # Send the audio to Deepgram and get the response
            response = deepgram.transcribe(payload, options)
            
            # Extract full transcript
            transcript = response.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")
            
            # Extract sentences with timestamps            
            paragraphs = response.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("paragraphs", {}).get("paragraphs", [])

            
            # Format word timestamps
            formatted_sentences = []

            for paragraph in paragraphs:
                for sentence in paragraph.get("sentences", []):
                    formatted_sentences.append({
                        "text": sentence.get("text", ""),
                        "start": sentence.get("start", 0.0),
                        "end": sentence.get("end", 0.0)
                    })
            
            return {
                "transcript": transcript,
                "sentences": formatted_sentences
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error during transcription: {str(e)}")

@app.post("/api/transcribe")
async def transcribe_audio_endpoint(file: UploadFile = File(...)):
    """
    Endpoint to upload an audio file and get its transcription
    """
    # Validate file type (optional)
    if not file.content_type.startswith('audio/'):
        raise HTTPException(status_code=400, detail="File must be an audio file")
    
    # Create a unique filename
    file_extension = os.path.splitext(file.filename)[1]
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)
    
    # Save uploaded file
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving file: {str(e)}")
    
    # Transcribe the audio
    try:
        transcription_result = await transcribe_audio(file_path)
        
        # Return the transcription with timestamps
        return {
            "success": True,
            "filename": file.filename,
            "transcription": transcription_result["transcript"],
            "sentences": transcription_result["sentences"]
        }
    except Exception as e:
        # Clean up the file in case of error
        if os.path.exists(file_path):
            os.remove(file_path)
        raise e
    finally:
        # Clean up after processing (optional - you might want to keep files)
        if os.path.exists(file_path):
            os.remove(file_path)

def generate_bullet_summary(transcript):
    """
    Generate a bullet-point summary of a transcript using Groq API
    """
    if not GROQ_API_KEY or not groq_client:
        # Return mock summary if Groq API is not available
        return """
        • This is a mock summary for development purposes.
        • Please set the GROQ_API_KEY environment variable for actual summary generation.
        • The real summary would extract key points from the transcript.
        • It would be organized as a bullet-point list for easy reading.
        """
        
    try:
        # Define the prompt for generating bullet point summaries
        prompt = f"""
        Create a concise and well-organized bullet point summary for the provided transcript.

        - Identify key points and important details from the transcript.
        - Summarize information in clear and concise bullet points.
        - Ensure that the bullet points capture the essence of the conversation or content.
        - Organize the bullets logically to maintain the flow of information.

        # Steps

        1. Read through the transcript to understand the main topics and key details.
        2. Identify and note down significant points, arguments, or data.
        3. Summarize these points into clear, concise bullet points.
        4. Ensure logical flow and organization of bullet points.
        5. Review the bullet points to ensure they are representative of the transcript's content.

        # Output Format

        1. Use markdown headers (#) for main sections
        2. Use bullet points (*) for key points
        3. Organize content into clear sections
        4. Include:
           - Main topics/themes
           - Key points and arguments
           - Important details and examples
           - Conclusions or takeaways

        # Examples

        ## Example Input
        [Transcript of a conversation or presentation.]

        ## Example Output
        - Introduction of the main topic
        - Key argument 1: [Summary of the argument]
        - Key argument 2: [Summary of the argument]
        - Closing remarks: [Summary of conclusions]
        (Note: In a realistic example, more detailed key points should be included.) 

        # Notes

        - Focus on clarity and brevity.
        - Avoid redundant information.
        
        Transcript:
        {transcript}
        """
        
        # Call Groq API to generate the summary
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",  # Using newer Llama 3.3 70B model
            messages=[
                {"role": "system", "content": "You are a helpful assistant that creates concise, well-organized bullet point summaries."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,  # Lower temperature for more focused responses
            max_tokens=1024
        )
        
        # Extract the summary from the response
        summary = response.choices[0].message.content
        return summary
    except Exception as e:
        return f"Error generating summary: {str(e)}"

# Define request and response models for the summary endpoint
class SummaryRequest(BaseModel):
    transcript: str

class SummaryResponse(BaseModel):
    success: bool
    summary: str

@app.post("/api/generate-summary", response_model=SummaryResponse)
async def generate_summary_endpoint(request: SummaryRequest):
    """
    Endpoint to generate a bullet-point summary from a transcript
    """
    try:
        if not request.transcript:
            raise HTTPException(status_code=400, detail="Transcript is required")
            
        # Truncate very long transcripts to prevent API limits
        # Most LLM APIs have context limits
        max_length = 16000  # Adjust based on the model's context window
        truncated_transcript = request.transcript[:max_length]
        if len(request.transcript) > max_length:
            truncated_transcript += "\n[Transcript truncated due to length...]"
            
        summary = generate_bullet_summary(truncated_transcript)
        
        return {
            "success": True,
            "summary": summary
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating summary: {str(e)}")

def generate_quiz_questions(transcript, num_questions=5):
    """
    Generate multiple-choice quiz questions based on a transcript using Groq API
    """
    if not GROQ_API_KEY or not groq_client:
        # Return mock questions if Groq API is not available
        return [
            {
                "question": "What is the main topic of this mock transcript?",
                "options": [
                    "Artificial Intelligence",
                    "Machine Learning",
                    "Data Science",
                    "Mock Data"
                ],
                "correct_answer": 3
            },
            {
                "question": "This is a mock question because...",
                "options": [
                    "The GROQ_API_KEY is not set",
                    "The transcript is too short",
                    "The system is in testing mode",
                    "All of the above"
                ],
                "correct_answer": 0
            }
        ]
    
    try:
        # Define the prompt for generating quiz questions
        prompt = f"""
        Create a quiz with {num_questions} multiple-choice questions based on the following transcript.
        
        Requirements:
        - Generate exactly {num_questions} questions (or fewer if the transcript is very short)
        - Each question should have 4 options (A, B, C, D)
        - Only one option should be correct
        - Questions should test understanding of key concepts from the transcript
        - Questions should vary in difficulty (some easy, some moderate, some challenging)
        - Include the correct answer index (0-based, where 0 is the first option)
        
        Format your response as a JSON array of objects, with each object having:
        - "question": The question text
        - "options": An array of 4 possible answers
        - "correct_answer": The index (0-3) of the correct answer
        
        Example format:
        [
          {{
            "question": "What is the main topic discussed in the lecture?",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correct_answer": 2
          }},
          ...more questions...
        ]
        
        Important: Your entire response should be valid JSON that can be parsed. Do not include any explanatory text outside the JSON array.
        
        Transcript:
        {transcript}
        """
        
        # Call Groq API to generate the questions
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",  # Using newer Llama 3.3 70B model
            messages=[
                {"role": "system", "content": "You are a helpful assistant that creates educational quizzes. You always respond with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,  # Slightly higher temperature for creative questions
            max_tokens=2048,
            response_format={"type": "json_object"}  # Ensure JSON response
        )
        
        # Extract the quiz from the response
        quiz_text = response.choices[0].message.content
        
        # Parse JSON
        import json
        try:
            quiz_data = json.loads(quiz_text)
            # If the JSON is wrapped in an object, extract the questions array
            if isinstance(quiz_data, dict) and "questions" in quiz_data:
                questions = quiz_data["questions"]
            # If it's directly an array
            elif isinstance(quiz_data, list):
                questions = quiz_data
            else:
                # Try to find any array in the response
                for key, value in quiz_data.items():
                    if isinstance(value, list) and len(value) > 0:
                        questions = value
                        break
                else:
                    # Fallback - couldn't find a valid array
                    raise ValueError("Could not extract questions array from response")
                
            # Validate and clean up questions
            validated_questions = []
            for q in questions:
                if "question" in q and "options" in q and "correct_answer" in q:
                    # Ensure correct_answer is an integer
                    if isinstance(q["correct_answer"], str) and q["correct_answer"].isdigit():
                        q["correct_answer"] = int(q["correct_answer"])
                    
                    # Ensure correct_answer is within valid range
                    if not isinstance(q["correct_answer"], int) or q["correct_answer"] < 0 or q["correct_answer"] >= len(q["options"]):
                        # Default to first option if invalid
                        q["correct_answer"] = 0
                        
                    validated_questions.append(q)
            
            return validated_questions
        except Exception as e:
            return [{"question": f"Error parsing quiz questions: {str(e)}",
                    "options": ["Error", "Try again", "Check logs", "Contact support"],
                    "correct_answer": 2}]
        
    except Exception as e:
        return [{"question": f"Error generating quiz: {str(e)}",
                "options": ["Error", "Try again", "Check API key", "Contact support"],
                "correct_answer": 2}]

# Define request and response models for the quiz endpoint
class QuizRequest(BaseModel):
    transcript: str
    num_questions: Optional[int] = 5

class QuizQuestion(BaseModel):
    question: str
    options: List[str]
    correct_answer: int

class QuizResponse(BaseModel):
    success: bool
    questions: List[QuizQuestion]

@app.post("/api/generate-quiz", response_model=QuizResponse)
async def generate_quiz_endpoint(request: QuizRequest):
    """
    Endpoint to generate a quiz with multiple-choice questions from a transcript
    """
    try:
        if not request.transcript:
            raise HTTPException(status_code=400, detail="Transcript is required")
            
        # Truncate very long transcripts to prevent API limits
        max_length = 16000  # Adjust based on the model's context window
        truncated_transcript = request.transcript[:max_length]
        if len(request.transcript) > max_length:
            truncated_transcript += "\n[Transcript truncated due to length...]"
            
        # Ensure num_questions is within reasonable limits
        num_questions = max(1, min(request.num_questions, 10))
        
        questions = generate_quiz_questions(truncated_transcript, num_questions)
        
        return {
            "success": True,
            "questions": questions
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating quiz: {str(e)}")

# Define the chat message model
class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str

# Define the chat request model
class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    transcript: str

# Define the chat response model
class ChatResponse(BaseModel):
    message: str
    
@app.post("/api/chat", response_model=ChatResponse)
async def chat_with_tutor(request: ChatRequest, user_id: str = None):
    """
    Endpoint to chat with an AI tutor about the transcript content with enhanced learning style analysis
    """
    try:
        # Get or create student profile
        profile = await get_student_profile(user_id) if user_id else None
        if not profile:
            profile = LearningStyleProfile()
        
        # Extract learning styles from the conversation with temporal weighting
        chat_history = [msg.content for msg in request.messages]
        new_profile = extract_learning_styles(chat_history)
        
        # Update knowledge state with enhanced tracking
        knowledge_state = update_knowledge_trace(chat_history)
        
        # Calculate learning metrics with more sophisticated analysis
        if user_id:
            # Calculate completion rate based on knowledge state with topic weights
            if knowledge_state.topics:
                weighted_completion = sum(
                    score * weight 
                    for topic, (score, weight) in knowledge_state.topics.items()
                )
                total_weight = sum(weight for _, (_, weight) in knowledge_state.topics.items())
                completion_rate = weighted_completion / total_weight if total_weight > 0 else 0.0
            else:
                completion_rate = 0.0
            
            # Calculate time to learn with exponential moving average
            message_times = [msg.timestamp for msg in request.messages if hasattr(msg, 'timestamp')]
            if len(message_times) >= 2:
                time_diffs = [(message_times[i] - message_times[i-1]).total_seconds() / 60 
                            for i in range(1, len(message_times))]
                alpha = 0.7  # EMA smoothing factor
                avg_time = time_diffs[0]
                for diff in time_diffs[1:]:
                    avg_time = alpha * diff + (1 - alpha) * avg_time
            else:
                avg_time = profile.learning_metrics.get('time_to_learn', 0.0)
            
            # Calculate engagement score with multiple factors
            total_chars = sum(len(msg.content) for msg in request.messages if msg.role == "user")
            avg_msg_length = total_chars / len(request.messages) if request.messages else 0
            
            # Consider message frequency, length, and interaction quality
            time_factor = min(1.0, avg_time / 120)  # Cap at 2 hours
            length_factor = min(1.0, avg_msg_length / 500)
            interaction_factor = min(1.0, len(request.messages) / 20)
            
            engagement_score = (
                time_factor * 0.3 +
                length_factor * 0.4 +
                interaction_factor * 0.3
            )
            
            # Update learning metrics with new calculations
            new_profile.learning_metrics = {
                'completion_rate': completion_rate,
                'time_to_learn': avg_time,
                'engagement_score': engagement_score
            }
            
            # Merge profiles with temporal weighting
            alpha = 0.7  # Profile update smoothing factor
            for category in ['perceptual_mode', 'cognitive_style', 'social_preference', 
                           'instruction_style', 'assessment_preference']:
                current = getattr(profile, category)
                new = getattr(new_profile, category)
                for key in current:
                    current[key] = alpha * new[key] + (1 - alpha) * current[key]
            
            # Save the updated profile
            await save_student_profile(user_id, profile)
        
        # Generate response based on learning style
        response = await generate_adaptive_response(request.messages, profile)
        
        return ChatResponse(message=response)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def generate_adaptive_response(messages: List[ChatMessage], profile: Optional[LearningStyleProfile] = None) -> str:
    """
    Generate a response that's adapted to the student's learning style
    """
    # Determine the dominant learning style
    perceptual_styles = profile.perceptual_mode if profile else {'visual': 0.25, 'auditory': 0.25, 'reading_writing': 0.25, 'kinesthetic': 0.25}
    dominant_style = max(perceptual_styles.items(), key=lambda x: x[1])[0]
    
    # Adapt the response based on learning style
    style_prompts = {
        'visual': "Include visual descriptions and metaphors. Use spatial language and encourage visualization.",
        'auditory': "Use sound-based metaphors and encourage verbal repetition. Focus on explanations through dialogue.",
        'reading_writing': "Provide written explanations with clear structure. Include lists and definitions.",
        'kinesthetic': "Include hands-on examples and practical applications. Use action-oriented language."
    }
    
    # Add style-specific instruction to the system prompt
    style_instruction = style_prompts.get(dominant_style, "")
    
    # Modify the system prompt to include learning style adaptation
    system_prompt = f"""You are an intelligent AI tutor who adapts to each student's learning style.
{style_instruction}
Your goal is to help the student understand the concepts deeply while matching their preferred way of learning."""
    
    # Add the system message to the conversation
    full_messages = [
        ChatMessage(role="system", content=system_prompt)
    ] + messages
    
    # Generate the response using the existing chat generation logic
    response = await generate_socratic_response(full_messages)
    
    return response

def get_socratic_system_prompt():
    """
    Create the system prompt for the Socratic tutor
    """
    return """You are a Socratic tutor. Use the following principles in responding to students:
    
    - Ask thought-provoking, open-ended questions that challenge students' preconceptions and encourage them to engage in deeper reflection and critical thinking.
    - Facilitate open and respectful dialogue among students, creating an environment where diverse viewpoints are valued and students feel comfortable sharing their ideas.
    - Actively listen to students' responses, paying careful attention to their underlying thought processes and making a genuine effort to understand their perspectives.
    - Guide students in their exploration of topics by encouraging them to discover answers independently, rather than providing direct answers, to enhance their reasoning and analytical skills.
    - Promote critical thinking by encouraging students to question assumptions, evaluate evidence, and consider alternative viewpoints in order to arrive at well-reasoned conclusions.
    - Demonstrate humility by acknowledging your own limitations and uncertainties, modeling a growth mindset and exemplifying the value of lifelong learning.

    Base your responses on the following transcription content. Your goal is not to simply provide answers, but to help the student think critically about the material through Socratic questioning.

    Keep your responses concise (3-5 sentences maximum) unless elaboration is necessary to explain a complex concept.
    """

def generate_socratic_response(messages):
    """
    Generate a Socratic tutor response using the Groq API
    """
    if not GROQ_API_KEY or not groq_client:
        # Return mock response if Groq API is not available
        return "I'd be happy to discuss this lecture with you! What specific aspect would you like to explore further? Is there a concept you find particularly challenging or interesting? (Note: This is a mock response as the Groq API key is not configured)"
    
    try:
        # Call Groq API to generate the response
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",  # Using Llama 3.3 70B model
            messages=messages,
            temperature=0.7,  # Slightly higher temperature for more varied responses
            max_tokens=1024
        )
        
        # Extract the response text
        return response.choices[0].message.content
    except Exception as e:
        return f"I'm having trouble processing your question. Could you try asking in a different way? (Error: {str(e)})"

@app.post("/api/chat-stream")
async def chat_with_tutor_stream(request: ChatRequest):
    """
    Endpoint to chat with an AI tutor with streaming response
    """
    try:
        if not request.transcript:
            raise HTTPException(status_code=400, detail="Transcript is required")
        
        if not request.messages or len(request.messages) == 0:
            raise HTTPException(status_code=400, detail="At least one message is required")
        
        # Always use the Socratic tutor system prompt
        system_prompt = get_socratic_system_prompt()
        
        # Format messages for the LLM
        formatted_messages = [
            {"role": "system", "content": system_prompt},
        ]
        
        # Add transcript context
        context_message = {
            "role": "system", 
            "content": f"The following is the transcript of a lecture that the student wants to discuss:\n\n{request.transcript[:8000]}"
        }
        formatted_messages.append(context_message)
        
        # Add conversation history
        for msg in request.messages:
            formatted_messages.append({"role": msg.role, "content": msg.content})
        
        # Return streaming response
        return StreamingResponse(
            generate_streaming_response(formatted_messages),
            media_type="text/event-stream"
        )
    
    except Exception as e:
        error_json = json.dumps({"error": str(e)})
        async def error_stream():
            yield f"data: {error_json}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

async def generate_streaming_response(messages):
    """
    Generate a streaming response from the model - optimized version
    """
    if not GROQ_API_KEY or not groq_client:
        # Mock streaming for development without API key
        mock_response = "I'd be happy to discuss this lecture with you! What specific aspect would you like to explore further? Is there a concept you find particularly challenging or interesting? (Note: This is a mock response as the Groq API key is not configured)"
        
        # Stream by blocks for better performance
        blocks = mock_response.split('. ')
        for block in blocks:
            yield f"data: {json.dumps({'chunk': block + '. '})}\n\n"
            await asyncio.sleep(0.08)  # Slightly shorter delay
        
        yield f"data: {json.dumps({'done': True})}\n\n"
        return
    
    try:
        # Call Groq API with streaming enabled
        stream = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.7,
            max_tokens=1024,
            stream=True
        )
        
        # Buffer for more efficient sending
        buffer = ""
        last_send_time = time.time()
        
        # Stream the response chunks with optimized buffering
        for chunk in stream:
            content = chunk.choices[0].delta.content
            if content:
                buffer += content
                
                # Send in larger chunks or after a time threshold to reduce overhead
                current_time = time.time()
                should_send = (
                    len(buffer) >= 10 or  # Send if buffer has 10+ characters
                    '.' in buffer or      # Send if buffer contains sentence end
                    '\n' in buffer or     # Send if buffer contains newline
                    current_time - last_send_time > 0.2  # Send at least every 200ms
                )
                
                if should_send and buffer:
                    yield f"data: {json.dumps({'chunk': buffer})}\n\n"
                    buffer = ""
                    last_send_time = current_time
        
        # Send any remaining buffered content
        if buffer:
            yield f"data: {json.dumps({'chunk': buffer})}\n\n"
        
        # Signal completion
        yield f"data: {json.dumps({'done': True})}\n\n"
    
    except Exception as e:
        error_message = f"I'm having trouble processing your question. Could you try asking in a different way? (Error: {str(e)})"
        yield f"data: {json.dumps({'chunk': error_message})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

def get_youtube_subtitles(youtube_url):
    try:
        # Run yt-dlp to get subtitles using subprocess
        result = subprocess.run(
            ["python", "-m", "yt_dlp", "--write-auto-sub", "--sub-format", "vtt", 
             "--skip-download", "--print-json", youtube_url],
            capture_output=True, text=True
        )
        
        if result.returncode != 0:
            return f"Error: Failed to fetch subtitles. {result.stderr}"
        
        json_output = json.loads(result.stdout)
        subtitles = json_output.get("automatic_captions", {}).get("en", [])
        
        if not subtitles:
            return "Error: No subtitles found for this video"
        
        subtitle_url = subtitles[-1]["url"]
        
        response = requests.get(subtitle_url)
        if response.status_code != 200:
            return f"Error: Failed to download subtitles. Status code: {response.status_code}"
        
        vtt_content = response.text
        
        # Extract text content (no timestamps)
        pattern = r'\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}\s*(.*?)(?=\n\d{2}:\d{2}:\d{2}\.\d{3}|$)'
        matches = re.findall(pattern, vtt_content, re.DOTALL)
        
        # Process and clean up the text with better formatting
        full_transcript = ""
        current_sentence = ""
        
        for text in matches:
            # Clean up the text
            clean_text = re.sub(r'align:(?:start|middle|end)\s+position:\d+%\s*', '', text)
            clean_text = re.sub(r'<[^>]+>', '', clean_text)
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()
            
            if clean_text:
                # Ensure proper spacing between segments
                if full_transcript and not full_transcript.endswith(('.', '!', '?', '"', "'", ':', ';')):
                    full_transcript += " "
                full_transcript += clean_text
        
        # Improve sentence capitalization and spacing
        full_transcript = re.sub(r'([.!?])\s*([a-z])', lambda m: m.group(1) + ' ' + m.group(2).upper(), full_transcript)
        
        return {
            "full_transcript": full_transcript.strip(),
            "video_title": json_output.get("title", "YouTube Video")
        }
        
    except Exception as e:
        return f"Error processing YouTube subtitles: {str(e)}"


# Define request model for YouTube URL
class YouTubeRequest(BaseModel):
    youtube_url: str

@app.post("/api/youtube-transcribe2")
async def youtube_transcribe_endpoint(request: YouTubeRequest):
    """
    Endpoint to get transcription from a YouTube video URL (without timestamps)
    """
    try:
        if not request.youtube_url:
            raise HTTPException(status_code=400, detail="YouTube URL is required")
            
        # Validate URL format (simple check)
        if not request.youtube_url.startswith(("https://www.youtube.com/", "https://youtu.be/")):
            raise HTTPException(status_code=400, detail="Invalid YouTube URL format")
            
        # Get the transcription
        result = get_youtube_subtitles(request.youtube_url)
        
        # Check if the result is an error message
        if isinstance(result, str) and result.startswith("Error"):
            raise HTTPException(status_code=500, detail=result)
            
        # Format the response without timestamps/sentences
        return {
            "success": True,
            "video_title": result.get("video_title", "YouTube Video"),
            "transcription": result.get("full_transcript", "")
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing YouTube transcription: {str(e)}")

def get_direct_system_prompt():
    """
    Create the system prompt for the direct answer mode
    """
    return """You are a helpful assistant that answers questions directly and accurately.
    
    - Provide clear, concise answers to the user's questions based on the transcription content.
    - If the answer is explicitly stated in the transcription, provide it directly.
    - If the answer requires inference, make reasonable inferences based solely on the transcription content.
    - If the question cannot be answered from the transcription, politely explain that the information is not available.
    - Include relevant details from the transcription to support your answers.
    - Keep your responses informative but concise, focusing on the most relevant information.
    
    Base your responses solely on the following transcription content.
    """

@app.post("/api/chat-direct")
async def chat_with_direct_answers(request: ChatRequest):
    """
    Endpoint to chat with AI that provides direct answers about the transcript content
    """
    try:
        if not request.transcript:
            raise HTTPException(status_code=400, detail="Transcript is required")
        
        if not request.messages or len(request.messages) == 0:
            raise HTTPException(status_code=400, detail="At least one message is required")
        
        # Format messages for the LLM
        formatted_messages = [
            {"role": "system", "content": get_direct_system_prompt()},
        ]
        
        # Add transcript context
        context_message = {
            "role": "system", 
            "content": f"The following is the transcript of a lecture that the user is asking about:\n\n{request.transcript[:8000]}"
        }
        formatted_messages.append(context_message)
        
        # Add conversation history
        for msg in request.messages:
            formatted_messages.append({"role": msg.role, "content": msg.content})
        
        # Generate response
        response = generate_direct_response(formatted_messages)
        
        return {
            "message": response
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating response: {str(e)}")

def generate_direct_response(messages):
    """
    Generate a direct answer response using the Groq API
    """
    if not GROQ_API_KEY or not groq_client:
        # Return mock response if Groq API is not available
        return "Based on the transcript, I can tell you that... (Note: This is a mock response as the Groq API key is not configured)"
    
    try:
        # Call Groq API to generate the response
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",  # Using Llama 3.3 70B model
            messages=messages,
            temperature=0.3,  # Lower temperature for more factual responses
            max_tokens=1024
        )
        
        # Extract the response text
        return response.choices[0].message.content
    except Exception as e:
        return f"I'm having trouble processing your question. Could you try asking in a different way? (Error: {str(e)})"

@app.post("/api/chat-direct-stream")
async def chat_with_direct_stream(request: ChatRequest):
    """
    Endpoint to chat with direct answers with streaming response
    """
    try:
        if not request.transcript:
            raise HTTPException(status_code=400, detail="Transcript is required")
        
        if not request.messages or len(request.messages) == 0:
            raise HTTPException(status_code=400, detail="At least one message is required")
        
        # Use the direct answer system prompt
        system_prompt = get_direct_system_prompt()
        
        # Format messages for the LLM
        formatted_messages = [
            {"role": "system", "content": system_prompt},
        ]
        
        # Add transcript context
        context_message = {
            "role": "system", 
            "content": f"The following is the transcript of a lecture that the user is asking about:\n\n{request.transcript[:8000]}"
        }
        formatted_messages.append(context_message)
        
        # Add conversation history
        for msg in request.messages:
            formatted_messages.append({"role": msg.role, "content": msg.content})
        
        # Return streaming response
        return StreamingResponse(
            generate_streaming_response(formatted_messages),
            media_type="text/event-stream"
        )
    
    except Exception as e:
        error_json = json.dumps({"error": str(e)})
        async def error_stream():
            yield f"data: {error_json}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

def extract_video_id(youtube_url):
    """Extract video ID from YouTube URL"""
    if "youtu.be/" in youtube_url:
        return youtube_url.split("youtu.be/")[1].split("?")[0]
    elif "youtube.com/watch?v=" in youtube_url:
        return youtube_url.split("v=")[1].split("&")[0]
    elif "youtube.com/v/" in youtube_url:
        return youtube_url.split("/v/")[1].split("?")[0]
    else:
        return None

@app.post("/api/youtube-transcribe")
async def youtube_transcribe_v2_endpoint(request: YouTubeRequest):
    """
    Alternative endpoint to get transcription using youtube-transcript-api
    """
    try:
        print("Starting transcription process...")
        if not request.youtube_url:
            raise HTTPException(status_code=400, detail="YouTube URL is required")
            
        # Validate URL format
        if not request.youtube_url.startswith(("https://www.youtube.com/", "https://youtu.be/")):
            raise HTTPException(status_code=400, detail="Invalid YouTube URL format")
        
        # Extract video ID
        video_id = extract_video_id(request.youtube_url)
        if not video_id:
            raise HTTPException(status_code=400, detail="Could not extract video ID from URL")
            
        try:
            text_transcript = supadata.youtube.transcript(
                video_id=video_id,
                text=True,
                lang="en"
            )
            
            formatted_transcript = text_transcript.content
            
            return {
                "success": True,
                "video_title": "YouTube Video",  # Default title
                "transcription": formatted_transcript
            }
            
        except Exception as e:
            error_message = str(e)
            raise HTTPException(
            status_code=500,
            detail="Error processing YouTube transcription")
            
                
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing YouTube transcription: {str(e)}")

# Add new models for PDF processing
class PDFSummaryResponse(BaseModel):
    success: bool
    summary: str
    questions: List[QuizQuestion]
    transcript: str
    error: Optional[str] = None

@app.post("/api/process-pdf", response_model=PDFSummaryResponse)
async def process_pdf_endpoint(file: UploadFile = File(...)):
    """
    Endpoint to process PDF files and generate summaries using RAG
    """
    try:
        if not file.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="File must be a PDF")

        # Create a temporary file to store the uploaded PDF
        temp_file_path = os.path.join(UPLOAD_DIR, f"temp_{uuid.uuid4()}.pdf")
        try:
            # Save the uploaded file temporarily
            with open(temp_file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)

            # Extract text from PDF
            pdf_text = extract_text_from_pdf_file(temp_file_path)
            
            # Create chunks
            chunks = create_chunks(pdf_text)
            
            if not chunks:
                raise HTTPException(status_code=400, detail="Could not extract text from PDF")

            # Generate summary using Groq (simpler approach without RAG for now)
            if not GROQ_API_KEY or not groq_client:
                raise HTTPException(
                    status_code=500, 
                    detail="GROQ_API_KEY not configured"
                )
                
            prompt = f"""
            Create a concise and well-organized bullet point summary for the provided transcript.

            - Identify key points and important details from the transcript.
            - Summarize information in clear and concise bullet points.
            - Ensure that the bullet points capture the essence of the conversation or content.
            - Organize the bullets logically to maintain the flow of information.

            # Steps

            1. Read through the transcript to understand the main topics and key details.
            2. Identify and note down significant points, arguments, or data.
            3. Summarize these points into clear, concise bullet points.
            4. Ensure logical flow and organization of bullet points.
            5. Review the bullet points to ensure they are representative of the transcript's content.

            # Output Format

            1. Use markdown headers (#) for main sections
            2. Use bullet points (*) for key points
            3. Organize content into clear sections
            4. Include:
            - Main topics/themes
            - Key points and arguments
            - Important details and examples
            - Conclusions or takeaways

            # Examples

            ## Example Input
            [Transcript of a conversation or presentation.]

            ## Example Output
            - Introduction of the main topic
            - Key argument 1: [Summary of the argument]
            - Key argument 2: [Summary of the argument]
            - Closing remarks: [Summary of conclusions]
            (Note: In a realistic example, more detailed key points should be included.) 

            # Notes

            - Focus on clarity and brevity.
            - Avoid redundant information.
            
            Transcript:
            {pdf_text}
            """
            
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",  # Using newer Llama 3.3 70B model
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that creates concise, well-organized bullet point summaries."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,  # Lower temperature for more focused responses
                max_tokens=1024
            )

            questions = generate_quiz_questions(pdf_text, 5)
                
            summary = response.choices[0].message.content
            
            return {
                "success": True,
                "transcript": pdf_text,
                "summary": summary,
                "questions": questions,
                "error": None
            }

        finally:
            # Clean up the temporary file
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
                
    except Exception as e:
        return {
            "success": False,
            "summary": "",
            "transcript": "",  # Add empty transcript
            "questions": [],   # Add empty questions list
            "error": str(e)
        }

def extract_text_from_pdf_file(file_path: str) -> str:
    """
    Extract text from a PDF file
    """
    try:
        with open(file_path, 'rb') as file:
            # Create a PDF reader object
            pdf_reader = PyPDF2.PdfReader(file)
            # Join text from all pages into one string
            text = " ".join(page.extract_text() or "" for page in pdf_reader.pages)
            return text.strip()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error reading PDF: {str(e)}")

# Add the RAG helper functions
def create_chunks(text: str, chunk_size: int = 500) -> List[str]:
    """
    Break the text into chunks of roughly chunk_size characters each.
    """
    words = text.split()
    chunks = []
    current_chunk = []
    current_length = 0
    
    for word in words:
        current_length += len(word) + 1  # +1 for space
        if current_length > chunk_size:
            chunks.append(" ".join(current_chunk))
            current_chunk = [word]
            current_length = len(word)
        else:
            current_chunk.append(word)
    if current_chunk:
        chunks.append(" ".join(current_chunk))
    return chunks

def get_embeddings(texts: List[str]) -> List[List[float]]:
    """
    Get embeddings using Hugging Face Inference API
    """
        
    embeddings = []
    batch_size = 8
    
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            time.sleep(1)  # Rate limiting
            model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
            batch_embeddings = model.encode(batch)
            embeddings.extend(batch_embeddings)
                            
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error getting embeddings: {str(e)}"
            )
            
    return embeddings

def search_relevant_chunks(query_embedding: List[float],
                         chunk_embeddings: List[List[float]],
                         chunks: List[str],
                         top_k: int = 3) -> List[str]:
    """
    Find most relevant chunks using cosine similarity
    """
    if not chunks or not chunk_embeddings:
        return []

    query_vec = np.array(query_embedding)
    chunk_mat = np.array(chunk_embeddings)
    
    similarities = np.dot(chunk_mat, query_vec) / (
        np.linalg.norm(chunk_mat, axis=1) * np.linalg.norm(query_vec)
    )
    
    top_indices = np.argsort(similarities)[-top_k:][::-1]
    return [chunks[i] for i in top_indices]

# Add environment variable check
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
if not ELEVENLABS_API_KEY:
    print("Warning: ELEVENLABS_API_KEY environment variable not set")

class SignedUrlResponse(BaseModel):
    signedUrl: str

@app.get("/api/get-signed-url", response_model=SignedUrlResponse)
async def get_signed_url():
    """Get a signed URL for ElevenLabs voice agent conversation"""
    try:
        if not ELEVENLABS_API_KEY:
            raise HTTPException(
                status_code=500, 
                detail="ELEVENLABS_API_KEY not configured"
            )

        # Get the agent ID from environment variable
        agent_id = os.getenv("ELEVENLABS_AGENT_ID")
        if not agent_id:
            raise HTTPException(
                status_code=500,
                detail="ELEVENLABS_AGENT_ID not configured"
            )

        # Make request to ElevenLabs API
        response = await requests.get(
            f"https://api.elevenlabs.io/v1/convai/conversation/get_signed_url?agent_id={agent_id}",
            headers={
                "xi-api-key": ELEVENLABS_API_KEY
            }
        )

        if not response.ok:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Failed to get signed URL: {response.text}"
            )

        data = response.json()
        return {"signedUrl": data["signed_url"]}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error getting signed URL: {str(e)}"
        )

class ConceptDetectiveRequest(BaseModel):
    transcript: str

class ConceptDetectiveQuestion(BaseModel):
    text: str
    type: str

class ConceptDetectiveLevel(BaseModel):
    title: str
    story: str
    questions: List[ConceptDetectiveQuestion]

class ConceptDetectiveResponse(BaseModel):
    success: bool
    analogy: str
    description: str
    levels: List[ConceptDetectiveLevel]
    error: Optional[str] = None

@app.post("/api/generate-concept-detective", response_model=ConceptDetectiveResponse)
async def generate_concept_detective(request: ConceptDetectiveRequest):
    """
    Generate a Concept Detective game based on the transcript content
    """
    try:
        if not request.transcript:
            raise HTTPException(status_code=400, detail="Transcript is required")
            
        # Truncate very long transcripts to prevent API limits
        max_length = 16000  # Adjust based on the model's context window
        truncated_transcript = request.transcript[:max_length]
        if len(request.transcript) > max_length:
            truncated_transcript += "\n[Transcript truncated due to length...]"
            
        # Use Groq to generate the game data
        if not GROQ_API_KEY or not groq_client:
            raise HTTPException(
                status_code=500, 
                detail="GROQ_API_KEY not configured"
            )
            
        prompt = f"""
        Create a Concept Detective game based on the following transcript.
        
        The game should:
        1. Use a creative analogy (like cookies, islands, pets, game consoles, etc.) that reflects the core idea of the transcript
        2. Have multiple levels that reflect different layers or subtopics from the material
        3. Each level should have a brief story using the analogy and 3-5 open-ended questions
        
        Format your response as a JSON object with the following structure:
        {{
          "analogy": "The creative analogy you've chosen",
          "description": "A brief description of how the analogy relates to the transcript content",
          "levels": [
            {{
              "title": "Level 1: [Level Title]",
              "story": "A brief story using the analogy that introduces the level",
              "questions": [
                {{
                  "text": "Question 1",
                  "type": "open-ended"
                }},
                // More questions...
              ]
            }},
            // More levels...
          ]
        }}
        
        Make sure the questions are thought-provoking and require the user to apply or explain key ideas from the transcript.
        
        Transcript:
        {truncated_transcript}
        """
        
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",  # Using Llama 3.3 70B model
            messages=[
                {"role": "system", "content": "You are a helpful assistant that creates educational games. You always respond with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,  # Higher temperature for more creative analogies
            max_tokens=2048,
            response_format={"type": "json_object"}  # Ensure JSON response
        )
        
        # Extract the game data from the response
        game_data = json.loads(response.choices[0].message.content)
        
        return {
            "success": True,
            "analogy": game_data.get("analogy", ""),
            "description": game_data.get("description", ""),
            "levels": game_data.get("levels", []),
            "error": None
        }
        
    except Exception as e:
        return {
            "success": False,
            "analogy": "",
            "description": "",
            "levels": [],
            "error": str(e)
        }

class ConceptDetectiveAnswer(BaseModel):
    levelIndex: int
    questionIndex: int
    answer: str

class ConceptDetectiveEvaluationRequest(BaseModel):
    transcript: str
    answers: List[ConceptDetectiveAnswer]

class ConceptDetectiveEvaluationResponse(BaseModel):
    success: bool
    scores: Dict[str, int]  # Format: "levelIndex-questionIndex": score
    feedback: Dict[str, str]  # Format: "levelIndex-questionIndex": feedback
    error: Optional[str] = None

@app.post("/api/evaluate-concept-detective", response_model=ConceptDetectiveEvaluationResponse)
async def evaluate_concept_detective(request: ConceptDetectiveEvaluationRequest):
    """
    Evaluate the user's answers for the Concept Detective game
    """
    try:
        if not request.transcript:
            raise HTTPException(status_code=400, detail="Transcript is required")
            
        if not request.answers:
            raise HTTPException(status_code=400, detail="Answers are required")
            
        # Truncate very long transcripts to prevent API limits
        max_length = 16000  # Adjust based on the model's context window
        truncated_transcript = request.transcript[:max_length]
        if len(request.transcript) > max_length:
            truncated_transcript += "\n[Transcript truncated due to length...]"
            
        # Use Groq to evaluate the answers
        if not GROQ_API_KEY or not groq_client:
            raise HTTPException(
                status_code=500, 
                detail="GROQ_API_KEY not configured"
            )
            
        # Format answers for the prompt
        formatted_answers = []
        for answer in request.answers:
            formatted_answers.append({
                "levelIndex": answer.levelIndex,
                "questionIndex": answer.questionIndex,
                "answer": answer.answer
            })
            
        prompt = f"""
        Evaluate the following answers for a Concept Detective game based on the transcript.
        
        For each answer, provide:
        1. A score from 0-4:
           - 0: Completely incorrect or misunderstanding
           - 1: Somewhat related but mostly off
           - 2: Partial understanding with missing or confused parts
           - 3: Mostly correct with minor flaws
           - 4: Fully correct, showing clear understanding
        2. Brief feedback explaining the score and what could be improved
        
        Format your response as a JSON object with the following structure:
        {{
          "scores": {{
            "levelIndex-questionIndex": score,
            // More scores...
          }},
          "feedback": {{
            "levelIndex-questionIndex": "Feedback text",
            // More feedback...
          }}
        }}
        
        Transcript:
        {truncated_transcript}
        
        Answers to evaluate:
        {json.dumps(formatted_answers, indent=2)}
        """
        
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",  # Using Llama 3.3 70B model
            messages=[
                {"role": "system", "content": "You are a helpful assistant that evaluates educational answers. You always respond with valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,  # Lower temperature for more consistent evaluation
            max_tokens=2048,
            response_format={"type": "json_object"}  # Ensure JSON response
        )
        
        # Extract the evaluation data from the response
        evaluation_data = json.loads(response.choices[0].message.content)
        
        return {
            "success": True,
            "scores": evaluation_data.get("scores", {}),
            "feedback": evaluation_data.get("feedback", {}),
            "error": None
        }
        
    except Exception as e:
        return {
            "success": False,
            "scores": {},
            "feedback": {},
            "error": str(e)
        }

class StudentProfileResponse(BaseModel):
    success: bool
    profile: Optional[Dict[str, Any]] = None
    knowledge_state: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

@app.get("/api/student/profile/{user_id}", response_model=StudentProfileResponse)
async def get_student_profile_endpoint(user_id: str):
    """
    Get a student's learning profile and knowledge state
    """
    try:
        # Get the student's profile
        profile = await get_student_profile(user_id)
        if not profile:
            return StudentProfileResponse(
                success=False,
                error="Student profile not found"
            )
        
        # Get the student's knowledge state
        knowledge_state = await get_knowledge_state(user_id)
        
        return StudentProfileResponse(
            success=True,
            profile=profile.__dict__,
            knowledge_state=knowledge_state.__dict__ if knowledge_state else None
        )
        
    except Exception as e:
        return StudentProfileResponse(
            success=False,
            error=str(e)
        )

"""
Application configuration.
Loads environment variables from .env and exposes them as typed constants.
"""
import os

from dotenv import load_dotenv

load_dotenv()

# ── API keys ─────────────────────────────────────────────────────────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

# ── FastAPI metadata ──────────────────────────────────────────────────────────
APP_TITLE: str = "Voice Command Shopping Assistant"
APP_VERSION: str = "1.0.0"
API_PREFIX: str = "/api/v1"

# ── LLM model identifiers ─────────────────────────────────────────────────────
GROQ_CHAT_MODEL: str = "openai/gpt-oss-120b"      # Best available Groq chat model
GROQ_WHISPER_MODEL: str = "whisper-large-v3"       # Groq Whisper STT
GEMINI_CHAT_MODEL: str = "gemini-2.5-flash"        # Gemini fallback (google-genai SDK)

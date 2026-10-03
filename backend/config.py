"""Paths and defaults for the offline email assistant."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "email_assistant.sqlite3"
OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "gemma3:4b"
MODEL_CHOICES = ("gemma3:4b", "qwen2.5:3b", "gemma3:1b")
WHISPER_PATH = ROOT / "models" / "whisper-small"
MAX_INPUT_CHARS = 12_000

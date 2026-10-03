import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from faster_whisper import WhisperModel
from backend.config import WHISPER_PATH

if __name__ == "__main__":
    WHISPER_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading Whisper small into {WHISPER_PATH}; this step requires internet.")
    WhisperModel("small", device="cpu", compute_type="int8", download_root=str(WHISPER_PATH))
    print("Download complete. Transcription uses this local folder only.")

"""Report local prerequisites without attempting downloads or network calls beyond localhost."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api import API

def main():
    status=API().env_status()
    print("Environment check (local services only)")
    o=status["ollama"]
    print(f"{'OK' if o['running'] else 'MISSING'} Ollama service at localhost:11434")
    print(f"{'OK' if o['model_present'] else 'MISSING'} model {o['model']}" + ("" if o['model_present'] else f" — run: ollama pull {o['model']}"))
    print(f"{'OK' if status['whisper_model_available'] else 'OPTIONAL'} local Whisper model: {Path(__file__).resolve().parents[1] / 'models' / 'whisper-small'}")
    print(f"{'OK' if status['outlook']['available'] else 'FALLBACK'} Outlook: {status['outlook']['mode']} ({status['outlook']['message']})")
    print(f"{'OK' if status['pywebview_available'] else 'MISSING'} pywebview Python package")
    print(f"{'OK' if status['webview2_available'] else 'MISSING'} Microsoft Edge WebView2 runtime")
    print(f"GPU: {status['gpu']}")
    print(f"RAM: {status['ram']}; disk: {status['disk']}")
    return 0 if o["running"] and o["model_present"] else 1

if __name__ == "__main__": raise SystemExit(main())

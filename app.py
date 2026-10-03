"""Entry point for the offline Correspond email workspace.

Starts pywebview with the native UI and binds the backend API bridge.
"""
import sys
from pathlib import Path

# Ensure project root is in sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def main():
    try:
        import webview
    except ImportError:
        print("\n[ERROR] pywebview is not installed in your Python environment.", file=sys.stderr)
        print("Please install requirements first:\n    pip install -r requirements.txt\n", file=sys.stderr)
        sys.exit(1)

    from backend.api import API
    from backend.db import Database

    ui_path = ROOT / "ui" / "index.html"
    if not ui_path.is_file():
        print(f"\n[ERROR] UI template not found at {ui_path}", file=sys.stderr)
        sys.exit(1)

    db = Database()
    api = API(db=db)

    window = webview.create_window(
        title="Correspond — Local Email Studio",
        url=str(ui_path),
        js_api=api,
        width=1200,
        height=840,
        min_size=(960, 680),
        text_select=True,
    )
    api.set_window(window)

    # Run native window event loop
    webview.start(debug=False)

if __name__ == "__main__":
    main()

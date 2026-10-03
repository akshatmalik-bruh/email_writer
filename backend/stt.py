"""Lazy, local-only faster-whisper transcription."""
import gc
from pathlib import Path
from .config import WHISPER_PATH

def _model_path():
    """Resolve either a flat CTranslate2 model folder or the HF cache snapshot."""
    root = Path(WHISPER_PATH)
    if (root / "model.bin").is_file() and (root / "config.json").is_file():
        return root

    repo = root / "models--Systran--faster-whisper-small"
    refs = repo / "refs" / "main"
    snapshots = repo / "snapshots"
    revision = refs.read_text(encoding="utf-8").strip() if refs.is_file() else ""
    candidates = ([snapshots / revision] if revision else [])
    if snapshots.is_dir():
        candidates.extend(p for p in snapshots.iterdir() if p.is_dir() and p not in candidates)
    for snapshot in candidates:
        if all((snapshot / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")):
            return snapshot
    return None

def available(): return _model_path() is not None

def transcribe(audio_path):
    path = Path(audio_path)
    if not path.is_file(): raise FileNotFoundError("The recording file could not be found.")
    model_path = _model_path()
    if model_path is None: raise RuntimeError("Hindi speech recognition model files are missing or incomplete. Re-download the local Whisper model; typing is still available.")
    model = None
    try:
        from faster_whisper import WhisperModel
        import faster_whisper.audio as fw_audio
        import av

        # PyAV 19 removed metadata_errors from av.open(), but faster-whisper
        # releases that still pass it are common. New PyAV always decodes
        # metadata as UTF-8, so drop only that obsolete keyword on v19+.
        av_version = tuple(int(part) for part in av.__version__.split(".")[:2] if part.isdigit())
        if av_version >= (19, 0) and not getattr(fw_audio.av.open, "_correspond_compat", False):
            original_open = fw_audio.av.open

            def open_compatible(*args, **kwargs):
                kwargs.pop("metadata_errors", None)
                kwargs.pop("metadata_encoding", None)
                return original_open(*args, **kwargs)

            open_compatible._correspond_compat = True
            fw_audio.av.open = open_compatible

        try:
            model = WhisperModel(str(model_path), device="cpu", compute_type="int8", local_files_only=True)
        except Exception as exc:
            raise RuntimeError(f"Whisper found its files but could not load model metadata from '{model_path}': {type(exc).__name__}: {exc}") from exc
        try:
            segments, _ = model.transcribe(str(path), language="hi", vad_filter=True, beam_size=5)
            return {"text":" ".join(s.text.strip() for s in segments).strip()}
        except Exception as exc:
            raise RuntimeError(f"Whisper loaded, but could not decode the recorded audio file: {type(exc).__name__}: {exc}") from exc
    finally:
        del model
        gc.collect()

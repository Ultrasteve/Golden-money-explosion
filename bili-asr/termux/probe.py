import os
import traceback

from funasr import AutoModel

print("soxr:", end=" ")
try:
    import soxr
    print(getattr(soxr, "__version__", "?"))
except Exception as exc:
    print("UNAVAILABLE ->", type(exc).__name__, exc)

print("numba:", end=" ")
try:
    import numba
    print(numba.__version__)
except Exception as exc:
    print("UNAVAILABLE ->", type(exc).__name__, exc)

try:
    m = AutoModel(
        model=os.path.expanduser("~/models/SenseVoiceSmall"),
        vad_model=os.path.expanduser("~/models/fsmn-vad"),
        vad_kwargs={"max_single_segment_time": 30000},
        trust_remote_code=False,
        device="cpu",
        disable_update=True,
    )
    print("MODEL_LOADED", type(m).__name__)
    print("model attrs:", [a for a in ("model", "vad_model", "frontend", "tokenizer") if hasattr(m, a)])
except Exception:
    print("=== FULL TRACEBACK ===")
    traceback.print_exc()

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


if sys.platform == "win32":
    # Workaround for occasional noisy ConnectionResetError (WinError 10054)
    # when clients disconnect during WebSocket/file upload traffic on Windows.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Allow running without installing the package
HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from foundryplan.app import main  # noqa: E402


if __name__ in {"__main__", "__mp_main__"}:
    main()

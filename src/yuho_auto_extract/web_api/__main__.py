from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m yuho_auto_extract.web_api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)

    os.environ.setdefault("YUHO_PROJECT_ROOT", str(Path.cwd().resolve()))
    try:
        import uvicorn  # type: ignore
    except ImportError:
        print("FastAPI web app requires uvicorn. Run: python -m pip install -e .")
        return 1
    uvicorn.run("yuho_auto_extract.web_api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


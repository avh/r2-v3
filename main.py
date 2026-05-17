"""R2 server entry point — parses CLI args, then launches uvicorn."""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def main():
    parser = argparse.ArgumentParser(description="R2 Personal Assistant Server")
    parser.add_argument(
        "--base", default="user", metavar="DIR",
        help="Directory for PA agent data (default: user)",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    parser.add_argument("--reload", action="store_true", default=False)
    args = parser.parse_args()

    base = Path(args.base)
    if not base.is_absolute():
        base = ROOT / base
    os.environ["R2_BASE_DIR"] = str(base)

    import uvicorn
    uvicorn.run("src.server:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()

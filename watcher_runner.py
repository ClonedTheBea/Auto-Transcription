from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Background launcher for the transcription watcher.")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--pid-file", default="")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    logs = root / "logs"
    logs.mkdir(exist_ok=True)

    stdout = (logs / "watcher.out.log").open("a", encoding="utf-8", buffering=1)
    stderr = (logs / "watcher.err.log").open("a", encoding="utf-8", buffering=1)
    sys.stdout = stdout
    sys.stderr = stderr

    print(f"{datetime.now().isoformat(timespec='seconds')} watcher_runner starting")
    pid_file = Path(args.pid_file) if args.pid_file else None
    if pid_file:
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
    try:
        import transcription_pipeline

        sys.argv = ["transcription_pipeline.py", "--config", args.config, "watch"]
        return transcription_pipeline.main()
    except BaseException:
        traceback.print_exc()
        raise
    finally:
        if pid_file:
            try:
                pid_file.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())

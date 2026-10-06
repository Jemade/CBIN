"""Print an already-downloaded ESC/POS file to an explicitly selected local CUPS queue."""

import argparse
import re
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Send a CBIN ESC/POS copy to a local Epson queue")
    parser.add_argument("file", type=Path)
    parser.add_argument("--queue", required=True, help="Installed CUPS printer queue name")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", args.queue):
        parser.error("Invalid printer queue")
    content = args.file.read_bytes()
    if len(content) > 2_000_000 or not content.startswith(b"\x1b@"):
        parser.error("Expected a CBIN ESC/POS output file")
    # No shell and no user-controlled remote printer host.
    result = subprocess.run(
        ["lp", "-d", args.queue, "-o", "raw", "--", str(args.file.resolve())],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    print(result.stdout.strip())
    print(
        "Queued locally. Confirm paper output on the printer; this is not a print acknowledgement."
    )


if __name__ == "__main__":
    main()

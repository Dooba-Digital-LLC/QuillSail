"""Print native macOS crash symbols without uploading process data or full reports."""

import json
from pathlib import Path


def main():
    folder = Path.home() / "Library/Logs/DiagnosticReports"
    reports = sorted(folder.glob("QuillSail-*.ips"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in reports[:3]:
        try:
            raw = path.read_text(encoding="utf-8")
            _, end = json.JSONDecoder().raw_decode(raw)
            report = json.loads(raw[end:].strip())
            print("Native crash:", path.name, report.get("exception"))
            index = report.get("faultingThread", 0)
            for frame in report.get("threads", [])[index].get("frames", [])[:35]:
                print(" ", frame.get("symbol", "unknown"), frame.get("symbolLocation", ""))
        except (ValueError, IndexError, OSError):
            print("Native crash report could not be summarized:", path.name)
    if not reports:
        print("No macOS desktop crash report found.")


if __name__ == "__main__":
    main()

"""Check JSON templates or one real round without modifying the input files."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agro_checker.inspection_priority_data import check_collection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("round", type=Path)
    parser.add_argument("results", type=Path)
    parser.add_argument("--window-hours", type=float, default=48)
    args = parser.parse_args()
    try:
        report = check_collection(json.loads(args.round.read_text(encoding="utf-8")),
                                  json.loads(args.results.read_text(encoding="utf-8")), args.window_hours)
    except (ValueError, OSError, TypeError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return int(bool(report["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())

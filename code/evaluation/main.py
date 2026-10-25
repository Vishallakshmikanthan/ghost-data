"""Ghost Data Still Exists - Evaluation CLI.

Usage:
    python code/evaluation/main.py [--predictions PREDICTIONS_CSV] [--reference REFERENCE_CSV]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure repository root is on sys.path ahead of standard library 'code'
repo_root = Path(__file__).resolve().parent.parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from code.evaluation.evaluator import SampleEvaluator


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate deletion-audit predictions against reference answers."
    )
    parser.add_argument(
        "--predictions",
        "-p",
        type=str,
        default=None,
        help="Path to prediction CSV file (default: dataset/sample_output.csv if not provided)",
    )
    parser.add_argument(
        "--reference",
        "-r",
        type=str,
        default=None,
        help="Path to reference CSV file (default: dataset/sample_output.csv)",
    )
    parser.add_argument(
        "--report-file",
        type=str,
        default=None,
        help="Optional path to save evaluation summary text",
    )
    args = parser.parse_args()

    sample_output = repo_root / "dataset" / "sample_output.csv"

    pred_path = Path(args.predictions) if args.predictions else sample_output
    ref_path = Path(args.reference) if args.reference else sample_output

    if not pred_path.exists():
        print(f"Error: Predictions file not found: {pred_path}", file=sys.stderr)
        return 1

    if not ref_path.exists():
        print(f"Error: Reference file not found: {ref_path}", file=sys.stderr)
        return 1

    evaluator = SampleEvaluator(reference_path=ref_path)
    report = evaluator.evaluate_file(pred_path, reference_path=ref_path)

    summary_text = report.format_console_summary()
    print(summary_text)

    if args.report_file:
        out_p = Path(args.report_file)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(summary_text, encoding="utf-8")
        print(f"Saved evaluation report to {out_p}")

    return 0 if report.contract_result.is_structurally_valid else 1


if __name__ == "__main__":
    sys.exit(main())

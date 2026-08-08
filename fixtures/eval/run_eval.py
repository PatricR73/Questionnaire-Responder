"""Reproducible eval runner: runs the real pipeline CLI against the 20-question CAIQ
fixture and scores the result against fixtures/eval/questions.json.

Deliberately shells out to `python -m src.pipeline answer` rather than calling
generate_answer/AnthropicAnswerer directly — this project's headline numbers were
first produced by one-off scripts that bypassed the CLI, which meant the thing being
measured wasn't quite the thing anyone would actually run. This script IS the
documented command; the CLI path and the eval path are the same code.

Usage (from the project root, with ANTHROPIC_API_KEY exported):

    python fixtures/eval/run_eval.py

Prints, per question: source_id, expected_label, actual status/confidence/polarity,
the full drafted answer text, and a structural match flag. Then a summary, with the
6 NOT_FOUND questions called out on their own line — a NOT_FOUND question that comes
back ANSWERED is flagged as a regression regardless of the rest of the score, per
this project's tuning-log convention.

This script does NOT assign usable/needs-editing/wrong — that's a hand-scored
judgment call by design (see LABELING_GUIDE.md's anti-mirror methodology), not
something to automate. It gives you the structural match plus every full answer to
read, which is the input hand-scoring needs.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
QUESTIONNAIRE = REPO_ROOT / "fixtures" / "eval" / "questionnaire_eval.xlsx"
QUESTIONS = REPO_ROOT / "fixtures" / "eval" / "questions.json"
OUTPUT = REPO_ROOT / "out" / "eval_run.xlsx"

sys.path.insert(0, str(REPO_ROOT))

from src.store.db import connect  # noqa: E402

EXPECTED_STATUS = {
    "ANSWERED_AFFIRMS": "ANSWERED",
    "ANSWERED_DENIES": "ANSWERED",
    "ANSWERED_PARTIAL": "ANSWERED",
    "AMBIGUOUS_EVIDENCE": "ANSWERED",
    "NOT_FOUND": "NOT_FOUND",
}
NOT_FOUND_LABEL = "NOT_FOUND"


def run_pipeline() -> None:
    cmd = [
        sys.executable, "-m", "src.pipeline", "answer",
        "--questionnaire", str(QUESTIONNAIRE),
        "--output", str(OUTPUT),
        "--limit", "0",
    ]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise SystemExit(f"pipeline run failed (exit {result.returncode})")


def main():
    run_pipeline()

    source_id_by_row = {}
    import openpyxl
    ws = openpyxl.load_workbook(QUESTIONNAIRE).active
    for row in range(2, ws.max_row + 1):
        source_id = ws.cell(row=row, column=1).value
        if source_id:
            source_id_by_row[row] = source_id

    questions = {q["source_id"]: q for q in json.load(open(QUESTIONS))["questions"]}

    conn = connect(REPO_ROOT / "out" / "store.db")
    run_id = conn.execute(
        "SELECT id FROM questionnaire_runs WHERE output_path = ? ORDER BY id DESC LIMIT 1",
        (str(OUTPUT),),
    ).fetchone()["id"]

    rows = conn.execute(
        """
        SELECT a.row_index, a.final_confidence, a.polarity, a.drafted_answer
        FROM answers a WHERE a.run_id = ? ORDER BY a.row_index
        """,
        (run_id,),
    ).fetchall()

    print()
    print("=" * 100)
    not_found_regressions = []
    matches = 0
    for row in rows:
        source_id = source_id_by_row.get(row["row_index"])
        q = questions.get(source_id)
        if q is None:
            continue
        expected_label = q["expected_label"]
        status = {"high": "ANSWERED", "low": "ANSWERED", "none": "NOT_FOUND", "error": "ERROR"}.get(row["final_confidence"], "ERROR")
        expected_status = EXPECTED_STATUS[expected_label]
        is_match = status == expected_status
        matches += is_match

        if expected_label == NOT_FOUND_LABEL and status == "ANSWERED":
            not_found_regressions.append(source_id)

        print(f"{source_id:10s} expected={expected_label:20s} status={status:10s} confidence={row['final_confidence']:6s} polarity={str(row['polarity']):8s} match={'yes' if is_match else 'NO'}")
        print(f"  answer: {row['drafted_answer'] or '(empty)'}")
        print()

    print("=" * 100)
    print("NOT_FOUND questions (must all stay NOT_FOUND):")
    for row in rows:
        source_id = source_id_by_row.get(row["row_index"])
        q = questions.get(source_id)
        if q and q["expected_label"] == NOT_FOUND_LABEL:
            status = {"high": "ANSWERED", "low": "ANSWERED", "none": "NOT_FOUND", "error": "ERROR"}.get(row["final_confidence"], "ERROR")
            print(f"  {source_id}: {status}")

    print()
    print(f"Structural match: {matches}/{len(rows)}")
    if not_found_regressions:
        print(f"!!! REGRESSION: these NOT_FOUND questions came back ANSWERED: {not_found_regressions}")
    else:
        print("No NOT_FOUND regressions.")
    print()
    print("Structural match is NOT the usable/needs-editing/wrong score — read the full")
    print("answers above and hand-score them, per this project's methodology.")


if __name__ == "__main__":
    main()

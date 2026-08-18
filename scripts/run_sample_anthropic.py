"""Produce the real-API sample output for docs/sample/ (pack 3, C3).

The stub sample (docs/sample/filled_stub.xlsx) demonstrates the plumbing, not the
product: stub answers are deterministic markers. A buyer needs to read real drafted
answers — citation quality, the hedged flagged-low row, the honest NOT_FOUND — to
judge the tool, and the fixtures are synthetic, so nothing about running the real
provider over them is confidential.

This script runs the real Anthropic provider over the committed 24-question eval
workbook and produces the artifacts this repo publishes:

    docs/sample/filled_anthropic.xlsx        the pipeline's filled workbook
    docs/sample/filled_anthropic.jsonl       the per-row sidecar log
    docs/sample/filled_anthropic_reviewed.xlsx  the reviewed export

Then COMMIT them (they are the deliverable). Requires a valid ANTHROPIC_API_KEY
and a few cents of API spend; rerun it whenever the numbers in EVAL.md change.

The reviewed export needs a review trail, so after the run the script records
human review events on a small set of representative rows — exactly as the review
UI would — approving the grounded rows, editing the flagged-low row (whose answer
the evidence only partially supports), and rejecting one abstention. This is
sample curation, not a measurement; the eval harness must never be pointed at
docs/sample/.

Usage: python scripts/run_sample_anthropic.py
"""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
QUESTIONNAIRE = REPO_ROOT / "fixtures" / "eval" / "questionnaire_eval.xlsx"
SAMPLE_DIR = REPO_ROOT / "docs" / "sample"
OUTPUT = SAMPLE_DIR / "filled_anthropic.xlsx"

# (row_index, human_action) — mirror the demo curation so the reviewed export
# demonstrates approve / edit / reject. See docs/sample/README.md for what each
# row shows.
REVIEW_EVENTS = {
    2: "approved",
    3: "approved",
    9: "approved",
    23: "approved",
    22: "edited",
    14: "rejected",
}


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set — export it first (see README).")

    print(f"1/3 running the real provider over {QUESTIONNAIRE.name} (24 rows, a few cents) ...")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "src.pipeline",
            "answer",
            "--questionnaire",
            str(QUESTIONNAIRE),
            "--output",
            str(OUTPUT),
            "--limit",
            "0",
            "--provider",
            "anthropic",
        ],
        cwd=REPO_ROOT,
        check=True,
    )

    print("2/3 recording a small review trail on representative rows ...")
    conn = sqlite3.connect(REPO_ROOT / "out" / "store.db")
    run_id = conn.execute(
        "SELECT id FROM questionnaire_runs WHERE output_path = ? ORDER BY id DESC LIMIT 1",
        (str(OUTPUT),),
    ).fetchone()[0]
    now = datetime.now(UTC).isoformat()
    for row_index, action in REVIEW_EVENTS.items():
        conn.execute(
            "INSERT INTO audit_log (run_id, row_index, sources_consulted, confidence, provider, human_action, timestamp) "
            "VALUES (?, ?, ?, 'review', 'review-ui', ?, ?)",
            (run_id, row_index, json.dumps({"review_event": True}), action, now),
        )
    conn.commit()

    print("3/3 exporting the reviewed workbook ...")

    from src.questionnaire.parse_xlsx import detect_columns  # noqa: F401
    from src.review_ui import export_reviewed_workbook

    rows = conn.execute(
        """
        SELECT a.row_index, a.final_confidence, a.drafted_answer, a.reviewed_answer,
               a.vocab_selection, l.human_action
        FROM answers a
        LEFT JOIN audit_log l ON l.id = (
            SELECT MAX(la.id) FROM audit_log la
            WHERE la.run_id = a.run_id AND la.row_index = a.row_index
              AND (la.confidence = 'review' OR la.human_action IS NOT NULL)
        )
        WHERE a.run_id = ? ORDER BY a.row_index
        """,
        (run_id,),
    ).fetchall()
    # export_reviewed_workbook expects row dicts with the keys its loop reads.
    row_dicts = [dict(r) for r in rows]
    source_path = conn.execute("SELECT source_path FROM questionnaire_runs WHERE id = ?", (run_id,)).fetchone()[0]
    export_path = export_reviewed_workbook(conn, run_id, source_path, str(OUTPUT), row_dicts)
    conn.close()

    print("done. Commit these artifacts to docs/sample/:")
    print(f"  {OUTPUT.name}")
    print(f"  {OUTPUT.with_suffix('.jsonl').name}")
    print(f"  {export_path.name}")


if __name__ == "__main__":
    main()

"""Build the committed demo store: ingest + stub run + curated review trail.

What this is (pack 3, C1/C2): a small, fully synthetic store committed to the repo
so 'qresp demo' and the hosted Streamlit Cloud demo work with no API key, no ingest
step, and no model download. This script is the reproducible way it was built:

1. Ingest fixtures/evidence/ (3 synthetic policy documents, 14 chunks) into
   demo_store/ exactly as the real pipeline would.
2. Run the real CLI with --provider stub over the committed 24-question eval
   questionnaire, so every row's retrieval, confidence state, and audit entry are
   genuine pipeline output.
3. CURATE the five answered rows into a frozen sample. This is the one step that
   touches data the pipeline did not produce: the stub provider emits placeholder
   answers ('[STUB] Per <file> — <chunk>'), which is correct for exercising the
   plumbing and useless for showing a prospect the product. Each curated draft is
   written by hand from the VERBATIM cited chunk (shown in the review UI), so the
   demonstration is grounded in real synthetic evidence; row 22 is deliberately
   marked low-confidence because the evidence covers key management but never
   mentions an HSM; row 9 is a documented negative (customers cannot manage their
   own keys). A human review trail (approved/edited) is recorded exactly as the
   review UI would.

   The store is a frozen SAMPLE, not a measured run: the eval harness must never
   be pointed at demo_store/ (drafted_answer on the five curated rows was not
   produced by a generation model). 'qresp demo' copies the store to out/ and adds
   a fresh, uncurated stub run on top, so a local reviewer always sees pipeline-
   produced output.

Usage: python scripts/build_demo_store.py
"""

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STORE = REPO_ROOT / "demo_store"
EVIDENCE = REPO_ROOT / "fixtures" / "evidence"
QUESTIONNAIRE = REPO_ROOT / "fixtures" / "eval" / "questionnaire_eval.xlsx"

# Curated drafts, grounded in the verbatim chunks the stub run cited (see the
# review UI / store.db for the exact chunk text). Keyed by sheet row index.
# (answer, final_confidence, self_confidence, polarity, cited_sentence, human_action)
CURATION = {
    2: (
        "Yes. All network traffic between clients and production services is encrypted in transit "
        "using TLS 1.2 or higher, and internal service-to-service traffic within the production VPC "
        "is additionally encrypted using mutual TLS.",
        "high", "high", "affirms",
        "All network traffic between clients and production services is encrypted in transit using TLS 1.2 or higher.",
        "approved",
    ),
    3: (
        "Yes. Production databases are backed up hourly with 30-day retention, and backups are stored "
        "in a separate cloud region from the primary production environment.",
        "high", "high", "affirms",
        "Production databases are backed up hourly with 30-day retention.",
        "approved",
    ),
    9: (
        "No. The access control policy states that customers do not have the ability to manage their "
        "own encryption keys; all key management is performed internally by the security team.",
        "high", "high", "denies",
        "Customers do not have the ability to manage their own encryption keys; all key management is performed internally by the security team.",
        "approved",
    ),
    22: (
        "Encryption keys are managed via a dedicated key management service and rotated annually. "
        "The provided evidence does not state whether the key management service is backed by a "
        "hardware security module (HSM), so this row should be confirmed with the security team before sending.",
        "low", "high", "partial",
        "Encryption keys are managed via a dedicated key management service and rotated annually.",
        "edited",
    ),
    23: (
        "Yes. Backups are stored off-site in a separate cloud region from the primary production "
        "environment, with hourly backups and 30-day retention.",
        "high", "high", "affirms",
        "Backups are stored in a separate cloud region from the primary production environment.",
        "approved",
    ),
}


def main() -> None:
    if STORE.exists():
        shutil.rmtree(STORE)
    env = dict(__import__("os").environ)
    env["QRESP_DATA_DIR"] = str(STORE)

    print("1/3 ingesting evidence ...")
    subprocess.run(
        [sys.executable, "-m", "src.pipeline", "ingest", "--evidence-dir", str(EVIDENCE)],
        cwd=REPO_ROOT, env=env, check=True,
    )
    print("2/3 running the stub questionnaire run ...")
    output = REPO_ROOT / "out" / "demo_eval_filled.xlsx"
    subprocess.run(
        [sys.executable, "-m", "src.pipeline", "answer",
         "--questionnaire", str(QUESTIONNAIRE), "--output", str(output),
         "--limit", "0", "--provider", "stub"],
        cwd=REPO_ROOT, env=env, check=True,
    )
    print("3/3 curating the five answered rows into a frozen sample ...")
    conn = sqlite3.connect(STORE / "store.db")
    run_id = conn.execute("SELECT MAX(id) FROM questionnaire_runs").fetchone()[0]
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    for row_index, (answer, final, self_conf, polarity, cited_sentence, action) in CURATION.items():
        conn.execute(
            "UPDATE answers SET drafted_answer=?, reviewed_answer=?, final_confidence=?, "
            "self_confidence=?, polarity=?, cited_sentences=? WHERE run_id=? AND row_index=?",
            (answer, answer if action == "approved" else answer, final, self_conf, polarity,
             json.dumps([cited_sentence]), run_id, row_index),
        )
        # Review event exactly as the review UI records it (confidence='review').
        conn.execute(
            "INSERT INTO audit_log (run_id, row_index, sources_consulted, confidence, provider, human_action, timestamp) "
            "VALUES (?, ?, ?, 'review', 'review-ui', ?, ?)",
            (run_id, row_index, json.dumps({"review_event": True}), action, now),
        )
    conn.commit()
    n_answers = conn.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
    n_reviewed = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE confidence='review'").fetchone()[0]
    conn.close()
    print(f"done: {n_answers} rows, {n_reviewed} curated review events in {STORE}")


if __name__ == "__main__":
    main()

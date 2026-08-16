"""Reproducible eval runner: runs the real pipeline CLI against the 20-question CAIQ
fixture and scores the result against fixtures/eval/questions.json.

Deliberately shells out to `python -m src.pipeline answer` rather than calling
generate_answer/AnthropicAnswerer directly — this project's headline numbers were
first produced by one-off scripts that bypassed the CLI, which meant the thing being
measured wasn't quite the thing anyone would actually run. This script IS the
documented command; the CLI path and the eval path are the same code.

Usage (from the project root, with ANTHROPIC_API_KEY exported):

    python fixtures/eval/run_eval.py
    python fixtures/eval/run_eval.py --repeats 3

Prints, per question: source_id, expected_label, actual status/confidence/polarity,
the full drafted answer text, and a structural match flag. Then a summary, with the
6 NOT_FOUND questions called out on their own line — a NOT_FOUND question that comes
back ANSWERED is flagged as a regression regardless of the rest of the score, per
this project's tuning-log convention.

--repeats N runs the whole pipeline N times into distinct output paths
(out/eval_run_1.xlsx .. out/eval_run_N.xlsx; plain out/eval_run.xlsx for N=1) so a
reported delta between runs is never a single stochastic sample again: every question
gets a per-question stability figure (how many of the N runs produced the same
status), the summary prints min/mean/max structural match across runs, and the 95%
Wilson confidence interval for the structural-match proportion at the current eval-set
size — with n=20, a one-question delta is squarely inside the interval, which is the
whole point of printing it.

This script does NOT assign usable/needs-editing/wrong — that's a hand-scored
judgment call by design (see LABELING_GUIDE.md's anti-mirror methodology), not
something to automate. It gives you the structural match plus every full answer to
read, which is the input hand-scoring needs.
"""

import argparse
import json
import statistics
import subprocess
import sys
from collections import Counter
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

# 1.959963984540054 = the 97.5th percentile of the standard normal (z for 95%).
_WILSON_Z = 1.959963984540054


def run_pipeline(output_path: Path) -> None:
    cmd = [
        sys.executable, "-m", "src.pipeline", "answer",
        "--questionnaire", str(QUESTIONNAIRE),
        "--output", str(output_path),
        "--limit", "0",
    ]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise SystemExit(f"pipeline run failed (exit {result.returncode})")


def load_run_rows(output_path: Path) -> list[dict]:
    """Pull one run's scored rows out of the store, joined to the fixture labels."""
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
        (str(output_path),),
    ).fetchone()["id"]

    rows = conn.execute(
        """
        SELECT a.row_index, a.final_confidence, a.polarity, a.drafted_answer
        FROM answers a WHERE a.run_id = ? ORDER BY a.row_index
        """,
        (run_id,),
    ).fetchall()

    scored = []
    for row in rows:
        source_id = source_id_by_row.get(row["row_index"])
        q = questions.get(source_id)
        if q is None:
            continue
        expected_label = q["expected_label"]
        status = {"high": "ANSWERED", "low": "ANSWERED", "none": "NOT_FOUND", "error": "ERROR"}.get(
            row["final_confidence"], "ERROR"
        )
        scored.append(
            {
                "source_id": source_id,
                "row_index": row["row_index"],
                "expected_label": expected_label,
                "status": status,
                "confidence": row["final_confidence"],
                "polarity": row["polarity"],
                "answer": row["drafted_answer"],
            }
        )
    return scored


def structural_match(expected_label: str, status: str, polarity: str | None) -> bool:
    """Status/polarity vs. expected label. Polarity is not yet compared (added in the
    polarity-scoring pass); status alone decides the structural match for now."""
    return status == EXPECTED_STATUS[expected_label]


def score_run(rows: list[dict]) -> tuple[int, list[str]]:
    matches = sum(1 for r in rows if structural_match(r["expected_label"], r["status"], r["polarity"]))
    not_found_regressions = [
        r["source_id"] for r in rows if r["expected_label"] == NOT_FOUND_LABEL and r["status"] == "ANSWERED"
    ]
    return matches, not_found_regressions


def wilson_interval(k: int, n: int) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion k/n — the honest uncertainty
    band around a structural-match count at this eval-set size. Chosen over the normal
    approximation because k/n near 0 or 1 (and small n) makes the normal interval
    degenerate; Wilson stays inside [0, 1] and is accurate even at n=20."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    z = _WILSON_Z
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / denom
    return (max(0.0, center - half), min(1.0, center + half))


def print_run_detail(rows: list[dict], not_found_regressions: list[str]) -> None:
    print()
    print("=" * 100)
    for r in rows:
        is_match = structural_match(r["expected_label"], r["status"], r["polarity"])
        print(
            f"{r['source_id']:10s} expected={r['expected_label']:20s} status={r['status']:10s} "
            f"confidence={r['confidence']:6s} polarity={str(r['polarity']):8s} "
            f"match={'yes' if is_match else 'NO'}"
        )
        print(f"  answer: {r['answer'] or '(empty)'}")
        print()

    print("=" * 100)
    print("NOT_FOUND questions (must all stay NOT_FOUND):")
    for r in rows:
        if r["expected_label"] == NOT_FOUND_LABEL:
            print(f"  {r['source_id']}: {r['status']}")
    print()
    if not_found_regressions:
        print(f"!!! REGRESSION: these NOT_FOUND questions came back ANSWERED: {not_found_regressions}")
    else:
        print("No NOT_FOUND regressions.")


def main():
    # stdout is block-buffered when redirected to a file, so the parent's progress
    # lines interleave confusingly with the (OS-unbuffered) pipeline subprocess
    # output; force line buffering so a logged run reads in order.
    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description=__doc__.split("Usage")[0])
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Run the pipeline N times into distinct output paths and report per-question "
        "stability plus min/mean/max structural match across runs (default 1).",
    )
    args = parser.parse_args()
    repeats = args.repeats
    if repeats < 1:
        raise SystemExit("--repeats must be >= 1")

    if repeats == 1:
        outputs = [OUTPUT]
    else:
        outputs = [OUTPUT.with_stem(f"{OUTPUT.stem}_{i}") for i in range(1, repeats + 1)]

    per_run_rows: list[list[dict]] = []
    per_run_matches: list[int] = []
    per_run_regressions: list[list[str]] = []

    for i, output_path in enumerate(outputs, start=1):
        print(f"\n=== Repeat {i}/{repeats} (output: {output_path.name}) ===")
        run_pipeline(output_path)
        rows = load_run_rows(output_path)
        matches, not_found_regressions = score_run(rows)
        per_run_rows.append(rows)
        per_run_matches.append(matches)
        per_run_regressions.append(not_found_regressions)
        print_run_detail(rows, not_found_regressions)

    n_questions = len(per_run_rows[0])

    if repeats > 1:
        print()
        print("=" * 100)
        print(f"Per-question stability across {repeats} runs (modal status / how many runs produced it):")
        by_source: dict[str, list[str]] = {}
        for rows in per_run_rows:
            for r in rows:
                by_source.setdefault(r["source_id"], []).append(r["status"])
        for source_id, statuses in by_source.items():
            counts = Counter(statuses)
            modal, modal_count = counts.most_common(1)[0]
            unstable = f"  <- statuses: {statuses}" if modal_count < len(statuses) else ""
            print(f"  {source_id:10s} {modal:10s} {modal_count}/{len(statuses)}{unstable}")

    print()
    print("=" * 100)
    print(f"Structural match per run: {per_run_matches}")
    mean_matches = statistics.mean(per_run_matches)
    print(
        f"min/mean/max across {repeats} run(s): {min(per_run_matches)} / {mean_matches:.1f} / "
        f"{max(per_run_matches)} out of {n_questions}"
    )

    # Wilson CI for the structural-match proportion at the current eval-set size. Uses
    # the mean match count across runs so a multi-run comparison has a single honest
    # band; at n=20 a one-question delta (e.g. 12/20 vs 13/20) sits entirely inside
    # it — which is exactly why the interval is printed: no delta under ~5 points at
    # this n is distinguishable from sampling noise.
    mean_k = round(mean_matches)
    ci_lo, ci_hi = wilson_interval(mean_k, n_questions)
    print(f"95% Wilson CI for structural-match proportion (n={n_questions}, mean across {repeats} run(s)): "
          f"[{ci_lo:.2f}, {ci_hi:.2f}]")

    if repeats == 1:
        print()
        print("Structural match is NOT the usable/needs-editing/wrong score — read the full")
        print("answers above and hand-score them, per this project's methodology.")


if __name__ == "__main__":
    main()

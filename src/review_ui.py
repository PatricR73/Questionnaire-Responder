"""Read-and-review UI for a completed questionnaire run.

Streamlit app over the existing SQLite tables — no Anthropic API calls, no retrieval,
no regeneration. Run with:

    streamlit run src/review_ui.py

Reads: questionnaire_runs, answers, audit_log, chunks (joined via cited_chunk_ids).
Writes: audit_log.human_action + audit_log.timestamp on every action; answers.reviewed_answer
only on "edited". drafted_answer is never modified — the eval harness scores against
it, and overwriting it would make past baselines unreproducible.
"""

import json
import sys
from pathlib import Path

# `streamlit run` execs this file directly and only adds this file's own directory
# (src/) to sys.path, not the project root — so `from src.store import db` below
# fails with ModuleNotFoundError unless the project root is added first. Must run
# before the src.* imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
import streamlit as st

from src.questionnaire.parse_xlsx import detect_columns
from src.questionnaire.write_xlsx import write_answer
from src.store import db

BADGES = {
    "high": ("🟢", "HIGH"),
    "low": ("🟡", "LOW"),
    "none": ("⚪", "NOT_FOUND"),
    "error": ("🔴", "ERROR"),
}

st.set_page_config(page_title="Questionnaire review", layout="wide")


def get_conn():
    """A fresh connection every call, deliberately not cached across Streamlit
    reruns (e.g. via @st.cache_resource): reruns can execute on different threads,
    and sqlite3 connections can only ever be used on the thread that created them.
    Caching one hands a connection created on thread A to a rerun executing on
    thread B, which raises sqlite3.ProgrammingError the first time it's used there —
    check_same_thread=False would silence that without making shared access actually
    safe, so the real fix is to never share a connection across threads at all."""
    return db.connect()


def load_chunk(conn, embedding_id):
    row = conn.execute(
        "SELECT source_filename, heading_path, text FROM chunks WHERE embedding_id = ?",
        (embedding_id,),
    ).fetchone()
    return row


def render_row(conn, run_id, row):
    emoji, label = BADGES.get(row["final_confidence"], ("❔", row["final_confidence"] or "UNKNOWN"))
    reviewed = bool(row["human_action"])

    header = f"{emoji} **{label}** — Row {row['row_index']}"
    if reviewed:
        header += f"  ·  ✓ {row['human_action']} ({row['timestamp']})"
    st.markdown(header)
    st.write(f"**Q:** {row['question_text']}")

    left, right = st.columns(2)
    with left:
        st.markdown("**Drafted answer**")
        st.write(row["drafted_answer"] or "*(none — no supporting evidence found)*")
        if row["reviewed_answer"]:
            st.markdown("**Human-edited answer**")
            st.write(row["reviewed_answer"])
    with right:
        st.markdown("**Cited evidence**")
        cited_ids = json.loads(row["cited_chunk_ids"]) if row["cited_chunk_ids"] else []
        if not cited_ids:
            st.write("*(none cited)*")
        for embedding_id in cited_ids:
            chunk = load_chunk(conn, embedding_id)
            if chunk is None:
                continue
            st.caption(f"{chunk['source_filename']} — {chunk['heading_path'] or '(no heading)'}")
            st.text(chunk["text"])

    row_index = row["row_index"]
    approve_col, edit_col, reject_col = st.columns(3)
    if approve_col.button("Approve", key=f"approve_{row_index}"):
        db.record_human_review(conn, run_id, row_index, "approved")
        st.rerun()
    if reject_col.button("Reject", key=f"reject_{row_index}"):
        db.record_human_review(conn, run_id, row_index, "rejected")
        st.rerun()

    edit_key = f"editing_{row_index}"
    if edit_col.button("Edit", key=f"edit_{row_index}"):
        st.session_state[edit_key] = True
    if st.session_state.get(edit_key):
        draft = row["reviewed_answer"] or row["drafted_answer"] or ""
        edited_text = st.text_area("Edited answer", value=draft, key=f"edit_text_{row_index}")
        if st.button("Save edit", key=f"save_edit_{row_index}"):
            db.record_human_review(conn, run_id, row_index, "edited", reviewed_answer=edited_text)
            st.session_state[edit_key] = False
            st.rerun()

    st.divider()


def _reviewed_export_path(output_path: str) -> Path:
    p = Path(output_path)
    return p.with_stem(p.stem + "_reviewed")


def export_reviewed_workbook(conn, run_id: int, source_path: str, output_path: str, rows) -> Path:
    """Writes a new workbook containing the *reviewed* result, never the pipeline's
    original output file. Reuses write_xlsx.write_answer exactly as the pipeline does
    (loads the pristine source questionnaire fresh via detect_columns + openpyxl, so
    merged cells/spacer rows/formatting stay untouched the same way) — no second
    writer, no new write logic. The only per-row decision made here, not in
    write_xlsx.py, is which text counts as the answer for a given human_action; that
    didn't need a new write_answer parameter since it already takes answer_text.
    """
    workbook = openpyxl.load_workbook(source_path)
    ws = workbook.active
    column_map = detect_columns(ws)

    counts = {"approved": 0, "edited": 0, "rejected": 0}
    for row in rows:
        action = row["human_action"]
        counts[action] += 1
        if action == "rejected":
            # Same NOT_FOUND marker convention as everywhere else — a rejected
            # answer must not ship, reviewed or not.
            write_answer(ws, row["row_index"], column_map, "", None, "none")
        elif action == "edited":
            write_answer(ws, row["row_index"], column_map, row["reviewed_answer"] or "", row["vocab_selection"], row["final_confidence"])
        else:  # approved
            write_answer(ws, row["row_index"], column_map, row["drafted_answer"] or "", row["vocab_selection"], row["final_confidence"])

    export_path = _reviewed_export_path(output_path)
    export_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(export_path)

    db.record_export(conn, run_id, str(export_path), counts)
    return export_path


def main():
    conn = get_conn()

    runs = conn.execute(
        "SELECT id, source_path, output_path, created_at, run_config FROM questionnaire_runs ORDER BY created_at DESC"
    ).fetchall()
    if not runs:
        st.info("No questionnaire runs found. Run `python -m src.pipeline answer ...` first.")
        return

    run_options = {f"{r['source_path']}  —  {r['created_at']}": r["id"] for r in runs}
    selected = st.sidebar.selectbox("Run", list(run_options.keys()))
    run_id = run_options[selected]
    run_row = next(r for r in runs if r["id"] == run_id)

    # The configuration that produced this run (recorded since P17) — model,
    # thresholds, fusion constants, chunk bounds, git revision — so a reviewer can
    # see what a given run was measured against before trusting its answers.
    with st.sidebar.expander("Run configuration"):
        try:
            cfg = json.loads(run_row["run_config"]) if run_row["run_config"] else {}
        except (TypeError, ValueError):
            cfg = {}
        if cfg:
            for key, value in cfg.items():
                st.write(f"**{key}:** {value}")
        else:
            st.write("*(not recorded — pre-P17 run)*")

    rows = conn.execute(
        """
        SELECT a.row_index, a.question_text, a.drafted_answer, a.reviewed_answer,
               a.vocab_selection, a.final_confidence, a.cited_chunk_ids,
               l.human_action, l.timestamp
        FROM answers a
        LEFT JOIN audit_log l ON l.run_id = a.run_id AND l.row_index = a.row_index
        WHERE a.run_id = ?
        ORDER BY a.row_index
        """,
        (run_id,),
    ).fetchall()

    total = len(rows)
    reviewed_count = sum(1 for r in rows if r["human_action"])

    all_reviewed = total > 0 and reviewed_count == total
    export_label = "Export reviewed workbook" if all_reviewed else f"Export reviewed workbook ({reviewed_count}/{total} reviewed)"
    if st.sidebar.button(export_label, disabled=not all_reviewed):
        export_path = export_reviewed_workbook(conn, run_id, run_row["source_path"], run_row["output_path"], rows)
        st.sidebar.success(f"Exported to {export_path}")

    filter_choice = st.sidebar.selectbox(
        "Filter", ["All", "High", "Low", "Not found", "Error", "Unreviewed"]
    )

    st.progress(reviewed_count / total if total else 0.0)
    st.write(f"**{reviewed_count} / {total} rows reviewed**")

    filter_map = {"High": "high", "Low": "low", "Not found": "none", "Error": "error"}
    if filter_choice == "Unreviewed":
        rows = [r for r in rows if not r["human_action"]]
    elif filter_choice in filter_map:
        rows = [r for r in rows if r["final_confidence"] == filter_map[filter_choice]]

    for row in rows:
        render_row(conn, run_id, row)


if __name__ == "__main__":
    main()

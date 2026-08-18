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
import math
import os
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

# Polarity badge (P28): the model's affirmed/denied/partial verdict on a row was
# stored but never displayed — the reviewer saw the answer without the claim it
# makes, which is exactly what polarity exists to surface.
POLARITY_BADGES = {
    "affirms": ("✅", "AFFIRMS"),
    "denies": ("🚫", "DENIES"),
    "partial": ("🧩", "PARTIAL"),
}

# 50 rows per page (P27): a 400-row run used to re-render the entire page on every
# button click; the filter is applied before pagination so the page counts and the
# filtered totals stay meaningful.
PAGE_SIZE = 50

# Read-only mode (pack 3, C2): QRESP_REVIEW_READ_ONLY=1 freezes the review
# screen over a committed store for the hosted demo — no approve/edit/reject/undo,
# no export. The store itself is not writable (Streamlit Cloud runs against the
# committed demo_store/), so this is belt-and-braces: the UI must also refuse to
# offer actions it cannot persist.
READ_ONLY = os.environ.get("QRESP_REVIEW_READ_ONLY", "").strip().lower() in ("1", "true", "yes", "on")

st.set_page_config(page_title="Questionnaire review", layout="wide")

# C11: the review UI serves a database of internal policy text; binding it to
# anything but localhost should be a loud, explicit decision. Streamlit's default
# is localhost, so this only fires when someone widened it (e.g. for a LAN
# review session or the Docker demo) — and QRESP_ALLOW_REMOTE_UI is the
# acknowledged override. A warning banner, not a hard block: there are
# legitimate uses (a container port mapping, a team LAN), but they must not be
# silent.
try:
    _bind_address = st.config.get_option("server.address")
except Exception:  # noqa: BLE001 — config option may not exist in older streamlit
    _bind_address = None
if (
    _bind_address
    and _bind_address not in ("localhost", "127.0.0.1", "::1")
    and not os.environ.get("QRESP_ALLOW_REMOTE_UI")
):
    st.warning(
        f"This review screen is bound to {_bind_address} — it serves internal policy text. "
        "Bind to localhost (streamlit run src/review_ui.py --server.address=127.0.0.1) or set "
        "QRESP_ALLOW_REMOTE_UI=1 to acknowledge the exposure. See docs/SECURITY-POSTURE.md."
    )


def get_conn():
    """A fresh connection every call, deliberately not cached across Streamlit
    reruns (e.g. via @st.cache_resource): reruns can execute on different threads,
    and sqlite3 connections can only ever be used on the thread that created them.
    Caching one hands a connection created on thread A to a rerun executing on
    thread B, which raises sqlite3.ProgrammingError the first time it's used there —
    check_same_thread=False would silence that without making shared access actually
    safe, so the real fix is to never share a connection across threads at all."""
    return db.connect()


def load_chunks(conn, embedding_ids: list[str]) -> dict:
    """All cited chunks in ONE query (P27): the render loop used to call load_chunk
    once per cited chunk per row, so a 400-row run issued on the order of two
    thousand queries on every single rerun."""
    if not embedding_ids:
        return {}
    placeholders = ",".join("?" * len(embedding_ids))
    rows = conn.execute(
        f"SELECT embedding_id, source_filename, heading_path, text FROM chunks WHERE embedding_id IN ({placeholders})",
        embedding_ids,
    ).fetchall()
    return {r["embedding_id"]: r for r in rows}


def _highlight_cited(text: str, cited_sentences: list[str]) -> str:
    """Bold every cited sentence that appears in a chunk's text, for markdown
    display — the reviewer's eye goes straight to the supporting line instead of
    reading a full passage."""
    display = text
    for sentence in sorted(cited_sentences, key=len, reverse=True):
        if sentence in display:
            display = display.replace(sentence, f"**{sentence}**")
    return display


def render_row(conn, run_id, row, chunks_by_id: dict):
    emoji, label = BADGES.get(row["final_confidence"], ("❔", row["final_confidence"] or "UNKNOWN"))
    reviewed = bool(row["human_action"])
    cited_sentences = json.loads(row["cited_sentences"]) if row["cited_sentences"] else []

    # self_confidence sits next to final_confidence so the reviewer can see when
    # the cross-check downgraded the model (model said "high", final is "low").
    confidence_line = f"{emoji} **{label}**"
    if row["self_confidence"] and row["self_confidence"] != row["final_confidence"]:
        confidence_line += f"  (model said: {row['self_confidence']})"
    polarity_emoji, polarity_label = POLARITY_BADGES.get(row["polarity"], ("", ""))
    if polarity_label:
        confidence_line += f"  ·  {polarity_emoji} **{polarity_label}**"

    sheet_label = f" [{row['sheet_name']}]" if row["sheet_name"] else ""
    header = f"{confidence_line} — Row {row['row_index']}{sheet_label}"
    if reviewed:
        header += f"  ·  ✓ {row['human_action']} ({row['timestamp']})"
    st.markdown(header)
    st.write(f"**Q:** {row['question_text']}")

    # C4: show when this row drew on the answer library, with the provenance —
    # the reviewer must be able to see that a prior approved answer was surfaced
    # and why, without digging into the store.
    lib = json.loads(row["library_candidate"]) if row["library_candidate"] else None
    if lib and lib.get("candidates"):
        top = lib["candidates"][0]
        state_label = (
            "**reused a prior approved answer**"
            if lib.get("state") == "used"
            else "had a prior approved answer surfaced (draft did not reuse it)"
        )
        st.caption(
            f"📚 Answer library: {state_label} — "
            f"run {top['run_id']}, row {top['row_index']}, {top['human_action']} at {top['reviewed_at']}"
        )

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
            chunk = chunks_by_id.get(embedding_id)
            if chunk is None:
                continue
            st.caption(f"{chunk['source_filename']} — {chunk['heading_path'] or '(no heading)'}")
            st.markdown(_highlight_cited(chunk["text"], cited_sentences))

    row_index = row["row_index"]
    if READ_ONLY:
        st.caption("Frozen sample — read-only. Run 'qresp demo' (or the Docker image) to review a real run.")
        st.divider()
        return
    edit_key = f"editing_{row_index}"
    approve_col, edit_col, reject_col, undo_col = st.columns(4)
    if approve_col.button("Approve", key=f"approve_{row_index}"):
        db.record_human_review(conn, run_id, row_index, "approved")
        st.rerun()
    if reject_col.button("Reject", key=f"reject_{row_index}"):
        db.record_human_review(conn, run_id, row_index, "rejected")
        st.rerun()
    # P29: review events are appended, never overwritten, so a misclick is
    # recoverable — this appends an "unreviewed" event that restores the row.
    if undo_col.button("Undo review", key=f"undo_{row_index}", disabled=not reviewed):
        db.record_human_review(conn, run_id, row_index, None)
        st.session_state.pop(edit_key, None)
        st.rerun()
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
        if row["final_confidence"] == "error":
            # Error rows are never approvable: always ship the ERROR marker,
            # regardless of any human_action, and never the drafted text.
            write_answer(ws, row["row_index"], column_map, "", None, "error")
            continue
        action = row["human_action"]
        # Unknown or None actions (unreviewed rows) are skipped, not crashed on.
        if action not in counts:
            continue
        counts[action] += 1
        if action == "rejected":
            # Same NOT_FOUND marker convention as everywhere else — a rejected
            # answer must not ship, reviewed or not.
            write_answer(ws, row["row_index"], column_map, "", None, "none")
        elif action == "edited":
            write_answer(
                ws,
                row["row_index"],
                column_map,
                row["reviewed_answer"] or "",
                row["vocab_selection"],
                row["final_confidence"],
            )
        else:  # approved
            write_answer(
                ws,
                row["row_index"],
                column_map,
                row["drafted_answer"] or "",
                row["vocab_selection"],
                row["final_confidence"],
            )

    export_path = _reviewed_export_path(output_path)
    export_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(export_path)

    db.record_export(conn, run_id, str(export_path), counts)
    return export_path


def main():
    conn = get_conn()

    if READ_ONLY:
        st.warning(
            "Frozen sample — this is a read-only, hosted demo of the review screen over "
            "synthetic fixture evidence. Approve/Edit/Reject and export are disabled. "
            "Run 'qresp demo' or the Docker image to review your own run. Source: "
            "[the repository](https://github.com/PatricR73/Questionnaire-Responder)."
        )

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

    # The latest review event per row is the current state (P29): events are
    # appended to audit_log (confidence='review'), never overwritten, so the trail
    # survives. Older overwritten rows (human_action set directly on the processing
    # entry) are matched too, for databases written before the change.
    rows = conn.execute(
        """
        SELECT a.row_index, a.question_text, a.drafted_answer, a.reviewed_answer,
               a.vocab_selection, a.final_confidence, a.self_confidence, a.polarity,
               a.cited_chunk_ids, a.cited_sentences, a.library_candidate, a.sheet_name,
               l.human_action, l.timestamp
        FROM answers a
        LEFT JOIN audit_log l ON l.id = (
            SELECT MAX(la.id) FROM audit_log la
            WHERE la.run_id = a.run_id AND la.row_index = a.row_index
              AND (la.confidence = 'review' OR la.human_action IS NOT NULL)
        )
        WHERE a.run_id = ?
        ORDER BY a.row_index
        """,
        (run_id,),
    ).fetchall()

    total = len(rows)
    reviewed_count = sum(1 for r in rows if r["human_action"])

    # Error rows cannot meaningfully be approved — they are always written as
    # ERROR_MARKER on export — so they are excluded from the gate (P29): a run with
    # a handful of ERROR rows used to be unexportable until every one of them was
    # 'reviewed'.
    reviewable = [r for r in rows if r["final_confidence"] != "error"]
    all_reviewed = len(reviewable) > 0 and reviewed_count == len(reviewable)
    export_label = (
        "Export reviewed workbook"
        if all_reviewed
        else f"Export reviewed workbook ({reviewed_count}/{len(reviewable)} reviewed)"
    )
    if READ_ONLY:
        st.sidebar.caption("Export disabled in read-only mode.")
    elif st.sidebar.button(export_label, disabled=not all_reviewed):
        export_path = export_reviewed_workbook(conn, run_id, run_row["source_path"], run_row["output_path"], rows)
        st.sidebar.success(f"Exported to {export_path}")

    filter_choice = st.sidebar.selectbox("Filter", ["All", "High", "Low", "Not found", "Error", "Unreviewed"])

    st.progress(reviewed_count / total if total else 0.0)
    st.write(f"**{reviewed_count} / {total} rows reviewed**")

    # P27: load every cited chunk for the whole run in one query, before the render
    # loop — the loop used to issue one query per cited chunk per row.
    all_cited_ids = sorted(
        {cid for r in rows for cid in (json.loads(r["cited_chunk_ids"]) if r["cited_chunk_ids"] else [])}
    )
    chunks_by_id = load_chunks(conn, all_cited_ids)

    filter_map = {"High": "high", "Low": "low", "Not found": "none", "Error": "error"}
    if filter_choice == "Unreviewed":
        filtered = [r for r in rows if not r["human_action"]]
    elif filter_choice in filter_map:
        filtered = [r for r in rows if r["final_confidence"] == filter_map[filter_choice]]
    else:
        filtered = rows

    # Pagination after filtering, so the per-page counts reflect the active filter.
    page_key = f"review_page_{run_id}"
    page = st.session_state.get(page_key, 1)
    page_count = max(1, math.ceil(len(filtered) / PAGE_SIZE))
    page = min(max(page, 1), page_count)
    start = (page - 1) * PAGE_SIZE
    page_rows = filtered[start : start + PAGE_SIZE]

    st.write(f"**Showing {len(filtered)} row(s)** ({start + 1}–{min(start + PAGE_SIZE, len(filtered))} on this page)")

    nav_cols = st.columns(4)
    if nav_cols[0].button("← Prev", key=f"prev_{page_key}", disabled=page <= 1):
        st.session_state[page_key] = page - 1
        st.rerun()
    nav_cols[1].write(f"Page {page} / {page_count}")
    if nav_cols[2].button("Next →", key=f"next_{page_key}", disabled=page >= page_count):
        st.session_state[page_key] = page + 1
        st.rerun()

    for row in page_rows:
        render_row(conn, run_id, row, chunks_by_id)


if __name__ == "__main__":
    main()

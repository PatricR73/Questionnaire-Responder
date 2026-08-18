"""Stable Python API for the Questionnaire Responder (pack 3, C10).

The only supported entry point used to be a CLI, so any buyer whose answer is
"we'd want this in our compliance portal" or "our platform team would wire this
into our intake flow" hit a wall — they'd have to shell out to a CLI and parse
stdout. This package defines the supported programmatic surface:

    from qresp import Pipeline

    pipeline = Pipeline()                      # optional data_dir=... / config_file=...
    pipeline.ingest("path/to/evidence/")
    result = pipeline.answer("q.xlsx", "filled.xlsx", provider="stub")
    report = pipeline.gap_report(result.run_id)

All three methods run the EXACT code path the CLI runs (the CLI is a thin wrapper
over the same functions); nothing here shells out or re-implements pipeline
logic. The service layer (qresp serve / src/service.py) is a thin HTTP wrapper
over this same surface — see docs/INTEGRATION.md.

Guarantees are identical to the CLI: same config precedence (CLI-equivalent flags
beat env vars beat the optional TOML file), same per-row fault isolation, same
citation/entailment/library behaviour, same artifacts on disk. The data directory
defaults to the same repo-resolved out/ (or QRESP_DATA_DIR) as the CLI, so API and
CLI runs share a store — one questionnaire can be answered by the API and reviewed
in the CLI's review UI.
"""

import dataclasses
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = ["AnswerRowResult", "AnswerRunResult", "GapReport", "Pipeline", "__version__"]
__version__ = "0.1.0"


@dataclass
class AnswerRowResult:
    """Structured result of one questionnaire row."""

    sheet: str
    row_index: int
    question_text: str
    answer: str | None
    final_confidence: str
    polarity: str | None
    provider: str
    cited_chunk_ids: list[str]
    library_state: str | None = None
    library_provenance: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AnswerRunResult:
    """Structured result of a whole run: the row results plus run-level counts."""

    run_id: int
    output_path: str
    counts: dict = field(default_factory=dict)
    rows: list[AnswerRowResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "output_path": self.output_path,
            "counts": self.counts,
            "rows": [r.to_dict() for r in self.rows],
        }


@dataclass
class GapReport:
    run_id: int
    source_path: str
    total_questions: int
    gap_count: int
    weak_count: int
    gaps: list[dict] = field(default_factory=list)
    weak: list[dict] = field(default_factory=list)
    domains_ranked: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class Pipeline:
    """Programmatic access to ingest -> answer -> gap-report.

    Parameters mirror the CLI: data_dir overrides where the store lives (defaults
    to the CLI's data_dir resolution), config_file is the optional TOML tuning
    file (same precedence rules). Instances are cheap; they hold no connections.
    """

    def __init__(self, data_dir: str | Path | None = None, config_file: str | Path | None = None):
        self._data_dir = str(data_dir) if data_dir is not None else None
        self._config_file = Path(config_file) if config_file is not None else None

    def _activate(self) -> None:
        """Point the store at this pipeline's data directory BEFORE any connect —
        the CLI's --workspace does the same thing (an env var read at call time)."""
        if self._data_dir is not None:
            os.environ["QRESP_DATA_DIR"] = self._data_dir

    # -- operations ----------------------------------------------------------

    def ingest(self, evidence_dir: str | Path) -> int:
        """Parse+chunk+embed an evidence directory into the store; returns the
        number of chunks ingested. Same idempotent delete-before-insert semantics
        as 'qresp ingest'."""
        self._activate()
        from src.ingest.embed import ingest_evidence
        from src.store import db
        from src.store.vectorstore import VectorStore

        conn = db.connect()
        vector_store = VectorStore()
        return ingest_evidence(Path(evidence_dir), conn, vector_store)

    def answer(
        self,
        questionnaire: str | Path,
        output: str | Path,
        *,
        limit: int = 0,
        provider: str = "stub",
        sheet: str | None = None,
        map_override: str | None = None,
        top_k: int | None = None,
        stub_fail_row: int | None = None,
        dry_run: bool = False,
    ) -> AnswerRunResult:
        """Answer a questionnaire against the store, exactly as 'qresp answer'
        would, and return the structured result. provider: anthropic | stub |
        local (local needs an OpenAI-compatible endpoint; see --provider local)."""
        self._activate()
        from src.pipeline import answer as answer_command

        callback = answer_command.callback
        if callback is None:
            raise RuntimeError("CLI answer command has no callback")
        callback(
            questionnaire=Path(questionnaire),
            output=Path(output),
            limit=limit,
            only_row=None,
            sheet=sheet,
            map_override=map_override,
            provider=provider,
            stub_fail_row=stub_fail_row,
            dry_run=dry_run,
            config=self._config_file,
            top_k=top_k,
            exact=False,
            verbose=False,
            quiet=True,
        )

        if dry_run:
            return AnswerRunResult(run_id=0, output_path=str(output))

        from src.store import db

        conn = db.connect()
        run = conn.execute(
            "SELECT id FROM questionnaire_runs WHERE output_path = ? ORDER BY id DESC LIMIT 1",
            (str(output),),
        ).fetchone()
        if run is None:
            raise RuntimeError(f"No run row found for output {output} — the pipeline ran but did not record a run.")
        run_id = run["id"]
        rows = conn.execute(
            "SELECT sheet_name, row_index, question_text, drafted_answer, final_confidence, polarity, "
            "cited_chunk_ids, library_candidate FROM answers WHERE run_id = ? ORDER BY row_index",
            (run_id,),
        ).fetchall()
        result_rows = []
        for r in rows:
            lib = __import__("json").loads(r["library_candidate"]) if r["library_candidate"] else None
            result_rows.append(
                AnswerRowResult(
                    sheet=r["sheet_name"] or "",
                    row_index=r["row_index"],
                    question_text=r["question_text"],
                    answer=r["drafted_answer"],
                    final_confidence=r["final_confidence"],
                    polarity=r["polarity"],
                    provider=provider,
                    cited_chunk_ids=__import__("json").loads(r["cited_chunk_ids"] or "[]"),
                    library_state=(lib or {}).get("state"),
                    library_provenance=(lib or {}).get("provenance"),
                )
            )
        counts = {"high": 0, "low": 0, "none": 0, "error": 0}
        for r in result_rows:
            counts[r.final_confidence] = counts.get(r.final_confidence, 0) + 1
        return AnswerRunResult(run_id=run_id, output_path=str(output), counts=counts, rows=result_rows)

    def gap_report(self, run_id: int, output_dir: str | Path | None = None) -> GapReport:
        """The documentation gap analysis for a completed run (see 'qresp
        gap-report'). When output_dir is given, the .md and .xlsx reports are also
        written there. Retrieval-only: no API calls."""
        self._activate()

        from src.config import load_config
        from src.data_dir import REPO_ROOT
        from src.gap_report import build_gap_report, render_markdown, render_xlsx
        from src.retrieval.hybrid_search import HybridSearcher
        from src.store import db
        from src.store.vectorstore import VectorStore

        conn = db.connect()
        src_row = conn.execute("SELECT source_path FROM questionnaire_runs WHERE id = ?", (run_id,)).fetchone()
        if src_row is None:
            raise ValueError(f"No questionnaire run with id {run_id}")
        questionnaire_path = Path(src_row["source_path"])
        if not questionnaire_path.is_absolute():
            candidate = REPO_ROOT / questionnaire_path
            if candidate.exists():
                questionnaire_path = candidate
        cfg = load_config(config_file=self._config_file)
        vector_store = VectorStore(model_name=cfg.embedding_model)
        searcher = HybridSearcher(
            conn, vector_store, vector_weight=cfg.vector_weight, rrf_k=cfg.rrf_k, candidate_pool=cfg.candidate_pool
        )
        report = build_gap_report(
            conn, run_id, questionnaire_path if questionnaire_path.exists() else Path(""), searcher, top_k=cfg.top_k
        )
        if output_dir is not None:
            out_dir = Path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"gap_report_run{run_id}.md").write_text(render_markdown(report))
            render_xlsx(report, out_dir / f"gap_report_run{run_id}.xlsx")
        return GapReport(
            run_id=report["run_id"],
            source_path=report["source_path"],
            total_questions=report["total_questions"],
            gap_count=report["gap_count"],
            weak_count=report["weak_count"],
            gaps=[dataclasses.asdict(g) for g in report["gaps"]],
            weak=report["weak"],
            domains_ranked=report["domains_ranked"],
        )

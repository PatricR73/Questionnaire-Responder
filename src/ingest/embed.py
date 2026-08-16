"""Ingest an evidence directory: parse -> chunk -> embed into Chroma -> mirror metadata into SQLite."""

import sqlite3
from pathlib import Path

from src.ingest.chunk import chunk_blocks
from src.ingest.parse_docs import parse_document
from src.store.vectorstore import VectorStore

SUPPORTED_SUFFIXES = {".md", ".txt", ".docx", ".pdf"}


def _source_key(path: Path, evidence_dir: Path) -> str:
    """Identity of a source document, used for chunk-id prefixes, Chroma metadata,
    and delete-before-insert.

    Keyed by the path relative to evidence_dir (posix separators), never the bare
    filename: hr/policy.md and eng/policy.md are different sources and must produce
    different chunk ids. Keying by path.name made them collide — one silently
    overwrote the other on ingest, and a re-ingest of one could never cleanly delete
    the other's chunks. source_filename stores this same key, so retrieval/UI labels
    also show which subdirectory a passage came from.
    """
    return path.relative_to(evidence_dir).as_posix()


def ingest_evidence(evidence_dir: Path, conn: sqlite3.Connection, vector_store: VectorStore) -> int:
    """Parse+chunk+embed every supported file in evidence_dir. Returns the number of chunks ingested.

    Idempotent: before inserting a document's chunks, every existing row/entry for
    that source is deleted from both SQLite and Chroma, so re-ingesting the same
    directory (or one where a source got shorter after edits) leaves a store with
    exactly the current content. INSERT OR REPLACE / Chroma upsert alone only
    overwrite same-id rows — trailing chunks from a previous, longer version of a
    document would otherwise survive and stay retrievable forever, meaning a control
    a user deleted from their policy would keep being cited into customer-facing
    questionnaires. That is the exact liability failure mode this project exists to
    prevent, so stale-chunk cleanup is part of ingest, not an afterthought.
    """
    files = sorted(p for p in evidence_dir.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES)

    total_chunks = 0
    for path in files:
        source_key = _source_key(path, evidence_dir)

        # Delete everything currently stored for this source, in both stores, before
        # re-inserting the fresh version.
        conn.execute("DELETE FROM chunks WHERE source_filename = ?", (source_key,))
        conn.commit()
        vector_store.delete_by_source(source_key)

        blocks = parse_document(path)
        chunks = chunk_blocks(blocks, source_filename=source_key)
        if not chunks:
            continue

        ids = [f"{source_key}::{i}" for i in range(len(chunks))]
        texts = [c.text for c in chunks]
        metadatas = [
            {"source_filename": c.source_filename, "heading_path": c.heading_path, "loc_ref": c.loc_ref} for c in chunks
        ]
        vector_store.upsert(ids=ids, texts=texts, metadatas=metadatas)

        conn.executemany(
            "INSERT OR REPLACE INTO chunks (source_filename, heading_path, loc_ref, text, embedding_id) "
            "VALUES (:source_filename, :heading_path, :loc_ref, :text, :embedding_id)",
            [
                {
                    "source_filename": c.source_filename,
                    "heading_path": c.heading_path,
                    "loc_ref": c.loc_ref,
                    "text": c.text,
                    "embedding_id": chunk_id,
                }
                for c, chunk_id in zip(chunks, ids)
            ],
        )
        conn.commit()
        total_chunks += len(chunks)

    return total_chunks

"""Ingest an evidence directory: parse -> chunk -> embed into Chroma -> mirror metadata into SQLite."""

import sqlite3
from pathlib import Path

from src.ingest.chunk import chunk_blocks
from src.ingest.parse_docs import parse_document
from src.store.vectorstore import VectorStore

SUPPORTED_SUFFIXES = {".md", ".txt", ".docx", ".pdf"}


def ingest_evidence(evidence_dir: Path, conn: sqlite3.Connection, vector_store: VectorStore) -> int:
    """Parse+chunk+embed every supported file in evidence_dir. Returns the number of chunks ingested."""
    files = sorted(p for p in evidence_dir.rglob("*") if p.suffix.lower() in SUPPORTED_SUFFIXES)

    total_chunks = 0
    for path in files:
        blocks = parse_document(path)
        chunks = chunk_blocks(blocks, source_filename=path.name)
        if not chunks:
            continue

        ids = [f"{path.name}::{i}" for i in range(len(chunks))]
        texts = [c.text for c in chunks]
        metadatas = [
            {"source_filename": c.source_filename, "heading_path": c.heading_path, "loc_ref": c.loc_ref}
            for c in chunks
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

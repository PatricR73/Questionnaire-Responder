"""Thin wrapper around a local persistent Chroma collection using a local sentence-transformers model."""

from pathlib import Path

from src.data_dir import data_dir

DEFAULT_COLLECTION = "evidence_chunks"
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
# Pin the exact model revision: a bare model name resolves to whatever the hub
# points at on install day, and bge-small-en-v1.5's weights can change out from
# under WEAK_MATCH_DISTANCE = 0.3 — "reproduce these numbers yourself" stops
# being true on a fresh install. This revision is what the deterministic eval
# numbers in EVAL.md/TUNING_LOG.md were measured against (see the lockfile note
# in EVAL.md). Passed through to SentenceTransformer via the embedding function's
# model kwargs; re-derive the eval numbers if it ever has to move.
DEFAULT_MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"

# BAAI's bge-*-en-v1.5 embedding models are trained for an asymmetric retrieval
# setup: the QUERY side must carry this instruction prefix while stored passages
# stay unprefixed. Omitting it costs recall on exactly the asymmetric
# short-query-to-long-passage case this project has (a CAIQ question against
# long policy chunks). Applied on the query path only (VectorStore.query) — never
# on upsert, where the prefix would corrupt the passage embeddings and invalidate
# every stored distance. REVISIT if DEFAULT_MODEL changes to a non-bge model: the
# prefix is specific to the bge-*-v1.5 training recipe and would degrade other
# models' embeddings if applied to them.
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class VectorStore:
    def __init__(
        self,
        persist_dir: Path | None = None,
        collection_name: str = DEFAULT_COLLECTION,
        model_name: str = DEFAULT_MODEL,
    ):
        if persist_dir is None:
            persist_dir = data_dir() / "chroma"
        # persist_dir is resolved at call time (not import time) so a
        # QRESP_DATA_DIR change or a foreign cwd takes effect for the next store —
        # same contract as db.connect().
        import chromadb
        from chromadb.utils import embedding_functions

        persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(persist_dir))
        embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=model_name, revision=DEFAULT_MODEL_REVISION
        )
        # Pin the distance metric explicitly. Chroma's default is L2, which the
        # confidence threshold (WEAK_MATCH_DISTANCE) used to be interpreted in —
        # but a future Chroma default change would silently invalidate that
        # threshold, and the collection's metric is fixed at creation. Cosine is
        # the right metric for normalized bge embeddings (the distance the
        # confidence docstring reasons about), and the threshold was converted to
        # cosine space (see confidence.py). Any existing out/chroma index was
        # built with the old metric and MUST be rebuilt (delete + re-ingest).
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            embedding_function=embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, ids: list[str], texts: list[str], metadatas: list[dict]) -> None:
        self._collection.upsert(ids=ids, documents=texts, metadatas=metadatas)

    def delete_by_source(self, source_key: str) -> None:
        """Remove every entry belonging to one source document.

        Used by ingest on re-ingest so a shorter re-version of a source can't leave
        trailing chunks behind (see src/ingest/embed.py). Deleting nothing when the
        source isn't in the collection is a legitimate no-op. Keyed on the
        source_filename metadata, which ingest stores as the evidence-relative path
        (see embed._source_key), so same-named files in different subdirectories are
        distinct sources here too."""
        self._collection.delete(where={"source_filename": source_key})

    def get(self, ids: list[str]) -> list[str]:
        """Return the subset of ids actually present in the collection. Exists so
        ingest tests can prove stale chunks were deleted from the vector store, not
        just from SQLite."""
        return self._collection.get(ids=ids)["ids"]

    def count(self) -> int:
        """Number of entries in the collection (another ingest-test hook: after a
        shortened re-ingest the count must equal the new chunk total, never the old)."""
        return self._collection.count()

    def query(self, text: str, top_k: int = 5) -> list[dict]:
        # Query-only instruction prefix — see BGE_QUERY_PREFIX. The stored passage
        # embeddings were built unprefixed at ingest; only the query side changes.
        result = self._collection.query(query_texts=[BGE_QUERY_PREFIX + text], n_results=top_k)
        hits = []
        for i in range(len(result["ids"][0])):
            hits.append(
                {
                    "id": result["ids"][0][i],
                    "text": result["documents"][0][i],
                    "metadata": result["metadatas"][0][i],
                    "distance": result["distances"][0][i],
                }
            )
        return hits

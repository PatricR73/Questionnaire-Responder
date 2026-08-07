"""Thin wrapper around a local persistent Chroma collection using a local sentence-transformers model."""

from pathlib import Path

DEFAULT_PERSIST_DIR = Path("out") / "chroma"
DEFAULT_COLLECTION = "evidence_chunks"
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


class VectorStore:
    def __init__(
        self,
        persist_dir: Path = DEFAULT_PERSIST_DIR,
        collection_name: str = DEFAULT_COLLECTION,
        model_name: str = DEFAULT_MODEL,
    ):
        import chromadb
        from chromadb.utils import embedding_functions

        persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(persist_dir))
        embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=model_name)
        self._collection = self._client.get_or_create_collection(name=collection_name, embedding_function=embedding_fn)

    def upsert(self, ids: list[str], texts: list[str], metadatas: list[dict]) -> None:
        self._collection.upsert(ids=ids, documents=texts, metadatas=metadatas)

    def query(self, text: str, top_k: int = 5) -> list[dict]:
        result = self._collection.query(query_texts=[text], n_results=top_k)
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

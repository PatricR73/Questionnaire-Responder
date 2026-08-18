"""Central configuration for every tuning knob, resolved with precedence
CLI flag > environment variable > optional TOML file > default.

Before this module, every knob was a module-level constant (MODEL, max_tokens,
WEAK_MATCH_DISTANCE, VECTOR_WEIGHT, RRF_K, CANDIDATE_POOL, top_k, chunk bounds,
DEFAULT_MODEL), so every tuning pass required a source edit and TUNING_LOG.md had
to describe changes in prose rather than pointing at a config. The frozen Config
dataclass holds them all; each module keeps its constant as the DEFAULT so current
behaviour is bit-identical when nothing is configured, and the explanatory
comments stay attached to the constants in their home modules — they are the most
valuable part of those modules and must not be lost in the move.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from dataclasses import MISSING, dataclass, fields
from pathlib import Path
from typing import Any

from src.answer.confidence import WEAK_MATCH_DISTANCE
from src.answer.generate import MAX_TOKENS, MODEL
from src.ingest.chunk import MAX_CHUNK_CHARS, MIN_CHUNK_CHARS, OVERLAP_SENTENCES
from src.retrieval.hybrid_search import CANDIDATE_POOL, RRF_K, VECTOR_WEIGHT
from src.store.vectorstore import DEFAULT_MODEL

# Every field name the Config carries; used to validate TOML keys and env vars so a
# typo fails loudly instead of silently configuring nothing.
_FIELD_NAMES = {
    "model",
    "max_tokens",
    "weak_match_distance",
    "vector_weight",
    "rrf_k",
    "candidate_pool",
    "top_k",
    "max_chunk_chars",
    "min_chunk_chars",
    "overlap_sentences",
    "embedding_model",
    "reranker",
    "entailment_check",
    "entailment_model",
    "answer_library",
    "library_semantic_threshold",
    "local_base_url",
    "local_model",
    "input_price_per_mtok",
    "output_price_per_mtok",
}

_INT_FIELDS = {
    "max_tokens",
    "rrf_k",
    "candidate_pool",
    "top_k",
    "max_chunk_chars",
    "min_chunk_chars",
    "overlap_sentences",
}
_FLOAT_FIELDS = {"weak_match_distance", "vector_weight", "input_price_per_mtok", "output_price_per_mtok", "library_semantic_threshold"}
_BOOL_FIELDS = {"reranker", "entailment_check", "answer_library"}

_ENV_VAR = "QRESP_CONFIG"  # env var pointing at a TOML file


@dataclass(frozen=True)
class Config:
    """Frozen resolved configuration. Defaults are the module constants, so an
    empty Config is byte-identical to the pre-P18 behaviour."""

    model: str = MODEL
    max_tokens: int = MAX_TOKENS
    weak_match_distance: float = WEAK_MATCH_DISTANCE
    vector_weight: float = VECTOR_WEIGHT
    rrf_k: int = RRF_K
    candidate_pool: int = CANDIDATE_POOL
    top_k: int = 5
    max_chunk_chars: int = MAX_CHUNK_CHARS
    min_chunk_chars: int = MIN_CHUNK_CHARS
    overlap_sentences: int = OVERLAP_SENTENCES
    embedding_model: str = DEFAULT_MODEL
    # P11: local cross-encoder reranker over the fused candidate pool, default OFF
    # so the existing baseline stays reproducible. See src/retrieval/reranker.py.
    reranker: bool = False
    # C4: the answer library — surface human-approved prior answers as labelled
    # candidates to the generator (never as evidence; citation/entailment still run
    # against the original evidence). Default OFF so the published baseline stays
    # reproducible; measured in TUNING_LOG.md before it ever defaults on.
    answer_library: bool = False
    # Semantic-equivalence threshold for the library's question matching (see
    # src/answer/library.py). Conservative by default; revisit with measured data.
    library_semantic_threshold: float = 0.75
    # C7: fully on-premise generation via any OpenAI-compatible endpoint (Ollama,
    # vLLM, llama.cpp server) — --provider local. Defaults mirror
    # src/answer/local.py. The answer_library, citation grounding, and entailment
    # guarantees run identically in local mode; only the model changes.
    local_base_url: str = "http://localhost:11434/v1"
    local_model: str = "qwen2.5:7b-instruct"
    # A1: third confidence layer — does the answer FOLLOW from the cited sentences
    # (not just cite them verbatim)? Default OFF so the 14/24 baseline stays
    # reproducible. See src/answer/entailment.py. entailment_model should be the
    # cheapest capable model on the account; the check costs one small call per
    # answered row.
    entailment_check: bool = False
    entailment_model: str = "claude-sonnet-5"
    # Rate card for the estimated-cost lines (B5): dollars per million tokens.
    # Moved out of _estimate_cost's source so a price change is a config change,
    # and serialized into run_config so an old run's cost stays interpretable.
    input_price_per_mtok: float = 3.0
    output_price_per_mtok: float = 15.0

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _coerce(name: str, raw: object):
    if name in _INT_FIELDS:
        return int(str(raw))
    if name in _FLOAT_FIELDS:
        return float(str(raw))
    if name in _BOOL_FIELDS:
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return raw


def _read_toml(path: Path) -> dict:
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    # Accept either top-level keys or a [config] section.
    section = data.get("config", data) if isinstance(data, dict) else {}
    unknown = set(section) - _FIELD_NAMES
    if unknown:
        raise ValueError(f"Unknown config key(s) in {path}: {sorted(unknown)} — valid keys: {sorted(_FIELD_NAMES)}")
    return {k: _coerce(k, v) for k, v in section.items()}


def _env_overrides() -> dict:
    """QRESP_<FIELD> environment variables, e.g. QRESP_TOP_K=8, QRESP_MODEL=..."""
    result = {}
    for name in _FIELD_NAMES:
        raw = os.environ.get(f"QRESP_{name.upper()}")
        if raw is not None:
            result[name] = _coerce(name, raw)
    return result


def load_config(config_file: Path | None = None, cli_overrides: dict | None = None) -> Config:
    """Resolve precedence: CLI flag > env var > optional TOML file > default.

    config_file may also be supplied via the QRESP_CONFIG environment variable.
    cli_overrides is a dict of field name -> value from CLI flags (the highest
    precedence); unknown names in it are a programming error and raise."""
    # Any-typed on purpose: values flow through the coercion chain (TOML/env/CLI)
    # and mypy cannot track the runtime types; the dataclass construction below is
    # the single point where the final types are enforced.
    values: dict[str, Any] = {}
    for f in fields(Config):
        if f.default is not MISSING:
            values[f.name] = f.default

    file_path = config_file
    if file_path is None and os.environ.get(_ENV_VAR):
        file_path = Path(os.environ[_ENV_VAR])
    if file_path is not None:
        values.update(_read_toml(file_path))

    values.update(_env_overrides())

    if cli_overrides:
        unknown = set(cli_overrides) - _FIELD_NAMES
        if unknown:
            raise ValueError(f"Unknown CLI config override(s): {sorted(unknown)}")
        values.update(cli_overrides)

    # Explicit construction rather than Config(**values) so the field names are
    # checked; the runtime types are guaranteed by the coercion chain above.
    return Config(
        model=values["model"],
        max_tokens=values["max_tokens"],
        weak_match_distance=values["weak_match_distance"],
        vector_weight=values["vector_weight"],
        rrf_k=values["rrf_k"],
        candidate_pool=values["candidate_pool"],
        top_k=values["top_k"],
        max_chunk_chars=values["max_chunk_chars"],
        min_chunk_chars=values["min_chunk_chars"],
        overlap_sentences=values["overlap_sentences"],
        embedding_model=values["embedding_model"],
        reranker=values["reranker"],
        entailment_check=values["entailment_check"],
        entailment_model=values["entailment_model"],
        answer_library=values["answer_library"],
        library_semantic_threshold=values["library_semantic_threshold"],
        local_base_url=values["local_base_url"],
        local_model=values["local_model"],
        input_price_per_mtok=values["input_price_per_mtok"],
        output_price_per_mtok=values["output_price_per_mtok"],
    )

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
from dataclasses import dataclass, fields
from pathlib import Path

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
_FLOAT_FIELDS = {"weak_match_distance", "vector_weight"}
_BOOL_FIELDS = {"reranker"}

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

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _coerce(name: str, raw: object):
    if name in _INT_FIELDS:
        return int(raw)
    if name in _FLOAT_FIELDS:
        return float(raw)
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
    values = {f.name: f.default for f in fields(Config)}

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

    return Config(**values)

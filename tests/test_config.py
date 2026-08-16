"""P18: config resolution precedence (CLI flag > env var > TOML file > default)."""

import pytest

from src.config import load_config


def test_defaults_match_the_module_constants():
    cfg = load_config()
    from src.answer.confidence import WEAK_MATCH_DISTANCE
    from src.answer.generate import MAX_TOKENS, MODEL
    from src.ingest.chunk import MAX_CHUNK_CHARS, MIN_CHUNK_CHARS
    from src.retrieval.hybrid_search import CANDIDATE_POOL, RRF_K, VECTOR_WEIGHT
    from src.store.vectorstore import DEFAULT_MODEL

    assert cfg.model == MODEL
    assert cfg.max_tokens == MAX_TOKENS
    assert cfg.weak_match_distance == WEAK_MATCH_DISTANCE
    assert cfg.vector_weight == VECTOR_WEIGHT
    assert cfg.rrf_k == RRF_K
    assert cfg.candidate_pool == CANDIDATE_POOL
    assert cfg.max_chunk_chars == MAX_CHUNK_CHARS
    assert cfg.min_chunk_chars == MIN_CHUNK_CHARS
    assert cfg.embedding_model == DEFAULT_MODEL
    assert cfg.top_k == 5


def test_toml_file_applies(tmp_path):
    cfg_file = tmp_path / "cfg.toml"
    cfg_file.write_text("[config]\nweak_match_distance = 0.5\ncandidate_pool = 30\n")
    cfg = load_config(config_file=cfg_file)
    assert cfg.weak_match_distance == 0.5
    assert cfg.candidate_pool == 30
    assert cfg.vector_weight == 2.0  # untouched default


def test_unknown_toml_key_raises(tmp_path):
    cfg_file = tmp_path / "cfg.toml"
    cfg_file.write_text("weak_match_distnace = 0.5\n")  # typo
    with pytest.raises(ValueError, match="weak_match_distnace"):
        load_config(config_file=cfg_file)


def test_env_var_overrides_toml(tmp_path, monkeypatch):
    cfg_file = tmp_path / "cfg.toml"
    cfg_file.write_text("top_k = 3\n")
    monkeypatch.setenv("QRESP_TOP_K", "7")
    cfg = load_config(config_file=cfg_file)
    assert cfg.top_k == 7  # env beats file
    assert cfg.rrf_k == RRF_K_from_module()


def RRF_K_from_module():
    from src.retrieval.hybrid_search import RRF_K

    return RRF_K


def test_cli_flag_beats_env(monkeypatch):
    monkeypatch.setenv("QRESP_TOP_K", "7")
    cfg = load_config(cli_overrides={"top_k": 9})
    assert cfg.top_k == 9


def test_cli_unknown_override_raises():
    with pytest.raises(ValueError, match="nonsense"):
        load_config(cli_overrides={"nonsense": 1})

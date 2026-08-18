"""Token counting with Claude's real tokenizer, computed locally.

dry-run needs accurate token counts with ZERO API calls — the count_tokens endpoint
is itself an API call, and the task that built this module rules out both calls and
character heuristics. The official Anthropic SDK shipped a local tokenizer for
years: it downloaded Anthropic's public tokenizer JSON and loaded it with the
`tokenizers` library (which is already installed here as a sentence-transformers
dependency). This module replicates that pattern.

Known caveat, stated plainly: the JSON is the original claude-v1-tokenization.json
(the file the official SDK used, still the tokenizer on every public mirror).
claude-sonnet-5 uses a newer tokenizer revision, and a live cross-check measured
this one UNDERCOUNTING the API's count by roughly 1.4-1.9x on real prompts. The
estimate is therefore directional, not exact — a real tokenizer, not a heuristic,
but a tokenizer one revision behind the model. If exact numbers matter, the
count_tokens endpoint exists but is an API call, which dry-run deliberately
avoids.

Provider caveat (2026-08-18): this is Claude's BPE, but the DEFAULT provider is
now OpenAI-compatible (DeepSeek, deepseek-v4-flash), which has its own tokenizer.
The dry-run prints this caveat: token counts (and therefore the cost band) are
estimates for the default provider, and --exact (the Anthropic count_tokens API)
only applies to --provider anthropic. Swap in the provider's tokenizer or
re-measure before treating a dry-run number as a bill.

The JSON is fetched once per machine (the URL the old SDK used is dead — verified
AccessDenied — so the mirror here is the HF-hosted copy of the same file) and
cached under the temp directory, exactly like the old SDK cached it. No network is
needed after the first dry-run.
"""

import os
import tempfile
from pathlib import Path

import httpx
from tokenizers import Tokenizer

# Third-party mirror (Xenova/claude-tokenizer on the HuggingFace hub) of the
# claude-v1-tokenization.json file the official anthropic-sdk-python shipped —
# NOT an Anthropic endpoint. The file is fetched once and cached under the temp
# dir; set QRESP_TOKENIZER_JSON to a local copy to go fully offline.
_TOKENIZER_REMOTE_URL = "https://huggingface.co/Xenova/claude-tokenizer/resolve/main/tokenizer.json"
_tokenizer: Tokenizer | None = None


def tokenizer_cache_path() -> Path:
    """Where the tokenizer JSON is cached (overridable for tests/offline use)."""
    override = os.environ.get("QRESP_TOKENIZER_JSON")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "anthropic" / "claude_tokenizer.json"


def _load_tokenizer() -> Tokenizer:
    global _tokenizer
    if _tokenizer is None:
        path = tokenizer_cache_path()
        if not path.exists():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                response = httpx.get(_TOKENIZER_REMOTE_URL, timeout=60)
                response.raise_for_status()
                path.write_text(response.text, encoding="utf-8")
            except httpx.HTTPError as exc:
                # B5: a silent download failure surfaces as an ImportError-adjacent
                # mystery; name the offline escape hatch explicitly.
                raise RuntimeError(
                    "Could not download the Claude tokenizer JSON needed for local token "
                    "counting. Set QRESP_TOKENIZER_JSON to a local copy of the file (a "
                    "claude-v1-tokenization.json), or use --dry-run --exact for "
                    "API-side counts instead."
                ) from exc
        _tokenizer = Tokenizer.from_file(str(path))
    return _tokenizer


def count_tokens(text: str) -> int:
    """Real Claude token count (the BPE the official SDK tokenizer uses), local."""
    if not text:
        return 0
    return len(_load_tokenizer().encode(text).ids)

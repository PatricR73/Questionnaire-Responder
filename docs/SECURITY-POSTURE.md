# Security posture

Pack 3, C11. This is the document a buyer's security team asks for before they
evaluate anything else: **what happens to their policy text**. It is written to be
handed over as-is. Where this document names a weak point, the mitigation is
already shipped or the tradeoff is explicit — not answered ad hoc over email.

## 1. What the tool holds, and where

| Data | Where it lives | At rest |
|---|---|---|
| Chunked policy text (the parsed evidence) | `out/store.db` (SQLite, `chunks` table) and `out/chroma/` (the vector index) | **Plaintext by default**; optional SQLCipher encryption of the SQLite store via `QRESP_STORE_KEY` (see §6) |
| Every drafted answer, vocabulary selection, and confidence state | `out/store.db` (`answers` table) | Plaintext by default; same optional encryption |
| The audit trail (sources consulted per row, human review actions) | `out/store.db` (`audit_log`) | Plaintext by default; same optional encryption |
| The answer library (human-approved answers with provenance) | `out/store.db` (`reviewed_answers`) | Plaintext by default; same optional encryption |
| Per-row structured logs | `*.log.jsonl` next to each output workbook | Plaintext |
| Per-run sidecar records | `*.jsonl` next to each output workbook | Plaintext |
| The API key | Never written to disk by the tool; an optional local `.env` file (gitignored) if you choose to keep one | Your responsibility |

All of it lives on the **operator's machine** (or the operator's chosen data
directory, including per-client workspaces under `out/workspaces/<name>/`). Nothing
is hosted by the project. `.gitignore` protects `out/`, `.env`, and `.venv/` —
policy text and answers are never committed to a repository by the tool.

## 2. What leaves the machine

- **Runs entirely locally:** parsing, chunking, embedding, retrieval, the
  confidence cross-check, the entailment check (hosted path only), the review UI,
  the gap report, and — with `--provider local` pointed at a LOCAL endpoint
  (Ollama/vLLM/llama.cpp) — generation too.
- **Sent to the Anthropic API — only during `answer` with the default provider:**
  for each question row, the system prompt, the question text, and the retrieved
  evidence passages (chunks of your policies). Nothing else: no full documents, no
  workbook contents beyond the question and the retrieved passages. `ingest`,
  `--dry-run`, the review UI, the gap report, and `--provider local` pointed at a
  LOCAL endpoint make no external calls at all. Anthropic's data-retention terms
  apply to what is sent;
  read them directly: <https://docs.anthropic.com/en/docs/legal/data-usage>.
- **`--provider local` at a LOCAL endpoint (Ollama/vLLM/llama.cpp, no
  `QRESP_LOCAL_API_KEY`): nothing leaves the machine.** This is the option for
  regulated industries and government suppliers; the measured quality trade is
  published in `EVAL.md`. With `QRESP_LOCAL_API_KEY` set, `--provider local` is a
  HOSTED OpenAI-compatible endpoint (DeepSeek et al.) and the question +
  retrieved passages leave your machine to that provider, under its terms — the
  same boundary as the Anthropic path above. The transport flag is
  `--provider openai-compatible`; `local` is a legacy alias that warns when the
  base_url is not loopback/private. Note specifically: DeepSeek's service is
  operated from the People's Republic of China — sending internal policy text
  to it transmits that text to an entity under PRC jurisdiction, which is a
  data-residency and legal decision the buyer must make explicitly and document,
  not a default. The key is read only from the environment or a config file,
  never a CLI flag.

## 3. The review UI

The review UI ships with **no authentication** — by design (it is a local review
surface over a local store). It must be bound to localhost:

```
streamlit run src/review_ui.py --server.address=127.0.0.1
```

If the bind address is widened, the UI shows a warning banner unless
`QRESP_ALLOW_REMOTE_UI=1` is set. Put an authenticating reverse proxy in front of
any non-localhost deployment; the hosted read-only demo (streamlit_app.py) is
frozen read-only over synthetic data and carries its own banner.

## 4. Retention and purging

- The store keeps everything a run produced, forever, until you delete it — the
  audit trail is the point (a client asking "where did this answer come from" gets
  a query, not a shrug).
- **Purge a workspace:** `qresp --workspace acme purge` deletes that workspace's
  SQLite store and Chroma index (chunk text, answers, audit trail) after
  confirmation. `qresp purge` does the same for the default store.
- Complete deletion (including sidecar `.log.jsonl` / `.jsonl` and workbooks
  under the data directory) is ordinary file deletion of the data directory —
  there is nothing stored anywhere else.

## 5. The service (qresp serve)

The integration HTTP service (pack 3, C10) has **no authentication built in** —
bind it to localhost and put an authenticating reverse proxy in front. It is
single-worker and not a multi-tenant product; per-client isolation is the
workspace primitive (pack 3, C9) via the `data_dir` parameter. See
`docs/INTEGRATION.md`.

## 6. At-rest encryption (optional, off by default)

The SQLite store can be encrypted at rest with SQLCipher by setting
`QRESP_STORE_KEY` (or passing a key to `db.connect`) before any command that
touches the store:

```
export QRESP_STORE_KEY='use-a-long-random-string'
qresp ingest --evidence-dir path/to/evidence/
qresp answer --questionnaire q.xlsx --output out/filled.xlsx --limit 0
```

Verified behaviour: a store opened with the key is unreadable by plain sqlite3 and
by a wrong key. Requirements, stated plainly:

- The **same key must be supplied on every open** of that store; losing the key
  loses the data (this is encryption, not recovery).
- Keys are alphanumeric (the PRAGMA path quotes them directly).
- The **Chroma vector index is not covered** — it stores the same chunk text.
  Full at-rest protection of the entire data directory (Chroma included) is
  disk-level encryption (LUKS, FileVault, BitLocker, an encrypted volume) and is
  the recommended default for a production deployment regardless.
- It is off by default because the zero-setup demo story is the product's front
  door (pack 3, C1) — turning encryption on by default would break every "try it
  in 30 seconds" path. The key lives in the environment, not in any file the tool
  writes.

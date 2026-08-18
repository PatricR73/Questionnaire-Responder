# Security

This project processes a company's internal security policies and drafts customer
questionnaires from them. Treat it accordingly.

## Reporting a vulnerability

Please do not open a public issue for a security vulnerability. Report it privately
to the maintainer: **patricrg321@gmail.com**. Include the run's config fingerprint
(the `Config:` line the pipeline prints at run start) and the steps to reproduce,
if you have them. You should receive an acknowledgement within a few days; if not,
follow up.

## What this project does with your data

Read the ["What leaves your machine"](README.md#what-leaves-your-machine) section
of the README — the plain-language answer to where your internal policies go:

- Parsing, chunking, embedding, retrieval, and the review UI run entirely locally.
- During `answer` only, the system prompt, the question, and the retrieved
  evidence passages are sent to the Anthropic API.
- Chunked policy text and drafted answers sit unencrypted in `out/store.db` and
  `out/chroma/`; the sidecar logs hold drafted answers. All of it is gitignored.
- Retention is governed by Anthropic's current data-usage terms (linked there),
  not by this project.

If your use involves policies you cannot transmit to a third-party API at all,
`--provider stub` exercises the pipeline with zero API calls, and the generation
step can be pointed at a different backend in future — but today, real answers
require the Anthropic API.

## Supported versions

Security fixes land on `main` and are backported to the latest release tag when
practical. The lockfile (`requirements.lock`) is the reproducibility path for the
published numbers; dependency updates arrive as Dependabot PRs that CI checks.

## Security posture

For what this tool holds, transmits, retains, and how to purge it — the document a buyer's security team asks for — see [\`docs/SECURITY-POSTURE.md\`](docs/SECURITY-POSTURE.md).

# Roadmap: evidence connectors — scoped, deliberately not built

Pack 3, C14. Company security documentation does not live in a folder of Markdown
files. It lives in Confluence, Notion, Google Drive, SharePoint, and a wiki nobody
has updated since the last audit. The current `--evidence-dir` assumes someone
exports and curates a local directory — real friction, and worse, it means the
evidence base goes stale the moment a policy is edited upstream, silently
reintroducing the class of problem the delete-by-source re-ingest fix (b35e575)
solved for local files.

This document scopes that work. It is deliberately a **plan, not a codebase**: the
project's stated discipline is to build against measured user demand, and there is
no user-demand data for connectors yet. A scoped, unbuilt roadmap item with clear
reasoning reads as judgment. Fourteen half-finished connectors read as thrash.
Saying so here is part of the point.

## Which sources matter, and in what order

Ordered by how much real security documentation actually lives there, for the
buyers this tool serves (B2B companies and the consultancies/MSPs that answer for
them):

1. **Confluence** — the default home of security policy in mid-market B2B. Highest
   priority, and the best-behaved: structured pages, stable URLs, a REST API with
   incremental change feeds (page version numbers).
2. **SharePoint** — the second most common home, especially in enterprise and
   regulated buyers. Worse-behaved (document libraries, versions, permissions),
   but the volume is real.
3. **Google Drive** — common in smaller teams; the API is clean but folder/permission
   semantics are messier than Confluence's.
4. **Notion** — growing, but the docs that feed security questionnaires are less
   often primary in Notion; lower priority until demand data says otherwise.
5. **Everything else** (generic wikis, GitHub wikis, internal knowledge bases) —
   long tail; only worth a connector when a buyer explicitly needs it.

Rationale for the order: it mirrors where the evidence actually lives, and the
first connector doubles as the reference implementation the others imitate (the
project has never built a connector, so building the cleanest one first is how the
shared design gets validated before it is copied into the messier ones).

## The incremental-sync design, given what already exists

Two existing pieces of the pipeline make connectors substantially cheaper than they
look, and the design must lean on both:

- **Chunk IDs are source-relative.** An ingested chunk's id is
  `<source_key>::<index>` where source_key is the path relative to the evidence
  directory. A connector is just a different producer of source_keys — e.g.
  `confluence/SPACE/PAGE-ID` — so chunk identity, deduplication, and the answer
  library's source-hash staleness checks all work unchanged.
- **Delete-by-source already exists.** `ingest_evidence` deletes every row and
  embedding for a source before re-inserting its fresh version. The hard part of
  incremental sync — "the document was deleted or shortened upstream; its stale
  chunks must not keep being cited" — is already solved for any source whose
  content can be re-fetched. The connector's job shrinks to: fetch the current
  state of a source, and call the existing ingest path with it.

The design, in one paragraph: a per-source `sync()` that (a) lists what the
upstream has (pages/documents plus their version or modified timestamps), (b)
compares against the local snapshot of what was last ingested per source_key, (c)
re-ingests changed sources through the existing `ingest_evidence` path, and (d)
deletes sources that no longer exist upstream. The snapshot table is a new
`source_sync_state` (source_key, upstream_version, last_synced_at) — the only new
persistence. Polling cadence is a config knob; the honest default is "on demand
before a run, plus a manual `qresp sync`", not a daemon.

## How document permissions must be respected

A connector runs with a service account that holds some view permission on some
documents. Two rules, non-negotiable:

1. **Ingest only what the sync account can read.** The evidence base must never
   contain a document whose existence the operator cannot prove the account was
   authorized to see — a leaked unauthenticated page in an evidence set is a
   worse liability than a missing one. Sync logs must record the permission
   context per source so a reviewer can audit it.
2. **Permission changes propagate like content changes.** If a page's ACL changes
   so the account can no longer read it, that is a delete (stale chunks must go),
   not a skip. The delete-by-source path handles it; the connector just has to
   detect the transition and log it as an event, not a silence.

## What would have to be true before it's worth building

1. **A real user with a real upstream.** The first connector is only worth
   building when a buyer (or a plausible prospect in a scoped pilot) actually has
   a Confluence/SharePoint/Drive they need synced — the project's rule is measured
   demand, not speculative connectors.
2. **A validator for connector-sourced evidence.** Local files can be eyeballed;
   a synced Confluence space cannot. Before the first connector ships, the sync
   path needs the same re-ingest correctness guarantee as local ingest (source
   snapshot + hash verification, already in place for local files) verified
   against a real space with a real permission matrix.
3. **A decision on the export-vs-connect boundary.** Some buyers will always
   prefer a weekly curated export (controlled, reviewable, air-gapped); the
   connector must demonstrably beat that workflow's total cost, not just its
   setup friction.

Until those three are true, this stays a roadmap page. That is the judgment call,
and it is deliberate.

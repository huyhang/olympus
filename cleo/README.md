# Cleo

New installation? Follow the [getting-started guide](doc/getting-started.md).

A librarian agent for Nineveh (API contract in the `contracts` directory).
Cleo answers questions about what is in the library, driven by a small
language model running locally. It runs inside [Olympus](../README.md), the
terminal home for this repository's agents, as an installed agent type.

Named for Clio, the Muse of history — conventionally depicted holding the
scrolls, which is roughly the job.

> **Status:** the first read-only local MVP is implemented. Ingest remains a
> future capability and is deliberately absent from the model's tool surface.

## What it is for

Three kinds of request, taken from how the library actually gets used.

**"What's the latest Vinland Saga volume I have?"**
The series can be named loosely, and by either the folder name on disk or the
title MangaBaka matched to it. Cleo resolves the name, then reports what is
actually held — volume count, the latest one, total size — alongside the
provider's own totals, so *"am I missing anything?"* is answerable without
leaving the house.

**"How many series do I have from this author? Did this one finish?"**
Answered from metadata Nineveh has already fetched and an administrator has
already reviewed. Cleo never calls MangaBaka itself: no rate-limit budget is
spent, and the answer matches what the catalog shows.

**Future: "I have this file — put it where it belongs."**
Cleo works out which series a downloaded volume belongs to, proposes a
filename consistent with the ones already there (`vinland_saga_v12.cbz` →
`Vinland Saga 012.cbz`), and shows exactly where it would land. It is placed
only after that proposal is accepted.

The front end is Olympus: a local, full-screen terminal application shared by
every agent. A remote front end over Tailscale remains a later step.

## Relationship with Nineveh and Olympus

**Nineveh owns everything that matters.** The catalog, the metadata, the files
on disk, and the authorization. Cleo holds no library state of its own — it is
a client, and a deliberately narrow one. **Olympus owns the rest**: the
interface, each Cleo instance's settings and credentials, and the
conversation history.

```
Olympus TUI ────► Cleo (harness + tools) ─► Nineveh /api/v1/librarian/*
                          │                        │
                     local model                catalog, metadata,
                      (Ollama)                     authorization
```

Cleo plugs into Olympus through `cleo.provider.CleoProvider`, registered under
the `olympus.agents` entry-point group. It ships inside the repository's
single distribution, `olympus-agents`, rather than as a package of its own;
`cleo/pyproject.toml` holds only its test configuration. It declares its settings (a Nineveh
URL and a secret token), validates and probes them, and opens a runtime for
each configured instance. Cleo contains no interface code.

The division with Nineveh is not a matter of taste:

- **Nineveh enforces permissions; Cleo's prompt does not.** A token carries an
  explicit set of capabilities and may be restricted to particular libraries.
  A request outside that grant is refused with `403` no matter what the model
  was persuaded to attempt. Cleo cannot widen its own access.
- **The current Cleo cannot write through Nineveh.** Its tool registry contains
  no ingest operation, and its token should have no ingest capability. The
  broader vendored contract includes discarding a staged ingest, but that route
  is never exposed to this librarian.
- **Nineveh records what happened.** Every read, proposal, placement and
  refusal is logged with the capabilities in force at the time, visible under
  **Admin → Librarian**. Cleo is auditable from the outside rather than on its
  own word.

### Capabilities

| Capability | Allows |
|---|---|
| `catalog:read` | Find series by title, list the volumes on disk |
| `metadata:read` | Answer author, status and publication questions |
| `ingest:stage` | Validate and stage a volume — writes nothing to the library |
| `ingest:commit` | Place a staged volume on disk |

Each Cleo instance's token should carry only `catalog:read` and
`metadata:read`. Its compiled tool registry contains four operations: list
libraries, search series by name, find series by author, and read one series.
The ingest operations in the vendored contract are not loaded dynamically and
cannot be selected by the model.

For the future ingest workflow, staging and placing are **separate
capabilities**. The intended deployment uses two tokens:

- **the phone** — `catalog:read`, `metadata:read`, `ingest:stage`
- **the desk** — the same, plus `ingest:commit`

So a volume can be proposed from anywhere and placed only where you are.
`GET /api/v1/librarian/ingest` lists what is waiting for approval.

## The vendored contract

`contracts/librarian-openapi.json` is the agent-facing slice of Nineveh's
OpenAPI document: eight operations and the schemas they reference, and
nothing else. `contracts/SOURCE` records the commit it came from and its
sha256.

Every JSON response is described by a named schema, and the operations declare
the `LibrarianToken` bearer scheme rather than an `authorization` header
parameter, so a generated client asks for the `nvh_` token instead of a raw
header. No schema sets `additionalProperties: false`, so a field Nineveh adds
later is ignored rather than rejected.

It is vendored rather than fetched so builds are reproducible offline and a
contract change arrives as a reviewable diff. Deliberately **not** the whole
`openapi.json` — that describes 60 operations, and every unrelated Nineveh
change would churn this repo and bury the signal that matters.

Refresh it, and the provenance beside it, from this directory with:

```sh
scripts/refresh-contract.sh            # assumes a nineveh checkout beside this repo
scripts/refresh-contract.sh /path/to/nineveh
git diff contracts/                    # the diff is the point — read it
```

`tests/test_contract.py` guards both halves: that the file matches its recorded
hash, carries no dangling `$ref` and asks for a token on every operation, and —
when a Nineveh is reachable — that its paths, and the schemas and auth scheme
they reference, still match the running server. The live check *skips* rather
than fails when Nineveh is down, so it can run on every local `pytest`:

```sh
NINEVEH_URL=http://nineveh.your-tailnet.ts.net .venv/bin/python -m pytest cleo/tests
```

Pin on the sha256, not on `info.version`: the version tracks Nineveh's package
and bumps on releases that leave these eight operations untouched.

## Credentials

Cleo authenticates to Nineveh with a bearer token issued under
**Admin → Librarian**. Nineveh shows it once and stores only a hash, so a lost
token is reissued rather than recovered.

Enter it when adding a Cleo in Olympus (**＋ Add → Cleo**). Olympus stores it
in macOS Keychain, falling back to an owner-only (`0600`) file in its data
directory only when Keychain cannot keep it; the agent manager shows which.
Editing an agent leaves the stored token in place unless a new one is typed.
For automation, `OLYMPUS_SECRET_<AGENT_ID>_NINEVEH_TOKEN` supplies one
instance's token without storing it (see the Olympus README).

Every instance uses its own token: several Cleos can point at different
libraries with differently scoped credentials. The standalone app's
`NINEVEH_TOKEN` variable and `cleo/.env` file are no longer read.

The runtime settings object redacts the token from `repr()`, so it never
reaches a traceback or log line.

**Grant only what the deployment needs.** This iteration needs only
`catalog:read` and `metadata:read`. Omitting both ingest capabilities makes
Nineveh independently enforce the same read-only boundary as Cleo.

## Using Cleo in Olympus

Choose Cleo in the sidebar, or press `Ctrl+G` and pick it, and ask. The
interface streams the answer, shows when Nineveh is being queried, and
attaches expandable evidence to factual responses. `Esc` cancels a response.
The full set of keys is in the Olympus README.

When a title matches several series and Nineveh reports no confident match,
Cleo stops and shows a numbered list; reply with a number. The choice is made
by the interface, not the model, so a loose name can never resolve to the
wrong series silently. A single confident match is used without asking. The
list waits for you even if you visit another agent first.

Local slash commands are parsed by Olympus before input reaches the model:

```text
/new                       start a conversation
/history [search]          search and resume history
/open ID                   resume by displayed ID prefix
/agent NUMBER|NAME         switch to another agent
/name NAME                 rename this agent
/persona [instructions]    edit its presentation preferences
/style compact|detailed    choose the default answer depth
/export [ID]               write a new Markdown transcript
/clear [ID|all]            clear history after confirmation
/help                      show command help
```

History lives in Olympus's database (`olympus --data-dir`), which is
owner-only, as are its exports. History has no automatic expiration. Export
uses a new filename and refuses to overwrite an existing file.

The name and persona are customizable per instance. Catalog grounding,
read-only behavior, and tool restrictions are an immutable part of the system
prompt and are also enforced by code. Model output is never interpreted as a
command. The model has no shell, subprocess, filesystem, generic HTTP, or
ingest tool; an unknown tool request is rejected. Clearing history is separate
trusted UI code, requires confirmation, and can affect only that agent's rows
in Olympus's database.

Two boundaries are worth naming because they are where model output meets
something that would otherwise act on it:

- **Series IDs are percent-encoded before they reach a URL.** The ID comes from
  the model, so an invented one must stay a single path segment rather than
  escaping `/api/v1/librarian/series/` into some other endpoint.
- **Terminal escape sequences are stripped whole from catalog and model text**
  before it is rendered, stored, or exported. Titles and filenames come off
  downloaded volumes; an escape sequence in one is removed, never executed,
  and leaves no `[31m` behind. A sequence split across streamed model tokens
  is held back until complete rather than shown in pieces.

Catalog facts must come from a Nineveh read *in the current conversation*. A
follow-up may reuse what an earlier answer already established — asking "who
wrote it?" straight after a lookup does not force a second one — but the first
claim in a conversation always requires a tool result.

## Development

Work from the repository root with its single environment; `pytest` there runs
the Olympus and Cleo suites together:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest
.venv/bin/ruff check src cleo/src tests cleo/tests cleo/scripts
```

Cleo's suite also runs on its own, holding the package to its own 90%
coverage floor:

```sh
cd cleo && ../.venv/bin/python -m pytest
```

No ruff configuration on purpose — Nineveh has none either, and ruff's defaults
already cover the rule set both repositories are checked against. Adding config
here would let the two drift. Lint from the repository root: import sorting
classifies packages by where ruff runs, and the root is the reference.

### Trying Olympus without Ollama or Nineveh

From the repository root:

```sh
.venv/bin/python cleo/scripts/fake_backends.py --launch
```

This runs the real Olympus TUI with a preconfigured Cleo against scripted
stand-ins for both services on one local port, with throwaway state; tokens
never reach Keychain. The identity bar reads `fake:demo`, so a fake session is
never mistaken for a real one. Both fakes are needed: an answer no Nineveh
read backs is replaced before it reaches the screen, so a fake model alone
would only ever produce the refusal.

`scripts/scenarios/demo.json` scripts a reply for each state the interface can
be in. Mention its keywords in a question: `vinland` for a streamed answer with
evidence, `saga` for the numbered pick list, `urasawa` for an author search,
`libraries` for the library listing, `slow` for a reply you can cancel with
Esc (or leave running while you switch agents), `berserk` for a Nineveh `403`,
`delete` for a refused tool call, `broken`, `crash` and `garbled` for Ollama
failures, `markdown` for rich formatting, and `ansi` for escape-code
stripping. Anything else gets the no-lookup refusal.

Run it without `--launch` to see each request logged as it arrives, and point
Olympus at it from another terminal using the command it prints. A different
scenario file can be passed as an argument; the file format is described at
the top of the script.

```
contracts/    the vendored API slice and its provenance
doc/          the getting-started guide
scripts/      re-vendoring, and fake backends for trying the TUI
src/cleo/     agent loop, read-only adapters, tools, and the Olympus provider
tests/        unit, adapter, provider, and contract tests
```

The root `pyproject.toml` sets `requires-python` to `>=3.12` to match
Nineveh's CI, though the local venv is whatever `python3` resolves to. **Never copy a `.venv` between checkouts**: the
editable install records an absolute path, so a copied environment silently
imports the other project's source. Recreate it, then confirm:

```sh
.venv/bin/python -c "import cleo; print(cleo.__file__)"   # must be under ./cleo/src
```

## Implemented shape

- **Model:** `granite4.2:8b` through Ollama's streaming chat API by default,
  with thinking explicitly disabled — it costs several seconds a turn and Cleo
  discards it. Olympus's defaults or a per-instance override select another,
  and the interface shows which one is answering.
- **Harness:** a purpose-built, bounded tool loop. A conversation with no
  successful Nineveh read behind it gets a fixed safe response instead of an
  answer. Ambiguity is resolved by the interface, never by the model.
- **Catalog adapter:** a typed client with only three fixed `GET` routes behind
  four clearly named model tools. The model supplies search fields or a series
  ID, never a method, host, or URL. When Nineveh explains a refusal, that
  explanation is passed through rather than flattened to a status code.
- **Provider:** validates the Nineveh URL and token, probes Nineveh and Ollama
  on request, and opens one runtime per configured instance.
- **Persistence and interface:** owned by Olympus — per-agent searchable
  transcripts, resumption, explicit clearing, preferences, and Markdown export.
  An answer is filed against the conversation it was asked in, even if you
  have moved on to another conversation or agent before it finished.

Ollama and Nineveh are the only network peers. Remote access and the future
ingest workflow are intentionally outside this iteration.

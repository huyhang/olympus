# Cleo

A librarian agent for Nineveh (API contract in the `contracts` directory.
Cleo answers questions about what is in the library and files new volumes
into it, driven by a small language model running locally.

Named for Clio, the Muse of history — conventionally depicted holding the
scrolls, which is roughly the job.

> **Status:** the API contract is vendored and verified. Nothing else is built
> yet.

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

**"I have this file — put it where it belongs."**
Cleo works out which series a downloaded volume belongs to, proposes a
filename consistent with the ones already there (`vinland_saga_v12.cbz` →
`Vinland Saga 012.cbz`), and shows exactly where it would land. It is placed
only after that proposal is accepted.

The intended front end is an iPhone over Tailscale: ask from anywhere, with
writes deliberately harder to reach than reads.

## Relationship with Nineveh

**Nineveh owns everything that matters.** The catalog, the metadata, the files
on disk, and the authorization. Cleo holds no library state of its own — it is
a client, and a deliberately narrow one.

```
iPhone ─ Tailscale ─► Cleo (harness + tools) ─► Nineveh /api/v1/librarian/*
                              │                        │
                         local model                catalog, metadata,
                          (Ollama)                  files, authorization
```

The division is not a matter of taste:

- **Nineveh enforces permissions; Cleo's prompt does not.** A token carries an
  explicit set of capabilities and may be restricted to particular libraries.
  A request outside that grant is refused with `403` no matter what the model
  was persuaded to attempt. Cleo cannot widen its own access.
- **Nineveh cannot overwrite or delete on Cleo's behalf.** There is no endpoint
  for it. Placing a volume fails outright if the name is taken; the agent API
  exposes no move, rename, or delete at all.
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

Staging and placing are **separate capabilities**, which is what makes the
phone safe. Two tokens:

- **the phone** — `catalog:read`, `metadata:read`, `ingest:stage`
- **the desk** — the same, plus `ingest:commit`

So a volume can be proposed from anywhere and placed only where you are.
`GET /api/v1/librarian/ingest` lists what is waiting for approval.

## The vendored contract

`contracts/librarian-openapi.json` is the agent-facing slice of Nineveh's
OpenAPI document: eight operations and the four schemas they reference, and
nothing else. `contracts/SOURCE` records the commit it came from and its
sha256.

It is vendored rather than fetched so builds are reproducible offline and a
contract change arrives as a reviewable diff. Deliberately **not** the whole
`openapi.json` — that describes 56 operations, and every unrelated Nineveh
change would churn this repo and bury the signal that matters.

Refresh it, and the provenance beside it, with:

```sh
scripts/refresh-contract.sh            # assumes ../../nineveh
scripts/refresh-contract.sh /path/to/nineveh
git diff contracts/                    # the diff is the point — read it
```

`tests/test_contract.py` guards both halves: that the file matches its recorded
hash and carries no dangling `$ref`, and — when a Nineveh is reachable — that
it still matches the running server. The live check *skips* rather than fails
when Nineveh is down, so it can run on every local `pytest`.

```sh
pytest tests/                          # needs httpx and pytest
NINEVEH_URL=http://nineveh.your-tailnet.ts.net pytest tests/
```

Pin on the sha256, not on `info.version`: the version tracks Nineveh's package
and bumps on releases that leave these eight operations untouched.

## Credentials

Cleo authenticates to Nineveh with a bearer token issued under
**Admin → Librarian**. Nineveh shows it once and stores only a hash, so a lost
token is reissued rather than recovered.

```sh
cp .env.example .env && chmod 600 .env
$EDITOR .env                      # paste NINEVEH_TOKEN, set NINEVEH_URL
```

`.env` is gitignored; `.env.example` is the committed template and carries no
value. Real environment variables override the file, so a container or CI job
can supply the token without one.

```python
from cleo.config import Settings

settings = Settings.load()
httpx.get(f"{settings.nineveh_url}/api/v1/librarian/libraries",
          headers=settings.authorization)
```

Three things the loader does on purpose:

- **Refuses a token file other accounts can read**, with the `chmod` to fix it.
  A secret readable by every account on the NAS is not one. The check applies
  only to a file that actually holds a token, so the template stays readable.
- **Redacts the token from `repr()`.** The default dataclass repr would put it
  into every traceback and log line that touched the object.
- **Says what to do when it is missing**, rather than failing with a `KeyError`
  three frames deep.

**Grant only what the deployment needs.** A Cleo that proposes uploads but must
not place them gets a token without `ingest:commit`; the refusal is then
enforced by Nineveh rather than by remembering not to call the endpoint.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest
ruff check src tests && ruff format --check src tests
```

No ruff configuration on purpose — Nineveh has none either, and ruff's defaults
already cover the rule set both repositories are checked against. Adding config
here would let the two drift.

```
contracts/    the vendored API slice and its provenance
scripts/      re-vendoring
src/cleo/     the agent (empty)
tests/        contract guards
```

`requires-python` is `>=3.12` to match Nineveh's CI, though the local venv is
whatever `python3` resolves to. **Never copy a `.venv` between checkouts**: the
editable install records an absolute path, so a copied environment silently
imports the other project's source. Recreate it, then confirm:

```sh
.venv/bin/python -c "import cleo; print(cleo.__file__)"   # must be under ./src
```

## Planned shape

Not built yet; recorded so the decisions do not have to be made twice.

- **Model:** `granite4.2:8b` via Ollama on a MacBookPro M1 Pro/Max — small, Apache 
  2.0, tuned for tool use. `ornith-1.5:35b` is the fallback if it's the M1 Max 
  and if it proves unreliable; it measured 100% on tool-calling benchmarks and 
  only ~3B parameters activate per token, so it stays fast despite its size.
- **Harness:** written here rather than adopted. A stock agentic harness ships
  with a shell tool that would then have to be disabled; a purpose-built loop
  simply never has one. The tool surface is four distinctly-named tools, because
  small models pick badly between endpoints that sound alike.
- **Tool server:** a separate process whose only permitted egress is Nineveh.
  The harness reaches Ollama; the tool server reaches Nineveh; neither needs the
  other's network.
- **Transport:** Tailscale, with the service bound to loopback and exposed via
  `tailscale serve`. Nothing listens on a routable address.


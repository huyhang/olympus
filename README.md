# Olympus

Olympus is a terminal home for local Ollama agents. It owns the user
interface, agent configuration, credentials, conversation history, and runtime
lifecycle; each installed agent contributes only its capabilities and the
settings it needs.

The first installed agent is **Cleo**, a read-only librarian for a Nineveh
catalog (see [`cleo/README.md`](cleo/README.md)).

## Install on macOS

Install once from this checkout with
[pipx](https://pipx.pypa.io/stable/installation/):

```sh
brew install pipx
pipx ensurepath
pipx install /absolute/path/to/olympus
```

Always install from the checkout path. The package is named
`olympus-agents` because PyPI's `olympus` is an unrelated project, so a bare
`pipx install olympus` would fetch something else entirely. If you installed
an earlier build under the name `olympus`, run `pipx uninstall olympus` first;
otherwise the old install keeps the `olympus` command. Your data is not
affected either way.

The `olympus` command then works from any directory:

```sh
olympus               # launch the TUI
olympus --version
olympus --data-dir    # show where your data lives
```

## First run

Olympus starts empty. Choose **＋ Add** (or press `Ctrl+A`), pick an agent
type, and fill in its settings. **Test connection** checks them without
closing the form. For Cleo that means a Nineveh URL and a librarian token; see
[`cleo/doc/getting-started.md`](cleo/doc/getting-started.md).

The Ollama server and model default to `http://localhost:11434` and
`granite4.2:8b`. Change them once under **Agents → Defaults**; any agent can
override either value. `OLLAMA_URL` and `OLYMPUS_MODEL` in the environment
override the saved defaults for that session only.

You can add several instances of one type, for example two Cleos pointed at
different libraries or running different models.

## Using Olympus

The agent sidebar sits beside the chat. Each agent keeps its own open
conversation and unsent draft while you are elsewhere, and a reply keeps
streaming in the background: switch back to watch it finish, or wait for the
"finished replying" notice.

| Key | Action |
|---|---|
| `Enter` / `Shift+Enter` or `Ctrl+J` | send / new line |
| `Esc` | cancel the reply on screen |
| `Ctrl+K` | command palette: switch agent, open a recent chat, and more |
| `Ctrl+G`, then `↑` `↓` `Enter` | switch agent from the keyboard |
| `Ctrl+A` | manage agents: add, edit, remove, restore, defaults |
| `Ctrl+N` / `Ctrl+R` | new conversation / conversation history |
| `Ctrl+S` | name, persona, and answer style of the current agent |
| `Ctrl+B` | show or hide the sidebar (hidden below 88 columns) |
| `Ctrl+↑` / `Ctrl+↓` | recall earlier input |
| `Ctrl+Q` | quit |

`Ctrl+1` to `Ctrl+5` jump straight to an agent, but only in terminals that
report Ctrl with number keys, such as kitty with its keyboard protocol.
Terminal.app sends them as plain digits; use `Ctrl+G` or `/agent NUMBER`
there. Likewise `Ctrl+H` opens history only where the terminal sends it as
its own key rather than as Backspace.

Type `/help` for local commands such as `/agent 2`, `/history vinland`,
`/export`, and `/clear`. They are handled by Olympus and never reach an agent.

## Where things live

On macOS everything is under `~/Library/Application Support/Olympus`
(`$XDG_DATA_HOME/olympus` or `~/.local/share/olympus` elsewhere);
`OLYMPUS_STATE_DIR` chooses another location.

| What | Where |
|---|---|
| Agents, conversations, defaults | `olympus.sqlite3`, owner-only (`0600`) |
| Credentials | macOS Keychain (service `dev.olympus.agents`) |
| Credential fallback | `secrets.json` (`0600`), used only when Keychain cannot keep a value |
| Exports | `exports/<agent id>/`, never overwritten |
| Pre-upgrade backups | `backups/` |

The agent manager shows where each agent's token is stored. For automation, a
token can be supplied per agent as `OLYMPUS_SECRET_<AGENT_ID>_<KEY>`, with the
ID (shown in the agent's edit form) upper-cased and dashes turned into
underscores, for example `OLYMPUS_SECRET_1B2C…_NINEVEH_TOKEN`. A value given
this way is never written anywhere.

Removing an agent offers two choices: **Keep history** archives it, restorable
later, and **Delete everything** erases its conversations and credentials.
Data from the former standalone Cleo app (`~/.local/share/cleo`) is neither
imported nor removed.

## Upgrade and uninstall

```sh
git pull --ff-only
pipx reinstall olympus-agents    # new code; data and credentials are untouched
pipx uninstall olympus-agents    # removes the app only
```

Use `reinstall`, not `upgrade`: for a package installed from a local path,
`pipx upgrade` leaves the installed code as it was.

Application code and user data never share a directory. When a new version
changes the database schema, Olympus first copies the database into
`backups/`, then migrates it one version at a time, each step in a single
transaction, so an interrupted upgrade leaves the previous schema intact.

## Agent types

Agent types are discovered through the `olympus.agents` Python entry-point
group. A trusted agent package can be installed into the same isolated
environment with `pipx inject olympus-agents /path/to/agent-package`; Olympus
itself never downloads code. Such a package should depend on `olympus-agents`,
which ships the `olympus` contracts it implements (Cleo is bundled in it). A plugin that fails to load is reported at startup
while the others keep working, and agents of a type that is no longer
installed stay visible in the manager so they can be removed.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest
.venv/bin/ruff check src cleo/src tests cleo/tests cleo/scripts
.venv/bin/ruff format --check src cleo/src tests cleo/tests cleo/scripts
```

Run lint from the repository root. The suite holds itself to 90% coverage;
Cleo's own suite also runs from `cleo/` (see its README).

Run the complete TUI against scripted Ollama and Nineveh stand-ins, with
throwaway state and no Keychain writes:

```sh
.venv/bin/python cleo/scripts/fake_backends.py --launch
```

The identity bar reads `fake:demo`, so a fake session is never mistaken for a
real one. Ask about `vinland`, `saga`, or `slow`; the full keyword list is in
[`cleo/README.md`](cleo/README.md#trying-olympus-without-ollama-or-nineveh).

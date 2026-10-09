# Getting started with Cleo

Cleo is a local librarian for a Nineveh catalog. It uses an Ollama
model (`granite4.2:8b` by default) to understand questions and Nineveh's API
as the only source of catalog facts. It runs inside Olympus, the terminal
interface at the repository root, which owns its settings, credentials, and
conversation history.

## 1. Check the prerequisites

You need:

- macOS with Python 3.12 or newer, and [pipx](https://pipx.pypa.io/stable/installation/)
- A running Nineveh server
- A Nineveh librarian token with `catalog:read` and `metadata:read`
- A running Ollama service with `granite4.2:8b` installed

Check Python and Ollama:

```sh
python3 --version
ollama list
```

If `granite4.2:8b` is not listed, install it:

```sh
ollama pull granite4.2:8b
```

Grant `ingest:stage` or `ingest:commit` only if this Cleo should file volumes
(step 7). Answering questions needs neither.

## 2. Install Olympus

From the repository root:

```sh
pipx install "$PWD"
```

This installs the `olympus-agents` package and puts the `olympus` command on
your `PATH`; it runs from any directory. Install from the checkout like this
rather than by name: PyPI's `olympus` is an unrelated project. If an earlier
build is installed as `olympus`, run `pipx uninstall olympus` first.

To upgrade later, `git pull --ff-only` and then
`pipx reinstall olympus-agents` (`pipx upgrade` does not pick up changes from a
local checkout). Your agents, conversations, and credentials live outside the
installed code and survive both.

## 3. Create a read-only Nineveh credential

In Nineveh, open **Admin → Librarian** and issue a token containing only:

- `catalog:read`
- `metadata:read`

Nineveh displays the token once. If it is lost, issue a replacement instead of
trying to recover it.

## 4. Add Cleo in Olympus

Launch Olympus:

```sh
olympus
```

It starts empty. Choose **＋ Add** (or press `Ctrl+A`, then `a`), select
**Cleo**, and fill in:

- **Nineveh URL** — for example `http://localhost:8080`, or the Tailscale name
  when Nineveh runs elsewhere
- **Nineveh token** — the token from step 3

Leave the Ollama URL and model blank to use Olympus's defaults
(`http://localhost:11434` and `granite4.2:8b`), or fill them in for this Cleo
only. Choose **Test connection** to check Nineveh and Ollama without leaving
the form, then **Save agent**.

The token goes to macOS Keychain. If Keychain cannot keep it, Olympus stores
it in an owner-only file in its data directory instead; the agent manager
shows which.

Add more Cleos the same way when different libraries, credentials, or models
should stay separate.

## 5. Ask a question

Try a catalog question such as:

```text
What's the latest Vinland Saga volume I have?
```

Cleo shows its current activity and attaches expandable Nineveh evidence to
the answer.

If the name matches several series and Nineveh cannot pick one, Cleo stops and
lists them:

```text
Several series match that name. Which one do you mean?

1. 7thGARDEN
2. Hoshin Engi

Reply with a number.
```

Type the number. Typing anything else abandons the list and is treated as a
new question.

## 6. Learn the controls

- Enter sends a message; Shift+Enter or Ctrl+J inserts a newline.
- Esc cancels the response on screen.
- Ctrl+K opens the command palette: switch agents, open recent conversations.
- Ctrl+G moves to the agent list; arrows and Enter switch agents.
- Ctrl+A manages agents: add, edit, remove, restore, and Ollama defaults.
- Ctrl+N starts a new conversation; Ctrl+R opens searchable history.
- Ctrl+S edits this agent's name, persona, and answer style.
- Ctrl+Q exits Olympus.

Switching agents keeps each one's open conversation and unsent text, and a
reply in progress keeps running; Olympus tells you when it finishes.

The interface also supports these local commands:

```text
/new                       start a conversation
/history [search]          search and resume history
/open ID                   resume by displayed ID prefix
/agent NUMBER|NAME         switch to another agent
/name NAME                 rename this agent
/persona [instructions]    edit presentation preferences
/style compact|detailed    choose the default answer depth
/export [ID]               export a Markdown transcript
/clear [ID|all]            clear history after confirmation
/file [FOLDER]             file volumes from a folder (filing on)
/help                      display command help
```

Local commands are handled by Olympus and are never sent to the language
model. Clearing history requires confirmation and can delete only that
agent's records in Olympus's own database.

## 7. File new volumes (optional)

To let Cleo file downloaded volumes into the library:

1. In Nineveh, issue a token that also carries `ingest:stage`, plus
   `ingest:commit` if volumes should be placed from this machine.
2. In Olympus, edit the Cleo (`Ctrl+A`, then `e`), turn on **Allow filing
   volumes from a folder**, enter the new token, and choose **Test
   connection**.
3. Press `Ctrl+O`, or type `/file ~/Downloads`, pick the folder, and start the
   review.

Cleo matches each `.cbz` to a series, uploads it to Nineveh's holding area,
and shows where it would land under which name. Press `Enter` to place a
volume, `r` to rename it first, `c` to pick another series, `s` to skip it,
or `A` to place everything that is ready without a warning. Leaving the
board, or quitting Olympus, withdraws the uploads you did not decide on,
including any still in progress, and leaving files a summary in the
conversation.

Uploads already waiting in Nineveh's queue, for example ones staged from a
phone, are listed under *From before this review*: `Enter` places one, `w`
withdraws it, and leaving keeps the rest. On a slow connection, turn off
**Upload every volume as soon as the board opens** in the Cleo's settings;
Cleo then uploads each volume only once you move to it on the board, or
place it.

## Data and upgrades

On macOS, persistent state lives in:

```text
~/Library/Application Support/Olympus
```

`olympus --data-dir` prints the location. It is outside pipx's managed
environment, so `pipx reinstall olympus-agents` replaces the application without
touching conversations or configuration. Before a new version changes the
database schema, Olympus writes a private backup, then migrates in a single
transaction per version.

This is intentionally a clean break from the former standalone Cleo app.
Olympus neither imports nor removes `~/.local/share/cleo`, and no longer reads
`cleo/.env` or `NINEVEH_TOKEN`.

## Test without Ollama or Nineveh

From a development environment installed with `pip install -e '.[test]'` at
the repository root:

```sh
.venv/bin/python cleo/scripts/fake_backends.py --launch
```

The harness configures a throwaway Cleo and starts the real Olympus TUI
against scripted Ollama and Nineveh stand-ins. The scripted keywords are
listed in the [Cleo README](../README.md#trying-olympus-without-ollama-or-nineveh).

## Troubleshooting

### Saving the agent says a token is required

Paste the Nineveh token into the form. When editing an existing Cleo, leaving
the token blank keeps the stored one.

### Saving says a URL is not valid

Olympus checks URLs when you save, so a typo such as a letter in the port is
caught before the first question. URLs need `http://` or `https://` and a
host.

### Ollama is unavailable

Check that Ollama is running and that the configured model exists:

```sh
ollama list
```

Also confirm the Ollama URL under **Agents → Defaults**, or the agent's own
override. `OLLAMA_URL` in the environment overrides the saved default for that
session; the Defaults screen says when it does. **Test connection** reports
which service failed.

### Nineveh is unavailable or denies access

Confirm that the Nineveh URL is reachable and that the token is current. A
`403` usually means the token lacks access to the requested library or one of
the two read capabilities.

### Removing Cleo reports a Keychain problem

The agent and its history are removed either way. If Keychain refuses to
delete the token, remove the `dev.olympus.agents` entry for it in Keychain
Access.

### History is not on Ctrl+H

Many terminals send Ctrl+H as Backspace, so Olympus uses Ctrl+R for history.
Ctrl+H still works in terminals that send it as its own key.

### Filing says the token cannot upload volumes

The token lacks `ingest:stage`. Issue one that has it, or turn filing off.

### A volume says it is awaiting approval

The token may stage but not place volumes (`ingest:commit`). The upload waits
in Nineveh's queue under **Admin → Librarian** for a token that may place it.

### Cleo declines a request

In chat, Cleo supports catalog reads only. It will not browse the web, inspect
local files, run commands, or call Nineveh's ingest operations; filing happens
only on the review board you open, and only for what you approve. When Nineveh cannot
verify a catalog fact, Cleo reports that limitation instead of answering from
the model's general knowledge.

The first claim in a conversation always needs a Nineveh lookup. Once one has
succeeded, a follow-up may reuse it — "who wrote it?" straight after a search
is answered without searching again.

### Answers feel slow

Cleo disables the model's thinking mode, which is most of the difference.
If turns are still slow, the model is the cost: `granite4.2:8b` is the
default, and the Ollama defaults or the agent's own model setting can point at
anything Ollama serves. The interface names the model that is answering, so
you can confirm which one is loaded.

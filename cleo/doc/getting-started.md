# Getting started with Cleo

Cleo is a local, read-only librarian for a Nineveh catalog. It uses Ollama's
`granite4.2:8b` model to understand questions and Nineveh's API as the only
source of catalog facts.

## 1. Check the prerequisites

You need:

- Python 3.12 or newer
- A running Nineveh server
- A Nineveh librarian token with only `catalog:read` and `metadata:read`
- A running Ollama service with `granite4.2:8b` installed

From the Cleo repository, check Python and Ollama:

```sh
python3 --version
ollama list
```

If `granite4.2:8b` is not listed, install it:

```sh
ollama pull granite4.2:8b
```

Do not grant the Cleo token `ingest:stage` or `ingest:commit`. Cleo does not
need either permission.

## 2. Create the Python environment

Run these commands from the repository root:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
```

Confirm that Cleo imports from this checkout:

```sh
.venv/bin/python -c "import cleo; print(cleo.__file__)"
```

The printed path should end in this repository's `src/cleo/__init__.py`.

## 3. Create a read-only Nineveh credential

In Nineveh, open **Admin → Librarian** and issue a token containing only:

- `catalog:read`
- `metadata:read`

Nineveh displays the token once. If it is lost, issue a replacement instead of
trying to recover it.

## 4. Configure Cleo

Create a private configuration file from the template:

```sh
cp .env.example .env
chmod 600 .env
```

Edit `.env` and set the Nineveh address and token:

```dotenv
NINEVEH_URL=http://localhost:8080
NINEVEH_TOKEN=paste_the_token_here

OLLAMA_URL=http://localhost:11434
CLEO_MODEL=granite4.2:8b
```

Use the actual Nineveh address if it is not running locally. Do not commit or
share `.env`. Real environment variables override values in the file.

Conversation history defaults to `~/.local/share/cleo`. To use another private
location, add:

```dotenv
CLEO_STATE_DIR=/your/private/path/cleo
```

Cleo creates the state directory with mode `0700` and its database and exports
with mode `0600`.

## 5. Verify the installation

Run the automated checks:

```sh
.venv/bin/python -m pytest
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
```

A live contract check is included. It uses `http://localhost:8080` unless you
set `NINEVEH_URL` in the command environment, and skips automatically when
that server is unavailable:

```sh
NINEVEH_URL=http://your-nineveh-host:8080 .venv/bin/python -m pytest
```

## 6. Start Cleo

Ensure Nineveh and Ollama are running, then launch the terminal interface:

```sh
.venv/bin/cleo
```

Try a catalog question such as:

```text
What's the latest Vinland Saga volume I have?
```

Cleo will show its current activity and attach expandable Nineveh evidence to
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

## 7. Learn the controls

- Enter sends a message.
- Shift+Enter inserts a newline.
- Esc cancels the current response.
- Ctrl+N starts a new conversation.
- Ctrl+H opens searchable conversation history.
- Ctrl+S opens librarian settings.
- Ctrl+Q exits Cleo.

The interface also supports these local commands:

```text
/new                       start a conversation
/history [search]          search and resume history
/open ID                   resume by displayed ID prefix
/name NAME                 rename the librarian
/persona [instructions]    edit presentation preferences
/style compact|detailed    choose the default answer depth
/export [ID]               export a Markdown transcript
/clear [ID|all]            clear history after confirmation
/help                      display command help
```

Local commands are handled by the TUI and are never sent to the language
model. Clearing history requires confirmation and can delete only records in
Cleo's own conversation database.

## Troubleshooting

### Cleo says no token is configured

Confirm that `.env` contains `NINEVEH_TOKEN` and is located in the repository
root. Cleo will explain how to fix an absent token.

### Cleo refuses to read `.env`

The file is readable by another account. Restore private permissions:

```sh
chmod 600 .env
```

### Ollama is unavailable

Check that Ollama is running and that the configured model exists:

```sh
ollama list
```

Also confirm that `OLLAMA_URL` points to the running service.

### Nineveh is unavailable or denies access

Confirm that `NINEVEH_URL` is reachable and that the token is current. A `403`
usually means the token lacks access to the requested library or one of the two
read capabilities.

### Cleo declines a request

Cleo supports catalog reads only. It will not browse the web, inspect local
files, run commands, or call Nineveh's ingest operations. When Nineveh cannot
verify a catalog fact, Cleo reports that limitation instead of answering from
the model's general knowledge.

The first claim in a conversation always needs a Nineveh lookup. Once one has
succeeded, a follow-up may reuse it — "who wrote it?" straight after a search
is answered without searching again.

### Answers feel slow

Cleo disables the model's thinking mode, which is most of the difference.
If turns are still slow, the model is the cost: `granite4.2:8b` is the
default, and `CLEO_MODEL` can point at anything Ollama serves. The interface
names the model that is answering, so you can confirm which one is loaded.

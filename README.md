# 🌿 Plant Bot

Never forget to water (or rename) another plant again. Plant Bot is a
Telegram bot that helps you keep track of your houseplants — organized into
groups, searchable, editable with a couple of taps, or just by typing what
you want in plain language. A web admin panel rides along for anyone who'd
rather click through a browser than a chat.

**What it can do:**
- 📋 Keep plants organized into groups, with fuzzy search so typos don't matter
- 📥 Import a whole collection at once from CSV or a plain-text/markdown list
- 🤖 Talk to it naturally — "add alocasia polly, repotted in march" just works
- 🖥️ Manage everything from a browser too, via the built-in admin panel

---

## 💬 Using the bot

The main menu keeps things simple — three buttons:

| Button | What it does |
|---|---|
| 📋 List | Browse your plants and groups, edit or delete inline |
| ➕ Add | Add a new plant |
| 📥 Import | Bring in a list from a file or pasted text |

Plus two commands: `/start` (greeting and menu) and `/cancel_import` (bail
out of an import in progress). Everything else — deleting, renaming,
editing — happens either through the buttons in **📋 List**, or by just
typing what you want and letting the AI agent handle it.

## 📥 Import format

Got your plants written down somewhere already? Drop them in as **CSV**:

```csv
group,name,comment
Alocasias,Alocasia Polly,repotted in March
Alocasias,Alocasia Odora,
Succulents,Haworthia,
```

...or as plain **text / markdown** — handy if you asked another AI to help
list them out:

```
Alocasias:
- Alocasia Polly: repotted in March
- Alocasia Odora

Succulents:
- Haworthia
```

Either way, before anything is saved you'll get a preview showing which
groups are new and which already matched (case- and whitespace-insensitive),
so nothing gets duplicated by accident.

## 🤖 AI agent

Turn it on with `AI_ENABLED=true` and an `AI_API_KEY`, and Plant Bot
understands plain-language requests instead of just button taps. It works
with any OpenAI-compatible API — OpenAI, DeepSeek, OpenRouter, whatever you
point `AI_API_BASE_URL` and `AI_MODEL` at.

It understands things like:

- *"add alocasia polly, repotted in march"* → adds it to "Alocasias" with a
  comment
- *"delete haworthia"* → deletes it if the match is unambiguous, otherwise
  asks you to pick
- *"rename group succulents to cacti"* → renames the group
- *"show all alocasias"* → filtered list, right in the chat

## 🖥️ Web admin panel

Sometimes a mouse is just faster. The `admin` service is a small FastAPI +
Jinja2 app (no JS frameworks, nothing to build) that reads and writes the
exact same data as the bot — same tables, same source of truth.

From it you can browse every user's plants and groups, add/rename/delete
groups (with the option to move or delete their plants), edit any plant's
name, comment, or group, import/export CSV, set a caption for ungrouped
plants, and read the AI agent's request log across all users at `/ai-logs`.

Login is a simple form (`ADMIN_USER` / `ADMIN_PASSWORD` — **change the
defaults**) backed by a signed, httponly cookie session that lasts 7 days.

By default the port only listens on `127.0.0.1:8080` — nothing exposed to
the outside world. To actually reach it:
- tunnel in over SSH: `ssh -L 8080:localhost:8080 user@your-server`, then
  open `http://localhost:8080` locally, or
- put nginx/caddy with HTTPS in front of it — this panel can delete data, so
  it's worth not exposing plain HTTP directly.

(If you really want it open, swap `127.0.0.1:8080:8080` for `8080:8080` in
`docker-compose.yml` — just know what you're trading away.)

## 🗂️ Structure

<details>
<summary>Project tree</summary>

```
bot/
├── config.py
├── main.py
├── db/
│   ├── models.py            # User, Group, Plant, AiLog
│   ├── database.py          # engine/session (SQLite + WAL)
│   └── crud/                # get/list/create/update/delete per entity
├── services/
│   ├── plant_service.py
│   ├── group_service.py
│   ├── import_service.py    # CSV + markdown parsers, import preview
│   └── ai_service/          # intent from free-form text (prompt, client, cache)
├── handlers/
│   ├── list_view.py         # /start, "📋 List" button
│   ├── plants/              # "➕ Add" button, editing/removing plants
│   ├── groups.py            # renaming/deleting a group (with plant transfer)
│   ├── import_.py           # "📥 Import" button — file or text, preview
│   └── ai_agent/            # free-form text → intent → flow (add/delete/
│                             # delete_group/create_group/rename_group/edit_plant/list)
├── keyboards/
│   ├── reply.py             # main reply menu
│   └── inline.py
├── middlewares/
│   └── user.py              # injects user_id into handlers
└── utils/
    ├── chat.py               # safe edit/delete of messages in multi-step dialogs
    ├── fuzzy.py               # fuzzy search by name (groups and plants)
    └── text.py

admin/                        # web admin panel (FastAPI + Jinja2), separate container
├── main.py                   # app assembly, route registration
├── routes/                   # users, plants, groups, ai_logs, auth
├── auth.py                   # login form + signed cookie session
├── database.py                # its own connection to the same DB (without bot.config)
├── helpers.py
└── templates/

requirements/                  # bot, admin, and dev requirements kept separate
├── bot.txt
├── admin.txt
└── dev.txt                    # pytest, ruff, mypy — not needed at runtime

tests/                         # pytest, isolated in-memory SQLite per test
.github/workflows/ci.yml       # ruff + mypy + pytest on every push/PR to main
```

</details>

## 🚀 Getting it running

<details>
<summary>Running locally</summary>

```bash
git clone https://github.com/Redi38/plantsbot.git
cd plantsbot
python -m venv venv && source venv/bin/activate
pip install -r requirements/bot.txt

cp .env.example .env
# fill in BOT_TOKEN (required) and AI_API_KEY (if you want the AI agent)

python -m bot.main
```

</details>

<details>
<summary>Running in Docker</summary>

```bash
cp .env.example .env
# fill in .env

make up
# same as: docker compose up -d --build
```

The SQLite file lives in the named volume `plant_bot_data`, so your data
survives container rebuilds. `docker compose up` brings up two containers —
`bot` and `admin` — sharing that same volume.

</details>

<details>
<summary>Development</summary>

```bash
pip install -r requirements/bot.txt -r requirements/admin.txt -r requirements/dev.txt

make test        # pytest
make lint        # ruff check .
make typecheck    # mypy bot && mypy admin
make ci           # everything together — same as what CI runs on GitHub
```

Other commands — `make up` / `make down` / `make logs` / `make ps`, etc. —
see the `Makefile`.

</details>

<details>
<summary>Tests and CI</summary>

`make test` runs pytest against an isolated in-memory SQLite (no network, no
real AI API). `.github/workflows/ci.yml` runs three independent jobs on every
push/PR to `main`: `ruff check .`, `mypy` (separately for `bot` and `admin`),
and pytest itself.

</details>

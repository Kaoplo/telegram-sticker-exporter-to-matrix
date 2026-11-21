# Development guide

## Environment

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), clone the
repository, and run:

```sh
uv sync --locked
```

Use Python 3.12 or 3.13 to match CI. The source layout is documented in
[architecture](docs/architecture.md). Application entry points are
`uv run matrix-stickers` and `uv run python -m matrix_stickers`.
Install FFmpeg on `PATH` to run WebM conversion and its integration tests.
The provided Docker image and CI install it automatically.

## Checks

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python -m unittest discover -s tests -v
uv pip check
```

Apply formatting with `uv run ruff format .`. Tests use dummy settings,
`httpx.MockTransport`, fake Matrix storage, and mocked Telegram replies. They
never load `.env` or contact Telegram/Matrix. Test imports, name preservation,
duplicate handling, authorization, previews, room isolation and bulk cancellation when
changing library operations.
Shortcode changes must preserve the original Telegram identity; test ID collisions,
re-imports and reply-based commands after edits. Room-wide suffix updates must
preflight every pack and leave unrelated metadata intact.
Animation tests generate their own moving TGS and WebM fixtures and exercise
the real renderers; they do not download third-party sticker packs.

## Dependencies

Use uv rather than editing a requirements export:

```sh
uv add package-name
uv add --dev development-tool
uv lock --upgrade-package package-name
uv sync --locked
```

Commit `pyproject.toml` and `uv.lock` together when dependencies change. The
Dockerfile and CI currently pin uv to `0.13.9`; update both if upgrading uv.
Follow the official uv guides for [GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/)
and [Docker](https://docs.astral.sh/uv/guides/integration/docker/).

## Container

```sh
docker build -t matrix-stickers:dev .
```

The runtime has no development tools or test files. Test application startup
with dummy environment values and an overridden Python entry point rather
than launching a second polling process with production credentials.

## Documentation and releases

Update the command table in `README.md`, `docs/commands.md`, and the Telegram
help/menu together when adding commands. Update `.env.example` and
`docs/configuration.md` when changing settings. Preserve existing image-pack
metadata and the stable Telegram-derived IDs.

Successful pushes to `main` publish the container automatically after all
checks pass. No manual registry secret is needed; repository and organization
policies must allow the workflow's `packages: write` permission.

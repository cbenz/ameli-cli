# AGENTS.md

## Tooling

- The ruff formatter is authoritative: don't "fix" its output. In particular, `except A, B:` (unparenthesized) is valid Python 3.14 (PEP 758) and is ruff's canonical form — leave it as-is.

## Development rules

- The README.md file is the main entry point for users and developers. It must always be kept up to date with the features of the application.
- Keep this AGENTS.md file **short**: rules belong here, but rationale, gotchas
  and technical deep-dives go in the dedicated docs (see "Documentation map").

## Language

Use **English** for everything written in this repository: code (identifiers,
comments, docstrings), documentation and CLI outputs. The project's user-facing
surface is English.

> The language used in the chat does not matter. Even when the conversation is
> held in French, every file created or modified in this repository (source
> code, comments, docs, config, commit messages…) must be written in English.

## Documentation map

- [docs/functional-specs.md](docs/functional-specs.md) — **functional spec**:
  purpose, commands, download layout, configuration and constraints (the
  “what”).
- [docs/technical-specs.md](docs/technical-specs.md) — **technical spec**:
  architecture, modules, auth/API/sync flows and key design decisions (the
  “how”).
- [docs/research.md](docs/research.md) — exploration journal: raw portal
  footprints, the initial scope (monthly statements archive) and what still has
  to be confirmed by a live capture.
- [docs/AUTH.md](docs/AUTH.md) — **browser authentication**: the dedicated
  Chrome + CDP technique, the double validation (SMS / app) and what must be
  confirmed about the portal login form. Read it before touching `auth.py`.
- [docs/API.md](docs/API.md) — the personal account portal
  (`assure.ameli.fr`) mapping: URLs, session/cookies, endpoints. Fill it in as
  endpoints are discovered by capture.

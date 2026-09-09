# ameli-cli

Command-line tool to back up documents from your **personal
[ameli](https://www.ameli.fr) account** (French health insurance / Assurance
Maladie): it opens a session on the personal-account portal, then lists and
downloads documents through that authenticated session.

**Current scope**: the **monthly reimbursement statements** from the archive
(`https://assure.ameli.fr/compte/aspm/releves-mensuels`), which keeps the
history beyond the six months shown on the main "Mes paiements et
remboursements" screen.

## Why a visible browser?

The personal-account portal rejects plain HTTP clients **and headless
browsers** (an F5/Imperva WAF answers "Request Rejected"). The archive is also
a separate OAuth2-protected app whose session is carried by **cookies only**
(no token in storage). Consequently:

- `sync` / `list` / `login` open a **dedicated, visible Chrome** with its own
  profile, driven over the DevTools/CDP protocol — never your normal browser.
- The first login is **manual**: numéro de sécurité sociale + password +
  **double validation** (code by SMS or the "Compte ameli" mobile app), typed
  right in the opened window.
- The session cookies are then **harvested and cached** on disk
  (`session.json`) and **re-injected** into a fresh dedicated Chrome on later
  runs, so the interactive login is only needed again once the portal session
  has expired (verified to work with the real portal).

## Requirements

- Python ≥ 3.14 (managed with [uv](https://docs.astral.sh/uv/))
- A Chromium-based browser, auto-detected in this order:
  `google-chrome-stable`, `google-chrome`, `chromium`, `chromium-browser`,
  `brave-browser`, `microsoft-edge-stable`

## Installation

> ⚠️ `ameli-cli` is **not published on PyPI**: `pip install ameli-cli` or
> `uvx ameli-cli` will not work. Install it from the repository instead.

### Run it directly with `uvx` (no install)

```bash
uvx --from git+https://github.com/cbenz/ameli-cli.git ameli-cli --help
```

### Install it as a standalone command

```bash
uv tool install --from git+https://github.com/cbenz/ameli-cli.git ameli-cli
ameli-cli --help
```

Update later with: `uv tool upgrade ameli-cli`.

### Development / local

```bash
uv sync                      # create .venv and install the dependencies
uv run ameli-cli --help      # list the available commands
```

## Usage

```bash
# Show the configuration / session state (no network, no browser)
ameli-cli status

# Authenticate (a Chrome window may open) and download the missing statements
# into <base>/RELEVES_MENSUELS/…  — an already-present statement is skipped.
ameli-cli sync
ameli-cli sync --download-dir /some/dir

# List the monthly statements without downloading (both names work)
ameli-cli list
ameli-cli ls
ameli-cli list --json        # raw JSON on stdout

# Manage the session
ameli-cli login              # ensure a valid session (interactive if needed)
ameli-cli logout             # delete the cached session
ameli-cli logout --reset     # full sign-out: cached session + Chrome profile

# Show / initialize the configuration file
ameli-cli config             # print the config file path
ameli-cli config --init      # write the default template (if missing)
```

Global options (usable on any command): `--config PATH`, `--verbose`.

## Download layout

Files are written under the download base dir, in **one sub-folder per
configured collection**, named from the `[download] file_mask` template
(default: `Relevé Mensuel {period}.pdf`):

```text
~/Documents/ameli/
└── RELEVES_MENSUELS/
    ├── Relevé Mensuel 2025-09.pdf
    ├── Relevé Mensuel 2025-10.pdf
    ├── Relevé Mensuel 2026-06.pdf
    └── …
```

`{period}` is `YYYY-MM` (zero-padded), so **alphabetical order is
chronological order**. A statement already present under the current mask is
skipped; a file left by an older naming scheme is **renamed automatically** to
the current mask. Only file names are compared — there is no local content
index.

## Configuration

TOML file (generated on first run): `~/.config/ameli-cli/config.toml` (use
`--config` for another one). Every key is **commented out** by default:
uncomment a line to override its default.

```toml
[auth]        # login_url, login, password, headless
[api]         # base_url, releves_url, collections = ["RELEVES_MENSUELS"]
[download]    # file_mask (file name template)
[log]         # level = "INFO" (DEBUG, INFO, WARNING, ERROR)
[paths]       # download_dir, chrome_dir, session_cache
```

> **Note**: the `login`, `password` and `headless` keys are accepted for
> forward compatibility but are **not used yet** — the login is always manual
> in the visible Chrome window, and the portal's WAF rejects headless Chrome.

### File name template (`[download] file_mask`)

The on-disk name of each statement comes from the `file_mask` template
(default `Relevé Mensuel {period}.pdf`). Supported placeholders:

| Placeholder | Value for June 2026 |
| --- | --- |
| `{period}` | `2026-06` (`YYYY-MM`, chronological) |
| `{year}` | `2026` |
| `{month}` | `06` |
| `{label}` | `Relevé mensuel juin 2026` |

Values are sanitized for the filesystem and the `.pdf` extension is ensured.
An unknown placeholder (or unbalanced braces) is rejected when the config is
read. Example — period first, so files sort chronologically by prefix:

```toml
[download]
file_mask = "{period} - Relevé mensuel.pdf"
```

## Locations (XDG defaults)

| Data | Default path |
| --- | --- |
| Downloaded statements (one folder per collection) | `~/Documents/ameli` |
| Dedicated Chrome profile (login, persisted session) | `~/.local/state/ameli-cli/chrome` |
| Session cache (live portal cookies) | `~/.local/state/ameli-cli/session.json` |
| Configuration | `~/.config/ameli-cli/config.toml` |

Everything can be overridden under `[paths]` in the config file.

## Development

```bash
uv run ruff check src/ameli_cli/
uv run ameli-cli --help
```

## Security notes

- `session.json` contains **live portal session cookies** for your account:
  treat it as a credential, keep it private and **never commit it**.
- The dedicated Chrome profile under `~/.local/state/ameli-cli/chrome` also
  holds portal session state; `logout --reset` wipes it.

## Technical documentation

- [docs/functional-specs.md](docs/functional-specs.md) — functional spec:
  commands, download layout, configuration and constraints.
- [docs/technical-specs.md](docs/technical-specs.md) — technical spec:
  architecture, modules, auth/API/sync flows and design decisions.
- [docs/research.md](docs/research.md) — exploration journal: portal
  footprints, the initial scope and the capture outcome.
- [docs/AUTH.md](docs/AUTH.md) — browser authentication: dedicated Chrome +
  CDP, the double validation, cookie harvesting/re-injection.
- [docs/API.md](docs/API.md) — the `assure.ameli.fr` portal mapping (URLs,
  REST endpoints, required headers).

# ameli-cli — Functional Specification

This document describes **what** `ameli-cli` does from a user's point of view:
its purpose, the commands it exposes, its observable behavior and its
constraints. Implementation details live in
[docs/technical-specs.md](technical-specs.md); raw exploration notes and
unsettled items in [docs/research.md](research.md); the actual API mapping
(discovered by capture) will go in [docs/API.md](API.md).

## 1. Overview

`ameli-cli` is a command-line tool that **backs up documents from the personal
account of the French health insurance ([Assurance Maladie](https://www.ameli.fr))**
— the "compte ameli" served on `assure.ameli.fr`.

**Initial scope**: the **monthly reimbursement statements** ("relevés mensuels")
available in the "Mes relevés mensuels" archive
(`https://assure.ameli.fr/compte/aspm/releves-mensuels`). This screen is
reached from the "Mes paiements et remboursements" screen of the portal
(`_pageLabel=as_paiements_page`) through the link **"Accéder à mon historique
depuis plus de 6 mois"** — the current screen only shows the last 6 months,
while the archive gives the full history.

In a few commands it:

1. **authenticates** against the account — reusing a live session when
   possible, otherwise logging in through a **dedicated Chrome** it starts
   itself (own profile, driven over the DevTools/CDP protocol);
2. **lists** the monthly statements of the archive;
3. **downloads** each statement (PDF) into a local folder, without re-downloading
   the ones already present.

It targets **beneficiaries of the French health insurance** who want a
repeatable, scriptable local backup of their own reimbursement statements.

## 2. Goals

- Automate the backup of **every monthly statement** available in the archive
  (beyond the last 6 months).
- Keep a *low-friction* backup possible: once logged in, later runs reuse the
  session without re-login (a visible window still opens, but nothing to type).
- Provide a **no-network** way to inspect configuration and authentication
  state (`status`).
- Support **incremental** runs: a statement already on disk is not re-downloaded.
- Make the configuration file-based, editable and fully overridable.
- Keep the scope **extensible**: other "collections" (attestations, payment
  details…) can be added later without changing the architecture or the
  commands.

## 3. Non-goals (out of scope)

- **No write access on the portal**: the tool never creates, moves or deletes
  anything in the ameli account.
- **No two-way sync**: data only flows from the portal to the local disk.
- **No content-based change detection**: only file *names* are compared (a
  statement is skipped when its sanitized name already exists in its folder) —
  no local content index (SHA-256 / `downloads.json`).
- **No GUI / multi-account**: a single-user CLI with one configuration file.
- **v1 scope limited to the monthly statements of the archive**: the other
  screens of the account (attestations, payment details, messaging, procedures…)
  are explicitly out of scope for the first version.

## 4. Key user scenarios

| Scenario | Behavior |
| --- | --- |
| First run | No config file exists → the default TOML template is written and logged; commands then apply the built-in defaults. |
| Check state | `status` prints the config paths, the active collection and whether a valid session is cached — **no network** call. |
| Fresh login | No valid session and no alive session → a **visible Chrome window** opens with a dedicated profile; the login (numéro de sécurité sociale) and password are pre-filled and submitted automatically; the user **completes the double validation** (SMS code or validation in the Compte ameli app); the window closes, the session stays on disk. |
| Silent re-login | Cached session expired but the portal session is still alive → the session is **reused without re-login**: the visible window opens but no typing is needed. |
| Backup | `sync` authenticates, lists the monthly statements of the archive and downloads the missing ones under the collection folder. |
| Sign-out | `logout` deletes the session cache; `logout --reset` also deletes the browser profile (full sign-out). |

## 5. Command reference

> `list` and `ls` are the same command. Running the bare `ameli-cli` (no
> subcommand) prints the help.

| Command | Purpose | Specific options |
| --- | --- | --- |
| `sync` | Authenticate, then list and download the monthly statements of the configured collection. | `--download-dir DIR` |
| `list` (`ls`) | Authenticate and print the statement list (period/label + reference per line) without downloading. | `--json` (raw JSON on stdout) |
| `login` | Ensure a valid session. Already valid → just reports it (no browser). Otherwise silent reuse, or a visible browser login. | — |
| `logout` | Delete the session cache. | `--reset` (also delete the browser profile — full sign-out) |
| `status` | Show configuration and session state (no network). | — |
| `config` | Print the path of the configuration file. | `--init` (write the default template if missing, then print the path) |

### Global options (available on any command)

| Option | Effect |
| --- | --- |
| `--config PATH` | Use another configuration file than the default `~/.config/ameli-cli/config.toml`. |
| `--verbose` | Debug logging. |

## 6. Download layout

Files are written under the download base directory (default
`~/Documents/ameli`), **one sub-folder per configured collection**, so the tree
mirrors the `[api] collections` config. Empty collections still get their folder
(created eagerly), keeping the tree stable:

```text
~/Documents/ameli/
└── RELEVES_MENSUELS/
    ├── Relevé Mensuel 2025-09.pdf
    ├── Relevé Mensuel 2025-10.pdf
    └── …
```

Rules:

- Each statement goes into `<base>/<COLLECTION>/<file name>` (an item without a
  collection falls back to a `MISC` folder, defensively).
- The on-disk file name is rendered from the **`[download] file_mask`**
  template (default `Relevé Mensuel {period}.pdf`) with the placeholders
  `{period}` (`YYYY-MM`, zero-padded → **alphabetical order = chronological
  order**), `{year}`, `{month}` and `{label}`. The rendered name is sanitized
  (characters like `/ \ : * ? " < > |` become `_`) and the `.pdf` extension is
  ensured.
- **Skip is by file name only**: if `<dest>/<file name>` (under the current
  mask) already exists, the statement is skipped (`Already present (by
  filename)`) — there is no local content index.
- Files downloaded by an older version (named after the portal's
  `nomAffichage`) are **renamed automatically** to the current mask on the
  next `sync`, so they are not downloaded twice under a second name.
- `sync` reports how many statements were downloaded vs. renamed vs. already
  present vs. failed.

## 7. Configuration

A single **TOML** file, generated automatically on first run and overridable
per run with `--config`. Every key is **commented out** in the template:
uncomment a line to override its default. Precedence is **CLI option > config
file > built-in default**.

| Section | Keys | Meaning |
| --- | --- | --- |
| `[auth]` | `login_url`, `login`, `password`, `headless` | Portal entry point; credentials kept for future automatic pre-fill (the login is manual for now); `headless` unused (the WAF requires a visible window). |
| `[api]` | `base_url`, `releves_url`, `collections` | Portal base URL; archive URL of the monthly statements; active collections (v1: `["RELEVES_MENSUELS"]`). |
| `[download]` | `file_mask` | File name template for the downloaded statements (`{period}`, `{year}`, `{month}`, `{label}`; default `Relevé Mensuel {period}.pdf`). |
| `[log]` | `level` | `DEBUG`, `INFO`, `WARNING`, `ERROR` (default `INFO`). |
| `[paths]` | `download_dir`, `chrome_dir`, `session_cache` | Override of the XDG default locations. |

### Password without a password manager dependency

The `password` may come from **any command** on the machine using the
`command:` prefix — the CLI runs it and uses its stdout. The `login` is the
**numéro de sécurité sociale** associated with the ameli account:

```toml
[auth]
login = "2 85 12 75 114 008 31"   # numéro de sécurité sociale
password = "command:pass show ameli"
```

> Requirement: the `command:` secret must be a **real executable** working in a
> non-interactive shell (shell functions/aliases from `~/.zshrc` are not
> loaded).

## 8. Locations (XDG defaults)

| Data | Default path |
| --- | --- |
| Downloaded files (one folder per collection) | `~/Documents/ameli` |
| Dedicated Chrome profile (persisted session, login) | `~/.local/state/ameli-cli/chrome` |
| Session cache | `~/.local/state/ameli-cli/session.json` |
| Configuration | `~/.config/ameli-cli/config.toml` |

All of them can be overridden under `[paths]` in the config file.

## 9. User-visible behavior of the authentication

- The portal rejects plain HTTP clients and headless browsers (WAF): the CLI
  opens **its own dedicated Chrome** (a dedicated, app-owned profile — never
  the user's personal browser) in a **visible window** on every authenticated
  run, and drives it over the DevTools protocol.
- While the cached session is still valid, the cookies are re-injected and the
  archive opens **without re-login** (a window appears, but nothing to type).
- Only when a real login is needed does the user log in **manually** in the
  window — credentials (numéro de sécurité sociale + password) and the
  **double validation** (SMS code or validation in the Compte ameli app) are
  completed by hand; the window then closes and the session stays on disk.
- `logout --reset` is the full sign-out: session cache **and** browser profile
  are deleted.

## 10. Non-functional requirements

| Area | Requirement |
| --- | --- |
| Security | Credentials are not sent over the network except to the official health-insurance portal. The password may be stored in the config or delegated to a system secret command. The dedicated Chrome profile is separate from the user's personal profile. The session is cached on disk under the state dir. |
| Reliability | A statement already present by name is skipped (incremental). API / session failures are reported clearly, with an actionable hint (`Run ameli-cli login` when the session is rejected/expired). |
| Usability | Human-readable, informative logs on **stderr**; `stdout` stays clean for data output (`ls --json`). French-language portal pages are handled (localized labels, possible cookie-consent banner). |
| Portability | Linux-oriented XDG paths; requires a Chromium-based browser for interactive login; `SHELL` used for `command:` secrets. CLI language: English. |
| Performance | Only missing statements are downloaded; no per-file hashing, no local index. |
| Exit codes | `0` success · `1` authentication/backup failure · `2` invalid configuration or CLI options · `130` interrupted by the user. |

## 11. Assumptions and constraints

- The user is a health-insurance beneficiary with an active **ameli account**
  and monthly statements in the archive.
- The machine has network access to `assure.ameli.fr` (portal), the
  authentication flow (ameli credentials + double validation SMS/app or
  FranceConnect) and the statements archive.
- A real **Chromium-based browser** (Google Chrome, Chromium, …) must be
  installed: the portal WAF rejects headless browsers, so a **visible** window
  is used on every authenticated run.
- The account space exposes **no public API**: the tool relies on the internal
  portal (reverse-engineered, documented in `docs/API.md`), which makes it
  sensitive to portal changes — the portal version is tracked.
- Reasonable use: personal backup, low frequency, read-only access to one's own
  data, in line with the ameli account terms of use.

# Browser authentication (the login with double validation)

The ameli personal account (and its monthly-statements sub‑app) is protected by
an **OAuth2/OIDC** login on `ameliconnect.ameli.fr` with a **double validation**
(SMS or "Compte ameli" app), and by an **F5/Imperva WAF** that rejects plain
HTTP clients **and headless Chrome**. Read this before touching
`src/ameli_cli/auth.py`.

## TL;DR

- The CLI opens a **dedicated, visible Chrome** (own profile,
  `--remote-debugging-port=9222 --user-data-dir=<dir>`) and drives it over CDP.
  A visible Chrome is **mandatory**: the WAF blocks headless.
- The session is carried by **session cookies** (not persisted by Chrome).
  After login they are harvested, cached on disk (`session.json`), and
  **re-injected** into a fresh dedicated Chrome on later runs (verified to
  work) — so the interactive double validation is only needed when the session
  has expired. A window still opens on every run, but no typing is needed while
  the session is valid.
- The data calls are **same-origin `fetch`** executed from the archive page
  (`AmeliBrowser.fetch_json`) with the SPA headers documented in `docs/API.md`
  — a plain `requests` client gets a WAF "Request Rejected".

## Flow (`auth.open_session`)

1. `AmeliBrowser.open()`: attach to a Chrome already on the CDP port, else
   launch a **visible** dedicated Chrome on the profile dir.
2. Inject the cached cookies, if any.
3. `ensure_archive(archive_url, …)`: navigate to the monthly-statements
   archive and wait until it is ready (a month heading / "Aucun paiement"
   marker in the page proves the REST list was rendered — i.e. the session is
   really valid). A WAF "Request Rejected" page or an OAuth redirect to
   `ameliconnect.ameli.fr/oauth2/authorize` means the session is not valid.
4. If not ready and interactive: wait (up to ~10 min) for the login form to
   appear in the visible window. When credentials are configured (`[auth]
   login`/`password`, with `command:` secrets resolved lazily), the form is
   **pre-filled** (`#userfield` = numéro de sécurité sociale, `#passwordfield`)
   and submitted once the page JS enables the button
   (`#id_r_cnx_btn_submit`); the user only completes the **double validation**
   (SMS / app). Without configured credentials — or through FranceConnect —
   the login is fully manual.
5. On success: harvest the cookies, refresh the cache, return the browser.

## Why a dedicated profile (not the user's personal Chrome)

- Relaunching the user's personal Chrome with a debug port is invasive, and it
  exposes the whole browsing profile on `127.0.0.1:9222`.
- The dedicated profile is app-owned, lives under the XDG **state** dir
  (`[paths] chrome_dir`, default `~/.local/state/ameli-cli/chrome`) and is
  removed by `logout --reset`.

## Notes / gotchas

- **Visible window is required on every run** — there is no silent headless
  mode: the WAF rejects headless Chrome ("Request Rejected"). This is a portal
  constraint, not a bug.
- Session cookies **do not survive** a Chrome close: they are re-injected from
  the cache at each run (this is why `session.json` exists).
- `--headless` is not supported; the option was removed from the CLI.
- The archive page and its REST API are versioned (SPA `x-app-version`, e.g.
  `25.40.0`); track changes in `docs/API.md`.
- `_setup_logging` sets the `asyncio` logger to `CRITICAL` to silence
  Playwright's Node-driver transport noise.
- Chrome is single-instance per `--user-data-dir`: if another Chrome uses the
  profile, close it before running.

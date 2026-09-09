"""ameli authentication: a headful dedicated Chrome bound to the archive.

Why a browser (and a visible one)?
- The monthly-statements archive is a separate OAuth2-protected app served by
  the portal (`assure.ameli.fr/compte/…`). Its data endpoints reject plain HTTP
  clients (F5/Imperva "Request Rejected") and even **headless** Chrome; only a
  real, visible Chrome with a valid OIDC session cookie
  (`mod_auth_openidc_session`) gets through.
- The portal session is carried by **session cookies** (not persisted by Chrome
  between runs). We harvest them after login, cache them on disk, and
  re-inject them into a fresh dedicated Chrome on later runs (verified to
  work), so the interactive double validation is only needed when the session
  has expired.

This module therefore provides `AmeliBrowser`, a small wrapper that opens the
dedicated Chrome, ensures the archive is reachable (interactive login when
needed) and runs same-origin `fetch` calls for the REST API (see api.py).
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlparse

import requests as _requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

log = logging.getLogger("ameli")

DEFAULT_CDP_URL = "http://127.0.0.1:9222"

# Cookie domains that indicate an active ameli session in a browser profile
# (used to pick the right context when attaching over CDP).
_SESSION_DOMAINS = ("assure.ameli.fr", "ameli.fr")

# Failure markers found on a blocked page.
_REJECT_MARKERS = ("request rejected", "your support id")

# Safety margin (seconds) before a cached session is considered expired.
_SAFETY_MARGIN = 60
# Validity we assign to a freshly harvested session. The real portal session
# lifetime is still to be measured (see docs/API.md); the browser probe is the
# arbiter at each run, so a stale cache only costs one window.
_SESSION_TTL = 1800


class AuthenticationError(Exception):
    """Raised when the archive cannot be reached (login failed/cancelled)."""


@dataclass
class SessionInfo:
    """Authentication result: the portal session cookies."""

    cookies: list[dict[str, Any]] = field(default_factory=list)
    obtained_at: float | None = None

    def is_valid(self) -> bool:
        return bool(self.cookies)


# ── On-disk session cache ──────────────────────────────────────────────


def load_session_cache(path: Path) -> SessionInfo | None:
    """Load a cached session if it has not expired yet."""
    try:
        data = json.loads(path.read_text())
    except OSError, ValueError:
        return None

    cookies = data.get("cookies")
    obtained = data.get("obtained_at")
    if not isinstance(cookies, list) or not cookies:
        return None
    if (
        isinstance(obtained, (int, float))
        and obtained + _SESSION_TTL - _SAFETY_MARGIN < time.time()
    ):
        return None
    return SessionInfo(cookies=cookies, obtained_at=float(obtained) if obtained else None)


def save_session_cache(session: SessionInfo, path: Path) -> None:
    """Write the session cookies (and their harvest time) to a JSON file."""
    try:
        path.write_text(
            json.dumps({"cookies": session.cookies, "obtained_at": session.obtained_at})
        )
    except OSError as exc:
        log.warning("⚠️  Could not write the session cache: %s", exc)


# ── Chrome process helpers ─────────────────────────────────────────────


def _cdp_reachable(cdp_url: str) -> bool:
    try:
        resp = _requests.get(f"{cdp_url.rstrip('/')}/json/version", timeout=1.5)
        return resp.status_code == 200
    except _requests.RequestException:
        return False


_CHROME_CANDIDATES = (
    "google-chrome-stable",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "brave-browser",
    "microsoft-edge-stable",
)


def _find_chrome_binary() -> str | None:
    for name in _CHROME_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def _cdp_port(cdp_url: str) -> int:
    return urlparse(cdp_url).port or 9222


# ── The browser session ────────────────────────────────────────────────


class AmeliBrowser:
    """A dedicated (headful) Chrome, opened on the statements archive.

    Used as a context manager. Once `ensure_archive` has returned True, the
    page sits on the archive origin and `fetch_json` can call the same-origin
    REST API with the session cookies.
    """

    def __init__(self, chrome_dir: Path, cdp_url: str = DEFAULT_CDP_URL) -> None:
        self.chrome_dir = chrome_dir
        self.cdp_url = cdp_url
        self._proc: subprocess.Popen | None = None
        self._pw = None
        self._browser = None
        self._ctx = None
        self._page = None

    # ── lifecycle ─────────────────────────────────────────────────────

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def open(self) -> None:
        """Attach to a running dedicated Chrome or launch a visible one."""
        if _cdp_reachable(self.cdp_url):
            log.info("🖥️  Using the dedicated Chrome already running on %s.", self.cdp_url)
        else:
            binary = _find_chrome_binary()
            if binary is None:
                log.error(
                    "❌ No Chromium browser found (searched for %s).", ", ".join(_CHROME_CANDIDATES)
                )
                raise AuthenticationError("no Chromium browser found")
            self.chrome_dir.mkdir(parents=True, exist_ok=True)
            log.info("🚀 Launching dedicated Chrome (profile: %s)…", self.chrome_dir)
            self._proc = subprocess.Popen(
                [
                    binary,
                    f"--remote-debugging-port={_cdp_port(self.cdp_url)}",
                    f"--user-data-dir={self.chrome_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--window-size=1280,900",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if not self._wait_cdp(20):
                log.error("❌ Chrome did not open the DevTools port. Close any Chrome")
                log.error("   already using profile %s and retry.", self.chrome_dir)
                self.close()
                raise AuthenticationError("Chrome did not open the DevTools port")

        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
        except PlaywrightError as exc:
            log.error("❌ Cannot connect to Chrome at %s (%s).", self.cdp_url, exc)
            self.close()
            raise AuthenticationError(f"cannot connect to Chrome: {exc}") from exc
        self._ctx = self._pick_context()
        if self._ctx is None:
            log.error("❌ No browser context found on %s", self.cdp_url)
            self.close()
            raise AuthenticationError("no browser context found")
        self._page = self._ctx.new_page()

    def close(self) -> None:
        if self._pw is not None:
            with contextlib.suppress(Exception):
                self._pw.stop()
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.terminate()
                self._proc.wait(timeout=5)
        self._pw = None
        self._browser = None
        self._ctx = None
        self._page = None
        self._proc = None

    def _wait_cdp(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if _cdp_reachable(self.cdp_url):
                return True
            time.sleep(0.25)
        return False

    def _pick_context(self) -> Any | None:
        """Pick the context holding the ameli session, else the first one."""
        assert self._browser is not None
        for ctx in self._browser.contexts:
            try:
                for cookie in ctx.cookies():
                    domain = (cookie.get("domain") or "").lstrip(".")
                    if any(domain.endswith(suffix) for suffix in _SESSION_DOMAINS):
                        return ctx
            except PlaywrightError:
                continue
        return self._browser.contexts[0] if self._browser.contexts else None

    # ── cookies ──────────────────────────────────────────────────────

    def inject_cookies(self, cookies: list[dict[str, Any]]) -> None:
        """Re-inject previously harvested session cookies into the context."""
        assert self._ctx is not None
        to_add: list[dict[str, Any]] = []
        for cookie in cookies:
            item: dict[str, Any] = {
                "name": cookie["name"],
                "value": cookie["value"],
                "domain": cookie["domain"],
                "path": cookie.get("path", "/"),
            }
            if isinstance(cookie.get("expires"), (int, float)) and cookie["expires"] > 0:
                item["expires"] = cookie["expires"]
            if cookie.get("secure"):
                item["secure"] = True
            if cookie.get("httpOnly"):
                item["httpOnly"] = True
            if cookie.get("sameSite") and cookie["sameSite"] != "None":
                item["sameSite"] = cookie["sameSite"]
            to_add.append(item)
        self._ctx.add_cookies(to_add)
        log.info("🍪 %s cached cookie(s) injected", len(to_add))

    def harvest_cookies(self) -> list[dict[str, Any]]:
        assert self._ctx is not None
        raw = self._ctx.cookies()
        log.info("🍪 %s cookie(s) harvested", len(raw))
        return raw

    # ── archive readiness ────────────────────────────────────────────

    def _archive_ready(self) -> bool:
        """True if the current page shows the statements archive content."""
        if self._page is None:
            return False
        try:
            text = self._page.inner_text("body")
        except PlaywrightError:
            return False
        low = text.lower()
        if any(marker in low for marker in _REJECT_MARKERS):
            return False
        # A month heading ("AOÛT 2026"…) or a statement/absence marker proves
        # the REST list was rendered — i.e. the session is really valid.
        return "relevés mensuels" in low and ("relevé mensuel" in low or "aucun paiement" in low)

    def ensure_archive(
        self,
        archive_url: str,
        wait_seconds: float,
        interactive: bool = False,
    ) -> bool:
        """Navigate to the archive and wait until it is ready.

        In interactive mode, the user may log in (double validation) in the
        visible window while we wait. Returns False on timeout/block."""
        assert self._page is not None
        if interactive:
            log.info("🔑 Please log in (numéro de sécurité sociale + password +")
            log.info("   double validation) in the Chrome window, if asked.")
        try:
            self._page.goto(archive_url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            log.warning("🚫 Navigation failed: %s", exc)
            return False
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if self._archive_ready():
                return True
            self._page.wait_for_timeout(1000)
        return False

    # ── same-origin REST calls ───────────────────────────────────────

    def fetch_json(self, url: str) -> dict[str, Any]:
        """GET a same-origin REST URL from the page and return parsed JSON."""
        assert self._page is not None
        log.debug("fetch_json → GET %s", url)
        try:
            payload = self._page.evaluate(
                """async (arg) => {
                    const r = await fetch(arg.u, {
                        credentials: "include",
                        headers: {
                            "Accept": "application/json, text/plain, */*",
                            "canal": '{"canal":"PORTAIL","reduction":"ASDS_X"}',
                            "x-app-name": "ASDS_X",
                            "x-app-version": "25.40.0",
                            "x-request-engine": "Axios",
                            "x-correlation-id": arg.c,
                        },
                    });
                    if (!r.ok) throw new Error("HTTP " + r.status);
                    return await r.json();
                }""",
                {"u": url, "c": str(uuid.uuid4())},
            )
        except PlaywrightError as exc:
            raise AuthenticationError(f"REST call failed ({exc})") from exc
        if not isinstance(payload, dict):
            raise TypeError(f"unexpected REST payload: {type(payload).__name__}")
        return payload


# ── Entry point: open an authenticated browser session ────────────────


def open_session(
    archive_url: str,
    chrome_dir: Path,
    cache_path: Path,
    interactive: bool = True,
    fast_wait: float = 12.0,
    login_wait: float = 600.0,
) -> AmeliBrowser:
    """Open a dedicated Chrome and make sure the archive is reachable.

    Steps:
    1. open the (headful) dedicated Chrome on `chrome_dir`;
    2. inject the cached cookies, if any, and try the archive;
    3. if it is not reachable and `interactive`, wait for the user to log in in
       the visible window;
    4. on success, refresh the session cache from the live cookies and return
       the browser (the caller is responsible for closing it).
    Raises AuthenticationError on failure/cancel.
    """
    browser = AmeliBrowser(chrome_dir)
    browser.open()
    try:
        cached = load_session_cache(cache_path)
        if cached is not None:
            browser.inject_cookies(cached.cookies)

        ready = browser.ensure_archive(archive_url, wait_seconds=fast_wait, interactive=False)
        if not ready and interactive:
            log.info("🔑 No live session — a visible Chrome window is open for login.")
            ready = browser.ensure_archive(archive_url, wait_seconds=login_wait, interactive=True)

        if not ready:
            log.error("❌ Could not reach the monthly-statements archive.")
            raise AuthenticationError(
                "archive not reachable (session rejected or login incomplete)"
            )

        save_session_cache(
            SessionInfo(cookies=browser.harvest_cookies(), obtained_at=time.time()), cache_path
        )
        log.info("✅ Archive reachable — session cached.")
        return browser
    except BaseException:
        browser.close()
        raise

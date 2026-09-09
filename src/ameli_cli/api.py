"""ameli monthly-statements API, called from inside the authenticated page.

The archive is an OAuth2-protected app: its REST endpoints live under
`/compte/aspmm/rest/releves-mensuels` on `assure.ameli.fr` and are called with
the session cookies (no `Authorization` header). Because a WAF rejects plain
HTTP clients and even headless browsers, every call runs through
`AmeliBrowser.fetch_json` (a same-origin `fetch` from the archive page).

Discovered mapping (see docs/API.md):
- list: `GET /compte/aspmm/rest/releves-mensuels/home?debutPeriode=YYYYMM&finPeriode=YYYYMM`
  → `{"rubriquesMensuelles":[{"moisAnnee":"202606","releves":[{objectType,identifiant}]}], …}`
  The archive keeps a ~27-month retention; the SPA loads it in sliding windows
  ("Afficher plus de relevés").
- pdf:  `GET /compte/aspmm/rest/releves-mensuels/pdf/<identifiant url-encoded>`
  → `{"nom":"…pdf","nomAffichage":"…pdf","taille":…,"typeMime":"application/pdf","contenu":"<base64>"}`
  `contenu` is the base64-encoded PDF payload.
"""

import base64
import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from ameli_cli.auth import AmeliBrowser

log = logging.getLogger("ameli")

_DEFAULT_BASE_URL = "https://assure.ameli.fr"
_DEFAULT_COLLECTIONS = ["RELEVES_MENSUELS"]
_REST_PATH = "/compte/aspmm/rest/releves-mensuels"
_WINDOW_MONTHS = 6  # months per list request, like the SPA
_RETENTION_DEFAULT = 27  # months kept by the archive ("27 derniers mois")

# Default file name template for downloaded statements (override in the
# [download] section of the config). {period} is YYYY-MM so alphabetical order
# matches chronological order.
DEFAULT_FILE_MASK = "Relevé Mensuel {period}.pdf"

_MONTH_NAMES = {
    1: "janvier",
    2: "février",
    3: "mars",
    4: "avril",
    5: "mai",
    6: "juin",
    7: "juillet",
    8: "août",
    9: "septembre",
    10: "octobre",
    11: "novembre",
    12: "décembre",
}


def _month_add(ym: int, delta: int) -> int:
    """Add `delta` months to a YYYYMM integer."""
    total = (ym // 100) * 12 + (ym % 100) - 1 + delta
    return (total // 12) * 100 + (total % 12) + 1


def _current_ym() -> int:
    now = datetime.now(ZoneInfo("Europe/Paris"))
    return now.year * 100 + now.month


def _ym_to_period(ym: int) -> str:
    return f"{ym // 100}-{ym % 100:02d}"


def _ym_to_label(ym: int) -> str:
    year, month = ym // 100, ym % 100
    return f"{_MONTH_NAMES.get(month, '')} {year}".strip()


def _sanitize(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    return re.sub(r"\s+", " ", name).strip()


def _strip_accents(text: str) -> str:
    """Remove diacritics (to match the portal's accent-free file names)."""
    return "".join(
        char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char)
    )


# Title-cased month stems, with and without accents, as used by the archive
# file names (e.g. `ReleveMensuelJuin2026.pdf`, `ReleveMensuelFevrier2026.pdf`).
_MONTH_STEMS: dict[int, tuple[str, ...]] = {
    month: tuple(dict.fromkeys((name.capitalize(), _strip_accents(name).capitalize())))
    for month, name in _MONTH_NAMES.items()
}


class AmeliAPI:
    """Monthly-statements API, driven through the authenticated browser."""

    def __init__(
        self,
        browser: AmeliBrowser,
        base_url: str = _DEFAULT_BASE_URL,
        collections: list[str] | None = None,
        file_mask: str | None = None,
    ) -> None:
        self.browser = browser
        self.base_url = base_url.rstrip("/")
        self.rest_base = f"{self.base_url}{_REST_PATH}"
        self.collections = collections or list(_DEFAULT_COLLECTIONS)
        self.file_mask = file_mask or DEFAULT_FILE_MASK

    # ── helpers ──────────────────────────────────────────────────────

    def _home_url(self, debut_ym: int, fin_ym: int) -> str:
        return f"{self.rest_base}/home?debutPeriode={debut_ym}&finPeriode={fin_ym}"

    def _pdf_url(self, identifiant: str) -> str:
        # The identifier contains a "#" ("SOINS_IJ#-#…") that must be encoded.
        return f"{self.rest_base}/pdf/{quote(identifiant, safe='')}"

    def _fetch_home(self, debut_ym: int, fin_ym: int) -> dict[str, Any]:
        payload = self.browser.fetch_json(self._home_url(debut_ym, fin_ym))
        if not isinstance(payload, dict):
            raise TypeError(f"unexpected list payload: {type(payload).__name__}")
        return payload

    @staticmethod
    def _retention(payload: dict[str, Any]) -> int:
        for info in payload.get("infosStatiques") or []:
            if "retention" in str(info.get("identifiant", "")).lower():
                try:
                    return int(info.get("contenu"))
                except TypeError, ValueError:
                    break
        return _RETENTION_DEFAULT

    # ── listing ──────────────────────────────────────────────────────

    def list_releves_mensuels(self) -> list[dict[str, Any]]:
        """List the available monthly statements.

        Walks the archive backwards in windows of `_WINDOW_MONTHS` months (the
        way the SPA paginates) until the retention is covered, then returns one
        entry per downloadable statement:
        `{period, label, object_type, identifiant, pdf_url}`."""
        # The archive lists up to the last *closed* month (the current one is
        # still incomplete and rejected by the API).
        last = _month_add(_current_ym(), -1)
        first = self._fetch_home(_month_add(last, -(_WINDOW_MONTHS - 1)), last)
        retention = self._retention(first)
        earliest = _month_add(last, -(retention - 1))
        log.info("Archive retention: %s months (from %s)", retention, _ym_to_period(earliest))

        # Collect months, starting with the first window already fetched.
        rubriques: list[dict[str, Any]] = list(first.get("rubriquesMensuelles") or [])
        fin = _month_add(last, -_WINDOW_MONTHS)
        while fin >= earliest:
            debut = max(earliest, _month_add(fin, -(_WINDOW_MONTHS - 1)))
            payload = self._fetch_home(debut, fin)
            rubriques = list(payload.get("rubriquesMensuelles") or []) + rubriques
            fin = _month_add(debut, -_WINDOW_MONTHS)

        entries: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for rubrique in rubriques:
            raw_ym = rubrique.get("moisAnnee")
            if raw_ym is None:
                continue
            try:
                ym = int(raw_ym)
            except TypeError, ValueError:
                continue
            period = _ym_to_period(ym)
            for releve in rubrique.get("releves") or []:
                if not isinstance(releve, dict):
                    continue
                identifiant = releve.get("identifiant")
                if not identifiant:
                    continue
                key = (period, str(identifiant))
                if key in seen:
                    continue
                seen.add(key)
                entries.append(
                    {
                        "period": period,
                        "label": f"Relevé mensuel {_ym_to_label(ym)}",
                        "object_type": releve.get("objectType", ""),
                        "identifiant": identifiant,
                        "pdf_url": self._pdf_url(str(identifiant)),
                    }
                )

        entries.sort(key=lambda e: e["period"])
        if not entries:
            log.warning("⚠️  No monthly statement found in the archive.")
        else:
            log.info("✅ %d monthly statement(s) found in the archive.", len(entries))
        return entries

    # ── download ─────────────────────────────────────────────────────

    # ── file naming (configurable file_mask) ─────────────────────────

    def _mask_fields(self, entry: dict[str, Any]) -> dict[str, str]:
        """Placeholder values available to the file_mask template."""
        period = str(entry.get("period") or "")
        year, separator, month = period.partition("-")
        if not separator:
            year, month = "", ""
        return {
            "period": period,
            "year": year,
            "month": month,
            "label": str(entry.get("label") or f"Releve mensuel {period}").strip(),
        }

    def _render_mask(self, entry: dict[str, Any], mask: str) -> str:
        """Render `mask` for `entry` into a safe PDF file name."""
        try:
            name = mask.format(**self._mask_fields(entry))
        except KeyError, IndexError, ValueError, AttributeError:
            # The mask is validated at config load; this is a last-resort
            # fallback so a bad mask never aborts a run.
            name = DEFAULT_FILE_MASK.format(**self._mask_fields(entry))
        name = _sanitize(name)
        if not name.lower().endswith(".pdf"):
            name = f"{name}.pdf"
        return name

    def file_name_for(self, entry: dict[str, Any]) -> str:
        """Disk file name for `entry`, rendered from the configured file_mask.

        The mask is the single source of truth for on-disk names: it is used
        both when downloading and when checking for an already-present
        statement, so the two always agree. Putting `{period}` (YYYY-MM) early
        keeps the folder sorted chronologically."""
        return self._render_mask(entry, self.file_mask)

    def legacy_file_names_for(self, entry: dict[str, Any]) -> list[str]:
        """Names used for `entry` before `file_mask` was configurable.

        Older versions named files after the portal's `nomAffichage`
        (`ReleveMensuel<Month><Year>.pdf`).
        """
        period = str(entry.get("period") or "")
        year, separator, month_s = period.partition("-")
        names = [f"Releve mensuel {period}.pdf"]
        if separator:
            for stem in _MONTH_STEMS.get(int(month_s), ()):
                names.append(f"ReleveMensuel{stem}{year}.pdf")
        return names

    def rename_legacy(self, entry: dict[str, Any], destination: Path) -> Path | None:
        """Rename a legacy-named file for `entry` to the current mask.

        Returns the (new) path if a legacy file was found and renamed, else
        None. Lets `sync` pick up files downloaded by older versions instead
        of downloading them again under a second name."""
        target = destination / self.file_name_for(entry)
        for legacy in self.legacy_file_names_for(entry):
            source = destination / legacy
            if source.exists():
                source.rename(target)
                log.info("🔁 Renamed: %s → %s", source.name, target.name)
                return target
        return None

    def download_releve(self, entry: dict[str, Any], destination: Path) -> Path | None:
        """Download a statement's PDF and write it into `destination`.

        The PDF endpoint returns JSON with the payload base64-encoded in
        `contenu`. The on-disk name is rendered from the configured `file_mask`
        (same name as the "already present" check, so skip and download agree)."""
        url = entry.get("pdf_url")
        if not url:
            log.error("❌ No download URL for statement: %s", entry)
            return None
        try:
            payload = self.browser.fetch_json(url)
        except Exception as exc:  # noqa: BLE001 - surfaced as a download failure
            log.error("❌ Failed to download %s: %s", entry.get("period"), exc)
            return None

        if not isinstance(payload, dict) or "contenu" not in payload:
            log.error(
                "❌ Unexpected PDF payload for %s: %s", entry.get("period"), type(payload).__name__
            )
            return None
        try:
            data = base64.b64decode(payload["contenu"])
        except ValueError as exc:
            log.error("❌ Invalid base64 PDF for %s: %s", entry.get("period"), exc)
            return None

        destination.mkdir(parents=True, exist_ok=True)
        path = destination / self.file_name_for(entry)
        path.write_bytes(data)
        log.info("✅ Saved: %s (%s bytes)", path, len(data))
        return path

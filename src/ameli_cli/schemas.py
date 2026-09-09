"""Pydantic schemas used to validate external inputs (config file + CLI).

Central place for every value coming from the outside world (TOML file and
command-line options): types, allowed values and constraints are validated
here so downstream code can trust them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Shared validators ──────────────────────────────────────────────────


def _ensure_http_url(value: str) -> str:
    """Reject values that are not absolute http(s) URLs."""
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("must be an absolute http(s) URL")
    return value


# ── Configuration file (TOML) ──────────────────────────────────────────

# Collections supported by the portal. v1: the monthly-statements archive only.
_COLLECTION_LITERALS = Literal["RELEVES_MENSUELS"]

# Placeholders accepted in the `[download] file_mask` template.
FILE_MASK_FIELDS = ("period", "year", "month", "label")


class AuthFile(BaseModel):
    """[auth] section of the config file."""

    model_config = ConfigDict(extra="forbid")

    login_url: str = (
        "https://assure.ameli.fr/PortailAS/appmanager/PortailAS/assure"
        "?_nfpb=true&_pageLabel=as_accueil_page"
    )
    login: str = ""
    password: str = ""
    headless: bool = False

    @field_validator("login_url")
    @classmethod
    def _validate_login_url(cls, value: str) -> str:
        return _ensure_http_url(value)


class ApiFile(BaseModel):
    """[api] section of the config file."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = "https://assure.ameli.fr"
    releves_url: str = "https://assure.ameli.fr/compte/aspm/releves-mensuels"
    collections: list[_COLLECTION_LITERALS] = ["RELEVES_MENSUELS"]

    @field_validator("base_url", "releves_url")
    @classmethod
    def _validate_urls(cls, value: str) -> str:
        return _ensure_http_url(value)


class LogFile(BaseModel):
    """[log] section of the config file."""

    model_config = ConfigDict(extra="forbid")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator("level", mode="before")
    @classmethod
    def _normalize_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.upper()
        return value


class PathsFile(BaseModel):
    """[paths] section of the config file."""

    model_config = ConfigDict(extra="forbid")

    download_dir: str = ""
    chrome_dir: str = ""
    session_cache: str = ""


class DownloadFile(BaseModel):
    """[download] section of the config file."""

    model_config = ConfigDict(extra="forbid")

    file_mask: str = ""

    @field_validator("file_mask")
    @classmethod
    def _validate_file_mask(cls, value: str) -> str:
        if value.count("{") != value.count("}"):
            raise ValueError("unbalanced braces in the file mask")
        for token in re.findall(r"\{([^{}]+)\}", value):
            field = token.split(":", 1)[0].split("!", 1)[0]
            if field not in FILE_MASK_FIELDS:
                supported = ", ".join(f"{{{name}}}" for name in FILE_MASK_FIELDS)
                raise ValueError(f"unknown placeholder {{{{{field}}}}} — supported: {supported}")
        return value


class ConfigFile(BaseModel):
    """Root model of the whole config file (all sections optional)."""

    model_config = ConfigDict(extra="forbid")

    auth: AuthFile = Field(default_factory=AuthFile)
    api: ApiFile = Field(default_factory=ApiFile)
    download: DownloadFile = Field(default_factory=DownloadFile)
    log: LogFile = Field(default_factory=LogFile)
    paths: PathsFile = Field(default_factory=PathsFile)


# ── CLI options ────────────────────────────────────────────────────────


class CliOptions(BaseModel):
    """Validated command-line options (built from the argparse namespace)."""

    model_config = ConfigDict(extra="ignore")

    config: Path | None = None
    download_dir: str | None = None
    headless: bool = False
    reset: bool = False
    verbose: bool = False
    # CLI flag `--json` is aliased to avoid clashing with BaseModel.json()
    as_json: bool = Field(default=False, validation_alias="json")
    init: bool = False

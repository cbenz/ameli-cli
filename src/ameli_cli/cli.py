"""ameli-cli: command-line interface for your personal ameli account.

Subcommands:
  sync     authenticate then download the monthly statements of the archive
  list     list the monthly statements without downloading (alias: ls)
  login    ensure a valid session (interactive login if needed)
  logout   delete the cached session (--reset wipes the browser profile too)
  status   show configuration and session state
  config   show / initialize the configuration file

Configuration: a TOML file (default ~/.config/ameli-cli/config.toml),
overridable with the global --config option.

Note on the browser: the portal rejects plain HTTP clients and headless
browsers (WAF), so `sync`/`list`/`login` open a **visible** dedicated Chrome.
The session cookies are cached and re-injected on later runs, so the
interactive double validation is only needed when the session has expired.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ameli_cli import paths
from ameli_cli.api import AmeliAPI
from ameli_cli.auth import (
    AmeliBrowser,
    AuthenticationError,
    load_session_cache,
    open_session,
)
from ameli_cli.config import (
    DEFAULT_CONFIG_FILE,
    Config,
    ConfigurationError,
    create_default_config,
    load_config,
)
from ameli_cli.schemas import CliOptions

log = logging.getLogger("ameli")

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"


def _setup_logging(level: str) -> None:
    """(Re)configure logging with the requested level (on stderr, so stdout
    stays usable for data output such as `ls --json`)."""
    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        datefmt="%H:%M:%S",
        stream=sys.stderr,
        force=True,
    )
    logging.getLogger("ameli").setLevel(level)
    # Playwright drives its Node driver through an asyncio subprocess whose
    # transport logs raw noise ("execute program …", "… exited with return
    # code 0") — silence it so the CLI output stays clean.
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)


def _report_validation_error(source: str, exc: ValidationError) -> None:
    """Log a pydantic validation error in a human-readable way."""
    log.error("Invalid %s:", source)
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"])
        log.error("  - %s: %s", loc or "<value>", error["msg"])


# ── Argument parsing (subcommands) ─────────────────────────────────────


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"Path to the TOML configuration file (default: {DEFAULT_CONFIG_FILE})",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose logging (DEBUG)",
    )


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    _add_common_options(common)

    parser = argparse.ArgumentParser(
        prog="ameli-cli",
        description="Manage and download documents from your personal ameli account.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p_sync = sub.add_parser("sync", parents=[common], help="Authenticate and download documents")
    p_sync.add_argument(
        "--download-dir",
        type=str,
        default=None,
        help=f"Destination directory (default: {paths.download_dir()})",
    )
    p_sync.set_defaults(func=cmd_sync)

    p_list = sub.add_parser(
        "list",
        parents=[common],
        aliases=["ls"],
        help="List the monthly statements without downloading",
    )
    p_list.add_argument(
        "--json", action="store_true", help="Output the statement list as JSON on stdout"
    )
    p_list.set_defaults(func=cmd_list)

    sub.add_parser(
        "login",
        parents=[common],
        help="Ensure a valid session (interactive login in the browser if needed)",
    ).set_defaults(func=cmd_login)

    p_logout = sub.add_parser(
        "logout", parents=[common], help="Sign out (delete the cached session)"
    )
    p_logout.add_argument(
        "--reset",
        action="store_true",
        help="Also delete the browser profile (dedicated Chrome session) — full sign-out",
    )
    p_logout.set_defaults(func=cmd_logout)

    sub.add_parser(
        "status", parents=[common], help="Show the configuration and session state"
    ).set_defaults(func=cmd_status)

    p_config = sub.add_parser(
        "config", parents=[common], help="Show or initialize the configuration file"
    )
    p_config.add_argument(
        "--init",
        action="store_true",
        help="Write the default template (if missing) and print its path",
    )
    p_config.set_defaults(func=cmd_config)

    return parser


# ── Shared helpers ─────────────────────────────────────────────────────


def _bootstrap_config(opts: CliOptions) -> tuple[Config, Path]:
    """Resolve + create the config file, load/validate it and set up logging.
    Returns (cfg, config_path)."""
    config_path = opts.config or DEFAULT_CONFIG_FILE

    if not config_path.exists():
        if create_default_config(config_path):
            log.info("📄 No config file found — wrote a default template to %s", config_path)
        else:
            log.warning("⚠️  Config file %s is missing and could not be created.", config_path)

    cfg = load_config(config_path)

    level_name = "DEBUG" if opts.verbose else cfg.log_level
    _setup_logging(level_name)
    log.info("📄 Config: %s", config_path)
    return cfg, config_path


def _bootstrap_paths(opts: CliOptions, cfg: Config) -> dict[str, Any]:
    """Compute the resolved paths from the config + CLI."""
    chrome_dir = cfg.chrome_dir or paths.chrome_dir()
    session_cache = cfg.session_cache or paths.session_cache_path()
    if opts.download_dir:
        download_dir = Path(opts.download_dir)
    else:
        download_dir = cfg.download_dir or paths.download_dir()

    chrome_dir.mkdir(parents=True, exist_ok=True)
    session_cache.parent.mkdir(parents=True, exist_ok=True)
    return {
        "chrome_dir": chrome_dir,
        "session_cache": session_cache,
        "download_dir": download_dir,
    }


def _resolve_secret(value: str) -> str:
    """Resolve a config secret. A `command:`-prefixed value runs the rest of
    the string through `$SHELL -c` (non-interactive) and uses its stdout.

    The command must be a *real executable* working in a non-interactive shell
    (shell functions/aliases from ~/.zshrc are not loaded). On failure the CLI
    warns and returns "" (the login proceeds without that credential)."""
    if not value.startswith("command:"):
        return value
    command = value[len("command:") :]
    shell = os.environ.get("SHELL") or "/bin/sh"
    try:
        result = subprocess.run(
            [shell, "-c", command],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("⚠️  Cannot run `command:` secret (%s): %s", command, exc)
        return ""
    if result.returncode != 0:
        log.warning("⚠️  `command:` secret failed (exit %s): %s", result.returncode, command)
        return ""
    return result.stdout.rstrip("\n")


def _open_session(cfg: Config, ctx: dict[str, Any]) -> AmeliBrowser:
    """Open the dedicated Chrome and ensure the archive is reachable.

    The configured credentials are passed along (resolved lazily, only when a
    login is actually needed) so the login form can be pre-filled. Raises
    AuthenticationError (→ exit code 1) if the login fails or is cancelled."""
    return open_session(
        archive_url=cfg.releves_url,
        chrome_dir=ctx["chrome_dir"],
        cache_path=ctx["session_cache"],
        interactive=True,
        login=cfg.login,
        password=cfg.password,
        resolve_secret=_resolve_secret,
    )


def _build_api(browser: AmeliBrowser, cfg: Config) -> AmeliAPI:
    return AmeliAPI(
        browser=browser,
        base_url=cfg.api_base_url,
        collections=cfg.collections,
        file_mask=cfg.file_mask,
    )


# ── Subcommands ────────────────────────────────────────────────────────


def cmd_sync(opts: CliOptions) -> None:
    """Authenticate, list and download the monthly statements."""
    cfg, _ = _bootstrap_config(opts)
    ctx = _bootstrap_paths(opts, cfg)

    log.info("=" * 50)
    log.info("🔐 Step 1: ameli authentication (a Chrome window may open)")
    log.info("=" * 50)
    try:
        browser = _open_session(cfg, ctx)
    except AuthenticationError as exc:
        log.error("❌ Authentication failed — %s", exc)
        raise SystemExit(1)

    try:
        api = _build_api(browser, cfg)
        log.info("=" * 50)
        log.info("🌐 Step 2: Fetching statements through the portal")
        log.info("=" * 50)
        # Collections are created eagerly (even when empty), so the tree
        # mirrors the configured collections.
        download_dir = ctx["download_dir"]
        download_dir.mkdir(parents=True, exist_ok=True)
        log.info("📁 Base dir: %s", download_dir.resolve())
        for collection in api.collections:
            (download_dir / collection).mkdir(parents=True, exist_ok=True)

        documents: list[dict[str, Any]] = []
        for collection in api.collections:
            if collection == "RELEVES_MENSUELS":
                documents.extend(api.list_releves_mensuels())
            else:
                log.warning("⚠️  Collection %s is not implemented yet — skipping.", collection)

        if not documents:
            log.warning("⚠️  No statement found.")
            return

        log.info("=" * 50)
        log.info("⬇️  Step 3: Download")
        log.info("=" * 50)
        # v1: every statement belongs to the RELEVES_MENSUELS collection.
        dest = download_dir / "RELEVES_MENSUELS"
        dest.mkdir(parents=True, exist_ok=True)

        downloaded = 0
        skipped = 0
        renamed = 0
        failed = 0
        for doc in documents:
            path = dest / api.file_name_for(doc)
            if path.exists():
                log.info("⏭️  Already present (by filename): %s", path.name)
                skipped += 1
                continue
            if api.rename_legacy(doc, dest) is not None:
                renamed += 1
                continue
            written = api.download_releve(doc, destination=dest)
            if written:
                downloaded += 1
            else:
                failed += 1

        log.info(
            "🎉 Done — %s downloaded, %s renamed, %s already present, %s failed, in %s",
            downloaded,
            renamed,
            skipped,
            failed,
            download_dir.resolve(),
        )
    finally:
        browser.close()


def cmd_list(opts: CliOptions) -> None:
    """Authenticate and print the statement list (stdout)."""
    cfg, _ = _bootstrap_config(opts)
    ctx = _bootstrap_paths(opts, cfg)

    try:
        browser = _open_session(cfg, ctx)
    except AuthenticationError as exc:
        log.error("❌ Authentication failed — %s", exc)
        raise SystemExit(1)

    try:
        api = _build_api(browser, cfg)
        documents = api.list_releves_mensuels()
    finally:
        browser.close()

    if opts.as_json:
        json.dump(documents, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    for doc in documents:
        label = doc.get("label") or doc.get("period") or "?"
        period = doc.get("period") or "?"
        print(f"{label}\t{period}")


def cmd_login(opts: CliOptions) -> None:
    """Ensure a valid session. If one is already cached, just report it (no
    browser). Otherwise open the visible Chrome and let the user log in."""
    cfg, _ = _bootstrap_config(opts)
    ctx = _bootstrap_paths(opts, cfg)

    cached = load_session_cache(ctx["session_cache"])
    if cached is not None:
        log.info("✅ Already logged in — session still valid.")
        return

    try:
        browser = _open_session(cfg, ctx)
    except AuthenticationError as exc:
        log.error("❌ Login failed — %s", exc)
        raise SystemExit(1)
    browser.close()
    log.info("✅ Login successful, session cached.")


def cmd_logout(opts: CliOptions) -> None:
    """Delete the cached session. With --reset, also delete the browser profile
    (dedicated Chrome session) — full sign-out."""
    cfg, _ = _bootstrap_config(opts)
    session_cache = cfg.session_cache or paths.session_cache_path()
    removed: list[str] = []
    if session_cache.exists():
        session_cache.unlink()
        removed.append(str(session_cache))

    if opts.reset:
        chrome_dir = cfg.chrome_dir or paths.chrome_dir()
        if chrome_dir.exists():
            shutil.rmtree(chrome_dir, ignore_errors=True)
            removed.append(str(chrome_dir))

    if removed:
        log.info("🗑️  Removed: %s", ", ".join(removed))
    elif opts.reset:
        log.info("ℹ️  Already signed out — nothing to remove.")
    else:
        log.info("ℹ️  No cached session to remove (%s).", session_cache)


def cmd_status(opts: CliOptions) -> None:
    """Show the configuration and session state (no network, no browser)."""
    cfg, config_path = _bootstrap_config(opts)
    session_cache = cfg.session_cache or paths.session_cache_path()

    print(f"Config file:     {config_path}")
    print(f"Login URL:       {cfg.login_url}")
    print(f"Statements URL:  {cfg.releves_url}")
    print(f"Collections:     {', '.join(cfg.collections or ['RELEVES_MENSUELS'])}")
    print(f"Download dir:    {cfg.download_dir or paths.download_dir()}")
    print(f"Chrome dir:      {cfg.chrome_dir or paths.chrome_dir()}")
    print(f"Session cache:   {session_cache}")

    session = load_session_cache(session_cache)
    if session is None:
        print("Authentication:  ❌ no valid cached session (run `login` or `sync`)")
    else:
        obtained = session.obtained_at
        stamp = f" (obtained at {obtained})" if obtained else ""
        print(f"Authentication:  ✅ cached session valid{stamp}")


def cmd_config(opts: CliOptions) -> None:
    """Show (and optionally initialize) the configuration file."""
    config_path = opts.config or DEFAULT_CONFIG_FILE
    if opts.init:
        if create_default_config(config_path):
            log.info("📄 Wrote a default template to %s", config_path)
        else:
            log.info("ℹ️  Config file already exists: %s", config_path)
    print(config_path)


# ── Entry point ────────────────────────────────────────────────────────


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # Running the bare command (no subcommand) shows the help, like `-h`.
    if args.command is None:
        parser.print_help()
        raise SystemExit(0)

    # Default logging configured early (in case the config is generated below)
    _setup_logging("INFO")

    # Validate the command-line options with pydantic
    try:
        opts = CliOptions.model_validate(vars(args))
    except ValidationError as exc:
        _report_validation_error("command-line options", exc)
        raise SystemExit(2)

    try:
        args.func(opts)
    except ConfigurationError as exc:
        log.error("%s", exc)
        raise SystemExit(2)
    except KeyboardInterrupt:
        log.info("⏹️  Interrupted by the user")
        raise SystemExit(130)


if __name__ == "__main__":
    main()

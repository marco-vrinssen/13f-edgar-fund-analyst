"""Settings: the fund list from funds.json, machine settings from config.local.json or env vars."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

REPO = Path(__file__).resolve().parent.parent
KEYCHAIN_OPENFIGI = "openfigi-api-key"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Fund:
    id: str
    name: str
    filer: str
    cik: int
    city: str
    manager: str
    style: str


@dataclass(frozen=True)
class Settings:
    user_agent: Optional[str]
    vault_dir: Optional[Path]
    note_folder: str
    note_title: str
    data_dir: Path


def load_funds() -> List[Fund]:
    path = Path(os.environ.get("EDGAR13F_FUNDS") or REPO / "funds.json")
    entries = json.loads(path.read_text())["funds"]
    return [Fund(**{**entry, "cik": int(entry["cik"])}) for entry in entries]


def find_fund(funds: List[Fund], query: str) -> Fund:
    """Match a fund by id, CIK or a unique part of its name."""
    q = query.strip().lower()
    for fund in funds:
        if q in (fund.id, str(fund.cik), fund.name.lower()):
            return fund
    hits = [f for f in funds if q in f.name.lower() or q in f.filer.lower()]
    if len(hits) == 1:
        return hits[0]
    known = ", ".join(f.id for f in funds)
    raise ConfigError(f"No single fund matches '{query}'. Known ids: {known}")


def load_settings() -> Settings:
    path = Path(os.environ.get("EDGAR13F_CONFIG") or REPO / "config.local.json")
    local = json.loads(path.read_text()) if path.exists() else {}

    def pick(key: str, env: str, default=None):
        return os.environ.get(env) or local.get(key) or default

    vault = pick("vault_dir", "EDGAR13F_VAULT_DIR")
    return Settings(
        user_agent=pick("user_agent", "EDGAR13F_USER_AGENT"),
        vault_dir=Path(vault).expanduser() if vault else None,
        note_folder=pick("note_folder", "EDGAR13F_NOTE_FOLDER", "Invest/13F Reports"),
        note_title=pick("note_title", "EDGAR13F_NOTE_TITLE", "13F Report {date}"),
        data_dir=Path(pick("data_dir", "EDGAR13F_DATA_DIR", "~/.local/share/edgar13f")).expanduser(),
    )


def require_user_agent(settings: Settings) -> str:
    if not settings.user_agent:
        raise ConfigError(
            "SEC requires a declared User-Agent with a contact email. "
            "Set user_agent in config.local.json or EDGAR13F_USER_AGENT."
        )
    return settings.user_agent


def openfigi_key() -> Optional[str]:
    """Optional OpenFIGI key from the env or the macOS keychain. Without one, mapping is slower."""
    key = os.environ.get("OPENFIGI_API_KEY")
    if key:
        return key
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_OPENFIGI, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None

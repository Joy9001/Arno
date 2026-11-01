"""Runtime configuration, read from the environment once per process."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_TRUSTED_HOSTS = ("localhost", "127.0.0.1", "testserver", "*.ts.net")


class ConfigurationError(RuntimeError):
    """Raised when the environment cannot produce a usable configuration."""


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    web_dir: Path
    rp_id: str
    rp_origin: str
    rp_name: str
    trusted_hosts: tuple[str, ...]
    cookie_secure: bool
    session_ttl_days: int
    setup_token_ttl_hours: int
    auto_migrate: bool
    agent_ro_password: str | None
    backup_password: str | None

    @property
    def cookie_name(self) -> str:
        return "fin_agent_session"

    @classmethod
    def from_env(cls) -> Settings:
        database_url = os.environ.get("DATABASE_URL", "").strip()
        if not database_url:
            raise ConfigurationError(
                "DATABASE_URL is not set. Example: postgresql://app:app@localhost:5432/fin_agent"
            )
        try:
            from psycopg.conninfo import conninfo_to_dict

            conninfo_to_dict(database_url)
        except Exception as exc:  # noqa: BLE001 - normalize driver errors
            raise ConfigurationError(f"DATABASE_URL is not a valid libpq URI: {exc}") from exc

        rp_id = os.environ.get("FIN_AGENT_RP_ID", "localhost").strip()
        rp_origin = os.environ.get("FIN_AGENT_RP_ORIGIN", f"http://{rp_id}:8000").strip()
        if not rp_origin.startswith(("http://", "https://")):
            raise ConfigurationError("FIN_AGENT_RP_ORIGIN must be an http(s) origin")

        hosts = os.environ.get("FIN_AGENT_TRUSTED_HOSTS")
        trusted_hosts = (
            tuple(h.strip() for h in hosts.split(",") if h.strip())
            if hosts
            else DEFAULT_TRUSTED_HOSTS
        )

        web_dir = Path(os.environ.get("FIN_AGENT_WEB_DIR", REPO_ROOT / "web" / "dist"))

        return cls(
            database_url=database_url,
            web_dir=web_dir,
            rp_id=rp_id,
            rp_origin=rp_origin,
            rp_name=os.environ.get("FIN_AGENT_RP_NAME", "Fin-Agent").strip(),
            trusted_hosts=trusted_hosts,
            cookie_secure=_flag("FIN_AGENT_COOKIE_SECURE", rp_origin.startswith("https://")),
            session_ttl_days=int(os.environ.get("FIN_AGENT_SESSION_TTL_DAYS", "30")),
            setup_token_ttl_hours=int(os.environ.get("FIN_AGENT_SETUP_TOKEN_TTL_HOURS", "24")),
            auto_migrate=_flag("FIN_AGENT_AUTO_MIGRATE", True),
            agent_ro_password=os.environ.get("AGENT_RO_PASSWORD") or None,
            backup_password=os.environ.get("BACKUP_PASSWORD") or None,
        )


_cached: Settings | None = None


def get_settings(refresh: bool = False) -> Settings:
    """Return the process configuration. Tests clear this with `refresh=True`."""
    global _cached
    if _cached is None or refresh:
        _cached = Settings.from_env()
    return _cached

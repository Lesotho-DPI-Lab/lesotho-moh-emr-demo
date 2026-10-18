"""Runtime settings, read once from the environment.

Secrets (database password, session key, initial user passwords) come only
from the environment, never from code or the repository.
"""

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("EMR_DATABASE_URL", "sqlite:///./emr.db"))
    secret_key: str | None = field(default_factory=lambda: _env("EMR_SECRET_KEY"))
    # Cookies are marked Secure behind nginx (HTTPS); tests run over plain HTTP.
    secure_cookies: bool = field(default_factory=lambda: _env("EMR_SECURE_COOKIES", "true") == "true")
    facility: str = field(default_factory=lambda: _env("EMR_FACILITY", "Queen 'Mamohato Memorial Hospital"))

    # X-Road: the EMR is the client subsystem CHILD-EMR on moh-ss and calls the
    # MoHA civil registry through moh-ss's consumer interface.
    xroad_ss_url: str = field(default_factory=lambda: _env("EMR_XROAD_SS_URL", "https://10.255.100.184:8443"))
    xroad_client: str = field(default_factory=lambda: _env("EMR_XROAD_CLIENT", "LS-DEMO/GOV/MOH/CHILD-EMR"))
    xroad_service: str = field(
        default_factory=lambda: _env("EMR_XROAD_SERVICE", "LS-DEMO/GOV/MOHA/CIVIL-REGISTRY/child-verification")
    )
    xroad_purpose: str = field(default_factory=lambda: _env("EMR_XROAD_PURPOSE", "CHILD_HEALTH_SERVICE"))
    xroad_timeout: float = field(default_factory=lambda: float(_env("EMR_XROAD_TIMEOUT", "20")))
    # TLS towards moh-ss: our client certificate (registered on moh-ss as a
    # CHILD-EMR internal TLS certificate) and moh-ss's pinned internal cert.
    xroad_client_cert: str | None = field(default_factory=lambda: _env("EMR_XROAD_CLIENT_CERT"))
    xroad_client_key: str | None = field(default_factory=lambda: _env("EMR_XROAD_CLIENT_KEY"))
    xroad_ss_cert: str | None = field(default_factory=lambda: _env("EMR_XROAD_SS_CERT"))
    # Seconds between automatic retries of PENDING verifications; 0 disables.
    retry_interval: int = field(default_factory=lambda: int(_env("EMR_RETRY_INTERVAL", "300")))
    instance_name: str = field(default_factory=lambda: _env("EMR_INSTANCE_NAME", "LS-DEMO"))


settings = Settings()

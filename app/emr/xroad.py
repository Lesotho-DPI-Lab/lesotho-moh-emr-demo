"""Civil registry client: verifies a child's identity with the Ministry of Home
Affairs over X-Road.

The EMR is the X-Road client subsystem LS-DEMO/GOV/MOH/CHILD-EMR. It calls the
REST service LS-DEMO/GOV/MOHA/CIVIL-REGISTRY/child-verification through the
consumer interface of moh-ss:

    POST {moh-ss}/r1/LS-DEMO/GOV/MOHA/CIVIL-REGISTRY/child-verification/verifications
    X-Road-Client: LS-DEMO/GOV/MOH/CHILD-EMR
    X-Road-UserId: <nurse username>
    X-Road-Issue:  <guardian consent reference>

Everything X-Road-specific lives here, so a SOAP variant of the EMR (through
the SOAP->REST adapter) only needs another implementation of `verify`.

Every answer is reduced to one of four outcomes:
    VERIFIED  identity confirmed; attributes returned
    DECLINED  RC-01 not found, RC-02 partial match, RC-05 restricted
    PENDING   registry or X-Road unreachable (RC-06, network, timeout): retry later
    ERROR     request refused (RC-03, RC-04, invalid request, X-Road access denied)
"""

import logging
import ssl
import time
from dataclasses import dataclass, field
from datetime import date

import httpx

from .config import settings
from .reason_codes import REASON_CODES

log = logging.getLogger("emr.xroad")

DECLINE_CODES = {"RC-01", "RC-02", "RC-05"}
ERROR_CODES = {"RC-03", "RC-04"}
# X-Road fault codes that mean "try again later" rather than "misconfigured".
TRANSIENT_XROAD_FAULTS = ("NetworkError", "Timeout", "ServiceFailed", "Unavailable", "OutdatedGlobalConf")


@dataclass
class VerificationResult:
    outcome: str
    reason_code: str | None = None
    message: str | None = None
    person: dict | None = None
    registry_verification_id: str | None = None
    xroad_id: str | None = None
    http_status: int | None = None
    duration_ms: int | None = None
    remedy: dict | None = field(default=None)

    @property
    def reason(self):
        return REASON_CODES.get(self.reason_code) if self.reason_code else None


def _ssl_context() -> ssl.SSLContext | bool:
    if not settings.xroad_ss_url.startswith("https"):
        return False
    if settings.xroad_ss_cert:
        # Pin moh-ss's self-signed internal TLS certificate. Its CN is the
        # server name, not the IP we connect to, so the pin replaces hostname
        # checking: only the holder of that exact certificate's key passes.
        ctx = ssl.create_default_context(cafile=settings.xroad_ss_cert)
        ctx.check_hostname = False
    else:
        ctx = ssl.create_default_context()
    if settings.xroad_client_cert and settings.xroad_client_key:
        ctx.load_cert_chain(settings.xroad_client_cert, settings.xroad_client_key)
    return ctx


class CivilRegistryClient:
    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._transport = transport
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            kwargs = {"timeout": settings.xroad_timeout}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            else:
                kwargs["verify"] = _ssl_context()
            self._client = httpx.Client(base_url=settings.xroad_ss_url.rstrip("/"), **kwargs)
        return self._client

    def _headers(self, user_id: str | None = None, consent_reference: str | None = None) -> dict:
        headers = {"X-Road-Client": settings.xroad_client, "Accept": "application/json"}
        if user_id:
            headers["X-Road-UserId"] = user_id
        if consent_reference:
            headers["X-Road-Issue"] = consent_reference
        return headers

    def verify(self, *, identifier_type: str, identifier: str, first_name: str, surname: str,
               date_of_birth: date, user_id: str, consent_reference: str) -> VerificationResult:
        body = {
            "purpose": settings.xroad_purpose,
            identifier_type: identifier,
            "firstName": first_name,
            "surname": surname,
            "dateOfBirth": date_of_birth.isoformat(),
        }
        started = time.monotonic()
        try:
            response = self.client.post(f"/r1/{settings.xroad_service}/verifications", json=body,
                                        headers=self._headers(user_id, consent_reference))
        except httpx.TransportError as exc:
            log.warning("civil registry unreachable via %s: %s", settings.xroad_ss_url, exc)
            return VerificationResult(
                "PENDING", "RC-06",
                f"Security server unreachable ({type(exc).__name__}). Verification will be retried.",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        result = interpret(response)
        result.duration_ms = int((time.monotonic() - started) * 1000)
        log.info("verification %s %s x-road-id=%s %d ms", result.outcome, result.reason_code or "",
                 result.xroad_id, result.duration_ms)
        return result

    def check_connection(self) -> dict:
        """Ask moh-ss which civil registry services CHILD-EMR may call (allowedMethods)."""
        provider = settings.xroad_service.rsplit("/", 1)[0]
        started = time.monotonic()
        try:
            r = self.client.get(f"/r1/{provider}/allowedMethods", headers=self._headers())
        except httpx.TransportError as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "services": []}
        elapsed = int((time.monotonic() - started) * 1000)
        try:
            payload = r.json()
        except ValueError:
            payload = {}
        if r.status_code != 200 or r.headers.get("X-Road-Error"):
            return {"ok": False, "status": r.status_code, "elapsed_ms": elapsed,
                    "error": payload.get("type") or r.headers.get("X-Road-Error") or r.text[:300],
                    "detail": payload.get("message"), "services": []}
        services = [s.get("service_code") for s in payload.get("service", []) if isinstance(s, dict)]
        return {"ok": True, "status": r.status_code, "elapsed_ms": elapsed, "services": services,
                "xroad_id": r.headers.get("X-Road-Id")}


def interpret(response: httpx.Response) -> VerificationResult:
    """Map a security server / registry response to a VerificationResult."""
    xroad_id = response.headers.get("X-Road-Id")
    status = response.status_code
    try:
        payload = response.json()
    except ValueError:
        payload = None

    # A fault raised by a security server rather than an answer from the registry.
    fault = response.headers.get("X-Road-Error")
    if fault or (isinstance(payload, dict) and "type" in payload and "outcome" not in payload):
        fault_type = (payload or {}).get("type") or fault or "X-Road error"
        detail = (payload or {}).get("message") or response.text[:300]
        transient = any(t in fault_type for t in TRANSIENT_XROAD_FAULTS)
        return VerificationResult(
            "PENDING" if transient else "ERROR", "RC-06" if transient else None,
            f"{fault_type}: {detail}", xroad_id=xroad_id, http_status=status,
        )

    if not isinstance(payload, dict) or "outcome" not in payload:
        return VerificationResult("PENDING" if status >= 500 else "ERROR", "RC-06" if status >= 500 else None,
                                  f"Unexpected response from registry (HTTP {status})",
                                  xroad_id=xroad_id, http_status=status)

    code = payload.get("reasonCode")
    common = dict(
        reason_code=code, message=payload.get("message"), remedy=payload.get("remedy"),
        registry_verification_id=payload.get("verificationId"), xroad_id=xroad_id, http_status=status,
    )
    if payload["outcome"] == "VERIFIED" and payload.get("person"):
        return VerificationResult("VERIFIED", person=payload["person"], **common)
    if payload["outcome"] == "ERROR":
        common["message"] = payload.get("message") or payload.get("error")
        return VerificationResult("ERROR", **common)
    if code in DECLINE_CODES:
        return VerificationResult("DECLINED", **common)
    if code == "RC-06":
        return VerificationResult("PENDING", **common)
    return VerificationResult("ERROR", **common)


_registry = CivilRegistryClient()


def registry() -> CivilRegistryClient:
    return _registry


def use_transport(transport: httpx.BaseTransport | None) -> None:
    """Swap the HTTP transport (tests use httpx.MockTransport)."""
    global _registry
    _registry = CivilRegistryClient(transport)

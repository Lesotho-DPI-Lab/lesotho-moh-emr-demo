"""Reason-code catalogue v0 (DM-001, SAF.1.4), as published by the MoHA civil
registry. Kept here so stored verification attempts can show their remedy.

Every decline carries a machine-readable code and a remedy route. No decline
stops care: the remedy always lets the visit continue.

The Sesotho remedy texts are drafts and must be reviewed by a Sesotho speaker
on the team before the demonstration.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReasonCode:
    code: str
    name: str
    meaning: str
    remedy_en: str
    remedy_st: str
    http_status: int


REASON_CODES = {
    rc.code: rc
    for rc in (
        ReasonCode(
            "RC-01",
            "NOT_FOUND",
            "No birth record matches the details",
            "Refer the guardian to civil registration; record the child as unverified",
            "Romela mohlokomeli ngolisong ea sechaba; ngola ngoana e le ea sa netefatsoang",
            200,
        ),
        ReasonCode(
            "RC-02",
            "PARTIAL_MATCH",
            "Number found, but name or date of birth differs",
            "Confirm details with the guardian; if still different, refer to the registrar for correction",
            "Netefatsa lintlha le mohlokomeli; haeba li ntse li fapane, mo romele ho mongoli hore li lokisoe",
            200,
        ),
        ReasonCode(
            "RC-03",
            "PURPOSE_MISSING",
            "No purpose, or a purpose not allowed for this client",
            "System issue: tell the EMR administrator; continue the visit",
            "Bothata ba sistimi: tsebisa motsamaisi oa EMR; tsoela pele ka ketelo",
            400,
        ),
        ReasonCode(
            "RC-04",
            "CONSENT_REF_MISSING",
            "No consent or encounter reference",
            "Record the guardian's consent and retry",
            "Ngola tumello ea mohlokomeli ebe o leka hape",
            400,
        ),
        ReasonCode(
            "RC-05",
            "RECORD_RESTRICTED",
            "Record under restriction (for example adoption or protection)",
            "No details released; refer to the registrar through the official channel",
            "Ha ho lintlha tse fanoang; romela ho mongoli ka tsela ea semmuso",
            200,
        ),
        ReasonCode(
            "RC-06",
            "SERVICE_UNAVAILABLE",
            "Registry cannot be reached",
            "Continue the visit; verification marked pending and retried",
            "Tsoela pele ka ketelo; netefatso e tla tšoaoa e emetse 'me e lekoe hape",
            503,
        ),
    )
}

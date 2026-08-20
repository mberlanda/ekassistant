"""Mock CRM account lookup. READ_ONLY, INTERNAL_DATA.

Exists in Phase 0 mainly to carry `authorized_contacts`. The email
workflow's most important control is that a recipient must come from the
account relationship rather than from model output or from anything
web-derived, and that control needs an authoritative list to check
against from the moment the workflow is written - not later.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from ekassistant.capabilities.contracts import (
    Classification,
    EffectClass,
    InvocationContext,
    Limits,
    ToolContract,
    ToolResult,
    Zone,
)
from ekassistant.capabilities.mocks._shared import mock_result


class CrmLookupInput(BaseModel):
    account_id: str = Field(min_length=1)


class CrmContact(BaseModel):
    name: str
    email: str
    role: str


class CrmLookupOutput(BaseModel):
    account_id: str
    account_name: str
    owner_principal_id: str
    relationship_status: str
    authorized_contacts: list[CrmContact]


CONTRACT = ToolContract(
    tool="crm_account_lookup",
    version=1,
    effect_class=EffectClass.READ_ONLY,
    zone=Zone.INTERNAL_DATA,
    required_scopes=frozenset({"crm.accounts.read"}),
    max_classification=Classification.CONFIDENTIAL,
    input_model=CrmLookupInput,
    output_model=CrmLookupOutput,
    limits=Limits(timeout_ms=3_000, max_rows=1),
    audit_fields=("principal_id", "tenant_id", "purpose", "account_id", "result_hash"),
    description="Read one account's summary and its authorized contact list.",
)

# Two accounts, deliberately owned by different principals, so an
# ownership check has something to fail against rather than trivially
# passing for every caller.
_ACCOUNTS: dict[str, dict[str, Any]] = {
    "acct-1001": {
        "account_id": "acct-1001",
        "account_name": "Northwind Logistics",
        "owner_principal_id": "bob",
        "relationship_status": "active",
        "authorized_contacts": [
            {"name": "Dana Reyes", "email": "dana.reyes@northwind.example", "role": "primary"},
            {"name": "Sam Okafor", "email": "sam.okafor@northwind.example", "role": "billing"},
        ],
    },
    "acct-2002": {
        "account_id": "acct-2002",
        "account_name": "Halcyon Manufacturing",
        "owner_principal_id": "carol",
        "relationship_status": "renewal_pending",
        "authorized_contacts": [
            {"name": "Priya Nair", "email": "priya.nair@halcyon.example", "role": "primary"},
        ],
    },
}


class MockCrmAccountLookup:
    contract = CONTRACT

    def invoke(self, payload: Mapping[str, Any], ctx: InvocationContext) -> ToolResult:
        account_id = payload["account_id"]
        account = _ACCOUNTS.get(account_id)
        if account is None:
            # A miss raises rather than returning an empty record. An
            # empty account would flow onward as "this account has no
            # authorized contacts", which reads as a permission result
            # rather than a lookup failure.
            raise KeyError(f"unknown account {account_id!r}")
        return mock_result(CONTRACT, account, source_ref=f"crm://accounts/{account_id}")

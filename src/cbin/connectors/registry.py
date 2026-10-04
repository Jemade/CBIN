import json
import os

from cbin.connectors.base import ConnectorError
from cbin.connectors.sandbox import SandboxAdapter
from cbin.connectors.zoho import ZohoBooksAdapter


def adapter_for(business_id, environment):
    config = json.loads(os.environ.get("CBIN_CONNECTOR_CONFIG") or "{}")
    entry = config.get(business_id)
    if not entry:
        raise ConnectorError("CONNECTOR_NOT_CONFIGURED")
    if entry["type"] == "sandbox" and environment == "test":
        return SandboxAdapter()
    if entry["type"] == "zoho" and environment == "test":
        return ZohoBooksAdapter(entry)
    # Live ledger posting is deliberately gated until sandbox reconciliation is validated.
    raise ConnectorError("CONNECTOR_NOT_APPROVED_FOR_ENVIRONMENT")

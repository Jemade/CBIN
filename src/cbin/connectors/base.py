from typing import Protocol


class ConnectorError(Exception):
    """A safe code only: provider messages may contain secrets or personal data."""


class AmbiguousOutcome(ConnectorError):
    pass


class PostingAdapter(Protocol):
    def capabilities(self) -> dict: ...
    def post_to_ledger(self, document_id: str, invoice: dict, mapping: dict) -> str: ...
    def recover_from_timeout(
        self, document_id: str, invoice: dict, mapping: dict
    ) -> str | None: ...

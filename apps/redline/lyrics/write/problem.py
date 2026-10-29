"""What a check finds wrong in a draft."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Problem:
    """Something a draft must fix: what kind, where, and how."""

    kind: str
    where: str
    detail: str

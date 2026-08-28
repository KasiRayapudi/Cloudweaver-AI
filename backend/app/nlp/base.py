"""Extractor interface shared by the rule based and LLM backed implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.ir import InfrastructureSpec


class Extractor(ABC):
    """Turns a natural language requirement into a draft ``InfrastructureSpec``.

    An extractor is responsible only for what the *user actually said*.  It must
    not invent supporting resources -- filling in VPCs, subnets and security
    groups is the mapper's job, so that implied infrastructure is added by one
    consistent set of rules regardless of which extractor ran.
    """

    name: str = "base"

    @abstractmethod
    def extract(self, prompt: str) -> InfrastructureSpec:
        """Parse ``prompt`` into a draft specification."""

    @property
    def available(self) -> bool:
        """Whether this extractor can run in the current environment."""
        return True


class ExtractionError(RuntimeError):
    """Raised when an extractor cannot produce a usable specification."""

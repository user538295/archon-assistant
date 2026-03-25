"""Tests for ContextProvider protocol."""
from pathlib import Path

from archon.ai.context_provider import ContextProvider


class _ConcreteProvider:
    def get_recent_context(self) -> str | None:
        return "summary"

    def get_context_files(self) -> list[Path]:
        return []

    def startup_context_prompt(self, rag_enabled: bool = False) -> str:
        return "prompt"


class _IncompleteProvider:
    def get_recent_context(self) -> str | None:
        return None


def test_isinstance_returns_true_for_compliant_class() -> None:
    assert isinstance(_ConcreteProvider(), ContextProvider)


def test_isinstance_returns_false_for_non_compliant_class() -> None:
    assert not isinstance(_IncompleteProvider(), ContextProvider)


def test_isinstance_returns_false_for_plain_object() -> None:
    assert not isinstance(object(), ContextProvider)

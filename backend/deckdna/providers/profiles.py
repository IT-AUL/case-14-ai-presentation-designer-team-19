"""Optional introspection: which models a gateway actually invoked.

NOT part of the frozen ``ModelGateway`` protocol
(see ``providers/base.py``): providers MAY implement
``used_model_ids()`` — consumers check ``isinstance`` and fall back to
an honest empty report instead of assuming. Record only calls that
returned successfully: a failed call means the model produced nothing
used downstream (LLM-planner fallback must not claim the model).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ModelProfileSource(Protocol):
    """Provider that can report the models behind its real calls."""

    provider_name: str
    """Honest provider identity: endpoint host[:port], 'mock', 'vk', ...

    MUST be sanitized — this string lands in Quality Passport
    artifacts: no userinfo (user:pass@host), no API keys, no paths."""

    def used_model_ids(self) -> dict[str, str]:
        """role -> model_id for calls that returned successfully.

        Roles: 'text', 'vision', 'embed', 'image' (ADR-017). A role absent
        means that modality was never invoked through this gateway.
        """


@runtime_checkable
class UsageSource(Protocol):
    """Provider that counts its real model calls and reported tokens.

    Also NOT part of the frozen ``ModelGateway`` protocol: consumers check
    ``isinstance``; a gateway without accounting yields an honest zero
    report instead of invented numbers. Only calls that returned a
    well-formed response are counted; tokens come from the provider's own
    ``usage`` block (absent block → 0 tokens, the call still counts).
    """

    def usage_report(self) -> dict[str, dict[str, Any]]:
        """model_id -> {"calls": int, "input_tokens": int, "output_tokens": int}."""

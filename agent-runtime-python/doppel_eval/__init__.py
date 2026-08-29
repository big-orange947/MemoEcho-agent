"""Doppel shadow evaluation support for MemoEcho.

Synthetic/replay test data infrastructure. Never touches the real QQ link;
all events are generated locally and validated against the runtime's
UnifiedEvent schema so that live, replay and synthetic modes share one
bridge semantics.
"""

from doppel_eval.generators import Tier, generate_dataset

__all__ = ["Tier", "generate_dataset"]

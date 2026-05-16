from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RuntimeProbeState:
    """Mutable state populated by engine probes; read by generation strategies. Defaults outside a run."""

    path_decoder_strict: bool = False

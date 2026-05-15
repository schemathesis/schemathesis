from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RuntimeProbeState:
    """Mutable state populated by engine-side probes during a run.

    Schemas hold a `RuntimeProbeState` for cross-phase persistence within a single run. Engine probes
    write into it; generation strategies read from it. Outside an engine run, the state stays at defaults.
    """

    path_decoder_strict: bool = False

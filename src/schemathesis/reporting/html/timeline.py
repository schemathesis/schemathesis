"""Plain-data calculations behind the HTML report timeline."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from schemathesis.core.output import pluralize
from schemathesis.reporting.html.model import AFTER_RUN

if TYPE_CHECKING:
    from schemathesis.reporting.html.model import PhaseSpan, ReportData


class Marker(NamedTuple):
    found_at: float
    phase: str
    href: str
    # Operation label and failure titles, for the tooltip.
    description: str
    # Failures of this kind on the response, as the top failures count them.
    failures: int = 1


# Steps for axis ticks, in seconds; the smallest one giving at most this many ticks wins.
TICK_STEPS = (0.001, 0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30) + (
    60,
    120,
    300,
    600,
    900,
    1800,
    3600,
    7200,
    14400,
    21600,
    43200,
    86400,
)
MAX_TICKS = 6
# Axis ticks past this share of the track would collide with the end label.
AXIS_END_CLEARANCE = 0.88
# A stretch without new failures is shaded only when it covers at least this share of the run.
QUIET_SHARE = 0.2
# Track width at a 1300px viewport, where the page stops growing; narrow layouts hide ticks.
TRACK_PX = 1000
# Ticks closer than this are indistinguishable and share one tick.
TICK_GAP_PX = 8
# A count badge spans this far right of its tick, so the next tick starts past it.
BADGE_PX = 22
# Ticks past this share open their tooltip leftwards, so it stays inside the track.
TIP_END_SHARE = 0.8
# Ticks past this share put their badge on the left, so it stays inside the track.
BADGE_END_SHARE = 0.97
# Above this many ticks across all lanes the chart is cluttered, and the curve alone shows when failures were found.
MAX_DRAWN_TICKS = 50
# The curve tops out at this height of 100, so its stroke at the final count stays inside the track.
CURVE_HEADROOM = 92
# Above this many steps the curve merges neighbours, which sit under 2px apart on the widest track.
CURVE_STEPS = 500


def short_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{round(seconds * 1000)}ms"
    if seconds < 60:
        return f"{seconds:.1f}".removesuffix(".0") + "s"
    minutes, rest = divmod(round(seconds), 60)
    if minutes >= 60:
        return f"{minutes // 60}h {minutes % 60}m" if minutes % 60 else f"{minutes // 60}h"
    return f"{minutes}m {rest}s" if rest else f"{minutes}m"


def share(seconds: float, total: float) -> float:
    return min(max(seconds / total, 0.0), 1.0)


def position(seconds: float, total: float) -> str:
    """Offset along the track as a CSS percentage."""
    return f"{share(seconds, total) * 100:.2f}%"


def total_duration(data: ReportData) -> float:
    """Length of the charted run; zero when there is nothing to chart."""
    if not data.phase_spans:
        return 0.0
    return max(0.0, data.running_time or 0.0, *(span.end for span in data.phase_spans))


def collect_markers(data: ReportData, anchors: dict[str, str]) -> list[Marker]:
    owned = [(row.label, anchors[row.label], case) for row in data.operations for case in row.failing_cases]
    owned.extend((label, "run-level", case) for label, case in data.unattributed_cases)
    markers: list[Marker] = []
    for label, fallback, case in owned:
        if case.found_at is None or case.found_in is None:
            continue
        href = f"case-{case.case_id}" if case.case_id is not None else fallback
        # One per failure kind; kinds found together on one case share its card.
        markers.extend(
            Marker(case.found_at, case.found_in, href, f"{label}: {failure.title}", len(failure.messages))
            for failure in case.failures
        )
    return sorted(markers)


def failure_count(markers: list[Marker]) -> int:
    return sum(marker.failures for marker in markers)


def found_in_phases(markers: list[Marker]) -> list[Marker]:
    return [marker for marker in markers if marker.phase != AFTER_RUN]


def quiet_stretch(markers: list[Marker], total: float) -> float | None:
    """How long the run went on after its last failure found in a phase, when long enough to shade."""
    found = found_in_phases(markers)
    if not found:
        return None
    quiet = total - found[-1].found_at
    return quiet if quiet >= total * QUIET_SHARE else None


def curve_path(markers: list[Marker], total: float) -> str:
    """SVG path in a 1000x100 box that steps up at each new failure; the y axis spans 0 to the final count."""
    count = failure_count(markers)
    # Failures found by each drawn x, in tenths of a box unit.
    reached: dict[int, int] = {}
    found = 0
    for marker in markers:
        found += marker.failures
        reached[round(share(marker.found_at, total) * 10000)] = found
    if len(reached) > CURVE_STEPS:
        width = 10000 // CURVE_STEPS
        # A step moves to the end of its stretch, so the curve never rises before a failure is found.
        reached = {-(-x // width) * width: found for x, found in reached.items()}
    path = "M0,100"
    previous_x, previous_y = 0, 1000
    for x, found in reached.items():
        y = round(1000 - found / count * CURVE_HEADROOM * 10)
        if y == previous_y:
            continue
        if x != previous_x:
            path += f"H{_tenths(x)}"
        path += f"V{_tenths(y)}"
        previous_x, previous_y = x, y
    return path + "H1000"


def _tenths(value: int) -> str:
    return f"{value / 10:.1f}".removesuffix(".0")


def tick_groups(markers: list[Marker], total: float) -> list[list[Marker]]:
    groups: list[list[Marker]] = []
    for marker in markers:
        if groups:
            reach = BADGE_PX if failure_count(groups[-1]) > 1 else TICK_GAP_PX
            if (marker.found_at - groups[-1][0].found_at) / total * TRACK_PX < reach:
                groups[-1].append(marker)
                continue
        groups.append([marker])
    return groups


def lane_ticks(lanes: list[Lane], total: float) -> list[list[list[Marker]]]:
    groups = [tick_groups(lane.markers, total) for lane in lanes]
    if sum(len(lane_groups) for lane_groups in groups) > MAX_DRAWN_TICKS:
        return [[] for _ in lanes]
    return groups


def tick_lines(group: list[Marker]) -> list[str]:
    return [f"{short_duration(marker.found_at)}: {marker.description}" for marker in group]


def axis_ticks(total: float) -> list[float]:
    step = next((step for step in TICK_STEPS if total / step <= MAX_TICKS), TICK_STEPS[-1])
    return [index * step for index in range(int(total / step) + 1) if index * step < total * AXIS_END_CLEARANCE]


def axis_label(seconds: float) -> str:
    return short_duration(seconds) if seconds else "0"


def axis_end(total: float, *, stopped: bool) -> str:
    return f"{short_duration(total)}, stopped" if stopped else short_duration(total)


class Lane(NamedTuple):
    phase: str
    # Stretches of the phase running, as (start, end) seconds from the run start; empty for failures outside phases.
    passes: list[tuple[float, float]]
    markers: list[Marker]
    errors: int = 0

    def details(self) -> str:
        parts = []
        if self.passes:
            spent = short_duration(sum(end - start for start, end in self.passes))
            parts.append(f"{spent} in {len(self.passes)} passes" if len(self.passes) > 1 else spent)
        if self.markers:
            parts.append(f"{failure_count(self.markers)} found")
        if self.errors:
            parts.append(pluralize(self.errors, "error"))
        return ", ".join(parts)


def phase_blocks(spans: list[PhaseSpan]) -> dict[str, list[tuple[float, float]]]:
    """Each phase's stretches of running time; back-to-back passes of one phase form a single stretch."""
    blocks: dict[str, list[tuple[float, float]]] = {}
    previous = None
    for span in spans:
        stretches = blocks.setdefault(span.phase, [])
        if span.phase == previous:
            stretches[-1] = (stretches[-1][0], span.end)
        else:
            stretches.append((span.start, span.end))
        previous = span.phase
    return blocks


def build_lanes(data: ReportData, markers: list[Marker]) -> list[Lane]:
    lanes = {phase: Lane(phase, passes, []) for phase, passes in phase_blocks(data.phase_spans).items()}
    for marker in markers:
        lanes.setdefault(marker.phase, Lane(marker.phase, [], [])).markers.append(marker)
    return [lane._replace(errors=data.phase_errors.get(lane.phase, 0)) for lane in lanes.values()]


def mark_classes(seconds: float, total: float) -> str:
    at = share(seconds, total)
    classes = " tl-tip-end" if at > TIP_END_SHARE else ""
    return classes + " tl-mark-end" if at > BADGE_END_SHARE else classes


def last_phase(lanes: list[Lane]) -> str:
    """The phase whose bar ends last, the one an interrupted run cut short."""
    return max((lane for lane in lanes if lane.passes), key=lambda lane: lane.passes[-1][1]).phase

"""Exact decoded presentation-frame timing, independent of packetization."""
from dataclasses import dataclass
from fractions import Fraction

from src.publisher.headline_tools import RenderError


@dataclass(frozen=True)
class FrameTimeline:
    times: tuple
    extent: Fraction
    time_base: Fraction


def frame_timeline(frames, time_base):
    try:
        tick = Fraction(time_base)
        if tick <= 0 or not frames:
            raise ValueError()
        values = [Fraction(int(frame['best_effort_timestamp'])) * tick for frame in frames]
        if any(right <= left for left, right in zip(values, values[1:])):
            raise ValueError()
        times = tuple(value - values[0] for value in values)
        duration = int(frames[-1].get('pkt_duration') or 0) * tick
        if duration <= 0:
            duration = times[-1] - times[-2] if len(times) > 1 else tick
        return FrameTimeline(times, times[-1] + duration, tick)
    except (ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
        raise RenderError('HEADLINE_TIMING_UNSUPPORTED') from exc


def validate_frame_timeline(source, output):
    if len(source.times) != len(output.times):
        raise RenderError('HEADLINE_OUTPUT_CADENCE_INVALID')
    intervals = [b-a for timeline in (source, output)
                 for a,b in zip(timeline.times, timeline.times[1:])]
    # Normalizing two quantized timestamps can accumulate one tick per stream.
    # The half-interval bound prevents matching a different presentation frame.
    tolerance = source.time_base + output.time_base
    if intervals:
        tolerance = min(tolerance, min(intervals) / 2)
    if any(abs(a-b) > tolerance for a,b in zip(source.times, output.times)):
        raise RenderError('HEADLINE_OUTPUT_CADENCE_INVALID')
    # MP4 edit lists may trim the last frame's declared duration; frame count
    # and every presentation instant remain strict, with one final-frame bound.
    terminal_interval = max([timeline.times[-1]-timeline.times[-2]
                             for timeline in (source, output) if len(timeline.times)>1]
                            or [source.time_base, output.time_base])
    if abs(source.extent-output.extent) > terminal_interval + tolerance:
        raise RenderError('HEADLINE_OUTPUT_CADENCE_INVALID')

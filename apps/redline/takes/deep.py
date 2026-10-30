"""The slow part of a take's ranking: how close the auto settings bring it to the references."""

from pathlib import Path

from redline.measure.separate import Separator


def deep_distance(
    take: Path, beat: Path, refs: list[Path], work_dir: Path, separator: Separator
) -> float:
    """Distance to the references after the own-vocal auto search, as `redline voice` runs it.

    Args:
        take: The take; its frame 0 is the beat's, so no offset is applied.
        beat: The beat it was recorded over.
        refs: The reference files.
        work_dir: Folder holding the measuring caches.
        separator: Separates the references.

    Returns:
        The auto search's distance after the search: 0 at the references' medians.
    """
    from redline.engine.track import track_engine
    from redline.measure.run import measure_own
    from redline.vocal.auto import auto_settings

    inputs = measure_own(take, beat, refs, work_dir, separator, 0.0)
    found = auto_settings(track_engine(inputs), inputs.refs, separator=separator)
    return found.distance_after

"""Stress marks from ruaccent, an optional extra: `uv sync --extra stress`."""

from collections.abc import Callable

from redline.lyrics.text.parse import LyricsError

Stresser = Callable[[str], str]


def load_stresser() -> Stresser:
    """A function putting "+" before the stressed vowel of each word of a line."""
    try:
        from ruaccent import RUAccent
    except ImportError as error:
        raise LyricsError("stress marks need the stress extra: uv sync --extra stress") from error
    model = RUAccent()
    model.load(omograph_model_size="turbo3.1", use_dictionary=True, tiny_mode=False)
    return model.process_all

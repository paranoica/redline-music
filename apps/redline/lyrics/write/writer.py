"""Writers of new texts behind one interface; `handoff` works through files in a session."""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from redline.lyrics.write.recipe import DEVICES, Recipe

PROMPT = Path(__file__).with_name("writer_prompt.md")
REFUSAL = "REFUSED:"
KIND_NAMES = {
    "intro": "Интро",
    "hook": "Припев",
    "prehook": "Предприпев",
    "verse": "Куплет",
    "bridge": "Бридж",
    "outro": "Аутро",
    "other": "Скит",
}


@dataclass(frozen=True)
class WriterRequest:
    """What a writer gets: the prompt, the recipe, the owner's excerpts, and what to fix."""

    prompt: str
    recipe: Recipe
    excerpts: tuple[str, ...]
    reasons: tuple[str, ...]
    round: int
    style: str = ""
    # set for an edit: the text to change and what to change in it
    current: str = ""
    change: str = ""
    sent_back: str = ""
    # the owner's marks on lines of earlier texts
    cringe: tuple[str, ...] = ()
    good: tuple[str, ...] = ()


@dataclass(frozen=True)
class Draft:
    """A text the writer wrote."""

    text: str


@dataclass(frozen=True)
class Waiting:
    """The request is out; the text is not back yet."""

    request: Path
    draft: Path


@dataclass(frozen=True)
class Refusal:
    """The writer would not write the text."""

    reason: str


class Writer(Protocol):
    """Anything that turns a request into a draft, now or later."""

    def ask(self, request: WriterRequest) -> Draft | Waiting | Refusal:
        """Answer the request, or say where the answer will come."""
        ...


def prompt_text() -> str:
    """The writer's prompt, the same for every writer."""
    return PROMPT.read_text(encoding="utf-8")


DELIVERIES = {
    "hard": "the beat hits hard; punchy short hits, shouted adlibs, hard consonants at the "
    "line ends, more chant in the hook",
    "calm": "the beat floats; lines glide, fewer shouts, adlibs as echoes",
}


def prompt_hash() -> str:
    """A short hash of the prompt, kept with every text written under it."""
    return hashlib.sha256(prompt_text().encode()).hexdigest()[:12]


def _change(request: WriterRequest) -> list[str]:
    if not request.change:
        return []
    return [
        "",
        "## The text to change",
        "",
        "```",
        request.current.rstrip(),
        "```",
        "",
        "## The change",
        "",
        request.change.strip(),
        "",
        "Change only what this asks. Keep every other line word for word and keep the "
        "sections, unless the change asks otherwise. Answer with the whole new text.",
        "",
        *(
            [
                "## Your draft that was sent back (the reasons below point at its lines)",
                "",
                "```",
                request.sent_back.rstrip(),
                "```",
                "",
            ]
            if request.sent_back
            else []
        ),
    ]


def _marks(request: WriterRequest) -> list[str]:
    if not (request.cringe or request.good):
        return []
    out = ["", "## The owner's marks on earlier texts", ""]
    if request.cringe:
        out += [
            "Lines the owner called cringe. Write nothing like them: not these words and not "
            "the move behind them.",
            "",
            *[f"- {line}" for line in request.cringe],
            "",
        ]
    if request.good:
        out += [
            "Lines the owner liked: the level to hold. Manner only, never these lines.",
            "",
            *[f"- {line}" for line in request.good],
            "",
        ]
    return out


def render(request: WriterRequest, answer_to: Path | None = None) -> str:
    """The request as one Markdown page."""
    r = request.recipe
    out = [
        request.prompt.rstrip(),
        "",
        "## Recipe",
        "",
        f"- Vibe and anchor words, not a story: {r.topic}",
    ]
    if r.bpm:
        out.append(
            f"- Beat: {r.bpm:g} BPM at half time; one line is one bar. Keep the lines of a "
            "section even, so each fills its bar alike; a line with adlibs keeps to the low end"
        )
    else:
        out.append(f"- Line length: {r.band}")
    if r.delivery:
        out.append(f"- Delivery: {r.delivery}: {DELIVERIES[r.delivery]}")
    out.append("- Sections, in this order:")
    for i, s in enumerate(r.sections, 1):
        low, high = s.syllables
        flow = f", flow {s.flow}" if s.flow else ""
        out.append(
            f"  {i}. [{KIND_NAMES[s.kind]}]: {s.lines} lines of {low}-{high} syllables{flow}, "
            f"rhyme scheme {s.scheme}"
        )
    out.append(f"- Words of the scene to draw on: {', '.join(r.words) or '-'}")
    out.append(f"- English words and brands: {', '.join(r.latin) or '-'}")
    out.append(f"- Adlibs: {', '.join(r.adlibs) or '-'}; on about {r.adlib_share:.0%} of lines")
    out.append(f"- Repeated lines inside a hook: about {r.hook_repeat_share:.0%}")
    out.append(f"- Words in Latin letters: about {r.en_share:.0%}")
    if r.devices:
        out.append("- What this text must also do:")
        out += [f"  - {count} times: {DEVICES[name]}" for name, count in r.devices]
    if request.style.strip():
        out += ["", "## Notes on the scene's style", "", request.style.strip()]
    out += _marks(request)
    if request.excerpts:
        out += ["", "## The owner's voice (manner only, never these lines)", ""]
        for excerpt in request.excerpts:
            out += ["```", excerpt.rstrip(), "```", ""]
    out += _change(request)
    if request.reasons:
        out += ["", f"## Round {request.round}: the last draft was sent back", ""]
        out += [f"- {reason}" for reason in request.reasons]
        out.append("")
        out.append("Write the whole text again with these fixed.")
    if answer_to is not None:
        out += ["", f"Write the text, and nothing else, to `{answer_to}`."]
    return "\n".join(out) + "\n"


class HandoffWriter:
    """Writes the request to a file and reads the draft a Claude Code agent leaves next to it."""

    def __init__(self, folder: Path) -> None:
        """Work in the seed's folder."""
        self.folder = folder

    def ask(self, request: WriterRequest) -> Draft | Waiting | Refusal:
        """Return the draft of this round if it is there, else put the request out."""
        asked = self.folder / f"request-{request.round}.md"
        answer = self.folder / f"draft-{request.round}.txt"
        if answer.exists():
            text = answer.read_text(encoding="utf-8")
            if text.lstrip().startswith(REFUSAL):
                return Refusal(text.lstrip()[len(REFUSAL) :].strip())
            return Draft(text)
        if not asked.exists():
            tmp = asked.with_name(asked.name + ".tmp")
            tmp.write_text(render(request, answer), encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(asked)
        return Waiting(asked, answer)

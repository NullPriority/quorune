"""Match one source attack exile and its linked optional Clue sacrifice copy."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

from ..rules.source_references import SourceReferenceSpec
from . import regex_working_set as re
from .ir_model import SourceSpan


_PRODUCER = re.compile(
    r"Whenever (?P<source>.+?) attacks, exile target card from a graveyard, then investigate\.",
    re.IGNORECASE,
)
_CONSUMER = re.compile(
    r"Whenever you sacrifice a Clue, you may have (?P<source>.+?) become a copy of a creature card "
    r"exiled with it until end of turn\.",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class LinkedClueCopySource:
    attack: SourceSpan
    sacrifice: SourceSpan


def linked_clue_copy_source(
    material_rows: Sequence[tuple[str, str, SourceSpan]], *, source_name: str,
) -> LinkedClueCopySource | None:
    source = SourceReferenceSpec(source_name)

    def match_spans(pattern) -> tuple[SourceSpan, ...]:
        return tuple(span for _, material, span in material_rows
                     if (match := pattern.fullmatch(" ".join(material.split())))
                     and (match["source"].casefold() == "this creature" or source.matches(match["source"])))

    producers = match_spans(_PRODUCER)
    consumers = match_spans(_CONSUMER)
    if len(producers) != 1 or len(consumers) != 1:
        return None
    return LinkedClueCopySource(attack=producers[0], sacrifice=consumers[0])

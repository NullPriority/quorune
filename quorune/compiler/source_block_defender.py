"""Printed results that remove defender from the creature that blocked."""

from ..creature_subtypes import canonical_creature_subtype
from ..source_block_defender_model import SOURCE_BLOCK_DEFENDER_MECHANIC, SourceBlockDefenderSpec
from ..rules.source_block_defender import source_block_defender_instruction
from . import regex_working_set as re


_SUBTYPES_AND_LOSS = re.compile(
    r"it becomes (?:a|an) (?P<first>[a-z'-]+) (?P<second>[a-z'-]+), and it loses defender[.]?",
    re.IGNORECASE,
)


def source_block_defender_result(binding, body: str):
    if (
        binding.event.value != "creature.blocks"
        or binding.variant != "this_creature_blocks"
        or binding.active_zone != "battlefield"
    ):
        return None
    if body.casefold().rstrip(".") == "it loses defender":
        spec = SourceBlockDefenderSpec()
    else:
        match = _SUBTYPES_AND_LOSS.fullmatch(body)
        if match is None:
            return None
        types = tuple(canonical_creature_subtype(match[group]) for group in ("first", "second"))
        if None in types or types[0] == types[1]:
            return None
        spec = SourceBlockDefenderSpec(tuple(sorted(types)))
    return ("blocking-source-loses-defender-v1", (source_block_defender_instruction(spec),), None,
            ("cr-611-continuous-effects", SOURCE_BLOCK_DEFENDER_MECHANIC)), True

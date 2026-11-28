"""Lower the exact equal-X, up-to-three-target legendary sorcery body."""

from . import regex_working_set as re
from ..rules.announced_three_target_damage import effect, target_schema
from ..rules.legendary_sorcery import is_legendary_sorcery
from ..rules.source_references import SourceReferenceSpec


def legendary_sorcery_damage_compiler(fallback, *, record, source_name):
    if not is_legendary_sorcery(record.type_line):
        return fallback
    pattern = re.compile(
        rf"{SourceReferenceSpec(source_name).regex_pattern} deals X damage to each of up to three targets\.", re.I,
    )

    def compile_spell(text, **context):
        if pattern.fullmatch(" ".join(text.split())):
            return "legendary-sorcery-announced-x-each-target-v1", (effect(),), target_schema(), (
                "cr-115-targets", "cr-120-damage",
            )
        return fallback(text, **context)

    return compile_spell

"""Lower the two printed keep-three and sacrifice-complement forms."""

from ..rules.keep_three_sacrifice import keep_three_sacrifice_effect


def keep_three_land_entry_template(text):
    if " ".join(text.casefold().split()) != "each player sacrifices all lands they control except for three.":
        return None
    return ("keep-three-lands-entry-v1", (keep_three_sacrifice_effect(lands_only=True),), None,
            ("fixed-affected-player-sacrifice", "sacrifice"))


def keep_three_permanents_spell_template(text, *, source_is_permanent):
    if (source_is_permanent is not False or " ".join(text.casefold().split()) !=
            "each player chooses three permanents they control, then sacrifices the rest."):
        return None
    return ("keep-three-permanents-spell-v1", (keep_three_sacrifice_effect(lands_only=False),), None,
            ("fixed-affected-player-sacrifice", "sacrifice"))

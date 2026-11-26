from __future__ import annotations

"""Lower the exact private opponent-library partition and linked cast permission."""

from dataclasses import replace

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.private_opponent_library_cast_shapes import private_opponent_library_cast_target_schema
from . import regex_working_set as re
from .draw_templates import trigger_ability_word_material_line


_BODY = re.compile(
    r"look at the top four cards of target opponent['’]s library, exile one of them face down, "
    r"then put the rest on the bottom of that library in a random order\. "
    r"you may cast that card for as long as it remains exiled, and mana of any type "
    r"can be spent to cast that spell\.",
    re.IGNORECASE,
)


def private_opponent_library_cast_trigger_compiler(fallback, *, permanent, trigger_node, match_source):
    if not permanent:
        return fallback

    def compile_trigger(**values):
        material = trigger_ability_word_material_line(values.get("material_line") or values["line"])
        match = match_source(material, card_name=values["card_name"])
        if match is None or match["event"].casefold() != "enters" or _BODY.fullmatch(match["body"]) is None:
            return fallback(**values)
        from ..private_opponent_library_cast_model import (
            PRIVATE_OPPONENT_LIBRARY_CAST_MECHANIC, private_opponent_library_cast_selection,
        )

        compiled = (
            "private-opponent-library-exile-cast-v1",
            (private_opponent_library_cast_selection(),),
            private_opponent_library_cast_target_schema(),
            (PRIVATE_OPPONENT_LIBRARY_CAST_MECHANIC,),
        )
        node = trigger_node(**values, effect_template=lambda _body, **_source: compiled)
        return replace(node, runtime_coverage=(*node.runtime_coverage, CURRENT_ABILITY_FRAGMENT_COVERAGE))

    return compile_trigger


def private_damage_library_cast_result(binding, body):
    from ..private_opponent_library_cast_model import DAMAGE_LIBRARY_CAST_MECHANIC, damage_library_cast_selection
    from ..rules.private_opponent_library_cast_shapes import damage_library_cast_condition

    text = " ".join(body.casefold().replace("’", "'").split())
    expected = ("look at the top three cards of that player's library, exile one of them face down, "
                "then put the rest into their graveyard. you may cast that card for as long as it "
                "remains exiled, and mana of any type can be spent to cast that spell.")
    if ((binding.event.value, binding.variant, binding.active_zone) !=
            ("damage.dealt.self", "source_combat_damage_player", "battlefield")
            or binding.event_condition != damage_library_cast_condition() or body != binding.body or text != expected):
        return None
    return ("private-damage-library-top-three-cast-v1", (damage_library_cast_selection(),), None,
            (DAMAGE_LIBRARY_CAST_MECHANIC,))


def private_cast_or_scheduled_result(binding, body, *, effect_template, card_name):
    if private_cast := private_damage_library_cast_result(binding, body):
        return private_cast, True
    from .scheduled_player_triggers import scheduled_player_result

    return scheduled_player_result(binding, body, effect_template=effect_template, card_name=card_name)

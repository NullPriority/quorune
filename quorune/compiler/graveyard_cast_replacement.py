from __future__ import annotations

"""Reuse fixed result owners in closed self-replacements based on a cast."""

from copy import deepcopy

from . import regex_working_set as re
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from .library_search_templates import fixed_counted_library_search_effect_template
from ..cast_origin_replacement import (
    CAST_ORIGIN_BY_VERSION, CAST_ORIGIN_REFERENCE,
    EXILE_CAST_REPLACEMENT_VERSION, GRAVEYARD_CAST_COPY_REPLACEMENT_VERSION, GRAVEYARD_CAST_REPLACEMENT_VERSION,
    CastOriginCondition, validate_cast_origin_replacement,
    MAIN_PHASE_CAST_ADDITIVE_VERSION, MAIN_PHASE_CAST_PREPARATION_VERSION, MAIN_PHASE_CAST_REFERENCE,
    MAIN_PHASE_CAST_REPLACEMENT_VERSION, MAIN_PHASE_CAST_UNTAP_VERSION, MAIN_PHASE_CAST_DISCARD_VERSION,
    MainPhaseCastCondition,
    validate_main_phase_cast_replacement,
)
from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC, RESOLUTION_CONDITION_OPERATION


_TEMPLATES = {
    GRAVEYARD_CAST_REPLACEMENT_VERSION: "fixed-graveyard-cast-replacement-v1",
    EXILE_CAST_REPLACEMENT_VERSION: "fixed-exile-cast-opponent-damage-v1",
    GRAVEYARD_CAST_COPY_REPLACEMENT_VERSION: "fixed-graveyard-cast-stack-copies-v1",
}
_ORIGIN_CLAUSE = re.compile(r"If this spell was cast from (?P<origin>a graveyard|exile), ", re.I)
_CONDITION = "if this spell was cast from a graveyard, "
_PRIVATE_SEARCH = (
    "search your library for a card and put that card into your hand. "
    + _CONDITION
    + "instead search your library for two cards and put those cards into your hand. then shuffle."
)


def _search_results():
    return tuple(
        fixed_counted_library_search_effect_template(
            f"Search your library for {noun}, put {pronoun} into your hand, then shuffle.",
        ).compiled()
        for noun, pronoun in (("a card", "that card"), ("two cards", "those cards"))
    )


def _fixed_results(normal_text, body, compile_component):
    normal = compile_component(normal_text)
    if normal[0] is None or len(normal[1]) != 1:
        return None
    token = re.fullmatch(rf"create (?P<count>{FIXED_COUNT_PATTERN}) of those tokens instead\.", body, re.I)
    counter = re.fullmatch(
        rf"put (?P<count>{FIXED_COUNT_PATTERN}) \+1/\+1 counters on that creature instead\.", body, re.I,
    )
    if token is not None and normal[2] is None and normal[1][0].get("op") == "create_token":
        alternative = (normal[0], ({**normal[1][0], "quantity": fixed_number(token["count"])},), None, normal[3])
    elif counter is not None and normal[2] is not None and normal[1][0].get("op") == "place_counters":
        alternative = compile_component(f'Put {counter["count"]} +1/+1 counters on target creature.')
    else:
        return None
    if alternative[0] is None or alternative[2:] != normal[2:]:
        return None
    return normal, alternative


def _exile_damage_results(normal_text, body, source_name, compile_component):
    match = re.fullmatch(
        r"it deals (?P<amount>[1-9][0-9]*) damage to each opponent and each creature they control instead\.",
        body, re.I,
    )
    if match is None:
        return None
    normal = compile_component(normal_text)
    alternate = compile_component(
        f'{source_name} deals {match["amount"]} damage to each opponent and each creature they control.',
    )
    if (
        normal[0] is None or alternate[0] is None or normal[2] is not None or normal[2:] != alternate[2:]
        or len(normal[1]) != 1 or len(alternate[1]) != 1
    ):
        return None
    return normal, alternate


_CONTROLLED_COPY = "copy target instant or sorcery spell you control."
_DOUBLE_COPY = "copy that spell twice instead. you may choose new targets for the copies."


def _graveyard_stack_copy_results(normal_text, compile_component):
    normal = compile_component(normal_text + " You may choose new targets for the copy.")
    if normal[0] is None or len(normal[1]) != 1 or normal[2] is None:
        return None
    effect, = normal[1]
    return normal, (normal[0], (deepcopy(effect), deepcopy(effect)), normal[2], normal[3])


def cast_origin_replacement_template(text, *, source_name, compile_component):
    text = text.strip()
    marker = _ORIGIN_CLAUSE.search(text)
    if marker is None:
        return None
    version = GRAVEYARD_CAST_REPLACEMENT_VERSION
    if text.casefold() == _PRIVATE_SEARCH:
        normal, alternative = _search_results()
    else:
        normal_text, body = text[:marker.start()].strip(), text[marker.end():]
        if not normal_text.endswith("."):
            return None
        if marker["origin"].casefold() == "exile":
            version = EXILE_CAST_REPLACEMENT_VERSION
            results = _exile_damage_results(normal_text, body, source_name, compile_component)
        elif normal_text.casefold() == _CONTROLLED_COPY and body.casefold() == _DOUBLE_COPY:
            version = GRAVEYARD_CAST_COPY_REPLACEMENT_VERSION
            results = _graveyard_stack_copy_results(normal_text, compile_component)
        else:
            results = _fixed_results(normal_text, body, compile_component)
        if results is None:
            return None
        normal, alternative = results
    wrapper = {
        "op": RESOLUTION_CONDITION_OPERATION, "schema_version": version,
        "player": "$controller", "condition": CastOriginCondition(CAST_ORIGIN_BY_VERSION[version]).to_dict(),
        "cast_fact": CAST_ORIGIN_REFERENCE, "prefix_mechanic_ids": [],
        "effects": deepcopy(list(alternative[1])), "mechanic_ids": list(alternative[3]),
        "otherwise_effects": deepcopy(list(normal[1])), "otherwise_mechanic_ids": list(normal[3]),
    }
    try:
        validate_cast_origin_replacement(wrapper)
    except (TypeError, ValueError, KeyError):
        return None
    return _TEMPLATES[version], (wrapper,), deepcopy(normal[2]), (RESOLUTION_CONDITION_MECHANIC, *normal[3])


def cast_origin_spell_compiler(compile_effect, *, source_name):
    def compile_spell_effect(text, **context):
        owned = cast_origin_replacement_template(
            text, source_name=context.get("card_name") or source_name,
            compile_component=lambda body: compile_effect(body, **context),
        )
        return owned if owned is not None else compile_effect(text, **context)

    return compile_spell_effect



MAIN_PHASE_CAST_TEMPLATE = "fixed-main-phase-cast-replacement-v1"
MAIN_PHASE_CAST_ADDITIVE_TEMPLATE = "fixed-main-phase-cast-addendum-v1"
MAIN_PHASE_CAST_PREPARATION_TEMPLATE = "fixed-main-phase-cast-preparation-v1"
MAIN_PHASE_CAST_UNTAP_TEMPLATE = "fixed-main-phase-cast-target-untap-v1"
MAIN_PHASE_CAST_DISCARD_TEMPLATE = "fixed-main-phase-cast-target-discard-v1"
_PREPARATION_RESULTS = {
    ("draw three cards.", "instead scry 3, then draw three cards."):
        ("Draw three cards.", "Scry 3, then draw three cards."),
    (
        "choose target creature you control and target creature you don't control.",
        "the creature you control gets +1/+1 until end of turn. then those creatures fight each other.",
    ): (
        "Target creature you control fights target creature you don't control.",
        "Target creature you control gets +1/+1 until end of turn. It fights target creature you don't control.",
    ),
}
_ADDITIVE_RESULTS = {
    "you gain 2 life.": "You gain 2 life.",
    "draw a card.": "Draw a card.",
    "that creature gains flying until end of turn.": "Target creature gains flying until end of turn.",
}
_MAIN_PHASE_CLAUSE = re.compile(r"(?:Addendum \u2014 )?If you cast this spell during your main phase, ", re.I)


def _main_phase_alternate_text(default_text, body, source_name):
    same_creature = re.fullmatch(
        r"that creature gets (?P<bonus>\+[1-9][0-9]*/\+[1-9][0-9]*) until end of turn instead\.", body, re.I,
    )
    if same_creature is not None and default_text.casefold().startswith("target creature gets "):
        return f'Target creature gets {same_creature["bonus"]} until end of turn.'
    private_player_result = re.fullmatch(
        rf"instead that player draws four cards, then discards (?P<count>{FIXED_COUNT_PATTERN}) cards\.", body, re.I,
    )
    if private_player_result is not None and default_text.casefold().startswith("target player draws four cards, then discards "):
        return f'Target player draws four cards, then discards {private_player_result["count"]} cards.'
    fixed_tapped_damage = re.fullmatch(r"it deals (?P<amount>[1-9][0-9]*) damage instead\.", body, re.I)
    if fixed_tapped_damage is not None and default_text.casefold().endswith(" damage to target tapped creature."):
        return f'{source_name} deals {fixed_tapped_damage["amount"]} damage to target tapped creature.'
    fixed_global_damage = re.fullmatch(
        rf"{re.escape(source_name)} deals (?P<amount>[1-9][0-9]*) damage to each creature and each player instead\.", body, re.I,
    )
    if fixed_global_damage is not None and default_text.casefold().endswith(" damage to each creature and each player."):
        return f'{source_name} deals {fixed_global_damage["amount"]} damage to each creature and each player.'
    return None


def _main_phase_untap_addendum_template(default_text, tail, compile_component):
    if (
        default_text.casefold() != "target creature gets -4/-0 until end of turn. draw a card."
        or tail.casefold() != "tap that creature and it doesn't untap during its controller's next untap step."
    ):
        return None
    base = compile_component(default_text)
    bonus = compile_component("Tap target creature. It doesn't untap during its controller's next untap step.")
    if base[0] is None or bonus[0] is None or base[2] != bonus[2]:
        return None
    mechanics = tuple(dict.fromkeys((*base[3], *bonus[3])))
    return _ordered_main_phase_result(
        base, (*base[1], *bonus[1]), mechanics, MAIN_PHASE_CAST_UNTAP_VERSION, MAIN_PHASE_CAST_UNTAP_TEMPLATE,
    )


def _main_phase_addendum_template(default_text, tail, compile_component):
    from .creature_cohort_addendum import creature_cohort_addendum_template

    if cohort := creature_cohort_addendum_template(default_text, tail):
        return cohort
    bonus_text = _ADDITIVE_RESULTS.get(tail.casefold())
    if bonus_text is None:
        return _main_phase_untap_addendum_template(default_text, tail, compile_component)
    base, bonus = (compile_component(text) for text in (default_text, bonus_text))
    if base[0] is None or bonus[0] is None or len(base[1]) != 1 or len(bonus[1]) != 1:
        return None
    if bonus[2] is not None and bonus[2] != base[2]:
        return None
    mechanics = tuple(dict.fromkeys((*base[3], *bonus[3])))
    return _ordered_main_phase_result(
        base, (*base[1], *bonus[1]), mechanics, MAIN_PHASE_CAST_ADDITIVE_VERSION, MAIN_PHASE_CAST_ADDITIVE_TEMPLATE,
    )


def _main_phase_preparation_template(default_text, tail, compile_component):
    bodies = _PREPARATION_RESULTS.get((default_text.casefold(), tail.casefold()))
    if bodies is None:
        return None
    base, prepared = (compile_component(body) for body in bodies)
    if base[0] is None or prepared[0] is None or base[2] != prepared[2]:
        return None
    return _ordered_main_phase_result(
        base, prepared[1], prepared[3], MAIN_PHASE_CAST_PREPARATION_VERSION, MAIN_PHASE_CAST_PREPARATION_TEMPLATE,
    )


def _ordered_main_phase_result(base, ordered_effects, mechanics, version, template):
    wrapper = {
        "op": RESOLUTION_CONDITION_OPERATION, "schema_version": version,
        "player": "$controller", "condition": MainPhaseCastCondition().to_dict(),
        "cast_fact": MAIN_PHASE_CAST_REFERENCE, "prefix_mechanic_ids": [],
        "effects": deepcopy(list(ordered_effects)), "mechanic_ids": list(mechanics),
        "otherwise_effects": deepcopy(list(base[1])), "otherwise_mechanic_ids": list(base[3]),
    }
    try:
        _condition, _effects, target = validate_main_phase_cast_replacement(wrapper)
    except (TypeError, ValueError, KeyError):
        return None
    if base[2] != target:
        return None
    return template, (wrapper,), deepcopy(target), (RESOLUTION_CONDITION_MECHANIC, *mechanics)


def _main_phase_target_discard_template(default_text, tail, compile_component):
    if default_text.casefold() != "target player discards two cards." or tail.casefold() != "that player discards four cards instead.":
        return None
    base = compile_component(default_text)
    if base[0] is None or len(base[1]) != 1:
        return None
    qualified = ({**base[1][0], "count": 4},)
    return _ordered_main_phase_result(
        base, qualified, base[3], MAIN_PHASE_CAST_DISCARD_VERSION, MAIN_PHASE_CAST_DISCARD_TEMPLATE,
    )


def _main_phase_optional_target_exile_template(default_text, tail, compile_component):
    from ..cast_origin_replacement import (
        BONUS_EXILE_REF, EXILE_MECHANICS, MAIN_PHASE_OPTIONAL_EXILE_TEMPLATE,
        MAIN_PHASE_OPTIONAL_EXILE_VERSION, OPTIONAL_EXILE_MECHANICS, PRIMARY_EXILE_REF,
        artifact_or_enchantment_exile_schema, main_phase_exile_instruction,
        main_phase_optional_exile_target_schema, optional_exile_instruction,
    )

    if (default_text.casefold() != "exile target artifact or enchantment."
            or tail.casefold() != "you may exile up to one other target artifact or enchantment."):
        return None
    ordinary = compile_component(default_text)
    if (ordinary[0] is None or ordinary[1] != (main_phase_exile_instruction("$target.0"),)
            or ordinary[2] != artifact_or_enchantment_exile_schema() or ordinary[3] != EXILE_MECHANICS):
        return None
    primary = main_phase_exile_instruction(PRIMARY_EXILE_REF)
    scoped = (ordinary[0], (primary,), main_phase_optional_exile_target_schema(), EXILE_MECHANICS)
    return _ordered_main_phase_result(
        scoped, (primary, optional_exile_instruction(BONUS_EXILE_REF)), OPTIONAL_EXILE_MECHANICS,
        MAIN_PHASE_OPTIONAL_EXILE_VERSION, MAIN_PHASE_OPTIONAL_EXILE_TEMPLATE,
    )


def cast_main_phase_replacement_template(text, *, source_name, compile_component):
    marker = _MAIN_PHASE_CLAUSE.search(text)
    if marker is None:
        return None
    default_text = text[:marker.start()].strip()
    if not default_text.endswith("."):
        return None
    tail = text[marker.end():]
    optional_exile = _main_phase_optional_target_exile_template(default_text, tail, compile_component)
    if optional_exile is not None:
        return optional_exile
    alternate_text = _main_phase_alternate_text(default_text, tail, source_name)
    if alternate_text is None:
        prepared = _main_phase_preparation_template(default_text, tail, compile_component)
        if prepared is not None:
            return prepared
        discard = _main_phase_target_discard_template(default_text, tail, compile_component)
        if discard is not None:
            return discard
        return (_main_phase_addendum_template(default_text, tail, compile_component)
                if marker.group().casefold().startswith("addendum") else None)
    normal = compile_component(default_text)
    alternate = compile_component(alternate_text)
    if normal[0] is None or alternate[0] is None or normal[2:] != alternate[2:]:
        return None
    wrapper = {
        "op": RESOLUTION_CONDITION_OPERATION, "schema_version": MAIN_PHASE_CAST_REPLACEMENT_VERSION,
        "player": "$controller", "condition": MainPhaseCastCondition().to_dict(),
        "cast_fact": MAIN_PHASE_CAST_REFERENCE, "prefix_mechanic_ids": [],
        "effects": deepcopy(list(alternate[1])), "mechanic_ids": list(alternate[3]),
        "otherwise_effects": deepcopy(list(normal[1])), "otherwise_mechanic_ids": list(normal[3]),
    }
    try:
        _condition, _effects, target = validate_main_phase_cast_replacement(wrapper)
    except (TypeError, ValueError, KeyError):
        return None
    if normal[2] != target:
        return None
    return MAIN_PHASE_CAST_TEMPLATE, (wrapper,), deepcopy(target), (RESOLUTION_CONDITION_MECHANIC, *normal[3])

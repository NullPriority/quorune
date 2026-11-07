from __future__ import annotations


"""Composition boundary for source-self trigger effect bodies."""

from typing import Any
import re
from ..rules.source_references import SourceReferenceSpec, CYCLED_SOURCE_DAMAGE_COVERAGE

from .creature_power_damage_templates import (
    fixed_creature_power_damage_effect_template,
)
from .damage_templates import source_pronoun_damage_effect_template
from .explore_templates import single_explore_effect_template
from .connive_templates import fixed_connive_effect_template
from .endure_templates import source_endure_trigger_result
from .declared_effect_amounts import declared_effect_amount_template


def compile_source_self_zone_effect(body,*,card_name,event_phrase,effect_template):
    from .scalar_effect_amounts import source_scalar_effect_amount_template
    from ..source_entry_cast_information import (source_entry_cast_condition,SOURCE_ENTRY_CAST_MECHANIC,
        SOURCE_ENTRY_CAST_HAND_MECHANIC)
    condition=None
    guard=re.fullmatch(r'if you cast it(?P<origin> from your hand)?, (?P<body>.+)',body.strip(),re.I) if event_phrase=='enters' else None
    if guard is not None:body=guard['body'];condition=source_entry_cast_condition(origin='hand' if guard['origin'] else None)
    if event_phrase == 'enters':
        from .suspect_templates import normalize_entering_source_suspect
        body = normalize_entering_source_suspect(body)
    compiled=source_scalar_effect_amount_template(body,source_name=card_name,
        event={'enters':'permanent.enter','dies':'creature.dies','leaves the battlefield':'permanent.leave'}[event_phrase],
        compile_fixed=lambda text:effect_template(text,card_name=card_name)) or source_self_contextual_effect_template(
            body,card_name=card_name,event_phrase=event_phrase,compile_effect=effect_template)
    template,effects,schema,mechanics=compiled if compiled is not None else effect_template(body,card_name=card_name)
    if condition is not None:mechanics=tuple(dict.fromkeys((*mechanics,SOURCE_ENTRY_CAST_MECHANIC)))
    if condition is not None and condition.get('origin')=='hand':mechanics=(*mechanics,SOURCE_ENTRY_CAST_HAND_MECHANIC)
    return template,effects,schema,mechanics,condition


_SOURCE_EVENT = re.compile(
    r"(?P<prefix>(?:When|Whenever) )(?P<subject>.+?)"
    r"(?P<tail> (?:attacks|blocks|becomes blocked|deals combat damage to a player|"
    r"deals combat damage to an opponent|deals damage to an opponent|is dealt damage), .+)",
    re.IGNORECASE,
)
_NAMED_SOURCE_UNION_PREFIX = re.compile(
    r"^Whenever (?s:.*?) or another [A-Za-z][A-Za-z'-]*\b", re.IGNORECASE
)


def named_source_union_candidate(text: str) -> bool:
    """Retain a superset of the existing named-source union prefix."""
    return _NAMED_SOURCE_UNION_PREFIX.match(text) is not None


def normalized_source_event_line(text: str, *, source_name: str, named_only: bool = False) -> str | None:
    """Normalize only an independently closed self-action event subject."""
    event = _SOURCE_EVENT.fullmatch(text)
    if event is None:
        return None
    if named_only and not SourceReferenceSpec(source_name).matches(event['subject']):
        return None
    source = re.fullmatch(
        rf"(?P<prefix>(?:When|Whenever) )(?P<subject>{SourceReferenceSpec(source_name).regex_pattern}"
        r"|this (?:artifact|enchantment|land|permanent|planeswalker|Saga|Spacecraft|Class|Vehicle))"
        r"(?P<tail> (?:attacks|blocks|becomes blocked|deals combat damage to a player|"
        r"deals combat damage to an opponent|deals damage to an opponent|is dealt damage), .+)",
        text, re.IGNORECASE,
    )
    # The label identifies this object under CR 201.5, not a type predicate.
    return source["prefix"] + "this creature" + source["tail"] if source is not None else None


def source_self_contextual_effect_template(
    text: str,
    *,
    card_name: str,
    event_phrase: str,
    compile_effect: Any = None,
) -> tuple[Any, ...] | None:
    """Delegate one source-bound body to its closed typed leaf owner."""

    from .event_player_choice_templates import source_death_player_sacrifice_result
    sacrifice = source_death_player_sacrifice_result(text, event_phrase=event_phrase)
    if sacrifice is not None:return sacrifice

    counter_payment = _source_self_counter_payment(text,card_name=card_name,compile_effect=compile_effect)
    if counter_payment is not None:
        return counter_payment

    optional = _source_self_optional_result(text, card_name=card_name,
        event_phrase=event_phrase, compile_effect=compile_effect)
    if optional is not None:
        return optional

    connive = fixed_connive_effect_template(text, source_name=card_name, source_is_permanent=True,
                                           allow_source_pronoun=True)
    if connive is not None:return connive

    endure = source_endure_trigger_result(
        text, source_name=card_name, allow_source_pronoun=True, effect_template=compile_effect,
    )
    if endure is not None:
        return endure

    normalized=normalized_source_result_body(text)
    if normalized is not None and compile_effect is not None:
        value=compile_effect(normalized,card_name=card_name)
        if value[0] is not None:return pin_source_result_incarnation(value)
    explored = single_explore_effect_template(
        text,
        allow_source_pronoun=True,
    )
    creature_power = fixed_creature_power_damage_effect_template(
        text,
        card_name=card_name,
        allow_source_pronoun=True,
    )
    fixed_damage = (
        source_pronoun_damage_effect_template(text)
        if event_phrase in {"enters", "dies"}
        else None
    )
    compiled = explored or creature_power or fixed_damage
    if compiled is not None:
        return compiled.compiled()
    if event_phrase not in {"enters", "dies"}:
        return None
    def compile_fixed(body: str):
        leaf = source_pronoun_damage_effect_template(body)
        return leaf.compiled() if leaf is not None else (None, (), None, ())
    return declared_effect_amount_template(
        text, source_name=card_name, compile_fixed=compile_fixed,
    )


def _source_self_counter_payment(text, *, card_name, compile_effect):
    if compile_effect is None:return None
    normalized=re.sub(r'^(you may remove [^.]+? counters? from) (?:it|him|her)(?=\.)',
        r'\1 this permanent',text,flags=re.I)
    if normalized==text:return None
    compiled=compile_effect(normalized,card_name=card_name)
    return compiled if compiled[0] is not None else None


def source_cycled_optional_damage_result(binding, body):
    """The fixed damage source is the card discarded to pay its own cycling."""
    if (binding.event.value != 'card.cycled.self' or binding.variant != 'source_cycles'
            or binding.active_zone != 'hand'):
        return None
    match = re.fullmatch(r'you may have it deal (?P<damage>[1-9][0-9]* damage to .+)', body, re.I)
    if match is None:
        return None
    leaf = source_pronoun_damage_effect_template('It deals ' + match['damage'])
    if leaf is None:
        return None
    from .optional_effect_templates import FixedOptionalEffectTemplate, FIXED_OPTIONAL_EFFECT_MECHANIC
    template, effects, target_schema, mechanics = leaf.compiled()
    return FixedOptionalEffectTemplate(component_template_id=template,
        _component_effects=effects, _target_schema=target_schema,
        mechanic_ids=(FIXED_OPTIONAL_EFFECT_MECHANIC, CYCLED_SOURCE_DAMAGE_COVERAGE, *mechanics)).compiled()


def _source_self_optional_result(text, *, card_name, event_phrase, compile_effect):
    if compile_effect is None:
        return None
    normalized = re.sub(r'^(you may have) (?:it|him|her) (deal|gain|get|lose)\b',
        r'\1 this permanent \2', text, flags=re.I)
    if normalized == text:
        return None
    from .optional_effect_templates import fixed_optional_effect_template
    def compile_body(body):
        leaf = source_self_contextual_effect_template(body, card_name=card_name,
            event_phrase=event_phrase, compile_effect=compile_effect)
        if leaf is None or leaf[0] is None:
            leaf = compile_effect(body, card_name=card_name)
        if (len(leaf[1]) == 1 and leaf[1][0].get('op') == 'modify_stats_until_end_of_turn'
                and leaf[1][0].get('card') == '$source'):
            from .fixed_target_effect_sequences import FixedSourceCharacteristicsTemplate
            leaf = FixedSourceCharacteristicsTemplate(source_kind='permanent',
                power=leaf[1][0]['power'], toughness=leaf[1][0]['toughness']).compiled()
        return pin_source_result_incarnation(leaf)
    optional = fixed_optional_effect_template(normalized, compile_effect=compile_body)
    return optional.compiled() if optional is not None else None


def normalized_source_result_body(body: str) -> str | None:
    """Bind only a complete source-led result, never new-object anaphora."""
    if re.search(r'\b(?:target|create|choose|another|that)\b',body,re.I):return None
    if not (re.match(r'(?:it|him|her) (?:gets|gains|deals)\b',body,re.I)
            or re.fullmatch(r'(?:Tap|Untap) (?:it|him|her)\.',body,re.I)
            or re.fullmatch(r'(?:(?:you may )?put .+? counter(?:s)? on|double the number of .+? counters on) (?:it|him|her)\.',body,re.I)):
        return None
    return re.sub(r'\b(?:it|him|her)\b','this creature',body,flags=re.I)


def pin_source_result_incarnation(compiled):
    template,effects,schema,mechanics=compiled
    pinned=tuple({**effect,'card':'$source.zone_object'} if effect.get('card')=='$source' else effect for effect in effects)
    return template,pinned,schema,mechanics


def source_characteristics_declaration_sequence(body, *, card_name, compile_effect):
    """Compose the existing source characteristic and declaration leaves."""
    from ..rules.source_references import SourceReferenceSpec
    match = re.fullmatch(r"(?P<subject>.+?) gets (?P<stats>[+-]\d+/[+-]\d+) "
        r"until end of turn and can't be blocked this turn\.", body, re.I)
    if match is None or not (match['subject'].casefold() == 'this creature'
            or SourceReferenceSpec(card_name).matches(match['subject'])):
        return None
    subject = match['subject']
    compiled = compile_effect(f"{subject} gets {match['stats']} until end of turn. "
        f"{subject} can't be blocked this turn.", card_name=card_name)
    if (compiled[0] is None or compiled[2] is not None or len(compiled[1]) != 2
            or tuple(effect.get('op') for effect in compiled[1]) != (
                'apply_source_characteristics_until_end_of_turn', 'grant_declaration_restriction_until_end_of_turn')
            or any(effect.get('card') not in {'$source', '$source.zone_object'} for effect in compiled[1])):
        return None
    return pin_source_result_incarnation(compiled)


__all__ = ["source_self_contextual_effect_template"]

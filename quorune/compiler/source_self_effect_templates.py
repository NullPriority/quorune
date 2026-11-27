from __future__ import annotations


"""Composition boundary for source-self trigger effect bodies."""

from typing import Any
from . import regex_working_set as re
from ..rules.source_references import CYCLED_SOURCE_DAMAGE_COVERAGE, SourceReferenceSpec, source_self_permanent_type

from .announced_damage_templates import fixed_announced_damage_template
from .creature_power_damage_templates import (
    fixed_creature_power_damage_effect_template,
)
from .damage_templates import source_pronoun_damage_effect_template
from .explore_templates import single_explore_effect_template
from .connive_templates import fixed_connive_effect_template
from .endure_templates import source_endure_trigger_result
from .declared_effect_amounts import declared_effect_amount_template


def bound_source_fixed_damage(binding, body):
    from ..rules.event_player_result_shapes import _source_combat_player_scope

    events = {
        'source_public_tap_state_transition': {'permanent.tap', 'permanent.untap'},
        'source_enters_or_leaves_the_battlefield': {'permanent.enter', 'permanent.leave'},
    }
    source_attack = binding.event.value == 'creature.attacks' and _source_combat_player_scope(binding.event_condition)
    if binding.active_zone != 'battlefield' or not (
        source_attack or binding.event.value in events.get(binding.variant, ())
    ):
        return None
    damage = source_pronoun_damage_effect_template(body)
    return damage.compiled() if damage is not None else None


_RANDOM_OPPONENT_CREATURE_DAMAGE = re.compile(
    r"(?:it|this creature) deals (?P<amount>[1-9][0-9]*) damage to a creature "
    r"an opponent controls chosen at random\.?",
    re.IGNORECASE,
)


def _optional_entry_phase_out(body):
    if " ".join(body.split()).casefold() != "up to one target creature phases out.":
        return None
    from ..phasing_model import PHASE_OUT_MECHANIC, optional_entry_phase_out_effect, optional_entry_phase_out_schema

    return ("optional-single-creature-entry-phase-out-v1", (optional_entry_phase_out_effect(),),
            optional_entry_phase_out_schema(), (PHASE_OUT_MECHANIC, "cr-115-targets"), None)

def source_death_hand_return_template(body):
    from ..delayed_object_model import (
        DELAYED_OBJECT_MECHANIC, SOURCE_DEATH_HAND_TEMPLATE, source_death_hand_return_effect,
    )

    if re.fullmatch(
        r"return (?:it|this card) to its owner['’]s hand at the beginning of the next end step\.?",
        body.strip(), re.I,
    ):
        return (SOURCE_DEATH_HAND_TEMPLATE, (source_death_hand_return_effect(),),
                None, (DELAYED_OBJECT_MECHANIC,), None)
    return None


def compile_source_self_zone_effect(body,*,card_name,event_phrase,effect_template):
    if event_phrase == "enters" and " ".join(body.casefold().split()) == (
        "search your library for a card, exile it face down, then shuffle. "
        "for as long as that card remains exiled, you may play it."
    ):
        from ..rules.private_search_play import PRIVATE_SEARCH_PLAY_MECHANIC, private_search_play_effect

        return ("entry-private-search-play-v1", (private_search_play_effect(),), None,
                (PRIVATE_SEARCH_PLAY_MECHANIC,), None)
    if event_phrase == "enters" and (phasing := _optional_entry_phase_out(body)) is not None:
        return phasing
    if event_phrase == "dies":
        from .library_search_templates import creature_subset_mana_value_search_template

        if delayed_hand := source_death_hand_return_template(body):
            return delayed_hand
        if search := creature_subset_mana_value_search_template(body):
            return (*search, None)
        random_damage = _RANDOM_OPPONENT_CREATURE_DAMAGE.fullmatch(body.strip())
        if random_damage is not None:
            from ..rules.random_opponent_damage import random_opponent_damage_instruction

            effect = random_opponent_damage_instruction(int(random_damage["amount"]))
            return "damage-source-death-random-opponent-creature-v1", (effect,), None, ("cr-120-damage",), None
    if (event_phrase == "dies"
        and body.strip().casefold() == "destroy all permanents at the beginning of the next end step."):
        from ..rules.destruction_result_model import next_end_step_destruction_effect

        return ("fixed-destruction-next-end-step-v1", (next_end_step_destruction_effect(),),
                None, ("destroy", "destroy-fixed-set"), None)
    from .scalar_effect_amounts import source_scalar_effect_amount_template
    from ..source_entry_cast_information import (source_entry_cast_condition,SOURCE_ENTRY_CAST_MECHANIC,
        SOURCE_ENTRY_CAST_HAND_MECHANIC)
    condition=None
    guard=re.fullmatch(r'if you cast it(?P<origin> from your hand)?, (?P<body>.+)',body.strip(),re.I) if event_phrase=='enters' else None
    if guard is not None:
        body = guard['body']
        condition = source_entry_cast_condition(origin='hand' if guard['origin'] else None)
        if not guard['origin'] and body.casefold() == 'you gain protection from everything until your next turn.':
            from ..rules.next_personal_turn_protection import (
                CAST_ENTRY_PROTECTION_MECHANIC, CAST_ENTRY_PROTECTION_TEMPLATE, cast_entry_protection_effect,
            )

            return (CAST_ENTRY_PROTECTION_TEMPLATE, (cast_entry_protection_effect(),), None,
                    (SOURCE_ENTRY_CAST_MECHANIC, CAST_ENTRY_PROTECTION_MECHANIC), condition)
    if event_phrase == 'enters':
        from .discard_result_templates import (
            selected_land_discard_result_template, source_entry_creature_discard_or_exile_template,
        )
        if condition is None:
            discard = source_entry_creature_discard_or_exile_template(body) or selected_land_discard_result_template(body)
            if discard is not None:
                return (*discard, None)
        from .manifest_templates import manifest_equipment_entry_template
        from .target_opponent_counter_choice import target_opponent_counter_choice_result
        from .keep_three_sacrifice_templates import keep_three_land_entry_template
        from .collective_source_damage_templates import collective_source_damage_entry_template
        from .protected_attack_payment_templates import next_turn_attack_payment_template
        from .library_search_templates import (
            optional_literal_name_any_number_search_template, recruiting_library_search_template,
        )
        from .variable_library_selection_templates import devotion_library_win_template

        from .qualified_library_search_templates import creature_stat_library_search_template

        if condition is None:
            if devotion_win := devotion_library_win_template(body):
                return (*devotion_win, None)
            library_search = (
                creature_stat_library_search_template(body)
                or optional_literal_name_any_number_search_template(body)
                or recruiting_library_search_template(body)
            )
            if library_search is not None:
                return (*library_search, None)
            entry_result = (target_opponent_counter_choice_result(body) or keep_three_land_entry_template(body)
                            or collective_source_damage_entry_template(body, card_name=card_name)
                            or next_turn_attack_payment_template(body, card_name=card_name))
            if entry_result is not None:
                return (*entry_result, None)
        if condition is None and (attachment := manifest_equipment_entry_template(body)) is not None:
            return (*attachment, None)
        from .suspect_templates import normalize_entering_source_suspect
        body = normalize_entering_source_suspect(body)
        if condition is None:
            from .token_creation_reflexive_templates import token_creation_reflexive_template
            reflexive_creation = token_creation_reflexive_template(
                body, compile_effect=lambda clause: effect_template(clause, card_name=card_name),
            )
            if reflexive_creation is not None:
                return (*reflexive_creation, None)
    from .recipient_control_templates import normalized_source_recipient_control_body
    body=normalized_source_recipient_control_body(body) or body
    compiled = fixed_announced_damage_template(body, card_name=card_name, allow_source_pronoun=True)
    compiled=compiled or source_scalar_effect_amount_template(body,source_name=card_name,
        event={'enters':'permanent.enter','dies':'creature.dies','leaves the battlefield':'permanent.leave'}[event_phrase],
        compile_fixed=lambda text:effect_template(text,card_name=card_name)) or source_self_contextual_effect_template(
            body,card_name=card_name,event_phrase=event_phrase,compile_effect=effect_template)
    if compiled is None:
        from .graveyard_copy_cast_templates import relative_graveyard_copy_template
        relative = (
            relative_graveyard_copy_template(body, card_name=card_name, event="enter")
            if condition is not None else None
        )
        if event_phrase == "dies":
            from .reanimation_templates import relative_target_reanimation_template
            relative = relative_target_reanimation_template(body, card_name=card_name, event="dies")
        compiled = relative or effect_template(body, card_name=card_name)
        if compiled[0] is None:
            compiled = _source_self_reflexive_payment(body, card_name=card_name,
                event_phrase=event_phrase, effect_template=effect_template) or compiled
    template,effects,schema,mechanics=compiled
    if condition is not None:mechanics=tuple(dict.fromkeys((*mechanics,SOURCE_ENTRY_CAST_MECHANIC)))
    if condition is not None and condition.get('origin')=='hand':mechanics=(*mechanics,SOURCE_ENTRY_CAST_HAND_MECHANIC)
    return template,effects,schema,mechanics,condition


def normalized_source_self_zone_event(event_phrase, template):
    from ..delayed_object_model import SOURCE_DEATH_HAND_EVENT, SOURCE_DEATH_HAND_TEMPLATE

    if template == SOURCE_DEATH_HAND_TEMPLATE:
        return SOURCE_DEATH_HAND_EVENT
    return {
        "enters": "permanent.enter.self",
        "dies": "creature.dies.self",
        "leaves the battlefield": "permanent.leave.self",
    }[event_phrase]


def _source_self_reflexive_payment(body, *, card_name, event_phrase, effect_template):
    from .occurrence_object_results import occurrence_power_damage_body
    from .reflexive_payment_templates import reflexive_payment_template

    def compile_child(text):
        if event_phrase in {'enters', 'dies'}:
            source = SourceReferenceSpec(card_name).regex_pattern
            subject = re.match(rf'^(?:it|this creature|{source}) (?=deals damage equal to its power to )', text, re.I)
            if subject is not None:
                power = occurrence_power_damage_body('It ' + text[subject.end():])
                if power is not None:
                    return power
        return source_self_contextual_effect_template(text, card_name=card_name, event_phrase=event_phrase,
            compile_effect=effect_template) or effect_template(text, card_name=card_name)

    return reflexive_payment_template(body, compile_effect=compile_child, bound_trigger=True)


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
    from .fixed_damage_life_sequences import fixed_damage_life_sequence_template
    if compile_effect is not None:
        sequence=fixed_damage_life_sequence_template(text,compile_clause=lambda clause:
            source_self_contextual_effect_template(clause,card_name=card_name,event_phrase=event_phrase,
                compile_effect=compile_effect) or compile_effect(clause,card_name=card_name))
        if sequence is not None:return sequence

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
        if event_phrase in {"enters", "dies", "leaves the battlefield"}
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


def _cycled_goblin_damage_amount(body):
    if not re.fullmatch(
        r"you may have it deal X damage to target creature, where X is the number of Goblins on the battlefield[.]?",
        body, re.I,
    ):
        return None
    from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
    from ..object_predicate import ObjectQuerySpec
    from ..query_effect_amount_model import PublicQueryAmountSpec

    quantity = CharacteristicQuantitySpec(
        CharacteristicQuantityScope.ALL_ZONES,
        ObjectQuerySpec(zones=("battlefield",), subtypes_all=("goblin",)),
    )
    return PublicQueryAmountSpec(quantity)


def source_cycled_optional_damage_result(binding, body):
    """The damage source is the card discarded to pay for its own cycling."""
    if (binding.event.value != 'card.cycled.self' or binding.variant != 'source_cycles'
            or binding.active_zone != 'hand'):
        return None
    match = re.fullmatch(r'you may have it deal (?P<damage>[1-9][0-9]* damage to .+)', body, re.I)
    amount = _cycled_goblin_damage_amount(body) if match is None else None
    if match is None and amount is None:
        return None
    damage_body = match["damage"] if match is not None else "1 damage to target creature."
    leaf = source_pronoun_damage_effect_template("It deals " + damage_body)
    if leaf is None:
        return None
    from .optional_effect_templates import FixedOptionalEffectTemplate, FIXED_OPTIONAL_EFFECT_MECHANIC
    template, effects, target_schema, mechanics = leaf.compiled()
    if amount is not None:
        from .public_query_effect_amounts import PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC

        effects = ({**effects[0], "amount": amount.to_dict()},)
        mechanics = (*mechanics, PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC)
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
    if re.fullmatch(r"It becomes prepared\.?", body, re.I):
        return "This creature becomes prepared."
    if re.fullmatch(r"Switch its power and toughness until end of turn\.?", body, re.I):
        return "Switch this creature's power and toughness until end of turn."
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


_BLOCK_RESTRICTION = r"can't be blocked (?:this turn(?: except by .+?)?|by .+? this turn)"
_SOURCE_DECLARATION_FOLLOWUP = re.compile(
    rf"(?P<subject>.+?) (?P<change>(?:gets|gains|becomes) .+?) until end of turn and (?P<rule>{_BLOCK_RESTRICTION})\.",
    re.I,
)
_SOURCE_DECLARATION_SCRY = re.compile(
    rf"(?P<subject>.+?) (?P<rule>{_BLOCK_RESTRICTION}) and (?:you )?(?P<scry>scry [1-9]\d*)\.",
    re.I,
)
_SOURCE_DECLARATION_SENTENCE = re.compile(rf"(?P<subject>.+?) (?P<rule>{_BLOCK_RESTRICTION})\.", re.I)
_DECLARATION_OPERATION = 'grant_declaration_restriction_until_end_of_turn'


def _source_declaration_clauses(body, *, card_name, allow_pronoun):
    from .closed_effect_programs import _sentence_partition

    def refers_to_source(subject):
        return (source_self_permanent_type(subject) is not None
                or bool(card_name) and SourceReferenceSpec(card_name).matches(subject))

    for pattern in (_SOURCE_DECLARATION_FOLLOWUP, _SOURCE_DECLARATION_SCRY):
        match = pattern.fullmatch(body)
        if match is None:
            continue
        subject = match['subject']
        if not refers_to_source(subject):
            if not allow_pronoun or subject.casefold() not in {'it', 'him', 'her'}:
                return None
            subject = 'This creature'
        rule = f"This creature {match['rule']}."
        if pattern is _SOURCE_DECLARATION_SCRY:
            return rule, match['scry'].capitalize() + '.', True
        return f"{subject} {match['change']} until end of turn.", rule, False
    if (parts := _sentence_partition(body)) is not None:
        match = _SOURCE_DECLARATION_SENTENCE.fullmatch(parts[-1])
        if match is not None and match['subject'].casefold() in {'it', 'him', 'her'}:
            return ' '.join(parts[:-1]), f"This creature {match['rule']}.", False
    return None


def source_declaration_sequence(body, *, card_name, compile_effect, allow_pronoun=False):
    """Keep a source blocker rule and its independently closed companion in order."""
    if "can't be blocked" not in body.casefold():
        return None
    if (clauses := _source_declaration_clauses(body, card_name=card_name, allow_pronoun=allow_pronoun)) is None:
        return None
    first, last, scry = clauses
    compiled = compile_effect(f"{first} {last}", card_name=card_name)
    if compiled[0] is None or compiled[2] is not None or len(compiled[1]) != 2:
        return None
    declaration, companion = compiled[1] if scry else reversed(compiled[1])
    if declaration.get('op') != _DECLARATION_OPERATION or declaration.get('card') not in {'$source', '$source.zone_object'}:
        return None
    if scry:
        if companion.get('op') != 'scry' or companion.get('player') != '$controller':
            return None
    elif (companion.get('op') not in {'apply_source_characteristics_until_end_of_turn', 'place_counters'}
          or companion.get('card') not in {'$source', '$source.zone_object'}):
        return None
    return pin_source_result_incarnation(compiled)


__all__ = ["source_self_contextual_effect_template"]

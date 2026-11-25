from __future__ import annotations

"""Closed fixed draw/life leaves addressed to sealed occurrence players."""

from . import regex_working_set as re
from collections.abc import Mapping
from ..rules.event_subscriptions import FixedEventSubscriptionSet, LAND_PLAY_EVENT
from ..rules.received_damage_events import is_creature_receiving_event
from ..event_player_result_values import event_result_value_capabilities
from ..target_controller_information import (
    OCCURRENCE_CONTROLLER_ENTRY_EVENTS, OCCURRENCE_CONTROLLER_GRAVEYARD_EVENTS, occurrence_controller_reference,
)
from .event_result_value_templates import event_result_instruction
from .public_event_closure_variants import ATTACHED_CREATURE_ATTACK_VARIANTS

EVENT_PLAYER_RESULT_CAPABILITY='resolution.event_player.fixed_result'
EVENT_PLAYER_RESULT_MECHANIC='fixed-event-player-result'
EVENT_PLAYER_DAMAGE_CAPABILITY='resolution.event_player.fixed_damage'
EVENT_PLAYER_DAMAGE_MECHANIC='fixed-event-player-damage'


def _damage_guarantees_player(condition):
    return isinstance(condition,Mapping) and (condition=={'field':'target_kind','op':'eq','value':'player'}
        or any(_damage_guarantees_player(child) for child in condition.get('all',())))


def _event_player(binding,subject):
    try:
        subscriptions=FixedEventSubscriptionSet.from_condition(binding.event_condition)
    except (TypeError,ValueError):return None
    occurrences=((binding.event.value,binding.event_condition),) if subscriptions is None else tuple(
        (entry.event,entry.condition) for entry in subscriptions.subscriptions)
    if binding.event.value not in {event for event,_condition in occurrences}:return None
    players=tuple(_occurrence_player(event,condition,binding.variant,subject) for event,condition in occurrences)
    return players[0] if players and players[0] is not None and all(player==players[0] for player in players) else None


def _qualified_attack_defender(event,condition,variant):
    from ..qualified_attack_model import QUALIFIED_ATTACK_VARIANT
    if variant!=QUALIFIED_ATTACK_VARIANT:return False
    from ..rules.qualified_attacks import qualified_attack_defender_condition_is_closed
    return qualified_attack_defender_condition_is_closed(event,condition)


def _occurrence_player(event,condition,variant,subject):
    if subject=='you':return '$controller'
    if event == 'creature.attacks' and subject == 'that player':
        from ..qualified_attack_model import OPPOSING_PLAYER_ATTACK_VARIANT, opposing_player_attack_condition
        if variant == OPPOSING_PLAYER_ATTACK_VARIANT and condition == opposing_player_attack_condition():
            return '$context.defending_player'
    if event == 'creature.attacks' and subject in {'its controller', "that creature's controller", "that creature’s controller"}:
        from ..rules.qualified_attacks import declared_defender_scope_is_closed, qualified_player_defense_is_closed
        if qualified_player_defense_is_closed(condition) or (
            variant == 'public_attack_a_creature' and declared_defender_scope_is_closed(condition)
        ):
            return occurrence_controller_reference()
    if subject == 'its controller' and condition == {
        'field': 'source_attachment_target_ref', 'op': 'eq', 'value': '$context.card',
    } and (
        event == 'creature.attacks' and variant in ATTACHED_CREATURE_ATTACK_VARIANTS
        or event in {'creature.attacks', 'creature.blocks'} and variant == 'enchanted_creature_attacks_or_blocks'
    ):
        return occurrence_controller_reference()
    if subject.replace('\u2019', "'")=="that creature's controller":
        # CR 608.2h uses current information while the original blocker remains.
        if event == 'creature.blocks' and variant == 'a_creature_blocks' and condition is None:
            return occurrence_controller_reference()
        return '$context.previous_controller' if event=='creature.dies' else None
    if subject=='defending player':
        if event == 'creature.attacks' and (
            variant == 'source_attacks' or variant.startswith('public_attack_') or variant in ATTACHED_CREATURE_ATTACK_VARIANTS
            or _qualified_attack_defender(event,condition,variant)
        ):
            player = '$context.defending_player'
        elif event=='creature.becomes_blocked' and variant in {'this_creature_becomes_blocked','this_creature_becomes_blocked_by_creature'}:player='$context.defending_player'
        elif event=='creature.unblocked' and variant=='source_attacks_unblocked':player='$context.defending_player'
        else:return None
    elif event in {'card.discarded', 'permanent.sacrificed'}:player='$context.previous_controller'
    elif event in {'land.enter','creature.enter','permanent.enter','artifact.enter','enchantment.enter'}:player='$context.controller'
    elif event=='creature.dies':player='$context.previous_controller'
    elif event in {LAND_PLAY_EVENT,'spell.cast','card.drawn','player.scried','player.surveilled'}:
        player='$context.player'
    elif event in {'damage.dealt','damage.dealt.self'} and _damage_guarantees_player(condition):
        player='$context.target'
    else:return None
    return player


def _bound_result_template(binding,text,*,effect_template,card_name):
    compiled=effect_template(text,card_name=card_name)
    if compiled[0] is None and binding.event.value=='spell.cast' and re.search(r"that spell['’]s mana value",text,re.I):
        from .scalar_effect_amounts import scalar_effect_amount_template
        scalar=scalar_effect_amount_template(text,source_name=card_name,event='spell.cast',
            compile_fixed=lambda value:effect_template(value,card_name=card_name))
        if scalar is not None:return scalar
    if compiled[0] is None:
        from .public_query_effect_amounts import public_query_effect_amount_template
        query = public_query_effect_amount_template(text, source_name=card_name,
            compile_fixed=lambda value: effect_template(value, card_name=card_name))
        if query is not None:return query
    return compiled


def _fixed_event_player_clause(binding,body,*,effect_template,card_name):
    match=re.fullmatch(r"(?P<subject>that player|they|defending player|that creature['’]s controller|its controller|you) (?P<verb>draws?|loses?|gains?) (?P<body>.+)",body,re.I)
    if match is None:return None
    player=_event_player(binding,match['subject'].casefold())
    if player is None:return None
    verb=match['verb'].casefold().removesuffix('s')
    normalized=event_result_instruction(match['subject'].casefold(),verb,match['body'])
    if normalized is None:return None
    compiled=_bound_result_template(binding,normalized,effect_template=effect_template,card_name=card_name)
    if compiled[0] is None or compiled[2] is not None or len(compiled[1])!=1:return None
    effect=compiled[1][0];layouts={'draw':('count',{'op','player','count','private'}),
        'life':('delta',{'op','player','delta'}),'lose_life':('amount',{'op','player','amount'})}
    if effect.get('op') not in layouts:return None
    field,fields=layouts[effect['op']]
    if (set(effect)!=fields or effect.get('player')!='$controller'
            or event_result_value_capabilities(effect['op'],field,effect.get(field)) is None
            or effect['op']=='draw' and effect['private'] is not True):return None
    return {**effect,'player':player},compiled[3]


def _spell_cast_count_life_result(binding, body):
    if (
        (binding.event.value, binding.active_zone) != ("spell.cast", "battlefield")
        or binding.spell_subject is None or body != binding.body
    ):
        return None
    from ..event_player_result_values import SpellCastCountLifeSpec

    cases = (
        ("controller", r"You gain 1 life for each spell you['’]ve cast this turn[.]?"),
        ("event_caster", r"They lose 1 life for each spell they['’]ve cast this turn[.]?"),
    )
    for recipient, pattern in cases:
        spec = SpellCastCountLifeSpec(recipient)
        if binding.event_condition == spec.event_condition and re.fullmatch(pattern, body, re.I):
            return (
                "fixed-current-turn-spell-count-life-v1", (spec.effect(),), None,
                (EVENT_PLAYER_RESULT_MECHANIC,),
            ), False
    return None


def fixed_event_player_result(binding,body,*,effect_template,card_name):
    from ..rules.recipient_control_shapes import DAMAGE_DEALER_CONTROL_VARIANT, damage_dealer_control_condition

    if binding.variant == DAMAGE_DEALER_CONTROL_VARIANT:
        if (binding.event.value != "damage.dealt" or binding.active_zone != "battlefield"
                or binding.event_condition != damage_dealer_control_condition()
                or re.fullmatch(r"that creature[\'’]s controller gains control of this land\.?", body.strip(), re.I) is None):
            return None
        from ..recipient_control_model import RECIPIENT_CONTROL_MECHANIC, recipient_control_effect
        from ..target_controller_information import damage_dealer_controller_reference

        control = recipient_control_effect(card="$source.zone_object", controller=damage_dealer_controller_reference())
        return ("damage-dealer-controls-source-land-v1", (control,), None,
                (RECIPIENT_CONTROL_MECHANIC, "cr-611-continuous-effects")), False
    spell_count = _spell_cast_count_life_result(binding, body)
    if spell_count is not None:
        return spell_count
    from ..unblocked_attacker_model import (
        UNBLOCKED_ATTACKER_EVENT, UNBLOCKED_ATTACKER_VARIANT, UNBLOCKED_DEFENDER_POISON_MECHANIC,
        unblocked_defender_poison_effect,
    )
    if (binding.event.value == UNBLOCKED_ATTACKER_EVENT and binding.variant == UNBLOCKED_ATTACKER_VARIANT
            and re.fullmatch(r"Defending player gets a poison counter[.]?", body.strip(), re.I)
            and _event_player(binding, "defending player") == "$context.defending_player"):
        return ("fixed-unblocked-defender-poison-v1", (unblocked_defender_poison_effect(),), None,
                (UNBLOCKED_DEFENDER_POISON_MECHANIC, "cr-122-counters")), False
    from .event_optional_draw_results import fixed_event_optional_draw_result
    optional = fixed_event_optional_draw_result(binding, body, effect_template=effect_template, card_name=card_name)
    if optional is not None:return optional
    from .fixed_controller_effect_sequences import _clauses
    clauses=_clauses(body) or (body,)
    lowered=tuple(_fixed_event_player_clause(binding,clause,effect_template=effect_template,card_name=card_name) for clause in clauses)
    if any(value is None for value in lowered) or not any(value[0]['player']!='$controller' for value in lowered):return None
    return ('fixed-event-player-result-v1',tuple(value[0] for value in lowered),None,
        tuple(dict.fromkeys((*[mechanic for value in lowered for mechanic in value[1]],EVENT_PLAYER_RESULT_MECHANIC)))),False


def _event_guarantees_type(condition, kind):
    if not isinstance(condition, Mapping):
        return False
    values = condition.get('value')
    if (condition.get('field') == 'types' and condition.get('op') == 'contains_any'
            and isinstance(values, (list, tuple)) and tuple(values) == (kind,)):
        return True
    if isinstance(condition.get('all'), (list, tuple)):
        return any(_event_guarantees_type(child, kind) for child in condition['all'])
    choices = condition.get('any')
    return isinstance(choices, (list, tuple)) and bool(choices) and all(
        _event_guarantees_type(child, kind) for child in choices
    )


def _zone_damage_controller(binding, kind):
    from .aggregated_zone_event_bindings import AGGREGATED_ZONE_VARIANT

    event = binding.event.value
    if (binding.variant == AGGREGATED_ZONE_VARIANT
            or event not in OCCURRENCE_CONTROLLER_ENTRY_EVENTS | OCCURRENCE_CONTROLLER_GRAVEYARD_EVENTS):
        return None
    if kind not in ('permanent', event.split('.', 1)[0]) and not _event_guarantees_type(binding.event_condition, kind):
        return None
    return occurrence_controller_reference() if event in OCCURRENCE_CONTROLLER_ENTRY_EVENTS else '$context.previous_controller'


_DEFENDING_CREATURE_DAMAGE = re.compile(
    r"(?:(?:it|this creature) deals|(?P<optional>you may have it deal)) "
    r"(?P<amount>[1-9][0-9]*) damage to each creature defending player controls\.", re.I,
)


def defending_creature_damage_result(binding, body):
    if binding.variant != "source_attacks" or binding.event.value != "creature.attacks":
        return None
    match = _DEFENDING_CREATURE_DAMAGE.fullmatch(body.strip())
    if match is None:
        return None
    from ..rules.defending_creature_damage import defending_creature_damage_effect

    template = "fixed-defending-player-creature-damage-v1"
    effects = (defending_creature_damage_effect(int(match["amount"])),)
    mechanics = ("cr-120-damage",)
    if match["optional"] is not None:
        from .optional_effect_templates import FIXED_OPTIONAL_EFFECT_MECHANIC, FixedOptionalEffectTemplate

        return FixedOptionalEffectTemplate(template, effects, None,
            (FIXED_OPTIONAL_EFFECT_MECHANIC, *mechanics)).compiled(), False
    return (template, effects, None, mechanics), False


def fixed_event_player_damage_result(binding,body,*,effect_template,card_name):
    if creatures := defending_creature_damage_result(binding, body):
        return creatures
    match=re.fullmatch(r"(?P<prefix>.+? deals .+? to )(?P<subject>that player|them|defending player|that "
                       r"(?P<kind>artifact|creature|enchantment|land|permanent)['’]s controller)\.?",body,re.I)
    if match is None:return None
    if match["subject"].casefold() == "them" and binding.event.value != "permanent.sacrificed":
        return None
    received = (match['kind'] is not None and match['kind'].casefold() == 'creature'
                and is_creature_receiving_event(binding.event.value, binding.event_condition))
    kind = match['kind'].casefold() if match['kind'] is not None else None
    if received:
        player = occurrence_controller_reference()
    elif kind is None or kind == 'creature' and binding.event.value == 'creature.blocks':
        player = _event_player(binding, match['subject'].casefold())
    else:
        player = _zone_damage_controller(binding, kind)
    if player is None:return None
    normalized = match['prefix'] + 'you.'
    if received:
        from .scalar_effect_amounts import scalar_effect_amount_template
        compiled = scalar_effect_amount_template(normalized, source_name=card_name, event=binding.event.value,
            compile_fixed=lambda value: effect_template(value, card_name=card_name))
        if compiled is None:
            return None
    else:
        compiled = _bound_result_template(binding, normalized, effect_template=effect_template, card_name=card_name)
    if compiled[0] is None:
        from .source_self_effect_templates import bound_source_fixed_damage
        compiled = bound_source_fixed_damage(binding, normalized) or compiled
    if compiled[0] is None or compiled[2] is not None or len(compiled[1])!=1:return None
    effect=compiled[1][0]
    if (set(effect)!={'op','source','target','amount'} or effect.get('op')!='damage' or effect.get('source')!='$source'
            or binding.event.value == 'creature.blocks' and type(effect.get('amount')) is not int
            or effect.get('target')!='$controller' or event_result_value_capabilities(
                'damage', 'amount', effect.get('amount'), committed_damage=received) is None):return None
    return ('fixed-event-player-damage-v1',({**effect,'target':player},),None,
        tuple(dict.fromkeys((*compiled[3],EVENT_PLAYER_RESULT_MECHANIC,EVENT_PLAYER_DAMAGE_MECHANIC)))),False

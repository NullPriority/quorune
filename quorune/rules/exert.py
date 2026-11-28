from __future__ import annotations

"""Optional attack Exert costs with current ability and linked-program authority."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..ability_fragments import ability_fragment_from_dict, canonical_ability_fragments
from ..card_program_faces import program_matches_face
from ..entry_copy import copiable_oracle_id
from ..errors import GameRuleError
from ..exert_model import EXERT_ATTACK_HANDLER, EXERT_FIRST_THIS_TURN_CAPABILITY, ExertAttackSpec
from ..turn_history import incarnation_exerted_this_turn
from ..tap_state import exert_permanent


@dataclass(frozen=True, slots=True)
class ExertAttackCost:
    object_id: str
    logical_object_id: str
    player: str
    result_semantic_key: str


def current_exert_specs(host, card):
    return tuple(spec for spec in canonical_ability_fragments(
        host._effective_card_data(card).get('ability_fragments', ())
    ) if isinstance(spec, ExertAttackSpec))


def legal_exert_specs(host, card):
    specs = current_exert_specs(host, card)
    if not specs:
        return ()
    linked_results = {
        program.key for program in host.semantics.programs_for_oracle(copiable_oracle_id(card))
        if program.event == "permanent.exerted"
        and program.provenance.get("template_id") == "linked-attack-exert-result-v1"
        and host.semantic_program_is_current_trusted(program)
        and program_matches_face(host.card_record(card), program, card)
    }
    return tuple(spec for spec in specs if spec.result_semantic_key in linked_results
                 and (not spec.requires_unexerted_this_turn
                      or incarnation_exerted_this_turn(host.state, card.logical_object_id) is False))


def guarded_exert_runtime_dependencies(program):
    fragments = [
        ability_fragment_from_dict(handler["fragment"])
        for handler in program.handlers if handler.get("handler_id") == EXERT_ATTACK_HANDLER
    ]
    if any(isinstance(fragment, ExertAttackSpec) and fragment.requires_unexerted_this_turn for fragment in fragments):
        return (EXERT_FIRST_THIS_TURN_CAPABILITY,)
    if EXERT_FIRST_THIS_TURN_CAPABILITY in program.capability_dependencies and program.event != "permanent.exerted":
        raise ValueError("A guarded Exert declaration requires its typed current-turn permission")
    return ()


def other_creature_extra_combat_runtime_dependencies(program):
    from ..extra_combat_model import (
        EXTRA_COMBAT_OTHER_CREATURES_CAPABILITY, extra_combat_effect_capabilities,
        uses_other_creature_extra_combat,
    )
    from ..replacement.immutable import FrozenMap
    from .effect_shape_walkers import nested_effects

    marked = tuple(effect for effect in nested_effects((*program.effects, *program.handlers))
                   if uses_other_creature_extra_combat(effect))
    if not marked and EXTRA_COMBAT_OTHER_CREATURES_CAPABILITY not in program.capability_dependencies:
        return ()
    expected = {"all": [
        {"field": "card", "op": "eq", "value": "$source.ref"},
        {"field": "linked_exert_program", "op": "eq", "value": program.key},
    ]}
    if (len(marked) != 1 or tuple(program.effects) != marked or program.handlers
            or (program.event, program.active_zone) != ("permanent.exerted", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None
            or not isinstance(program.event_condition, Mapping) or FrozenMap(program.event_condition) != FrozenMap(expected)):
        raise ValueError("The other-creature extra combat requires its original linked Exert trigger")
    return extra_combat_effect_capabilities(marked[0])


def exert_offer_rows(host, seat, candidates):
    cards = {card.ref: card for card in host.state.cards.values()}
    rows = []
    for row in candidates:
        card = cards[str(row['id'])]
        specs = legal_exert_specs(host, card)
        if specs:
            rows.append({'value': card.ref, 'label': str(row.get('name') or card.ref),
                         'maximum': len(specs),
                         'options': [{'value': spec.result_semantic_key,
                                      'label': 'Exert as this creature attacks'} for spec in specs]})
    return rows


def prepare_exert_costs(host, seat, chosen, raw):
    if raw is None:
        return ()
    if not isinstance(raw, Mapping) or any(type(ref) is not str for ref in raw):
        raise GameRuleError('Exert must map declared attackers to linked ability identities')
    attackers = {card.ref: card for card, _details in chosen}
    plans = []
    for ref, keys in sorted(raw.items()):
        card = attackers.get(ref)
        if card is None:
            raise GameRuleError('Only a declared attacker may pay an Exert attack cost')
        if type(keys) is str:
            keys = [keys]
        if not isinstance(keys, (list, tuple)) or not keys or any(type(key) is not str for key in keys):
            raise GameRuleError('Exert requires a nonempty list of linked ability identities')
        if len(set(keys)) != len(keys):
            raise GameRuleError('An Exert attack cost cannot be paid twice')
        current = {spec.result_semantic_key for spec in legal_exert_specs(host, card)}
        if any(key not in current for key in keys):
            raise GameRuleError('Exert requires its current ability and independently trusted linked result')
        plans.extend(ExertAttackCost(card.object_id, card.logical_object_id, seat, key)
                     for key in sorted(keys))
    return tuple(plans)


def commit_exert_costs(host, plans, *, trigger_batch):
    for plan in plans:
        card = host.state.cards.get(plan.object_id)
        if card is None or card.logical_object_id != plan.logical_object_id:
            raise GameRuleError('Exert attack cost lost its original permanent')
        exert_permanent(host, card, actor=plan.player,
                        linked_program=plan.result_semantic_key, trigger_batch=trigger_batch)

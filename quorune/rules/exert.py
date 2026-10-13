from __future__ import annotations

"""Optional attack Exert costs with current ability and linked-program authority."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..ability_fragments import canonical_ability_fragments
from ..card_program_faces import program_matches_face
from ..entry_copy import copiable_oracle_id
from ..errors import GameRuleError
from ..exert_model import ExertAttackSpec
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


def exert_offer_rows(host, seat, candidates):
    cards = {card.ref: card for card in host.state.cards.values()}
    rows = []
    for row in candidates:
        card = cards[str(row['id'])]
        specs = current_exert_specs(host, card)
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
        current = {spec.result_semantic_key for spec in current_exert_specs(host, card)}
        trusted = {program.key for program in host.semantics.programs_for_oracle(copiable_oracle_id(card))
                   if program.event == 'permanent.exerted'
                   and program.provenance.get('template_id') == 'linked-attack-exert-result-v1'
                   and host.semantic_program_is_current_trusted(program)
                   and program_matches_face(host.card_record(card), program, card)}
        if any(key not in current or key not in trusted for key in keys):
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

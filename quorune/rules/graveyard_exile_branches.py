"""Read the ordinary exile receipt and send one exclusive result to its owner."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..errors import GameRuleError
from ..graveyard_exile_branches_model import (
    GRAVEYARD_EXILE_BRANCH_BASE_DEPENDENCIES,
    GRAVEYARD_EXILE_BRANCH_OPERATION,
    GraveyardExileBranchSpec,
    GraveyardExileConsequence,
)
from ..object_query import ObjectQueryResult
from ..replacement.immutable import FrozenMap
from .committed_quantity_followups import CommittedQuantityFollowup
from .graveyard_card_targets import PublicGraveyardCardTargetKind, PublicGraveyardCardTargetSpec


def graveyard_exile_branch_target_schema() -> dict:
    return PublicGraveyardCardTargetSpec(PublicGraveyardCardTargetKind.CARD, owner_relation='any').to_target_schema()


def _is_branch(effect: object) -> bool:
    return isinstance(effect, Mapping) and effect.get('op') == GRAVEYARD_EXILE_BRANCH_OPERATION


def _decode_branch(effect: Mapping) -> GraveyardExileBranchSpec:
    if set(effect) != {'op', 'card', 'result'} or effect.get('card') != '$target.0':
        raise ValueError('Exclusive graveyard exile requires exactly one direct graveyard card target')
    return GraveyardExileBranchSpec.from_dict(effect['result'])


def graveyard_exile_branch_payload_dependencies(effect) -> tuple[str, ...]:
    if not _is_branch(effect):
        return ()
    spec = _decode_branch(effect)
    dependent = ('continuous.resolution.fixed_characteristics_until_end_of_turn' if spec.optional
        else 'zone.draw.library_to_hand')
    return (*GRAVEYARD_EXILE_BRANCH_BASE_DEPENDENCIES, 'life.change.effect', dependent)


def _branch_dependencies(effects, target_schema) -> tuple[str, ...]:
    if (len(effects) != 1 or not isinstance(effects[0], Mapping)
            or not isinstance(target_schema, Mapping) or type(target_schema.get('count')) is not int
            or FrozenMap(target_schema) != FrozenMap(graveyard_exile_branch_target_schema())):
        return ()
    effect = effects[0]
    optional = effect.get('op') == 'offer_optional_effect'
    if optional:
        child = effect.get('effects')
        if (set(effect) != {'op', 'player', 'effects'} or effect.get('player') != '$controller'
                or not isinstance(child, (list, tuple)) or len(child) != 1):
            return ()
        effect = child[0]
    if not _is_branch(effect) or _decode_branch(effect).optional != optional:
        return ()
    dependencies = graveyard_exile_branch_payload_dependencies(effect)
    return (*dependencies, 'effect.choice.optional_fixed') if optional else dependencies


def graveyard_exile_branch_node_capabilities(*, effects, target_schema, **kwargs) -> tuple[str, ...]:
    try:
        return _branch_dependencies(effects, target_schema)
    except (ValueError, TypeError):
        return ()


def graveyard_exile_branch_runtime_dependencies(program) -> tuple[str, ...]:
    def contains_branch(value: object) -> bool:
        if isinstance(value, Mapping):
            return _is_branch(value) or any(contains_branch(child) for child in value.values())
        if isinstance(value, (list, tuple)):
            return any(contains_branch(child) for child in value)
        return False

    if not contains_branch((program.effects, program.handlers)):
        return ()
    dependencies = _branch_dependencies(program.effects, program.target_schema)
    if not dependencies or contains_branch(program.handlers):
        raise ValueError('Exclusive graveyard exile must retain its exact single-target and optional envelope')
    return dependencies


def is_closed_graveyard_exile_branch_spell(program) -> bool:
    if (program.event != 'resolve' or program.handlers or len(program.effects) != 1
            or not _is_branch(program.effects[0])):
        return False
    dependencies = graveyard_exile_branch_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    return bool(dependencies) and set(dependencies) <= set(program.capability_dependencies)


def _public_graveyard_card_types(host, card) -> frozenset[str]:
    row = host._public_object_query_result(card)
    if (not isinstance(row, ObjectQueryResult) or row.object_id != card.object_id
            or row.logical_object_id != card.logical_object_id or row.known_to_actor is not True or row.token):
        raise GameRuleError('Exclusive graveyard exile needs the current public facts of its physical card')
    return frozenset(row.types)


def _committed_exile_card_types(host, receipt) -> frozenset[str]:
    current = host.state.cards.get(receipt.object_id)
    if (current is None or not current.is_card_object or current.zone != 'exile'
            or current.logical_object_id != receipt.logical_object_id):
        raise GameRuleError('The committed exile card is no longer its recorded incarnation')
    return frozenset() if current.face_down else _public_graveyard_card_types(host, current)


def _committed_creature_card(host, receipt) -> bool:
    return 'creature' in _committed_exile_card_types(host, receipt)


def _followup(host, intent, consequence: GraveyardExileConsequence | None) -> CommittedQuantityFollowup:
    if consequence is None:
        return CommittedQuantityFollowup(())
    if consequence is GraveyardExileConsequence.DRAW_ONE:
        effect = {'op': 'draw', 'player': '$controller', 'count': 1, 'private': True}
    elif consequence is GraveyardExileConsequence.SOURCE_PLUS_ONE_UNTIL_END_OF_TURN:
        source = host.state.cards.get(intent.source_object_id)
        if (source is None or source.zone != 'battlefield' or source.phased_out
                or source.logical_object_id != intent.source_logical_object_id):
            return CommittedQuantityFollowup(())
        effect = {'op': 'modify_stats_until_end_of_turn', 'card': '$source.zone_object', 'power': 1, 'toughness': 1}
    else:
        amount = 3 if consequence is GraveyardExileConsequence.GAIN_THREE_LIFE else 2
        effect = {'op': 'life', 'player': '$controller', 'delta': amount}
    return CommittedQuantityFollowup((FrozenMap(effect),))


@dataclass(frozen=True, slots=True)
class GraveyardCreatureExileFacts:
    committed: bool
    creature_card: bool | None


def _commit_exile_receipt(host, intent, *, capture_prior: bool):
    """Commit the ordinary movement and use only the appropriate public or cached facts."""
    from ..public_zone_moves import PublicZoneMoveError, exile_public_graveyard_card_transition

    host._require_seat(intent.actor, in_game=True)
    card = host._resolve_object(intent.actor, intent.object_ref, zones={'graveyard'})
    if not card.is_card_object or card.zone != 'graveyard':
        raise GameRuleError('Exclusive graveyard exile requires a current physical graveyard card')
    original_id = card.logical_object_id
    prior_types = _public_graveyard_card_types(host, card) if capture_prior else None
    try:
        receipt = exile_public_graveyard_card_transition(host, intent.object_ref, actor=intent.actor,
            reason=intent.reason, replacement_selections=intent.replacement_selections)
    except PublicZoneMoveError as exc:
        raise GameRuleError(str(exc)) from exc
    if receipt.object_id != card.object_id:
        raise GameRuleError('Exclusive graveyard exile receipt names a different card')
    committed = receipt.actual_destination == 'exile' and receipt.logical_object_id != original_id
    return committed, receipt, prior_types


def commit_classified_graveyard_exile(host, intent, *, requires_committed_exile: bool) -> GraveyardCreatureExileFacts:
    committed, receipt, prior = _commit_exile_receipt(host, intent, capture_prior=not requires_committed_exile)
    prior_creature = None if prior is None else 'creature' in prior
    creature = _committed_creature_card(host, receipt) if committed else prior_creature
    return GraveyardCreatureExileFacts(committed, creature)


def commit_graveyard_exile_card_types(host, intent) -> frozenset[str]:
    committed, receipt, prior = _commit_exile_receipt(host, intent, capture_prior=True)
    assert prior is not None
    return _committed_exile_card_types(host, receipt) if committed else prior


def commit_graveyard_exile_branch(host, intent) -> CommittedQuantityFollowup:
    from ..semantic_runtime.graveyard_exile_branch_intent import GraveyardExileBranchIntent

    if not isinstance(intent, GraveyardExileBranchIntent):
        raise GameRuleError('Exclusive graveyard exile requires a typed intent')
    facts = commit_classified_graveyard_exile(host, intent, requires_committed_exile=intent.result.optional)
    consequence = intent.result.consequence(exile_committed=facts.committed, creature_card=facts.creature_card)
    return _followup(host, intent, consequence)

"""Measure actual public card arrivals through simultaneous graveyard exile."""

from collections.abc import Mapping

from ..errors import GameRuleError
from ..object_predicate import ObjectQuerySpec
from ..object_query import object_matches_query
from ..replacement.immutable import FrozenMap
from ..targeted_graveyard_outcome_model import (
    CreatureExileOutcome, GraveyardExileTargetScope, TargetedGraveyardExileOutcomeSpec,
)
from .committed_quantity_followups import CommittedQuantityFollowup, quantity_followup_from_count
from .graveyard_card_targets import PublicGraveyardCardTargetKind, PublicGraveyardCardTargetSpec


TARGETED_GRAVEYARD_EXILE_OPERATION = 'exile_public_graveyard_targets'
TARGETED_GRAVEYARD_OUTCOME_FIELD = 'followup'
DAMAGE_TARGET_BOUND_FIELD = 'combat_damage_target_bound'
DAMAGE_GRAVEYARD_OUTCOME_CAPABILITY = 'card.exile.damaged_player_graveyard_committed_partition'
_DAMAGE_OUTCOME = TargetedGraveyardExileOutcomeSpec(
    GraveyardExileTargetScope.UP_TO_DEALT_DAMAGE, CreatureExileOutcome.COUNTERS_AND_LIFE_FROM_PARTITION, 2,
)
_DAMAGE_MAXIMUM = '$context.amount'
_DAMAGE_OWNER = '$context.target'
TARGETED_GRAVEYARD_OUTCOME_CAPABILITY = 'card.exile.public_graveyard_targeted_outcomes'
TARGETED_GRAVEYARD_OUTCOME_BASE_DEPENDENCIES = (
    'card.exile.public_graveyard',
    'resolution.effect.fixed_homogeneous_target_set',
    'target.revalidate_resolution',
)

_EXILED_CREATURE = ObjectQuerySpec(zones=('exile',), types_all=('creature',), token=False)


def targeted_graveyard_outcome_schema(spec: TargetedGraveyardExileOutcomeSpec) -> dict:
    if spec.damage_linked:
        from .event_player_targets import damaged_player_graveyard_schema

        return {**damaged_player_graveyard_schema(), 'up_to': 0, DAMAGE_TARGET_BOUND_FIELD: 1}
    target = PublicGraveyardCardTargetSpec(PublicGraveyardCardTargetKind.CARD, owner_relation='any')
    schema = dict(target.to_target_schema())
    if spec.scope.minimum == 0:
        del schema['count']
        schema['up_to'] = spec.scope.maximum
    if spec.scope.same_graveyard:
        schema['same_owner'] = True
    return schema


def targeted_graveyard_outcome_effect(spec: TargetedGraveyardExileOutcomeSpec) -> dict:
    if spec.damage_linked:
        return {
            'op': TARGETED_GRAVEYARD_EXILE_OPERATION, 'cards': '$targets',
            'maximum_targets': _DAMAGE_MAXIMUM, 'owner_seat': _DAMAGE_OWNER,
            TARGETED_GRAVEYARD_OUTCOME_FIELD: spec.to_dict(),
        }
    return {
        'op': TARGETED_GRAVEYARD_EXILE_OPERATION,
        'cards': '$targets',
        'maximum_targets': spec.scope.maximum,
        TARGETED_GRAVEYARD_OUTCOME_FIELD: spec.to_dict(),
    }


def damage_graveyard_outcome_template():
    return targeted_graveyard_outcome_effect(_DAMAGE_OUTCOME), targeted_graveyard_outcome_schema(_DAMAGE_OUTCOME)


def bind_damage_graveyard_target_bound(schema, context):
    if not _schema_matches_recipe(schema, _DAMAGE_OUTCOME):
        raise ValueError('Damage-linked graveyard target bounds require the exact compiled schema')
    amount = context.get('amount')
    if (context.get('target_kind') != 'player' or context.get('combat') is not True
            or type(amount) is not int or amount < 1):
        raise ValueError('Damage-linked graveyard targets require committed combat damage to a player')
    return {**{key: value for key, value in schema.items() if key != DAMAGE_TARGET_BOUND_FIELD}, 'up_to': amount}


def read_targeted_graveyard_outcome(effect: Mapping[str, object], *, resolved: bool = False) -> TargetedGraveyardExileOutcomeSpec:
    if not is_targeted_graveyard_outcome(effect):
        raise ValueError('Targeted graveyard exile requires an owned outcome instruction')
    spec = TargetedGraveyardExileOutcomeSpec.from_dict(effect[TARGETED_GRAVEYARD_OUTCOME_FIELD])
    if spec.damage_linked:
        expected = targeted_graveyard_outcome_effect(spec)
        if resolved:
            maximum, owner = effect.get('maximum_targets'), effect.get('owner_seat')
            if (type(maximum) is not int or maximum < 1 or type(owner) is not str or not owner or owner.startswith('$')):
                raise ValueError('Damage-linked exile requires the committed damage and damaged player')
            expected.update(maximum_targets=maximum, owner_seat=owner, cards=effect.get('cards'))
        if {key: value for key, value in effect.items() if not resolved or key not in {'reason', '_replacement_selections'}} != expected:
            raise ValueError('Damage-linked exile must preserve its target bound, owner and outcome')
        return spec
    required = {'op', 'cards', 'maximum_targets', TARGETED_GRAVEYARD_OUTCOME_FIELD}
    extra = {'reason', '_replacement_selections'} if resolved else set()
    if not isinstance(effect, Mapping) or not required <= set(effect) <= required | extra:
        raise ValueError('Targeted graveyard exile outcome instruction fields are closed')
    if effect['op'] != TARGETED_GRAVEYARD_EXILE_OPERATION or (not resolved and effect['cards'] != '$targets'):
        raise ValueError('Targeted graveyard exile outcome requires the ordinary fixed target set')
    if type(effect['maximum_targets']) is not int or effect['maximum_targets'] != spec.scope.maximum:
        raise ValueError('Targeted graveyard exile outcome must retain the recipe target bound')
    return spec


def is_targeted_graveyard_outcome(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get('op') == TARGETED_GRAVEYARD_EXILE_OPERATION
        and TARGETED_GRAVEYARD_OUTCOME_FIELD in value
    )


def targeted_graveyard_outcome_payload_dependencies(effect) -> tuple[str, ...]:
    if not is_targeted_graveyard_outcome(effect):
        return ()
    spec = read_targeted_graveyard_outcome(effect)
    if spec.damage_linked:
        from .event_player_targets import DAMAGED_PLAYER_GRAVEYARD_EXILE_CAPABILITY

        return (DAMAGE_GRAVEYARD_OUTCOME_CAPABILITY, DAMAGED_PLAYER_GRAVEYARD_EXILE_CAPABILITY,
                *TARGETED_GRAVEYARD_OUTCOME_BASE_DEPENDENCIES, 'counter.producer.fixed_effect', 'life.change.effect')
    outcome = spec.outcome
    dependencies = (TARGETED_GRAVEYARD_OUTCOME_CAPABILITY, *TARGETED_GRAVEYARD_OUTCOME_BASE_DEPENDENCIES)
    if outcome is CreatureExileOutcome.CREATE_ONE_CLUE:
        return (*dependencies, 'token.creation.fixed_definition', 'zone.draw.library_to_hand')
    if outcome is CreatureExileOutcome.CREATE_ONE_FOOD:
        return (*dependencies, 'token.creation.fixed_definition', 'life.change.effect')
    return (*dependencies, 'life.change.effect')


def _schema_matches_recipe(target_schema, spec: TargetedGraveyardExileOutcomeSpec) -> bool:
    number_field = 'count' if spec.scope.minimum == 1 else 'up_to'
    return (
        isinstance(target_schema, Mapping)
        and type(target_schema.get(number_field)) is int
        and ('same_owner' not in target_schema or target_schema['same_owner'] is True)
        and (not spec.damage_linked or type(target_schema.get(DAMAGE_TARGET_BOUND_FIELD)) is int)
        and FrozenMap(target_schema) == FrozenMap(targeted_graveyard_outcome_schema(spec))
    )


def targeted_graveyard_outcome_node_capabilities(*, effects, target_schema) -> tuple[str, ...]:
    if len(effects) != 1 or not is_targeted_graveyard_outcome(effects[0]):
        return ()
    effect = effects[0]
    try:
        recipe = read_targeted_graveyard_outcome(effect)
    except (TypeError, ValueError):
        return ()
    return targeted_graveyard_outcome_payload_dependencies(effect) if _schema_matches_recipe(target_schema, recipe) else ()


def targeted_graveyard_outcome_runtime_dependencies(program) -> tuple[str, ...]:
    def contains_outcome(value) -> bool:
        if isinstance(value, Mapping):
            return is_targeted_graveyard_outcome(value) or any(contains_outcome(child) for child in value.values())
        if isinstance(value, (list, tuple)):
            return any(contains_outcome(child) for child in value)
        return False

    if not contains_outcome((program.effects, program.handlers)):
        return ()
    required = targeted_graveyard_outcome_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if not required or contains_outcome(program.handlers):
        raise ValueError('Targeted graveyard exile outcome requires its one direct instruction and exact target schema')
    if DAMAGE_GRAVEYARD_OUTCOME_CAPABILITY in required:
        from .event_player_targets import is_source_combat_damage_player_binding

        if (program.active_zone != 'battlefield' or program.cost_schema is not None
                or not is_source_combat_damage_player_binding(program.event, program.event_condition)):
            raise ValueError('Damage-linked graveyard exile requires the source combat damage occurrence')
    return required


def _partition_exiled_cards(host, original_incarnations: Mapping[str, str], moved) -> tuple[int, int]:
    exiled = {}
    for card in moved:
        original = original_incarnations.get(card.object_id)
        if (
            original is None
            or card.logical_object_id == original
            or card.zone != 'exile'
            or card.face_down
            or not card.is_card_object
        ):
            continue
        row = host._public_object_query_result(card)
        if row.object_id != card.object_id or row.logical_object_id != card.logical_object_id or not row.known_to_actor:
            raise GameRuleError('Creature exile outcome requires public facts for the actual exile incarnation')
        exiled[card.object_id] = object_matches_query(row, _EXILED_CREATURE)
    creatures = sum(exiled.values())
    return creatures, len(exiled) - creatures


def _commit_targeted_graveyard_exile_batch(host, intent) -> tuple[int, int]:
    """Commit one legal target set and classify only its actual public arrivals."""
    from ..semantic_runtime.targeted_graveyard_outcome_intent import TargetedGraveyardExileOutcomeIntent

    if not isinstance(intent, TargetedGraveyardExileOutcomeIntent):
        raise GameRuleError('Creature exile outcome requires the typed graveyard target intent')
    host._require_seat(intent.actor, in_game=True)
    if not intent.object_refs:
        return 0, 0
    cards = tuple(host._resolve_object(intent.actor, ref, zones={'graveyard'}) for ref in intent.object_refs)
    originals = {card.object_id: card.logical_object_id for card in cards}
    if len(originals) != len(cards) or any(card.zone != 'graveyard' or not card.is_card_object for card in cards):
        raise GameRuleError('Creature exile outcome needs distinct physical graveyard cards')
    if intent.result.scope.same_graveyard and len({card.owner for card in cards}) != 1:
        raise GameRuleError('Creature exile outcome requires cards from one graveyard')
    if intent.damaged_player is not None and any(card.owner != intent.damaged_player for card in cards):
        raise GameRuleError('Damage-linked exile requires the damaged player’s graveyard')
    moved = host._move_cards_simultaneously(
        tuple((card.object_id, 'exile') for card in cards),
        reason=intent.reason,
        log=False,
        replacement_selections=intent.replacement_selections,
    )
    return _partition_exiled_cards(host, originals, moved)


def commit_targeted_graveyard_exiles(host, intent) -> int:
    return _commit_targeted_graveyard_exile_batch(host, intent)[0]


def commit_targeted_graveyard_outcome(host, intent) -> CommittedQuantityFollowup:
    if not intent.result.damage_linked:
        return creature_exile_followup(intent.result, commit_targeted_graveyard_exiles(host, intent))
    creatures, noncreatures = _commit_targeted_graveyard_exile_batch(host, intent)
    counter = FrozenMap({
        'op': 'place_counters', 'card': '$source.zone_object', 'counter': '+1/+1',
        'amount': 1, 'source': '$source',
    })
    life = FrozenMap({'op': 'life', 'player': '$controller', 'delta': 1})
    return CommittedQuantityFollowup((
        *quantity_followup_from_count(counter, creatures).effects,
        *quantity_followup_from_count(life, noncreatures).effects,
    ))


def creature_exile_followup(spec: TargetedGraveyardExileOutcomeSpec, count: int) -> CommittedQuantityFollowup:
    if type(count) is not int or not 0 <= count <= spec.scope.maximum:
        raise ValueError('Creature exile outcome requires its committed target count')
    if count == 0:
        return CommittedQuantityFollowup(())
    if spec.outcome is CreatureExileOutcome.GAIN_ONE_PER_CREATURE:
        life = FrozenMap({'op': 'life', 'player': '$controller', 'delta': 1})
        return quantity_followup_from_count(life, count)
    if spec.outcome is CreatureExileOutcome.DRAIN_TWO_ONCE:
        return CommittedQuantityFollowup((FrozenMap({'op': 'drain_each_opponent', 'amount': 2}),))

    from ..fixed_token_production import clue_token_effect, food_token_effect

    make_token = clue_token_effect if spec.outcome is CreatureExileOutcome.CREATE_ONE_CLUE else food_token_effect
    return CommittedQuantityFollowup((FrozenMap(make_token(1)),))

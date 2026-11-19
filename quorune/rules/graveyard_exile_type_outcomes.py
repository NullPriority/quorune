"""Closed creature and land outcomes of one graveyard card exile."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..errors import GameRuleError
from ..graveyard_exile_token_model import ExiledCreatureCardPredicate, GraveyardExileTokenSpec
from ..replacement.immutable import FrozenMap, thaw_value
from .committed_quantity_followups import CommittedQuantityFollowup, fixed_followup_capabilities, fixed_quantity_effect
from .effect_shape_walkers import nested_effect_operations
from .graveyard_exile_branches import commit_graveyard_exile_card_types, graveyard_exile_branch_target_schema


GRAVEYARD_EXILE_TYPE_OUTCOME_OPERATION = 'exile_public_graveyard_card_for_type_outcomes'
GRAVEYARD_EXILE_TYPE_OUTCOME_CAPABILITY = 'card.exile.public_graveyard_overlapping_type_outcomes'
GRAVEYARD_EXILE_TYPE_OUTCOME_BASE_DEPENDENCIES = (
    GRAVEYARD_EXILE_TYPE_OUTCOME_CAPABILITY,
    'card.exile.public_graveyard',
    'target.revalidate_resolution',
)


@dataclass(frozen=True, slots=True)
class GraveyardExileTypeOutcomeSpec:
    creature_token: Mapping[str, object]
    treasure_token: Mapping[str, object]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Graveyard exile type outcomes require schema version one')
        creature = GraveyardExileTokenSpec(ExiledCreatureCardPredicate.CREATURE_CARD, self.creature_token)
        if creature.token_effect['characteristics'].get('ability_fragments'):
            raise ValueError('Graveyard exile type outcomes require an ordinary creature token definition')
        treasure = fixed_quantity_effect(self.treasure_token)
        if (set(treasure) != {'op', 'controller', 'quantity', 'name', 'characteristics'}
                or treasure.get('name') != 'Treasure Token'
                or treasure['characteristics'].get('type_line') != 'Token Artifact — Treasure'):
            raise ValueError('Graveyard exile land outcome requires the ordinary Treasure token')
        object.__setattr__(self, 'creature_token', creature.token_effect)
        object.__setattr__(self, 'treasure_token', treasure)

    def to_dict(self) -> dict:
        return {
            'schema_version': self.schema_version,
            'creature_token': thaw_value(self.creature_token),
            'treasure_token': thaw_value(self.treasure_token),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> 'GraveyardExileTypeOutcomeSpec':
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'creature_token', 'treasure_token'}:
            raise ValueError('Graveyard exile type outcome descriptor fields are closed')
        return cls(value['creature_token'], value['treasure_token'], value['schema_version'])

    def effect(self) -> dict:
        return {'op': GRAVEYARD_EXILE_TYPE_OUTCOME_OPERATION, 'card': '$target.0', 'result': self.to_dict()}

    def followup(self, *, creature_card: bool, land_card: bool) -> CommittedQuantityFollowup:
        """Apply each matching token clause in order, or life if neither matches."""
        if type(creature_card) is not bool or type(land_card) is not bool:
            raise ValueError('Graveyard exile type outcomes require known creature and land classifications')
        tokens = tuple(token for matches, token in ((creature_card, self.creature_token),
            (land_card, self.treasure_token)) if matches)
        return CommittedQuantityFollowup(tokens or (FrozenMap({'op': 'life', 'player': '$controller', 'delta': 3}),))

def _is_type_outcome(effect) -> bool:
    return isinstance(effect, Mapping) and effect.get('op') == GRAVEYARD_EXILE_TYPE_OUTCOME_OPERATION


def graveyard_exile_type_outcome_payload_dependencies(effect) -> tuple[str, ...]:
    if not _is_type_outcome(effect):
        return ()
    if set(effect) != {'op', 'card', 'result'} or effect['card'] != '$target.0':
        raise ValueError('Graveyard exile type outcomes require exactly one direct card target')
    result = GraveyardExileTypeOutcomeSpec.from_dict(effect['result'])
    return tuple(dict.fromkeys((*GRAVEYARD_EXILE_TYPE_OUTCOME_BASE_DEPENDENCIES, 'life.change.effect',
        *fixed_followup_capabilities(result.creature_token), *fixed_followup_capabilities(result.treasure_token))))


def graveyard_exile_type_outcome_node_capabilities(*, effects, target_schema, **kwargs) -> tuple[str, ...]:
    if (len(effects) != 1 or not _is_type_outcome(effects[0]) or not isinstance(target_schema, Mapping)
            or type(target_schema.get('count')) is not int
            or FrozenMap(target_schema) != FrozenMap(graveyard_exile_branch_target_schema())):
        return ()
    try:
        return graveyard_exile_type_outcome_payload_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def graveyard_exile_type_outcome_runtime_dependencies(program) -> tuple[str, ...]:
    operation = GRAVEYARD_EXILE_TYPE_OUTCOME_OPERATION
    if operation not in nested_effect_operations((program.effects, program.handlers)):
        return ()
    required = graveyard_exile_type_outcome_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if not required or operation in nested_effect_operations(program.handlers):
        raise ValueError('Graveyard exile type outcomes require their direct instruction and exact card target')
    return required


def commit_graveyard_exile_type_outcomes(host, intent) -> CommittedQuantityFollowup:
    from ..semantic_runtime.graveyard_exile_type_outcome_intent import GraveyardExileTypeOutcomeIntent

    if not isinstance(intent, GraveyardExileTypeOutcomeIntent):
        raise GameRuleError('Graveyard exile type outcomes require a typed intent')
    types = commit_graveyard_exile_card_types(host, intent)
    return intent.result.followup(creature_card='creature' in types, land_card='land' in types)

from __future__ import annotations

"""CR 701.4: a fixed quality selected from hand or battlefield."""

from dataclasses import dataclass
from collections.abc import Mapping
from ..creature_subtypes import canonical_creature_subtype
from .casting_additional_costs import AdditionalCostError

BEHOLD_ADDITIONAL_COST_KIND = 'behold'
BEHOLD_ADDITIONAL_COST_CAPABILITY = 'casting.additional_cost.fixed_behold'
BEHOLD_COST_INFORMATION_FIELD = 'behold_casting_cost_information'
LINKED_BEHOLD_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class FixedBeholdAdditionalCost:
    subtype: str
    schema_version: int = 1
    kind: str = BEHOLD_ADDITIONAL_COST_KIND
    linked_exile_pair: str | None = None

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version not in {1, LINKED_BEHOLD_SCHEMA_VERSION}
                or self.kind != BEHOLD_ADDITIONAL_COST_KIND or type(self.subtype) is not str
                or canonical_creature_subtype(self.subtype) != self.subtype):
            raise AdditionalCostError('Behold additional cost requires one canonical creature subtype')
        if self.schema_version == LINKED_BEHOLD_SCHEMA_VERSION:
            from ..linked_exile_return_model import validate_linked_ability_binding

            try:
                validate_linked_ability_binding(self.linked_exile_pair)
            except ValueError as exc:
                raise AdditionalCostError(str(exc)) from exc
        elif self.linked_exile_pair is not None:
            raise AdditionalCostError('A nonmoving Behold cost cannot carry a linked exile pair')

    def to_descriptor(self):
        value = {'schema_version': self.schema_version, 'kind': self.kind, 'subtype': self.subtype}
        if self.linked_exile_pair is not None:
            value['linked_exile_pair'] = self.linked_exile_pair
        return value

    @classmethod
    def from_descriptor(cls, value):
        if not isinstance(value, Mapping):
            raise AdditionalCostError('Behold additional-cost descriptor fields are closed')
        fields = {'schema_version', 'kind', 'subtype'}
        if value.get('schema_version') == LINKED_BEHOLD_SCHEMA_VERSION:
            fields.add('linked_exile_pair')
        if set(value) != fields:
            raise AdditionalCostError('Behold additional-cost descriptor fields are closed')
        return cls(**value)


def fixed_behold_additional_cost(value):
    if not isinstance(value, Mapping):
        raise AdditionalCostError('Additional costs must be objects')
    return FixedBeholdAdditionalCost.from_descriptor(value) if value.get('kind') == BEHOLD_ADDITIONAL_COST_KIND else None


def behold_cost_dependencies():
    from .reveal_additional_cost import REVEAL_ADDITIONAL_COST_CAPABILITY
    return (BEHOLD_ADDITIONAL_COST_CAPABILITY, REVEAL_ADDITIONAL_COST_CAPABILITY)


def linked_behold_from_cost_schema(cost_schema):
    if not isinstance(cost_schema, Mapping):
        return None
    costs = cost_schema.get('additional_costs')
    if not isinstance(costs, (list, tuple)):
        return None
    candidates = [row for row in costs if isinstance(row, Mapping)
                  and row.get('kind') == BEHOLD_ADDITIONAL_COST_KIND
                  and ('linked_exile_pair' in row or row.get('schema_version') == LINKED_BEHOLD_SCHEMA_VERSION)]
    if not candidates:
        return None
    if set(cost_schema) != {'additional_costs'} or len(costs) != 1:
        raise AdditionalCostError('Linked Behold requires one mandatory fixed casting price')
    return FixedBeholdAdditionalCost.from_descriptor(candidates[0])


def linked_behold_cost_dependencies(*, cost_schema):
    if linked_behold_from_cost_schema(cost_schema) is None:
        return ()
    from ..linked_ability_exile_model import LINKED_ABILITY_EXILE_CAPABILITY

    return (*behold_cost_dependencies(), 'casting.additional_cost.zone_change.fixed_exile',
            'zone.change.destination_replacement', LINKED_ABILITY_EXILE_CAPABILITY)


def validate_linked_behold_card_group(abilities):
    from ..linked_exile_return_model import LINKED_EXILE_RETURN_OPERATION, is_linked_behold_hand_return_spec
    from .effect_shape_walkers import nested_effects

    prices, returns = [], []
    for program in abilities:
        face = program.provenance.get('face_id') or 'front'
        cost = linked_behold_from_cost_schema(program.cost_schema)
        if cost is not None:
            if (program.event, program.active_zone, program.destination) != ('resolve', 'stack', 'battlefield'):
                raise ValueError('Linked Behold requires its original permanent spell')
            prices.append((face, cost.linked_exile_pair))
        for row in nested_effects(program.effects):
            spec = row.get('spec')
            if row.get('op') == LINKED_EXILE_RETURN_OPERATION and is_linked_behold_hand_return_spec(spec):
                returns.append((face, row.get('binding_id')))
    if (prices or returns) and (len(prices) != 1 or prices != returns):
        raise ValueError('Linked Behold requires its unique same-face cost and return')

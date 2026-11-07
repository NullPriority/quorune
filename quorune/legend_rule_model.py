from __future__ import annotations

"""Closed current-object exemptions from CR 704.5j."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .creature_subtypes import canonical_creature_subtype
if TYPE_CHECKING:
    from .object_predicate import ObjectQuerySpec


LEGEND_RULE_CAPABILITY = 'state_based.legend_rule.fixed_exemption'
LEGEND_RULE_HANDLER = 'ability.static.legend-rule-exemption.v1'


class LegendRuleError(ValueError):
    """A legend-rule predicate or public grouping snapshot is malformed."""


@dataclass(frozen=True, slots=True)
class LegendRuleExemptionSpec:
    scope: str
    predicate: ObjectQuerySpec

    def __post_init__(self) -> None:
        from .object_predicate import ObjectQuerySpec

        if self.scope not in {'global', 'controller'}:
            raise LegendRuleError('Unsupported legend-rule exemption scope')
        if not isinstance(self.predicate, ObjectQuerySpec):
            raise LegendRuleError('Legend-rule exemptions require a typed public query')
        query = self.predicate
        expected = ObjectQuerySpec(
            zones=('battlefield',), types_all=query.types_all,
            subtypes_all=query.subtypes_all, token=query.token,
        )
        if query != expected or query.types_all not in {(), ('creature',)}:
            raise LegendRuleError('Unsupported legend-rule exemption predicate')
        if type(query.token) not in {type(None), bool}:
            raise LegendRuleError('Legend-rule token predicates must be boolean')
        if len(query.subtypes_all) > 1 or any(
            canonical_creature_subtype(term) != term for term in query.subtypes_all
        ):
            raise LegendRuleError('Legend-rule subtype must use the shared vocabulary')
        if query.subtypes_all and (query.types_all or query.token is not None):
            raise LegendRuleError('Combined subtype exemption predicates are unsupported')
        if self.scope == 'global' and query != ObjectQuerySpec(zones=('battlefield',)):
            raise LegendRuleError('Qualified global legend-rule exemptions are unsupported')

    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': 1, 'scope': self.scope, 'predicate': self.predicate.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LegendRuleExemptionSpec:
        from .object_predicate import ObjectQuerySpec

        if (
            not isinstance(value, Mapping)
            or set(value) != {'schema_version', 'scope', 'predicate'}
            or type(value['schema_version']) is not int
            or value['schema_version'] != 1
        ):
            raise LegendRuleError('Malformed legend-rule exemption descriptor')
        return cls(value['scope'], ObjectQuerySpec.from_dict(value['predicate']))


def legend_rule_payload_dependencies(value: Any) -> tuple[str, ...]:
    """Retain this authority when an existing owner carries a nested fragment."""
    if isinstance(value, LegendRuleExemptionSpec):
        return (LEGEND_RULE_CAPABILITY,)
    if isinstance(value, Mapping):
        if value.get('kind') == 'legend_rule_exemption':
            if set(value) != {'kind', 'value'}:
                raise LegendRuleError('Legend-rule fragments use a closed payload')
            LegendRuleExemptionSpec.from_dict(value['value'])
            return (LEGEND_RULE_CAPABILITY,)
        return tuple(sorted({capability for child in value.values()
            for capability in legend_rule_payload_dependencies(child)}))
    if isinstance(value, (tuple, list)):
        return tuple(sorted({capability for child in value
            for capability in legend_rule_payload_dependencies(child)}))
    return ()

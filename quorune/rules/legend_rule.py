from __future__ import annotations

"""Pure current-object grouping for the CR 704.5j legend rule."""

from collections.abc import Iterable
from dataclasses import dataclass

from ..legend_rule_model import LegendRuleError, LegendRuleExemptionSpec
from ..object_query import ObjectQueryResult, object_matches_query
from ..characteristic_read_frames import characteristic_read_method


@dataclass(frozen=True, slots=True)
class LegendRulePermanentSnapshot:
    card: ObjectQueryResult
    current_name: str
    exemptions: tuple[LegendRuleExemptionSpec, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.card, ObjectQueryResult) or self.card.zone != 'battlefield':
            raise LegendRuleError('Legend-rule snapshot requires a public battlefield object')
        if not self.card.object_id or not self.card.controller or type(self.current_name) is not str:
            raise LegendRuleError('Legend-rule snapshot requires current identity and name')
        if not isinstance(self.exemptions, tuple) or any(
            not isinstance(spec, LegendRuleExemptionSpec) for spec in self.exemptions
        ):
            raise LegendRuleError('Legend-rule source exemptions must be immutable typed values')


@dataclass(frozen=True, slots=True)
class LegendRuleGroup:
    controller: str
    name: str
    object_ids: tuple[str, ...]


def detect_legend_conflicts(
    permanents: Iterable[LegendRulePermanentSnapshot], *, player_order: tuple[str, ...],
) -> tuple[LegendRuleGroup, ...]:
    """Exclude exempt objects before grouping, in the supplied live player order."""
    if (
        not isinstance(player_order, tuple)
        or any(type(seat) is not str or not seat for seat in player_order)
        or len(set(player_order)) != len(player_order)
    ):
        raise LegendRuleError('Legend-rule player order must contain unique live identities')
    values = tuple(permanents)
    if any(not isinstance(value, LegendRulePermanentSnapshot) for value in values):
        raise LegendRuleError('Legend-rule grouping requires typed snapshots')
    if len({value.card.object_id for value in values}) != len(values):
        raise LegendRuleError('Legend-rule grouping received a repeated physical object')
    if any(value.card.controller not in player_order for value in values):
        raise LegendRuleError('Legend-rule snapshot controller is absent from live player order')
    active = tuple(value for value in values if not value.card.phased_out)
    rules = tuple(
        (source.card.controller, spec)
        for source in active for spec in source.exemptions
    )
    groups: dict[tuple[str, str], list[str]] = {}
    for value in active:
        card = value.card
        if not value.current_name or 'legendary' not in card.supertypes:
            continue
        if any((spec.scope == 'global' or card.controller == controller)
               and object_matches_query(card, spec.predicate) for controller, spec in rules):
            continue
        groups.setdefault((card.controller, value.current_name), []).append(card.object_id)
    rank = {seat: index for index, seat in enumerate(player_order)}
    return tuple(
        LegendRuleGroup(controller, name, tuple(sorted(ids)))
        for (controller, name), ids in sorted(groups.items(), key=lambda row: (rank[row[0][0]], row[0][1]))
        if len(ids) > 1
    )


def capture_legend_rule_permanents(host) -> tuple[LegendRulePermanentSnapshot, ...]:
    """Read each live physical permanent through current characteristics."""
    from ..ability_fragments import canonical_ability_fragments
    from ..object_query import object_query_result

    result = []
    seen = set()
    for seat in host.active_seats:
        for object_id in host.state.players[seat].zones['battlefield']:
            card = host.state.cards.get(object_id)
            if card is None:
                raise LegendRuleError('Legend-rule battlefield index contains an unknown object')
            if card.zone != 'battlefield' or card.controller != seat or card.phased_out:
                continue
            if object_id in seen:
                continue
            seen.add(object_id)
            data = host._effective_card_data(card)
            fragments = canonical_ability_fragments(data.get('ability_fragments', ()))
            view = object_query_result(
                card, data, type_parts=host._type_parts(str(data.get('type_line') or '')),
                known_to_actor=True, attached_to_ref=None,
            )
            result.append(LegendRulePermanentSnapshot(
                view, str(data.get('name') or ''),
                tuple(fragment for fragment in fragments if isinstance(fragment, LegendRuleExemptionSpec)),
            ))
    return tuple(result)


@characteristic_read_method
def current_legend_conflicts(host) -> tuple[LegendRuleGroup, ...]:
    return detect_legend_conflicts(
        capture_legend_rule_permanents(host), player_order=tuple(host.apnap_order()),
    )

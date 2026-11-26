from __future__ import annotations

"""Read existing typed quantities and validate a bounded mana allocation."""

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .characteristic_fragments import CharacteristicQuantitySpec, CharacteristicQuantityScope
from .dynamic_characteristics import query_characteristic_count
from .errors import GameRuleError
from .mana import ManaMode
from .public_quantity_mana_model import (
    CHOSEN_DEVOTION_MANA_VERSION, CHOSEN_SUBTYPE_QUANTITY_MANA_VERSION,
    ControlledCreatureThresholdManaAmount, ControlledLandSubtypePairManaAmount,
    PAID_ACTIVATION_MANA_VALUE_CONTEXT, PAID_SACRIFICE_QUANTITY_MANA_VERSION,
    PUBLIC_QUANTITY_MANA_KIND, PublicQuantityManaOutput,
)
from .rules.chosen_type_events import chosen_type_event_value
from .scalar_effect_amounts import resolve_scalar_effect_amount, scalar_source_context
from .scalar_effect_amount_model import PaidSacrificeManaValueSnapshot
from .util import normalize_mana_bundle
from .replacement.immutable import FrozenMap, thaw_value
from .live_battlefield_quantities import current_live_battlefield_count
from .live_battlefield_quantity_model import LiveBattlefieldQuantitySpec


@dataclass(frozen=True, slots=True)
class ManaSourceInformation:
    source: Any
    scalar_context: Mapping[str, Any]
    chosen_creature_type: str | None = None
    paid_mana_value: PaidSacrificeManaValueSnapshot | None = None


def public_quantity_output(ability) -> PublicQuantityManaOutput | None:
    raw = ability.dynamic_mana_output
    return PublicQuantityManaOutput.from_dict(raw) if isinstance(raw, Mapping) and raw.get('kind') == PUBLIC_QUANTITY_MANA_KIND else None


def chosen_subtype_mana_output(ability) -> PublicQuantityManaOutput | None:
    raw = ability.dynamic_mana_output
    if (isinstance(raw, Mapping) and raw.get("kind") == PUBLIC_QUANTITY_MANA_KIND
            and raw.get("schema_version") == CHOSEN_SUBTYPE_QUANTITY_MANA_VERSION):
        return PublicQuantityManaOutput.from_dict(raw)
    return None


def capture_mana_source_information(host, seat, source, ability, *, paid_objects=(), paid_cost_fact=None):
    """Capture only server-owned source information after other costs are paid."""
    output = public_quantity_output(ability)
    if output is None:
        return None
    locked = replace(source, controller=seat, counters=dict(source.counters))
    if output.schema_version == PAID_SACRIFICE_QUANTITY_MANA_VERSION:
        if paid_cost_fact is None or paid_cost_fact[0] != PAID_ACTIVATION_MANA_VALUE_CONTEXT:
            raise GameRuleError("Mana output requires the exact paid sacrifice")
        fact = paid_cost_fact[1]
        if tuple(paid_objects) != (fact["paid_object_id"],):
            raise GameRuleError("Mana output lost the paid object's identity")
        try:
            snapshot = PaidSacrificeManaValueSnapshot.from_dict(fact["snapshot"])
        except (TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        return ManaSourceInformation(locked, FrozenMap(), paid_mana_value=snapshot)
    context = scalar_source_context(host, source, (output.to_dict(),))
    chosen = chosen_type_event_value(source) if output.schema_version == CHOSEN_SUBTYPE_QUANTITY_MANA_VERSION else None
    return ManaSourceInformation(locked, FrozenMap(context), chosen)


def public_quantity_mana_amount(host, seat, source, output, *, source_information=None):
    from types import SimpleNamespace

    info = source_information
    locked = info.source if info is not None else replace(source, controller=seat)
    try:
        if output.schema_version == PAID_SACRIFICE_QUANTITY_MANA_VERSION:
            if info is None or info.paid_mana_value is None:
                raise ValueError("Mana output requires the committed sacrifice's mana value")
            amount = info.paid_mana_value.mana_value
        elif output.schema_version == CHOSEN_SUBTYPE_QUANTITY_MANA_VERSION:
            chosen = info.chosen_creature_type if info is not None else chosen_type_event_value(locked)
            if chosen is None:
                return 0
            query = replace(output.amount.query, subtypes_all=(chosen,))
            amount = query_characteristic_count(host, locked, replace(output.amount, query=query))
        elif isinstance(output.amount, ControlledLandSubtypePairManaAmount):
            amount = output.amount.enhanced_amount if all(
                query_characteristic_count(host, locked, query) for query in output.amount.queries
            ) else 1
        elif isinstance(output.amount, ControlledCreatureThresholdManaAmount):
            count = query_characteristic_count(host, locked, output.amount.query)
            amount = 2 if count >= output.amount.minimum_creatures else 1
        elif isinstance(output.amount, LiveBattlefieldQuantitySpec):
            amount = current_live_battlefield_count(host, seat, output.amount, source_ref=locked.ref)
        elif isinstance(output.amount, CharacteristicQuantitySpec):
            amount = query_characteristic_count(host, locked, output.amount)
        else:
            context = thaw_value(info.scalar_context) if info is not None else {}
            context['source_logical_object_id'] = locked.logical_object_id
            item = SimpleNamespace(controller=seat, source_object_id=locked.object_id,
                card_object_id=None, context=context, targets=[])
            amount = resolve_scalar_effect_amount(host, output.amount.to_dict(), item)
        if type(amount) is not int:
            raise ValueError('Public mana quantity is unavailable')
        return max(0, amount)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc


def public_quantity_mana_offer_amount(host, seat, source, ability, output):
    if ability.source_counter_removal_cost is not None and isinstance(output.amount, CharacteristicQuantitySpec) \
            and output.amount.scope is CharacteristicQuantityScope.SOURCE_COUNTER:
        from .rules.activation.counter_costs import preview_source_counter_removal_cost
        source = preview_source_counter_removal_cost(host, source, ability)
    return public_quantity_mana_amount(host, seat, source, output)


def public_quantity_mana_modes(host, seat, source, ability):
    output = public_quantity_output(ability)
    if output.schema_version in {PAID_SACRIFICE_QUANTITY_MANA_VERSION, CHOSEN_DEVOTION_MANA_VERSION}:
        return ()
    amount = public_quantity_mana_amount(host, seat, source, output)
    # Pure-color allocations are useful payment candidates. Mixed allocations
    # are represented by the closed choice schema, never exponential modes.
    explicit = output.schema_version != CHOSEN_SUBTYPE_QUANTITY_MANA_VERSION and bool(
        output.selection == 'combination' or ability.sacrifice_source
        or ability.source_counter_removal_cost is not None or sum(ability.mana.values())
    )
    colors = output.colors if amount else output.colors[:1]
    return tuple(ManaMode(normalize_mana_bundle({color: amount}), requires_choice=explicit)
        for color in colors)


def select_public_quantity_mana(host, seat, source, ability, response, *, source_information=None):
    output = public_quantity_output(ability)
    if output.schema_version == CHOSEN_DEVOTION_MANA_VERSION:
        from .devotion import current_devotion

        color = response.get("mana_choice")
        if response.get("mana_output") is not None or type(color) is not str or color not in output.colors:
            raise GameRuleError("Choose one offered ordinary color for the devotion mana")
        return normalize_mana_bundle({color: current_devotion(host, seat, (color,))})
    amount = public_quantity_mana_amount(host, seat, source, output, source_information=source_information)
    raw = response.get('mana_output')
    choice = response.get('mana_choice')
    if raw is not None and choice is not None:
        raise GameRuleError('Choose one mana allocation representation')
    if raw is not None:
        if not isinstance(raw, Mapping) or any(type(key) is not str or key not in output.colors
            or type(value) is not int or value < 0 for key, value in raw.items()):
            raise GameRuleError('Mana allocation requires canonical nonnegative integers')
        bundle = normalize_mana_bundle(raw)
    elif choice is not None:
        if type(choice) is not str or choice not in output.colors:
            raise GameRuleError('Mana color is outside this output')
        bundle = normalize_mana_bundle({choice: amount})
    elif output.selection == 'fixed':
        bundle = normalize_mana_bundle({output.colors[0]: amount})
    elif amount == 0:
        bundle = normalize_mana_bundle(None)
    else:
        raise GameRuleError('Choose which mana this ability produces')
    colors = tuple(color for color, value in bundle.items() if value)
    if sum(bundle.values()) != amount or any(color not in output.colors for color in colors):
        raise GameRuleError('Mana allocation does not match the current public quantity')
    if output.selection in {'fixed', 'choose_one'} and len(colors) > 1:
        raise GameRuleError('This output requires one mana color')
    return bundle


def public_quantity_mana_choice_schema(host, seat, source, ability):
    output = public_quantity_output(ability)
    if output is None or output.selection == 'fixed':
        return None
    if output.schema_version == CHOSEN_DEVOTION_MANA_VERSION:
        labels = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
        return {"mana_choice": {"type": "enum", "label": "Mana color",
            "legal_values": list(labels), "option_labels": labels}}
    return {'mana_output': {'type': 'mana_bundle', 'label': 'Mana to add',
        'allowed_colors': list(output.colors), 'total': public_quantity_mana_offer_amount(host, seat, source, ability, output),
        'single_color': output.selection == 'choose_one'}}

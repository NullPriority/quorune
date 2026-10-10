from __future__ import annotations

"""Read existing typed quantities and validate a bounded mana allocation."""

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .characteristic_fragments import CharacteristicQuantitySpec
from .dynamic_characteristics import query_characteristic_count
from .errors import GameRuleError
from .mana import ManaMode
from .public_quantity_mana_model import PublicQuantityManaOutput
from .scalar_effect_amounts import resolve_scalar_effect_amount, scalar_source_context
from .util import normalize_mana_bundle
from .replacement.immutable import FrozenMap, thaw_value


@dataclass(frozen=True, slots=True)
class ManaSourceInformation:
    source: Any
    scalar_context: Mapping[str, Any]


def public_quantity_output(ability) -> PublicQuantityManaOutput | None:
    raw = ability.dynamic_mana_output
    return PublicQuantityManaOutput.from_dict(raw) if isinstance(raw, Mapping) else None


def capture_mana_source_information(host, seat, source, ability):
    """Capture only server-owned source information after other costs are paid."""
    output = public_quantity_output(ability)
    if output is None:
        return None
    locked = replace(source, controller=seat, counters=dict(source.counters))
    context = scalar_source_context(host, source, (output.to_dict(),))
    return ManaSourceInformation(locked, FrozenMap(context))


def public_quantity_mana_amount(host, seat, source, output, *, source_information=None):
    from types import SimpleNamespace

    info = source_information
    locked = info.source if info is not None else replace(source, controller=seat)
    try:
        if isinstance(output.amount, CharacteristicQuantitySpec):
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


def public_quantity_mana_modes(host, seat, source, ability):
    output = public_quantity_output(ability)
    amount = public_quantity_mana_amount(host, seat, source, output)
    # Pure-color allocations are useful payment candidates. Mixed allocations
    # are represented by the closed choice schema, never exponential modes.
    explicit = bool(output.selection == 'combination' or ability.sacrifice_source
        or ability.source_counter_removal_cost is not None or sum(ability.mana.values()))
    colors = output.colors if amount else output.colors[:1]
    return tuple(ManaMode(normalize_mana_bundle({color: amount}), requires_choice=explicit)
        for color in colors)


def select_public_quantity_mana(host, seat, source, ability, response, *, source_information=None):
    output = public_quantity_output(ability)
    amount = public_quantity_mana_amount(host, seat, source, output, source_information=source_information)
    raw = response.get('mana_output')
    choice = response.get('mana_choice')
    if raw is not None and choice is not None:
        raise GameRuleError('Choose one mana allocation representation')
    if raw is not None:
        if not isinstance(raw, Mapping) or any(type(key) is not str or key not in 'WUBRGC' or len(key) != 1
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
    return {'mana_output': {'type': 'mana_bundle', 'label': 'Mana to add',
        'allowed_colors': list(output.colors), 'total': public_quantity_mana_amount(host, seat, source, output),
        'single_color': output.selection == 'choose_one'}}

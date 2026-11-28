from __future__ import annotations

"""Read current or server-captured chosen colors without interpreting prose."""

from dataclasses import dataclass
from typing import Mapping

from .chosen_color_mana_model import CHOSEN_COLOR_MANA_KIND, NOTED_MANA_OUTPUT_VERSION, ChosenColorManaOutput
from .rules.noted_mana import current_noted_mana_type
from .entry_designations import EntryDesignationKind, validate_designation
from .errors import GameRuleError
from .mana import ManaMode
from .util import normalize_mana_bundle


@dataclass(frozen=True, slots=True)
class ChosenColorManaSourceInformation:
    logical_object_id: str
    ability_id: str
    color: str | None


def chosen_color_mana_output(ability):
    raw = ability.dynamic_mana_output
    return ChosenColorManaOutput.from_dict(raw) if isinstance(raw, Mapping) and raw.get('kind') == CHOSEN_COLOR_MANA_KIND else None


def _source_color(source):
    value = source.annotations.get('chosen_color')
    try:
        return validate_designation(EntryDesignationKind.COLOR, value)
    except ValueError:
        return None


def capture_chosen_color_mana_source(source, ability):
    output = chosen_color_mana_output(ability)
    if output is None:
        return None
    color = current_noted_mana_type(source) if output.schema_version == NOTED_MANA_OUTPUT_VERSION else _source_color(source)
    return ChosenColorManaSourceInformation(source.logical_object_id, ability.ability_id, color)


def chosen_color_mana_modes(source, ability, *, source_information=None):
    output = chosen_color_mana_output(ability)
    if output is None:
        return ()
    if source_information is not None:
        if not isinstance(source_information, ChosenColorManaSourceInformation) or source_information.ability_id != ability.ability_id:
            raise GameRuleError('Chosen-color mana source information is not bound to this ability')
        if output.schema_version == NOTED_MANA_OUTPUT_VERSION and source_information.logical_object_id != source.logical_object_id:
            raise GameRuleError("Noted mana belongs to the original source incarnation")
        color = source_information.color
    else:
        color = current_noted_mana_type(source) if output.schema_version == NOTED_MANA_OUTPUT_VERSION else _source_color(source)
    if output.schema_version == NOTED_MANA_OUTPUT_VERSION:
        return (ManaMode(normalize_mana_bundle({color: 1} if color is not None else None)),)
    bundles = [] if output.fixed_alternative is None else [output.fixed_alternative.bundle]
    if color is not None:
        bundles.append(normalize_mana_bundle({color: output.amount}))
    explicit = bool(ability.sacrifice_source or ability.choices or ability.source_counter_removal_cost is not None or sum(ability.mana.values()))
    unique = {tuple(bundle[key] for key in 'WUBRGC'): bundle for bundle in bundles}
    return tuple(ManaMode(bundle, requires_choice=explicit) for bundle in unique.values())

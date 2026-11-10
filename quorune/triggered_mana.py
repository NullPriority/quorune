from __future__ import annotations

"""Discover and apply mana triggers at the production boundary."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .ability_fragments import static_component_keys
from .attachments import attached_object_identity
from .errors import GameRuleError
from .fixed_mana_abilities import FixedManaMode
from .triggered_mana_model import (
    CHOSEN_TRIGGERED_MANA_CAPABILITY,
    FIXED_TRIGGERED_MANA_EVENT,
    MANA_LAND_SUBTYPES,
    TRIGGERED_MANA_OPTION_FIELD,
    FixedTriggeredManaSpec,
    TriggeredManaChoice,
    TriggeredManaOption,
    fixed_triggered_mana_spec,
)
from .util import normalize_mana_bundle


@dataclass(frozen=True, slots=True)
class TapManaSource:
    object_id: str
    logical_object_id: str
    ref: str
    types: frozenset[str]
    subtypes: frozenset[str]
    supertypes: frozenset[str]


@dataclass(frozen=True, slots=True)
class TriggeredManaOutput:
    provider_id: str
    provider_ref: str
    bundle: FixedManaMode
    snow: bool
    modifier_id: str | None = None
    needs_choice: bool = False

    def survives(self, departed: frozenset[str]) -> bool:
        return self.provider_id not in departed and self.modifier_id not in departed


@dataclass(frozen=True, slots=True)
class _ManaProvider:
    controller: str
    spec: FixedTriggeredManaSpec
    provider_id: str
    provider_ref: str
    snow: bool
    modifier_id: str | None
    attachment: tuple[str, str] | None

    def options(self, base: Mapping[str, int]) -> tuple[TriggeredManaOutput, ...]:
        result = self.spec.output
        options = result.options(base) if isinstance(result, TriggeredManaChoice) else (result,)
        return tuple(TriggeredManaOutput(self.provider_id, self.provider_ref, output,
                                        self.snow, self.modifier_id, len(options) > 1) for output in options)


def tap_mana_source(host: Any, card: Any, *, tap_source: bool) -> TapManaSource | None:
    if (not tap_source or card.zone != "battlefield" or card.phased_out
            or not host.semantics.programs_for_event(FIXED_TRIGGERED_MANA_EVENT)):
        return None
    types, subtypes, supertypes = host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))
    return TapManaSource(card.object_id, card.logical_object_id, card.ref,
                         frozenset(types), frozenset(subtypes), frozenset(supertypes))


def fixed_mana_providers(host: Any) -> tuple[_ManaProvider, ...]:
    from .trigger_discovery import applicable_trigger_multipliers

    programs = {
        program.key: fixed_triggered_mana_spec(program)
        for program in host.semantics.programs_for_event(FIXED_TRIGGERED_MANA_EVENT)
        if host.semantic_program_is_current_trusted(program)
    }
    if not programs:
        return ()
    result = []
    sources = sorted(host._semantic_event_sources(zones={"battlefield"}),
                     key=lambda card: (card.zone_timestamp, card.object_id))
    for source in sources:
        if source.phased_out:
            continue
        data = host._effective_card_data(source)
        specs = tuple(programs[key] for key in static_component_keys(data.get("ability_fragments", ())) if key in programs)
        if not specs:
            continue
        multipliers = applicable_trigger_multipliers(host, source=source, controller=source.controller,
            event=FIXED_TRIGGERED_MANA_EVENT, context={}, participation_sources=sources, ability_characteristics=data)
        snow = "snow" in host._type_parts(str(data.get("type_line") or ""))[2]
        for spec in specs:
            if spec.requires_monarch and host.state.monarch != source.controller:
                continue
            identity = attached_object_identity(host.state.cards, source) if spec.scope == "attached" else None
            if spec.scope == "attached" and identity is None:
                continue
            attachment = (identity.object_id, identity.logical_object_id) if identity is not None else None
            for modifier in (None, *(entry.source_object_id for entry in multipliers)):
                result.append(_ManaProvider(source.controller, spec, source.object_id, source.ref, snow, modifier, attachment))
    return tuple(result)


def _subject_matches(spec: FixedTriggeredManaSpec, source: TapManaSource) -> bool:
    if spec.basic_only and "basic" not in source.supertypes:
        return False
    if spec.subtypes_any and source.subtypes.isdisjoint(spec.subtypes_any):
        return False
    if spec.subject in MANA_LAND_SUBTYPES:
        return "land" in source.types and spec.subject in source.subtypes
    return spec.subject == "permanent" or spec.subject in source.types


def _matching_outputs(providers: Sequence[_ManaProvider], source: TapManaSource | None,
                      actor: str, bundle: Mapping[str, int], *, source_departs: bool = False) -> tuple[tuple[TriggeredManaOutput, ...], ...]:
    if source is None or not any(bundle.values()):
        return ()
    return tuple(provider.options(bundle) for provider in providers
        if (provider.spec.scope != "controller" or provider.controller == actor)
        and (provider.attachment is None or provider.attachment == (source.object_id, source.logical_object_id))
        and (not source_departs or (source.object_id not in (provider.provider_id, provider.modifier_id) and provider.attachment is None))
        and (provider.spec.produced_color is None or bundle.get(provider.spec.produced_color, 0) > 0)
        and _subject_matches(provider.spec, source))


def _funding_signature(outputs: tuple[TriggeredManaOutput, ...], departures: frozenset[str]) -> tuple:
    lots = {}
    for output in outputs:
        dependent = tuple(sorted(departures.intersection((output.provider_id, output.modifier_id))))
        key = (output.snow, dependent)
        pool = lots.setdefault(key, normalize_mana_bundle(None))
        for color, amount in output.bundle.bundle.items():
            pool[color] += amount
    return tuple((key, tuple(pool.values())) for key, pool in sorted(lots.items()))


def _output_variants(rows: tuple[tuple[TriggeredManaOutput, ...], ...], departures: frozenset[str]) -> tuple:
    if all(len(options) == 1 for options in rows):
        return (tuple(options[0] for options in rows),)
    variants = ((),)
    for options in rows:
        combined = {}
        for prefix in variants:
            for output in options:
                result = (*prefix, output)
                combined.setdefault(_funding_signature(result, departures), result)
        variants = tuple(combined.values())
    return variants


def modes_with_triggered_mana(host: Any, actor: str, card: Any, modes: tuple, providers: Sequence[_ManaProvider],
                              *, departures: frozenset[str] = frozenset()) -> tuple:
    if not providers:
        return modes
    source = tap_mana_source(host, card, tap_source=any(mode.tap_source for mode in modes))
    result = []
    for mode in modes:
        if not mode.tap_source:
            result.append(mode)
            continue
        rows = _matching_outputs(providers, source, actor, mode.bundle, source_departs=mode.sacrifices_source)
        result.extend(replace(mode, triggered_mana=outputs) for outputs in _output_variants(rows, departures))
    return tuple(result)


def triggered_mana_selection(base: Mapping[str, int], outputs: Sequence[TriggeredManaOutput], *, always: bool = False) -> str | None:
    choices = tuple(output.bundle for output in outputs if output.needs_choice)
    return TriggeredManaOption(FixedManaMode.from_bundle(base), choices).encode() if choices or always else None


def _decode_selection(value: Any) -> TriggeredManaOption:
    try:
        return TriggeredManaOption.decode(value)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc


def triggered_mana_base_response(response: Mapping[str, Any]) -> Mapping[str, Any]:
    selection = response.get(TRIGGERED_MANA_OPTION_FIELD)
    if selection is None:
        return response
    base = _decode_selection(selection).base.bundle
    supplied = response.get("mana_output")
    if response.get("mana_choice") or (supplied is not None and (
        not isinstance(supplied, Mapping) or normalize_mana_bundle(supplied) != base
    )):
        raise GameRuleError("The selected triggered mana option already binds its base output")
    return {**response, "mana_output": base}


def triggered_mana_offer_schema(host: Any, actor: str, source: Any, ability: Any) -> dict[str, Any] | None:
    if not ability.tap_source or not any(CHOSEN_TRIGGERED_MANA_CAPABILITY in program.capability_dependencies
            for program in host.semantics.programs_for_event(FIXED_TRIGGERED_MANA_EVENT)):
        return None
    modes = modes_with_triggered_mana(host, actor, source, host._mana_modes_for_ability(actor, source, ability), fixed_mana_providers(host))
    if not any(output.needs_choice for mode in modes for output in mode.triggered_mana):
        return None
    labels = {}
    for mode in modes:
        if not any(mode.bundle.values()):
            continue
        token = triggered_mana_selection(mode.bundle, mode.triggered_mana, always=True)
        labels[token] = triggered_mana_mode_label(mode)
    return {"label": "Choose mana output", "legal_values": list(labels), "option_labels": labels}


def _mana_symbols(bundle: Mapping[str, int]) -> str:
    return "".join(f"{{{color}}}" * count for color, count in bundle.items())


def triggered_mana_mode_label(mode) -> str:
    label = "Add " + _mana_symbols(mode.bundle)
    for output in mode.triggered_mana:
        label += f", then {_mana_symbols(output.bundle.bundle)} from {output.provider_ref}"
        if output.snow:
            label += " (snow source)"
    return label


def _selected_outputs(rows, bundle, raw_selection):
    choices = ()
    if raw_selection is not None:
        option = _decode_selection(raw_selection)
        if option.base.bundle != normalize_mana_bundle(bundle):
            raise GameRuleError("The triggered mana option does not match the actual base output")
        choices = option.choices
    result = []
    index = 0
    for options in rows:
        if len(options) == 1:
            result.append(options[0])
            continue
        if index >= len(choices):
            raise GameRuleError("Choose an output for each triggered mana ability")
        chosen = next((output for output in options if output.bundle == choices[index]), None)
        if chosen is None:
            raise GameRuleError("The selected triggered mana output is unavailable")
        result.append(chosen)
        index += 1
    if index != len(choices):
        raise GameRuleError("The triggered mana selection includes extra outputs")
    return result


def commit_triggered_mana(host: Any, actor: str, source: TapManaSource | None, bundle: Mapping[str, int], *, selection=None) -> bool:
    if source is None or not any(bundle.values()):
        if selection is not None:
            raise GameRuleError("Triggered mana options require positive own-tap production")
        return False
    outputs = _selected_outputs(_matching_outputs(fixed_mana_providers(host), source, actor, bundle), bundle, selection)
    for output in outputs:
        mana = output.bundle.bundle
        host._add_mana_to_pool(actor, mana, snow_source=output.snow)
        host._log(actor, "mana.triggered",
                  f"{output.provider_ref} added mana for {actor} after {source.ref} produced mana.",
                  {"source": output.provider_ref, "producer": source.ref,
                   "bundle": {color: amount for color, amount in mana.items() if amount}},
                  importance=0, changed_players=[actor])
    return bool(outputs)

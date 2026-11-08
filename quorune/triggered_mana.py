from __future__ import annotations

"""Discover and apply fixed mana triggers at the production boundary."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .ability_fragments import static_component_keys
from .attachments import attached_object_identity
from .fixed_mana_abilities import FixedManaMode
from .triggered_mana_model import (
    FIXED_TRIGGERED_MANA_EVENT,
    MANA_LAND_SUBTYPES,
    FixedTriggeredManaSpec,
    fixed_triggered_mana_spec,
)


@dataclass(frozen=True, slots=True)
class TapManaSource:
    object_id: str
    logical_object_id: str
    ref: str
    types: frozenset[str]
    subtypes: frozenset[str]


@dataclass(frozen=True, slots=True)
class TriggeredManaOutput:
    provider_id: str
    provider_ref: str
    bundle: FixedManaMode
    snow: bool
    modifier_id: str | None = None

    def survives(self, departed: frozenset[str]) -> bool:
        return self.provider_id not in departed and self.modifier_id not in departed


@dataclass(frozen=True, slots=True)
class _ManaProvider:
    controller: str
    spec: FixedTriggeredManaSpec
    result: TriggeredManaOutput
    attachment: tuple[str, str] | None


def tap_mana_source(host: Any, card: Any, *, tap_source: bool) -> TapManaSource | None:
    if (not tap_source or card.zone != "battlefield" or card.phased_out
            or not host.semantics.programs_for_event(FIXED_TRIGGERED_MANA_EVENT)):
        return None
    types, subtypes, _ = host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))
    return TapManaSource(card.object_id, card.logical_object_id, card.ref, frozenset(types), frozenset(subtypes))


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
            identity = attached_object_identity(host.state.cards, source) if spec.scope == "attached" else None
            if spec.scope == "attached" and identity is None:
                continue
            attachment = (identity.object_id, identity.logical_object_id) if identity is not None else None
            for modifier in (None, *(entry.source_object_id for entry in multipliers)):
                result.append(_ManaProvider(source.controller, spec,
                    TriggeredManaOutput(source.object_id, source.ref, spec.output, snow, modifier), attachment))
    return tuple(result)


def _subject_matches(spec: FixedTriggeredManaSpec, source: TapManaSource) -> bool:
    if spec.subject in MANA_LAND_SUBTYPES:
        return "land" in source.types and spec.subject in source.subtypes
    return spec.subject == "permanent" or spec.subject in source.types


def _matching_outputs(providers: Sequence[_ManaProvider], source: TapManaSource | None,
                      actor: str, bundle: Mapping[str, int], *, source_departs: bool = False) -> tuple[TriggeredManaOutput, ...]:
    if source is None or not any(bundle.values()):
        return ()
    departed = frozenset({source.object_id}) if source_departs else frozenset()
    return tuple(provider.result for provider in providers
        if (provider.spec.scope != "controller" or provider.controller == actor)
        and (provider.attachment is None or provider.attachment == (source.object_id, source.logical_object_id))
        and provider.result.survives(departed) and (not source_departs or provider.attachment is None)
        and (provider.spec.produced_color is None or bundle.get(provider.spec.produced_color, 0) > 0)
        and _subject_matches(provider.spec, source))


def modes_with_triggered_mana(host: Any, actor: str, card: Any, modes: tuple, providers: Sequence[_ManaProvider]) -> tuple:
    if not providers:
        return modes
    source = tap_mana_source(host, card, tap_source=any(mode.tap_source for mode in modes))
    return tuple(replace(mode, triggered_mana=_matching_outputs(
        providers, source, actor, mode.bundle, source_departs=mode.sacrifices_source,
    )) if mode.tap_source else mode for mode in modes)


def commit_triggered_mana(host: Any, actor: str, source: TapManaSource | None, bundle: Mapping[str, int]) -> bool:
    if source is None or not any(bundle.values()):
        return False
    outputs = _matching_outputs(fixed_mana_providers(host), source, actor, bundle)
    for output in outputs:
        mana = output.bundle.bundle
        host._add_mana_to_pool(actor, mana, snow_source=output.snow)
        host._log(actor, "mana.triggered",
                  f"{output.provider_ref} added mana for {actor} after {source.ref} produced mana.",
                  {"source": output.provider_ref, "producer": source.ref,
                   "bundle": {color: amount for color, amount in mana.items() if amount}},
                  importance=0, changed_players=[actor])
    return bool(outputs)

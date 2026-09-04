from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..bloodthirst import BLOODTHIRST_MECHANIC, BloodthirstSpec
from ..cast_timing import PRINTED_FLASH_MECHANIC
from ..semantic_runtime.cast_costs import (
    convoke_handler_descriptor,
)
from ..death_return import (
    DeathReturnSpec,
    PERSIST_KEYWORD,
    UNDYING_KEYWORD,
)
from ..evolve import EVOLVE_EVENT_CONDITION_FIELD
from ..echo import ECHO_MECHANIC_ID
from ..fixed_keyword_entry_counters import FIXED_KEYWORD_ENTRY_MECHANICS
from ..unleash import (
    UNLEASH_MECHANIC,
    unleash_block_handler_descriptor,
    unleash_entry_handler_descriptor,
)
from ..riot import RIOT_MECHANIC, riot_entry_handler_descriptor
from ..read_ahead import (
    READ_AHEAD_CAPABILITIES,
    READ_AHEAD_MECHANIC_ID,
    read_ahead_entry_handler_descriptor,
)
from ..semantic_runtime.sunburst import SUNBURST_MECHANIC_ID, SunburstSpec
from ..renown import RENOWN_MECHANIC_ID, RenownSpec
from ..modular import MODULAR_MECHANIC_ID, ModularSpec
from ..morph import (
    compile_fixed_mana_face_down_method,
    FACE_DOWN_CAST_METHODS,
    FACE_DOWN_METHOD_CAPABILITY_IDS,
    FACE_DOWN_METHOD_RUNTIME_EVENTS,
    face_down_method_handler_descriptor,
)
from .cumulative_upkeep_nodes import (
    fixed_life_cumulative_upkeep_node,
    fixed_mana_cumulative_upkeep_node,
)
from .saga_chapter_nodes import ordinary_saga_chapter_nodes
from .commander_pairing_nodes import commander_pairing_keyword_node
from .cascade_nodes import CASCADE_MECHANIC_ID, cascade_keyword_node
from .storm_nodes import STORM_MECHANIC_ID, storm_keyword_node
from .bestow_nodes import fixed_mana_bestow_keyword_node
from .flashback_nodes import ordinary_fixed_mana_flashback_keyword_node
from .cast_lifecycle_nodes import fixed_cast_lifecycle_keyword_node
from .echo_nodes import fixed_mana_echo_node
from .crew_nodes import ordinary_crew_keyword_node
from .station_nodes import ordinary_station_keyword_node
from .unearth_nodes import ordinary_unearth_keyword_node
from .kicker_nodes import fixed_mana_kicker_keyword_node
from .cycling_nodes import (
    ordinary_cycling_keyword_node,
    typecycling_keyword_node,
)
from .casting_payment_keyword_nodes import (
    fixed_mana_evoke_keyword_node,
    ordinary_delve_keyword_node,
    ordinary_improvise_keyword_node,
    typed_affinity_keyword_node,
)
from .counter_keyword_activation_nodes import (
    fixed_counter_keyword_activation_node,
)
from .ability_keyword_fragments import lower_ability_keyword_fragments
from .characteristic_definition_nodes import (
    characteristic_definition_keyword_node,
)
from .dependency_gate import (
    DependencyGate,
    explicit_capability_gate,
    explicit_capabilities_gate,
    keyword_dependency_gate,
)
from .day_night_nodes import day_night_keyword_node
from .devoid_characteristics import DEVOID_MECHANIC_ID
from .ir_model import (
    OracleNode,
    OracleResidual,
    SourceSpan,
    append_residual,
)
from ..rules.capabilities import CapabilityRegistry


_DREDGE_MECHANIC = "dredge"
_EVOLVE_MECHANIC = "evolve"
_FABRICATE_MECHANIC = "fabricate"
_PERSIST_MECHANIC = PERSIST_KEYWORD
_UNDYING_MECHANIC = UNDYING_KEYWORD
_UNLEASH_MECHANIC = UNLEASH_MECHANIC
_RIOT_MECHANIC = RIOT_MECHANIC
_MENTOR_MECHANIC = "mentor"
_CASCADE_MECHANIC = CASCADE_MECHANIC_ID
_STORM_MECHANIC = STORM_MECHANIC_ID
_PROWESS_MECHANIC = "prowess"
_CONVOKE_MECHANIC = "convoke"
_AFFINITY_MECHANIC = "affinity"
_DELVE_MECHANIC = "delve"
_EVOKE_MECHANIC = "evoke"
_IMPROVISE_MECHANIC = "improvise"
_BLOODTHIRST_MECHANIC = BLOODTHIRST_MECHANIC
_SUNBURST_MECHANIC = SUNBURST_MECHANIC_ID
_RENOWN_MECHANIC = RENOWN_MECHANIC_ID
_MODULAR_MECHANIC = MODULAR_MECHANIC_ID
_ECHO_MECHANIC = ECHO_MECHANIC_ID
_DEVOID_MECHANIC = DEVOID_MECHANIC_ID
_FACE_DOWN_METHOD_MECHANICS = frozenset(FACE_DOWN_CAST_METHODS)
_TOXIC_MECHANIC = "toxic"
_GROUPED_SPLIT_MECHANICS = (
    _AFFINITY_MECHANIC,
    _BLOODTHIRST_MECHANIC,
    _SUNBURST_MECHANIC,
    _TOXIC_MECHANIC,
    _EVOLVE_MECHANIC,
    *tuple(sorted(FIXED_KEYWORD_ENTRY_MECHANICS)),
)
_PARAMETERIZED_SPLIT_MECHANICS = frozenset(
    {
        _BLOODTHIRST_MECHANIC,
        _SUNBURST_MECHANIC,
        _RENOWN_MECHANIC,
        _MODULAR_MECHANIC,
        _ECHO_MECHANIC,
        _TOXIC_MECHANIC,
        _AFFINITY_MECHANIC,
        _EVOKE_MECHANIC,
        *FIXED_KEYWORD_ENTRY_MECHANICS,
    }
)
_INSTANCE_PART_MECHANICS = (
    _AFFINITY_MECHANIC,
    _DELVE_MECHANIC,
    _EVOKE_MECHANIC,
    _IMPROVISE_MECHANIC,
    _BLOODTHIRST_MECHANIC,
    _SUNBURST_MECHANIC,
    _EVOLVE_MECHANIC,
    _PERSIST_MECHANIC,
    _RIOT_MECHANIC,
    _UNDYING_MECHANIC,
    _UNLEASH_MECHANIC,
    _MENTOR_MECHANIC,
    _CASCADE_MECHANIC,
    _STORM_MECHANIC,
    _PROWESS_MECHANIC,
    _RENOWN_MECHANIC,
    _MODULAR_MECHANIC,
    _ECHO_MECHANIC,
    _TOXIC_MECHANIC,
    _CONVOKE_MECHANIC,
    _DEVOID_MECHANIC,
    *tuple(sorted(FIXED_KEYWORD_ENTRY_MECHANICS)),
)
_SPLIT_MECHANICS = frozenset(
    {
        PRINTED_FLASH_MECHANIC,
        _FABRICATE_MECHANIC,
        *_INSTANCE_PART_MECHANICS,
    }
)


@dataclass(frozen=True, slots=True)
class KeywordNodePlan:
    """One source-spanned keyword fragment compiled as an independent node."""

    node_id: str
    line: str
    material_line: str
    span: SourceSpan
    mechanics: tuple[str, ...]


def keyword_node_plans(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
) -> tuple[KeywordNodePlan, ...]:
    """Split independently executable keyword instances deterministically."""

    split_mechanics = tuple(
        mechanic
        for mechanic in (
            PRINTED_FLASH_MECHANIC,
            _FABRICATE_MECHANIC,
            _CONVOKE_MECHANIC,
            _DELVE_MECHANIC,
            _EVOKE_MECHANIC,
            _IMPROVISE_MECHANIC,
        )
        if mechanic in mechanics
    ) + tuple(
        grouped
        for grouped in _GROUPED_SPLIT_MECHANICS
        for mechanic in mechanics
        if mechanic == grouped
    ) + tuple(
        mechanic
        for mechanic in mechanics
        if mechanic
        in {
            _DEVOID_MECHANIC,
            _PERSIST_MECHANIC,
            _RIOT_MECHANIC,
            _UNDYING_MECHANIC,
            _UNLEASH_MECHANIC,
            _MENTOR_MECHANIC,
            _CASCADE_MECHANIC,
            _STORM_MECHANIC,
            _PROWESS_MECHANIC,
            _RENOWN_MECHANIC,
            _MODULAR_MECHANIC,
            _ECHO_MECHANIC,
        }
    )
    if not split_mechanics:
        return (
            KeywordNodePlan(node_id, line, material_line, span, mechanics),
        )

    occurrence: dict[str, int] = {}
    instance_parts = {
        mechanic: tuple(
            (
                match.group().strip().rstrip("."),
                match.start()
                + len(match.group())
                - len(match.group().lstrip()),
                match.end()
                - len(match.group())
                + len(match.group().rstrip()),
            )
            for match in re.finditer(r"[^,]+", material_line)
            if (
                match.group().strip().rstrip(".").casefold() == mechanic
                or (
                    mechanic in _PARAMETERIZED_SPLIT_MECHANICS
                    and re.fullmatch(
                        rf"{re.escape(mechanic)}\s+.+",
                        match.group().strip().rstrip("."),
                        re.IGNORECASE,
                    )
                )
            )
        )
        for mechanic in _INSTANCE_PART_MECHANICS
    }
    result: list[KeywordNodePlan] = []
    for mechanic in split_mechanics:
        occurrence[mechanic] = occurrence.get(mechanic, 0) + 1
        suffix = (
            f"{mechanic}:{occurrence[mechanic]}"
            if mechanics.count(mechanic) > 1
            else mechanic
        )
        selected_line = line
        selected_material_line = material_line
        selected_span = span
        parts = instance_parts.get(mechanic, ())
        if occurrence[mechanic] <= len(parts):
            fragment, start, end = parts[occurrence[mechanic] - 1]
            selected_line = fragment
            selected_material_line = fragment
            selected_span = SourceSpan(
                start=span.start + start,
                end=span.start + end,
                line=span.line,
            )
        result.append(
            KeywordNodePlan(
                node_id=f"{node_id}:{suffix}",
                line=selected_line,
                material_line=selected_material_line,
                span=selected_span,
                mechanics=(mechanic,),
            )
        )
    remaining = tuple(
        mechanic
        for mechanic in mechanics
        if mechanic not in _SPLIT_MECHANICS
    )
    if remaining:
        result.append(
            KeywordNodePlan(node_id, line, material_line, span, remaining)
        )
    return tuple(result)


def closed_special_keyword_node(
    *,
    record: Any,
    face_id: str,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    printed_power: str | None,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    """Lower closed keyword families that own their complete node shape."""

    values = {
        "node_id": node_id,
        "line": line,
        "material_line": material_line,
        "span": span,
        "mechanics": mechanics,
        "capability_registry": capability_registry,
        "capability_profile": capability_profile,
        "residuals": residuals,
    }
    day_night = day_night_keyword_node(
        record=record, face_id=face_id, **values
    )
    if day_night is not None:
        return day_night
    characteristic_definition = characteristic_definition_keyword_node(
        **values,
    )
    if characteristic_definition is not None:
        return characteristic_definition
    commander_pairing = commander_pairing_keyword_node(**values)
    if commander_pairing is not None:
        return commander_pairing
    bestow = fixed_mana_bestow_keyword_node(**values)
    if bestow is not None:
        return bestow
    flashback = ordinary_fixed_mana_flashback_keyword_node(**values)
    if flashback is not None:
        return flashback
    cast_lifecycle = fixed_cast_lifecycle_keyword_node(**values)
    if cast_lifecycle is not None:
        return cast_lifecycle
    counter_activation = fixed_counter_keyword_activation_node(
        **values,
        printed_power=printed_power,
    )
    if counter_activation is not None:
        return counter_activation
    morph = fixed_mana_morph_keyword_node(**values)
    if morph is not None:
        return morph
    unearth = ordinary_unearth_keyword_node(**values)
    if unearth is not None:
        return unearth
    kicker = fixed_mana_kicker_keyword_node(**values)
    if kicker is not None:
        return kicker
    renown = renown_keyword_node(
        **values,
        trusted_mechanics=trusted_mechanics,
    )
    if renown is not None:
        return renown
    for lower in (
        fixed_mana_evoke_keyword_node,
        cascade_keyword_node,
        storm_keyword_node,
        ordinary_convoke_keyword_node,
        ordinary_delve_keyword_node,
        ordinary_improvise_keyword_node,
        ordinary_affinity_keyword_node,
        ordinary_crew_keyword_node,
        ordinary_station_keyword_node,
        typecycling_keyword_node,
        ordinary_cycling_keyword_node,
        fixed_mana_cumulative_upkeep_node,
        fixed_life_cumulative_upkeep_node,
        fixed_mana_echo_node,
    ):
        node = lower(**values)
        if node is not None:
            return node
    return None


def fixed_mana_morph_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    """Lower one ordinary fixed-mana face-down method or retain its blocker."""

    if len(mechanics) != 1 or mechanics[0] not in _FACE_DOWN_METHOD_MECHANICS:
        return None
    method = mechanics[0]
    spec = compile_fixed_mana_face_down_method(material_line)
    ordinary = spec is not None and spec.method == method
    gate = explicit_capability_gate(
        FACE_DOWN_METHOD_CAPABILITY_IDS[method],
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    blockers = gate.blockers if ordinary else ("mechanic:morph-unsupported-cost",)
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract" if ordinary else "keyword_grammar",
                text=line,
                span=span,
                reason=(
                    f"{method.title()} depends on a blocked typed fixed-mana contract"
                    if ordinary
                    else (
                        f"{method.title()} cost is outside the fixed "
                        "ordinary-mana grammar"
                    )
                ),
                blockers=blockers,
            ),
        )
        if blockers
        else ()
    )
    return OracleNode(
        node_id=node_id,
        kind="keyword_ability",
        text=line,
        span=span,
        active_zone="all",
        event=FACE_DOWN_METHOD_RUNTIME_EVENTS[method],
        lowerable=ordinary,
        exact=ordinary and not residual_ids,
        template_id=(
            f"{method}-fixed-mana-face-down-special-action-v1"
            if ordinary
            else None
        ),
        handlers=(
            (face_down_method_handler_descriptor(spec),)
            if ordinary and spec is not None
            else ()
        ),
        runtime_coverage=(
            "face_down_cast",
            "face_down_characteristics",
            "turn_face_up_special_action",
            *(
                ("face_down_ward",)
                if method == "disguise"
                else ("turn_face_up_counter_placement",)
                if method == "megamorph"
                else ()
            ),
        )
        if ordinary
        else (),
        mechanics=mechanics,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities if ordinary else (),
        capability_closure=(
            gate.closure.reachable
            if ordinary and gate.closure is not None
            else ()
        ),
        capability_profile=(
            gate.closure.profile
            if ordinary and gate.closure is not None
            else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint
            if ordinary and gate.closure is not None
            else None
        ),
    )


def ordinary_convoke_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    if mechanics != (_CONVOKE_MECHANIC,):
        return None
    ordinary = material_line.strip().rstrip(".").casefold() == _CONVOKE_MECHANIC
    gate = explicit_capability_gate(
        "casting.payment.convoke",
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    blockers = gate.blockers if ordinary else ("mechanic:convoke-unsupported-wording",)
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract" if ordinary else "keyword_grammar",
                text=line,
                span=span,
                reason=(
                    "Convoke depends on a blocked typed casting-cost capability"
                    if ordinary
                    else "Convoke wording is outside the ordinary keyword grammar"
                ),
                blockers=blockers,
            ),
        )
        if blockers
        else ()
    )
    return OracleNode(
        node_id=node_id,
        kind="keyword_ability",
        text=line,
        span=span,
        active_zone="stack",
        event="cast.cost",
        lowerable=ordinary,
        exact=ordinary and not blockers,
        template_id="ordinary-convoke-payment-v1" if ordinary else None,
        handlers=(convoke_handler_descriptor(),) if ordinary else (),
        runtime_coverage=("typed_convoke_payment",) if ordinary else (),
        mechanics=(_CONVOKE_MECHANIC,),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


def ordinary_affinity_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    return typed_affinity_keyword_node(
        node_id=node_id,
        line=line,
        material_line=material_line,
        span=span,
        mechanics=mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )


def bloodthirst_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    gate: DependencyGate,
    residual_ids: tuple[str, ...],
) -> OracleNode | None:
    """Lower one ordinary fixed CR 702.54a keyword instance."""

    if mechanics != (_BLOODTHIRST_MECHANIC,):
        return None
    match = re.fullmatch(
        rf"{re.escape(_BLOODTHIRST_MECHANIC)}\s+(?P<amount>[1-9]\d*)\.?",
        material_line.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return OracleNode(
            node_id=node_id,
            kind="static_ability",
            text=line,
            span=span,
            active_zone="all",
            event="zone.change",
            lowerable=False,
            exact=False,
            mechanics=mechanics,
            residual_ids=residual_ids,
            capability_dependencies=gate.capabilities,
        )
    spec = BloodthirstSpec(int(match.group("amount")))
    return OracleNode(
        node_id=node_id,
        kind="static_ability",
        text=line,
        span=span,
        active_zone="all",
        event="zone.change",
        lowerable=True,
        exact=not residual_ids,
        template_id="bloodthirst-opponent-damage-entry-counter-v1",
        handlers=(spec.handler_descriptor(),),
        runtime_coverage=("conditional_self_entry_counter",),
        mechanics=mechanics,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


def sunburst_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    printed_card_types: tuple[str, ...],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    """Lower one ordinary Sunburst using the printed type boundary."""

    if mechanics != (_SUNBURST_MECHANIC,):
        return None
    ordinary = (
        material_line.strip().rstrip(".").casefold()
        == _SUNBURST_MECHANIC
    )
    gate = explicit_capability_gate(
        "counter.producer.sunburst",
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    blockers = (
        gate.blockers
        if ordinary
        else ("mechanic:sunburst-unsupported-wording",)
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract" if ordinary else "keyword_grammar",
                text=line,
                span=span,
                reason=(
                    "Sunburst depends on a blocked typed capability"
                    if ordinary
                    else "Sunburst wording is outside the ordinary keyword grammar"
                ),
                blockers=blockers,
            ),
        )
        if blockers
        else ()
    )
    spec = SunburstSpec.for_printed_types(printed_card_types)
    return OracleNode(
        node_id=node_id,
        kind="static_ability",
        text=line,
        span=span,
        active_zone="all",
        event="zone.change",
        lowerable=ordinary,
        exact=ordinary and not blockers,
        template_id="sunburst-cast-entry-counter-v1" if ordinary else None,
        handlers=(spec.handler_descriptor(),) if ordinary else (),
        runtime_coverage=("cast_mana_color_entry_counter",) if ordinary else (),
        mechanics=mechanics,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


def evolve_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    gate: DependencyGate,
    residual_ids: tuple[str, ...],
) -> OracleNode | None:
    instances = tuple(
        part
        for part in material_line.rstrip(".").split(",")
        if part.strip().casefold() == _EVOLVE_MECHANIC
    )
    if mechanics != (_EVOLVE_MECHANIC,) or not instances:
        return None
    return OracleNode(
        node_id=node_id,
        kind="triggered_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="creature.enter",
        lowerable=True,
        exact=not gate.blockers,
        template_id="evolve-creature-enter-counter-v1",
        effects=(
            {
                "op": "place_counters",
                "card": "$source",
                "counter": "+1/+1",
                "amount": 1,
                "source": "$source",
            },
        ),
        event_condition={
            "field": EVOLVE_EVENT_CONDITION_FIELD,
            "op": "truthy",
        },
        runtime_coverage=("intervening_condition",),
        mechanics=mechanics,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


def prowess_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    gate: DependencyGate,
    residual_ids: tuple[str, ...],
    handlers: tuple[Mapping[str, Any], ...],
) -> OracleNode | None:
    if (
        mechanics != (_PROWESS_MECHANIC,)
        or material_line.strip().rstrip(".").casefold() != _PROWESS_MECHANIC
    ):
        return None
    return OracleNode(
        node_id=node_id,
        kind="triggered_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="spell.cast",
        lowerable=True,
        exact=not residual_ids,
        template_id="prowess-noncreature-spell-trigger-v1",
        effects=(
            {
                "op": "modify_stats_until_end_of_turn",
                "card": "$source.zone_object",
                "power": 1,
                "toughness": 1,
            },
        ),
        handlers=handlers,
        event_condition={
            "all": [
                {
                    "field": "controller",
                    "op": "eq",
                    "value": "$source.controller",
                },
                {
                    "not": {
                        "field": "types",
                        "op": "contains_any",
                        "value": ["creature"],
                    }
                },
            ]
        },
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        mechanics=mechanics,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


def renown_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    match = re.fullmatch(
        r"Renown\s+(?P<amount>[1-9]\d*)\.?",
        material_line.strip(),
        re.IGNORECASE,
    )
    if mechanics != (_RENOWN_MECHANIC,) or match is None:
        return None
    gate = keyword_dependency_gate(
        material_line=material_line,
        mechanics=mechanics,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    fragment_lowering = lower_ability_keyword_fragments(
        material_line,
        mechanics,
    )
    residual_id_values: list[str] = []
    if gate.blockers:
        residual_id_values.append(
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason="recognized keyword lacks a trusted mechanic contract",
                blockers=gate.blockers,
            )
        )
    if fragment_lowering.residual_kind is not None:
        residual_id_values.append(
            append_residual(
                residuals,
                kind=fragment_lowering.residual_kind,
                text=line,
                span=span,
                reason=str(fragment_lowering.residual_reason),
                blockers=fragment_lowering.residual_blockers,
            )
        )
    residual_ids = tuple(residual_id_values)
    spec = RenownSpec(amount=int(match.group("amount")))
    return OracleNode(
        node_id=node_id,
        kind="triggered_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="damage.dealt.self",
        lowerable=True,
        exact=not residual_ids,
        template_id="renown-combat-damage-counter-designation-v1",
        effects=(spec.effect_descriptor(),),
        handlers=fragment_lowering.handlers,
        event_condition=spec.event_condition(),
        runtime_coverage=(
            CURRENT_ABILITY_FRAGMENT_COVERAGE,
            "intervening_condition",
            "cr-122-counters",
        ),
        mechanics=mechanics,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


def death_return_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    gate: DependencyGate,
    residual_ids: tuple[str, ...],
) -> OracleNode | None:
    if mechanics not in {(_PERSIST_MECHANIC,), (_UNDYING_MECHANIC,)}:
        return None
    mechanic = mechanics[0]
    if material_line.strip().rstrip(".").casefold() != mechanic:
        return None
    spec = DeathReturnSpec.for_keyword(mechanic)
    return OracleNode(
        node_id=node_id,
        kind="triggered_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="creature.dies.self",
        lowerable=True,
        exact=not gate.blockers,
        template_id=f"{mechanic}-death-return-counter-v1",
        effects=(spec.effect_descriptor(),),
        event_condition=spec.event_condition(),
        runtime_coverage=("departure_intervening_condition",),
        mechanics=mechanics,
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


def modular_keyword_nodes(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> tuple[OracleNode, ...]:
    """Lower one fixed positive ``Modular N`` into both CR 702.43a abilities."""

    if mechanics != (_MODULAR_MECHANIC,):
        return ()
    match = re.fullmatch(
        r"Modular\s+(?P<amount>[1-9]\d*)\.?",
        material_line.strip(),
        re.IGNORECASE,
    )
    if match is None:
        residual_id = append_residual(
            residuals,
            kind="unsupported_modular_value",
            text=line,
            span=span,
            reason=(
                "Modular requires one printed positive integer; "
                "Modular—Sunburst remains a separate residual"
            ),
            blockers=("positive integer Modular value",),
        )
        return (
            OracleNode(
                node_id=node_id,
                kind="keyword_ability",
                text=line,
                span=span,
                active_zone="all",
                event="unresolved",
                lowerable=False,
                exact=False,
                mechanics=mechanics,
                residual_ids=(residual_id,),
            ),
        )

    gate = keyword_dependency_gate(
        material_line=material_line,
        mechanics=mechanics,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason="Modular depends on a blocked typed lifecycle capability",
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    spec = ModularSpec(amount=int(match.group("amount")))
    common = {
        "text": line,
        "span": span,
        "lowerable": True,
        "exact": not residual_ids,
        "mechanics": mechanics,
        "residual_ids": residual_ids,
        "capability_dependencies": gate.capabilities,
        "capability_closure": (
            gate.closure.reachable if gate.closure is not None else ()
        ),
        "capability_profile": (
            gate.closure.profile if gate.closure is not None else None
        ),
        "capability_fingerprint": (
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    }
    return (
        OracleNode(
            node_id=f"{node_id}:entry",
            kind="static_ability",
            active_zone="all",
            event="zone.change",
            template_id="modular-fixed-entry-counter-v1",
            handlers=(spec.entry_handler_descriptor(),),
            runtime_coverage=("replacement_aware_self_entry_counter",),
            **common,
        ),
        OracleNode(
            node_id=f"{node_id}:departure",
            kind="triggered_ability",
            active_zone="battlefield",
            event="permanent.graveyard.self",
            template_id="modular-lki-counter-transfer-v1",
            effects=(spec.departure_effect_descriptor(),),
            target_schema=spec.target_schema(),
            runtime_coverage=(
                "departure_counter_lki",
                "optional_targeted_counter_placement",
            ),
            **common,
        ),
    )


def unleash_keyword_nodes(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> tuple[OracleNode, ...]:
    """Lower the two static abilities represented by ordinary Unleash."""

    if material_line.strip().rstrip(".").casefold() != UNLEASH_MECHANIC:
        residual_id = append_residual(
            residuals,
            kind="keyword_grammar",
            text=line,
            span=span,
            reason="Unleash wording is outside the ordinary keyword grammar",
            blockers=("mechanic:unleash-unsupported-wording",),
        )
        return (
            OracleNode(
                node_id=node_id,
                kind="keyword_ability",
                text=line,
                span=span,
                active_zone="battlefield",
                event="continuous",
                lowerable=False,
                exact=False,
                mechanics=(UNLEASH_MECHANIC,),
                residual_ids=(residual_id,),
            ),
        )

    specifications = (
        (
            "unleash-entry",
            "all",
            "zone.change",
            "counter.producer.optional_self_entry",
            "unleash-optional-entry-counter-v1",
            unleash_entry_handler_descriptor(),
            "optional_entry_counter",
        ),
        (
            "unleash-block",
            "battlefield",
            "combat.block",
            "combat.block.self_counter_prohibition",
            "unleash-self-counter-block-prohibition-v1",
            unleash_block_handler_descriptor(),
            "counter_conditional_block_restriction",
        ),
    )
    result: list[OracleNode] = []
    for (
        suffix,
        active_zone,
        event,
        capability,
        template_id,
        handler,
        runtime_coverage,
    ) in specifications:
        gate = explicit_capability_gate(
            capability,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
        )
        residual_ids = (
            (
                append_residual(
                    residuals,
                    kind="dependency_contract",
                    text=line,
                    span=span,
                    reason="Unleash depends on a blocked typed capability",
                    blockers=gate.blockers,
                ),
            )
            if gate.blockers
            else ()
        )
        result.append(
            OracleNode(
                node_id=f"{node_id}:{suffix}",
                kind="static_ability",
                text=line,
                span=span,
                active_zone=active_zone,
                event=event,
                lowerable=True,
                exact=not gate.blockers,
                template_id=template_id,
                handlers=(handler,),
                runtime_coverage=(runtime_coverage,),
                mechanics=(UNLEASH_MECHANIC,),
                residual_ids=residual_ids,
                capability_dependencies=gate.capabilities,
                capability_closure=(
                    gate.closure.reachable if gate.closure is not None else ()
                ),
                capability_profile=(
                    gate.closure.profile if gate.closure is not None else None
                ),
                capability_fingerprint=(
                    gate.closure.fingerprint
                    if gate.closure is not None
                    else None
                ),
            )
        )
    return tuple(result)


def riot_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode:
    """Lower ordinary Riot as one linked entry-result choice."""

    ordinary = material_line.strip().rstrip(".").casefold() == RIOT_MECHANIC
    gate = explicit_capability_gate(
        "counter.producer.riot",
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    blockers = (
        gate.blockers
        if ordinary
        else ("mechanic:riot-unsupported-wording",)
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind=(
                    "dependency_contract" if ordinary else "keyword_grammar"
                ),
                text=line,
                span=span,
                reason=(
                    "Riot depends on a blocked typed capability"
                    if ordinary
                    else "Riot wording is outside the ordinary keyword grammar"
                ),
                blockers=blockers,
            ),
        )
        if blockers
        else ()
    )
    return OracleNode(
        node_id=node_id,
        kind="static_ability",
        text=line,
        span=span,
        active_zone="all",
        event="zone.change",
        lowerable=ordinary,
        exact=ordinary and not blockers,
        template_id="riot-linked-entry-choice-v1" if ordinary else None,
        handlers=(riot_entry_handler_descriptor(),) if ordinary else (),
        runtime_coverage=("linked_entry_counter_or_haste",) if ordinary else (),
        mechanics=(RIOT_MECHANIC,),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


def read_ahead_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    printed_subtypes: tuple[str, ...],
    chapter_numbers: tuple[int, ...],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode:
    """Lower ordinary printed Read Ahead with its final-chapter boundary."""

    ordinary = material_line.strip().rstrip(".").casefold() == (
        READ_AHEAD_MECHANIC_ID
    )
    printed_saga = "saga" in printed_subtypes
    chapters_closed = bool(chapter_numbers)
    gate = explicit_capabilities_gate(
        READ_AHEAD_CAPABILITIES,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    grammar_blockers = tuple(
        blocker
        for condition, blocker in (
            (ordinary, "mechanic:read-ahead-unsupported-wording"),
            (printed_saga, "mechanic:read-ahead-requires-printed-saga"),
            (
                chapters_closed,
                "mechanic:read-ahead-unrepresented-final-chapter",
            ),
        )
        if not condition
    )
    blockers = (*grammar_blockers, *gate.blockers)
    residual_ids = (
        (
            append_residual(
                residuals,
                kind=(
                    "keyword_grammar"
                    if grammar_blockers
                    else "dependency_contract"
                ),
                text=line,
                span=span,
                reason=(
                    "Read Ahead requires ordinary wording on a printed Saga "
                    "with contiguous chapter symbols"
                    if grammar_blockers
                    else "Read Ahead depends on a blocked typed capability"
                ),
                blockers=blockers,
            ),
        )
        if blockers
        else ()
    )
    grammar_closed = ordinary and printed_saga and chapters_closed
    return OracleNode(
        node_id=node_id,
        kind="static_ability",
        text=line,
        span=span,
        active_zone="all",
        event="zone.change",
        lowerable=grammar_closed,
        exact=grammar_closed and not blockers,
        template_id=(
            "read-ahead-saga-entry-choice-v1"
            if grammar_closed
            else None
        ),
        handlers=(
            (read_ahead_entry_handler_descriptor(chapter_numbers),)
            if grammar_closed
            else ()
        ),
        runtime_coverage=(
            ("read_ahead_chapter_choice", "replacement_aware_lore_entry")
            if grammar_closed
            else ()
        ),
        mechanics=(READ_AHEAD_MECHANIC_ID,),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


def fabricate_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    gate: DependencyGate,
    residual_ids: tuple[str, ...],
) -> OracleNode | None:
    matches = tuple(
        match
        for part in material_line.rstrip(".").split(",")
        for match in (
            re.fullmatch(
                r"Fabricate\s+(?P<count>[1-9]\d*)\.?",
                part.strip(),
                re.IGNORECASE,
            ),
        )
        if match is not None
    )
    if mechanics != (_FABRICATE_MECHANIC,) or len(matches) != 1:
        return None
    return OracleNode(
        node_id=node_id,
        kind="triggered_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="permanent.enter.self",
        lowerable=True,
        exact=not gate.blockers,
        template_id="fabricate-enter-choice-v1",
        mechanics=mechanics,
        effects=(
            {
                "op": _FABRICATE_MECHANIC,
                "amount": int(matches[0].group("count")),
            },
        ),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


def dredge_keyword_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    mechanics: tuple[str, ...],
    gate: DependencyGate,
    residual_ids: tuple[str, ...],
) -> OracleNode | None:
    match = re.fullmatch(
        r"Dredge\s+(?P<count>[1-9]\d*)\.?",
        material_line,
        re.IGNORECASE,
    )
    if mechanics != (_DREDGE_MECHANIC,) or match is None:
        return None
    return OracleNode(
        node_id=node_id,
        kind="keyword_ability",
        text=line,
        span=span,
        active_zone="graveyard",
        event="draw",
        lowerable=True,
        exact=not gate.blockers,
        template_id="dredge-keyword-replacement-v1",
        mechanics=mechanics,
        handlers=(
            {
                "handler_id": "replacement.draw.dredge.v1",
                "schema_version": 1,
                "event": "draw",
                "modification": {"mill_count": int(match.group("count"))},
            },
        ),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=(
            gate.closure.reachable if gate.closure is not None else ()
        ),
        capability_profile=(
            gate.closure.profile if gate.closure is not None else None
        ),
        capability_fingerprint=(
            gate.closure.fingerprint if gate.closure is not None else None
        ),
    )


__all__ = [
    "KeywordNodePlan",
    "bloodthirst_keyword_node",
    "closed_special_keyword_node",
    "dredge_keyword_node",
    "death_return_keyword_node",
    "evolve_keyword_node",
    "fabricate_keyword_node",
    "keyword_node_plans",
    "modular_keyword_nodes",
    "ordinary_affinity_keyword_node",
    "ordinary_convoke_keyword_node",
    "prowess_keyword_node",
    "renown_keyword_node",
    "riot_keyword_node",
    "storm_keyword_node",
    "unleash_keyword_nodes",
]

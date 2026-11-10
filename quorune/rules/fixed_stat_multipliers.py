from __future__ import annotations

"""Fixed doubling and tripling as signed, resolution-created modifiers."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import re

from ..attachment_references import AttachmentReferenceKind, AttachmentReferenceSpec
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..object_predicate import ObjectQuerySpec, object_query_capabilities
from ..qualified_attack_model import ATTACK_CHARACTERISTIC_RESULT_CAPABILITY
from ..replacement.immutable import thaw_value
from ..resolution_characteristic_model import CHARACTERISTIC_OPERATION


STAT_MULTIPLIER_VERSION = 19
STAT_MULTIPLIER_CAPABILITY = "continuous.resolution.fixed_stat_multiplier"
STAT_MULTIPLIER_MECHANIC = "fixed-resolution-stat-multiplier"
_BASE_CAPABILITY = "continuous.resolution.fixed_characteristics_until_end_of_turn"
_TARGET_CAPABILITY = "target.revalidate_resolution"
_SOURCE = "$source.zone_object"
ATTACK_MULTIPLIER_RECIPIENT = "$event.permanent_object"
EQUIPPED_MULTIPLIER_RECIPIENT = AttachmentReferenceSpec(AttachmentReferenceKind.EQUIPPED, "creature", schema_version=2)
_SCALAR_TARGET = re.compile(r"\$target\.0|\$target_group\.(?:clause_[0-9]+|target_[0-9]+)\.0")


class MultipliedStat(StrEnum):
    POWER = "power"
    TOUGHNESS = "toughness"
    BOTH = "power and toughness"


@dataclass(frozen=True, slots=True)
class FixedStatMultiplier:
    factor: int
    stat: MultipliedStat
    grant_first_strike: bool = False

    def __post_init__(self):
        if type(self.factor) is not int or self.factor not in (2, 3) or not isinstance(self.stat, MultipliedStat):
            raise ValueError("A fixed stat multiplier requires a closed factor and characteristic")
        if type(self.grant_first_strike) is not bool:
            raise ValueError("A fixed stat multiplier keyword grant must be boolean")

    def delta(self, power: int | None, toughness: int | None) -> tuple[int, int]:
        if any(value is not None and type(value) is not int for value in (power, toughness)):
            raise ValueError("A creature's resolved characteristics must be integers or absent")
        coefficient = self.factor - 1
        return (
            coefficient * (power or 0) if self.stat is not MultipliedStat.TOUGHNESS else 0,
            coefficient * (toughness or 0) if self.stat is not MultipliedStat.POWER else 0,
        )


def is_fixed_stat_multiplier(value) -> bool:
    return isinstance(value, Mapping) and value.get("op") == CHARACTERISTIC_OPERATION and value.get("schema_version") == STAT_MULTIPLIER_VERSION


def decode_fixed_stat_multiplier(effect, *, controller: str | None = None):
    fields = {"op", "schema_version", "factor", "stat"}
    if not is_fixed_stat_multiplier(effect) or type(effect["schema_version"]) is not int:
        raise ValueError("Fixed stat multiplier version is malformed")
    first_strike = "grant_first_strike" in effect
    if first_strike:
        fields.add("grant_first_strike")
        if effect["grant_first_strike"] is not True:
            raise ValueError("A combined stat result grants the fixed keyword or omits the field")
    try:
        spec = FixedStatMultiplier(effect["factor"], MultipliedStat(effect["stat"]), first_strike)
    except KeyError as exc:
        raise ValueError("Fixed stat multiplier fields are incomplete") from exc
    if set(effect) == fields | {"card"}:
        ref = effect["card"]
        if controller is not None and (ref is None or type(ref) is str and ref and not ref.startswith("$")):
            return spec, ref, False
        if controller is None:
            if type(ref) is str and _SCALAR_TARGET.fullmatch(ref):
                return spec, ref, False
            if not first_strike and type(ref) is str and ref in {_SOURCE, ATTACK_MULTIPLIER_RECIPIENT}:
                return spec, ref, False
            if not first_strike and isinstance(ref, Mapping) and AttachmentReferenceSpec.from_dict(ref) == EQUIPPED_MULTIPLIER_RECIPIENT:
                return spec, EQUIPPED_MULTIPLIER_RECIPIENT, False
        raise ValueError("Fixed stat multiplier has an unbound recipient")
    if first_strike or set(effect) != fields | {"predicate", "exclude_source"} or type(effect["exclude_source"]) is not bool:
        raise ValueError("Fixed stat multiplier selection fields are closed")
    query = ObjectQuerySpec.from_dict(effect["predicate"])
    expected = ObjectQuerySpec(zones=("battlefield",), controller=controller or "$controller",
        types_all=("creature",), subtypes_all=query.subtypes_all)
    if len(query.subtypes_all) > 1 or query != expected or thaw_value(effect["predicate"]) != expected.to_dict():
        raise ValueError("Fixed stat multiplier requires one controlled battlefield creature set")
    return spec, query, effect["exclude_source"]


def fixed_stat_multiplier_runtime_dependencies(effect, *, target_schema):
    spec, subject, _exclude = decode_fixed_stat_multiplier(effect)
    required = {STAT_MULTIPLIER_CAPABILITY, _BASE_CAPABILITY}
    if spec.grant_first_strike:
        required.update(FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES["First Strike"])
    if isinstance(subject, ObjectQuerySpec):
        required.update(object_query_capabilities(subject))
    elif isinstance(subject, AttachmentReferenceSpec):
        required.add("attachment.reference.current_or_lki")
    elif subject == ATTACK_MULTIPLIER_RECIPIENT:
        if target_schema is not None:
            raise ValueError("An attack multiplier requires one untargeted occurrence")
        required.update((ATTACK_CHARACTERISTIC_RESULT_CAPABILITY, "resolution.event_object.fixed_result"))
    elif subject != _SOURCE:
        from ..compiler.direct_target import direct_permanent_target_spec

        schema = thaw_value(target_schema)
        if isinstance(schema, Mapping) and subject == "$target.0" and "groups" in schema:
            groups = schema["groups"]
            if not isinstance(groups, (list, tuple)) or not groups or not isinstance(groups[0], Mapping):
                raise ValueError("A target sequence requires its first group")
            schema = {key: value for key, value in groups[0].items() if key != "id"}
        elif subject != "$target.0" and isinstance(schema, Mapping):
            from ..targets import target_group_mappings

            group_id = subject.split(".")[1]
            groups = target_group_mappings(schema, tuple(schema.get("modes", ())), require_modes=False)
            schema = next(({key: value for key, value in group.items() if key != "id"}
                           for index, group in enumerate(groups) if group.get("id", f"target_{index}") == group_id), None)
        allowed = tuple(direct_permanent_target_spec(text).to_target_schema()
                        for text in ("target creature", "target creature you control"))
        if schema not in allowed:
            raise ValueError("A targeted stat multiplier requires its own closed creature target")
        required.add(_TARGET_CAPABILITY)
    return tuple(sorted(required))


def fixed_stat_multiplier_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or not {STAT_MULTIPLIER_MECHANIC, "cr-611-continuous-effects"} <= set(mechanic_ids):
        return ()
    try:
        return fixed_stat_multiplier_runtime_dependencies(effects[0], target_schema=target_schema)
    except (TypeError, ValueError):
        return ()


def apply_fixed_stat_multiplier(host, effect, *, actor, reason):
    from ..continuous_effect_state import (
        ResolutionContinuousComponent, create_resolution_continuous_effect,
        create_resolution_continuous_effect_components, matching_battlefield_objects, resolution_effect_source,
    )
    from ..continuous_effects import ContinuousOperation, Layer
    from ..errors import GameRuleError

    try:
        spec, subject, exclude_source = decode_fixed_stat_multiplier(
            {key: value for key, value in effect.items() if key != "_runtime_source"}, controller=actor,
        )
        if subject is None:
            return []
        if isinstance(subject, ObjectQuerySpec):
            candidates = matching_battlefield_objects(host, subject)
        else:
            try:
                candidates = (host._resolve_object(actor, subject, zones={"battlefield"}),)
            except GameRuleError:
                return []
        if not candidates:
            return []
        source = resolution_effect_source(host, effect, fallback_card=candidates[0])
        groups = defaultdict(list)
        for card in candidates:
            if card.phased_out or exclude_source and card.logical_object_id == source.logical_object_id:
                continue
            current = host._public_object_query_result(card, _maximum_layer=None)
            if "creature" in current.types:
                groups[spec.delta(current.effective_power, current.effective_toughness)].append(card)
        for (power, toughness), cards in groups.items():
            if spec.grant_first_strike:
                components = [ResolutionContinuousComponent(
                    Layer.ABILITY, "6", (ContinuousOperation("add_ability", "First Strike"),),
                )]
                if power or toughness:
                    components.append(ResolutionContinuousComponent(
                        Layer.POWER_TOUGHNESS, "7c", (ContinuousOperation("modify_power_toughness", (power, toughness)),),
                    ))
                create_resolution_continuous_effect_components(host, source=source, targets=cards, components=components)
            elif (power or toughness) and create_resolution_continuous_effect(
                host, source=source, targets=cards, layer=Layer.POWER_TOUGHNESS, sublayer="7c",
                operations=(ContinuousOperation("modify_power_toughness", (power, toughness)),),
            ) is None:
                raise GameRuleError("Fixed stat multiplication requires the continuous-effect journal")
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    affected = [card for cards in groups.values() for card in cards]
    refs = [card.ref for card in affected]
    if refs:
        host._log(actor, "permanent.characteristics", "Multiplied creature characteristics until end of turn.",
            {"objects": refs, "reason": reason}, importance=1, changed_objects=[card.object_id for card in affected])
    return refs

"""Minimum ownership and live projection of resolved attack requirements."""
from collections.abc import Mapping

from ..attachment_references import ATTACHMENT_INFORMATION_CAPABILITY, AttachmentReferenceKind, AttachmentReferenceSpec
from ..replacement.immutable import FrozenMap, thaw_value
from ..temporary_attack_requirement_model import (
    TEMPORARY_ATTACK_REQUIREMENT_KIND, TEMPORARY_ATTACK_REQUIREMENT_CAPABILITY,
    TemporaryAttackRequirementSpec,
)


_MINIMUM = (TEMPORARY_ATTACK_REQUIREMENT_CAPABILITY, "combat.declaration.typed_components",
            "continuous.resolution.declaration_rules_until_end_of_turn")


LIVE_OPPONENT_ATTACK_RULE = "opponents_attack_each_combat_except_controller"
LIVE_OPPONENT_ATTACK_CAPABILITY = "combat.requirement.live_opponent_creatures_next_turn"


def live_opponent_attack_effect(*, timed: bool):
    from ..affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation
    from ..next_turn_effect import NEXT_TURN_EFFECT_FIELD
    from ..object_predicate import ObjectQuerySpec

    scope = AffectedPermanentSetSpec(
        ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
        PermanentControllerRelation.OPPONENTS,
    )
    return {"op": "grant_declaration_restriction_until_end_of_turn",
            "set": scope.to_dict(), "restriction": LIVE_OPPONENT_ATTACK_RULE,
            **({NEXT_TURN_EFFECT_FIELD: True} if timed else {})}


def live_opponent_attack_node_capabilities(effect, target_schema, mechanic_ids):
    from ..next_turn_effect import NEXT_TURN_EFFECT_CAPABILITY, NEXT_TURN_EFFECT_FIELD

    if (target_schema is not None
            or not {"cr-611-continuous-effects", "cr-508-declare-attackers-step"} <= set(mechanic_ids)
            or effect.get(NEXT_TURN_EFFECT_FIELD) is not True
            or FrozenMap(effect) != FrozenMap(live_opponent_attack_effect(timed=True))):
        return ()
    return (LIVE_OPPONENT_ATTACK_CAPABILITY, "combat.declaration.typed_components",
            "continuous.resolution.declaration_rules_until_end_of_turn", NEXT_TURN_EFFECT_CAPABILITY)


def commit_live_opponent_attack_rule(host, effect, *, actor, next_turn, reason):
    from ..continuous_effect_state import create_resolution_declaration_rule_effect, resolution_effect_source
    from ..declaration_rule_effects import live_opponent_attack_query
    from ..errors import GameRuleError

    raw = {key: value for key, value in effect.items() if key not in {"reason", "_runtime_source"}}
    if not next_turn or FrozenMap(raw) != FrozenMap(live_opponent_attack_effect(timed=False)):
        raise GameRuleError("Live opponent attack requirements need their closed next-turn instruction")
    if actor not in host.active_seats or host.state.continuous_effects is None:
        raise GameRuleError("Live opponent attack requirements need an active controller and effect journal")
    rule = create_resolution_declaration_rule_effect(
        host, source=resolution_effect_source(host, effect), targets=(),
        restriction=TemporaryAttackRequirementSpec().to_requirement(),
        live_query=live_opponent_attack_query(actor), expires_on_next_turn_of=actor,
    )
    host._log(actor, "combat.declaration_restriction", reason,
              {"continuous_effect": rule.effect_id, "expires_on_next_turn_of": actor}, importance=1)
    return []


def _requirement_dependencies(effect, target_schema):
    if set(effect) != {"op", "card", "restriction"} or effect["op"] != "grant_declaration_restriction_until_end_of_turn":
        raise ValueError("Resolved attack requirements have one closed subject instruction")
    spec = TemporaryAttackRequirementSpec.from_dict(effect["restriction"])
    if thaw_value(target_schema) != spec.target_schema():
        raise ValueError("Resolved attack requirement target schema changed")
    if spec.schema_version == 3:
        reference = AttachmentReferenceSpec(AttachmentReferenceKind.ENCHANTED, "creature", schema_version=2)
        if AttachmentReferenceSpec.from_dict(effect["card"]) != reference:
            raise ValueError("An enchanted attack requirement needs the current or last-known source attachment")
        return (*_MINIMUM, ATTACHMENT_INFORMATION_CAPABILITY)
    if effect["card"] != "$target.0":
        raise ValueError("A targeted attack requirement must keep its original creature target")
    return (*_MINIMUM, "target.revalidate_resolution")


def temporary_attack_requirement_node_capabilities(*, effects, target_schema, mechanic_ids):
    mechanics = {"cr-611-continuous-effects", "cr-508-declare-attackers-step"}
    if target_schema is not None:
        mechanics.add("cr-115-targets")
    if len(effects) != 1 or not mechanics <= set(mechanic_ids):
        return ()
    try:
        return _requirement_dependencies(effects[0], target_schema)
    except (TypeError, ValueError):
        return ()


def temporary_attack_requirement_runtime_dependencies(program):
    required = set()

    def visit(value):
        if isinstance(value, Mapping):
            spec = value.get("restriction")
            if spec == LIVE_OPPONENT_ATTACK_RULE:
                owners = live_opponent_attack_node_capabilities(value, program.target_schema, program.coverage)
                if (not owners or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
                        or tuple(program.effects) != (value,) or program.handlers or program.cost_schema is not None):
                    raise ValueError("Live opponent attack requirements need their complete original self-entry trigger")
                required.update(owners)
            if (value.get("op") == "grant_declaration_restriction_until_end_of_turn"
                    and isinstance(spec, Mapping) and spec.get("kind") == TEMPORARY_ATTACK_REQUIREMENT_KIND):
                if spec.get("schema_version") in {3, 4} and (
                        (program.event, program.active_zone) != ("activate", "battlefield")
                        or tuple(program.effects) != (value,)):
                    raise ValueError("A source-bound attack requirement needs its original activated ability")
                required.update(_requirement_dependencies(value, program.target_schema))
            for child in value.values():
                visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value:
                visit(child)

    visit((program.effects, program.handlers))
    return tuple(sorted(required))

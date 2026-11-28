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
LIVE_OPPONENT_ATTACK_THIS_TURN_RULE = "opponents_attack_each_combat_this_turn"
LIVE_OPPONENT_ATTACK_THIS_TURN_CAPABILITY = "combat.requirement.live_opponent_creatures_this_turn"
LIVE_OPPONENT_ATTACK_RULES = (LIVE_OPPONENT_ATTACK_RULE, LIVE_OPPONENT_ATTACK_THIS_TURN_RULE)


def live_opponent_attack_effect(*, timed: bool, current_turn: bool = False):
    from ..affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation
    from ..next_turn_effect import NEXT_TURN_EFFECT_FIELD
    from ..object_predicate import ObjectQuerySpec

    scope = AffectedPermanentSetSpec(
        ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
        PermanentControllerRelation.OPPONENTS,
    )
    restriction = LIVE_OPPONENT_ATTACK_THIS_TURN_RULE if current_turn else LIVE_OPPONENT_ATTACK_RULE
    return {"op": "grant_declaration_restriction_until_end_of_turn",
            "set": scope.to_dict(), "restriction": restriction,
            **({NEXT_TURN_EFFECT_FIELD: True} if timed else {})}


def live_opponent_attack_node_capabilities(effect, target_schema, mechanic_ids):
    from ..next_turn_effect import NEXT_TURN_EFFECT_CAPABILITY, NEXT_TURN_EFFECT_FIELD

    if (target_schema is not None
            or not {"cr-611-continuous-effects", "cr-508-declare-attackers-step"} <= set(mechanic_ids)):
        return ()
    if FrozenMap(effect) == FrozenMap(live_opponent_attack_effect(timed=False, current_turn=True)):
        return (LIVE_OPPONENT_ATTACK_THIS_TURN_CAPABILITY, "combat.declaration.typed_components",
                "continuous.resolution.declaration_rules_until_end_of_turn")
    if (effect.get(NEXT_TURN_EFFECT_FIELD) is not True
            or FrozenMap(effect) != FrozenMap(live_opponent_attack_effect(timed=True))):
        return ()
    return (LIVE_OPPONENT_ATTACK_CAPABILITY, "combat.declaration.typed_components",
            "continuous.resolution.declaration_rules_until_end_of_turn", NEXT_TURN_EFFECT_CAPABILITY)


def commit_live_opponent_attack_rule(host, effect, *, actor, next_turn, reason):
    from ..continuous_effect_state import create_resolution_declaration_rule_effect, resolution_effect_source
    from ..declaration_rule_effects import live_opponent_attack_query
    from ..errors import GameRuleError

    raw = {key: value for key, value in effect.items() if key not in {"reason", "_runtime_source"}}
    current_turn = raw.get("restriction") == LIVE_OPPONENT_ATTACK_THIS_TURN_RULE
    if next_turn == current_turn or FrozenMap(raw) != FrozenMap(
        live_opponent_attack_effect(timed=False, current_turn=current_turn),
    ):
        raise GameRuleError("Live opponent attack requirements need their exact duration and opponent scope")
    if actor not in host.active_seats or host.state.continuous_effects is None:
        raise GameRuleError("Live opponent attack requirements need an active controller and effect journal")
    rule = create_resolution_declaration_rule_effect(
        host, source=resolution_effect_source(host, effect), targets=(),
        restriction=TemporaryAttackRequirementSpec().to_requirement(),
        live_query=live_opponent_attack_query(actor), expires_on_next_turn_of=actor if next_turn else None,
    )
    details = {"continuous_effect": rule.effect_id}
    if next_turn:
        details["expires_on_next_turn_of"] = actor
    host._log(actor, "combat.declaration_restriction", reason,
              details, importance=1)
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
            if spec in LIVE_OPPONENT_ATTACK_RULES:
                owners = live_opponent_attack_node_capabilities(value, program.target_schema, program.coverage)
                current_turn = spec == LIVE_OPPONENT_ATTACK_THIS_TURN_RULE
                event = "activate" if current_turn else "permanent.enter.self"
                handlers = ("activation.catalog.pinned.v1",) if current_turn else ()
                if (not owners or (program.event, program.active_zone) != (event, "battlefield")
                        or tuple(program.effects) != (value,)
                        or tuple(handler.get("handler_id") for handler in program.handlers) != handlers
                        or not current_turn and program.cost_schema is not None):
                    raise ValueError("Live opponent attack requirements need their complete original battlefield ability")
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


def goad_chapter_node_capabilities(effects, target_schema):
    from ..goad_instruction import (
        HAND_COUNT_UNBLOCKABLE_CAPABILITY, HAND_COUNT_UNBLOCKABLE_FIELD, WATCHED_COMBAT_DRAW_CAPABILITY,
        WATCHED_COMBAT_DRAW_FIELD, hand_count_unblockable_effect, hand_count_unblockable_schema,
        watched_combat_draw_effect, watched_combat_draw_schema,
    )
    from ..replacement.immutable import thaw_value

    for marker, specification, schema, required in (
        (WATCHED_COMBAT_DRAW_FIELD, watched_combat_draw_effect, watched_combat_draw_schema,
         (WATCHED_COMBAT_DRAW_CAPABILITY, "target.cardinality.any_number", "target.set.one_per_player",
          "target.revalidate_resolution", "combat.goad.fixed_instruction", "permanent.tap.effect",
          "trigger.event.normalized_damage", "trigger.placement.apnap", "zone.draw.library_to_hand")),
        (HAND_COUNT_UNBLOCKABLE_FIELD, hand_count_unblockable_effect, hand_count_unblockable_schema,
         (HAND_COUNT_UNBLOCKABLE_CAPABILITY, "target.revalidate_resolution",
          "continuous.resolution.fixed_characteristics_until_end_of_turn",
          "continuous.resolution.declaration_rules_until_end_of_turn", "combat.declaration.typed_components")),
    ):
        if not any(marker in effect for effect in effects):
            continue
        if (thaw_value(effects) != [specification()] or type(effects[0].get(marker)) is not int
                or thaw_value(target_schema) != schema()):
            return ()
        return required
    return None


def goad_chapter_program_dependencies(program):
    from ..goad_instruction import WATCHED_COMBAT_DRAW_CAPABILITY

    required = goad_chapter_node_capabilities(program.effects, program.target_schema)
    if required is None:
        return ()
    events = ({"saga.chapter.1", "saga.chapter.2"} if WATCHED_COMBAT_DRAW_CAPABILITY in required else {"saga.chapter.3"})
    if (not required or program.event not in events or program.active_zone != "battlefield"
            or program.event_condition is not None or program.cost_schema is not None or program.handlers):
        raise ValueError("Goad and hand-count chapters require their original exact Saga programs")
    return required


def resolving_goad_chapter_source(host, effect, *, actor):
    from ..continuous_effect_state import ResolutionEffectSource
    from ..goad_instruction import HAND_COUNT_UNBLOCKABLE_FIELD, WATCHED_COMBAT_DRAW_FIELD
    from ..replacement.immutable import thaw_value

    source = ResolutionEffectSource.from_effect(effect)
    item = next((row for row in host.state.stack if row.ref == source.stack_ref), None)
    program = host.semantics.get(item.semantic_key) if item is not None else None
    required = goad_chapter_program_dependencies(program) if program is not None else ()
    if (item is None or item.controller != actor or item.context.get("currently_resolving") is not True
            or item.context.get("targets_revalidated") is not True or not required
            or not host.semantic_program_is_current_trusted(program)
            or not set(required) <= set(program.capability_dependencies)
            or (item.source_object_id, item.context.get("source_logical_object_id"))
                != (source.object_id, source.logical_object_id)):
        raise ValueError("Goad chapter lost its original source or target resolution")
    expected = dict(thaw_value(program.effects[0]))
    field, ref_field = ((WATCHED_COMBAT_DRAW_FIELD, "cards") if WATCHED_COMBAT_DRAW_FIELD in expected
                        else (HAND_COUNT_UNBLOCKABLE_FIELD, "card"))
    refs = tuple(ref for ref in item.targets if ref is not None)
    expected[ref_field] = list(refs) if ref_field == "cards" else refs[0] if len(refs) == 1 else None
    actual = {key: thaw_value(value) for key, value in effect.items() if key != "_runtime_source"}
    if type(actual.get(field)) is not int or expected != actual:
        raise ValueError("Goad chapter changed its exact revalidated target set")
    return source, refs


def resolve_watched_goad_chapter(host, effect, *, actor, reason, goad):
    from ..continuous_effect_model import ContinuousObjectIdentity
    from ..goad_instruction import WATCHED_COMBAT_DRAW_FIELD
    from ..tap_state import dispatch_tap_state_group, set_permanent_tapped
    from ..trigger_processing import schedule_delayed_trigger

    source, refs = resolving_goad_chapter_source(host, effect, actor=actor)
    if not refs:
        return []
    cards = tuple(host._resolve_object(actor, ref, zones={"battlefield"}) for ref in refs)
    if (len({card.controller for card in cards}) != len(cards)
            or any(card.phased_out or card.controller == actor or "creature" not in host._effective_type_parts(card)[0]
                   for card in cards)):
        raise ValueError("Goad chapter requires one current creature per retained opponent")
    identities = tuple(ContinuousObjectIdentity(card.object_id, card.logical_object_id) for card in cards)
    to_tap = tuple(card for card in cards if not card.tapped)
    for card in to_tap:
        set_permanent_tapped(host, card.ref, actor=actor, tapped=True, reason=reason,
                             logical_object_id=card.logical_object_id, semantic_events=False)
    dispatch_tap_state_group(host, to_tap, tapped=True, reason=reason)
    for card in cards:
        goad(card.ref)
    schedule_delayed_trigger(
        host, controller=actor, label="Draw for the watched creature's combat damage", event_kind="damage.dealt",
        condition={WATCHED_COMBAT_DRAW_FIELD: {
            "schema_version": 1, "expires_at_turns_begun": host.state.players[actor].turns_begun + 1,
            "watched": [identity.to_dict() for identity in identities],
        }},
        stack_template={"context": {"dynamic_effects": [
            {"op": "draw", "player": "$controller", "count": 1, "private": True},
        ]}},
        source_object_id=source.object_id, source_logical_object_id=source.logical_object_id,
        once=False,
    )
    return list(refs)

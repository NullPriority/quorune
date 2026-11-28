"""Pin controlled nontoken deaths to their source's optional upkeep return."""
from collections.abc import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..entry_counter_model import EffectEntryCounter
from ..errors import GameRuleError
from ..linked_exile_return_model import ExiledObjectIdentity, validate_linked_ability_binding
from ..replacement.immutable import FrozenMap
from .death_battlefield_return import DEATH_BATTLEFIELD_ARRIVALS, decode_death_battlefield_arrivals

FIELD = "linked_death_upkeep_return"
OFFERED = "_linked_death_upkeep_offered"
VERSION = 18
CAPABILITY = "zone.move.linked_death_upkeep_finality"
COMMON = (CAPABILITY, "zone.exile.linked_ability_pair", "zone.linked_exile_return.fixed",
          "trigger.effect.fixed_event", "trigger.placement.apnap", "zone.change.destination_replacement")
DEATH_DEPENDENCIES = (*COMMON, "trigger.event.normalized_zone_change", "trigger.event.qualified_zone_change")
RETURN_DEPENDENCIES = (*COMMON, "counter.producer.effect_entry", "counter.rule.finality",
                       "counter.placement.quantity_replacement")
DEATH_CONDITION = {"all": [{"field": "controller", "op": "eq", "value": "$source.controller"},
                           {"field": "token", "op": "eq", "value": False}]}
UPKEEP_CONDITION = {"all": [{"field": "player", "op": "eq", "value": "$source.controller"},
                            {"field": "step", "op": "eq", "value": "upkeep"}]}
_RUNTIME_FIELDS = {"_runtime_source", "_replacement_selections"}


def instruction(pair, phase, *, selected=None):
    validate_linked_ability_binding(pair)
    if phase not in {"exile", "choose", "enter"}:
        raise ValueError("Linked death upkeep instruction has an unknown phase")
    result = {"op": "choose_objects" if phase == "choose" else "linked_exile_return",
              "schema_version": VERSION, FIELD: phase, "binding_id": pair}
    if phase == "exile":
        result[DEATH_BATTLEFIELD_ARRIVALS] = "$context." + DEATH_BATTLEFIELD_ARRIVALS
    elif phase == "enter":
        if not isinstance(selected, ExiledObjectIdentity):
            raise ValueError("The upkeep return requires its chosen exile incarnation")
        result["selected"] = selected.to_dict()
    return result


def uses_instruction(program):
    from .effect_shape_walkers import nested_effects

    return (CAPABILITY in program.capability_dependencies
            or any(FIELD in effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))))


def program_dependencies(program):
    if not uses_instruction(program):
        return ()
    if len(program.effects) != 1:
        raise ValueError("Linked death upkeep effects require their original printed trigger")
    effect, = program.effects
    phase, pair = effect.get(FIELD), effect.get("binding_id")
    if phase not in {"exile", "choose"} or type(effect.get("schema_version")) is not int:
        raise ValueError("Linked death upkeep requires an exact trigger phase")
    event, condition, required = (("creature.dies", DEATH_CONDITION, DEATH_DEPENDENCIES) if phase == "exile"
                                  else ("step.begin", UPKEEP_CONDITION, RETURN_DEPENDENCIES))
    if (FrozenMap(effect) != FrozenMap(instruction(pair, phase)) or program.event != event
            or FrozenMap(program.event_condition or {}) != FrozenMap(condition)
            or program.active_zone != "battlefield" or program.cost_schema is not None
            or program.target_schema is not None or program.handlers or program.trigger_limit is not None):
        raise ValueError("Linked death upkeep lost its exact original event and scope")
    return required


def _source(host, item, actor, pair, phase):
    if (item is None or item.kind not in {"triggered_ability", "triggered_ability_copy"}
            or item.controller != actor or item.context.get("currently_resolving") is not True):
        raise GameRuleError("Linked death upkeep requires its resolving trigger")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or tuple(program.effects) != (instruction(pair, phase),)):
        raise GameRuleError("Linked death upkeep lost the trusted original pair")
    try:
        return ContinuousObjectIdentity(item.source_object_id, item.context.get("source_logical_object_id"))
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc


def _current_creatures(host, item, actor, pair):
    from ..linked_ability_exile_state import current_linked_ability_exile

    source = _source(host, item, actor, pair, "choose")
    if item.context.get("event") != "step.begin" or item.context.get("step") != "upkeep":
        raise GameRuleError("Linked death return requires the original upkeep trigger")
    for identity in current_linked_ability_exile(host, source=source, ability_pair=pair):
        card = host.state.cards[identity.object_id]
        if not card.face_down and "creature" in host._type_parts(
            str(host._effective_card_data(card).get("type_line") or ""),
        )[0]:
            yield identity


def choice_refs(host, item, actor, effect):
    pair = effect.get("binding_id")
    literal = {key: value for key, value in effect.items() if key != OFFERED}
    if FrozenMap(literal) != FrozenMap(instruction(pair, "choose")):
        raise GameRuleError("Linked death upkeep selection has a malformed instruction")
    return tuple(value.ref for value in _current_creatures(host, item, actor, pair))


def resolved_instruction(effect):
    phase, pair = effect.get(FIELD), effect.get("binding_id")
    literal = {key: value for key, value in effect.items() if key not in _RUNTIME_FIELDS}
    if type(effect.get("schema_version")) is not int or phase not in {"exile", "enter"}:
        raise ValueError("Linked death upkeep requires a versioned resolved phase")
    if phase == "exile":
        arrivals = decode_death_battlefield_arrivals(effect[DEATH_BATTLEFIELD_ARRIVALS])
        expected = {**instruction(pair, phase), DEATH_BATTLEFIELD_ARRIVALS: [row.to_dict() for row in arrivals]}
        chosen = arrivals
    else:
        chosen = ExiledObjectIdentity.from_dict(effect.get("selected"))
        expected = instruction(pair, phase, selected=chosen)
    if FrozenMap(literal) != FrozenMap(expected):
        raise ValueError("Linked death upkeep resolution changed its original committed instruction")
    return phase, pair, chosen


def apply_linked_death_upkeep(host, item, effect, *, actor, reason):
    from ..linked_ability_exile_state import exile_batch_for_linked_ability
    from ..zone_transitions import ZoneTransitionOwner

    try:
        phase, pair, chosen = resolved_instruction(effect)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    replacements = tuple(effect.get("_replacement_selections", ()))
    if phase == "exile":
        source = _source(host, item, actor, pair, "exile")
        context = item.context
        former = context.get("previous_controller")
        if (context.get("event"), context.get("from"), context.get("to"), context.get("token")) != (
            "creature.dies", "battlefield", "graveyard", False,
        ) or ("creature" not in context.get("types", ()) or not former or context.get("controller") != former
              or item.kind == "triggered_ability" and former != actor
              or FrozenMap({DEATH_BATTLEFIELD_ARRIVALS: context.get(DEATH_BATTLEFIELD_ARRIVALS)})
              != FrozenMap({DEATH_BATTLEFIELD_ARRIVALS: [row.to_dict() for row in chosen]})):
            raise GameRuleError("Linked death exile lost the committed controlled nontoken death")
        cards = tuple(host.state.cards[row.object_id] for row in chosen
                      if row.matches(host.state.cards.get(row.object_id)))
        return exile_batch_for_linked_ability(host, cards=cards, source=source, ability_pair=pair,
                                              reason=reason, replacement_selections=replacements)
    if chosen not in _current_creatures(host, item, actor, pair):
        return ()
    card = host.state.cards[chosen.object_id]
    counters = {card.object_id: (EffectEntryCounter("finality", 1, actor, item.ref, "122.6a"),)}
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        ((card.object_id, "battlefield"),), reason=reason, destination_controllers={card.object_id: actor},
        effect_entry_counters=counters, putting_players={card.object_id: actor}, replacement_selections=replacements,
    )
    return tuple(value.ref for value in moved if value.zone == "battlefield")

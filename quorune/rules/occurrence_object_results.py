from __future__ import annotations

"""Runtime minimum and event compatibility for sealed occurrence results."""

from collections.abc import Mapping
from ..block_characteristic_model import BLOCK_CHARACTERISTIC_CONDITION
from ..phasing_model import (
    PHASE_OUT_ANY_NUMBER_SET_CAPABILITY, PHASE_OUT_CAPABILITY, PHASE_OUT_OPERATION,
    PHASE_OUT_TARGET_SET_CAPABILITY, PHASE_OUT_TARGET_SET_SUBJECT, PHASING_CAPABILITY,
    any_number_phase_out_schema, validate_phase_out_effect,
)
from .event_subscriptions import FixedEventSubscriptionSet
from .tap_state_capability_shapes import event_next_untap_node_capabilities


def _permanent_receiving_condition(value):
    if not isinstance(value,Mapping):return False
    if set(value)=={'field','op','value'}:
        return value=={'field':'target_kind','op':'eq','value':'permanent'}
    if set(value)=={'all'} and isinstance(value['all'],(list,tuple)):
        return any(_permanent_receiving_condition(child) for child in value['all'])
    if set(value)=={'any'} and isinstance(value['any'],(list,tuple)) and value['any']:
        return all(_permanent_receiving_condition(child) for child in value['any'])
    return False


def _permanent_occurrence_subscriptions(program):
    condition=getattr(program,'event_condition',None)
    subscriptions=FixedEventSubscriptionSet.from_condition(condition)
    values=tuple((child.event,child.condition) for child in subscriptions.subscriptions) if subscriptions else ((program.event,condition),)
    for event,condition in values:
        event=event.removesuffix('.self')
        if event in {'permanent.tap','permanent.untap'}:continue
        if event not in {'damage.received','combat.damage.received'} or not _permanent_receiving_condition(condition):return False
    return True


_PHASE_OUT_CREATURE = {"zones": ["battlefield"], "categories": ["permanent"],
                       "count": 1, "types_any": ["creature"]}


def _announced_phase_out_capabilities(target_schema):
    from ..query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY
    from ..replacement.immutable import FrozenMap
    from ..target_cardinality import ANNOUNCED_TARGET_SET_CAPABILITY, AnnouncedTargetCardinality
    from .permanent_predicate_capability_shapes import direct_target_predicate_capabilities

    schema = {key: value for key, value in _PHASE_OUT_CREATURE.items() if key != "count"}
    schema["count_parameter"] = AnnouncedTargetCardinality(selection="up_to").to_dict()
    if not isinstance(target_schema, Mapping) or FrozenMap(target_schema) != FrozenMap(schema):
        return ()
    try:
        AnnouncedTargetCardinality.from_dict(target_schema["count_parameter"])
    except (TypeError, ValueError):
        return ()
    return (
        PHASE_OUT_TARGET_SET_CAPABILITY, PHASE_OUT_CAPABILITY, PHASING_CAPABILITY,
        ANNOUNCED_TARGET_SET_CAPABILITY, DECLARED_AMOUNT_CAPABILITY, "target.revalidate_resolution",
        *direct_target_predicate_capabilities(_PHASE_OUT_CREATURE),
    )



def _any_number_phase_out_capabilities(target_schema):
    from ..replacement.immutable import FrozenMap
    from ..target_cardinality import ANY_NUMBER_TARGET_CAPABILITY, AnnouncedTargetCardinality
    from .permanent_predicate_capability_shapes import direct_target_predicate_capabilities

    if not isinstance(target_schema, Mapping):
        return ()
    scopes = [any_number_phase_out_schema(other_creatures=other) for other in (False, True)]
    if not any(FrozenMap(target_schema) == FrozenMap(scope) for scope in scopes):
        return ()
    try:
        AnnouncedTargetCardinality.from_dict(target_schema["count_parameter"])
    except (TypeError, ValueError):
        return ()
    singular = {key: value for key, value in target_schema.items() if key != "count_parameter"}
    singular["count"] = 1
    return (
        PHASE_OUT_ANY_NUMBER_SET_CAPABILITY, PHASE_OUT_CAPABILITY, PHASING_CAPABILITY,
        ANY_NUMBER_TARGET_CAPABILITY, "target.revalidate_resolution",
        *direct_target_predicate_capabilities(singular),
    )


def phase_out_node_capabilities(effects, target_schema, *, cost_schema=None):
    if len(effects) != 1:
        return ()
    effect = effects[0]
    try:
        subject = validate_phase_out_effect(effect)
    except ValueError:
        return ()
    if subject == PHASE_OUT_TARGET_SET_SUBJECT:
        from .casting_additional_cost_groups import fixed_additional_cost_node_capabilities

        if effect["schema_version"] == 3:
            owned = _any_number_phase_out_capabilities(target_schema)
        else:
            owned = _announced_phase_out_capabilities(target_schema)
        return (*owned, *fixed_additional_cost_node_capabilities(cost_schema=cost_schema)) if owned else ()
    required = (PHASE_OUT_CAPABILITY, PHASING_CAPABILITY)
    from ..phasing_model import CONTROLLED_LAND_PHASE_OUT_SUBJECT

    if subject == CONTROLLED_LAND_PHASE_OUT_SUBJECT:
        return (*required, "trigger.effect.fixed_event", "trigger.placement.apnap") if target_schema is None else ()
    if subject == "source":
        return required if target_schema is None else ()
    if (effect["card"] != "$target.0" or not isinstance(target_schema, Mapping)
            or type(target_schema.get("count")) is not int):
        return ()
    from ..replacement.immutable import FrozenMap
    allowed = (_PHASE_OUT_CREATURE, {**_PHASE_OUT_CREATURE, "controller_relation": "you"},
               {**_PHASE_OUT_CREATURE, "controller_relation": "opponent"},
               {**_PHASE_OUT_CREATURE, "types_any": ["artifact", "creature", "land"]})
    if not any(FrozenMap(target_schema) == FrozenMap(schema) for schema in allowed):
        return ()
    from .permanent_predicate_capability_shapes import direct_target_predicate_capabilities
    return (*required, "target.revalidate_resolution", *direct_target_predicate_capabilities(target_schema))


def phase_out_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effect_operations
    if PHASE_OUT_OPERATION not in nested_effect_operations(program.effects):
        return ()
    required = phase_out_node_capabilities(program.effects, program.target_schema)
    if (not required or program.active_zone not in ("battlefield", "stack")
            or (program.effects[0]["subject"] == "source" and program.active_zone != "battlefield")):
        raise ValueError("Phase out requires one complete battlefield-source or single-target result")
    subject = program.effects[0]["subject"]
    from ..phasing_model import CONTROLLED_LAND_PHASE_OUT_SUBJECT, controlled_land_set_phase_out_condition

    if subject == CONTROLLED_LAND_PHASE_OUT_SUBJECT and (
        (program.event, program.active_zone) != ("step.begin", "battlefield")
        or program.cost_schema is not None or program.handlers
        or program.event_condition != controlled_land_set_phase_out_condition()
    ):
        raise ValueError("Land set phasing requires the original controller's upkeep")
    if subject == PHASE_OUT_TARGET_SET_SUBJECT:
        effect = program.effects[0]
        entry_targets = effect["schema_version"] == 3 and program.target_schema.get("source_exclusion") is True
        if entry_targets:
            valid = program.event == "permanent.enter.self" and program.active_zone == "battlefield"
        else:
            valid = program.event == "resolve" and program.active_zone == "stack" and program.ability_id.startswith("spell:")
        if not valid:
            raise ValueError("The phasing target set requires its printed spell or entry scope")
    return required


def is_closed_phase_out_program(program):
    if len(program.effects) != 1 or program.effects[0].get("op") != PHASE_OUT_OPERATION:
        return False
    try:
        required = phase_out_runtime_dependencies(program)
    except ValueError:
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def controlled_attacker_delayed_phase_out_runtime_dependencies(program):
    from ..delayed_object_model import (
        CONTROLLED_ATTACKER_PHASE_OUT_VERSION, RESOLVE_OBJECT_ACTION, SCHEDULE_OBJECT_ACTION,
        controlled_attacker_delayed_phase_out_capabilities, controlled_attacker_delayed_phase_out_effect,
        validate_delayed_object_instruction,
    )
    from .effect_shape_walkers import nested_effects

    if not any(effect.get("op") in (SCHEDULE_OBJECT_ACTION, RESOLVE_OBJECT_ACTION)
               and (effect.get("schema_version") == CONTROLLED_ATTACKER_PHASE_OUT_VERSION
                    or effect.get("action") == "phase_out")
               for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("creature.attacks", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or "one_or_more_event_batch" in program.coverage or len(program.effects) != 1
            or program.event_condition != {"field": "controller", "op": "eq", "value": "$source.controller"}
            or program.effects[0] != controlled_attacker_delayed_phase_out_effect()):
        raise ValueError("Delayed phasing requires its exact controlled attack and end-of-combat action")
    validate_delayed_object_instruction(program.effects[0], compiled=True)
    return controlled_attacker_delayed_phase_out_capabilities()


def controlled_combat_dealer_condition():
    return {"all": [
        {"field": "source_controller", "op": "eq", "value": "$source.controller"},
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
    ]}


def _controlled_combat_dealer_blink_dependencies(program):
    from ..replacement.immutable import FrozenMap
    from .fixed_effect_clause_shapes import fixed_optional_effect_node_capabilities
    from .effect_shape_walkers import nested_effects

    linked = [
        effect for effect in nested_effects(program.effects)
        if effect.get("op") == "linked_exile_return" and effect.get("cards") == "$event.permanent_object"
    ]
    if not linked:
        return ()
    effects = program.effects
    if (
        (program.event, program.active_zone) != ("damage.dealt", "battlefield")
        or program.target_schema is not None or program.cost_schema is not None or program.handlers
        or len(linked) != 1 or len(effects) != 1 or effects[0].get("op") != "offer_optional_effect"
        or FrozenMap(program.event_condition or {}) != FrozenMap(controlled_combat_dealer_condition())
    ):
        raise ValueError("Optional combat dealer return requires its exact sealed untargeted damage occurrence")
    required = fixed_optional_effect_node_capabilities(
        effects=effects, target_schema=None, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Optional combat dealer return requires its closed immediate owner-return pair")
    return (*required, "trigger.event.normalized_damage", "trigger.effect.fixed_event", "trigger.placement.apnap")


def occurrence_object_runtime_dependencies(program):
    from .mill_capability_shapes import attacker_controller_mill_program_dependencies

    if required := attacker_controller_mill_program_dependencies(program):
        return required
    from .trigger_source_information import untapped_damage_draw_runtime_dependencies

    if draw := untapped_damage_draw_runtime_dependencies(program):
        return draw

    if required := controlled_attacker_delayed_phase_out_runtime_dependencies(program):
        return required
    from .attachment_action_capability_shapes import targeted_equipment_attachment_runtime_dependencies
    from .attachment_actions import entry_equipment_attachment_runtime_dependencies
    from ..event_card_return import event_library_move_runtime_dependencies

    if required := (entry_equipment_attachment_runtime_dependencies(program)
                    or targeted_equipment_attachment_runtime_dependencies(program)
                    or event_library_move_runtime_dependencies(program)):
        return required
    from .creature_power_damage_capability_shapes import occurrence_power_damage_runtime_dependencies
    if required := occurrence_power_damage_runtime_dependencies(program):
        return required
    if required := phase_out_runtime_dependencies(program):
        return required
    from .combat_removal_shapes import combat_removal_runtime_dependencies
    combat_removal = combat_removal_runtime_dependencies(program)
    if combat_removal:
        return combat_removal
    from .damage_permanent_recipients import damage_permanent_runtime_dependencies
    damaged=damage_permanent_runtime_dependencies(program)
    if damaged:return damaged
    def counterpart(value):
        if isinstance(value, Mapping):return any(counterpart(child) for child in value.values())
        if isinstance(value, (list, tuple)):return any(counterpart(child) for child in value)
        return value == '$event.block_counterpart'
    if counterpart(program.effects):
        from .node_capability_shapes import fixed_source_characteristics_node_capabilities
        from .damage_capability_shapes import fixed_damage_node_capabilities
        from ..delayed_object_model import delayed_object_node_capabilities
        subscriptions = FixedEventSubscriptionSet.from_condition(program.event_condition)
        pairs=tuple((child.event,child.condition) for child in subscriptions.subscriptions) if subscriptions else ((program.event,program.event_condition),)
        def guarantees(condition, field, operation, value):
            if not isinstance(condition, Mapping):return False
            if condition.get('field') == field and condition.get('op') == operation and condition.get('value') == value:
                return True
            if isinstance(condition.get('all'), (list, tuple)):
                return any(guarantees(child, field, operation, value) for child in condition['all'])
            return False
        if (program.event not in {'creature.blocks','creature.becomes_blocked_by'} or program.active_zone != 'battlefield'
                or program.target_schema is not None or not pairs
                or {event for event,_condition in pairs} not in ({'creature.blocks'},{'creature.becomes_blocked_by'},{'creature.blocks','creature.becomes_blocked_by'})
                or any(not guarantees(condition,'card','eq','$source.ref') for _event,condition in pairs)
                or any(event=='creature.blocks' and not guarantees(condition,'blocked_attacker','truthy',True) for event,condition in pairs)):
            raise ValueError('Block counterpart requires the closed source-specific per-creature subscriptions')
        required = (fixed_source_characteristics_node_capabilities(effects=program.effects,
            target_schema=None, mechanic_ids=program.coverage)
            or fixed_damage_node_capabilities(effects=program.effects, target_schema=None, mechanic_ids=program.coverage)
            or delayed_object_node_capabilities(effects=program.effects, target_schema=None, mechanic_ids=program.coverage))
        if not required:
            required=block_counterpart_zone_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
        if not required:
            required = event_next_untap_node_capabilities(effects=program.effects, target_schema=None, mechanic_ids=program.coverage)
            if required:
                from .block_characteristics import qualified_block_runtime_dependencies
                if any(not guarantees(condition, BLOCK_CHARACTERISTIC_CONDITION, 'eq', True) for _event, condition in pairs):
                    raise ValueError('Next-untap block counterparts require sealed counterpart characteristics')
                required = (*required, *qualified_block_runtime_dependencies(program))
        if not required:
            raise ValueError('Block counterpart requires one independently closed untargeted result')
        return tuple(sorted(set(required) | {'trigger.event.block_pair_counterpart','trigger.event.normalized_public_action'}
            | ({'trigger.subscription.fixed_multi_event'} if subscriptions else set())))
    attached=any(e.get('op')=='destroy' and isinstance(e.get('card'),Mapping) and e['card'].get('kind')=='source_attachment' for e in program.effects)
    if attached:
        from ..attachment_references import AttachmentReferenceSpec
        if program.target_schema is not None or len(program.effects)!=1 or set(program.effects[0])!={'op','card'}:
            raise ValueError('Attached destruction requires one closed untargeted relation')
        AttachmentReferenceSpec.from_dict(program.effects[0]['card'])
        return ('permanent.destroy.effect','attachment.reference.current_or_lki')
    def contains(value):
        if isinstance(value,Mapping):return any(contains(v) for v in value.values())
        if isinstance(value,(list,tuple)):return any(contains(v) for v in value)
        return value=='$event.permanent_object'
    controller=any(isinstance(e.get(field),Mapping) and e[field].get('kind')=='occurrence_controller_information'
        for e in program.effects for field in ('target','player'))
    if controller:
        if program.event == 'creature.attacks':
            from .event_player_result_shapes import attack_occurrence_controller_runtime_dependencies
            required = attack_occurrence_controller_runtime_dependencies(program)
            if not required:
                raise ValueError('Attack controller information requires a closed life instruction')
            return required
        if program.event == 'creature.blocks':
            from .event_player_result_shapes import blocking_controller_runtime_dependencies
            required = blocking_controller_runtime_dependencies(program)
            if not required:
                raise ValueError('Blocking controller information requires one closed untargeted result')
            return required
        from ..received_damage_model import RECEIVED_DAMAGE_EVENTS
        from ..target_controller_information import OCCURRENCE_CONTROLLER_ENTRY_EVENTS
        if program.event in OCCURRENCE_CONTROLLER_ENTRY_EVENTS | RECEIVED_DAMAGE_EVENTS:
            from .event_player_result_shapes import event_player_damage_runtime_dependencies
            required = event_player_damage_runtime_dependencies(program) if len(program.effects) == 1 else ()
            if not required:
                raise ValueError('Occurrence controller information requires one closed untargeted damage result')
            return required
        if len(program.effects)==1 and program.effects[0].get('op') in {'life','lose_life','mill','damage'}:
            from .tap_state_event_capability_shapes import tap_state_event_player_node_capabilities
            required=tap_state_event_player_node_capabilities(effects=program.effects,target_schema=program.target_schema,
                mechanic_ids=program.coverage)
        else:required=occurrence_object_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=('cr-122-counters',))
        if program.event not in {'permanent.tap','permanent.untap'} or not required:
            raise ValueError('Occurrence controller result requires a closed untargeted tap-state instruction')
        return required
    from .optional_counter_capability_shapes import (
        opponent_damage_source_counter_runtime_dependencies, recipient_damage_source_counter_runtime_dependencies,
    )

    if counters := (recipient_damage_source_counter_runtime_dependencies(program)
                    or opponent_damage_source_counter_runtime_dependencies(program)):
        return counters
    if dealer := _damage_dealer_result_runtime_dependencies(program):
        return dealer
    if blink := _controlled_combat_dealer_blink_dependencies(program):
        return blink
    if not contains(program.effects):return ()
    if program.event=='creature.attacks':
        from .referenced_damage_shapes import referenced_damage_node_capabilities,referenced_damage_runtime_dependencies
        if referenced_damage_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=('cr-120-damage',)):
            return referenced_damage_runtime_dependencies(program)
        from .attack_event_results import attack_event_characteristic_capabilities
        required=attack_event_characteristic_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
        if not required:raise ValueError('Attack occurrence characteristics require one closed untargeted result')
        return required
    prohibited=len(program.effects)==1 and program.effects[0].get('regeneration_prohibited') is True
    expected={'op':'destroy','card':'$event.permanent_object',**({'regeneration_prohibited':True} if prohibited else {})}
    if (not _permanent_occurrence_subscriptions(program) or program.target_schema is not None
            or len(program.effects)!=1 or program.effects[0]!=expected):
        raise ValueError('Occurrence destruction requires one sealed untargeted permanent incarnation')
    return ('permanent.destroy.effect','resolution.event_object.fixed_result',*(('permanent.destroy.regeneration_prohibition',) if prohibited else ()))


DAMAGE_DEALER_RESULT_TEMPLATE = "fixed-typed-effect-public-damage-dealer-response-v1"
DAMAGE_DEALER_RESULT_VARIANTS = ("damage_creature_destroy", "damage_permanent_return", "combat_damage_creature_destroy_token")


def damage_dealer_result_condition(variant):
    if variant not in DAMAGE_DEALER_RESULT_VARIANTS:
        raise ValueError("Unknown damage-dealer result role")
    conditions = [
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "target", "op": "eq", "value": "$source.controller"},
        {"field": "source_zone", "op": "eq", "value": "battlefield"},
    ]
    if variant != "damage_permanent_return":
        conditions.append({"field": "source_types", "op": "contains_any", "value": ["creature"]})
    if variant == "combat_damage_creature_destroy_token":
        conditions.append({"field": "combat", "op": "truthy", "value": True})
    return {"all": conditions}


def damage_dealer_result_effects(variant):
    if variant not in DAMAGE_DEALER_RESULT_VARIANTS:
        raise ValueError("Unknown damage-dealer result role")
    result = {"op": "bounce" if variant == "damage_permanent_return" else "destroy",
              "card": "$event.permanent_object"}
    if variant != "combat_damage_creature_destroy_token":
        return (result,)
    return (result, {
        "op": "create_token", "controller": "$controller", "name": "Spirit Token", "quantity": 1,
        "characteristics": {"type_line": "Token Creature — Spirit", "literal_token_name_version": 1,
                            "colors": ["W", "B"], "power": "1", "toughness": "1", "keywords": ["Flying"]},
    })


def _damage_dealer_result_variant(effects):
    from ..replacement.immutable import thaw_value
    from ..util import stable_json

    if not effects or effects[0].get("card") != "$event.permanent_object":
        return None
    observed = stable_json(thaw_value(effects))
    return next((variant for variant in DAMAGE_DEALER_RESULT_VARIANTS
                 if observed == stable_json(damage_dealer_result_effects(variant))), None)


def _damage_dealer_result_dependencies(variant):
    operation = "permanent.return.owner_hand" if variant == "damage_permanent_return" else "permanent.destroy.effect"
    token = ("token.creation.fixed_definition", "combat.block.flying") if variant == "combat_damage_creature_destroy_token" else ()
    return (operation, "resolution.event_object.fixed_result", "trigger.event.normalized_damage", *token)


def _damage_dealer_result_runtime_dependencies(program):
    from ..replacement.immutable import thaw_value

    if DAMAGE_DEALER_RESULT_TEMPLATE not in str(program.provenance.get("template_id") or "").split("+"):
        return ()
    variant = _damage_dealer_result_variant(program.effects)
    if (variant is None or (program.event, program.active_zone) != ("damage.dealt", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or thaw_value(program.event_condition) != damage_dealer_result_condition(variant)):
        raise ValueError("Damage-dealer response requires the original untargeted damage and complete result")
    return _damage_dealer_result_dependencies(variant)


def occurrence_object_node_capabilities(*,effects,target_schema,mechanic_ids):
    if target_schema is None and "trigger-event-normalized-damage" in mechanic_ids:
        variant = _damage_dealer_result_variant(effects)
        if variant is not None:
            return _damage_dealer_result_dependencies(variant)
    if target_schema is not None or len(effects)!=1:return ()
    effect=effects[0];mechanics=set(mechanic_ids)
    expected={'op':'place_player_counters','subjects':'target','counter':'poison','amount':1,'source':'$source',
        'target':{'kind':'occurrence_controller_information','schema_version':1}}
    if effect==expected and 'cr-122-counters' in mechanics and type(effect.get('amount')) is int and type(effect['target'].get('schema_version')) is int:
        return ('resolution.event_object.fixed_result','counter.producer.fixed_player_effect','resolution.target_controller_information')
    return ()


def block_counterpart_zone_capabilities(*,effects,target_schema,mechanic_ids):
    if target_schema is not None or len(effects)!=1:return ()
    effect=effects[0]
    if effect=={'op':'bounce','card':'$event.block_counterpart'} and 'return-to-owner-hand'in mechanic_ids:
        return ('permanent.return.owner_hand','trigger.event.block_pair_counterpart')
    if 'destroy'not in mechanic_ids:return ()
    for prohibited in (False,True):
        expected={'op':'destroy','card':'$event.block_counterpart',**({'regeneration_prohibited':True} if prohibited else {})}
        if effect==expected:return ('permanent.destroy.effect','trigger.event.block_pair_counterpart',
            *(('permanent.destroy.regeneration_prohibition',) if prohibited else ()))
    return ()

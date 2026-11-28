"""Read sealed declaration snapshots; commit no game state or trigger batches."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import replace

from ..affected_permanents import AffectedPermanentSetSpec
from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec
from ..attack_batch_model import (ATTACK_BATCH_CONDITION,ATTACK_BATCH_CONTEXT,QualifiedAttackBatchSpec,
    QUALIFIED_ATTACK_BATCH_CAPABILITY,attack_batch_conditions)
from ..attack_batch_model import (
    ATTACHED_PLAYER_ATTACK_VARIANT,
    DECLARED_ATTACK_POWER_CAPABILITY,
    DECLARED_ATTACK_POWER_COVERAGE,
    DECLARED_ATTACK_POWER_FIELD,
    DIRECT_PLAYER_ATTACK_CAPABILITY,
    DIRECT_PLAYER_ATTACK_EVENT,
    DIRECT_PLAYER_ATTACK_VARIANTS,
    declared_attack_power_condition,
    direct_player_attack_condition,
)
from ..attack_transition_model import AttackRecipientKind, AttackTransitionEvent
from ..fixed_token_production import gold_token_effect
from ..initiative_model import TAKE_INITIATIVE_CAPABILITY
from ..qualified_attack_model import ATTACK_FACT_PREFIX,ATTACK_FACT_COLLECTIONS,ATTACK_FACT_BOOLEANS,ATTACK_FACT_NUMBERS
from ..object_query import ObjectQueryResult,object_matches_query
from ..replacement.immutable import FrozenMap, thaw_value


DECLARED_ATTACK_COUNTER_FIELD = "declared_attack_counter_members"
DECLARED_ATTACK_COUNTER_REFERENCE = "$event.declared_attack_counter_members"
DECLARED_ATTACK_COUNTER_CAPABILITY = "counter.producer.declared_attack_group"
ATTACKING_OPPONENT_TOKEN_FIELD = "attacking_opponent_token_version"
ATTACKING_OPPONENT_TOKEN_CAPABILITY = "token.creation.attacked_player_attacking_opponent"


def attacking_opponent_token_effects():
    return (gold_token_effect(), {
        **gold_token_effect(), ATTACKING_OPPONENT_TOKEN_FIELD: 1,
    })


def attacking_opponent_token_effects_are_closed(effects):
    return (len(effects) == 2
            and all(isinstance(effect, Mapping) for effect in effects)
            and type(effects[1].get(ATTACKING_OPPONENT_TOKEN_FIELD)) is int
            and all(type(effect.get("quantity")) is int for effect in effects)
            and tuple(thaw_value(effect) for effect in effects) == attacking_opponent_token_effects())


def bind_attacking_opponent_token(host, item, effect):
    if ATTACKING_OPPONENT_TOKEN_FIELD not in effect:
        return effect
    from .casting.public_conditions import current_creature_attacking_controller

    context = item.context
    attacker, defender = context.get("player"), context.get("defender")
    if (item.kind != "triggered_ability" or context.get("event") != DIRECT_PLAYER_ATTACK_EVENT
            or context.get("currently_resolving") is not True
            or attacker not in host.seats or defender not in host.seats or attacker == defender
            or not attacking_opponent_token_effects_are_closed((gold_token_effect(), effect))):
        raise ValueError("Attacking-opponent creation needs its original direct-player attack")
    if (attacker == item.controller or attacker != host.state.active_player
            or attacker not in host.active_seats or defender not in host.active_seats
            or not current_creature_attacking_controller(host, defender)):
        return None
    return {**gold_token_effect(), "controller": attacker}


def declared_attack_counter_set():
    return AffectedPermanentSetSpec(query=ObjectQuerySpec(zones=("battlefield",)))


def declared_attack_counter_effect():
    return {"op": "place_counters_on_set", "source": "$source",
            "set": {"kind": "declared_attack_group", "schema_version": 1},
            "counter": "+1/+1", "amount": 1, DECLARED_ATTACK_COUNTER_FIELD: DECLARED_ATTACK_COUNTER_REFERENCE}


def resolved_declared_attack_counter_effect(effect, *, source_ref):
    if not isinstance(effect, Mapping):
        raise ValueError("Declared attack counters require one versioned effect")
    raw = thaw_value(effect)
    members = raw.get(DECLARED_ATTACK_COUNTER_FIELD)
    raw[DECLARED_ATTACK_COUNTER_FIELD] = DECLARED_ATTACK_COUNTER_REFERENCE
    expected = {**declared_attack_counter_effect(), "source": source_ref}
    if (raw != expected or type(raw["amount"]) is not int
            or type(raw["set"]["schema_version"]) is not int):
        raise ValueError("Declared attack counters lost their exact compiled group")
    return validate_declared_attack_counter_members(members)


def declared_attack_counter_effect_capabilities(effect):
    if (isinstance(effect, Mapping) and thaw_value(effect) == declared_attack_counter_effect()
            and type(effect["amount"]) is int and type(effect["set"]["schema_version"]) is int):
        return ("counter.producer.fixed_permanent_set_effect", DECLARED_ATTACK_COUNTER_CAPABILITY)
    return ()


def declared_attack_counter_condition(condition):
    specs = attack_batch_conditions(condition)
    if len(specs) != 1:
        raise ValueError("Declared attack counters require one sealed attack predicate")
    spec = specs[0]
    types = spec.query.subtypes_all
    if (spec.grouping != "declaration" or spec.minimum != 1 or spec.exclude_source
            or len(types) != 1 or canonical_creature_subtype(types[0]) != types[0]
            or spec.query != ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), subtypes_all=types)):
        raise ValueError("Declared attack counters require one fixed matching subtype")
    canonical = {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": ATTACK_BATCH_CONDITION, "op": "gte", "value": 1, "attack_batch": spec.to_dict()},
    ]}
    if thaw_value(condition) != canonical:
        raise ValueError("Declared attack counters require the original controller relation")
    return canonical["all"][1]


def declared_attack_counter_context(program, source, context):
    if len(program.effects) != 1 or not declared_attack_counter_effect_capabilities(program.effects[0]):
        return {}
    condition = declared_attack_counter_condition(program.event_condition)
    members = qualified_attack_incarnations(source, condition, context)
    if not members:
        raise ValueError("Declared attack counter trigger lost all matching declaration members")
    return {DECLARED_ATTACK_COUNTER_FIELD: members}


def seal_attack_declaration(event,characteristic_contexts):
    participants={p.object_id:p for p in event.participants}
    return tuple(FrozenMap({'reference':participants[a.attacker_object_id].reference,
        'logical_object_id':participants[a.attacker_object_id].logical_object_id,
        'controller':participants[a.attacker_object_id].controller,
        'recipient_kind':a.recipient.kind.value,'recipient':a.recipient.reference,
        'recipient_logical_object_id':a.recipient.logical_object_id,
        'defending_player':a.recipient.defending_player,
        'characteristics':dict(characteristic_contexts[a.attacker_object_id])}) for a in event.assignments)


def direct_player_attack_facts(event: AttackTransitionEvent, initiative_holder: str | None) -> tuple[FrozenMap, ...]:
    counts = Counter(assignment.recipient.reference for assignment in event.assignments
                     if assignment.recipient.kind is AttackRecipientKind.PLAYER)
    return tuple(FrozenMap({
        "player": event.active_player, "defender": defender, "attacker_count": count,
        "defender_has_initiative": defender == initiative_holder,
    }) for defender, count in sorted(counts.items()))


def _sealed_rows(context):
    rows=context.get(ATTACK_BATCH_CONTEXT)
    if not isinstance(rows,(tuple,list)) or not rows:raise ValueError('Qualified attack batch requires sealed declaration facts')
    seen=set();identities=set();controllers=set()
    for row in rows:
        if not isinstance(row,Mapping) or set(row)!={'reference','logical_object_id','controller','recipient_kind','recipient','recipient_logical_object_id','defending_player','characteristics'}:
            raise ValueError('Malformed sealed attacker')
        if any(type(row[k]) is not str or not row[k] for k in ('reference','logical_object_id','controller','recipient','defending_player')):
            raise ValueError('Sealed attacker identities must be nonempty strings')
        if row['reference'] in seen:raise ValueError('A declared attacker occurs once')
        seen.add(row['reference'])
        if row['logical_object_id'] in identities:raise ValueError('An attacker incarnation occurs once')
        identities.add(row['logical_object_id']);controllers.add(row['controller'])
        if row['recipient_kind'] not in {'player','planeswalker','battle'}:raise ValueError('Unknown attack recipient kind')
        if row['recipient_kind']=='player' and row['recipient']!=row['defending_player']:
            raise ValueError('Attacked player and defending player differ')
        identity=row['recipient_logical_object_id']
        if row['recipient_kind']=='player' and identity is not None or row['recipient_kind']!='player' and (type(identity) is not str or not identity):
            raise ValueError('Attack recipient incarnation is inconsistent with its kind')
        facts=row['characteristics']
        expected={'version',*ATTACK_FACT_COLLECTIONS,*ATTACK_FACT_BOOLEANS,*ATTACK_FACT_NUMBERS}
        if not isinstance(facts,Mapping) or set(facts)!={ATTACK_FACT_PREFIX+k for k in expected}:
            raise ValueError('Sealed attacker characteristic fields changed')
        if type(facts[ATTACK_FACT_PREFIX+'version']) is not int or facts[ATTACK_FACT_PREFIX+'version']!=1:raise ValueError('Unknown sealed attacker version')
        for name in ATTACK_FACT_COLLECTIONS:
            values=facts[ATTACK_FACT_PREFIX+name]
            if not isinstance(values,(tuple,list)) or any(type(v) is not str or not v for v in values) or len(values)!=len(set(values)):
                raise ValueError('Sealed attacker collections must be distinct strings')
        if any(type(facts[ATTACK_FACT_PREFIX+name]) is not bool for name in ATTACK_FACT_BOOLEANS):raise ValueError('Sealed attacker state must be boolean')
        if 'creature' not in facts[ATTACK_FACT_PREFIX+'types'] or not facts[ATTACK_FACT_PREFIX+'attacking']:
            raise ValueError('Sealed declaration members must be attacking creatures')
        for name in ATTACK_FACT_NUMBERS:
            value=facts[ATTACK_FACT_PREFIX+name]
            if value is not None and type(value) is not int or name=='total_counters' and (type(value) is not int or value<0):
                raise ValueError('Sealed attacker numeric facts changed')
        if len(facts[ATTACK_FACT_PREFIX+'counter_names'])>facts[ATTACK_FACT_PREFIX+'total_counters']:
            raise ValueError('Sealed counter kinds exceed the public total')
    if len(controllers)!=1:raise ValueError('One declaration has one attacking controller')
    return rows


def _row_matches(source,spec,row):
    if row['controller']!=source.controller or spec.exclude_source and row['reference']==source.ref:return False
    f={k.removeprefix(ATTACK_FACT_PREFIX):v for k,v in row['characteristics'].items()}
    state={name:f[name] for name in ATTACK_FACT_BOOLEANS-{'monstrous'}}
    candidate=ObjectQueryResult(row['logical_object_id'],row['reference'],'Declared attacker',row['controller'],row['controller'],'battlefield',
        types=tuple(f['types']),subtypes=tuple(f['subtypes']),supertypes=tuple(f['supertypes']),colors=tuple(f['colors']),keywords=tuple(f['keywords']),
        counters={name:1 for name in f['counter_names']},effective_power=f['power'],effective_toughness=f['toughness'],
        monstrous_value=1 if f['monstrous'] else None,logical_object_id=row['logical_object_id'],**state)
    query=spec.query
    if query.state_predicate is not None and query.state_predicate.minimum_total_counter_count is not None:
        if f['total_counters']<query.state_predicate.minimum_total_counter_count:return False
        state=query.state_predicate
        remaining=(state.entered_this_turn or state.counter_name is not None or any(getattr(state,name) is not None
            for name in ('tapped','attacking','blocking','enchanted','equipped','modified','monstrous')))
        query=replace(query,state_predicate=replace(state,minimum_total_counter_count=None) if remaining else None)
    return object_matches_query(candidate,query)


def _qualified_attack_matching_rows(source, condition, context):
    spec = QualifiedAttackBatchSpec.from_dict(condition['attack_batch'])
    rows = _sealed_rows(context)
    allowed = {'declaration': {'player', 'planeswalker', 'battle'}, 'player': {'player'},
               'player_or_planeswalker': {'player', 'planeswalker'}}[spec.grouping]
    occurrence = next((row for row in rows if row['reference'] == context.get('card')), None)
    if occurrence is None:
        raise ValueError('Attack occurrence is absent from its sealed declaration')
    if context.get('controller', occurrence['controller']) != occurrence['controller']:
        raise ValueError('Attack occurrence controller differs from its sealed declaration')
    if occurrence['recipient_kind'] not in allowed:
        return
    expected = (context.get('attacked_recipient_kind'), context.get('defender'))
    if spec.grouping != 'declaration' and (occurrence['recipient_kind'], occurrence['recipient']) != expected:
        raise ValueError('Attack occurrence recipient differs from its sealed declaration')
    if not _row_matches(source, spec, occurrence):
        return
    for row in rows:
        if row['controller'] != source.controller or row['recipient_kind'] not in allowed:
            continue
        if spec.grouping != 'declaration' and (row['recipient_kind'], row['recipient']) != expected:
            continue
        if _row_matches(source, spec, row):
            yield row


def qualified_attack_match_count(source, condition, context):
    return sum(1 for _ in _qualified_attack_matching_rows(source, condition, context))


def qualified_attack_incarnations(source, condition, context):
    """Retain the same declaration membership used to qualify the trigger."""
    return tuple((row['reference'], row['logical_object_id'])
                 for row in _qualified_attack_matching_rows(source, condition, context))


def validate_declared_attack_counter_members(members):
    if not isinstance(members, (tuple, list)) or not members:
        raise ValueError("Attack counters require sealed declaration members")
    references = set()
    incarnations = set()
    normal = []
    for pair in members:
        if (not isinstance(pair, (tuple, list)) or len(pair) != 2
                or any(type(term) is not str or not term for term in pair)):
            raise ValueError("Attack counter member identity is malformed")
        reference, logical = pair
        if reference in references or logical in incarnations:
            raise ValueError("An attack counter member must occur only once")
        references.add(reference)
        incarnations.add(logical)
        normal.append((reference, logical))
    return tuple(normal)


def current_declared_attack_counter_recipients(members, find_public_object):
    """Resolve only original attacking incarnations which remain available."""
    selected = []
    for reference, logical in validate_declared_attack_counter_members(members):
        current = find_public_object(reference)
        if (current is not None and current.zone == "battlefield"
                and not current.phased_out and current.logical_object_id == logical):
            if current.ref != reference:
                raise ValueError("The attack counter binding disagrees with the original reference")
            selected.append(current)
    return tuple(selected)


def attack_batch_aggregation_scope(program,context):
    specs=attack_batch_conditions(program.event_condition)
    if not specs or specs[0].grouping=='declaration':return {}
    _sealed_rows(context)
    kind=context.get('attacked_recipient_kind');reference=context.get('defender')
    if kind not in {'player','planeswalker'} or type(reference) is not str or not reference:raise ValueError('Grouped attack lost its recipient')
    row=next((row for row in context[ATTACK_BATCH_CONTEXT] if row['reference']==context.get('card')),None)
    if row is None:raise ValueError('Grouped attack occurrence is absent')
    return {'attacked_recipient_kind':kind,'attacked_recipient':reference,'attacked_recipient_logical_object_id':row['recipient_logical_object_id']}


def declared_attack_power_total(context, *, source_object_id, source_logical_object_id, controller):
    """Read the signed power sum of the original declaration, across all defenders."""
    event = AttackTransitionEvent.from_dict(context.get("attack_transition"))
    participants = {row.object_id: row for row in event.participants}
    source = participants.get(source_object_id)
    assignment = next((row for row in event.assignments if row.attacker_object_id == source_object_id), None)
    if (
        context.get("event_id") != event.transition_id
        or context.get("controller") != controller
        or event.active_player != controller
        or source is None
        or source.logical_object_id != source_logical_object_id
        or source.reference != context.get("card")
        or assignment is None
        or assignment.recipient.reference != context.get("defender")
        or assignment.recipient.defending_player != context.get("defending_player")
        or assignment.recipient.kind.value != context.get("attacked_recipient_kind")
    ):
        raise ValueError("Declared attack power lost the source's original declaration")

    powers = tuple(participants[row.attacker_object_id].power for row in event.assignments)
    if any(power is None for power in powers):
        raise ValueError("Declared attack power requires resolved power for each attacker")
    return sum(powers)


def _declared_attack_power_minimum(condition):
    condition = thaw_value(condition)
    try:
        minimum = condition["all"][1]["value"]
    except (TypeError, IndexError, KeyError) as exc:
        raise ValueError("Declared attack power requires its closed source condition") from exc
    if condition != declared_attack_power_condition(minimum):
        raise ValueError("Declared attack power requires its closed source condition")
    return minimum


def declared_attack_power_recheck(program, context, *, source_object_id, controller):
    minimum = _declared_attack_power_minimum(program.event_condition)
    return declared_attack_power_total(
        context, source_object_id=source_object_id,
        source_logical_object_id=context.get("source_logical_object_id"), controller=controller,
    ) >= minimum


def _mentions_declared_attack_power(value):
    if isinstance(value, Mapping):
        return value.get("field") == DECLARED_ATTACK_POWER_FIELD or any(
            _mentions_declared_attack_power(child) for child in value.values()
        )
    return isinstance(value, (list, tuple)) and any(_mentions_declared_attack_power(child) for child in value)


def _mentions_declared_attack_counter_group(value):
    if isinstance(value, Mapping):
        return (DECLARED_ATTACK_COUNTER_FIELD in value or value.get("kind") == "declared_attack_group"
                or any(_mentions_declared_attack_counter_group(child) for child in value.values()))
    if isinstance(value, (list, tuple)):
        return any(_mentions_declared_attack_counter_group(child) for child in value)
    return type(value) is str and value == DECLARED_ATTACK_COUNTER_REFERENCE


def _optional_attack_batch_strike_dependencies(program):
    from ..attack_batch_model import OPTIONAL_ATTACK_BATCH_STRIKE_CAPABILITY, OPTIONAL_ATTACK_BATCH_STRIKE_COVERAGE
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from .fixed_effect_clause_shapes import fixed_optional_effect_node_capabilities

    if (OPTIONAL_ATTACK_BATCH_STRIKE_CAPABILITY not in program.capability_dependencies
            and OPTIONAL_ATTACK_BATCH_STRIKE_COVERAGE not in program.coverage):
        return ()
    effect = {
        "op": "offer_optional_effect", "player": "$controller",
        "effects": [{"op": "grant_keyword_until_end_of_turn", "card": "$target.0", "keyword": "Double Strike"}],
    }
    target = {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
              "combat_state": "attacking", "count": 1}
    if (program.event != "creature.attacks" or program.active_zone != "battlefield"
            or program.event_condition is not None or program.cost_schema is not None
            or program.handlers or program.trigger_limit is not None or len(program.effects) != 1
            or FrozenMap(program.effects[0]) != FrozenMap(effect) or program.target_schema is None
            or type(program.target_schema.get("count")) is not int or FrozenMap(program.target_schema) != FrozenMap(target)
            or not {CURRENT_ABILITY_FRAGMENT_COVERAGE, "one_or_more_event_batch",
                    OPTIONAL_ATTACK_BATCH_STRIKE_COVERAGE}.issubset(program.coverage)):
        raise ValueError("The optional attacking creature result needs its original coalesced declaration and current target")
    required = fixed_optional_effect_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=("fixed-optional-effect-choice", "cr-611-continuous-effects", "cr-115-targets", "double-strike"),
    )
    if not required:
        raise ValueError("The optional result lost its typed target and double strike authority")
    return (OPTIONAL_ATTACK_BATCH_STRIKE_CAPABILITY, *required)


def qualified_attack_batch_runtime_dependencies(program):
    from .source_counter_attacking_tokens import source_counter_attacking_tokens_program_dependencies

    if required := source_counter_attacking_tokens_program_dependencies(program):
        return required
    from .event_subscriptions import (
        EVENT_SUBSCRIPTIONS_FIELD, FixedEventSubscriptionSet, opponent_attack_draw_cast_runtime_dependencies,
    )
    from .effect_shape_walkers import contains_nested_key

    if optional_strike := _optional_attack_batch_strike_dependencies(program):
        return optional_strike
    condition = program.event_condition
    effects = getattr(program, "effects", ())
    target = getattr(program, "target_schema", None)
    cost = getattr(program, "cost_schema", None)
    handlers = getattr(program, "handlers", ())
    attached_player = direct_player_attack_condition(ATTACHED_PLAYER_ATTACK_VARIANT)
    if (condition == attached_player or contains_nested_key((effects, target, cost, handlers), ATTACKING_OPPONENT_TOKEN_FIELD)
            or ATTACKING_OPPONENT_TOKEN_CAPABILITY in getattr(program, "capability_dependencies", ())):
        if ((program.event, program.active_zone, condition) != (DIRECT_PLAYER_ATTACK_EVENT, "battlefield", attached_player)
                or target is not None or cost is not None or handlers
                or not attacking_opponent_token_effects_are_closed(effects)):
            raise ValueError("Attached-player attack creation requires the original two untargeted instructions")
        return (DIRECT_PLAYER_ATTACK_CAPABILITY, ATTACKING_OPPONENT_TOKEN_CAPABILITY)
    if isinstance(condition, Mapping) and EVENT_SUBSCRIPTIONS_FIELD in condition:
        subscriptions = FixedEventSubscriptionSet.from_condition(condition)
        if subscriptions is not None and any(
            child.event.removesuffix(".self") == DIRECT_PLAYER_ATTACK_EVENT for child in subscriptions.subscriptions
        ):
            if required := opponent_attack_draw_cast_runtime_dependencies(program, subscriptions):
                return required
            raise ValueError("Direct player attack requires one closed event subscription")
    if program.event.removesuffix(".self") == DIRECT_PLAYER_ATTACK_EVENT:
        from .faction_trigger_model import faction_condition
        from .faction_trigger_shapes import faction_branch_for_program

        if thaw_value(condition) == faction_condition("Enclave"):
            faction_branch_for_program(program)
            return (DIRECT_PLAYER_ATTACK_CAPABILITY,)
        for variant in DIRECT_PLAYER_ATTACK_VARIANTS:
            if (program.event == DIRECT_PLAYER_ATTACK_EVENT and program.active_zone == "battlefield"
                    and condition == direct_player_attack_condition(variant)):
                return ((DIRECT_PLAYER_ATTACK_CAPABILITY, TAKE_INITIATIVE_CAPABILITY)
                        if variant == "controller_attacks_initiative_holder" else (DIRECT_PLAYER_ATTACK_CAPABILITY,))
        raise ValueError("Direct player attack requires a closed battlefield subscription")
    specs=attack_batch_conditions(condition)
    if specs and (program.event!='creature.attacks' or 'one_or_more_event_batch' not in program.coverage):
        raise ValueError('Qualified attack batches require declared-attack coalescing')
    if DECLARED_ATTACK_POWER_COVERAGE in program.coverage or _mentions_declared_attack_power(program.event_condition):
        if (
            program.event != "creature.attacks" or program.active_zone != "battlefield"
            or not {"intervening_condition", DECLARED_ATTACK_POWER_COVERAGE} <= set(program.coverage)
            or "one_or_more_event_batch" in program.coverage
        ):
            raise ValueError("Declared attack power requires one source attack with a resolution recheck")
        _declared_attack_power_minimum(program.event_condition)
        return (DECLARED_ATTACK_POWER_CAPABILITY,)
    extras = (target, cost, handlers)
    if _mentions_declared_attack_counter_group(extras):
        raise ValueError("Declared attack counters belong only to the original effect")
    declared = DECLARED_ATTACK_COUNTER_CAPABILITY in getattr(program, "capability_dependencies", ())
    if declared or _mentions_declared_attack_counter_group(effects):
        required = declared_attack_counter_effect_capabilities(effects[0]) if len(effects) == 1 else ()
        if (not required or program.active_zone != "battlefield" or program.event != "creature.attacks"
                or "one_or_more_event_batch" not in program.coverage
                or extras[0] is not None or extras[1] is not None or extras[2]):
            raise ValueError("Declared attack counters need one complete untargeted attack result")
        declared_attack_counter_condition(program.event_condition)
        return (QUALIFIED_ATTACK_BATCH_CAPABILITY, *required)
    return (QUALIFIED_ATTACK_BATCH_CAPABILITY,) if specs else ()

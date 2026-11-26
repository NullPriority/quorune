"""Keep the public stopping occurrence and its later targeted result together."""

from ..errors import GameRuleError
from ..public_quantity_reductions import public_mana_value
from ..reflexive_trigger_model import (
    BOUND_REFLEXIVE_PROGRAM_VERSION, REFLEXIVE_PAYMENT_CAPABILITY, ReflexiveTriggerProgram,
)
from ..reveal_until_library_selection_model import (
    REVEAL_UNTIL_OPERATION, REVEAL_UNTIL_REFLEXIVE_CAPABILITY, REVEAL_UNTIL_REFLEXIVE_VERSION,
    reveal_until_partition,
)
from .reflexive_payment_shapes import reflexive_program_capabilities


def _counter_consequence(amount):
    return ReflexiveTriggerProgram(
        effects=({"op": "place_counters", "card": "$target.0", "counter": "+1/+1",
                  "amount": amount, "source": "$source"},),
        target_schema={"zones": ["battlefield"], "categories": ["permanent"], "count": 1,
                       "types_any": ["creature"], "controller_relation": "you"},
        mechanic_ids=("cr-122-counters", "cr-115-targets"),
        schema_version=BOUND_REFLEXIVE_PROGRAM_VERSION,
    )


def reveal_until_reflexive_dependencies():
    owned = reflexive_program_capabilities(_counter_consequence(1))
    if not owned:
        raise ValueError('The reflexive result needs ordinary target validation and counter placement')
    return (REVEAL_UNTIL_REFLEXIVE_CAPABILITY, REFLEXIVE_PAYMENT_CAPABILITY, 'trigger.placement.apnap', *owned)


def reveal_until_reflexive_runtime_dependencies(program):
    from .reveal_until_library_selection_shapes import reveal_until_library_selection_node_capabilities

    effects = program.effects
    if not any(effect.get('op') == REVEAL_UNTIL_OPERATION
               and effect.get('schema_version') == REVEAL_UNTIL_REFLEXIVE_VERSION for effect in effects):
        return ()
    required = reveal_until_library_selection_node_capabilities(
        effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or (program.event, program.active_zone) != ('resolve', 'stack')
            or getattr(program, 'handlers', ()) or program.cost_schema is not None):
        raise ValueError('The reveal reflexive result requires its complete nontargeted resolving spell')
    return required


def enqueue_reveal_until_reflexive(host, intent):
    from .reflexive_triggers import enqueue_resolving_reflexive

    parent = next((item for item in host.state.stack if item.ref == intent.parent_stack_ref), None)
    if (parent is None or parent.kind not in {'spell', 'spell_copy'} or parent.controller != intent.controller
            or parent.controller not in host.active_seats or parent.context.get('currently_resolving') is not True):
        raise GameRuleError('A reveal reflexive trigger requires its original resolving spell and controller')
    program = host.semantics.get(parent.semantic_key)
    if program is None or not host.semantic_program_is_current_trusted(program):
        raise GameRuleError('A reveal reflexive trigger requires its admitted CardProgram')
    try:
        required = reveal_until_reflexive_runtime_dependencies(program)
        if not required or not set(required) <= set(program.capability_dependencies):
            raise ValueError('The reveal reflexive result lost a required owner')
        effect = {**program.effects[0], 'player': parent.controller}
        query = host._semantic_choice_query(parent.controller, effect=effect, stack_item=parent)
        _, revealed = reveal_until_partition(effect, query)
        if revealed is None or revealed.logical_object_id != intent.revealed_logical_object_id:
            raise ValueError('The stopping creature no longer matches the original library occurrence')
        card = host.state.cards[revealed.object_id]
        if not set(host.seats) <= set(card.revealed_to):
            raise ValueError('The original stopping creature has not been publicly revealed')
        amount = public_mana_value({'mana_value': revealed.mana_value})
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    return enqueue_resolving_reflexive(host, parent, _counter_consequence(amount))

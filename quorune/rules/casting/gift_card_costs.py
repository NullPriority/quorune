"""Use ordinary cast prices to offer supported public Gift declarations."""

from ...gift_card_model import GIFT_CARD_PROMISE_FIELD, GIFT_EXTRA_TURN_COST_FIELD, GiftCardPromise
from ..gift_card_spells import gift_card_runtime_dependencies
from ..gift_fish_spells import gift_fish_runtime_dependencies, gift_fish_target_schema_for_program
from ..gift_counter_spells import gift_counter_runtime_dependencies, gift_counter_target_schema


def _gift_variant(program):
    from ..gift_graveyard_spells import runtime_dependencies

    if runtime_dependencies(program):
        return "graveyard_limits"
    if gift_fish_runtime_dependencies(program):
        return "fish"
    if gift_counter_runtime_dependencies(program):
        return "counter"
    if program is not None and gift_card_runtime_dependencies(program):
        return "extra_turn" if GIFT_EXTRA_TURN_COST_FIELD in program.cost_schema else "card"
    return None


def _gift_target_branch(program, variant, promised):
    if variant == "fish":
        return gift_fish_target_schema_for_program(program, promised)
    if variant == "graveyard_limits":
        from ..gift_graveyard_spells import target_schema_for_program

        return target_schema_for_program(program, promised)
    if variant == "counter":
        return gift_counter_target_schema(promised)
    return None


def expand_gift_card_costs(host, seat, program, options, *, suppress_source_costs):
    if suppress_source_costs or program is None:
        return options
    try:
        variant = _gift_variant(program)
        if variant is None:
            return options
    except ValueError:
        return []
    if not host.semantic_program_is_current_trusted(program):
        return []
    recipients = tuple(player for player in host.active_seats if player != seat)
    result = []
    for option in options:
        result.append(_gift_option(option, None, variant=variant, program=program))
        result.extend(_gift_option(option, recipient, variant=variant, program=program) for recipient in recipients)
    return result


def _gift_option(option, recipient, *, variant, program):
    choice = {**option, GIFT_CARD_PROMISE_FIELD: GiftCardPromise(recipient).to_dict()}
    branch = _gift_target_branch(program, variant, recipient is not None)
    if branch is not None:
        choice["target_schema"] = branch
    if recipient is not None:
        label = {"fish": "a tapped Fish", "extra_turn": "an extra turn"}.get(variant, "a card")
        choice.update(id=f"{option['id']}+gift:{recipient}",
                      label=f"{option.get('label') or 'Cast'} and promise {label} to {recipient}")
    return choice


def validate_gift_commit(host, seat, program, option):
    variant = _gift_variant(program)
    if variant is None:
        if GIFT_CARD_PROMISE_FIELD in option:
            raise ValueError("The spell has no closed Gift declaration")
        return
    if not host.semantic_program_is_current_trusted(program):
        raise ValueError("The spell lost its current Gift authority")
    promise = GiftCardPromise.from_dict(option.get(GIFT_CARD_PROMISE_FIELD))
    if promise.recipient is not None and (promise.recipient == seat or promise.recipient not in host.active_seats):
        raise ValueError("The Gift recipient is no longer an active opponent")
    branch = _gift_target_branch(program, variant, promise.recipient is not None)
    if branch is not None and option.get("target_schema") != branch:
        raise ValueError("The selected target domain differs from the Gift promise")


def gift_card_context(option):
    if GIFT_CARD_PROMISE_FIELD not in option:
        return {}
    promise = GiftCardPromise.from_dict(option[GIFT_CARD_PROMISE_FIELD])
    return {GIFT_CARD_PROMISE_FIELD: promise.to_dict()}


def resolving_spell_gift_promise(host, item):
    """Read an original declaration only at the successful resolution boundary."""
    program = host.semantics.get(item.semantic_key)
    if (
        item.kind not in {"spell", "spell_copy"} or item.context.get("targets_revalidated") is not True
        or not host.state.stack or host.state.stack[-1] is not item
        or _gift_variant(program) is None or not host.semantic_program_is_current_trusted(program)
    ):
        raise ValueError("A resolving Gift requires its original trusted spell and revalidated targets")
    promise = GiftCardPromise.from_dict(item.context.get(GIFT_CARD_PROMISE_FIELD))
    if promise.recipient is not None and promise.recipient not in host.state.players:
        raise ValueError("A resolving Gift lost its original declared recipient")
    return promise

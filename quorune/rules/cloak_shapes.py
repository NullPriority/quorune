"""Closed library Cloak instructions and their complete program envelope."""

from collections.abc import Mapping

CLOAK_OPERATION = "cloak_library_cards"
CLOAK_REMAINDER_OPERATION = "finish_cloak_library_partition"
CLOAK_CAPABILITY = "keyword_action.cloak.library"
CLOAK_PARTITION_CAPABILITY = "keyword_action.cloak.fixed_partition"
CLOAK_MECHANIC = "cloak"


def cloak_library_effect(form: str, player: str = "$controller") -> dict:
    return {"op": CLOAK_OPERATION, "schema_version": 1, "player": player, "form": form}


def validate_cloak_instruction(effect: Mapping, actor: str) -> str:
    if (
        not isinstance(effect, Mapping)
        or set(effect) != {"op", "schema_version", "player", "form"}
        or effect["op"] != CLOAK_OPERATION
        or type(effect["schema_version"]) is not int
        or effect["schema_version"] != 1
        or effect["player"] != actor
        or type(effect["form"]) is not str
        or effect["form"] not in {"top", "top_five_choose_two"}
    ):
        raise ValueError("Cloak needs its exact current controller and represented selection")
    return effect["form"]


def cloak_payload_dependencies(effect: Mapping) -> tuple[str, ...]:
    operation = effect.get("op")
    if operation == CLOAK_REMAINDER_OPERATION:
        raise ValueError("A Cloak remainder must follow the private selection")
    if operation != CLOAK_OPERATION:
        return ()
    form = validate_cloak_instruction(effect, "$controller")
    return (CLOAK_CAPABILITY, CLOAK_PARTITION_CAPABILITY) if form == "top_five_choose_two" else (CLOAK_CAPABILITY,)


def cloak_node_capabilities(*, effects, target_schema, mechanics):
    if target_schema is not None or len(effects) != 1 or CLOAK_MECHANIC not in mechanics:
        return ()
    try:
        return cloak_payload_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()

"""Typed instruction and name history for repeated library-top exile."""

from collections.abc import Mapping
from dataclasses import dataclass


EXILE_UNTIL_SAME_NAME_OPERATION = "exile_library_top_until_same_name"
EXILE_UNTIL_SAME_NAME_CAPABILITY = "library.exile.repeated_top_with_name_stop"
EXILE_UNTIL_SAME_NAME_MECHANIC = "exile-library-top-until-same-name"


def exile_until_same_name_instruction() -> dict:
    return {
        "op": EXILE_UNTIL_SAME_NAME_OPERATION,
        "schema_version": 1,
        "player": "$controller",
    }


def validate_exile_until_same_name(effect: Mapping, *, player: str = "$controller") -> None:
    expected = {**exile_until_same_name_instruction(), "player": player}
    if (
        not isinstance(effect, Mapping) or effect != expected
        or type(effect["schema_version"]) is not int
        or type(player) is not str or not player
    ):
        raise ValueError("Repeated top-library exile requires its original resolving controller")


@dataclass(frozen=True, slots=True)
class ExiledNameHistory:
    names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.names, tuple)
            or any(type(name) is not str or not name for name in self.names)
            or self.names != tuple(sorted(set(self.names)))
        ):
            raise ValueError("Exiled card names require an ordered, unique tuple")

    def after_exile(self, rules_names: tuple[str, ...]) -> "ExiledNameHistory | None":
        """Return the updated history, or end the instruction after a shared name."""
        if not isinstance(rules_names, tuple) or any(type(name) is not str or not name for name in rules_names):
            raise ValueError("Committed exile requires typed rules names")
        if not set(self.names).isdisjoint(rules_names):
            return None
        return ExiledNameHistory(tuple(sorted(set(self.names).union(rules_names))))

    def to_dict(self) -> dict:
        return {"schema_version": 1, "names": list(self.names)}

    @classmethod
    def from_dict(cls, value: Mapping) -> "ExiledNameHistory":
        if (
            not isinstance(value, Mapping) or set(value) != {"schema_version", "names"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["names"], (list, tuple))
        ):
            raise ValueError("Invalid repeated exile name history")
        return cls(tuple(value["names"]))


def exile_until_same_name_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or target_schema is not None or EXILE_UNTIL_SAME_NAME_MECHANIC not in mechanic_ids:
        return ()
    try:
        validate_exile_until_same_name(effects[0])
    except (TypeError, ValueError):
        return ()
    return (EXILE_UNTIL_SAME_NAME_CAPABILITY,)


def is_closed_exile_until_same_name_program(program):
    if (
        (getattr(program, "event", None), getattr(program, "active_zone", None)) != ("resolve", "stack")
        or getattr(program, "handlers", ()) or getattr(program, "event_condition", None) is not None
    ):
        return False
    required = exile_until_same_name_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(required) and set(required).issubset(program.capability_dependencies)

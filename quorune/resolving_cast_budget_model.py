"""Closed permission and replay identity for the two-spell six-value cast result."""
from collections.abc import Mapping
from dataclasses import dataclass

from .replacement.immutable import FrozenMap, thaw_value

OPERATION = "choose_budgeted_hand_graveyard_cast"
MECHANIC = "budgeted-hand-graveyard-free-cast"
CAPABILITY = "casting.resolution.budgeted_hand_graveyard"
DEPENDENCIES = (CAPABILITY, "zone.exile.resolving_spell")
CONTINUATION_FIELDS = {"_options_fingerprint"}


def effect(*, player="$controller"):
    return {"op": OPERATION, "schema_version": 1, "player": player, "cast_limit": 2, "mana_value_limit": 6}


def validate_effect(raw, *, actor=None):
    player = "$controller" if actor is None else actor
    if (not isinstance(raw, Mapping) or FrozenMap(raw) != FrozenMap(effect(player=player))
            or any(type(raw.get(key)) is not int for key in ("schema_version","cast_limit","mana_value_limit"))
            or actor is not None and (type(actor) is not str or not actor or actor.startswith("$"))):
        raise ValueError("The resolving cast requires the original two-spell and six-mana-value limits")


def node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or target_schema is not None or MECHANIC not in mechanic_ids:
        return ()
    try:
        validate_effect(effects[0])
    except (TypeError, ValueError):
        return ()
    return (CAPABILITY,)


def _body_is_closed(program):
    return (node_capabilities(effects=program.effects, target_schema=program.target_schema,
                              mechanic_ids=program.coverage) == (CAPABILITY,)
            and (program.event, program.active_zone) == ("resolve", "stack")
            and program.target_schema is None and program.cost_schema is None and not program.handlers
            and program.event_condition is None and getattr(program,"ability_id","").startswith("spell:"))


def program_dependencies(program):
    if not any(row.get("op") == OPERATION for row in program.effects):
        return ()
    from .rules.spell_self_exile import SELF_EXILE

    if (not _body_is_closed(program) or program.destination != SELF_EXILE.destination
            or SELF_EXILE.mechanic not in program.coverage):
        raise ValueError("The two-spell resolving permission requires its whole terminal self-exile spell")
    return DEPENDENCIES


def is_closed_body(program):
    return _body_is_closed(program) and CAPABILITY in program.capability_dependencies


@dataclass(frozen=True, slots=True)
class ResolveBudgetedCastIntent:
    actor: str
    stack_ref: str
    options_fingerprint: str
    response: FrozenMap
    replacement_selections: tuple = ()

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.actor,self.stack_ref))
                or type(self.options_fingerprint) is not str or len(self.options_fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in self.options_fingerprint)
                or not isinstance(self.response,FrozenMap) or not isinstance(self.replacement_selections,tuple)):
            raise ValueError("Budgeted cast requires its closed frame, offered choice and response")
        if any(type(key) is not str or key.startswith("_") for key in self.response):
            raise ValueError("A cast cannot submit internal payment or budget authority")

    def identity(self):
        return {"schema_version":1,"actor":self.actor,"stack_ref":self.stack_ref,
                "options_fingerprint":self.options_fingerprint,"response":thaw_value(self.response)}


def validate_intent_identity(value):
    if (not isinstance(value,Mapping)
            or set(value) != {"schema_version","actor","stack_ref","options_fingerprint","response"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["response"],Mapping)):
        raise ValueError("Budgeted cast replay identity is malformed")
    return ResolveBudgetedCastIntent(value["actor"],value["stack_ref"],value["options_fingerprint"],
                                     FrozenMap(value["response"])).identity()

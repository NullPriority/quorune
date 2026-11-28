"""Own graveyard play with a current-turn card destination replacement."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..continuous_effect_state import ResolutionEffectSource
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap, thaw_value
from ..replacement.model import ReplacementClass, ReplacementEffect
from ..replacement.operations import SetField
from .effect_shape_walkers import nested_effect_operations


OPERATION = "grant_owned_graveyard_turn_play"
CAPABILITY = "casting.permission.owned_graveyard_until_cleanup_with_exile"
MECHANIC = "owned-graveyard-play-exile-turn"
STATS_FIELD = "owned_graveyard_turn_play"
TEMPLATE = "owned-graveyard-play-exile-turn-v1"
INSTRUCTION = FrozenMap({"op": OPERATION, "schema_version": 1, "player": "$controller"})


@dataclass(frozen=True, slots=True)
class GraveyardTurnPlay:
    source: ResolutionEffectSource
    program_key: str
    controller: str
    turn_sequence: int

    def __post_init__(self):
        if (not isinstance(self.source, ResolutionEffectSource) or type(self.program_key) is not str
                or not self.program_key or type(self.controller) is not str or not self.controller
                or type(self.turn_sequence) is not int or self.turn_sequence < 0):
            raise ValueError("Graveyard turn play must retain its source, controller and turn")

    def to_dict(self):
        return {"schema_version": 1, "source": self.source.to_dict(), "program_key": self.program_key,
                "controller": self.controller, "turn_sequence": self.turn_sequence}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {"schema_version", "source", "program_key", "controller", "turn_sequence"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Graveyard turn play has a closed persisted scope")
        return cls(ResolutionEffectSource.from_effect({"_runtime_source": value["source"]}),
                   value["program_key"], value["controller"], value["turn_sequence"])

    def replacement(self):
        return ReplacementEffect(
            effect_id=f"{OPERATION}:{self.source.stack_ref}:{self.controller}", source_id=self.source.stack_ref,
            event_kind="zone.change", replacement_class=ReplacementClass.OTHER,
            conditions={"owner": {"eq": self.controller}, "destination": {"eq": "graveyard"},
                        "object_kind": {"eq": "card"}},
            operations=(SetField("destination", "exile"),),
            label=f"Exile a card that would enter {self.controller}'s graveyard instead",
        )


def validate_graveyard_turn_instruction(effect, *, actor="$controller"):
    expected = {**INSTRUCTION, "player": actor}
    if FrozenMap(effect) != FrozenMap(expected) or type(effect.get("schema_version")) is not int:
        raise ValueError("Graveyard turn play requires its exact player and coupled replacement")


def graveyard_turn_play_node_capabilities(effects, target_schema, mechanics):
    if target_schema is not None or MECHANIC not in mechanics or len(effects) != 1:
        return ()
    try:
        validate_graveyard_turn_instruction(effects[0])
    except (TypeError, ValueError):
        return ()
    return (CAPABILITY, "zone.change.destination_replacement")


def graveyard_turn_play_runtime_dependencies(program):
    if OPERATION not in nested_effect_operations(program.effects):
        return ()
    required = graveyard_turn_play_node_capabilities(program.effects, program.target_schema, program.coverage)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:") or program.handlers or program.event_condition is not None
            or program.cost_schema is not None or program.destination != "graveyard"):
        raise ValueError("Graveyard turn play requires the complete resolving spell")
    return required


def is_closed_graveyard_turn_play_program(program):
    try:
        required = graveyard_turn_play_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def current_graveyard_turn_play_grants(host, seat):
    player = host.state.players.get(seat)
    if player is None or not player.in_game:
        return ()
    rows = player.stats.get(STATS_FIELD, ())
    if not isinstance(rows, (list, tuple)):
        return ()
    grants = []
    for raw in rows:
        try:
            grant = GraveyardTurnPlay.from_dict(raw)
        except (TypeError, ValueError, KeyError):
            continue
        if grant.controller == seat and grant.turn_sequence == host.state.turn_sequence:
            program = host.semantics.get(grant.program_key)
            if (program is not None and host.semantic_program_is_current_trusted(program)
                    and is_closed_graveyard_turn_play_program(program)):
                grants.append(grant)
    return tuple(grants)


def owned_graveyard_turn_play_permission(host, seat, card):
    return (card.owner == seat and card.zone == "graveyard" and card.is_card_object
            and bool(current_graveyard_turn_play_grants(host, seat)))


def owned_graveyard_turn_play_replacements(host, active_seats):
    return tuple(grant.replacement() for seat in active_seats for grant in current_graveyard_turn_play_grants(host, seat))


def apply_owned_graveyard_turn_play(host, effect, *, actor, operation, reason):
    try:
        visible = {key: value for key, value in thaw_value(effect).items() if key not in {"reason", "_runtime_source"}}
        validate_graveyard_turn_instruction(visible, actor=actor)
        source = ResolutionEffectSource.from_effect(effect)
        item = next((row for row in host.state.stack if row.ref == source.stack_ref), None)
        if (item is None or item.kind not in {"spell", "spell_copy"} or item.controller != actor
                or item.context.get("currently_resolving") is not True or item.targets):
            raise ValueError("Graveyard play requires its original untargeted resolving spell")
        program = host.semantics.get(item.semantic_key)
        if (program is None or not host.semantic_program_is_current_trusted(program)
                or not is_closed_graveyard_turn_play_program(program)):
            raise ValueError("Graveyard play requires its admitted resolving spell")
        grant = GraveyardTurnPlay(source, program.key, actor, host.state.turn_sequence)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    stats = host.state.players[actor].stats
    stats[STATS_FIELD] = [*stats.get(STATS_FIELD, ()), grant.to_dict()]
    host._log(actor, "permission.owned_graveyard_turn_play", "Granted graveyard play and card exile for this turn.",
              {"source": source.stack_ref}, changed_players=[actor])
    return True

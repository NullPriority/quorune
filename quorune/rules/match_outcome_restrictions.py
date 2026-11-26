"""Closed controller-relative restrictions on winning and losing a match."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE, StaticComponentSpec
from ..all_player_turn_rule_model import AllPlayerTurnRule, has_all_player_turn_rule
from ..turn_match_outcome_model import ResolvedTurnMatchOutcomeProtection


MATCH_OUTCOME_RESTRICTION_CAPABILITY = "rule.player.static_match_outcome_restriction"
MATCH_OUTCOME_RESTRICTION_HANDLER = "rule.player.match-outcome-restriction.v1"
MATCH_OUTCOME_RESTRICTION_TEMPLATE = "static-match-outcome-restriction-v1"
_CANNOT_LOSE_TEXT = "you can't lose the game and your opponents can't win the game."
_CANNOT_WIN_TEXT = "you can't win the game and your opponents can't lose the game."


class MatchOutcomeRestrictionKind(StrEnum):
    CONTROLLER_CANNOT_LOSE = "controller_cannot_lose"
    CONTROLLER_CANNOT_WIN = "controller_cannot_win"


@dataclass(frozen=True, slots=True)
class MatchOutcomeRestrictions:
    loss_prohibited: frozenset[str] = frozenset()
    win_prohibited: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class MatchOutcomeRestrictionSpec:
    kind: MatchOutcomeRestrictionKind

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MatchOutcomeRestrictionKind):
            raise ValueError("Match outcome restrictions require a closed relation")

    @property
    def dependencies(self) -> tuple[str, ...]:
        return (MATCH_OUTCOME_RESTRICTION_CAPABILITY,)

    def descriptor(self) -> dict:
        return {
            "handler_id": MATCH_OUTCOME_RESTRICTION_HANDLER,
            "schema_version": 1,
            "event": "characteristics.evaluate",
            "kind": self.kind.value,
        }

    @classmethod
    def from_descriptor(cls, descriptor: Mapping) -> "MatchOutcomeRestrictionSpec":
        if (
            not isinstance(descriptor, Mapping)
            or set(descriptor) != {"handler_id", "schema_version", "event", "kind"}
            or descriptor["handler_id"] != MATCH_OUTCOME_RESTRICTION_HANDLER
            or type(descriptor["schema_version"]) is not int
            or descriptor["schema_version"] != 1
            or descriptor["event"] != "characteristics.evaluate"
            or type(descriptor["kind"]) is not str
        ):
            raise ValueError("Match outcome restriction descriptor is malformed")
        return cls(MatchOutcomeRestrictionKind(descriptor["kind"]))


def _normalize_clause(text: str) -> str:
    return " ".join(text.casefold().replace("’", "'").split())


def compile_static_match_outcome_restriction(text: str) -> MatchOutcomeRestrictionKind | None:
    """Recognize the two printed controller-relative clauses in full."""
    return {
        _CANNOT_LOSE_TEXT: MatchOutcomeRestrictionKind.CONTROLLER_CANNOT_LOSE,
        _CANNOT_WIN_TEXT: MatchOutcomeRestrictionKind.CONTROLLER_CANNOT_WIN,
    }.get(_normalize_clause(text))


def compile_match_outcome_component(text: str) -> MatchOutcomeRestrictionSpec | None:
    kind = compile_static_match_outcome_restriction(text)
    return MatchOutcomeRestrictionSpec(kind) if kind is not None else None


def static_match_outcome_restrictions(
    kind: MatchOutcomeRestrictionKind, controller: str, active_seats: frozenset[str],
) -> MatchOutcomeRestrictions:
    """Resolve a represented live source's scope against the active roster."""
    if not isinstance(kind, MatchOutcomeRestrictionKind):
        raise ValueError("A match outcome restriction must name a fixed controller relation")
    if controller not in active_seats:
        return MatchOutcomeRestrictions()
    own = frozenset({controller})
    opponents = active_seats - own
    if kind is MatchOutcomeRestrictionKind.CONTROLLER_CANNOT_LOSE:
        return MatchOutcomeRestrictions(loss_prohibited=own, win_prohibited=opponents)
    return MatchOutcomeRestrictions(loss_prohibited=opponents, win_prohibited=own)


def match_outcome_restriction_runtime_dependencies(program) -> tuple[str, ...]:
    descriptors = [
        descriptor for descriptor in program.handlers
        if descriptor.get("handler_id") == MATCH_OUTCOME_RESTRICTION_HANDLER
    ]
    marked = program.provenance.get("template_id") == MATCH_OUTCOME_RESTRICTION_TEMPLATE
    if not descriptors and not marked:
        return ()
    if (
        not marked or len(descriptors) != 1 or len(program.handlers) != 1
        or (program.event, program.active_zone) != ("characteristics.evaluate", "battlefield")
        or program.effects or program.event_condition is not None
        or program.cost_schema is not None or program.target_schema is not None
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
    ):
        raise ValueError("Match outcome restrictions require their live source characteristic")
    return MatchOutcomeRestrictionSpec.from_descriptor(descriptors[0]).dependencies


def current_match_outcome_restrictions(host) -> MatchOutcomeRestrictions:
    """Combine live battlefield abilities and already resolved turn effects."""
    active = frozenset(host.active_seats)
    if has_all_player_turn_rule(host.state.continuous_effects, AllPlayerTurnRule.NO_GAME_OUTCOME):
        return MatchOutcomeRestrictions(active, active)
    losing: set[str] = set()
    winning: set[str] = set()
    for rule in host.state.continuous_effects or ():
        if isinstance(rule, ResolvedTurnMatchOutcomeProtection):
            if rule.controller in active:
                losing.add(rule.controller)
            winning.update(active - {rule.controller})
    for source in host._semantic_event_sources(zones={"battlefield"}):
        if source.phased_out or source.controller not in active:
            continue
        for fragment in host._effective_ability_fragments(source):
            if not isinstance(fragment, StaticComponentSpec):
                continue
            program = host.semantics.get(fragment.semantic_key)
            if program is None or program.provenance.get("template_id") != MATCH_OUTCOME_RESTRICTION_TEMPLATE:
                continue
            if not host.semantic_program_is_current_trusted(program):
                continue
            descriptor, = program.handlers
            spec = MatchOutcomeRestrictionSpec.from_descriptor(descriptor)
            rules = static_match_outcome_restrictions(spec.kind, source.controller, active)
            losing.update(rules.loss_prohibited)
            winning.update(rules.win_prohibited)
    return MatchOutcomeRestrictions(frozenset(losing), frozenset(winning))

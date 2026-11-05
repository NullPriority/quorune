from __future__ import annotations

"""Read-only discovery of one complete current intrinsic Miracle declaration."""

from typing import Any, Protocol

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..card_program_faces import program_matches_face
from ..card_programs.admission import program_has_complete_card_program_admission
from ..model import CardInstance
from ..semantic_runtime.intrinsic_draw_reveals import MiracleIntrinsicRevealHandler
from .miracle_model import (
    FixedMiracleCost, MiracleDrawOpportunity, MIRACLE_REVEAL_EVENT, MIRACLE_REVEAL_HANDLER_ID,
)
from .restrictions import drawn_this_turn


class MiracleDiscoveryHost(Protocol):
    state: Any
    semantics: Any
    active_seats: list[str]

    def card_record(self, card: CardInstance) -> Any: ...
    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...
    def _effective_static_component_keys(self, card: CardInstance) -> tuple[str, ...]: ...


def current_miracle_declaration(
    host: MiracleDiscoveryHost, card: CardInstance,
) -> tuple[str, FixedMiracleCost] | None:
    if (not isinstance(card, CardInstance) or not card.is_card_object
            or card.zone not in {"library", "hand"}
            or card.annotations.get("copy_overrides") is not None):
        return None
    record = host.card_record(card)
    if record is None:
        return None
    result = []
    component_keys = None
    handler = MiracleIntrinsicRevealHandler()
    for program in host.semantics.runtime_handler_programs_for_oracle(
        record.oracle_id, active_zone="all", event=MIRACLE_REVEAL_EVENT,
    ):
        if (not host.semantic_program_is_current_trusted(program)
                or not program_has_complete_card_program_admission(program)
                or not program_matches_face(record, program, card)):
            continue
        if card.zone == "hand" and CURRENT_ABILITY_FRAGMENT_COVERAGE in program.coverage:
            if component_keys is None:
                component_keys = frozenset(host._effective_static_component_keys(card))
            if program.key not in component_keys:
                continue
        for descriptor in program.handlers:
            if descriptor.get("handler_id") == MIRACLE_REVEAL_HANDLER_ID:
                result.append((program.key, handler.validate(descriptor)))
    # Multiple declarations require separate reveal/link semantics, not an
    # arbitrary choice of the first cost or a duplicate-trigger collapse.
    return result[0] if len(result) == 1 else None


def discover_miracle_draw(
    host: MiracleDiscoveryHost, *, player: str, event_id: str,
) -> MiracleDrawOpportunity | None:
    if player not in host.active_seats or drawn_this_turn(host, player) != 0:
        return None
    library = host.state.players[player].zones["library"]
    if not library:
        return None
    card = host.state.cards.get(library[-1])
    if card is None or card.owner != player or card.zone != "library":
        return None
    declaration = current_miracle_declaration(host, card)
    if declaration is None:
        return None
    program_key, cost = declaration
    return MiracleDrawOpportunity.capture(
        card, event_id=event_id, drawer=player, program_key=program_key, cost=cost,
        turn_sequence=host.state.turn_sequence, turn_anchor=host.state.active_player,
        library_size=len(library), draw_ordinal=1,
    )

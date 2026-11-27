from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Collection, Iterable, Iterator

from .model import Capability, DecisionGroup, GameState
from .library_search_control import pending_library_search_control


class PermissionDenied(RuntimeError):
    """Raised when a principal attempts an action outside its issued capability."""


def current_turn_controller(state: GameState, actor: str) -> str | None:
    """Read a live controller without changing stored turn-control history."""
    player = state.players.get(actor)
    if player is None or not player.in_game:
        return None
    controller = player.stats.get("turn_controlled_by")
    if not isinstance(controller, str) or controller == actor:
        return None
    controlling_player = state.players.get(controller)
    return controller if controlling_player is not None and controlling_player.in_game else None


@dataclass(frozen=True, slots=True)
class AuthorizedCommand:
    capability: Capability
    decision: DecisionGroup
    action: str
    payload: dict[str, Any]


class CapabilityManager:
    """
    Opaque, single-use decision capabilities.

    The game server derives ``principal`` from the authenticated connection.  A
    local ChatGPT session passes it explicitly, but the same command envelope can
    be exposed through HTTP/WebSocket without changing the engine permission
    model.  Pilots never receive mutation or rules-resolution capabilities.
    """

    def __init__(self, state: GameState):
        self.state = state

    @staticmethod
    def pilot_principal(seat: str) -> str:
        return f"pilot:{seat}"

    def issue(
        self,
        *,
        kind: str,
        role: str,
        actors: Iterable[str],
        allowed_actions: Iterable[str],
        payload_by_actor: dict[str, dict[str, Any]] | None = None,
        simultaneous: bool = False,
        continuation: dict[str, Any] | None = None,
    ) -> DecisionGroup:
        if self.state.pending_decision is not None:
            raise RuntimeError("Cannot issue a new decision while another is pending")
        actor_list = list(actors)
        decision_id = f"D{self.state.ref_counters.get('decision', 0) + 1}"
        self.state.ref_counters["decision"] = self.state.ref_counters.get("decision", 0) + 1
        decision = DecisionGroup(
            decision_id=decision_id,
            kind=kind,
            role=role,
            actors=actor_list,
            allowed_actions=list(allowed_actions),
            payload_by_actor=dict(payload_by_actor or {}),
            simultaneous=simultaneous,
            continuation=dict(continuation or {}),
            created_revision=self.state.revision,
        )
        self.state.pending_decision = decision

        for token, capability in self._decision_capabilities(
            decision, actor_list, occupied=self.state.capabilities,
        ).items():
            self.state.capabilities[token] = capability
        return decision

    def _actor_grants(
        self, decision: DecisionGroup, actor: str,
    ) -> Iterator[tuple[str, str | None, list[str]]]:
        if decision.role != "pilot":
            yield decision.role, None if actor == decision.role else actor, decision.allowed_actions
            return
        own_principal = self.pilot_principal(actor)
        search = pending_library_search_control(self.state, decision, actor)
        if search is not None and search.controller is not None:
            yield self.pilot_principal(search.controller.controller), actor, ["choose"]
            return
        controller = current_turn_controller(self.state, actor)
        principal = self.pilot_principal(controller) if controller else own_principal
        actions = decision.allowed_actions
        delegated_concession = principal != own_principal and "concede" in actions
        if delegated_concession:
            actions = [action for action in actions if action != "concede"]
        yield principal, actor, actions
        if delegated_concession and decision.kind == "priority":
            yield own_principal, actor, ["concede"]

    def _decision_capabilities(
        self, decision: DecisionGroup, actors: Iterable[str], *, occupied: Collection[str] = (),
    ) -> dict[str, Capability]:
        proposed: dict[str, Capability] = {}
        for actor in actors:
            for principal, capability_actor, allowed_actions in self._actor_grants(decision, actor):
                token = self._new_token(occupied, proposed)
                proposed[token] = Capability(
                    token=token,
                    decision_id=decision.decision_id,
                    principal=principal,
                    role=decision.role,
                    actor=capability_actor,
                    allowed_actions=list(allowed_actions),
                    created_revision=self.state.revision,
                )
        return proposed

    @staticmethod
    def _new_token(occupied: Collection[str], proposed: Collection[str]) -> str:
        while True:
            # 32 random bytes provide 256 bits of bearer capability entropy.
            # The token remains short-lived and decision-scoped; this merely
            # makes guessing safe enough for a real network boundary.
            token = "c_" + secrets.token_urlsafe(32)
            if token not in occupied and token not in proposed:
                return token

    def _capability_actor_is_live(self, capability: Capability) -> bool:
        if capability.role != "pilot":
            return True
        actor = capability.actor
        player = self.state.players.get(actor) if actor is not None else None
        if player is None or not player.in_game:
            return False
        search = pending_library_search_control(self.state, self.state.pending_decision, player.seat)
        if search is not None and search.controller is not None:
            return capability.principal == self.pilot_principal(search.controller.controller)
        if capability.principal == self.pilot_principal(player.seat):
            return True
        controller = current_turn_controller(self.state, player.seat)
        return controller is not None and capability.principal == self.pilot_principal(controller)

    def pending_capabilities(self) -> Iterator[Capability]:
        decision = self.state.pending_decision
        if not decision:
            return
        for capability in self.state.capabilities.values():
            if (
                capability.decision_id == decision.decision_id
                and not capability.consumed
                and self._capability_actor_is_live(capability)
            ):
                yield capability

    def capability_for(self, principal: str) -> Capability | None:
        return next((capability for capability in self.pending_capabilities() if capability.principal == principal), None)

    def authorize(
        self,
        *,
        token: str,
        principal: str,
        action: str,
        payload: dict[str, Any],
    ) -> AuthorizedCommand:
        decision = self.state.pending_decision
        if decision is None:
            raise PermissionDenied("No decision is currently pending")
        capability = self.state.capabilities.get(token)
        if capability is None:
            raise PermissionDenied("Unknown capability token")
        if capability.consumed:
            raise PermissionDenied("Capability has already been consumed")
        if capability.decision_id != decision.decision_id:
            raise PermissionDenied("Capability belongs to an expired decision")
        if capability.principal != principal:
            raise PermissionDenied("Capability was not issued to this principal")
        if not self._capability_actor_is_live(capability):
            raise PermissionDenied("Pilot no longer has live authority for this actor")
        if (
            action == "concede" and capability.role == "pilot"
            and principal != self.pilot_principal(capability.actor or "")
        ):
            raise PermissionDenied("Only the authenticated pilot for a seat can concede that seat")
        if action not in capability.allowed_actions:
            raise PermissionDenied(
                f"Action {action!r} is outside capability scope {capability.allowed_actions}"
            )
        if "action" in payload and payload["action"] != action:
            raise PermissionDenied("Payload action does not match the authorized action")
        capability.consumed = True
        return AuthorizedCommand(capability, decision, action, payload)

    def record_response(self, command: AuthorizedCommand) -> None:
        actor_key = command.capability.actor or command.capability.principal
        command.decision.responses[actor_key] = {
            **command.payload,
            "action": command.action,
        }

    def decision_complete(self) -> bool:
        decision = self.state.pending_decision
        if decision is None:
            return False
        expected = set(decision.actors)
        if decision.role != "pilot":
            # Non-pilot actors are usually the principal name itself.
            expected = {
                cap.actor or cap.principal
                for cap in self.state.capabilities.values()
                if cap.decision_id == decision.decision_id
            }
        return expected.issubset(decision.responses)

    def close_decision(self) -> DecisionGroup:
        decision = self.state.pending_decision
        if decision is None:
            raise RuntimeError("No pending decision")
        for capability in self.state.capabilities.values():
            if capability.decision_id == decision.decision_id:
                capability.consumed = True
        self.state.pending_decision = None
        return decision

    def reissue_pending(self) -> None:
        """Replace persisted capability metadata with fresh opaque tokens.

        Capability tokens are transport credentials, not authoritative game
        state. Checkpoints deliberately omit them and reissue only the still
        unanswered actors when a record is loaded or replayed.
        """
        decision = self.state.pending_decision
        self.state.capabilities = (
            self._decision_capabilities(
                decision, (actor for actor in decision.actors if actor not in decision.responses),
            )
            if decision is not None
            else {}
        )

    def invalidate_current(self) -> None:
        decision = self.state.pending_decision
        if not decision:
            return
        for capability in self.state.capabilities.values():
            if capability.decision_id == decision.decision_id:
                capability.consumed = True
        self.state.pending_decision = None


def decision_context_for_capability(
    decision: DecisionGroup, capability: Capability,
) -> dict[str, Any]:
    """Project a priority response catalog to the participant's authority."""
    context = decision.payload_by_actor.get(capability.actor or capability.principal, {})
    if decision.kind != "priority" or "concede" not in decision.allowed_actions:
        return context
    legal = context.get("legal") or {}
    if capability.allowed_actions == ["concede"]:
        return {"legal": {"actions": [
            action for action in legal.get("actions", ())
            if action.get("action") == "concede"
        ]}}
    if "concede" not in capability.allowed_actions:
        return {
            **context,
            "legal": {
                **legal,
                "actions": [
                    action for action in legal.get("actions", ())
                    if action.get("action") != "concede"
                ],
            },
        }
    return context

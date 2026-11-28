"""Bind actual combat prevention to the ordinary token creation owner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..damage_modifier_state import CreateTokensPreventionAftermath
from ..drawing.model import QueuedDraw
from ..errors import GameRuleError
from ..replacement.immutable import thaw_value

COMBAT_TOKEN_AFTERMATH_RESUME = "combat_token_aftermath"


@dataclass(frozen=True, slots=True)
class CombatPreventionTokenRequest:
    effect_id: str
    source: str
    controller: str
    amount: int
    damage_events: tuple[str, ...]
    recipe: CreateTokensPreventionAftermath

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.effect_id, self.source, self.controller))
                or type(self.amount) is not int or self.amount <= 0
                or not isinstance(self.damage_events, tuple) or not self.damage_events
                or any(type(value) is not str or not value for value in self.damage_events)
                or len(set(self.damage_events)) != len(self.damage_events)
                or not isinstance(self.recipe, CreateTokensPreventionAftermath)):
            raise ValueError("Prevention tokens require one actual committed prevention receipt")

    def to_dict(self):
        return {
            "effect_id": self.effect_id, "source": self.source, "controller": self.controller,
            "amount": self.amount, "damage_events": list(self.damage_events), "recipe": self.recipe.to_dict(),
        }

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {"effect_id", "source", "controller", "amount", "damage_events", "recipe"}
                or not isinstance(value["damage_events"], (list, tuple))):
            raise ValueError("Prevention token receipt is malformed")
        return cls(value["effect_id"], value["source"], value["controller"], value["amount"],
                   tuple(value["damage_events"]), CreateTokensPreventionAftermath.from_dict(value["recipe"]))


def combat_prevention_token_requests(host, applications):
    recipes = {
        shield.effect_id: (shield, result)
        for shield in host.state.damage_prevention_shields
        for result in shield.aftermath
        if isinstance(result, CreateTokensPreventionAftermath)
    }
    requests = []
    for application in applications:
        bound = recipes.get(application.effect_id)
        if bound is None:
            continue
        shield, recipe = bound
        if application.source_id != shield.source_id or application.affected_players != (shield.controller,):
            raise ValueError("Prevention token result lost its original controlling player")
        requests.append(CombatPreventionTokenRequest(
            application.effect_id, shield.source_id, shield.controller,
            application.prevented_amount, application.damage_event_ids, recipe,
        ))
    return tuple(requests)


@dataclass(frozen=True, slots=True)
class CombatPreventionTokenQueue:
    requests: tuple[CombatPreventionTokenRequest, ...]
    draws: tuple[QueuedDraw, ...]
    turn: int
    damage_step: int

    def __post_init__(self):
        if (not isinstance(self.requests, tuple) or not self.requests
                or any(not isinstance(row, CombatPreventionTokenRequest) for row in self.requests)
                or len({row.effect_id for row in self.requests}) != len(self.requests)
                or not isinstance(self.draws, tuple) or any(not isinstance(row, QueuedDraw) for row in self.draws)
                or type(self.turn) is not int or self.turn < 0
                or type(self.damage_step) is not int or self.damage_step < 0):
            raise ValueError("Prevention tokens require the original combat damage boundary")

    def to_dict(self):
        return {
            "schema_version": 1, "requests": [row.to_dict() for row in self.requests],
            "draws": [row.to_dict() for row in self.draws], "turn": self.turn, "damage_step": self.damage_step,
        }

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {"schema_version", "requests", "draws", "turn", "damage_step"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or any(not isinstance(value[key], (list, tuple)) for key in ("requests", "draws"))):
            raise ValueError("Prevention token continuation has a closed shape")
        return cls(tuple(CombatPreventionTokenRequest.from_dict(row) for row in value["requests"]),
                   tuple(QueuedDraw.from_dict(row) for row in value["draws"]), value["turn"], value["damage_step"])

    def validate_current(self, host):
        state = host.state
        if ((state.phase, state.step) != ("combat", "combat_damage")
                or state.turn_sequence != self.turn or state.combat.damage_step_index != self.damage_step):
            raise GameRuleError("Prevention token continuation changed its combat damage boundary")
        shields = {shield.effect_id: shield for shield in state.damage_prevention_shields}
        for receipt in self.requests:
            shield = shields.get(receipt.effect_id)
            if (shield is None or shield.source_id != receipt.source or shield.controller != receipt.controller
                    or shield.aftermath != (receipt.recipe,) or receipt.controller not in host.active_seats):
                raise GameRuleError("Prevention token continuation lost its original shield")


def _continue_combat_prevention_tokens(host, queue, selections=()):
    from ..replacement_effects import ReplacementChoiceRequired, replacement_choice_payload
    from ..token_creation import TokenCreationError, create_tokens
    from .combat_prevention_draw import complete_combat_damage_draws

    queue.validate_current(host)
    for index, receipt in enumerate(queue.requests):
        supplied = selections if index == 0 else ()
        try:
            with host.transaction():
                tokens = create_tokens(
                    host, receipt.controller, name=receipt.recipe.name, quantity=receipt.amount,
                    characteristics=thaw_value(receipt.recipe.characteristics), reason="combat damage prevention",
                    replacement_selections=supplied,
                )
        except ReplacementChoiceRequired as required:
            chooser = required.pending.choice.chooser
            retained = CombatPreventionTokenQueue(
                queue.requests[index:], queue.draws, queue.turn, queue.damage_step,
            )
            host.permissions.issue(
                kind="replacement.order", role="pilot", actors=[chooser], allowed_actions=["choose"],
                payload_by_actor={chooser: replacement_choice_payload(required.pending, required.effects)},
                continuation={
                    "replacement_resume_kind": COMBAT_TOKEN_AFTERMATH_RESUME,
                    "combat_prevention_tokens": retained.to_dict(),
                    "replacement_selections": list(supplied),
                    "replacement_batch": required.batch.to_dict(),
                    "replacement_effects": [effect.to_dict() for effect in required.effects],
                },
            )
            return
        except TokenCreationError as exc:
            raise GameRuleError(str(exc)) from exc
        host._log(
            receipt.controller, "damage.prevention.tokens", "Created tokens from prevented combat damage.",
            {"effect": receipt.effect_id, "source": receipt.source, "prevented_amount": receipt.amount,
             "damage_events": list(receipt.damage_events), "tokens": tokens},
            importance=2, changed_players=[receipt.controller],
        )
    if queue.draws:
        from ..drawing.coordinator import begin_draw_batch

        begin_draw_batch(host, queue.draws, continuation={"kind": "combat_damage"})
    else:
        complete_combat_damage_draws(host)


def begin_combat_prevention_aftermath(host, result):
    requests, draws = result.combat_prevention_tokens, result.combat_prevention_draws
    if requests:
        _continue_combat_prevention_tokens(host, CombatPreventionTokenQueue(
            requests, draws, host.state.turn_sequence, host.state.combat.damage_step_index,
        ))
    elif draws:
        from ..drawing.coordinator import begin_draw_batch

        begin_draw_batch(host, draws, continuation={"kind": "combat_damage"})
    return bool(requests or draws)


def resume_combat_prevention_tokens(host, restored, selection):
    queue = CombatPreventionTokenQueue.from_dict(thaw_value(restored.combat_prevention_tokens))
    _continue_combat_prevention_tokens(host, queue, (*restored.replacement_selections, selection))

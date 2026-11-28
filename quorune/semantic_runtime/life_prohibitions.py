from __future__ import annotations

"""Live source permissions in the existing life runtime component registry."""

from dataclasses import dataclass
from typing import Protocol, Any, Mapping
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..all_player_turn_rule_model import AllPlayerTurnRule, has_all_player_turn_rule
from ..entry_copy import copiable_oracle_id
from ..life_gain_permission import (ALLOW_ALL_LIFE_GAIN, FixedLifeGainProhibitionSpec, LifeGainPermissionSnapshot,
    LIFE_GAIN_PROHIBITION_CAPABILITY,LIFE_GAIN_PROHIBITION_HANDLER,LIFE_GAIN_PROHIBITION_EVENT,
    ControllerLifeTotalProhibition, LIFE_TOTAL_PROHIBITION_CAPABILITY, LIFE_TOTAL_PROHIBITION_HANDLER,
    decode_life_gain_prohibition, decode_life_total_prohibition, ResolvedLifeGainProhibition,
    validate_temporary_life_gain_effect)
from .context import SemanticNodeError
from .current_ability_components import program_has_current_ability_fragments


class LifeGainPermissionHost(Protocol):
    semantics: Any
    active_seats: list[str]
    state: Any
    def _semantic_event_sources(self, *, zones: set[str] | None = None) -> list[Any]: ...
    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...
    def _effective_card_data(self, card: Any): ...


@dataclass(frozen=True, slots=True)
class LifeGainProhibitionHandler:
    handler_id: str = LIFE_GAIN_PROHIBITION_HANDLER
    schema_version: int = 1
    family: str = 'life.gain.prohibition'
    event: str = LIFE_GAIN_PROHIBITION_EVENT
    rule_references: tuple[str, ...] = ('119.7','119.9','119.10')
    capability_dependencies: tuple[str, ...] = (LIFE_GAIN_PROHIBITION_CAPABILITY,)

    def validate(self, descriptor):
        try:
            return decode_life_gain_prohibition(descriptor)
        except (TypeError,ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)


@dataclass(frozen=True, slots=True)
class LifeTotalProhibitionHandler:
    handler_id: str = LIFE_TOTAL_PROHIBITION_HANDLER
    schema_version: int = 1
    family: str = "life.total.prohibition"
    event: str = LIFE_GAIN_PROHIBITION_EVENT
    rule_references: tuple[str, ...] = ("119.4", "119.7", "119.8")
    capability_dependencies: tuple[str, ...] = (LIFE_TOTAL_PROHIBITION_CAPABILITY,)

    def validate(self, descriptor):
        try:
            return decode_life_total_prohibition(descriptor)
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        del context
        return (self.validate(descriptor),)


def apply_temporary_life_gain_prohibition(host, actor, effect):
    from ..continuous_effect_state import commit_continuous_effect
    from ..errors import GameRuleError

    try:
        spec = validate_temporary_life_gain_effect(effect, resolved=True)
        denied = spec.permissions(actor, host.active_seats).denied_players
    except (ValueError, TypeError) as exc:
        raise GameRuleError(str(exc)) from exc
    if not denied:
        return 0
    rule = ResolvedLifeGainProhibition(
        host._next_ref("LG"), effect["source"], host._next_zone_timestamp(), actor, denied,
        affected_player_relation=spec.affected_player_relation,
        **({"duration": spec.duration} if isinstance(spec, ControllerLifeTotalProhibition) else {}),
    )
    commit_continuous_effect(host.state, rule)
    code = "life.total.prohibition.created" if rule.denies_loss else "life.gain.prohibition.created"
    message = "A temporary life-total prohibition applies." if rule.denies_loss else "A temporary life-gain prohibition applies."
    host._log(actor, code, message, {"effect": rule.effect_id, "players": list(denied)}, importance=1)
    return rule.effect_id


def collect_life_gain_permissions(host: LifeGainPermissionHost, *, sources=None, source_zones=None):
    from .life_replacements import default_life_replacement_registry,LifeReplacementSourceContext
    registry = default_life_replacement_registry()
    journal = getattr(getattr(host, "state", None), "continuous_effects", None)
    denied = {
        player for rule in journal or ()
        if isinstance(rule, ResolvedLifeGainProhibition)
        for player in rule.denied_players if player in host.active_seats
    }
    denied_loss = _temporary_life_total_denials(host)
    candidates = list(sources) if sources is not None else host._semantic_event_sources(zones={'battlefield'})
    for source in candidates:
        zone = source_zones.get(source.object_id,source.zone) if source_zones is not None else source.zone
        if zone != 'battlefield' or source.phased_out or source.controller not in host.active_seats:
            continue
        programs = host.semantics.runtime_handler_programs_for_oracle(copiable_oracle_id(source),
            active_zone='battlefield',event=LIFE_GAIN_PROHIBITION_EVENT)
        for program in programs:
            if not host.semantic_program_is_current_trusted(program):
                continue
            if CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage:
                raise SemanticNodeError('Life-gain prohibition lacks current source-ability scope')
            if not program_has_current_ability_fragments(program,host._effective_card_data(source)):
                continue
            for index,descriptor in enumerate(program.handlers):
                if descriptor.get('event') != LIFE_GAIN_PROHIBITION_EVENT:
                    continue
                context = LifeReplacementSourceContext(source.ref,source.controller,f'{program.key}:{index}')
                for spec in registry.lower(descriptor,context):
                    if not isinstance(spec, (FixedLifeGainProhibitionSpec, ControllerLifeTotalProhibition)):
                        raise SemanticNodeError("Life permission handler produced another rule type")
                    permissions = spec.permissions(source.controller, host.active_seats)
                    denied.update(permissions.denied_players)
                    denied_loss.update(permissions.denied_loss_players)
    return LifeGainPermissionSnapshot(tuple(sorted(denied)), tuple(sorted(denied_loss)))


def _temporary_life_total_denials(host) -> set[str]:
    journal = getattr(getattr(host, "state", None), "continuous_effects", None)
    if has_all_player_turn_rule(journal, AllPlayerTurnRule.NO_LIFE_LOSS):
        return set(host.active_seats)
    return {
        rule.controller for rule in journal or ()
        if isinstance(rule, ResolvedLifeGainProhibition) and rule.denies_loss and rule.controller in host.active_seats
    }


def has_life_total_prohibition(host) -> bool:
    if _temporary_life_total_denials(host):
        return True
    programs_for_event = getattr(getattr(host, "semantics", None), "programs_for_event", None)
    return programs_for_event is not None and any(
        descriptor.get("handler_id") == LIFE_TOTAL_PROHIBITION_HANDLER
        for program in programs_for_event(LIFE_GAIN_PROHIBITION_EVENT)
        for descriptor in program.handlers
    )


def life_payment_prohibited(host, player: str) -> bool:
    """Skip battlefield permission discovery unless a life-total program is present."""

    return has_life_total_prohibition(host) and not collect_life_gain_permissions(host).allows_loss(player)


def life_permissions_for_choice(host, effect):
    from ..library_order_model import uses_paid_private_top_cycle

    if uses_paid_private_top_cycle(effect):
        return collect_life_gain_permissions(host)
    upkeep_gives_life = (
        effect.get("op") == "offer_optional_mana_payment"
        and effect.get("schema_version") == 10
        and isinstance(effect.get("upkeep_effect_cost"), Mapping)
        and effect["upkeep_effect_cost"].get("kind") == "opponent_life_gain"
    )
    if upkeep_gives_life or has_life_total_prohibition(host):
        return collect_life_gain_permissions(host)
    return ALLOW_ALL_LIFE_GAIN

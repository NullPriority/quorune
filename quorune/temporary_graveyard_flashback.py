"""A temporary printed-cost Flashback grant on one graveyard incarnation."""

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from .continuous_effect_model import ContinuousObjectIdentity
from .continuous_effect_state import ResolutionEffectSource
from .util import stable_json


OPERATION = "grant_target_graveyard_flashback"
CAPABILITY = "casting.flashback.target_printed_mana_until_cleanup"
MECHANIC = "target-graveyard-printed-flashback"
STATS_FIELD = "target_graveyard_flashback_grants"
OPTION_PREFIX = "granted-flashback:"


@dataclass(frozen=True, slots=True)
class TargetGraveyardFlashbackGrant:
    source: ResolutionEffectSource
    target: ContinuousObjectIdentity
    program_key: str
    controller: str
    turn_sequence: int

    def __post_init__(self):
        if (not isinstance(self.source, ResolutionEffectSource) or self.source.object_id is None
                or not isinstance(self.target, ContinuousObjectIdentity)
                or type(self.program_key) is not str or not self.program_key
                or type(self.controller) is not str or not self.controller
                or type(self.turn_sequence) is not int or self.turn_sequence < 0):
            raise ValueError("Granted Flashback must retain its source, target and turn")

    def to_dict(self):
        return {"schema_version": 1, "source": self.source.to_dict(), "target": self.target.to_dict(),
                "program_key": self.program_key, "controller": self.controller, "turn_sequence": self.turn_sequence}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {"schema_version", "source", "target", "program_key", "controller", "turn_sequence"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Granted Flashback requires its closed persisted identity")
        return cls(ResolutionEffectSource.from_effect({"_runtime_source": value["source"]}),
                   ContinuousObjectIdentity.from_dict(value["target"]), value["program_key"], value["controller"], value["turn_sequence"])

    @property
    def fingerprint(self):
        return sha256(stable_json(self.to_dict()).encode()).hexdigest()


def current_flashback_grants(host, seat, card):
    player = host.state.players.get(seat)
    if (player is None or not player.in_game or card.owner != seat or card.zone != "graveyard"
            or not card.is_card_object):
        return ()
    rows = player.stats.get(STATS_FIELD, ())
    grants = []
    if not isinstance(rows, (list, tuple)):
        return ()
    for raw in rows:
        try:
            grant = TargetGraveyardFlashbackGrant.from_dict(raw)
        except (TypeError, ValueError, KeyError):
            continue
        if (grant.turn_sequence == host.state.turn_sequence and grant.controller == seat
                and grant.target.object_id == card.object_id and grant.target.logical_object_id == card.logical_object_id):
            program = host.semantics.get(grant.program_key)
            if program is not None and host.semantic_program_is_current_trusted(program) and CAPABILITY in program.capability_dependencies:
                grants.append(grant)
    return tuple(grants)


def granted_flashback_options(host, seat, card, printed):
    grants = current_flashback_grants(host, seat, card)
    if not grants:
        return ()
    grant = grants[0]
    return tuple({**option, "id": OPTION_PREFIX + option["id"], "kind": "alternate",
                  "label": "Flashback — " + option.get("label", "printed mana cost"), "source_zone": "graveyard",
                  "granted_flashback_fingerprint": grant.fingerprint}
                 for option in printed)


def is_granted_flashback_option(option):
    value = option.get("id")
    return type(value) is str and value.startswith(OPTION_PREFIX) and len(value) > len(OPTION_PREFIX)

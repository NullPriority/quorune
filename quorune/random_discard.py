from __future__ import annotations

"""Fixed random hand subsets committed through canonical discard transitions."""

from dataclasses import dataclass
import re
from typing import Mapping

from .replacement.immutable import FrozenMap
from .whole_hand_discard import whole_hand_input_snapshot
from .rules.random_selection import sample_identity_rows


RANDOM_DISCARD_OPERATION = "discard_random_cards"
RANDOM_DISCARD_MECHANIC = "fixed-random-discard"
RANDOM_DISCARD_CAPABILITY = "zone.discard.fixed_random"


@dataclass(frozen=True, slots=True)
class RandomDiscardIntent:
    actor: str
    players: tuple[str, ...]
    count: int
    randomization_key: str
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self):
        if any(type(value) is not str or not value for value in
               (self.actor, self.randomization_key, self.reason)):
            raise ValueError("Random discard requires actor, instruction identity and reason")
        if type(self.count) is not int or not 1 <= self.count <= 4:
            raise ValueError("Random discard count must be a fixed integer from one to four")
        if not isinstance(self.players, (list, tuple)) or any(
            type(player) is not str or not player for player in self.players
        ) or len(self.players) != len(set(self.players)):
            raise ValueError("Random discard players must be unique seats")
        object.__setattr__(self, "players", tuple(self.players))
        from .semantic_runtime.zone_move_intent import _freeze_replacement_selections
        object.__setattr__(self, "replacement_selections", _freeze_replacement_selections(
            self.replacement_selections, family="Random discard"
        ))


def random_discard_effect_template(text: str):
    match = re.fullmatch(
        r"(?:(?P<subject>Target player|Target opponent|Each player|Each opponent|You) )?"
        r"discards? (?P<count>a|one|two|three|four|[1-4]) cards? at random\.?",
        text.strip(), re.I,
    )
    if match is None:
        return None
    subject = (match["subject"] or "You").casefold()
    count = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4}.get(match["count"].casefold())
    if count is None:
        count = int(match["count"])
    players = {"you": ["$controller"], "each player": "all", "each opponent": "opponents"}.get(subject, ["$target.0"])
    targeted = subject.startswith("target")
    schema = {"zones": ["player"], "categories": ["player"], "count": 1,
              "player_relation": "opponent" if subject == "target opponent" else "any"} if targeted else None
    return (
        "fixed-random-discard-v1",
        ({"op": RANDOM_DISCARD_OPERATION, "actor": "$controller", "players": players, "count": count},),
        schema, (RANDOM_DISCARD_MECHANIC, "cr-402-hand", *(("cr-115-targets",) if targeted else ())),
    )


def random_discard_node_capabilities(*, effects, target_schema, mechanic_ids, **kwargs):
    del kwargs
    if RANDOM_DISCARD_MECHANIC not in mechanic_ids or len(effects) != 1:
        return ()
    effect = effects[0]
    if (set(effect) != {"op", "actor", "players", "count"}
            or effect["op"] != RANDOM_DISCARD_OPERATION or effect["actor"] != "$controller"
            or type(effect["count"]) is not int or not 1 <= effect["count"] <= 4):
        return ()
    dependencies = {RANDOM_DISCARD_CAPABILITY}
    if effect["players"] == ["$context.target"]:
        if "trigger-event-normalized-damage" not in mechanic_ids:
            return ()
        dependencies.update({"trigger.event.normalized_damage", "trigger.placement.apnap"})
    if effect["players"] == ["$target.0"]:
        if target_schema not in (
            {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any"},
            {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "opponent"},
        ):
            return ()
        dependencies.add("target.revalidate_resolution")
    elif target_schema is not None or effect["players"] not in (
        "all", "opponents", ["$controller"], ["$context.target"],
    ):
        return ()
    return tuple(sorted(dependencies))


def damage_player_random_discard_result(binding, body: str):
    if binding.event.value not in {"damage.dealt", "damage.dealt.self"}:
        return None
    def guarantees_player(condition):
        if not isinstance(condition, Mapping):
            return False
        if condition == {"field": "target_kind", "op": "eq", "value": "player"}:
            return True
        children = condition.get("all", ())
        return isinstance(children, (list, tuple)) and any(guarantees_player(child) for child in children)
    if not guarantees_player(binding.event_condition):
        return None
    match = re.fullmatch(r"(?:that player|they) discards? (?P<body>.+)", body, re.I)
    if match is None:
        return None
    compiled = random_discard_effect_template("Discard " + match["body"])
    if compiled is None:
        return None
    effect = {**compiled[1][0], "players": ["$context.target"]}
    return ("fixed-damage-player-random-discard-v1", (effect,), None, compiled[3]), False


def random_discard_input_snapshot(host, effect, *, actor):
    if effect.get("op") != RANDOM_DISCARD_OPERATION:
        return None
    return whole_hand_input_snapshot(host, {
        "op": "discard_whole_hands", "actor": effect.get("actor"), "players": effect.get("players"),
    }, actor=actor)


def resolve_random_discard(host, intent: RandomDiscardIntent, *, commit_batch, exclude_object_ids=()):
    if (not isinstance(exclude_object_ids,tuple) or any(type(oid) is not str or not oid for oid in exclude_object_ids)
            or len(set(exclude_object_ids))!=len(exclude_object_ids)):
        raise ValueError('Random discard exclusions require distinct object identities')
    snapshot = random_discard_input_snapshot(host, {
        "op": RANDOM_DISCARD_OPERATION, "actor": intent.actor, "players": intent.players,
    }, actor=intent.actor)
    selected = []
    for hand in snapshot["hands"]:
        members = sorted((row for row in hand['members'] if row['object_id'] not in exclude_object_ids),key=lambda row:row['object_id'])
        selected.extend(row['ref'] for row in sample_identity_rows(members,intent.count,
            seed=host.state.config.seed,instruction_key=intent.randomization_key,
            namespace='random-discard',seat=hand['seat']))
    return commit_batch(tuple(selected)) if selected else ()

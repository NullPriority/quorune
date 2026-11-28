from __future__ import annotations

"""Private affected-player searches followed by one public land-entry batch."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from ..errors import GameRuleError
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..util import stable_json
from .committed_quantity_followups import CommittedQuantityFollowup
from .library_selection import LibrarySelectionObjectIdentity


EXILE_LAND_SEARCH_CAPABILITY = "library.search.actual_exile_controller_batch"
EXILE_LAND_SEARCH_MECHANIC = "exile-controller-land-search"
EXILE_LAND_SEARCH_FIELD = "exile_land_search_version"
SEARCH_BATCH_FIELD = "exile_land_search_batch"
SEARCH_BATCH_VERSION = 32
EXILE_SEARCH_QUERY = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))


def exile_land_target_schema():
    return {"zones": ["battlefield"], "categories": ["permanent"], "count": 1,
            "types_any": ["creature"], "controller_relation": "opponent"}


def exile_land_target_effect():
    return {"op": "exile_permanent_targets", "cards": "$targets", "maximum_targets": 1, EXILE_LAND_SEARCH_FIELD: 1}


def exile_land_payload_dependencies(effect):
    if EXILE_LAND_SEARCH_FIELD not in effect:
        return ()
    if effect != exile_land_target_effect() or any(type(effect[key]) is not int
            for key in (EXILE_LAND_SEARCH_FIELD, "maximum_targets")):
        raise ValueError("Creature exile compensation requires one original target")
    return exile_land_search_dependencies()


def exile_land_search_dependencies():
    return (EXILE_LAND_SEARCH_CAPABILITY, "library.search.fixed_counted", "zone.entry.tapped_state",
            "zone.change.destination_replacement", "resolution.target_controller_information", "library.shuffle.fixed_controller")


def exile_land_search_runtime_dependencies(program):
    from ..public_zone_moves import PublicZoneMoveSetSpec
    from .public_exile_result_model import PublicExileFollowup

    effects = tuple(program.effects)
    marked = EXILE_LAND_SEARCH_CAPABILITY in program.capability_dependencies or any(
        EXILE_LAND_SEARCH_FIELD in child
        or (isinstance(child.get("followup"), Mapping)
            and child["followup"].get("effect") == {"kind": "former_controller_basic_land_search"})
        for child in effects
    )
    if not marked:
        return ()
    if len(effects) != 1 or (program.event, program.active_zone) != ("resolve", "stack"):
        raise ValueError("Exile compensation requires its single resolving spell instruction")
    effect, = effects
    if EXILE_LAND_SEARCH_FIELD in effect:
        if program.target_schema != exile_land_target_schema() or type(program.target_schema["count"]) is not int:
            raise ValueError("Targeted compensation requires its original opposing creature")
        return exile_land_payload_dependencies(effect)
    if (program.target_schema is not None or set(effect) != {"op", "source", "set", "followup"}
            or effect["op"] != "move_public_zone_set" or effect["source"] != "$source"):
        raise ValueError("Untargeted compensation requires the original opposing creature set")
    followup = PublicExileFollowup.from_dict(effect["followup"])
    if followup.schema_version != 3:
        raise ValueError("Untargeted compensation requires its exact controller search follow-up")
    followup.validate_move(PublicZoneMoveSetSpec.from_dict(effect["set"]))
    return exile_land_search_dependencies()


def _library_fingerprint(host, seat):
    from hashlib import sha256
    from .library_search_rules import library_search_constraint

    scope = library_search_constraint(host, seat)
    library = host.state.players[seat].zones["library"]
    data = (scope.prohibited, scope.top_count, tuple(host.state.cards[object_id].logical_object_id for object_id in library))
    return sha256(stable_json(data).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ExileLandSearchBatch:
    stack_ref: str
    controller: str
    quotas: tuple[tuple[str, int, str], ...]
    chosen: tuple[tuple[str, tuple[LibrarySelectionObjectIdentity, ...]], ...] = ()

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.stack_ref, self.controller)):
            raise GameRuleError("The compensation batch requires its resolving source")
        seats = tuple(seat for seat, _, _ in self.quotas)
        if (not seats or len(set(seats)) != len(seats) or self.controller in seats
                or any(type(seat) is not str or not seat or type(count) is not int or count < 1
                       or type(fingerprint) is not str or len(fingerprint) != 64
                       for seat, count, fingerprint in self.quotas)):
            raise GameRuleError("The affected-player search quotas are malformed")
        if (tuple(seat for seat, _ in self.chosen) != seats[:len(self.chosen)]
                or len(self.chosen) > len(self.quotas)
                or any(len(cards) > quota[1]
                       for (_, cards), quota in zip(self.chosen, self.quotas))):
            raise GameRuleError("The search choices changed their affected-player order or count")
        identities = tuple(card for _, cards in self.chosen for card in cards)
        if len({card.object_id for card in identities}) != len(identities):
            raise GameRuleError("Compensation search results require distinct original cards")

    def to_dict(self):
        return {"stack": self.stack_ref, "controller": self.controller,
                "quotas": [list(quota) for quota in self.quotas],
                "chosen": [[seat, [card.to_dict() for card in cards]] for seat, cards in self.chosen]}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"stack", "controller", "quotas", "chosen"}:
            raise GameRuleError("The compensation search continuation has a closed shape")
        try:
            quotas = tuple(tuple(row) for row in value["quotas"])
            chosen = tuple((seat, tuple(LibrarySelectionObjectIdentity.from_dict(card) for card in cards))
                           for seat, cards in value["chosen"])
            return cls(value["stack"], value["controller"], quotas, chosen)
        except (ValueError, TypeError, KeyError) as exc:
            raise GameRuleError("The compensation search continuation is malformed") from exc

    def next_instruction(self):
        state = {SEARCH_BATCH_FIELD: self.to_dict()}
        if len(self.chosen) == len(self.quotas):
            return {"op": "search", "schema_version": SEARCH_BATCH_VERSION, "commit": True, **state}
        seat, count, _ = self.quotas[len(self.chosen)]
        return {"op": "search", "schema_version": SEARCH_BATCH_VERSION, "searching_player": seat,
                "zone": "library", "selector": {"types": ["land"], "supertypes": ["basic"]},
                "count": {"minimum": 0, "maximum": count}, "destination": "battlefield",
                "reveal": False, "tapped": True, "optional": False, "shuffle_after": False, **state}

    def add_choice(self, seat, cards):
        return ExileLandSearchBatch(self.stack_ref, self.controller, self.quotas, (*self.chosen, (seat, tuple(cards))))


def _require_batch_item(host, item, actor):
    if item is None or item.kind not in {"spell", "spell_copy"} or item.controller != actor or (
        item.context.get("currently_resolving") is not True
    ):
        raise GameRuleError("Compensation requires its original resolving spell")
    program = host.semantics.get(item.semantic_key)
    if (program is None or EXILE_LAND_SEARCH_CAPABILITY not in program.capability_dependencies
            or not host.semantic_program_is_current_trusted(program)):
        raise GameRuleError("Compensation requires its admitted exile program")


def committed_exile_land_search(host, actor, controllers):
    counts = Counter(controllers)
    order = tuple(seat for seat in host.apnap_order() if seat != actor and counts[seat] > 0)
    if not order:
        return CommittedQuantityFollowup(())
    item = next((candidate for candidate in reversed(host.state.stack)
                 if candidate.context.get("currently_resolving") is True), None)
    _require_batch_item(host, item, actor)
    quotas = tuple((seat, counts[seat], _library_fingerprint(host, seat)) for seat in order)
    batch = ExileLandSearchBatch(item.ref, actor, quotas)
    return CommittedQuantityFollowup((FrozenMap(batch.next_instruction()),))


def validate_exile_land_search(effect):
    batch = ExileLandSearchBatch.from_dict(effect.get(SEARCH_BATCH_FIELD))
    supplied = {key: item for key, item in effect.items() if key != "_replacement_selections"}
    if (type(effect.get("schema_version")) is not int
            or FrozenMap(supplied) != FrozenMap(batch.next_instruction())):
        raise GameRuleError("The compensation search is outside its original phase")
    return batch


def verify_exile_land_search(host, item, effect):
    batch = validate_exile_land_search(effect)
    _require_batch_item(host, item, batch.controller)
    if (batch.stack_ref != item.ref or tuple(seat for seat, _, _ in batch.quotas) !=
            tuple(seat for seat in host.apnap_order() if seat in {row[0] for row in batch.quotas})):
        raise GameRuleError("The compensation batch lost its APNAP participants")
    if not effect.get("commit"):
        seat, _, before = batch.quotas[len(batch.chosen)]
        if _library_fingerprint(host, seat) != before:
            raise GameRuleError("An affected player's simultaneous search window changed")
    return batch


def advance_exile_land_search(host, context, refs, *, searched=True):
    batch = validate_exile_land_search(context.effect)
    seat = context.seat
    cards = tuple(host._resolve_object(seat, ref, zones={"library"}, owned_only=True) for ref in refs)
    chosen = tuple(LibrarySelectionObjectIdentity(card.object_id, card.logical_object_id, card.ref) for card in cards)
    advanced = batch.add_choice(seat, chosen)
    if searched:
        host._log(seat, "library.search", f"{seat} searched their library.",
                  {"player": seat, "source": context.stack_ref, "zone": "library"},
                  importance=2, changed_players=[seat])
    host._continue_resolution(
        stack_ref=context.stack_ref, effects=[advanced.next_instruction(), *context.continuation["remaining"]],
        destination=context.continuation.get("destination"), note=str(context.continuation.get("note") or ""),
        instruction_pointer=int(context.frame["instruction_pointer"]) + 1,
    )


def commit_exile_land_search(host, item, effect, batch):
    from ..zone_transitions import ZoneTransitionOwner

    entrants = {}
    for seat, cards in batch.chosen:
        for selected in cards:
            card = host._resolve_object(seat, selected.ref, zones={"library"}, owned_only=True)
            if ((card.object_id, card.logical_object_id) != (selected.object_id, selected.logical_object_id)
                    or not host._search_candidate_matches(card, {"types": ["land"], "supertypes": ["basic"]})):
                raise GameRuleError("A found basic land lost its original library incarnation")
            entrants[card.object_id] = seat
    owner = ZoneTransitionOwner(host)
    selections = tuple(effect.get("_replacement_selections") or ())
    if entrants:
        owner.move_cards_simultaneously(
            tuple((object_id, "battlefield") for object_id in entrants), reason=item.label,
            tapped=True, destination_controllers=entrants, putting_players=entrants,
            replacement_selections=selections,
        )
    elif selections:
        raise GameRuleError("A compensation batch without lands cannot accept entry choices")
    for seat, _, _ in batch.quotas:
        owner.shuffle_library(seat, reason=item.label)

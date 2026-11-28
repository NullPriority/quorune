"""Constructed sheet setup and exact physical name-sticker placements."""
from dataclasses import dataclass
import random
from collections.abc import Mapping

from ..errors import GameRuleError
from .name_sticker_sheets import NAME_STICKER_SHEETS

ACCESS = "name_sticker_access"
PLACEMENTS = "name_sticker_placements"
CAPABILITY = "characteristic.name_sticker.constructed"


@dataclass(frozen=True, slots=True)
class NameSticker:
    sheet: str
    slot: int

    def __post_init__(self):
        if self.sheet not in NAME_STICKER_SHEETS or type(self.slot) is not int or not 0 <= self.slot < 3:
            raise ValueError("Name sticker does not identify a represented slot")

    @property
    def key(self):
        return f"{self.sheet}:{self.slot}"

    @property
    def text(self):
        return NAME_STICKER_SHEETS[self.sheet][1][self.slot]

    @property
    def red_mana(self):
        return len(set(self.text.upper()) & set("AEIOUY"))

    @classmethod
    def parse(cls, key):
        if type(key) is not str:
            raise ValueError("Name sticker choice requires a slot ID")
        sheet, sep, slot = key.partition(":")
        if not sep or slot not in ("0", "1", "2"):
            raise ValueError("Name sticker choice requires a sheet and slot")
        return cls(sheet, int(slot))


@dataclass(frozen=True, slots=True)
class NameStickerPlacement:
    sticker: NameSticker
    position: int
    timestamp: int

    def __post_init__(self):
        if (not isinstance(self.sticker, NameSticker) or type(self.position) is not int or self.position < 0
                or type(self.timestamp) is not int or self.timestamp <= 0):
            raise ValueError("Name sticker placement requires its original slot, position and timestamp")

    @classmethod
    def from_dict(cls, row):
        if not isinstance(row, Mapping) or set(row) != {"sticker", "position", "timestamp"}:
            raise ValueError("Name sticker placement has a closed value")
        return cls(NameSticker.parse(row["sticker"]), row["position"], row["timestamp"])

    def to_dict(self):
        return {"sticker": self.sticker.key, "position": self.position, "timestamp": self.timestamp}


def registration(card_db, deck, *, seed, seat):
    entries = tuple(entry for entry in deck.entries if entry.board == "stickers")
    if not entries:
        return None
    sheets = []
    for entry in entries:
        if entry.quantity != 1:
            raise GameRuleError("Every sticker sheet in a Constructed registration must be unique")
        try:
            card = card_db.lookup(entry.name, fuzzy=False)
        except KeyError as exc:
            raise GameRuleError("Sticker sheet is absent from the pinned card catalog") from exc
        review = NAME_STICKER_SHEETS.get(card.oracle_id)
        if card.type_line != "Stickers" or review is None or review[0] != card.name:
            raise GameRuleError("Sticker sheet has no represented name-sticker slots")
        sheets.append(card.oracle_id)
    if len(sheets) < 10 or len(sheets) != len(set(sheets)):
        raise GameRuleError("Use zero sheets or at least ten distinct Constructed sticker sheets")
    registered = sorted(sheets)
    chosen = random.Random(f"{seed}|{seat}|constructed-stickers-v1").sample(registered, 3)
    return {"schema_version": 1, "registered": registered, "chosen": chosen}


def access(player):
    value = player.stats.get(ACCESS)
    if value is None:
        return None
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "registered", "chosen"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1):
        raise ValueError("Sticker setup has a closed schema")
    registered, picked = value["registered"], value["chosen"]
    if (not isinstance(registered, (list, tuple)) or not isinstance(picked, (list, tuple))
            or any(type(sheet) is not str or sheet not in NAME_STICKER_SHEETS for sheet in (*registered, *picked))
            or len(registered) < 10 or list(registered) != sorted(set(registered))
            or len(picked) != 3 or len(set(picked)) != 3 or not set(picked) <= set(registered)):
        raise ValueError("Sticker setup lost its distinct submitted and chosen sheets")
    return {"schema_version": 1, "registered": list(registered), "chosen": list(picked)}


def placements(card):
    from ..zone_transition_model import PUBLIC_ZONES

    raw = card.annotations.get(PLACEMENTS, ())
    if not isinstance(raw, (list, tuple)):
        raise ValueError("Name sticker placements must be an ordered list")
    result = tuple(NameStickerPlacement.from_dict(row) for row in raw)
    if len({row.sticker for row in result}) != len(result) or len({row.timestamp for row in result}) != len(result):
        raise ValueError("Name stickers and their timestamps must be discrete")
    if result and card.zone not in PUBLIC_ZONES:
        raise ValueError("Name stickers cannot remain on hidden objects")
    return result


def choices(state, seat):
    from ..zone_transition_model import PUBLIC_ZONES

    sheet_access = access(state.players[seat])
    if sheet_access is None:
        return ()
    used = {row.sticker for card in state.cards.values() if card.owner == seat and card.zone in PUBLIC_ZONES
            for row in placements(card)}
    return tuple(sticker for sheet in sheet_access["chosen"] for slot in range(3)
                 if (sticker := NameSticker(sheet, slot)) not in used)


def name_words(name):
    return [word for word in (name or "").split() if set(word) != {"_"}]


def name_with_sticker(name, text, position):
    words = name_words(name)
    words.insert(min(position, len(words)), text)
    return " ".join(words)


def characteristic_effects(card):
    from ..continuous_effect_model import (
        ContinuousEffect, ContinuousEffectDuration, ContinuousEffectOrigin,
        ContinuousObjectIdentity, ContinuousOperation, Layer,
    )

    identity = ContinuousObjectIdentity(card.object_id, card.logical_object_id)
    return tuple(ContinuousEffect(
        effect_id=f"name-sticker:{row.sticker.key}:{card.object_id}", source_id=card.object_id,
        layer=Layer.TEXT, sublayer="3", timestamp=row.timestamp, locked_objects=(identity,),
        origin=ContinuousEffectOrigin.RESOLUTION, duration=ContinuousEffectDuration.ZONE_OBJECT,
        operations=(ContinuousOperation("insert_name_sticker", {"text": row.sticker.text, "position": row.position}),),
    ) for row in placements(card))


OPERATION = "place_optional_source_name_sticker"
MECHANIC = "source-entry-name-sticker-mana"
EVENT = "permanent.enter.self"
DEPENDENCIES = (CAPABILITY, "mana.production.fixed_resolution", "trigger.effect.fixed_event",
                "trigger.event.normalized_zone_change", "trigger.placement.apnap")


def instruction():
    return {"op": OPERATION, "schema_version": 1, "player": "$controller"}


def node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) == 1 and target_schema is None and MECHANIC in mechanic_ids
            and effects[0] == instruction() and type(effects[0].get("schema_version")) is int):
        return DEPENDENCIES
    return ()


def program_dependencies(program):
    if not any(effect.get("op") == OPERATION for effect in program.effects):
        return ()
    if ((program.event, program.active_zone) != (EVENT, "battlefield")
            or program.event_condition is not None
            or program.handlers or program.cost_schema is not None
            or not node_capabilities(effects=program.effects, target_schema=program.target_schema,
                                     mechanic_ids=program.coverage)):
        raise ValueError("Name sticker mana requires the original self-entry trigger")
    return DEPENDENCIES


def is_closed_program(program):
    result = program_dependencies(program)
    return bool(result) and set(result) <= set(program.capability_dependencies)


def _resolving_source(host, item):
    if item is None or item.kind not in {"triggered_ability", "triggered_ability_copy"}:
        raise GameRuleError("Name sticker placement lost its resolving trigger")
    program = host.semantics.get(item.semantic_key)
    if (not item.context.get("currently_resolving") or program is None
            or not host.semantic_program_is_current_trusted(program) or not is_closed_program(program)):
        raise GameRuleError("Name sticker placement requires the original trusted ETB instruction")
    card = host.state.cards.get(item.source_object_id or "")
    if (card is None or card.zone != "battlefield" or card.phased_out or card.owner != item.controller
            or card.logical_object_id != item.context.get("source_logical_object_id")):
        return None
    if card.permanent_composition is not None:
        raise GameRuleError("Name stickers on composed permanents need their physical departure rules")
    return card


def choice_facts(host, item, effect):
    if effect.get("op") != OPERATION:
        return {}
    card = _resolving_source(host, item)
    if card is None:
        return {}
    name = host._effective_card_data(card).get("name")
    return {"source":card.logical_object_id, "object_id":card.object_id, "controller":card.controller,
            "name":name, "positions":len(name_words(name)) + 1,
            "stickers":tuple(sticker.key for sticker in choices(host.state, item.controller))}


@dataclass(frozen=True, slots=True)
class NameStickerPlacementIntent:
    actor: str
    payer: str
    stack_ref: str
    object_id: str
    logical_object_id: str
    sticker: NameSticker
    position: int

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (
                self.actor, self.payer, self.stack_ref, self.object_id, self.logical_object_id))
                or not isinstance(self.sticker, NameSticker) or type(self.position) is not int or self.position < 0):
            raise ValueError("Name sticker placement requires its actor, origin and offered word position")


def commit_placement(host, intent):
    item = next((row for row in host.state.stack if row.ref == intent.stack_ref), None)
    card = _resolving_source(host, item)
    if (card is None or item.controller != intent.payer or card.controller != intent.actor
            or card.object_id != intent.object_id or card.logical_object_id != intent.logical_object_id
            or intent.sticker not in choices(host.state, intent.payer)):
        raise GameRuleError("Name sticker placement no longer matches its current public object")
    before = host._effective_card_data(card).get("name")
    if intent.position > len(name_words(before)):
        raise GameRuleError("Name sticker word position is no longer available")
    from ..zone_transitions import ZoneTransitionOwner

    timestamp = ZoneTransitionOwner(host).next_timestamp()
    placement = NameStickerPlacement(intent.sticker, intent.position, timestamp)
    card.annotations[PLACEMENTS] = [*(row.to_dict() for row in placements(card)), placement.to_dict()]
    name = name_with_sticker(before, intent.sticker.text, intent.position)
    host._log(intent.actor, "sticker.name.placed", f"{intent.actor} named {card.ref} {name}.",
              {"object":card.ref,"sticker":intent.sticker.key,"position":intent.position,"name":name},
              changed_objects=[card.object_id], importance=2)
    return name

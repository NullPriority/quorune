"""Public origin of a committed discard caused by a resolving effect."""

from collections.abc import Mapping
from dataclasses import dataclass

_RESOLVING_KINDS = frozenset({"spell", "spell_copy", "activated_ability", "triggered_ability"})
_FIELDS = {"schema_version", "stack_ref", "controller", "kind"}


@dataclass(frozen=True, slots=True)
class DiscardEffectOrigin:
    stack_ref: str
    controller: str
    kind: str

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.stack_ref, self.controller, self.kind)):
            raise ValueError("Discard effect origin needs its sealed stack reference, controller and kind")
        if self.kind not in _RESOLVING_KINDS:
            raise ValueError("Only a resolving spell or ability can provide discard effect provenance")

    @classmethod
    def from_resolving_stack(cls, item):
        return cls(item.ref, item.controller, item.kind)

    def to_dict(self):
        return {"schema_version": 1, "stack_ref": self.stack_ref, "controller": self.controller, "kind": self.kind}

    @classmethod
    def from_dict(cls, raw):
        if (not isinstance(raw, Mapping) or set(raw) != _FIELDS
                or type(raw["schema_version"]) is not int or raw["schema_version"] != 1):
            raise ValueError("Discard effect origin needs its exact versioned shape")
        return cls(raw["stack_ref"], raw["controller"], raw["kind"])


def opponent_discard_origin(raw, *, discarding_player, opponents):
    if raw is None:
        return None
    source = DiscardEffectOrigin.from_dict(raw)
    if source.controller == discarding_player or source.controller not in opponents:
        return None
    return source


DISCARDED_CARD_CAST = "cast_discarded_card_after_source_counters"
DISCARD_EFFECT_ORIGIN_VERSION = 1
DISCARD_EFFECT_ORIGIN_VERSION_FIELD = "discard_effect_origin_version"
BOUND_DISCARD_EFFECT_ORIGIN = "_discard_effect_origin"
OPPONENT_SOURCE_DISCARD_EVENT = "card.discarded.opponent_effect"
DISCARD_BATCH_EVENT = 'card.discarded.batch'
DISCARD_BATCH_CAPABILITY = 'trigger.event.normalized_discard_batch'
DISCARD_BATCH_VARIANT = 'controller_discards_one_or_more_cards'
PUBLIC_DISCARD_DESTINATION_SCOPE = "public_discard_destination"


def discard_effect_origin_version_fields(version):
    if version is None:
        return {}
    if type(version) is not int or version != DISCARD_EFFECT_ORIGIN_VERSION:
        raise ValueError("Unsupported discard effect provenance version")
    return {DISCARD_EFFECT_ORIGIN_VERSION_FIELD: version}


def read_discard_effect_origin_version(data):
    if DISCARD_EFFECT_ORIGIN_VERSION_FIELD not in data:
        return None
    version = data[DISCARD_EFFECT_ORIGIN_VERSION_FIELD]
    if not discard_effect_origin_version_fields(version):
        raise ValueError("Discard effect provenance version cannot be null")
    return version


def validate_discard_effect_provenance(format_value, state):
    if read_discard_effect_origin_version(format_value) != getattr(state, DISCARD_EFFECT_ORIGIN_VERSION_FIELD, None):
        raise ValueError("Discard effect provenance does not match the initial checkpoint")


def _capture_discard_origin(state, item):
    enabled = bool(discard_effect_origin_version_fields(getattr(state, DISCARD_EFFECT_ORIGIN_VERSION_FIELD, None)))
    if enabled and item.kind in _RESOLVING_KINDS:
        return DiscardEffectOrigin.from_resolving_stack(item)
    return None


def bind_discard_effect_origin(state, item, effect):
    """Seal the resolving effect's public source before hand membership or replacements."""
    if effect.get("op") not in {"discard_whole_hands", "discard_random_cards", "discard"}:
        return effect
    origin = _capture_discard_origin(state, item)
    if BOUND_DISCARD_EFFECT_ORIGIN in effect and (
        origin is None or DiscardEffectOrigin.from_dict(effect[BOUND_DISCARD_EFFECT_ORIGIN]) != origin
    ):
        raise ValueError("The resolving discard effect's sealed origin changed")
    return {**effect, BOUND_DISCARD_EFFECT_ORIGIN: origin.to_dict()} if origin is not None else effect


def read_bound_discard_origin(effect, *, stack_ref, controller):
    if BOUND_DISCARD_EFFECT_ORIGIN not in effect:
        return None
    origin = DiscardEffectOrigin.from_dict(effect[BOUND_DISCARD_EFFECT_ORIGIN])
    if origin.stack_ref != stack_ref or origin.controller != controller:
        raise ValueError("Discard effect origin differs from the resolving stack source")
    return origin


def origin_for_resolving_hand_choice(state, item, effect):
    if item is None:
        return None
    operation = effect.get("op")

    hand_choices = {
        DISCARDED_CARD_CAST,
        "fixed_hand_inspection",
        "connive",
        "discard_draw_up_to",
        "discard_hands_then_draw",
        "ward_counter_unless_pay",
    }
    apnap_discard = operation == "commit_apnap_object_choices" and effect.get("event_code") == "choice.discard"
    if apnap_discard or operation in hand_choices:
        return _capture_discard_origin(state, item)
    return None


def event_source_zone(event, actual_zone):
    if event == OPPONENT_SOURCE_DISCARD_EVENT:
        return PUBLIC_DISCARD_DESTINATION_SCOPE
    return actual_zone


def authorized_legacy_discard_origin(state, effect, *, controller):
    if BOUND_DISCARD_EFFECT_ORIGIN not in effect:
        return None
    origin = DiscardEffectOrigin.from_dict(effect[BOUND_DISCARD_EFFECT_ORIGIN])
    item = next((row for row in state.stack if row.ref == origin.stack_ref), None)
    if (effect.get("op") != "discard" or item is None or origin.controller != controller
            or _capture_discard_origin(state, item) != origin):
        raise ValueError("Legacy discard origin requires its current resolving stack source")
    return origin

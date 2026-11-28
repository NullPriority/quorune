"""Bind one qualifying library card to ordinary casting during resolution."""

from collections.abc import Mapping
from dataclasses import dataclass
import re

from ..discover_model import discover_options_fingerprint
from ..errors import GameRuleError, StateInvariantError
from ..mana_value_limit import ManaValueLimitSpec
from ..replacement.immutable import FrozenMap, thaw_value
from .library_selection import LibrarySelectionObjectIdentity


OPERATION = "search_library_for_qualified_cast"
CAPABILITY = "library.search.fixed_qualified_cast"
MECHANIC = "fixed-qualified-library-cast"
DEPENDENCIES = (CAPABILITY, "library.search.fixed_counted", "library.search.restriction.static",
                "library.shuffle.fixed_controller")
_BASE = frozenset(("op", "player", "search_cast"))


@dataclass(frozen=True, slots=True)
class QualifiedLibraryCastSpec:
    maximum: ManaValueLimitSpec
    colors: tuple[str, ...]

    def __post_init__(self):
        if (not isinstance(self.maximum, ManaValueLimitSpec) or not isinstance(self.colors, tuple)
                or len(self.colors) != 2
                or any(type(color) is not str or len(color) != 1 or color not in "WUBRG" for color in self.colors)
                or self.colors != tuple(sorted(set(self.colors)))):
            raise ValueError("A qualified library cast requires a fixed value and two distinct colors")

    def effect(self, player="$controller"):
        return {"op": OPERATION, "player": player, "search_cast": {
            "schema_version": 1, "colors_any": list(self.colors), "type": "instant",
            "mana_value": self.maximum.to_dict(),
        }}

    @classmethod
    def from_effect(cls, effect, *, actor="$controller"):
        if not isinstance(effect, Mapping) or set(effect) != _BASE:
            raise ValueError("Qualified library cast requires its complete instruction")
        raw = effect.get("search_cast")
        if (not isinstance(raw, Mapping) or type(raw.get("schema_version")) is not int
                or not isinstance(raw.get("colors_any"), (list, tuple))
                or any(type(color) is not str or len(color) != 1 for color in raw["colors_any"])):
            raise ValueError("Qualified library cast selector is malformed")
        spec = cls(ManaValueLimitSpec.from_dict(raw.get("mana_value")), tuple(raw["colors_any"]))
        if FrozenMap(effect) != FrozenMap(spec.effect(actor)):
            raise ValueError("Qualified library cast selector and actor must retain their exact fields")
        return spec

    def matches(self, types, colors, mana_value):
        return ("instant" in types and not set(self.colors).isdisjoint(colors)
                and self.maximum.permits(mana_value))


def qualified_library_cast_payload_dependencies(effect):
    if not isinstance(effect, Mapping) or effect.get("op") != OPERATION:
        return ()
    QualifiedLibraryCastSpec.from_effect(effect)
    return DEPENDENCIES


def qualified_library_cast_node_capabilities(effects, target_schema):
    if len(effects) != 1 or target_schema is not None:
        return ()
    try:
        return qualified_library_cast_payload_dependencies(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()


def is_closed_qualified_library_cast_program(program):
    if (program.event, program.active_zone) != ("activate", "battlefield") or MECHANIC not in program.coverage:
        return False
    required = qualified_library_cast_node_capabilities(program.effects, program.target_schema)
    return bool(required) and set(required) <= set(program.capability_dependencies)


def chosen_library_cast(effect, actor, stack_ref):
    if not isinstance(effect, Mapping) or set(effect) != _BASE | {"phase", "stack", "selected"}:
        raise ValueError("The library cast requires its retained selection")
    if effect["phase"] != "cast" or effect["stack"] != stack_ref:
        raise ValueError("The library cast lost the original stack occurrence")
    spec = QualifiedLibraryCastSpec.from_effect({key: effect[key] for key in _BASE}, actor=actor)
    return spec, LibrarySelectionObjectIdentity.from_dict(effect["selected"])


@dataclass(frozen=True, slots=True)
class ResolveQualifiedLibraryCastIntent:
    actor: str
    stack_ref: str
    effect: FrozenMap
    options_fingerprint: str
    response: FrozenMap
    replacement_selections: tuple = ()

    def __post_init__(self):
        if (type(self.actor) is not str or not self.actor or type(self.stack_ref) is not str or not self.stack_ref
                or not isinstance(self.effect, FrozenMap) or not isinstance(self.response, FrozenMap)
                or type(self.options_fingerprint) is not str
                or re.fullmatch("[0-9a-f]{64}", self.options_fingerprint) is None
                or type(self.response.get("cost_option")) is not str or not self.response["cost_option"]
                or any(type(key) is not str or key.startswith("_") for key in self.response)):
            raise ValueError("The selected library cast requires its issued actor, option and price")
        chosen_library_cast(self.effect, self.actor, self.stack_ref)
        from ..semantic_runtime.zone_move_intent import _freeze_replacement_selections

        object.__setattr__(self, "replacement_selections",
                           _freeze_replacement_selections(self.replacement_selections, family="qualified library cast"))

    def identity(self):
        return {"actor": self.actor, "stack_ref": self.stack_ref, "effect": thaw_value(self.effect),
                "options_fingerprint": self.options_fingerprint, "response": thaw_value(self.response)}


def validate_qualified_library_cast_identity(value):
    fields = {"actor", "stack_ref", "effect", "options_fingerprint", "response"}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("Qualified library cast intent identity is malformed")
    return ResolveQualifiedLibraryCastIntent(
        value["actor"], value["stack_ref"], FrozenMap(value["effect"]),
        value["options_fingerprint"], FrozenMap(value["response"]),
    ).identity()


def _library_cast_permission(host, item, actor):
    if (item is None or item.kind != "activated_ability" or item.controller != actor
            or actor not in host.active_seats or not item.context.get("currently_resolving")):
        raise GameRuleError("The library cast lost its original resolving activation")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not is_closed_qualified_library_cast_program(program)
            or not host.semantic_program_is_current_trusted(program)):
        raise GameRuleError("The source has no trusted qualified library cast")
    return QualifiedLibraryCastSpec.from_effect(program.effects[0])


def _option_pairs(host, item, actor, effect):
    from .casting.face_queries import offered_cast_methods
    from .casting.proposal import build_cast_offer
    from .library_search_rules import library_search_object_ids

    original = _library_cast_permission(host, item, actor)
    spec, identity = chosen_library_cast(effect, actor, item.ref)
    card = host.state.cards.get(identity.object_id)
    if (spec != original or card is None or card.owner != actor or card.zone != "library"
            or card.ref != identity.ref or card.logical_object_id != identity.logical_object_id
            or card.object_id not in library_search_object_ids(host, actor, "library")):
        raise GameRuleError("The selected library card changed before casting")
    values = host._effective_card_data(card)
    types, _, _ = host._type_parts(values["type_line"])
    if not spec.matches(types, values["colors"], values["mana_value"]):
        raise GameRuleError("The selected library card no longer matches the search")
    pairs = []
    for method, face in offered_cast_methods(host, card):
        if method is not None:
            continue
        offered = build_cast_offer(host, actor, card, face_name=face,
                                   resolving_payment="free", resolving_origin="library")
        if offered.offer is None:
            continue
        offer = offered.offer.to_dict(include_fingerprint=False)
        for price in offer["cost_options"]:
            price = {**price, "face": offer.get("face"), "cast_method": None}
            fingerprint = discover_options_fingerprint(({"object": card.logical_object_id, "price": price},))
            public = {**price, "id": f"searched-library:{fingerprint}", "card": card.ref,
                      "label": f"{offer['label']} — {price.get('label', 'Cast without paying its mana cost')}"}
            pairs.append((public, price, card))
    return tuple(pairs)


def qualified_library_cast_options(host, *, item, actor, effect):
    if effect.get("phase") != "cast":
        original = _library_cast_permission(host, item, actor)
        instruction = {key: effect[key] for key in _BASE}
        if QualifiedLibraryCastSpec.from_effect(instruction, actor=actor) != original:
            raise GameRuleError("The authorized library search selector changed")
        return ()
    raw = {key: value for key, value in effect.items() if key != "_options_fingerprint"}
    return tuple(row for row, _, _ in _option_pairs(host, item, actor, raw))


def execute_qualified_library_cast(host, intent):
    from .resolving_cast_payments import commit_resolving_cast

    item = next((entry for entry in host.state.stack if entry.ref == intent.stack_ref), None)
    pairs = _option_pairs(host, item, intent.actor, intent.effect)
    if discover_options_fingerprint(tuple(row for row, _, _ in pairs)) != intent.options_fingerprint:
        raise GameRuleError("The issued qualified library cast changed")
    choice = tuple((price, card) for row, price, card in pairs if row["id"] == intent.response["cost_option"])
    if len(choice) != 1:
        raise GameRuleError("Choose one issued library cast option")
    (price, card), = choice
    response = thaw_value(intent.response)
    if (response.get("card", card.ref) != card.ref or response.get("from", "library") != "library"
            or response.get("face") not in (None, price["face"]) or response.get("cast_method") is not None):
        raise GameRuleError("The response changed the authorized library card or face")
    response.update(card=card.ref, cost_option=price["id"], auto_pay=True)
    response["from"] = "library"
    if price["face"] is not None:
        response["face"] = price["face"]
    previous = card.logical_object_id
    commit_resolving_cast(host, actor=intent.actor, response=response, origin="library",
                          payment_id=f"{item.ref}:searched-library:{previous}",
                          replacement_selections=intent.replacement_selections)
    current = host.state.cards[card.object_id]
    if (current.zone != "stack" or current.controller != intent.actor or current.logical_object_id == previous
            or not any(entry.kind == "spell" and entry.card_object_id == current.object_id for entry in host.state.stack)):
        raise StateInvariantError("The authorized library cast did not create a spell")
    return current.ref

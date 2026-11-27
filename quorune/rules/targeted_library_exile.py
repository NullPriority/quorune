"""Explicit role separation for mandatory unqualified target-library exile searches."""
from collections.abc import Mapping
from dataclasses import dataclass
from ..replacement.immutable import FrozenMap
from ..errors import GameRuleError
from ..library_search_model import TARGET_NAME_MULTIZONE_EXILE_VERSION, TARGET_OPPONENT_PRIVATE_PLAY_SEARCH_VERSION


class TargetedLibrarySearchError(GameRuleError, ValueError):
    """A targeted private search cannot legally begin or resume."""

TARGET_LIBRARY_EXILE_CAPABILITY = 'library.search.targeted_fixed_exile'
TARGET_LIBRARY_EXILE_VERSION = 9
PRIVATE_TARGET_LIBRARY_PLAY_CAPABILITY = "library.search.target_opponent_private_exile_play"
TARGET_NAME_MULTIZONE_EXILE_CAPABILITY = "library.search.target_name_multizone_exile"
TARGET_NAME_MULTIZONE_CAPTURE = "_target_name_exile_capture"
_TARGET_NAME_MULTIZONE_MATCHES = "_target_name_exile_matches"
_TARGET_NAME_MULTIZONE_ZONES = ("graveyard", "hand", "library")
_TARGET_NAME_MULTIZONE_QUALIFIERS = frozenset(("artifact", "nonblack creature", "nonbasic land", "enchantment"))


@dataclass(frozen=True, slots=True)
class TargetNameMultizoneExileSpec:
    qualifier: str

    def __post_init__(self):
        if type(self.qualifier) is not str or self.qualifier not in _TARGET_NAME_MULTIZONE_QUALIFIERS:
            raise TargetedLibrarySearchError("Name exile requires a closed permanent target")

    def target_schema(self):
        schema = {"zones": ["battlefield"], "categories": ["permanent"], "count": 1,
                  "types_any": [self.qualifier.split()[-1]]}
        if self.qualifier == "nonblack creature":
            schema["colors_none"] = ["B"]
        elif self.qualifier == "nonbasic land":
            schema["supertypes_none"] = ["basic"]
        return schema

    def effect(self, *, searching_player="$controller", target="$target.0"):
        return {"op": "search", "schema_version": TARGET_NAME_MULTIZONE_EXILE_VERSION,
                "searching_player": searching_player, "name_target": target, "target_qualifier": self.qualifier}

    @classmethod
    def from_effect(cls, effect, *, actor=None):
        if not isinstance(effect, Mapping):
            raise TargetedLibrarySearchError("Name exile requires its exact typed instruction")
        if type(effect.get("schema_version")) is not int:
            raise TargetedLibrarySearchError("Name exile requires its explicit version")
        target = "$target.0" if actor is None else effect.get("name_target")
        searcher = "$controller" if actor is None else actor
        spec = cls(effect.get("target_qualifier"))
        if (type(target) is not str or not target or (actor is not None and target.startswith("$"))
                or FrozenMap(effect) != FrozenMap(spec.effect(searching_player=searcher, target=target))):
            raise TargetedLibrarySearchError("Name exile requires its original resolving controller and target")
        return spec

    def capabilities(self):
        shared = (
            TARGET_NAME_MULTIZONE_EXILE_CAPABILITY, "library.search.fixed_counted",
            "library.search.restriction.static", "zone.change.destination_replacement",
            "permanent.exile.effect", "target.revalidate_resolution",
        )
        if self.qualifier in {"nonblack creature", "nonbasic land"}:
            return (*shared, "target.permanent.characteristic_predicate")
        return shared


def _target_name_search_effect(actor, owner, capture, zones):
    return {
        "op": "search", "schema_version": TARGET_NAME_MULTIZONE_EXILE_VERSION,
        "searching_player": actor, "library_player": owner,
        "zone": list(zones), "selector": {},
        "count": {"minimum": 0, "maximum": "all_eligible"}, "optional": True,
        "destination": "exile", "reveal": False, "shuffle_after": True,
        TARGET_NAME_MULTIZONE_CAPTURE: capture,
    }


def prepare_target_name_multizone_exile(host, item, effect):
    from ..carddb_characteristics import public_object_rules_names
    from .library_selection import LibrarySelectionObjectIdentity

    actor = item.controller
    TargetNameMultizoneExileSpec.from_effect(effect, actor=actor)
    target = host._resolve_object(actor, effect["name_target"], zones={"battlefield"})
    if target.phased_out:
        raise TargetedLibrarySearchError("The original name-exile target is unavailable")
    names = public_object_rules_names(target, host._effective_card_data(target), host.card_record(target))
    capture = {
        "target": LibrarySelectionObjectIdentity(target.object_id, target.logical_object_id, target.ref).to_dict(),
        "names": sorted(set(names)), "stack": item.ref,
    }
    return (
        {"op": "exile_permanent", "card": target.ref},
        _target_name_search_effect(actor, target.controller, capture, _TARGET_NAME_MULTIZONE_ZONES),
    )


def _target_name_multizone_scope(effect, actor):
    from .library_selection import LibrarySelectionObjectIdentity

    base = {key: value for key, value in effect.items()
            if key not in _SEARCH_RESUME_FIELDS | {_TARGET_NAME_MULTIZONE_MATCHES}}
    capture = base.get(TARGET_NAME_MULTIZONE_CAPTURE)
    zones = base.get("zone")
    owner = base.get("library_player")
    if (not isinstance(capture, Mapping) or set(capture) != {"target", "names", "stack"}
            or type(capture.get("stack")) is not str or not capture["stack"]
            or not isinstance(zones, (list, tuple))
            or tuple(zones) not in (_TARGET_NAME_MULTIZONE_ZONES, _TARGET_NAME_MULTIZONE_ZONES[:-1])
            or any(type(value) is not str or not value or value.startswith("$") for value in (actor, owner))):
        raise TargetedLibrarySearchError("The bound name search lost its original scope")
    names = capture["names"]
    if (not isinstance(names, (list, tuple)) or any(type(name) is not str or not name for name in names)
            or tuple(names) != tuple(sorted(set(names)))):
        raise TargetedLibrarySearchError("The bound name search requires current canonical rules names")
    try:
        LibrarySelectionObjectIdentity.from_dict(capture["target"])
    except (TypeError, ValueError, KeyError) as exc:
        raise TargetedLibrarySearchError("The captured name-search target identity is malformed") from exc
    if (type(base.get("schema_version")) is not int or base.get("optional") is not True
            or base.get("shuffle_after") is not True or base.get("reveal") is not False
            or not isinstance(base.get("count"), Mapping) or type(base["count"].get("minimum")) is not int
            or FrozenMap(base) != FrozenMap(_target_name_search_effect(actor, owner, capture, zones))):
        raise TargetedLibrarySearchError("The bound name search changed its actor, disposition or shuffle")
    return owner, tuple(zones), tuple(names), capture["stack"]


def validate_bound_target_name_multizone_search(effect):
    _target_name_multizone_scope(effect, effect.get("searching_player"))


def _target_name_multizone_entries(host, actor, effect):
    from ..carddb_characteristics import library_card_rules_names
    from .library_search_rules import library_search_constraint, library_search_object_ids
    from .library_selection import LibrarySelectionObjectIdentity

    owner, zones, names, _stack = _target_name_multizone_scope(effect, actor)
    if actor not in host.state.active_seats() or owner not in host.state.active_seats():
        raise TargetedLibrarySearchError("Name search requires its active searcher and target controller")
    expected = _TARGET_NAME_MULTIZONE_ZONES
    if library_search_constraint(host, actor).prohibited:
        expected = expected[:-1]
    if zones != expected:
        raise TargetedLibrarySearchError("The authorized name-search window changed")
    names = frozenset(names)
    entries = []
    for zone in zones:
        for object_id in library_search_object_ids(host, owner, zone, searching_player=actor):
            card = host.state.cards[object_id]
            record = host.card_record(card)
            if card.zone != zone or card.owner != owner:
                raise TargetedLibrarySearchError("A name search card left the instructed zone or player")
            if record is not None and not names.isdisjoint(library_card_rules_names(record)):
                identity = LibrarySelectionObjectIdentity(card.object_id, card.logical_object_id, card.ref)
                entries.append({"zone": zone, "identity": identity.to_dict()})
    return tuple(entries)


def bind_target_name_multizone_search(host, actor, effect, stack_ref):
    from .library_search_rules import library_search_constraint

    _, zones, _, stack = _target_name_multizone_scope(effect, actor)
    if (_TARGET_NAME_MULTIZONE_MATCHES in effect or _SEARCH_RESUME_FIELDS & set(effect)
            or zones != _TARGET_NAME_MULTIZONE_ZONES or stack != stack_ref):
        raise TargetedLibrarySearchError("A fresh name search must follow its original target exile")
    if library_search_constraint(host, actor).prohibited:
        effect = {**effect, "zone": list(zones[:-1])}
    return {**effect, _TARGET_NAME_MULTIZONE_MATCHES: _target_name_multizone_entries(host, actor, effect)}


def bind_resolving_targeted_library_search(host, item, effect, *, actor):
    """Bind the applicable version without sharing its original actor authority."""
    if effect.get("schema_version") != TARGET_NAME_MULTIZONE_EXILE_VERSION:
        if effect.get("schema_version") == TARGET_OPPONENT_PRIVATE_PLAY_SEARCH_VERSION:
            require_private_target_library_play(host, item, effect, actor)
        return bind_targeted_library_exile(host, actor, effect)
    if actor != item.controller:
        raise GameRuleError("The private name search changed its resolving controller")
    return bind_target_name_multizone_search(host, actor, effect, item.ref)


def _target_name_multizone_card_groups(host, actor, effect):
    current = _target_name_multizone_entries(host, actor, effect)
    raw = effect.get(_TARGET_NAME_MULTIZONE_MATCHES)
    if not isinstance(raw, (list, tuple)) or FrozenMap({"entries": raw}) != FrozenMap({"entries": current}):
        raise TargetedLibrarySearchError("The captured public and hidden name candidates changed")
    mandatory, optional = [], []
    for row in current:
        card = host.state.cards[row["identity"]["object_id"]]
        (mandatory if row["zone"] == "graveyard" else optional).append(card)
    return tuple(mandatory), tuple(optional)


def target_name_multizone_search_view(host, actor, effect):
    from .library_search_rules import library_search_object_ids

    owner, zones, _, _ = _target_name_multizone_scope(effect, actor)
    result = {}
    for zone, field in (("hand", "searched_hand"), ("library", "search_window")):
        if zone in zones:
            cards = (host.state.cards[oid] for oid in library_search_object_ids(host, owner, zone, searching_player=actor))
            result[field] = [{"id": card.ref, "name": card.printed_name} for card in cards]
    return result


@dataclass(frozen=True, slots=True)
class TargetLibraryExileSpec:
    count: int
    schema_version: int = TARGET_LIBRARY_EXILE_VERSION

    def __post_init__(self):
        if (type(self.schema_version) is not int
                or self.schema_version not in {TARGET_LIBRARY_EXILE_VERSION, TARGET_OPPONENT_PRIVATE_PLAY_SEARCH_VERSION}
                or type(self.count) is not int or self.count < 1 or self.private_play and self.count != 1):
            raise ValueError("Target-library exile requires its exact version and fixed count")

    @property
    def private_play(self):
        return self.schema_version == TARGET_OPPONENT_PRIVATE_PLAY_SEARCH_VERSION

    def target_schema(self):
        schema = {'zones': ['player'], 'categories': ['player'], 'count': 1}
        if self.private_play:
            schema["player_relation"] = "opponent"
        return schema

    def effect(self, *, searching_player='$controller', library_player='$target.0'):
        if any(type(value) is not str or not value for value in (searching_player, library_player)):
            raise ValueError('Target-library search requires separate explicit searching and library players')
        return {'op': 'search', 'schema_version': self.schema_version,
            'searching_player': searching_player, 'library_player': library_player,
            'zone': 'library', 'selector': {},
            'count': {'minimum': self.count, 'maximum': self.count},
            'optional': False, 'destination': 'exile', 'reveal': False, 'shuffle_after': True}

    @classmethod
    def from_effect(cls, effect, *, searching_player='$controller', library_player='$target.0'):
        if not isinstance(effect, Mapping):
            raise ValueError('Target-library exile instruction must be a mapping')
        counts = effect.get('count')
        if not isinstance(counts, Mapping) or set(counts) != {'minimum', 'maximum'}:
            raise ValueError('Target-library exile requires exact count bounds')
        if type(counts['minimum']) is not int or any(type(effect.get(key)) is not bool for key in ('optional', 'reveal', 'shuffle_after')):
            raise ValueError('Search counts and flags must retain their exact scalar types')
        spec = cls(counts['maximum'], effect.get('schema_version'))
        if FrozenMap(effect) != FrozenMap(spec.effect(searching_player=searching_player, library_player=library_player)):
            raise ValueError('Target-library exile roles and fields are closed')
        return spec


TARGET_LIBRARY_IDENTITIES = '_target_library_identities'
_SEARCH_RESUME_FIELDS = frozenset({'_search_completion', '_replacement_selections'})


def bound_target_library_exile(effect, actor):
    if not isinstance(effect, Mapping):
        raise TargetedLibrarySearchError('Target-library search effect must be a mapping')
    base = {key: value for key, value in effect.items()
        if key not in _SEARCH_RESUME_FIELDS | {TARGET_LIBRARY_IDENTITIES}}
    owner = base.get('library_player')
    try:
        spec = TargetLibraryExileSpec.from_effect(base, searching_player=actor, library_player=owner)
    except (ValueError, TypeError, KeyError) as exc:
        raise TargetedLibrarySearchError(str(exc)) from exc
    if spec.private_play and owner == actor:
        raise TargetedLibrarySearchError("Private play search requires a distinct opponent library")
    return spec, owner


def require_private_target_library_play(host, item, effect, actor):
    spec, owner = bound_target_library_exile(effect, actor)
    program = host.semantics.get(item.semantic_key)
    if (
        not spec.private_play or item.controller != actor or item.kind not in {"spell", "spell_copy"}
        or item.context.get("targets_revalidated") is not True or tuple(item.targets) != (owner,)
        or item.context.get("currently_resolving") is not True or program is None
        or (program.event, program.active_zone) != ("resolve", "stack")
        or not host.semantic_program_is_current_trusted(program)
        or PRIVATE_TARGET_LIBRARY_PLAY_CAPABILITY not in program.capability_dependencies
        or PRIVATE_TARGET_LIBRARY_PLAY_CAPABILITY not in targeted_library_exile_node_capabilities(
            effects=program.effects, target_schema=program.target_schema,
        )
    ):
        raise TargetedLibrarySearchError("Private play requires its exact trusted resolving opponent search")


def target_library_entries(host, actor, effect):
    from .library_search_rules import library_search_object_ids
    from .library_selection import LibrarySelectionObjectIdentity
    _spec, owner = bound_target_library_exile(effect, actor)
    if actor not in host.state.active_seats() or owner not in host.state.active_seats():
        raise TargetedLibrarySearchError('Target-library search requires its active searcher and library owner')
    ids = library_search_object_ids(host, owner, 'library', searching_player=actor)
    return tuple(LibrarySelectionObjectIdentity(host.state.cards[key].object_id,
        host.state.cards[key].logical_object_id, host.state.cards[key].ref) for key in ids)


def bind_targeted_library_exile(host, actor, effect):
    if TARGET_LIBRARY_IDENTITIES in effect or _SEARCH_RESUME_FIELDS & set(effect):
        raise TargetedLibrarySearchError('A fresh targeted search cannot contain a saved completion')
    entries = target_library_entries(host, actor, effect)
    return {**dict(effect), TARGET_LIBRARY_IDENTITIES: tuple(entry.to_dict() for entry in entries)}


def targeted_library_exile_cards(host, actor, effect, selected=None):
    from .library_selection import LibrarySelectionObjectIdentity
    spec, owner = bound_target_library_exile(effect, actor)
    raw = effect.get(TARGET_LIBRARY_IDENTITIES)
    if not isinstance(raw, (list, tuple)):
        raise TargetedLibrarySearchError('Target-library search has no pinned private identities')
    try:
        saved = tuple(LibrarySelectionObjectIdentity.from_dict(value) for value in raw)
    except (ValueError, TypeError, KeyError) as exc:
        raise TargetedLibrarySearchError(str(exc)) from exc
    current = target_library_entries(host, actor, effect)
    if saved != current:
        raise TargetedLibrarySearchError('Target-library search window or incarnation changed')
    by_ref = {entry.ref: host.state.cards[entry.object_id] for entry in current}
    if selected is not None and (not isinstance(selected, (list, tuple))
            or any(type(ref) is not str for ref in selected)
            or len(selected) != min(spec.count, len(current))):
        raise TargetedLibrarySearchError('An unqualified search must select its full available fixed count')
    refs = tuple(by_ref) if selected is None else tuple(selected)
    if len(refs) != len(set(refs)) or any(ref not in by_ref for ref in refs):
        raise TargetedLibrarySearchError('Target-library search selected foreign or repeated references')
    if any(by_ref[ref].zone != 'library' or by_ref[ref].owner != owner for ref in refs):
        raise TargetedLibrarySearchError('Target-library search object left the instructed owner library')
    return tuple(by_ref[ref] for ref in refs)


def targeted_library_owner(host, actor, effect):
    if effect.get("schema_version") == TARGET_NAME_MULTIZONE_EXILE_VERSION:
        owner, _, _, _ = _target_name_multizone_scope(effect, actor)
    else:
        _spec, owner = bound_target_library_exile(effect, actor)
    if actor not in host.state.players or owner not in host.state.players:
        raise TargetedLibrarySearchError('Target-library search requires known player roles')
    return owner


def targeted_library_departed(host, actor, effect):
    return targeted_library_owner(host, actor, effect) not in host.state.active_seats()


def continue_departed_target_search(host, context):
    host._continue_resolution(stack_ref=context.stack_ref,
        effects=[dict(value) for value in context.continuation.get('remaining', ())],
        destination=context.continuation.get('destination'),
        note=str(context.continuation.get('note') or ''),
        instruction_pointer=int(context.frame.get('instruction_pointer', 0)) + 1)


def targeted_library_search_options(host, actor, effect):
    if targeted_library_departed(host, actor, effect):
        return []
    if effect.get("schema_version") == TARGET_NAME_MULTIZONE_EXILE_VERSION:
        _, cards = _target_name_multizone_card_groups(host, actor, effect)
    else:
        cards = targeted_library_exile_cards(host, actor, effect)
    return [{"id": card.ref, "name": card.printed_name} for card in cards]


def move_targeted_library_exile(host, context, values, replacement_selections=()):
    from ..zone_transitions import ZoneTransitionOwner
    if context.effect.get("schema_version") == TARGET_NAME_MULTIZONE_EXILE_VERSION:
        _, _, _, stack = _target_name_multizone_scope(context.effect, context.seat)
        if stack != context.stack_ref or context.seat != context.item.controller:
            raise TargetedLibrarySearchError("The name search changed its original stack occurrence or actor")
        required, optional = _target_name_multizone_card_groups(host, context.seat, context.effect)
        legal = {card.ref: card for card in optional}
        if len(values) != len(set(values)) or any(ref not in legal for ref in values):
            raise TargetedLibrarySearchError("The name search selected a foreign or repeated hidden candidate")
        cards = (*required, *(legal[ref] for ref in values))
    else:
        cards = targeted_library_exile_cards(host, context.seat, context.effect, values)
    if not cards:
        return []
    if context.effect.get("schema_version") == TARGET_OPPONENT_PRIVATE_PLAY_SEARCH_VERSION:
        from ..private_opponent_library_cast_model import PrivateLibrarySearchPlaySource

        require_private_target_library_play(host, context.item, context.effect, context.seat)
        card, = cards
        source = PrivateLibrarySearchPlaySource(context.stack_ref, card.owner, card.logical_object_id)
        return [ZoneTransitionOwner(host).move_card(
            card.object_id, "exile", reason=context.item.label + " search", log=False,
            private_library_exile_viewer=context.seat, private_library_cast_source=source,
            private_replacement_journal=True, replacement_selections=replacement_selections,
        )]
    return ZoneTransitionOwner(host).move_cards_simultaneously(
        tuple((card.object_id, 'exile') for card in cards),
        reason=context.item.label + ' search', log=False,
        replacement_selections=replacement_selections)


TARGET_LIBRARY_EXILE_MECHANIC = 'targeted-fixed-library-exile'
TARGET_LIBRARY_EXILE_DEPENDENCIES = (
    TARGET_LIBRARY_EXILE_CAPABILITY, 'library.search.fixed_counted',
    'library.search.restriction.static', 'zone.change.destination_replacement',
    'target.revalidate_resolution',
)

def targeted_library_exile_payload_dependencies(effect):
    if not isinstance(effect, Mapping) or effect.get('op') != 'search':
        return ()
    version = effect.get('schema_version')
    if type(version) is int and version == TARGET_NAME_MULTIZONE_EXILE_VERSION:
        return TargetNameMultizoneExileSpec.from_effect(effect).capabilities()
    if type(version) is int and version in {TARGET_LIBRARY_EXILE_VERSION, TARGET_OPPONENT_PRIVATE_PLAY_SEARCH_VERSION}:
        spec = TargetLibraryExileSpec.from_effect(effect)
        return (*TARGET_LIBRARY_EXILE_DEPENDENCIES, *((PRIVATE_TARGET_LIBRARY_PLAY_CAPABILITY,) if spec.private_play else ()))
    if 'library_player' in effect or TARGET_LIBRARY_IDENTITIES in effect:
        raise TargetedLibrarySearchError('Legacy search cannot declare targeted-library authority')
    return ()

def targeted_library_exile_node_capabilities(*, effects, target_schema, mechanic_ids=()):
    if len(effects) != 1:
        return ()
    try:
        named = effects[0].get("schema_version") == TARGET_NAME_MULTIZONE_EXILE_VERSION
        spec = (TargetNameMultizoneExileSpec.from_effect(effects[0]) if named
                else TargetLibraryExileSpec.from_effect(effects[0]))
    except (ValueError, TypeError, KeyError):
        return ()
    if FrozenMap(target_schema or {}) != FrozenMap(spec.target_schema()):
        return ()
    return spec.capabilities() if named else targeted_library_exile_payload_dependencies(effects[0])

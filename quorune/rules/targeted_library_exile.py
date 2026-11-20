"""Explicit role separation for mandatory unqualified target-library exile searches."""
from collections.abc import Mapping
from dataclasses import dataclass
from ..replacement.immutable import FrozenMap
from ..errors import GameRuleError


class TargetedLibrarySearchError(GameRuleError, ValueError):
    """A targeted private search cannot legally begin or resume."""

TARGET_LIBRARY_EXILE_CAPABILITY = 'library.search.targeted_fixed_exile'
TARGET_LIBRARY_EXILE_VERSION = 9


@dataclass(frozen=True, slots=True)
class TargetLibraryExileSpec:
    count: int
    schema_version: int = TARGET_LIBRARY_EXILE_VERSION

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != TARGET_LIBRARY_EXILE_VERSION
                or type(self.count) is not int or self.count < 1):
            raise ValueError('Target-library exile requires a positive fixed count and explicit version nine')

    def target_schema(self):
        return {'zones': ['player'], 'categories': ['player'], 'count': 1}

    def effect(self, *, searching_player='$controller', library_player='$target.0'):
        if any(type(value) is not str or not value for value in (searching_player, library_player)):
            raise ValueError('Target-library search requires separate explicit searching and library players')
        return {'op': 'search', 'schema_version': TARGET_LIBRARY_EXILE_VERSION,
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
    return spec, owner


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
    return [{'id':card.ref, 'name':card.printed_name}
        for card in targeted_library_exile_cards(host, actor, effect)]


def move_targeted_library_exile(host, context, values, replacement_selections=()):
    from ..zone_transitions import ZoneTransitionOwner
    cards = targeted_library_exile_cards(host, context.seat, context.effect, values)
    if not cards:
        return []
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
    if type(version) is int and version == TARGET_LIBRARY_EXILE_VERSION:
        TargetLibraryExileSpec.from_effect(effect)
        return TARGET_LIBRARY_EXILE_DEPENDENCIES
    if 'library_player' in effect or TARGET_LIBRARY_IDENTITIES in effect:
        raise TargetedLibrarySearchError('Legacy search cannot declare targeted-library authority')
    return ()

def targeted_library_exile_node_capabilities(*, effects, target_schema, mechanic_ids=()):
    if len(effects) != 1:
        return ()
    try:
        spec = TargetLibraryExileSpec.from_effect(effects[0])
    except (ValueError, TypeError, KeyError):
        return ()
    if FrozenMap(target_schema or {}) != FrozenMap(spec.target_schema()):
        return ()
    return TARGET_LIBRARY_EXILE_DEPENDENCIES

"""Prepare and commit one melded entry through ordinary zone and composition owners."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from .composition_snapshot import CompositionObjectSnapshot
from .composition_storage import PermanentCompositionStorage
from .continuous_effect_model import ContinuousObjectIdentity
from .entry_copy import COPIABLE_ORACLE_FIELD
from .errors import GameRuleError
from .game_object_store import GameObjectStore
from .melded_composition import MeldedCompositionFace
from .permanent_composition import PermanentComponent, PermanentComposition
from .replacement.immutable import FrozenMap, thaw_value
from .zone_transition_journal import finish_self_graveyard_shuffles, prepare_zone_side_effects
from .zone_transition_model import entry_putting_player
from .zone_trigger_events import ZoneTransitionKind


@dataclass(frozen=True, slots=True)
class PreparedMeldEntry:
    anchor: ContinuousObjectIdentity
    companion: ContinuousObjectIdentity
    face_oracle_id: str
    face_values: FrozenMap
    owner: str

    def __post_init__(self):
        if (not isinstance(self.anchor, ContinuousObjectIdentity)
                or not isinstance(self.companion, ContinuousObjectIdentity)
                or self.anchor.object_id == self.companion.object_id
                or not isinstance(self.face_values, Mapping)
                or self.face_values.get(COPIABLE_ORACLE_FIELD) != self.face_oracle_id
                or self.face_values.get("mana_cost") != ""
                or type(self.owner) is not str or not self.owner):
            raise ValueError("Meld entry requires its actual two exile cards and their combined face")
        object.__setattr__(self, "face_values", FrozenMap(self.face_values))


def _physical_exile_member(host, identity, owner):
    card = host.state.cards.get(identity.object_id)
    if (card is None or card.logical_object_id != identity.logical_object_id or card.zone != "exile"
            or card.owner != owner or card.is_token or not card.is_card_object or card.face_down):
        raise GameRuleError("The original physical meld exile card is unavailable")
    return card


def prepare_meld_entry(host, anchor, companion, result, *, actor):
    anchor_id = ContinuousObjectIdentity(anchor.object_id, anchor.logical_object_id)
    companion_id = ContinuousObjectIdentity(companion.object_id, companion.logical_object_id)
    for identity in (anchor_id, companion_id):
        _physical_exile_member(host, identity, actor)
    front_values = [host.card_record(card).mana_value for card in (anchor, companion)]
    if result.layout != "meld" or result.mana_value != sum(front_values):
        raise GameRuleError("The combined printed face does not agree with its physical cards")
    face = replace(anchor, oracle_id=result.oracle_id, printed_name=result.name,
                   printing=None, zone="battlefield", active_face=None, permanent_composition=None)
    values = dict(host._copyable_characteristics(face))
    values[COPIABLE_ORACLE_FIELD] = result.oracle_id
    return PreparedMeldEntry(anchor_id, companion_id, result.oracle_id, values, actor)


def meld_entry_characteristics(card, entry, destination):
    if (not isinstance(entry, PreparedMeldEntry) or card.object_id != entry.anchor.object_id
            or card.logical_object_id != entry.anchor.logical_object_id or destination != "battlefield"
            or card.zone != "exile" or card.owner != entry.owner):
        raise GameRuleError("Prospective meld entry lost its original exile anchor")
    values = thaw_value(entry.face_values)
    return values, str(values["type_line"])


def commit_melded_components(owner, root, entry):
    partner = _physical_exile_member(owner.host, entry.companion, entry.owner)
    root_part = CompositionObjectSnapshot.capture(
        root, copiable_values=owner.host._copyable_characteristics(root), controller=root.controller,
    )
    partner_part = CompositionObjectSnapshot.capture(
        partner, copiable_values=owner.host._copyable_characteristics(partner), controller=partner.owner, schema_version=2,
    )
    parts = (root_part, partner_part)
    components = tuple(PermanentComponent(part.identity, part.card_state["owner"],
                                          commander_designation_id=part.card_state.get("commander_designation_id"))
                       for part in parts)
    topology = PermanentComposition(root_part.identity, components)
    face = MeldedCompositionFace(topology.physical_object_ids, entry.face_oracle_id, entry.face_values, root.zone_timestamp)
    storage = PermanentCompositionStorage(topology, parts, melded_face=face)
    owner.remove_from_zone(partner)
    GameObjectStore(owner.host).commit(removals=(partner.object_id,))
    return storage


def commit_meld_entry(owner, entry, *, reason, replacement_selections=()):
    root = _physical_exile_member(owner.host, entry.anchor, entry.owner)
    _physical_exile_member(owner.host, entry.companion, entry.owner)
    with owner.host.transaction():
        plan = owner._prepare_move(
            root, "battlefield", controller=entry.owner, tapped=None, entry_pay_life=False,
            enter_face=None, battle_protector=None, aura_target_ref=None, resolving_as_aura_spell=False,
            aura_enchant_spec=None, position="top", reason=reason, log=True,
            replacement_selections=replacement_selections, prepared_replacement=None,
            transition_kind=ZoneTransitionKind.ORDINARY, meld_entry=entry,
        )
        if plan is root or plan.destination == "exile":
            return ()
        if plan.destination != "battlefield":
            raise GameRuleError("This replacement of a melded entry requires a supported joint arrival")
        prepared = plan.prepared_replacement
        if prepared.devour_sacrifices or prepared.graveyard_copy_exiles or prepared.entry_land_discard:
            raise GameRuleError("This melded entry requires a supported additional replacement continuation")
        reveals = prepare_zone_side_effects(owner.host, (prepared,))
        entering_player = entry_putting_player(owner.host, root, "battlefield", entry.owner)
        departure = owner._capture_departure(root, semantic_events=True, putting_player=entering_player)
        owner._commit_move(plan, departure, controller=entry.owner, tapped=None, enter_face=None, aura_target_ref=None,
                           zone_timestamp=None, reveal_to=())
        finish_self_graveyard_shuffles(owner, reveals)
        owner._journal_move(plan, departure, reveal_to=(), reason=reason, log=True, unviewed_library_exile=False)
        owner._complete_move(plan, departure, reason=reason, log=True, semantic_events=True,
                             transition_kind=ZoneTransitionKind.ORDINARY)
    return (root.ref,)

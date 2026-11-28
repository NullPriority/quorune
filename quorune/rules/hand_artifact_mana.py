"""Seal the selected artifact cards for an immediate mana ability."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..continuous_effect_model import ContinuousObjectIdentity
from ..errors import GameRuleError
from ..object_predicate import ObjectQuerySpec
from .hand_inspection import HandCardPredicateSpec


ARTIFACT_HAND_CARDS = HandCardPredicateSpec(ObjectQuerySpec(zones=("hand",), types_all=("artifact",)))


@dataclass(frozen=True, slots=True)
class ArtifactHandManaSelection:
    actor: str
    source: ContinuousObjectIdentity
    cards: tuple[ContinuousObjectIdentity, ...]

    def __post_init__(self) -> None:
        if (type(self.actor) is not str or not self.actor
                or not isinstance(self.source, ContinuousObjectIdentity)
                or not isinstance(self.cards, tuple)
                or any(not isinstance(value, ContinuousObjectIdentity) for value in self.cards)
                or len({value.object_id for value in self.cards}) != len(self.cards)):
            raise ValueError("Artifact mana requires distinct selected hand incarnations and the original source")

    @property
    def colorless(self) -> int:
        return 2 * len(self.cards)

    def to_dict(self) -> dict:
        return {"actor": self.actor, "source": self.source.to_dict(),
                "cards": [value.to_dict() for value in self.cards]}

    @classmethod
    def from_dict(cls, value: object):
        if (not isinstance(value, Mapping) or set(value) != {"actor", "source", "cards"}
                or not isinstance(value["cards"], (list, tuple))):
            raise ValueError("The artifact mana selection has an invalid shape")
        return cls(value["actor"], ContinuousObjectIdentity.from_dict(value["source"]),
                   tuple(ContinuousObjectIdentity.from_dict(row) for row in value["cards"]))


def current_artifact_hand_cards(host, actor):
    host._require_seat(actor, in_game=True)
    cards = (host.state.cards[object_id] for object_id in host.state.players[actor].zones["hand"])
    return tuple(
        card for card in cards
        if card.zone == "hand" and card.owner == actor and card.is_card_object and not card.is_token
        and ARTIFACT_HAND_CARDS.matches(host._public_object_query_result(card))
    )


def _require_current_artifact_mana_source(host, actor, identity):
    if not isinstance(identity, ContinuousObjectIdentity):
        raise GameRuleError("Artifact mana requires the original source identity")
    source = host.state.cards.get(identity.object_id)
    if (source is None or source.logical_object_id != identity.logical_object_id
            or source.zone != "battlefield" or source.phased_out or source.controller != actor):
        raise GameRuleError("The original artifact mana source is unavailable")


def select_artifact_hand_mana_cards(host, actor, source, refs) -> ArtifactHandManaSelection:
    _require_current_artifact_mana_source(host, actor, source)
    if (not isinstance(refs, (tuple, list))
            or any(type(ref) is not str or not ref for ref in refs)
            or len(set(refs)) != len(refs)):
        raise GameRuleError("Choose distinct artifact cards in your hand")
    available = {card.ref: card for card in current_artifact_hand_cards(host, actor)}
    if not set(refs) <= available.keys():
        raise GameRuleError("Choose distinct artifact cards in your hand")
    return ArtifactHandManaSelection(
        actor, source,
        tuple(ContinuousObjectIdentity(available[ref].object_id, available[ref].logical_object_id) for ref in refs),
    )


def revalidate_artifact_hand_mana_cards(host, selection: ArtifactHandManaSelection):
    _require_current_artifact_mana_source(host, selection.actor, selection.source)
    available = {card.object_id: card for card in current_artifact_hand_cards(host, selection.actor)}
    cards = tuple(available.get(row.object_id) for row in selection.cards)
    if any(card is None or card.logical_object_id != row.logical_object_id
           for card, row in zip(cards, selection.cards)):
        raise GameRuleError("The selected artifact hand incarnations changed")
    return cards



def artifact_hand_mana_choice(host, actor):
    refs = [card.ref for card in current_artifact_hand_cards(host, actor)]
    return {"type": "object_ref_array", "legal_refs": refs, "zone": "hand", "minimum": 0,
            "maximum": len(refs), "distinct": True, "label": "Reveal artifacts for colorless mana"}


def artifact_hand_activation_details(host, actor, source, ability, response):
    from ..artifact_hand_mana_model import ARTIFACT_HAND_MANA_SELECTION, ARTIFACT_HAND_MANA_REVEAL_FIELD, is_artifact_hand_mana

    if not is_artifact_hand_mana(ability):
        return {}
    selection = select_artifact_hand_mana_cards(
        host, actor, ContinuousObjectIdentity(source.object_id, source.logical_object_id),
        response.get(ARTIFACT_HAND_MANA_REVEAL_FIELD, ()),
    )
    require_artifact_hand_mana_bundle(response.get("mana_output"), selection)
    if response.get("mana_choice") not in (None, ""):
        raise GameRuleError("Artifact hand mana produces colorless mana")
    return {ARTIFACT_HAND_MANA_SELECTION: selection.to_dict()}


def require_artifact_hand_mana_bundle(declared, selection):
    if declared is not None and (
        not isinstance(declared, Mapping) or set(declared) - {"C"}
        or type(declared.get("C", 0)) is not int or declared.get("C", 0) != selection.colorless
    ):
        raise GameRuleError("Declared mana must match the selected artifacts")


def artifact_hand_selection_for_commit(proposal, ability):
    from ..artifact_hand_mana_model import ARTIFACT_HAND_MANA_SELECTION, is_artifact_hand_mana
    from .action_proposals import thaw_json

    if not is_artifact_hand_mana(ability):
        return None
    try:
        return ArtifactHandManaSelection.from_dict(thaw_json(proposal.details).get(ARTIFACT_HAND_MANA_SELECTION))
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc


def commit_artifact_hand_mana_reveal(host, selection, source_ref):
    from ..effect_runtime.hand_inspection import commit_selected_hand_reveal
    from ..util import normalize_mana_bundle

    cards = revalidate_artifact_hand_mana_cards(host, selection)
    for card in cards:
        commit_selected_hand_reveal(host, {
            "player": selection.actor, "object_id": card.object_id, "logical_object_id": card.logical_object_id,
            "ref": card.ref, "name": host.display_name(card.object_id),
        }, source_ref=source_ref, summary="A selected artifact was revealed to produce mana.")
    return normalize_mana_bundle({"C": selection.colorless})


def prepare_artifact_hand_mana_step(host, actor, source, activation):
    from ..artifact_hand_mana_model import ARTIFACT_HAND_MANA_STEP, ArtifactHandManaStep

    if ARTIFACT_HAND_MANA_STEP not in activation:
        return None, None
    if "input" in activation or activation.get("tap_source", True) is not True:
        raise GameRuleError("An artifact mana step requires its tap ability and selected cards")
    try:
        step = ArtifactHandManaStep.from_dict(activation[ARTIFACT_HAND_MANA_STEP])
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    selection = select_artifact_hand_mana_cards(
        host, actor, ContinuousObjectIdentity(source.object_id, source.logical_object_id), step.cards,
    )
    require_artifact_hand_mana_bundle(activation.get("bundle"), selection)
    return step.ability_id, selection



_ARTIFACT_MANA_FORM_PREFIX = "artifact_mana_cards:"


def artifact_hand_mana_payment_fields(host, actor, *, spend_context, exclude_sources):
    from ..artifact_hand_mana_model import ARTIFACT_HAND_MANA_CAPABILITY
    from .event_subscriptions import event_has_capability_subscriber

    if not event_has_capability_subscriber(host, "activate", ARTIFACT_HAND_MANA_CAPABILITY):
        return {}
    fields = {}
    for source in host.available_mana_sources(actor, spend_context=spend_context):
        if source.object_id in exclude_sources:
            continue
        for mode in source.modes:
            if mode.artifact_hand_mana is not None and mode.total:
                key = _ARTIFACT_MANA_FORM_PREFIX + source.ref + ":" + mode.artifact_hand_mana
                fields[key] = {**artifact_hand_mana_choice(host, actor),
                               "label": "Artifacts to reveal with " + source.name}
    return fields


def artifact_hand_mana_payment_steps(response):
    from ..artifact_hand_mana_model import ARTIFACT_HAND_MANA_STEP

    fields = []
    for key, refs in response.items():
        if not key.startswith(_ARTIFACT_MANA_FORM_PREFIX):
            continue
        source, separator, ability = key.removeprefix(_ARTIFACT_MANA_FORM_PREFIX).rpartition(":")
        if not separator or not source or not ability or not isinstance(refs, (tuple, list)):
            raise GameRuleError("An artifact mana choice requires its source, ability, and card selection")
        if refs:
            fields.append({"source": source, "bundle": {"C": 2 * len(refs)},
                           ARTIFACT_HAND_MANA_STEP: {"ability_id": ability, "cards": list(refs)}})
    return fields


def validate_artifact_payment_sources(host, actor, activations, exclude_sources):
    from ..artifact_hand_mana_model import ARTIFACT_HAND_MANA_STEP

    if not exclude_sources:
        return
    for activation in activations:
        if ARTIFACT_HAND_MANA_STEP in activation:
            source = host._resolve_object(actor, str(activation.get("source") or ""),
                                          zones={"battlefield"}, controlled_only=True)
            if source.object_id in exclude_sources:
                raise GameRuleError("A reserved cost object cannot also produce artifact mana")

"""Read the linked card's copiable values at the versioned copy commit."""

from collections.abc import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..defined_token_faces import single_face_copy_values
from ..errors import GameRuleError
from ..linked_exile_return_model import ExiledObjectIdentity
from ..rules.linked_clue_copy import (
    LINKED_CLUE_COPY_PAIR_CONTEXT, LINKED_CLUE_COPY_VERSION,
    require_linked_clue_copy_version, validate_linked_clue_pair,
)
from ..rules.linked_exile_copy import current_linked_exile_copy_target
from ..linked_ability_exile_model import LinkedAbilityExile
from .persistent_copy import resolving_persistent_copy_source


def prepare_linked_clue_copy_commit(host, effect, actor):
    fields = {"op", LINKED_CLUE_COPY_VERSION, "source", "subject", "ability_pair"}
    require_linked_clue_copy_version(effect)
    subject = effect.get("subject")
    if (set(effect) - {"reason", "_runtime_source"} != fields or effect["op"] != "copy_until_end_of_turn"
            or not isinstance(subject, Mapping) or set(subject) != {"object_id", "logical_object_id", "ref"}):
        raise ValueError("Linked copy requires the chosen public exile incarnation")
    resolved = resolving_persistent_copy_source(host, effect, actor=actor, kind="triggered_ability")
    if resolved is None:
        return subject["ref"], (), {}
    item, source = resolved
    pair = validate_linked_clue_pair(effect["ability_pair"])
    if item.context.get(LINKED_CLUE_COPY_PAIR_CONTEXT) != pair:
        raise GameRuleError("The copy lost the resolving ability's original link")
    model = ExiledObjectIdentity(subject["object_id"], subject["ref"], subject["logical_object_id"])
    linked = LinkedAbilityExile(
        ContinuousObjectIdentity(source.object_id, source.logical_object_id), pair, model,
    )
    target = current_linked_exile_copy_target(host, linked)
    if target is None:
        raise GameRuleError("The chosen creature is no longer in the original linked exile")
    return target.ref, (source,), single_face_copy_values(host._copyable_characteristics(target))

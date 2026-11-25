"""An untargeted copy subject chosen while a closed temporary copy spell resolves."""

from collections.abc import Mapping
from enum import StrEnum

from ..continuous_effect_model import ContinuousObjectIdentity
from ..errors import GameRuleError
from ..replacement.immutable import thaw_value
from ..target_cardinality import ANY_NUMBER_TARGET_CAPABILITY, AnnouncedTargetCardinality
from ..util import stable_json
from .temporary_self_copy import TEMPORARY_SELF_COPY_OPERATION


TEMPORARY_COPY_SUBJECT_OPERATION = "choose_temporary_copy_subject"
TEMPORARY_COPY_SUBJECT_CAPABILITY = "continuous.resolution.temporary_chosen_creature_copy"
TEMPORARY_COPY_SUBJECT_COMMIT_VERSION = "temporary_copy_subject_commit_version"
TEMPORARY_COPY_SUBJECT_RULES = ("115.1a", "115.3", "601.2c", "601.2f", "608.2b", "608.2d", "611.2c", "707.2")


class TemporaryCopySubjectScope(StrEnum):
    NONLEGENDARY_FOR_ONE_TARGET = "nonlegendary_for_one_target"
    CREATURE_FOR_CONTROLLED_TARGETS = "creature_for_controlled_targets"

    @property
    def multiple_receivers(self):
        return self is self.CREATURE_FOR_CONTROLLED_TARGETS

    def choice_effect(self):
        return {
            "op": TEMPORARY_COPY_SUBJECT_OPERATION, "schema_version": 1,
            "player": "$controller", "scope": self.value,
            "receivers": "$targets" if self.multiple_receivers else ["$target.0"],
        }

    def target_schema(self):
        schema = {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"]}
        if self.multiple_receivers:
            return {**schema, "controller_relation": "you",
                    "count_parameter": AnnouncedTargetCardinality("up_to", "any_number", 2).to_dict()}
        return {**schema, "count": 1}

    def accepts_subject(self, row):
        return (row is not None and row.zone == "battlefield" and not row.phased_out
                and "creature" in row.types and (self.multiple_receivers or "legendary" not in row.supertypes))

    @property
    def capability_dependencies(self):
        common = (TEMPORARY_COPY_SUBJECT_CAPABILITY, "target.revalidate_resolution")
        return (*common, ANY_NUMBER_TARGET_CAPABILITY) if self.multiple_receivers else common


def decode_temporary_copy_subject_effect(effect, *, actor=None):
    fields = {"op", "schema_version", "player", "scope", "receivers"}
    if (not isinstance(effect, Mapping) or set(effect) != fields
            or effect["op"] != TEMPORARY_COPY_SUBJECT_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 1):
        raise ValueError("Temporary subject copy requires its exact versioned choice")
    scope = TemporaryCopySubjectScope(effect["scope"])
    if actor is None:
        if thaw_value(effect) != scope.choice_effect():
            raise ValueError("Temporary subject copy changed its declared controller or target roles")
        return scope
    refs = effect["receivers"]
    if (effect["player"] != actor or not isinstance(refs, (list, tuple))
            or any(type(ref) is not str or not ref or ref.startswith("$") for ref in refs)
            or len(set(refs)) != len(refs)
            or not scope.multiple_receivers and len(refs) != 1):
        raise ValueError("Temporary subject copy requires the resolving controller and actual surviving targets")
    return scope


def temporary_copy_subject_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or "cr-115-targets" not in mechanic_ids:
        return ()
    try:
        scope = decode_temporary_copy_subject_effect(effects[0])
    except (TypeError, ValueError):
        return ()
    return scope.capability_dependencies if stable_json(thaw_value(target_schema)) == stable_json(scope.target_schema()) else ()


def temporary_copy_subject_runtime_dependencies(program):
    from .linked_clue_copy import linked_clue_copy_runtime_dependencies

    if linked := linked_clue_copy_runtime_dependencies(program):
        return linked
    if not any(effect.get("op") == TEMPORARY_COPY_SUBJECT_OPERATION for effect in program.effects):
        return ()
    required = temporary_copy_subject_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=("cr-115-targets",),
    )
    if not required or program.event != "resolve":
        raise ValueError("Temporary subject copying requires its complete resolving spell and announced target scope")
    return required


def is_closed_temporary_copy_subject_program(program):
    try:
        required = temporary_copy_subject_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def copy_subject_object_reference(row):
    return {"ref": row.ref, **ContinuousObjectIdentity(row.object_id, row.logical_object_id).to_dict()}


def _copy_object_from_reference(host, raw):
    if not isinstance(raw, Mapping) or set(raw) != {"ref", "object_id", "logical_object_id"}:
        raise ValueError("A temporary copy object requires its original reference and incarnation")
    reference = raw["ref"]
    if type(reference) is not str or not reference:
        raise ValueError("A temporary copy object requires a public reference")
    identity = ContinuousObjectIdentity.from_dict({key: raw[key] for key in ("object_id", "logical_object_id")})
    current = host.state.cards.get(identity.object_id)
    if (current is None or current.ref != reference or current.logical_object_id != identity.logical_object_id
            or current.zone != "battlefield" or current.phased_out):
        return None
    return current


def copy_subject_commit_effect(scope, selected, receivers):
    return {"op": TEMPORARY_SELF_COPY_OPERATION, TEMPORARY_COPY_SUBJECT_COMMIT_VERSION: 1,
            "scope": scope.value, "subject": selected, "receivers": tuple(receivers)}


def prepare_temporary_copy_subject_commit(host, effect, actor):
    from ..defined_token_faces import single_face_copy_values

    fields = {"op", TEMPORARY_COPY_SUBJECT_COMMIT_VERSION, "scope", "subject", "receivers"}
    if (not fields <= set(effect) <= fields | {"reason", "_runtime_source"}
            or effect["op"] != TEMPORARY_SELF_COPY_OPERATION
            or type(effect[TEMPORARY_COPY_SUBJECT_COMMIT_VERSION]) is not int
            or effect[TEMPORARY_COPY_SUBJECT_COMMIT_VERSION] != 1):
        raise ValueError("Temporary subject copy commit requires its complete versioned choice")
    scope = TemporaryCopySubjectScope(effect["scope"])
    references = effect["receivers"]
    if not isinstance(references, (list, tuple)) or not scope.multiple_receivers and len(references) != 1:
        raise ValueError("Temporary subject copy changed the retained target count")
    recipients = tuple(card for value in references if (card := _copy_object_from_reference(host, value)) is not None)
    if len({card.object_id for card in recipients}) != len(recipients):
        raise ValueError("Temporary subject copy repeats a retained target")
    recipients = tuple(card for card in recipients if "creature" in host._effective_type_parts(card)[0]
                       and (not scope.multiple_receivers or card.controller == actor))
    subject = _copy_object_from_reference(host, effect["subject"])
    if subject is None:
        raise GameRuleError("The chosen copy subject is no longer present")
    types, _, supertypes = host._effective_type_parts(subject)
    if "creature" not in types or ("legendary" in supertypes and not scope.multiple_receivers):
        raise GameRuleError("The chosen copy subject is no longer eligible")
    return subject.ref, recipients, single_face_copy_values(host._copyable_characteristics(subject))

from __future__ import annotations

"""Closed grammatical subject and single-target reference binding."""

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..basic_land_types import LAND_SUBTYPES
from ..target_controller_information import (
    TARGET_CONTROLLER_INFORMATION_MECHANIC, target_controller_reference,
)
from .closed_effect_programs import (
    CLOSED_EFFECT_PROGRAM_MECHANIC,
    ClosedEffectProgramTemplate,
    CompiledEffectTemplate,
    ComponentCompiler,
    _NESTED_SEQUENCE_MECHANICS,
    _UNSAFE_LINKAGE,
    _candidate_partitions,
    _partition,
    _sentence_partition,
)
from .library_search_templates import FixedLibrarySearchTemplate, fixed_library_search_effect_template
from .optional_effect_templates import FIXED_OPTIONAL_EFFECT_MECHANIC, OPTIONAL_EFFECT_OPERATION


BOUND_EFFECT_PROGRAM_MECHANIC = "bound-effect-program"
BOUND_EFFECT_PROGRAM_CAPABILITY = "resolution.effect_program.bound_references"
BOUND_EFFECT_PROGRAM_TEMPLATE_ID = "bound-effect-program-v1"
_SUBJECT = r"target player|target opponent|each player|each opponent|you"
_REFERENCE = r"it|that (?:player|permanent|artifact|battle|creature|enchantment|land|planeswalker)"
_TARGET_WORD = re.compile(r"\btarget\b", re.IGNORECASE)
_TARGET_CONTROLLER_DAMAGE = re.compile(
    r"(?P<source>.+?) deals (?P<amount>[1-9][0-9]*) damage to that "
    r"(?P<kind>permanent|artifact|battle|creature|enchantment|land|planeswalker)"
    r"['’]s controller\.",
    re.IGNORECASE,
)
_TARGET_CONTROLLER_SEARCH = re.compile(
    r"(?:Its controller|(?P<opponent>That player)|That "
    r"(?P<kind>permanent|artifact|battle|creature|enchantment|land|planeswalker)['’]s controller) "
    r"may search their library for (?P<body>.+)\.",
    re.IGNORECASE,
)
_CONTROLLER_PREFIX_OPS = frozenset({
    "destroy", "exile_permanent", "tap", "place_counters", "modify_stats_until_end_of_turn",
})


@dataclass(frozen=True, slots=True)
class BoundEffectProgramTemplate(ClosedEffectProgramTemplate):
    @property
    def template_id(self) -> str:
        return BOUND_EFFECT_PROGRAM_TEMPLATE_ID


def _public_single_target(schema: Mapping[str, Any] | None) -> bool:
    if schema is None or type(schema.get("count")) is not int or schema.get("count") != 1:
        return False
    return (
        schema.get("zones") == ["player"] and schema.get("categories") == ["player"]
    ) or (
        schema.get("zones") == ["battlefield"] and schema.get("categories") == ["permanent"]
    )


def _program(compiled, *, schema) -> BoundEffectProgramTemplate | None:
    if not 2 <= len(compiled) <= 4 or any(
        template is None or not effects or not mechanics
        or BOUND_EFFECT_PROGRAM_MECHANIC in mechanics
        or CLOSED_EFFECT_PROGRAM_MECHANIC in mechanics
        for template, effects, _schema, mechanics in compiled
    ):
        return None
    effects = tuple(deepcopy(effect) for _, components, _, _ in compiled for effect in components)
    if not 2 <= len(effects) <= 8:
        return None
    return BoundEffectProgramTemplate(
        component_template_ids=tuple(str(component[0]) for component in compiled),
        component_effect_counts=tuple(len(component[1]) for component in compiled),
        _effects=effects, _target_schema=schema,
        mechanic_ids=tuple(dict.fromkeys((
            CLOSED_EFFECT_PROGRAM_MECHANIC, BOUND_EFFECT_PROGRAM_MECHANIC,
            *(mechanic for _, _, _, mechanics in compiled for mechanic in mechanics
              if mechanic not in _NESTED_SEQUENCE_MECHANICS),
        ))),
    )


def _shared_player_subject(text: str, compile_component: ComponentCompiler):
    match = re.fullmatch(rf"(?P<subject>{_SUBJECT}) (?P<body>.+)", text, re.IGNORECASE)
    if match is None:
        return None
    subject = match.group("subject")
    body = match.group("body")
    comma_clauses = _partition(body, ", ")
    partitions = (*_candidate_partitions(body), *((comma_clauses,) if comma_clauses else ()))
    for clauses in partitions:
        compiled = []
        for index, clause in enumerate(clauses):
            clause = re.sub(r"^(?:Then|And) ", "", clause, flags=re.IGNORECASE)
            if index and re.match(r"^That player\b", clause, re.IGNORECASE):
                if not subject.casefold().startswith("target "):
                    break
                clause = re.sub(r"^That player\b", subject, clause, flags=re.IGNORECASE)
            elif not re.match(rf"^(?:{_SUBJECT})\b", clause, re.IGNORECASE):
                clause = f"{subject} {clause}"
            # A second explicit target is a different target occurrence, not
            # grammatical ellipsis. The outer production rejects it first.
            compiled.append(compile_component(clause))
        if len(compiled) != len(clauses):
            continue
        targeted = tuple(component[2] for component in compiled if component[2] is not None)
        if targeted and (
            not _public_single_target(targeted[0])
            or targeted[0].get("categories") != ["player"]
            or any(schema != targeted[0] for schema in targeted[1:])
        ):
            continue
        result = _program(compiled, schema=targeted[0] if targeted else None)
        if result is not None:
            return result
    return None


def target_controller_prefix_matches(effect: Mapping[str, Any]) -> bool:
    return (
        isinstance(effect, Mapping)
        and effect.get("op") in _CONTROLLER_PREFIX_OPS
        and effect.get("card") == "$target.0"
    )


def target_controller_search_prefix_matches(effect: Mapping[str, Any]) -> bool:
    return target_controller_prefix_matches(effect) and effect["op"] in {"destroy", "exile_permanent"}


def _target_type_is_guaranteed(schema: Mapping[str, Any], kind: str) -> bool:
    if kind == "permanent" or kind in schema.get("types_all", ()):
        return True
    if tuple(schema.get("types_any", ())) == (kind,):
        return True
    land_choices = set(schema.get("subtypes_any", ()))
    return kind == "land" and (
        bool(LAND_SUBTYPES.intersection(schema.get("subtypes_all", ())))
        or bool(land_choices and land_choices <= LAND_SUBTYPES)
    )


def _target_controller_damage_tail(
    first: CompiledEffectTemplate,
    clause: str,
    compile_component: ComponentCompiler,
) -> CompiledEffectTemplate | None:
    schema = first[2]
    match = _TARGET_CONTROLLER_DAMAGE.fullmatch(clause)
    if (
        match is None or schema is None or schema["categories"] != ["permanent"]
        or schema.get("up_to") or len(first[1]) != 1
        or not target_controller_prefix_matches(first[1][0])
        or not _target_type_is_guaranteed(schema, match["kind"].casefold())
    ):
        return None
    damage = compile_component(f"{match['source']} deals {match['amount']} damage to you.")
    expected = {
        "op": "damage", "source": "$source", "target": "$controller", "amount": int(match["amount"]),
    }
    if damage[0] is None or damage[1] != (expected,) or damage[2] is not None:
        return None
    return (
        damage[0], ({**expected, "target": target_controller_reference()},), None,
        (*damage[3], TARGET_CONTROLLER_INFORMATION_MECHANIC),
    )


def _target_controller_search_program(
    clauses: tuple[str, ...] | None,
    compile_component: ComponentCompiler,
) -> BoundEffectProgramTemplate | None:
    if clauses is None or (match := _TARGET_CONTROLLER_SEARCH.fullmatch(clauses[1])) is None:
        return None
    first = compile_component(clauses[0])
    schema = first[2]
    if (
        first[0] is None or schema is None or not _public_single_target(schema)
        or schema["categories"] != ["permanent"] or schema.get("up_to")
        or len(first[1]) != 1 or not target_controller_search_prefix_matches(first[1][0])
    ):
        return None
    if match["opponent"] is not None and schema.get("controller_relation") != "opponent":
        return None
    if match["kind"] is not None and not _target_type_is_guaranteed(schema, match["kind"].casefold()):
        return None
    search = fixed_library_search_effect_template(f"Search your library for {match['body']}.")
    if not isinstance(search, FixedLibrarySearchTemplate) or (
        search.destination != "battlefield" or search.count != 1 or search.optional_count
    ):
        return None
    _, (search_effect,), _, mechanics = search.compiled()
    chooser = target_controller_reference()
    optional_search = (
        "fixed-target-controller-optional-library-search-v1",
        ({"op": OPTIONAL_EFFECT_OPERATION, "player": chooser,
          "effects": [{**search_effect, "searching_player": chooser}]},),
        None,
        (*mechanics, FIXED_OPTIONAL_EFFECT_MECHANIC, TARGET_CONTROLLER_INFORMATION_MECHANIC),
    )
    following = tuple(compile_component(clause) for clause in clauses[2:])
    if any(component[2] is not None for component in following):
        return None
    return _program((first, optional_search, *following), schema=schema)


def _single_target_reference(text: str, compile_component: ComponentCompiler, *, conditional_tail=None):
    for clauses in _candidate_partitions(text):
        first = compile_component(clauses[0])
        schema = first[2]
        if first[0] is None or not _public_single_target(schema):
            continue
        assert schema is not None
        if conditional_tail is not None and (
            clauses[-1] != conditional_tail[0] or not _target_type_is_guaranteed(schema, "creature")
        ):
            continue
        if len(clauses) == 2:
            tail = _target_controller_damage_tail(first, clauses[1], compile_component)
            if tail is not None:
                return _program((first, tail), schema=schema)
        compiled = [first]
        bound_reference_seen = False
        for index, clause in enumerate(clauses[1:], start=1):
            clause = re.sub(r"^Then ", "", clause, flags=re.IGNORECASE)
            followups = (
                (conditional_tail[1],)
                if conditional_tail is not None and index == len(clauses) - 1
                else _linked_followups(clause, schema, compile_component)
            )
            if not followups:
                break
            compiled.extend(followups)
            bound_reference_seen |= any(component[2] is not None for component in followups)
        else:
            if bound_reference_seen:
                result = _program(compiled, schema=schema)
                if result is not None:
                    return result
    return None


def _bound_object_followup(clause, schema, compile_component):
    references = tuple(re.finditer(rf"\b(?:{_REFERENCE})\b", clause, re.IGNORECASE))
    # The type-retention rider describes the same object, not a second
    # referent. Other possessives remain outside this binding.
    linkage = re.sub(r" in addition to its other types\b", "", clause, flags=re.IGNORECASE)
    if len(references) != 1 or re.search(r"\b(?:its|their|that much|this way)\b", linkage, re.IGNORECASE):
        return None
    reference, = references
    noun = reference.group().casefold()
    if noun == "that player":
        if schema["categories"] != ["player"]:
            return None
        subject = "target player"
    else:
        kind = "permanent" if noun == "it" else noun.removeprefix("that ")
        if schema["categories"] != ["permanent"] or not _target_type_is_guaranteed(schema, kind):
            return None
        subject = f"target {kind}"
    prefix, suffix = clause[:reference.start()], clause[reference.end():]
    component = compile_component(prefix + subject + suffix)
    if (
        component[0] is None and subject == "target permanent"
        and _target_type_is_guaranteed(schema, "creature")
    ):
        component = compile_component(prefix + "target creature" + suffix)
    if (
        component[0] is None or not _public_single_target(component[2])
        or component[2].get("categories") != schema.get("categories")
    ):
        return None
    return component


def _linked_followups(clause, schema, compile_component):
    original = compile_component(clause)
    if original[0] is not None:
        return (original,) if original[2] is None else ()
    bound = _bound_object_followup(clause, schema, compile_component)
    if bound is not None:
        return (bound,)
    conjunction = _partition(clause, " and ")
    if conjunction is None or len(conjunction) != 2:
        return ()
    components = tuple(_bound_object_followup(part, schema, compile_component) for part in conjunction)
    return components if all(component is not None for component in components) else ()


def bound_effect_program_template(
    text: str, *, compile_component: ComponentCompiler
) -> BoundEffectProgramTemplate | None:
    """Lower one grammatical subject or one explicitly established target."""

    normalized = " ".join(text.strip().split())
    linkage_text = re.sub(r"\banother target\b", "target", normalized, flags=re.IGNORECASE)
    if not normalized or any(c in normalized for c in ('"', "(", ")")):
        return None
    target_count = len(_TARGET_WORD.findall(normalized))
    if target_count > 1:
        return None
    if target_count == 1:
        affected_search = _target_controller_search_program(_sentence_partition(normalized), compile_component)
        if affected_search is not None:
            return affected_search
    if _UNSAFE_LINKAGE.search(linkage_text):
        clauses = _sentence_partition(normalized)
        if (
            target_count != 1 or clauses is None
            or any(_UNSAFE_LINKAGE.search(re.sub(r"\banother target\b", "target", clause, flags=re.IGNORECASE))
                   for clause in clauses[:-1])
        ):
            return None
        from .temporary_target_interactions import linked_creature_subtype_stats_template

        conditional = linked_creature_subtype_stats_template(clauses[-1])
        if conditional is None:
            return None
        return _single_target_reference(normalized, compile_component, conditional_tail=(clauses[-1], conditional))
    shared = _shared_player_subject(normalized, compile_component)
    if shared is not None:
        return shared
    return _single_target_reference(normalized, compile_component) if target_count == 1 else None


__all__ = [
    "BOUND_EFFECT_PROGRAM_CAPABILITY", "BOUND_EFFECT_PROGRAM_MECHANIC",
    "BOUND_EFFECT_PROGRAM_TEMPLATE_ID", "BoundEffectProgramTemplate", "bound_effect_program_template",
]

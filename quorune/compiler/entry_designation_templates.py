from __future__ import annotations

"""Source-spanned entry designations and fixed chosen-value characteristics."""

from . import regex_working_set as re
from collections import Counter
from dataclasses import replace
from typing import Mapping, Sequence

from ..entry_designations import (
    CHOSEN_CHARACTERISTICS_CAPABILITY,
    CHOSEN_COLOR_TARGET_PREDICATE, CHOSEN_CREATURE_TYPE_TARGET_PREDICATE,
    CHOSEN_CHARACTERISTICS_HANDLER_ID,
    COUNTED_CREATURE_TYPE_ENTRY_HANDLER_ID,
    COUNTED_ENTRY_OPPONENT_DEPENDENCIES,
    COUNTED_ENTRY_OPPONENT_HANDLER_ID,
    COUNTED_ENTRY_OPPONENT_TEMPLATE,
    COUNTED_ENTRY_COUNTER_NAME,
    ENTRY_DESIGNATION_CAPABILITY,
    ENTRY_DESIGNATION_HANDLER_ID,
    ENTRY_DESIGNATION_HANDLER_IDS,
    ENTRY_CARD_NAME_HANDLER_ID, ENTRY_CARD_NAME_TEMPLATE,
    ENTRY_OPPONENT_HANDLER_ID, ENTRY_PLAYER_HANDLER_ID,
    LINKED_ENTRY_OPPONENT_TEMPLATE, LINKED_ENTRY_PLAYER_TEMPLATE, LINKED_PLAYER_DAMAGE_TEMPLATE,
    LINKED_PLAYER_RECEIVED_DAMAGE_PROGRAM_TEMPLATE,
    LINKED_PLAYER_PROTECTION_TEMPLATE,
    LINKED_PLAYER_PROTECTION_CAPABILITIES,
    LINKED_PLAYER_CAST_MILL_PROGRAM_TEMPLATE,
    LINKED_PLAYER_GRAVEYARD_DEFINITION_TEMPLATE,
    LINKED_OPPONENT_MAXIMUM_TEMPLATE,
    LINKED_OPPONENT_HAND_MODIFIER_TEMPLATE,
    LINKED_OPPONENT_DEFINITION_TEMPLATE,
    LINKED_OPPONENT_UPKEEP_TEMPLATE,
    LIMITED_CREATURE_TYPE_ENTRY_CAPABILITY,
    LIMITED_CREATURE_TYPE_ENTRY_HANDLER_ID,
    SELF_CHOSEN_SUBTYPE_CONTINUOUS_HANDLER_ID,
    SELF_CHOSEN_SUBTYPE_ENTRY_HANDLER_ID,
)
from ..rules.source_references import SourceReferenceSpec
from .continuous_templates import (
    fixed_query_characteristic_grant_handler,
    fixed_query_keyword_grant_handler,
    fixed_power_toughness_anthem_handler,
)
from .ir_model import OracleFaceIR, append_residual


def scope_entry_designation_faces(
    faces: Sequence[OracleFaceIR],
) -> tuple[OracleFaceIR, ...]:
    """Reject linked choices outside the plain retained-designation owner."""
    result = []
    for original in faces:
        face = _scope_entry_player_pair(_scope_entry_opponent_pair(_compose_self_chosen_subtype_entry(original)))
        face = _scope_entry_card_name(face)
        declarations = Counter(
            descriptor["designation"]
            for node in face.nodes
            for descriptor in node.handlers
            if descriptor.get("handler_id") in ENTRY_DESIGNATION_HANDLER_IDS
        )
        duplicates = {kind for kind, count in declarations.items() if count > 1}
        source = (
            rf"(?:{SourceReferenceSpec(face.face_name).regex_pattern}|"
            r"this (?:artifact|card|creature|enchantment|land|permanent))"
        )
        linked_type_addition = any(
            re.fullmatch(
                rf"{source} is the chosen type in addition to its other types\.",
                node.text,
                re.IGNORECASE,
            )
            for node in face.nodes
        )
        owns_self_type = any(descriptor.get("handler_id") == SELF_CHOSEN_SUBTYPE_ENTRY_HANDLER_ID
                             for node in face.nodes for descriptor in node.handlers)
        blocked = duplicates | (
            {"creature_type"} if linked_type_addition and not owns_self_type else set()
        )
        if not blocked:
            result.append(face)
            continue
        residuals = list(face.residuals)
        nodes = []
        for node in face.nodes:
            if any(
                descriptor.get("handler_id") in ENTRY_DESIGNATION_HANDLER_IDS
                and descriptor.get("designation") in blocked
                for descriptor in node.handlers
            ):
                residual_id = append_residual(
                    residuals,
                    kind="entry_designation",
                    text=node.text,
                    span=node.span,
                    reason=(
                        "A linked source-type addition requires its own choice "
                        "and characteristic owner"
                        if linked_type_addition and "creature_type" not in duplicates
                        else "Multiple same-kind intrinsic choices require independent "
                        "linked designation identities"
                    ),
                    blockers=("linked entry designation ownership",),
                )
                node = replace(
                    node,
                    exact=False,
                    lowerable=False,
                    event="unresolved",
                    handlers=(),
                    residual_ids=(*node.residual_ids, residual_id),
                )
            nodes.append(node)
        result.append(replace(face, nodes=tuple(nodes), residuals=tuple(residuals)))
    return tuple(_scope_linked_entry_targets(
        _scope_linked_entry_targets(face, "creature_type", CHOSEN_CREATURE_TYPE_TARGET_PREDICATE),
        "color", CHOSEN_COLOR_TARGET_PREDICATE,
    ) for face in result)


def _scope_entry_opponent_pair(face: OracleFaceIR) -> OracleFaceIR:
    choices = [node for node in face.nodes if node.template_id == LINKED_ENTRY_OPPONENT_TEMPLATE]
    consumers = [node for node in face.nodes if node.template_id in {
        LINKED_OPPONENT_UPKEEP_TEMPLATE, LINKED_OPPONENT_MAXIMUM_TEMPLATE,
        LINKED_OPPONENT_HAND_MODIFIER_TEMPLATE, LINKED_OPPONENT_DEFINITION_TEMPLATE,
    }]
    return _scope_linked_entry_pair(
        face, choices, consumers,
        reason="Linked opponent hand rules require one exact entry choice and its protected same-face consumer",
        blocker="linked entry opponent ownership",
    )


def _scope_entry_player_pair(face: OracleFaceIR) -> OracleFaceIR:
    choices = [node for node in face.nodes if node.template_id == LINKED_ENTRY_PLAYER_TEMPLATE]
    consumer_sets = {
        frozenset((LINKED_PLAYER_DAMAGE_TEMPLATE,)),
        frozenset((LINKED_PLAYER_RECEIVED_DAMAGE_PROGRAM_TEMPLATE,)),
        frozenset((LINKED_PLAYER_PROTECTION_TEMPLATE,)),
        frozenset((LINKED_PLAYER_GRAVEYARD_DEFINITION_TEMPLATE, LINKED_PLAYER_CAST_MILL_PROGRAM_TEMPLATE)),
    }
    known_consumers = frozenset().union(*consumer_sets)
    consumers = [node for node in face.nodes if node.template_id in known_consumers]
    templates = frozenset(node.template_id for node in consumers)
    complete = len(templates) == len(consumers) and templates in consumer_sets
    return _scope_linked_entry_pair(
        face, choices, consumers, complete=complete,
        reason="Linked player rules require the exact any-player entry choice and all same-face consumers",
        blocker="linked entry player ownership",
    )


def _scope_entry_card_name(face: OracleFaceIR) -> OracleFaceIR:
    choices = [node for node in face.nodes if node.template_id == ENTRY_CARD_NAME_TEMPLATE]
    if not choices:
        return face
    scopes = {
        "static-cast-chosen-name-prohibition-v1": True,
        "chosen-name-all-activation-prohibition-v1": True,
        "chosen-name-nonmana-activation-prohibition-v1": False,
    }
    consumers = [node for node in face.nodes if node.template_id in scopes]
    complete = (
        len(choices) == len(consumers) == 1
        and len(choices[0].handlers) == 1
        and choices[0].handlers[0].get("nonland") is scopes[consumers[0].template_id]
    )
    return _scope_linked_entry_pair(
        face, choices, consumers, complete=complete,
        reason="The entry card name requires its exact same-face restriction",
        blocker="linked entry card name ownership",
    )


def _scope_linked_entry_pair(face, choices, consumers, *, complete=None, reason, blocker):
    linked = choices + consumers
    if complete is None:
        complete = len(consumers) == 1
    if not linked or len(choices) == 1 and complete and all(node.exact for node in linked):
        return face
    residuals = list(face.residuals)
    nodes = []
    for node in face.nodes:
        if node not in linked:
            nodes.append(node)
            continue
        residual = append_residual(
            residuals, kind="entry_designation", text=node.text, span=node.span,
            reason=reason,
            blockers=(blocker,),
        )
        nodes.append(replace(node, exact=False, lowerable=False, event="unresolved",
                             handlers=(), effects=(), capability_dependencies=(), capability_closure=(),
                             capability_profile=None, capability_fingerprint=None,
                             residual_ids=(*node.residual_ids, residual)))
    return replace(face, nodes=tuple(nodes), residuals=tuple(residuals))


def _compose_self_chosen_subtype_entry(face: OracleFaceIR) -> OracleFaceIR:
    """Link one plain entry choice to its exact same-face self type addition."""
    if any(residual.material for residual in face.residuals):
        return face
    declarations = [
        node for node in face.nodes for descriptor in node.handlers
        if descriptor.get("handler_id") in ENTRY_DESIGNATION_HANDLER_IDS
        and descriptor.get("designation") == "creature_type"
    ]
    additions = [
        node for node in face.nodes for descriptor in node.handlers
        if descriptor.get("handler_id") == SELF_CHOSEN_SUBTYPE_CONTINUOUS_HANDLER_ID and node.exact
    ]
    if not additions:
        return face
    if len(declarations) != 1 or len(additions) != 1:
        return _unlinked_self_chosen_subtypes(face, additions)
    choice, = declarations
    addition, = additions
    if (not choice.exact or len(choice.handlers) != 1
            or choice.handlers[0].get("handler_id") != ENTRY_DESIGNATION_HANDLER_ID):
        return _unlinked_self_chosen_subtypes(face, additions)
    descriptor = {**dict(choice.handlers[0]), "handler_id": SELF_CHOSEN_SUBTYPE_ENTRY_HANDLER_ID,
                  "schema_version": 5, "requires_complete_card_program": True}
    combined = replace(
        choice, template_id="intrinsic-entry-self-chosen-subtype-v5", handlers=(descriptor,),
        capability_dependencies=addition.capability_dependencies,
        capability_closure=addition.capability_closure, capability_profile=addition.capability_profile,
        capability_fingerprint=addition.capability_fingerprint,
    )
    return replace(face, nodes=tuple(combined if node is choice else node for node in face.nodes))


def _unlinked_self_chosen_subtypes(face: OracleFaceIR, additions) -> OracleFaceIR:
    residuals = list(face.residuals)
    nodes = []
    for node in face.nodes:
        if node not in additions:
            nodes.append(node)
            continue
        residual = append_residual(
            residuals, kind="entry_designation", text=node.text, span=node.span,
            reason="Self chosen subtype requires its own single represented entry designation",
            blockers=("linked entry designation ownership",),
        )
        nodes.append(replace(node, exact=False, lowerable=False, event="unresolved",
                             handlers=(), residual_ids=(*node.residual_ids, residual)))
    return replace(face, nodes=tuple(nodes), residuals=tuple(residuals))


def _scope_linked_entry_targets(face: OracleFaceIR, designation, predicate) -> OracleFaceIR:
    """Require an exact plain choice of the described kind on the same face."""
    if any(node.exact and descriptor.get("handler_id") == ENTRY_DESIGNATION_HANDLER_ID
           and descriptor.get("designation") == designation
           for node in face.nodes for descriptor in node.handlers):
        return face
    linked = tuple(node for node in face.nodes if isinstance(node.target_schema, Mapping)
                   and node.target_schema.get("predicate") == predicate)
    if not linked:
        return face
    residuals = list(face.residuals)
    nodes = []
    for node in face.nodes:
        if node not in linked:
            nodes.append(node)
            continue
        residual = append_residual(
            residuals, kind="target_predicate", text=node.text, span=node.span,
            reason=f"A linked {designation.replace('_', '-')} target requires a represented source entry choice",
            blockers=("linked entry designation ownership",),
        )
        nodes.append(replace(node, exact=False, lowerable=False, event="unresolved",
                             effects=(), target_schema=None, residual_ids=(*node.residual_ids, residual)))
    return replace(face, nodes=tuple(nodes), residuals=tuple(residuals))


def source_linked_designation_target_template(text: str):
    if re.fullmatch(r"Destroy target permanent of the chosen color\.", text, re.IGNORECASE):
        from ..rules.chosen_color_target_shapes import TARGET_SCHEMA
        from .destruction_templates import targeted_destruction_effect_template

        base = targeted_destruction_effect_template("Destroy target permanent.")
        if base is None:
            return None
        template, effects, target_schema, mechanics = base.compiled()
        target = {**target_schema, "predicate": CHOSEN_COLOR_TARGET_PREDICATE}
        if target != TARGET_SCHEMA:
            raise ValueError("Chosen-color destruction must retain one mandatory permanent target")
        return template + "-source-chosen-color-v1", effects, target, mechanics
    return source_chosen_creature_type_target_template(text)


def source_chosen_creature_type_target_template(text: str):
    if "of the chosen type" not in text.casefold():
        return None
    controlled_counter = re.fullmatch(
        r"(?P<body>Put a (?a:[a-z]+) counter on target creature you control) of the chosen type\.",
        text, re.IGNORECASE,
    )
    if controlled_counter is not None:
        from ..keyword_counters import keyword_counter_mechanic
        from .counter_placement_templates import fixed_counter_placement_effect_template

        base = fixed_counter_placement_effect_template(controlled_counter["body"] + ".", card_name="This source")
        if base is None or keyword_counter_mechanic(base.counter_name) is not None:
            return None
    elif re.fullmatch(
        r"Return target creature card of the chosen type from your graveyard to the battlefield "
        r"with a finality counter on it\.", text, re.IGNORECASE,
    ):
        from .reanimation_entry_counter_templates import fixed_reanimation_entry_counter_template

        base = fixed_reanimation_entry_counter_template(re.sub(r" of the chosen type", "", text, flags=re.IGNORECASE))
        if base is None:
            return None
    else:
        return None
    template, effects, target_schema, mechanics = base.compiled()
    return (template + "-source-chosen-creature-type-v1", effects,
            {**target_schema, "predicate": CHOSEN_CREATURE_TYPE_TARGET_PREDICATE}, mechanics)


def entry_designation_handler(text: str, *, source_name: str):
    from .entry_color_composition import composed_entry_color_designation_handler

    return (composed_entry_color_designation_handler(text, source_name=source_name)
            or _counted_entry_opponent_handler(text)
            or _linked_entry_opponent_handler(text)
            or _entry_card_name_handler(text)
            or _counted_creature_type_entry_handler(text)
            or _plain_entry_designation_handler(text, source_name=source_name))


def _entry_card_name_handler(text: str):
    match = re.fullmatch(r"As this (?:artifact|creature|enchantment) enters, choose a (?P<scope>nonland )?card name\.",
                         text, re.IGNORECASE)
    if match is None:
        return None
    return (
        ENTRY_CARD_NAME_TEMPLATE,
        {"handler_id": ENTRY_CARD_NAME_HANDLER_ID, "schema_version": 1, "event": "zone.change",
         "nonland": match["scope"] is not None},
        (ENTRY_DESIGNATION_CAPABILITY,),
    )


def _counted_entry_opponent_handler(text: str):
    line = " ".join(text.casefold().split())
    printed = ("as this creature enters, choose an opponent. this creature enters with a -1/-1 "
               "counter on it for each creature that player controls.")
    if line != printed:
        return None
    return (
        COUNTED_ENTRY_OPPONENT_TEMPLATE,
        {"handler_id": COUNTED_ENTRY_OPPONENT_HANDLER_ID, "schema_version": 1, "event": "zone.change"},
        COUNTED_ENTRY_OPPONENT_DEPENDENCIES,
    )


def _linked_entry_opponent_handler(text: str):
    if text.casefold() == "as this creature enters, choose a player.":
        return (
            LINKED_ENTRY_PLAYER_TEMPLATE,
            {"handler_id": ENTRY_PLAYER_HANDLER_ID, "schema_version": 2, "event": "zone.change"},
            (ENTRY_DESIGNATION_CAPABILITY,),
        )
    if text.casefold() not in {"as this artifact enters, choose an opponent.", "as this creature enters, choose an opponent."}:
        return None
    return (
        LINKED_ENTRY_OPPONENT_TEMPLATE,
        {"handler_id": ENTRY_OPPONENT_HANDLER_ID, "schema_version": 1, "event": "zone.change"},
        (ENTRY_DESIGNATION_CAPABILITY,),
    )


def _counted_creature_type_entry_handler(text: str):
    match = re.fullmatch(
        rf"As this artifact enters, choose a creature type\.\s+This artifact enters with a "
        rf"(?P<counter>(?a:{COUNTED_ENTRY_COUNTER_NAME})) counter on it for each creature you control "
        r"of the chosen type\.",
        text, re.IGNORECASE,
    )
    if match is None:
        return None
    from ..semantic_runtime.entry_designations import CountedCreatureTypeEntryDesignationHandler

    descriptor = {
        "handler_id": COUNTED_CREATURE_TYPE_ENTRY_HANDLER_ID,
        "schema_version": 4,
        "event": "zone.change",
        "designation": "creature_type",
        "counter_name": match["counter"].casefold(),
    }
    handler = CountedCreatureTypeEntryDesignationHandler()
    try:
        handler.validate(descriptor)
    except ValueError:
        return None
    return "intrinsic-entry-creature-type-counter-v4", descriptor, handler.capability_dependencies


def _plain_entry_designation_handler(text: str, *, source_name: str):
    source = (
        rf"(?:{SourceReferenceSpec(source_name).regex_pattern}|"
        r"this (?:artifact|Aura|card|creature|enchantment|Equipment|land|permanent))"
    )
    match = re.fullmatch(
        rf"As {source} enters, choose a (?P<kind>color|creature type)\.",
        text,
        re.IGNORECASE,
    )
    if match is None:
        return _listed_creature_type_entry_handler(text, source=source)
    return (
        "intrinsic-entry-designation-v1",
        {
            "handler_id": ENTRY_DESIGNATION_HANDLER_ID,
            "schema_version": 1,
            "event": "zone.change",
            "designation": match["kind"].casefold().replace(" ", "_"),
        },
        (ENTRY_DESIGNATION_CAPABILITY,),
    )


def _listed_creature_type_entry_handler(text, *, source):
    from ..creature_subtypes import canonical_creature_subtype

    match = re.fullmatch(rf"As {source} enters, choose (?P<leading>[^.]+?)(?:,)? or (?P<last>[^,.]+)\.",
        text, re.IGNORECASE)
    if match is None:
        return None
    types = tuple(canonical_creature_subtype(value) for value in (*match["leading"].split(", "), match["last"]))
    if any(value is None for value in types) or len(set(types)) != len(types):
        return None
    return "intrinsic-limited-creature-type-entry-v3", {
        "handler_id": LIMITED_CREATURE_TYPE_ENTRY_HANDLER_ID, "schema_version": 3, "event": "zone.change",
        "designation": "creature_type", "options": sorted(types),
    }, (ENTRY_DESIGNATION_CAPABILITY, LIMITED_CREATURE_TYPE_ENTRY_CAPABILITY)


def chosen_characteristics_handler(text: str, *, source_name: str = 'source'):
    from .conditional_chosen_templates import conditional_chosen_characteristics_handler

    if text.casefold() == "this creature has protection from the chosen player.":
        from ..semantic_runtime.chosen_characteristics import linked_player_protection_descriptor

        return (
            LINKED_PLAYER_PROTECTION_TEMPLATE, linked_player_protection_descriptor(),
            LINKED_PLAYER_PROTECTION_CAPABILITIES,
        )
    return (conditional_chosen_characteristics_handler(text, source_name=source_name)
            or _fixed_chosen_characteristics_handler(text))


def _fixed_chosen_characteristics_handler(text: str):
    match = re.fullmatch(
        r"(?P<other>Other )?(?P<all>All )?creatures"
        r"(?P<controlled> you control)? of the chosen (?P<kind>color|type)"
        r"(?P<opponents> your opponents control)? (?P<body>get .+|have .+)",
        text,
        re.IGNORECASE,
    )
    if match is None or (match["controlled"] and match["opponents"]):
        return None
    if match["opponents"]:
        subject = "Creatures your opponents control"
    else:
        subject = (
            ("Other " if match["other"] else "")
            + "Creatures"
            + (" you control" if match["controlled"] else "")
        )
    body = subject + " " + match["body"]
    compiled = None
    compilers = (
        fixed_query_characteristic_grant_handler,
        fixed_power_toughness_anthem_handler,
        fixed_query_keyword_grant_handler,
    )
    for compiler in compilers:
        compiled = compiler(body)
        if compiled is not None:
            break
    if compiled is None:
        return None
    _, descriptor, capabilities = compiled
    if not isinstance(descriptor, Mapping):
        return None
    body_capabilities = (
        (capabilities,) if isinstance(capabilities, str) else capabilities
    )
    result = (
        "fixed-chosen-designation-characteristics-v1",
        {
            "handler_id": CHOSEN_CHARACTERISTICS_HANDLER_ID,
            "schema_version": 1,
            "event": "characteristics.evaluate",
            "designation": (
                "color" if match["kind"].casefold() == "color" else "creature_type"
            ),
            "body": dict(descriptor),
        },
        tuple(dict.fromkeys((CHOSEN_CHARACTERISTICS_CAPABILITY, *body_capabilities))),
    )
    from ..semantic_runtime.chosen_characteristics import (
        CHOSEN_SOURCE_COUNTER_ANTHEM_CAPABILITY, SOURCE_COUNTER_ANTHEM_HANDLER, ChosenCharacteristicsHandler,
    )
    try:
        ChosenCharacteristicsHandler().validate(result[1])
    except (TypeError, ValueError):
        return None
    if descriptor["handler_id"] == SOURCE_COUNTER_ANTHEM_HANDLER:
        return result[0] + "-source-counter", result[1], (*result[2], CHOSEN_SOURCE_COUNTER_ANTHEM_CAPABILITY)
    return result

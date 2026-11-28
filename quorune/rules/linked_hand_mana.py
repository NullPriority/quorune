from .linked_hand_mana_model import CAPABILITY, CHOICE, MANA_HANDLER, REQUIREMENTS, imprint_pair
from .effect_shape_walkers import nested_effects


def linked_hand_mana_program_dependencies(program):
    marked = tuple(row for row in nested_effects((program.effects, program.handlers)) if row.get("op") == CHOICE)
    readers = tuple(row for row in program.handlers if row.get("handler_id") == MANA_HANDLER)
    catalogs = tuple(row for row in program.handlers if row.get("handler_id") == "activation.catalog.pinned.v1")
    catalog_output = (catalogs[0].get("ability") or {}).get("color_set_mana_output") if len(catalogs) == 1 else None
    if readers or (catalog_output and catalog_output.get("relation") == "linked_exile"):
        if (marked or program.effects or len(readers) != 1 or len(catalogs) > 1
                or len(program.handlers) != 1 + len(catalogs)
                or (catalogs and catalog_output != readers[0].get("ability"))
                or program.event != "activate" or program.active_zone != "battlefield" or program.target_schema is not None):
            raise ValueError("Linked hand mana requires its one original battlefield mana ability")
        return REQUIREMENTS
    if not marked and CAPABILITY not in program.capability_dependencies:
        return ()
    if (len(marked) != 1 or tuple(program.effects) != marked or program.handlers
            or program.active_zone != "battlefield" or program.event != "permanent.enter.self"
            or program.event_condition is not None or program.target_schema is not None or program.cost_schema is not None):
        raise ValueError("Linked hand imprint requires its exact original enters trigger")
    imprint_pair(marked[0])
    return REQUIREMENTS


def is_closed_linked_hand_imprint(program):
    if not any(effect.get("op") == CHOICE for effect in program.effects):
        return False
    try:
        return set(linked_hand_mana_program_dependencies(program)) <= set(program.capability_dependencies)
    except (TypeError, KeyError, ValueError):
        return False


def validate_linked_hand_mana_group(abilities):
    from ..color_set_mana_abilities import ColorSetActivatedManaAbilitySpec
    producers = []
    consumers = []
    for program in abilities:
        if not linked_hand_mana_program_dependencies(program):
            continue
        face = program.provenance.get("face_id") or "front"
        if program.event == "activate":
            spec = ColorSetActivatedManaAbilitySpec.from_dict(program.handlers[0]["ability"])
            consumers.append((face, spec.linked_exile_pair))
        else:
            producers.append((face, imprint_pair(program.effects[0])))
    if (producers or consumers) and (len(producers) != 1 or producers != consumers):
        raise ValueError("Linked hand mana requires its original same-face producer and consumer")

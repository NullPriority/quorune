from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence


FORM_VERSION = 1


def _title(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip().title()


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _labels(context: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in (
        "hand",
        "candidates",
        "objects",
        "search_cards",
        "options",
        "triggers",
        "battle_defenders",
        "planeswalker_defenders",
    ):
        for row in _rows(context.get(key)):
            value = row.get("id")
            if value is None:
                continue
            result[str(value)] = str(
                row.get("label") or row.get("name") or value
            )
    return result


def _options(
    values: Sequence[Any],
    context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    labels = _labels(context)
    return [
        {
            "value": copy.deepcopy(value),
            "label": labels.get(str(value), str(value)),
        }
        for value in values
    ]


def _object_map_field(
    name: str,
    value: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    field: dict[str, Any] = {
        "name": name,
        "label": str(value.get("label") or _title(name)),
        "required": bool(value.get("required", True)),
        "control": "object_map",
        "keys": list(value.get("legal_refs") or []),
        "options": _options(list(value.get("legal_values") or []), context),
    }
    required_count = value.get("required")
    if isinstance(required_count, int) and not isinstance(required_count, bool):
        field["minimum"] = required_count
    return field


def _ordered_partition_field(
    name: str,
    value: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "name": name,
        "label": str(value.get("label") or _title(name)),
        "required": bool(value.get("required", True)),
        "control": "ordered_partition",
        "options": _options(list(value.get("legal_refs") or []), context),
        "partitions": copy.deepcopy(dict(value.get("partitions") or {})),
        "complete": bool(value.get("complete", True)),
        "distinct": bool(value.get("distinct", True)),
    }


def _field(
    name: str,
    descriptor: Any,
    context: Mapping[str, Any],
) -> dict[str, Any] | None:
    if descriptor == "boolean":
        descriptor = {"type": "boolean"}
    if not isinstance(descriptor, Mapping):
        return None
    value = dict(descriptor)
    field: dict[str, Any] = {
        "name": name,
        "label": str(value.get("label") or _title(name)),
        "required": bool(
            value.get("required", not value.get("optional", False))
        ),
    }
    target_schema = value.get("target_schema")
    if isinstance(target_schema, Mapping):
        field.update(
            {
                "control": "targets",
                "schema": copy.deepcopy(dict(target_schema)),
            }
        )
        if "default" in value:
            field["default"] = copy.deepcopy(value["default"])
        return field

    shape = str(value.get("shape") or "")
    value_type = str(value.get("type") or "")
    legal_values = value.get("legal_values")
    legal_refs = value.get("legal_refs")
    if shape == "object_map":
        return _object_map_field(name, value, context)
    if shape == "ordered_partition":
        return _ordered_partition_field(name, value, context)
    if name == "copy_targets" and value.get("copy_count") is not None:
        field.update(
            {
                "control": "copy_targets",
                "copy_count": int(value.get("copy_count") or 0),
                "may_keep_default": bool(value.get("may_keep_default")),
                "copies": copy.deepcopy(list(context.get("copies") or [])),
            }
        )
        return field
    if value_type == "boolean" or (
        isinstance(legal_values, Sequence)
        and not isinstance(legal_values, (str, bytes))
        and legal_values
        and all(isinstance(item, bool) for item in legal_values)
    ):
        field["control"] = "boolean"
        if isinstance(legal_values, Sequence):
            field["legal_values"] = list(legal_values)
        if "default" in value:
            field["default"] = bool(value["default"])
        return field
    if value_type == "mana_bundle":
        options = _rows(value.get("options"))
        field.update(
            {
                "control": "mana_modes",
                "options": [
                    {
                        "value": copy.deepcopy(option.get("value", {})),
                        "label": str(option.get("label") or "Add mana"),
                    }
                    for option in options
                ],
            }
        )
        if options:
            field["default"] = copy.deepcopy(
                options[0].get("value", {})
            )
        return field
    if value_type == "integer":
        field["control"] = "integer"
        if value.get("minimum") is not None:
            field["minimum"] = int(value["minimum"])
        if value.get("maximum") is not None:
            field["maximum"] = int(value["maximum"])
        if value.get("default") is not None:
            field["default"] = int(value["default"])
        return field
    if value_type in {"card_name", "creature_type", "string"}:
        field["control"] = "text"
        if value.get("max_length") is not None:
            field["max_length"] = int(value["max_length"])
        return field
    if value_type == "seat":
        seats = list(value.get("legal_seats") or [])
        field.update(
            {
                "control": "select",
                "options": _options(seats, context),
            }
        )
        return field
    if isinstance(legal_refs, Sequence) and not isinstance(
        legal_refs, (str, bytes)
    ):
        minimum = value.get("count", value.get("minimum"))
        maximum = value.get("count", value.get("maximum"))
        multiple = (
            shape == "ref_array"
            or value_type == "object_ref_array"
            or minimum is not None
            or maximum is not None
            or name.endswith("s")
        )
        field.update(
            {
                "control": "refs" if multiple else "ref",
                "options": _options(list(legal_refs), context),
            }
        )
        if minimum is not None:
            field["minimum"] = int(minimum)
        if maximum is not None:
            field["maximum"] = int(maximum)
        if value.get("allowed_cardinalities") is not None:
            field["allowed_cardinalities"] = list(value["allowed_cardinalities"])
            if value.get("distinct") is not None:
                field["distinct"] = bool(value["distinct"])
        if value.get("order") is not None:
            field["ordered"] = True
            field["order"] = str(value["order"])
        return field
    if isinstance(legal_values, Sequence) and not isinstance(
        legal_values, (str, bytes)
    ):
        field.update(
            {
                "control": "select",
                "options": _options(list(legal_values), context),
            }
        )
        if "default" in value:
            field["default"] = copy.deepcopy(value["default"])
        return field
    return None


def _schema_fields(
    schema: Any,
    context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(schema, Mapping):
        return []
    value = dict(schema)
    fields: list[dict[str, Any]] = []
    field_name = value.get("field")
    if isinstance(field_name, str) and field_name:
        parsed = _field(field_name, value, context)
        if parsed is not None:
            fields.append(parsed)
        top_order = value.get("top_order")
        if isinstance(top_order, Mapping):
            order_name = str(top_order.get("field") or "top_order")
            order_field = _field(
                order_name,
                {
                    "type": "object_ref_array",
                    "legal_refs": list(value.get("legal_refs") or []),
                    "minimum": 0,
                    "maximum": len(list(value.get("legal_refs") or [])),
                    "order": top_order.get("order") or "top-first",
                    "required": False,
                },
                context,
            )
            if order_field is not None:
                order_field["options_from_map"] = field_name
                order_field["required_value"] = str(
                    top_order.get("required_when_value") or "top"
                )
                fields.append(order_field)
        if value.get("entry_pay_life") == "boolean":
            fields.append(
                {
                    "name": "entry_pay_life",
                    "label": "Pay 2 life to enter untapped",
                    "control": "boolean",
                    "required": False,
                    "default": False,
                }
            )
        return fields

    # Compatibility for the first fetchland schema. New schemas should use
    # explicit field descriptors, but the adapter remains deterministic for
    # already persisted Game Record decisions.
    candidates = value.get("search_candidates")
    if isinstance(candidates, Sequence) and not isinstance(
        candidates, (str, bytes)
    ):
        parsed = _field(
            "search_card",
            {
                "type": "object_ref",
                "legal_refs": list(candidates),
                "optional": bool(value.get("may_fail_to_find")),
            },
            context,
        )
        if parsed is not None:
            fields.append(parsed)

    for name, descriptor in value.items():
        if name in {
            "search_candidates",
            "may_fail_to_find",
            "example",
            "required",
        }:
            continue
        parsed = _field(str(name), descriptor, context)
        if parsed is not None:
            fields.append(parsed)
    return fields


def _target_field(schema: Any) -> dict[str, Any] | None:
    if not isinstance(schema, Mapping):
        return None
    return {
        "name": "targets",
        "label": "Targets and modes",
        "control": "targets",
        "required": True,
        "schema": copy.deepcopy(dict(schema)),
    }


def _variant_form(
    options: Any,
    context: Mapping[str, Any],
) -> dict[str, Any] | None:
    rows = _rows(options)
    if not rows:
        return None
    variants: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        value = str(row.get("id") or index)
        fields = _schema_fields(row.get("choice_schema"), context)
        target = _target_field(row.get("target_schema"))
        if target is not None:
            fields.append(target)
        variants.append(
            {
                "value": value,
                "label": str(row.get("label") or _title(value)),
                "fields": fields,
            }
        )
    default = next(
        (
            variant["value"]
            for variant in variants
            if variant["value"] == "normal"
        ),
        variants[0]["value"],
    )
    return {
        "field": "cost_option",
        "label": "Casting cost",
        "default": default,
        "required": len(variants) > 1,
        "options": variants,
    }


def _ref_field(
    name: str,
    values: Sequence[Any],
    context: Mapping[str, Any],
    *,
    minimum: int,
    maximum: int,
    ordered: bool = False,
) -> dict[str, Any]:
    result = {
        "name": name,
        "label": _title(name),
        "control": (
            "refs"
            if name.endswith("s") or ordered or maximum != 1 or minimum != 1
            else "ref"
        ),
        "required": minimum > 0,
        "minimum": minimum,
        "maximum": maximum,
        "options": _options(list(values), context),
    }
    if ordered:
        result["ordered"] = True
    return result


def _special_fields(
    action: Mapping[str, Any],
    decision_kind: str,
    context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    action_name = str(action.get("action") or "")
    if decision_kind == "mulligan.bottom" and action_name == "bottom":
        count = int(context.get("count") or 0)
        refs = [row["id"] for row in _rows(context.get("hand")) if row.get("id")]
        return [_ref_field("cards", refs, context, minimum=count, maximum=count)]
    if decision_kind == "cleanup.discard" and action_name == "discard":
        count = int(context.get("count") or 0)
        refs = [row["id"] for row in _rows(context.get("hand")) if row.get("id")]
        return [_ref_field("cards", refs, context, minimum=count, maximum=count)]
    if decision_kind == "trigger.order" and action_name == "order":
        refs = [
            row["id"]
            for row in _rows(context.get("triggers"))
            if row.get("id")
        ]
        return [
            _ref_field(
                "triggers",
                refs,
                context,
                minimum=len(refs),
                maximum=len(refs),
                ordered=True,
            )
        ]
    if decision_kind == "choice.apnap" and action_name == "choose":
        refs = list(context.get("options") or [])
        count = int(context.get("count") or 0)
        return [_ref_field("cards", refs, context, minimum=count, maximum=count)]
    if decision_kind == "state.legend" and action_name == "choose":
        refs = list(context.get("keep_one") or [])
        return [_ref_field("card", refs, context, minimum=1, maximum=1)]
    if decision_kind == "combat.attackers" and action_name == "attack":
        rows = [
            {
                "value": str(row["id"]),
                "label": str(row.get("name") or row["id"]),
                "options": _options(list(context.get("defenders") or []), context),
            }
            for row in _rows(context.get("candidates"))
            if row.get("id")
        ]
        return [
            {
                "name": "attackers",
                "label": "Attackers and defenders",
                "control": "assignment_map",
                "required": True,
                "allow_none": True,
                "rows": rows,
            }
        ]
    if decision_kind == "combat.blockers" and action_name == "block":
        legal = context.get("legal_blocks") or {}
        rows = [
            {
                "value": str(ref),
                "label": str(ref),
                "options": _options(
                    list(legal.get(str(ref), []))
                    if isinstance(legal, Mapping)
                    else [],
                    context,
                ),
            }
            for ref in list(context.get("blockers") or [])
        ]
        return [
            {
                "name": "blocks",
                "label": "Blockers and attackers",
                "control": "assignment_map",
                "required": True,
                "allow_none": True,
                "rows": rows,
                "minimum_group_sizes": copy.deepcopy(
                    dict(context.get("minimum_blockers") or {})
                ),
            }
        ]
    if decision_kind == "combat.damage" and action_name == "assign_damage":
        return [
            {
                "name": "assignments",
                "label": "Combat damage",
                "control": "damage_assignments",
                "required": True,
                "combat": copy.deepcopy(dict(context.get("combat") or {})),
            }
        ]
    if action_name == "pass":
        return [
            {
                "name": "yield",
                "label": "Pass duration",
                "control": "select",
                "required": False,
                "options": _options(
                    [
                        "none",
                        "until_public_change",
                        "until_my_turn",
                        "auto_if_no_response",
                    ],
                    context,
                ),
                "default": "none",
            }
        ]
    if action_name == "mulligan":
        return [
            {
                "name": "override_reason",
                "label": "Post-free mulligan reason (optional)",
                "control": "text",
                "required": False,
                "max_length": 500,
            }
        ]
    return []


def build_action_form(
    action: Mapping[str, Any],
    *,
    decision_kind: str,
    context: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Normalize one server-issued action into a JSON-only UI form.

    The adapter does not decide legality. It translates already projected,
    principal-scoped metadata into controls and is shared by projection and
    command-field authorization so a displayed field is also an authorized
    field. The engine still revalidates every submitted value.
    """

    fields = _schema_fields(action.get("choice_schema"), context)
    variants = _variant_form(action.get("cost_options"), context)
    if variants is None and isinstance(action.get("choice_schema"), Mapping):
        variants = _variant_form(
            action["choice_schema"].get("cast_options"), context
        )
    target = _target_field(action.get("target_schema"))
    if target is not None and not (
        variants
        and any(
            any(field.get("control") == "targets" for field in option["fields"])
            for option in variants["options"]
        )
    ):
        fields.append(target)
    if not fields and variants is None:
        fields = _special_fields(action, decision_kind, context)
    if not fields and variants is None:
        return None
    result: dict[str, Any] = {
        "v": FORM_VERSION,
        "fields": fields,
        "submit_label": str(
            action.get("label") or _title(str(action.get("action") or "submit"))
        ),
    }
    if variants is not None:
        result["variants"] = variants
    return result


def delegated_choice_fields(
    action: Mapping[str, Any],
    *,
    decision_kind: str,
    context: Mapping[str, Any],
) -> set[str]:
    form = build_action_form(
        action,
        decision_kind=decision_kind,
        context=context,
    )
    if form is None:
        return set()
    result: set[str] = set()

    def add_fields(fields: Sequence[Mapping[str, Any]]) -> None:
        for field in fields:
            name = str(field.get("name") or "")
            if name:
                result.add(name)
            if field.get("control") == "targets":
                result.add("modes")

    add_fields(list(form.get("fields") or []))
    variants = form.get("variants")
    if isinstance(variants, Mapping):
        result.add(str(variants.get("field") or "cost_option"))
        for option in _rows(variants.get("options")):
            add_fields(_rows(option.get("fields")))
    return result

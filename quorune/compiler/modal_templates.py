from __future__ import annotations

"""Closed compiler owners for fixed nonrepeating modal programs."""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from . import regex_working_set as re
from typing import Any, Callable, Mapping, Sequence

from ..rules.modal_selection import (
    COMMANDER_CAST_MODE_LIMIT_FIELD, conditional_cast_mode_capability, wizard_cast_mode_limit_matches,
)
from ..util import stable_json
from ..rules.modal_selection import (
    DISTINCT_TURN_GRAVEYARD_MODES_VERSION, DISTINCT_TURN_TRIGGER_MODES_FIELD, valid_distinct_turn_trigger_modes,
)
from .declared_effect_amounts import contains_declared_effect_amount
from .linked_exile_return_templates import scope_linked_return_bindings


FIXED_CHOOSE_ONE_MODAL_MECHANIC = "fixed-choose-one-modal-spell"
FIXED_CHOOSE_ONE_MODAL_CAPABILITY = "choice.modal.fixed_one"
FIXED_NONREPEATING_MODAL_MECHANIC = "fixed-nonrepeating-modal"
FIXED_NONREPEATING_MODAL_CAPABILITY = "choice.modal.fixed_nonrepeating"

CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]
EffectCompiler = Callable[[str], CompiledEffectTemplate]
MaterialRow = tuple[str, str, Any]

_HEADER = "Choose one —"
_NONREPEATING_HEADERS = {
    _HEADER: (1, 1),
    "Choose one or both —": (1, 2),
    "Choose one or more —": (1, None),
    "Choose any number —": (0, None),
    "Choose up to one —": (0, 1),
    "Choose two —": (2, 2),
}
_DISTINCT_TURN_HEADERS = (
    "Choose one that hasn't been chosen this turn \u2014",
    "Choose one that hasn't been chosen this turn.",
)
_BULLET_PREFIX = "• "
_NAMED_MODE = re.compile(
    r"^(?P<label>[A-Z][A-Za-z' ]*[A-Za-z']) — (?P<body>.+)$"
)


@dataclass(frozen=True, slots=True)
class FixedChooseOneModalSpellTemplate:
    """One mandatory choice among two or three independently closed modes."""

    component_template_ids: tuple[str, ...]
    _target_schema: Mapping[str, Any]
    mechanic_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            len(self.component_template_ids) not in {2, 3}
            or any(not value for value in self.component_template_ids)
            or self.mechanic_ids[:1] != (FIXED_CHOOSE_ONE_MODAL_MECHANIC,)
        ):
            raise ValueError("Fixed Choose one modal template is malformed")
        schema = deepcopy(dict(self._target_schema))
        if schema.get("mode_count") != 1:
            raise ValueError("Fixed Choose one modal template requires one mode")
        modes = schema.get("modes")
        expected = tuple(
            f"mode_{index}"
            for index in range(1, len(self.component_template_ids) + 1)
        )
        if not isinstance(modes, Mapping) or tuple(modes) != expected:
            raise ValueError("Fixed Choose one modal definitions are malformed")
        object.__setattr__(self, "_target_schema", schema)

    @property
    def template_id(self) -> str:
        digest = hashlib.sha256(
            stable_json(
                {
                    "components": self.component_template_ids,
                    "target_schema": self._target_schema,
                }
            ).encode("utf-8")
        ).hexdigest()[:16]
        return f"fixed-choose-one-modal-{digest}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return ()

    @property
    def target_schema(self) -> Mapping[str, Any]:
        return deepcopy(self._target_schema)

    @property
    def mechanics(self) -> tuple[str, ...]:
        return self.mechanic_ids

    def compiled(self) -> CompiledEffectTemplate:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


@dataclass(frozen=True, slots=True)
class FixedNonrepeatingModalTemplate:
    """One bounded ordered selection among independently closed modes."""

    component_template_ids: tuple[str, ...]
    minimum_modes: int
    maximum_modes: int
    context_prefix: str
    _target_schema: Mapping[str, Any]
    mechanic_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        mode_total = len(self.component_template_ids)
        schema = deepcopy(dict(self._target_schema))
        supported_bounds = {
            (0, mode_total),
            (0, 1),
            (1, 1),
            (1, mode_total),
            (2, 2),
        }
        if wizard_cast_mode_limit_matches(schema):
            supported_bounds.add((1, 2))
        if (
            mode_total not in {2, 3, 4, 5}
            or any(not value for value in self.component_template_ids)
            or not 0 <= self.minimum_modes <= self.maximum_modes <= mode_total
            or (self.minimum_modes == 0 and not self.context_prefix)
            or (self.minimum_modes, self.maximum_modes) not in supported_bounds
            or self.mechanic_ids[:1]
            != (FIXED_NONREPEATING_MODAL_MECHANIC,)
        ):
            raise ValueError("Fixed nonrepeating modal template is malformed")
        fields = {"mode_count", "min_modes", "max_modes", "modes"}
        if conditional_cast_mode_capability(schema):
            fields.add(COMMANDER_CAST_MODE_LIMIT_FIELD)
        if DISTINCT_TURN_TRIGGER_MODES_FIELD in schema:
            fields.add(DISTINCT_TURN_TRIGGER_MODES_FIELD)
        if (
            set(schema) != fields
            or (DISTINCT_TURN_TRIGGER_MODES_FIELD in schema
                and (not self.context_prefix or not valid_distinct_turn_trigger_modes(schema)))
            or schema.get("mode_count") != self.minimum_modes
            or schema.get("min_modes") != self.minimum_modes
            or schema.get("max_modes") != self.maximum_modes
        ):
            raise ValueError("Fixed modal selection bounds are malformed")
        modes = schema.get("modes")
        expected = tuple(
            f"mode_{index}" for index in range(1, mode_total + 1)
        )
        if not isinstance(modes, Mapping) or tuple(modes) != expected:
            raise ValueError("Fixed modal definitions are malformed")
        object.__setattr__(self, "_target_schema", schema)

    @property
    def template_id(self) -> str:
        digest = hashlib.sha256(
            stable_json(
                {
                    "components": self.component_template_ids,
                    "minimum_modes": self.minimum_modes,
                    "maximum_modes": self.maximum_modes,
                    "target_schema": self._target_schema,
                }
            ).encode("utf-8")
        ).hexdigest()[:16]
        return f"fixed-nonrepeating-modal-{digest}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return ()

    @property
    def target_schema(self) -> Mapping[str, Any]:
        return deepcopy(self._target_schema)

    @property
    def mechanics(self) -> tuple[str, ...]:
        return self.mechanic_ids

    def compiled(self) -> CompiledEffectTemplate:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


def _mode_body(material_line: str) -> str | None:
    if not material_line.startswith(_BULLET_PREFIX):
        return None
    body = material_line[len(_BULLET_PREFIX) :]
    named = _NAMED_MODE.fullmatch(body)
    return named.group("body") if named is not None else body


def fixed_choose_one_modal_spell_template(
    material_rows: Sequence[MaterialRow],
    *,
    compile_effect: EffectCompiler,
) -> FixedChooseOneModalSpellTemplate | None:
    """Lower only a complete ordinary two- or three-mode spell face."""

    if (
        len(material_rows) not in {3, 4}
        or material_rows[0][1] != _HEADER
    ):
        return None
    bodies = tuple(_mode_body(row[1]) for row in material_rows[1:])
    if any(body is None or not body.strip() for body in bodies):
        return None
    compiled = tuple(compile_effect(str(body)) for body in bodies)
    if any(
        template_id is None
        or not effects
        or not mechanics
        or contains_declared_effect_amount(effects)
        or target_schema is not None
        and "modes" in target_schema
        for template_id, effects, target_schema, mechanics in compiled
    ):
        return None

    modes: dict[str, dict[str, Any]] = {}
    mechanic_ids = [FIXED_CHOOSE_ONE_MODAL_MECHANIC]
    for index, (_template, effects, target_schema, mechanics) in enumerate(
        compiled,
        1,
    ):
        definition = deepcopy(dict(target_schema or {}))
        if target_schema is None:
            definition["groups"] = []
        definition["effects"] = scope_linked_return_bindings([deepcopy(dict(effect)) for effect in effects], f"mode_{index}")
        definition["mechanics"] = list(mechanics)
        modes[f"mode_{index}"] = definition
        mechanic_ids.extend(mechanics)
    return FixedChooseOneModalSpellTemplate(
        component_template_ids=tuple(
            str(template_id)
            for template_id, _effects, _schema, _mechanics in compiled
        ),
        _target_schema={"mode_count": 1, "modes": modes},
        mechanic_ids=tuple(dict.fromkeys(mechanic_ids)),
    )


def fixed_nonrepeating_modal_template(
    material_rows: Sequence[MaterialRow],
    *,
    compile_effect: EffectCompiler,
) -> FixedNonrepeatingModalTemplate | None:
    """Lower one complete fixed modal block without repeatable selections."""

    if len(material_rows) not in {3, 4, 5, 6}:
        return None
    header_line = material_rows[0][1]
    header = next(
        (
            value
            for value in (*_NONREPEATING_HEADERS, *_DISTINCT_TURN_HEADERS)
            if header_line.casefold().replace("\u2019", "'").endswith(value.casefold())
        ),
        None,
    )
    if header is None:
        return None
    context_prefix = header_line[: -len(header)]
    if context_prefix and not context_prefix.endswith(" "):
        return None
    bodies = tuple(_mode_body(row[1]) for row in material_rows[1:])
    if any(body is None or not body.strip() for body in bodies):
        return None
    distinct_turn_modes = header in _DISTINCT_TURN_HEADERS
    distinct_turn_version = (
        DISTINCT_TURN_GRAVEYARD_MODES_VERSION
        if context_prefix.casefold() == "whenever one or more cards leave your graveyard, "
        else 1
    )
    if distinct_turn_modes and (not context_prefix or len(bodies) != 3):
        return None
    minimum_modes, fixed_maximum = (1, 1) if distinct_turn_modes else _NONREPEATING_HEADERS[header]
    maximum_modes = len(bodies) if fixed_maximum is None else fixed_maximum
    if maximum_modes > len(bodies) or (minimum_modes == 0 and not context_prefix):
        return None
    compiled = tuple(compile_effect(str(body)) for body in bodies)
    if any(
        template_id is None
        or not effects
        or not mechanics
        or contains_declared_effect_amount(effects)
        or target_schema is not None
        and "modes" in target_schema
        for template_id, effects, target_schema, mechanics in compiled
    ):
        return None

    if distinct_turn_modes and any(schema is not None for _, _, schema, _ in compiled):
        return None
    modes: dict[str, dict[str, Any]] = {}
    mechanic_ids = [FIXED_NONREPEATING_MODAL_MECHANIC]
    for index, (_template, effects, target_schema, mechanics) in enumerate(
        compiled,
        1,
    ):
        mode_id = f"mode_{index}"
        if target_schema is not None and 'groups' in target_schema:
            modes[mode_id]={**deepcopy(dict(target_schema)),'group_scope':'mode',
                'effects':scope_linked_return_bindings([deepcopy(dict(effect)) for effect in effects],mode_id),
                'mechanics':list(mechanics)}
            mechanic_ids.extend(mechanics)
            continue
        groups: list[dict[str, Any]] = []
        if target_schema is not None:
            group = deepcopy(dict(target_schema))
            group["id"] = f"{mode_id}_target_1"
            groups.append(group)
        modes[mode_id] = {
            "groups": groups,
            "effects": scope_linked_return_bindings([deepcopy(dict(effect)) for effect in effects], mode_id),
            "mechanics": list(mechanics),
        }
        mechanic_ids.extend(mechanics)
    return FixedNonrepeatingModalTemplate(
        component_template_ids=tuple(
            str(template_id)
            for template_id, _effects, _schema, _mechanics in compiled
        ),
        minimum_modes=minimum_modes,
        maximum_modes=maximum_modes,
        context_prefix=context_prefix,
        _target_schema={
            "mode_count": minimum_modes,
            "min_modes": minimum_modes,
            "max_modes": maximum_modes,
            "modes": modes,
            **({DISTINCT_TURN_TRIGGER_MODES_FIELD: distinct_turn_version} if distinct_turn_modes else {}),
        },
        mechanic_ids=tuple(dict.fromkeys(mechanic_ids)),
    )


__all__ = [
    "FIXED_CHOOSE_ONE_MODAL_CAPABILITY",
    "FIXED_CHOOSE_ONE_MODAL_MECHANIC",
    "FIXED_NONREPEATING_MODAL_CAPABILITY",
    "FIXED_NONREPEATING_MODAL_MECHANIC",
    "FixedChooseOneModalSpellTemplate",
    "FixedNonrepeatingModalTemplate",
    "fixed_choose_one_modal_spell_template",
    "fixed_nonrepeating_modal_template",
]

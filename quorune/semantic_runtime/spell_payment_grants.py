from __future__ import annotations

"""Typed battlefield sources for the existing spell payment mechanics."""

from dataclasses import dataclass, replace
from typing import Any, Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..card_program_faces import program_matches_face
from ..card_programs.admission import program_has_complete_card_program_admission
from ..object_predicate import ObjectQuerySpec
from ..object_query import object_matches_query, object_query_result
from .component_registry import exact_fields
from .context import SemanticNodeError


SPELL_PAYMENT_GRANT_CAPABILITY = "continuous.ability.spell_payment_grant"
SPELL_PAYMENT_GRANT_EVENT = "cast.payment.grant"
SPELL_PAYMENT_GRANT_HANDLERS = {
    kind: f"casting.payment.grant.{kind}.v1" for kind in ("convoke", "delve", "improvise")
}
SPELL_PAYMENT_GRANT_FIELD = "battlefield_payment_grants"
_SPELL_PREDICATES = (
    ObjectQuerySpec(),
    ObjectQuerySpec(types_all=("artifact",)),
    ObjectQuerySpec(types_all=("creature",)),
    ObjectQuerySpec(types_all=("creature",), supertypes_all=("legendary",)),
    ObjectQuerySpec(excluded_types=("artifact",)),
    ObjectQuerySpec(excluded_types=("creature",)),
    ObjectQuerySpec(minimum_color_count=2),
)


@dataclass(frozen=True, slots=True)
class SpellPaymentGrantSpec:
    kind: str
    predicate: ObjectQuerySpec
    origin: str | None = None

    def __post_init__(self) -> None:
        if (type(self.kind) is not str or self.kind not in SPELL_PAYMENT_GRANT_HANDLERS
                or not isinstance(self.predicate, ObjectQuerySpec) or self.predicate not in _SPELL_PREDICATES
                or self.origin not in (None, "exile")):
            raise ValueError("Spell payment grants require a closed spell predicate and casting origin")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "predicate": self.predicate.to_dict(), "origin": self.origin}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SpellPaymentGrantSpec:
        if not isinstance(value, Mapping) or set(value) != {"kind", "predicate", "origin"}:
            raise ValueError("Spell payment grant fields are closed")
        return cls(value["kind"], ObjectQuerySpec.from_dict(value["predicate"]), value["origin"])


@dataclass(frozen=True, slots=True)
class SpellPaymentGrantHandler:
    kind: str
    schema_version: int = 1
    family: str = SPELL_PAYMENT_GRANT_CAPABILITY
    event: str = SPELL_PAYMENT_GRANT_EVENT
    rule_references: tuple[str, ...] = ("601.2h", "611.3a", "611.3b", "702.51a", "702.66a", "702.126a")

    @property
    def handler_id(self) -> str:
        return SPELL_PAYMENT_GRANT_HANDLERS[self.kind]

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        return (SPELL_PAYMENT_GRANT_CAPABILITY, f"casting.payment.{self.kind}")

    def validate(self, descriptor: Mapping[str, Any]) -> SpellPaymentGrantSpec:
        exact_fields(descriptor, {"handler_id", "schema_version", "event", "requires_complete_card_program", "grant"},
                     field="battlefield spell payment grant")
        if (descriptor["handler_id"] != self.handler_id or type(descriptor["schema_version"]) is not int
                or descriptor["schema_version"] != self.schema_version or descriptor["event"] != self.event
                or descriptor["requires_complete_card_program"] is not True):
            raise SemanticNodeError("Spell payment grant requires its exact handler and complete source")
        try:
            spec = SpellPaymentGrantSpec.from_dict(descriptor["grant"])
            if spec.kind != self.kind:
                raise ValueError("Spell payment grant and handler kinds differ")
            return spec
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor: Mapping[str, Any], context: Any) -> tuple[SpellPaymentGrantSpec, ...]:
        return (self.validate(descriptor),)


def active_spell_payment_grants(host, seat: str) -> tuple[SpellPaymentGrantSpec, ...]:
    """Resolve trusted, currently present source abilities in the caster's control."""
    if not host.semantics.programs_for_event(SPELL_PAYMENT_GRANT_EVENT):
        return ()
    from .cast_costs import default_cast_cost_component_registry

    registry = default_cast_cost_component_registry()
    handler_ids = frozenset(SPELL_PAYMENT_GRANT_HANDLERS.values())
    current = host._effective_static_component_key_map()
    result = []
    for source in host._semantic_event_sources(zones={"battlefield"}):
        if source.zone != "battlefield" or source.phased_out or source.controller != seat:
            continue
        keys = current.get(source.object_id, ())
        if not keys:
            continue
        record = host.card_record(source)
        if record is None:
            continue
        for program in host.semantics.runtime_handler_programs_for_oracle(
            record.oracle_id, active_zone="battlefield", event=SPELL_PAYMENT_GRANT_EVENT,
        ):
            if (program.key not in keys or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
                    or not program_matches_face(record, program, source)
                    or not program_has_complete_card_program_admission(program)
                    or not host.semantic_program_is_current_trusted(program)):
                continue
            for descriptor in program.handlers:
                if descriptor.get("handler_id") in handler_ids:
                    result.extend(registry.lower(descriptor, None))
    return tuple(result)


def matching_spell_payment_kinds(host, seat, card, grants, *, origin, cast_type_line=None, face_down=False):
    if not grants:
        return ()
    if face_down:
        effective = {"type_line": "Creature", "colors": ()}
    else:
        spell = replace(card, zone="stack", controller=seat) if card.zone != "stack" else card
        effective = dict(host._effective_card_data(spell))
    if cast_type_line is not None:
        effective["type_line"] = cast_type_line
    row = object_query_result(
        card, effective, type_parts=host._type_parts(str(effective.get("type_line") or "")),
        known_to_actor=True, attached_to_ref=None,
    )
    return tuple(sorted({spec.kind for spec in grants
                         if spec.origin in (None, origin) and object_matches_query(row, spec.predicate)}))


def granted_payment_mechanics(kinds):
    return [{"kind": kind, "schema_version": 1} for kind in kinds]


def revalidate_spell_payment_grant(host, seat, card, uses, *, origin, cast_type_line, face_down):
    if (not isinstance(uses, Mapping) or not uses
            or any(kind not in SPELL_PAYMENT_GRANT_HANDLERS or not isinstance(refs, list) or not refs
                   or any(type(ref) is not str or not ref for ref in refs) or len(set(refs)) != len(refs)
                   for kind, refs in uses.items())):
        raise ValueError("Granted spell payment selections are malformed")
    current = matching_spell_payment_kinds(
        host, seat, card, active_spell_payment_grants(host, seat), origin=origin,
        cast_type_line=cast_type_line, face_down=face_down,
    )
    if not set(uses) <= set(current):
        raise ValueError("A spell payment ability was lost before costs could be paid")

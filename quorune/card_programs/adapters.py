from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, replace
import hashlib
from typing import Any, Iterable, Literal, Mapping

from ..carddb import CardDatabase, CardRecord
from ..compiler.program_generation import (
    _generated_programs_from_ir,
    rulings_source_hash,
    runtime_handler_footprint,
)
from ..compiler.activated_ability_catalog import (
    catalog_carrier_is_shadowed,
    with_activated_ability_catalog,
)
from ..compiler.card_form_rules import (
    CardFormNode,
    IntrinsicBasicLandManaRuleNode,
    SharedTypeDoorRuleNode,
    compile_card_form_rules,
)
from ..oracle_ir import ORACLE_COMPILER_VERSION, compile_oracle_card
from ..rules.capabilities import CapabilityRegistry
from ..rules.capabilities import load_default_capability_registry
from ..semantics import SemanticProgram, SemanticRegistry
from ..util import stable_json
from .model import CardProgram, CardProgramError, CardProgramFace
from .reviewed_overlay import shadowed_reviewed_program_keys


SEMANTIC_PACK_COMPATIBILITY_COMPILER = "semantic-pack-v3-card-program-v2"


class CardProgramSourceMismatch(CardProgramError):
    """Two implementations for one card were built against different data."""

    def __init__(self, source: Literal["Oracle", "rulings"], oracle_id: str,
                 programs: tuple[SemanticProgram, ...], card_name: str | None) -> None:
        super().__init__(f"Semantic compatibility group has inconsistent {source} hashes")
        self.source = source
        self.oracle_id = oracle_id
        self.card_name = card_name
        self.program_sources = tuple(_program_source_identity(program) for program in sorted(programs, key=lambda row: row.key))
        fingerprint = _text_hash(stable_json([source, oracle_id, self.program_sources]))[:16]
        self.diagnostic_id = f"card-data-{fingerprint}"

    def diagnostic(self, card_name: str) -> dict[str, Any]:
        return {
            "diagnostic_id": self.diagnostic_id,
            "kind": f"{self.source.lower()}_source_mismatch",
            "card": card_name,
            "oracle_id": self.oracle_id,
            "programs": [dict(identity) for identity in self.program_sources],
        }


def _program_source_identity(program: SemanticProgram) -> dict[str, str | None]:
    provenance = program.provenance
    author = str(provenance.get("authored_by") or "")
    template = str(provenance.get("template_id") or "")
    if template or author.startswith("oracle-ir-"):
        origin = f"Generated program · {template or author}"
    elif author:
        origin = f"Reviewed program · {author}"
    else:
        origin = "Semantic program"
    return {
        "key": program.key,
        "origin": origin,
        "oracle_hash": str(provenance.get("source_oracle_hash") or "") or None,
        "rulings_hash": str(provenance.get("source_rulings_hash") or "") or None,
    }


def _source_hashes(programs: Iterable[SemanticProgram], field: str) -> set[str]:
    return {str(program.provenance.get(field) or "") for program in programs} - {""}


def card_program_source_diagnostics(
    record: CardRecord,
    programs: Iterable[SemanticProgram],
    *,
    oracle_hash: str,
    rulings_hash: str,
) -> list[dict[str, Any]]:
    values = tuple(programs)
    diagnostics = []
    sources = (
        ("Oracle", "source_oracle_hash", oracle_hash),
        ("rulings", "source_rulings_hash", rulings_hash),
    )
    for source, field, current in sources:
        if len(_source_hashes(values, field)) > 1 or any(
            program.trust_level == "trusted" and program.provenance.get(field) != current
            for program in values
        ):
            mismatch = CardProgramSourceMismatch(source, record.oracle_id, values, record.name)
            diagnostics.append(mismatch.diagnostic(record.name))
    return diagnostics


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _record_faces(
    record: CardRecord,
    *,
    compiled_face_ids: Iterable[str],
) -> tuple[CardProgramFace, ...]:
    face_ids = tuple(compiled_face_ids)
    if record.faces:
        if len(face_ids) != len(record.faces):
            raise CardProgramError(
                "Compiled face count does not match the card record"
            )
        return tuple(
            CardProgramFace(
                face_id=face_id,
                name=str(face.get("name") or record.name),
                type_line=str(face.get("type_line") or record.type_line),
                oracle_text_hash=_text_hash(
                    str(face.get("oracle_text") or "")
                ),
            )
            for face_id, face in zip(face_ids, record.faces, strict=True)
        )
    return (
        CardProgramFace(
            face_id=face_ids[0] if face_ids else "front",
            name=record.name,
            type_line=record.type_line,
            oracle_text_hash=_text_hash(record.oracle_text),
        ),
    )


def _program_face_id(program: SemanticProgram) -> str:
    explicit = str(program.provenance.get("face_id") or "").strip()
    if explicit:
        return explicit
    parts = program.ability_id.split(":")
    if len(parts) > 1:
        positional = parts[1].split("-", 1)[0]
        if positional in {"front", "back"}:
            return positional
    return "front"


def _bind_program_faces(
    programs: Iterable[SemanticProgram],
    faces: Iterable[CardProgramFace],
) -> tuple[SemanticProgram, ...]:
    face_values = tuple(faces)
    face_ids = {face.face_id for face in face_values}
    only_face = next(iter(face_ids)) if len(face_ids) == 1 else None
    positional_aliases = (
        {
            "front": face_values[0].face_id,
            "back": face_values[1].face_id,
        }
        if len(face_values) == 2
        else {}
    )
    result = []
    for program in programs:
        face_id = _program_face_id(program)
        if face_id in face_ids:
            result.append(program)
            continue
        if face_id in positional_aliases:
            provenance = dict(program.provenance)
            provenance["face_id"] = positional_aliases[face_id]
            provenance["face_identity_adapter"] = (
                "two_face_positional_alias"
            )
            result.append(replace(program, provenance=provenance))
            continue
        if only_face is None:
            raise CardProgramError(
                f"Ability {program.ability_id} has no unambiguous face identity"
            )
        provenance = dict(program.provenance)
        provenance["face_id"] = only_face
        provenance["face_identity_adapter"] = "single_face_compatibility"
        result.append(replace(program, provenance=provenance))
    return tuple(result)


def _card_form_rule_programs(
    record: CardRecord,
    *,
    nodes: Iterable[CardFormNode],
    oracle_source_hash: str,
    rulings_hash: str,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    trust_level: str,
) -> tuple[SemanticProgram, ...]:
    """Lower rules-derived card forms without treating them as Oracle text."""

    if capability_registry is None:
        return ()
    programs: list[SemanticProgram] = []
    for node in nodes:
        closure = capability_registry.closure(
            node.capability_dependencies,
            profile=capability_profile,
        )
        if trust_level == "trusted" and not closure.trusted:
            raise ValueError(
                f"{record.name} cannot be promoted to trusted generated "
                f"semantics: {node.descriptor()['kind']} capability is blocked"
            )
        descriptor = node.descriptor()
        intrinsic_mana = isinstance(node, IntrinsicBasicLandManaRuleNode)
        if intrinsic_mana:
            subject = descriptor["basic_land_type"]
            ability_id = (
                f"static:{node.face_id}:intrinsic-mana-{subject}"
            )
            label = f"{record.name} — intrinsic {subject} mana ability"
            event = "intrinsic_ability"
            template_id = "intrinsic_basic_land_mana"
            test_id = "card_form_rule:intrinsic_basic_land_mana"
            coverage = [
                "generated_card_form_rule",
                "intrinsic_basic_land_mana",
            ]
        elif isinstance(node, SharedTypeDoorRuleNode):
            ability_id = f"static:{node.face_id}:shared-type-doors"
            label = f"{record.name} shared type line"
            event = "intrinsic_ability"
            template_id = descriptor["kind"]
            test_id = "card_form_rule:shared_type_split_permanent"
            coverage = ["generated_card_form_rule", template_id]
        else:
            subject = descriptor["counter_name"]
            ability_id = f"static:{node.face_id}:intrinsic-entry-{subject}"
            label = f"{record.name} — intrinsic {subject} entry counters"
            event = "intrinsic_entry"
            template_id = "intrinsic_entry_counter"
            test_id = "card_form_rule:intrinsic_entry_counter"
            coverage = [
                "generated_card_form_rule",
                "intrinsic_entry_counter",
                *(
                    ["saga_entry_counter"]
                    if descriptor["required_type"] == "saga"
                    else []
                ),
            ]
        programs.append(
            SemanticProgram(
                key=f"{record.oracle_id}:{ability_id}",
                label=label,
                effects=[],
                requires_arbiter=trust_level != "trusted",
                oracle_id=record.oracle_id,
                ability_id=ability_id,
                active_zone="all",
                event=event,
                trust_level=trust_level,
                provenance={
                    "source_oracle_hash": oracle_source_hash,
                    "source_rulings_hash": rulings_hash,
                    "authored_by": "card-form-rule-compiler-v3",
                    "review_status": (
                        "capability_closure_verified"
                        if trust_level == "trusted"
                        else "generated_review_required"
                    ),
                    "template_id": template_id,
                    "face_id": node.face_id,
                    "source_kind": "type_line",
                    "source_span": asdict(node.span),
                    "card_form_descriptor": descriptor,
                    "capability_registry_fingerprint": (
                        closure.registry_fingerprint
                    ),
                    "capability_closure_fingerprint": closure.fingerprint,
                    "capability_profile": closure.profile,
                },
                tests=[test_id],
                event_condition={"card_form_rule": descriptor},
                coverage=coverage,
                capability_dependencies=list(
                    node.capability_dependencies
                ),
                capability_closure=closure.to_dict(),
            )
        )
    return tuple(programs)


def compile_card_program(
    db: CardDatabase,
    record: CardRecord,
    *,
    semantic_registry: SemanticRegistry | None = None,
    capability_registry: CapabilityRegistry | None = None,
    capability_profile: str = "traditional",
    trust_level: str = "provisional",
    trusted_mechanics: Iterable[str] = (),
) -> CardProgram:
    """Compile one pinned record and overlay reviewed ability programs by key."""

    ir = compile_oracle_card(
        record,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    return _compile_card_program_from_ir(
        db, record, ir, semantic_registry=semantic_registry,
        capability_registry=capability_registry, capability_profile=capability_profile,
        trust_level=trust_level,
    )


def _matches_retained_card_form(retained: SemanticProgram, fresh: SemanticProgram) -> bool:
    """Compare complete declarations while respecting the requested trust level."""
    stored = retained.to_dict()
    current = fresh.to_dict()
    if stored == current:
        return True
    if fresh.trust_level != "provisional" or retained.trust_level != "trusted":
        return False
    # An ordinary retained form was compiled as trusted. Explicit provisional
    # compilation keeps its own trust result while checking the same contract.
    current["trust_level"] = "trusted"
    current["requires_arbiter"] = False
    current["provenance"] = {
        **current["provenance"], "review_status": "capability_closure_verified",
    }
    return stored == current


def _without_retained_card_forms(
    programs: Iterable[SemanticProgram], forms: tuple[SemanticProgram, ...],
) -> Iterable[SemanticProgram]:
    if not forms:
        return programs
    forms_by_key = {program.key: program for program in forms}
    overlay = []
    for program in programs:
        fresh = forms_by_key.get(program.key)
        if fresh is None:
            overlay.append(program)
        elif not _matches_retained_card_form(program, fresh):
            raise CardProgramError(
                f"Retained card-form ability {program.key} conflicts with the current declaration"
            )
    return overlay


def _compile_card_program_from_ir(
    db: CardDatabase, record: CardRecord, ir: Any, *,
    semantic_registry: SemanticRegistry | None,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str, trust_level: str,
) -> CardProgram:
    """Build a fresh CardProgram from this invocation's original compiled IR."""
    faces = _record_faces(
        record,
        compiled_face_ids=(face.face_id for face in ir.faces),
    )
    card_form_compilation = compile_card_form_rules(
        record,
        compiled_face_ids=tuple(face.face_id for face in faces),
    )
    programs = {
        program.key: program
        for program in _generated_programs_from_ir(
            db,
            record,
            ir,
            trust_level=trust_level,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            has_rules_derived_trust_carrier=bool(
                card_form_compilation.nodes
            ),
        )
    }
    reviewed_keys: list[str] = []
    superseded_reviewed_keys: list[str] = []
    ruling_hash = rulings_source_hash(db, record)
    card_form_programs = _card_form_rule_programs(
        record,
        nodes=card_form_compilation.nodes,
        oracle_source_hash=ir.oracle_hash,
        rulings_hash=ruling_hash,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        trust_level=trust_level,
    )
    if semantic_registry is not None:
        overlay = _without_retained_card_forms(
            semantic_registry.programs_for_oracle(record.oracle_id), card_form_programs,
        )
        reviewed_programs = tuple(
            with_activated_ability_catalog(
                record,
                overlay,
                reference_programs=tuple(programs.values()),
            )
        )
        shadowed_reviewed_keys = shadowed_reviewed_program_keys(
            record,
            programs.values(),
            reviewed_programs,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            oracle_ir=ir,
        )
        for program in reviewed_programs:
            if program.key in shadowed_reviewed_keys:
                superseded_reviewed_keys.append(program.key)
                continue
            for key, generated in tuple(programs.items()):
                if catalog_carrier_is_shadowed(
                    record,
                    generated,
                    (program,),
                ):
                    programs.pop(key)
            reviewed_footprint = runtime_handler_footprint(program)
            if reviewed_footprint is not None and program.trust_level == "trusted":
                for key, generated in tuple(programs.items()):
                    if runtime_handler_footprint(generated) == reviewed_footprint:
                        programs.pop(key)
            programs[program.key] = program
            reviewed_keys.append(program.key)
    abilities = _bind_program_faces(
        (*programs.values(), *card_form_programs), faces
    )
    residuals = [
        {"face_id": face.face_id, **residual.to_dict()}
        for face in ir.faces
        for residual in face.residuals
    ]
    for face in ir.faces:
        spell_nodes = tuple(node for node in face.nodes if node.kind == 'spell_ability' and node.lowerable)
        if not spell_nodes or any(program.ability_id == 'spell:' + face.face_id for program in programs.values()):
            continue
        residuals.append({
            'face_id': face.face_id, 'residual_id': 'program_lowering:' + face.face_id,
            'kind': 'program_lowering', 'material': True,
            'text': '\n'.join(node.text for node in spell_nodes),
            'span': {'start': min(node.span.start for node in spell_nodes),
                'end': max(node.span.end for node in spell_nodes), 'line': spell_nodes[0].span.line},
            'reason': 'Recognized physical spell clauses have no executable lowering group',
            'blockers': ['complete physical spell program lowering'],
        })
    from ..compiler.paired_spell_faces import missing_paired_variant_residuals
    from ..spell_text_variants import SpellTextVariantKind
    for kind in SpellTextVariantKind:
        residuals.extend(missing_paired_variant_residuals(ir, programs.values(),kind))
    from ..compiler.fuse_faces import missing_fuse_program_residuals
    residuals.extend(missing_fuse_program_residuals(ir, programs.values()))
    residuals.extend(card_form_compilation.residuals)
    return CardProgram.create(
        compiler_version=ORACLE_COMPILER_VERSION,
        oracle_id=record.oracle_id,
        card_name=record.name,
        faces=faces,
        oracle_source_hash=ir.oracle_hash,
        rulings_source_hash=ruling_hash,
        abilities=abilities,
        residuals=residuals,
        provenance={
            "source": "oracle_ir_with_semantic_pack_overlay",
            "oracle_ir_schema_version": ir.schema_version,
            "oracle_ir_semantic_hash": ir.semantic_hash,
            "reviewed_semantic_keys": sorted(reviewed_keys),
            "superseded_reviewed_semantic_keys": sorted(
                superseded_reviewed_keys
            ),
            "capability_profile": (
                capability_profile if capability_registry is not None else None
            ),
            "capability_registry_fingerprint": (
                capability_registry.fingerprint
                if capability_registry is not None
                else None
            ),
        },
    )


def retain_rules_only_card_program(db, record, ir, registry, *, capability_registry, capability_profile):
    """Retain a complete rules-only card in the normal runtime view."""
    if ir.status != "exact" or ir.material_residuals:
        return
    compiled = _compile_best_available_card_program_from_ir(
        db, record, ir, semantic_registry=registry, capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    if compiled.residuals or compiled.trust_closure["trust_basis"] != "capability_closed":
        return
    if not compiled.abilities:
        registry.retain_empty_card_program(compiled)
    elif all("generated_card_form_rule" in ability.coverage for ability in compiled.abilities):
        for ability in compiled.abilities:
            registry.put(ability)


def compile_best_available_card_program(
    db: CardDatabase,
    record: CardRecord,
    *,
    semantic_registry: SemanticRegistry,
    capability_profile: str,
    capability_registry: CapabilityRegistry | None = None,
) -> CardProgram:
    """Compile trusted output when possible and preserve provisional IR otherwise."""

    capabilities = capability_registry or load_default_capability_registry()
    ir = compile_oracle_card(record, capability_registry=capabilities,
                             capability_profile=capability_profile)
    return _compile_best_available_card_program_from_ir(
        db, record, ir, semantic_registry=semantic_registry,
        capability_registry=capabilities, capability_profile=capability_profile,
    )


def _compile_best_available_card_program_from_ir(
    db: CardDatabase, record: CardRecord, ir: Any, *,
    semantic_registry: SemanticRegistry,
    capability_registry: CapabilityRegistry, capability_profile: str,
) -> CardProgram:
    """Retain trusted-to-provisional fallback without reparsing the record."""
    try:
        return _compile_card_program_from_ir(
            db,
            record,
            ir,
            semantic_registry=semantic_registry,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            trust_level="trusted",
        )
    except ValueError as exc:
        if "cannot be promoted to trusted generated semantics" not in str(exc):
            raise
        return _compile_card_program_from_ir(
            db,
            record,
            ir,
            semantic_registry=semantic_registry,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            trust_level="provisional",
        )


def card_program_from_semantic_programs(
    programs: Iterable[SemanticProgram],
    *,
    card_name: str | None = None,
) -> CardProgram:
    """Adapt one legacy semantic-pack Oracle group into CardProgram V2."""

    # CardProgram is the pinned canonical artifact. Keep its abilities
    # independent from the mutable legacy compatibility index so later
    # in-memory mutation is detectable at the runtime boundary.
    values = tuple(
        SemanticProgram.from_dict(program.to_dict()) for program in programs
    )
    if not values:
        raise CardProgramError("Cannot adapt an empty semantic program group")
    oracle_ids = {program.oracle_id for program in values}
    if len(oracle_ids) != 1 or None in oracle_ids:
        raise CardProgramError(
            "A semantic compatibility group requires one oracle_id"
        )
    oracle_id = str(next(iter(oracle_ids)))
    oracle_hashes = _source_hashes(values, "source_oracle_hash")
    rulings_hashes = _source_hashes(values, "source_rulings_hash")
    if len(oracle_hashes) > 1:
        raise CardProgramSourceMismatch("Oracle", oracle_id, values, card_name)
    if len(rulings_hashes) > 1:
        raise CardProgramSourceMismatch("rulings", oracle_id, values, card_name)
    face_ids = sorted({_program_face_id(program) for program in values})
    oracle_hash = (
        next(iter(oracle_hashes)) if oracle_hashes else _text_hash("")
    )
    rulings_hash = (
        next(iter(rulings_hashes)) if rulings_hashes else _text_hash("")
    )
    faces = tuple(
        CardProgramFace(
            face_id=face_id,
            name=None,
            type_line=None,
            oracle_text_hash=oracle_hash,
        )
        for face_id in face_ids
    )
    abilities = _bind_program_faces(values, faces)
    return CardProgram.create(
        compiler_version=SEMANTIC_PACK_COMPATIBILITY_COMPILER,
        oracle_id=oracle_id,
        card_name=card_name,
        faces=faces,
        oracle_source_hash=oracle_hash,
        rulings_source_hash=rulings_hash,
        abilities=abilities,
        provenance={
            "source": "semantic_pack_v3_compatibility",
            "face_hash_scope": "card_source_fallback",
            "contains_unpinned_abilities": any(
                not program.provenance.get("source_oracle_hash")
                or not program.provenance.get("source_rulings_hash")
                for program in values
            ),
            "authored_by": sorted(
                {
                    str(program.provenance.get("authored_by"))
                    for program in values
                }
            ),
            "review_statuses": sorted(
                {
                    str(program.provenance.get("review_status"))
                    for program in values
                }
            ),
        },
    )


def card_programs_from_semantic_programs(
    programs: Iterable[SemanticProgram],
) -> dict[str, CardProgram]:
    grouped: dict[str, list[SemanticProgram]] = defaultdict(list)
    for program in programs:
        if program.oracle_id:
            grouped[program.oracle_id].append(program)
    return {
        oracle_id: card_program_from_semantic_programs(values)
        for oracle_id, values in sorted(grouped.items())
    }


def program_fingerprints_for_semantic_keys(
    programs: Mapping[str, CardProgram],
    semantic_keys: Iterable[str],
) -> dict[str, str]:
    requested = set(semantic_keys)
    result: dict[str, str] = {}
    for oracle_id, program in sorted(programs.items()):
        if any(ability.key in requested for ability in program.abilities):
            result[oracle_id] = program.fingerprint
    return result

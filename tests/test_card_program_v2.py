from __future__ import annotations

from dataclasses import replace
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import jsonschema

from common import DB_PATH
from quorune.card_programs import CardProgram, CardProgramError
from quorune.card_programs.commands import (
    _compile_best_available,
    audit_card_program,
    card_program_coverage,
    explain_card_program,
)
from quorune.card_programs.adapters import (
    card_program_from_semantic_programs,
    card_programs_from_semantic_programs,
    compile_card_program,
)
from quorune.carddb import CardDatabase, CardRecord
from quorune.cli import main as cli_main
from quorune.oracle_ir import ORACLE_COMPILER_VERSION, register_generated_programs
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.rules.event_subscriptions import FixedEventSubscriptionSet
from quorune.card_programs.validation import (
    canonical_program_fingerprint,
)
from quorune.semantics import SemanticProgram, SemanticRegistry


ROOT = Path(__file__).resolve().parents[1]


def _bolt() -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-00000000b017",
        name="Lightning Bolt",
        mana_cost="{R}",
        mana_value=1.0,
        type_line="Instant",
        oracle_text="Lightning Bolt deals 3 damage to any target.",
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=("R",),
        color_identity=("R",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="1993-08-05",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def _keyword_card(keyword: str, oracle_text: str, suffix: int) -> CardRecord:
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-{suffix:012d}",
        name=f"Generic {keyword.title()} Fixture",
        mana_cost="{1}{B}",
        mana_value=2.0,
        type_line="Creature — Phyrexian Test",
        oracle_text=oracle_text,
        power="2",
        toughness="2",
        loyalty=None,
        defense=None,
        colors=("B",),
        color_identity=("B",),
        keywords=(keyword.title(),),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def _prevention_life_card() -> CardRecord:
    return replace(
        _bolt(),
        oracle_id="00000000-0000-4000-8000-000000061505",
        name="Generic Prevention Life Fixture",
        mana_cost="{W}",
        oracle_text=(
            "Prevent the next 3 damage that would be dealt to any target "
            "this turn by a source of your choice. You gain 3 life."
        ),
        colors=("W",),
        color_identity=("W",),
    )


def _multi_event_artifact() -> CardRecord:
    return replace(
        _bolt(),
        oracle_id="00000000-0000-4000-8000-000000204001",
        name="Generic Multi-Event Artifact",
        mana_cost="{2}",
        mana_value=2.0,
        type_line="Artifact",
        oracle_text=(
            "When this artifact enters or is put into a graveyard from the "
            "battlefield, draw a card."
        ),
        colors=(),
        color_identity=(),
    )


class CardProgramV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(DB_PATH)
        cls.capabilities = load_default_capability_registry()
        cls.schema = json.loads(
            (ROOT / "schemas" / "card-program-v2.schema.json").read_text(
                encoding="utf-8"
            )
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_generated_program_is_deterministic_typed_and_schema_valid(self):
        first = compile_card_program(
            self.db,
            _bolt(),
            capability_registry=self.capabilities,
            capability_profile="commander_duel",
            trust_level="trusted",
        )
        second = compile_card_program(
            self.db,
            _bolt(),
            capability_registry=self.capabilities,
            capability_profile="commander_duel",
            trust_level="trusted",
        )
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(2, first.schema_version)
        self.assertEqual("Lightning Bolt", first.card_name)
        self.assertTrue(first.trust_closure["trusted"])
        self.assertIn(
            "damage.result.player_life", first.capability_dependencies
        )
        ability = first.to_dict()["abilities"][0]
        self.assertEqual("spell", ability["kind"])
        self.assertEqual(["stack"], ability["active_zones"])
        self.assertEqual("resolve", ability["timing_permissions"]["event"])
        self.assertEqual("damage", ability["effect_nodes"][0]["op"])
        self.assertEqual("damageable", ability["targets"]["predicate"])
        self.assertEqual("front", ability["face_id"])
        self.assertTrue(ability["source_span"])
        jsonschema.Draft202012Validator(self.schema).validate(first.to_dict())
        restored = CardProgram.from_dict(first.to_dict())
        self.assertEqual(first.to_dict(), restored.to_dict())

    def test_typed_multi_event_program_supersedes_complete_reviewed_split(self):
        record = _multi_event_artifact()
        generated = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        typed = next(
            ability
            for ability in generated.abilities
            if FixedEventSubscriptionSet.from_condition(
                ability.event_condition
            )
            is not None
        )
        registry = SemanticRegistry(include_builtin_packs=False)
        split = (
            ("reviewed:enter", "permanent.enter.self"),
            ("reviewed:graveyard", "artifact.graveyard.self"),
        )
        for key, event in split:
            registry.put(
                SemanticProgram(
                    key=key,
                    label=key,
                    effects=[dict(effect) for effect in typed.effects],
                    oracle_id=record.oracle_id,
                    ability_id=f"trigger:{key.rpartition(':')[2]}",
                    active_zone="battlefield",
                    event=event,
                    trust_level="trusted",
                    provenance=dict(typed.provenance),
                    tests=[
                        "test_typed_multi_event_program_supersedes_complete_reviewed_split"
                    ],
                )
            )

        current = compile_card_program(
            self.db,
            record,
            semantic_registry=registry,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        keys = {ability.key for ability in current.abilities}
        self.assertIn(typed.key, keys)
        self.assertTrue(keys.isdisjoint(key for key, _event in split))
        self.assertEqual(
            sorted(key for key, _event in split),
            current.provenance["superseded_reviewed_semantic_keys"],
        )
        self.assertEqual(
            "capability_closed",
            current.trust_closure["trust_basis"],
        )

        registry.put(
            SemanticProgram(
                key="reviewed:distinct",
                label="Distinct reviewed trigger",
                effects=[{"op": "draw", "player": "you", "amount": 2}],
                oracle_id=record.oracle_id,
                ability_id="trigger:distinct",
                active_zone="battlefield",
                event="permanent.enter.self",
                trust_level="trusted",
                provenance=dict(typed.provenance),
                tests=[
                    "test_typed_multi_event_program_supersedes_complete_reviewed_split"
                ],
            )
        )
        with_distinct = compile_card_program(
            self.db,
            record,
            semantic_registry=registry,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        self.assertIn(
            "reviewed:distinct",
            {ability.key for ability in with_distinct.abilities},
        )

    def test_strike_keywords_lower_with_precise_capability_closed_spans(self):
        for keyword, suffix in (("first strike", 510401), ("double strike", 510402)):
            with self.subTest(keyword=keyword):
                record = _keyword_card(keyword, keyword.title(), suffix)
                program = compile_card_program(
                    self.db,
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="trusted",
                )

                self.assertEqual(
                    ("combat.damage.participation.strike_steps",),
                    program.capability_dependencies,
                )
                self.assertEqual(
                    "capability_closed",
                    program.trust_closure["trust_basis"],
                )
                self.assertTrue(program.trust_closure["trusted"])
                ability = program.to_dict()["abilities"][0]
                self.assertEqual(
                    {"line": 1, "start": 0, "end": len(keyword)},
                    ability["source_span"],
                )
                self.assertEqual([], program.to_dict()["residuals"])

    def test_defender_keyword_lowers_with_precise_capability_closed_span(self):
        keyword = "defender"
        program = compile_card_program(
            self.db,
            _keyword_card(keyword, "Defender", 702_003),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )

        self.assertEqual(
            ("combat.attack.defender",),
            program.capability_dependencies,
        )
        self.assertEqual(
            "capability_closed",
            program.trust_closure["trust_basis"],
        )
        self.assertTrue(program.trust_closure["trusted"])
        ability = program.to_dict()["abilities"][0]
        self.assertEqual(
            {"line": 1, "start": 0, "end": len(keyword)},
            ability["source_span"],
        )
        self.assertEqual([], program.to_dict()["residuals"])

    def test_menace_keyword_lowers_with_precise_capability_closed_span(self):
        keyword = "menace"
        program = compile_card_program(
            self.db,
            _keyword_card(keyword, "Menace", 702_111),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )

        self.assertEqual(
            ("combat.block.menace",),
            program.capability_dependencies,
        )
        self.assertEqual(
            "capability_closed",
            program.trust_closure["trust_basis"],
        )
        self.assertTrue(program.trust_closure["trusted"])
        ability = program.to_dict()["abilities"][0]
        self.assertEqual(
            {"line": 1, "start": 0, "end": len(keyword)},
            ability["source_span"],
        )
        self.assertEqual([], program.to_dict()["residuals"])

    def test_basic_landwalk_keywords_lower_with_precise_closed_spans(self):
        for suffix, keyword in enumerate(
            (
                "plainswalk",
                "islandwalk",
                "swampwalk",
                "mountainwalk",
                "forestwalk",
            ),
            start=702_014_001,
        ):
            with self.subTest(keyword=keyword):
                program = compile_card_program(
                    self.db,
                    _keyword_card(keyword, keyword.title(), suffix),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="trusted",
                )

                self.assertEqual(
                    ("combat.block.landwalk.basic_type",),
                    program.capability_dependencies,
                )
                self.assertEqual(
                    "capability_closed",
                    program.trust_closure["trust_basis"],
                )
                self.assertTrue(program.trust_closure["trusted"])
                ability = program.to_dict()["abilities"][0]
                self.assertEqual(
                    {"line": 1, "start": 0, "end": len(keyword)},
                    ability["source_span"],
                )
                self.assertEqual([], program.to_dict()["residuals"])

    def test_ordinary_evasion_keywords_lower_with_precise_closed_spans(self):
        for suffix, (keyword, capability) in enumerate(
            (
                ("fear", "combat.block.fear"),
                ("horsemanship", "combat.block.horsemanship"),
                ("intimidate", "combat.block.intimidate"),
                ("shadow", "combat.block.shadow"),
                ("skulk", "combat.block.skulk"),
            ),
            start=702_118_101,
        ):
            with self.subTest(keyword=keyword):
                program = compile_card_program(
                    self.db,
                    _keyword_card(keyword, keyword.title(), suffix),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="trusted",
                )

                self.assertEqual(
                    (capability,),
                    program.capability_dependencies,
                )
                self.assertEqual(
                    "capability_closed",
                    program.trust_closure["trust_basis"],
                )
                self.assertTrue(program.trust_closure["trusted"])
                ability = program.to_dict()["abilities"][0]
                self.assertEqual(
                    {"line": 1, "start": 0, "end": len(keyword)},
                    ability["source_span"],
                )
                self.assertEqual([], program.to_dict()["residuals"])

    def test_damage_aftermath_card_program_is_capability_closed(self):
        current = compile_card_program(
            self.db,
            self.db.lookup("Deflecting Palm"),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )

        self.assertEqual(ORACLE_COMPILER_VERSION, current.compiler_version)
        self.assertEqual(
            "capability_closed", current.trust_closure["trust_basis"]
        )
        self.assertIn(
            "damage.prevention.aftermath.damage",
            current.capability_dependencies,
        )
        choice = current.abilities[0].effects[0]
        self.assertEqual("choose_damage_source", choice["op"])
        self.assertEqual(
            "deal_damage", choice["shield"]["aftermath"][0]["kind"]
        )

    def test_two_face_semantic_aliases_bind_to_exact_oracle_faces(self):
        program = _compile_best_available(
            self.db,
            self.db.lookup("Tithing Blade"),
            registry=SemanticRegistry(),
            profile="commander_review",
            capabilities=self.capabilities,
        )
        by_id = {
            ability.ability_id: ability
            for ability in program.abilities
        }

        self.assertEqual(
            "Tithing Blade",
            by_id["spell:front"].provenance["face_id"],
        )
        self.assertEqual(
            "Tithing Blade",
            by_id["trigger:front-enter"].provenance["face_id"],
        )
        self.assertEqual(
            "Consuming Sepulcher",
            by_id["trigger:back-upkeep"].provenance["face_id"],
        )
        self.assertEqual(
            {
                "Tithing Blade",
                "Consuming Sepulcher",
            },
            {face.face_id for face in program.faces},
        )

    def test_corrected_prevention_sequence_has_new_pinned_fingerprint(self):
        current = compile_card_program(
            self.db,
            _prevention_life_card(),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        self.assertEqual(ORACLE_COMPILER_VERSION, current.compiler_version)
        self.assertEqual("capability_closed", current.trust_closure["trust_basis"])
        self.assertEqual(
            ["choose_damage_source", "life"],
            [effect["op"] for effect in current.abilities[0].effects],
        )

        current_choice = dict(current.abilities[0].effects[0])
        historical_shield = dict(current_choice["shield"])
        historical_shield["aftermath"] = [
            {
                "kind": "gain_life",
                "player": "$controller",
                "per_prevented": 0,
                "fixed_amount": 3,
            }
        ]
        current_choice["shield"] = historical_shield
        historical_ability = replace(
            current.abilities[0],
            effects=[current_choice],
        )
        historical = CardProgram.create(
            compiler_version="oracle-ir-v17",
            oracle_id=current.oracle_id,
            card_name=current.card_name,
            faces=current.faces,
            oracle_source_hash=current.oracle_source_hash,
            rulings_source_hash=current.rulings_source_hash,
            abilities=(historical_ability,),
            residuals=current.residuals,
            provenance=current.provenance,
        )

        self.assertNotEqual(current.fingerprint, historical.fingerprint)
        restored = CardProgram.from_dict(historical.to_dict())
        self.assertEqual(historical.to_dict(), restored.to_dict())

    def test_keyword_programs_declare_exact_damage_result_capabilities(self):
        for index, (keyword, oracle_text, expected) in enumerate(
            (
                ("infect", "Infect", ("damage.result.infect",)),
                ("wither", "Wither", ("damage.result.wither",)),
                ("lifelink", "Lifelink", ("damage.result.lifelink",)),
                ("toxic", "Toxic 2", ("damage.result.toxic",)),
                (
                    "deathtouch",
                    "Deathtouch",
                    (
                        "combat.damage.assignment.deathtouch",
                        "damage.result.deathtouch",
                    ),
                ),
            ),
            101,
        ):
            with self.subTest(keyword=keyword):
                program = compile_card_program(
                    self.db,
                    _keyword_card(keyword, oracle_text, index),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="trusted",
                )
                self.assertEqual(
                    expected,
                    program.capability_dependencies,
                )
                self.assertEqual("capability_closed", program.trust_closure["trust_basis"])
                self.assertTrue(program.trust_closure["trusted"])
                self.assertEqual(1, len(program.abilities))
                ability = program.to_dict()["abilities"][0]
                self.assertEqual("static", ability["kind"])
                self.assertEqual([], ability["effect_nodes"])

    def test_generic_prevention_programs_declare_closed_capabilities(self):
        fixtures = (
            (
                replace(
                    _bolt(),
                    oracle_id="00000000-0000-4000-8000-000000006151",
                    name="Generic X Prevention",
                    mana_cost="{X}{W}",
                    colors=("W",),
                    color_identity=("W",),
                    oracle_text=(
                        "Prevent the next X damage that would be dealt to any "
                        "target this turn."
                    ),
                ),
                {"damage.prevention.persistent_amount"},
                "$x",
            ),
            (
                replace(
                    _bolt(),
                    oracle_id="00000000-0000-4000-8000-000000006152",
                    name="Generic Counter Prevention",
                    mana_cost="{1}{W}",
                    colors=("W",),
                    color_identity=("W",),
                    oracle_text=(
                        "Prevent the next 3 damage that would be dealt to target "
                        "creature this turn. For each 1 damage prevented this way, "
                        "put a +1/+1 counter on that creature."
                    ),
                ),
                {
                    "counter.placement.quantity_replacement",
                    "damage.prevention.persistent_amount",
                },
                3,
            ),
        )
        for record, expected_dependencies, expected_amount in fixtures:
            with self.subTest(record.name):
                program = compile_card_program(
                    self.db,
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="trusted",
                )
                self.assertTrue(program.trust_closure["trusted"])
                self.assertTrue(
                    expected_dependencies.issubset(
                        program.capability_dependencies
                    )
                )
                effect = program.to_dict()["abilities"][0]["effect_nodes"][0]
                self.assertEqual(expected_amount, effect["amount"])

    def test_prevention_triggers_use_narrow_apnap_capability_closure(self):
        record = replace(
            _bolt(),
            oracle_id="00000000-0000-4000-8000-000000006153",
            name="Fixture Prevention Guardian",
            type_line="Creature — Human Soldier",
            mana_cost="{3}{W}",
            colors=("W",),
            color_identity=("W",),
            oracle_text=(
                "When Fixture Prevention Guardian enters, prevent all damage "
                "that would be dealt to you this turn.\n"
                "Whenever damage that would be dealt to you is prevented, put "
                "that many +1/+1 counters on Fixture Prevention Guardian."
            ),
        )

        program = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )

        self.assertEqual(
            {
                "counter.placement.quantity_replacement",
                "damage.prevention.persistent_amount",
                "damage.prevention.triggered_results",
                "trigger.event.normalized_zone_change",
                "trigger.placement.apnap",
            },
            set(program.capability_dependencies),
        )
        self.assertEqual(
            "capability_closed",
            program.trust_closure["trust_basis"],
        )
        self.assertTrue(program.trust_closure["trusted"])
        triggered = next(
            ability
            for ability in program.to_dict()["abilities"]
            if ability["timing_permissions"]["event"]
            == "damage.prevented"
        )
        self.assertEqual(
            ["$source.controller"],
            triggered["triggers"][0]["condition"]["value"],
        )

    def test_life_multiplier_no_longer_requires_a_card_specific_program(self):
        record = self.db.lookup("Boon Reflection")
        registry = SemanticRegistry()
        self.assertEqual([], registry.programs_for_oracle(record.oracle_id))

        program = compile_card_program(
            self.db,
            record,
            semantic_registry=registry,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )

        self.assertEqual(
            ("life.gain.replacement.static_multiplier",),
            program.capability_dependencies,
        )
        self.assertEqual(
            "capability_closed", program.trust_closure["trust_basis"]
        )
        self.assertTrue(program.trust_closure["trusted"])
        self.assertEqual([], program.trust_closure["compatibility_provenance"])

    def test_tampered_projection_hash_and_closure_fail_closed(self):
        program = compile_card_program(
            self.db,
            _bolt(),
            capability_registry=self.capabilities,
            capability_profile="commander_duel",
            trust_level="trusted",
        )
        changed = program.to_dict()
        changed["abilities"][0]["kind"] = "activated"
        with self.assertRaisesRegex(CardProgramError, "does not match"):
            CardProgram.from_dict(changed)

        changed = program.to_dict()
        changed["fingerprint"] = "0" * 64
        with self.assertRaisesRegex(CardProgramError, "fingerprint"):
            CardProgram.from_dict(changed)

        changed = program.to_dict()
        changed["trust_closure"]["trusted"] = False
        with self.assertRaisesRegex(CardProgramError, "trust_closure"):
            CardProgram.from_dict(changed)

    def test_unparsed_material_text_is_preserved_as_residual(self):
        changed = replace(
            _bolt(),
            oracle_text=(
                "Lightning Bolt deals 3 damage to any target. "
                "Then copy this spell."
            ),
        )
        program = compile_card_program(
            self.db,
            changed,
            capability_registry=self.capabilities,
            capability_profile="commander_duel",
        )
        self.assertTrue(program.residuals)
        self.assertFalse(program.trust_closure["trusted"])
        self.assertTrue(
            any(
                value.startswith("residual:")
                for value in program.trust_closure["blockers"]
            )
        )

    def test_every_builtin_semantic_pack_adapts_to_one_canonical_group(self):
        registry = SemanticRegistry()
        values = registry.programs()
        adapted = card_programs_from_semantic_programs(reversed(values))
        expected_oracle_ids = {
            program.oracle_id for program in values if program.oracle_id
        }
        self.assertEqual(expected_oracle_ids, set(adapted))
        self.assertEqual(
            len(values), sum(len(program.abilities) for program in adapted.values())
        )
        for oracle_id, program in adapted.items():
            with self.subTest(oracle_id):
                jsonschema.Draft202012Validator(self.schema).validate(
                    program.to_dict()
                )
                self.assertEqual(
                    program.to_dict(),
                    CardProgram.from_dict(program.to_dict()).to_dict(),
                )

    def test_point_lookup_isolated_from_unrelated_invalid_group(self):
        registry = SemanticRegistry(include_builtin_packs=False)
        invalid_oracle = "00000000-0000-4000-8000-000000000001"
        valid_oracle = "00000000-0000-4000-8000-000000000002"
        for key in ("test:duplicate-one", "test:duplicate-two"):
            registry.put(
                SemanticProgram(
                    key=key,
                    label=key,
                    oracle_id=invalid_oracle,
                    ability_id="spell:front",
                )
            )
        registry.put(
            SemanticProgram(
                key="test:valid",
                label="Valid",
                oracle_id=valid_oracle,
                ability_id="static:front:n1",
            )
        )

        self.assertEqual(
            valid_oracle,
            registry.card_program_for_oracle(valid_oracle).oracle_id,
        )
        with self.assertRaisesRegex(CardProgramError, "ability IDs"):
            registry.card_program_for_oracle(invalid_oracle)
        with self.assertRaisesRegex(CardProgramError, "ability IDs"):
            registry.card_programs()

    def test_registry_snapshot_roundtrips_canonical_and_legacy_views(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "semantics.json"
            registry = SemanticRegistry(path)
            registry.save()
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(2, raw["card_program_schema_version"])
            self.assertFalse(raw["runtime_handler_compatibility_enabled"])
            self.assertEqual(
                registry.card_program_fingerprints(),
                {
                    oracle_id: value["fingerprint"]
                    for oracle_id, value in raw["card_programs"].items()
                },
            )
            restored = SemanticRegistry(path)
            self.assertFalse(restored.runtime_handler_compatibility_enabled)
            self.assertEqual(
                registry.card_program_fingerprints(),
                restored.card_program_fingerprints(),
            )
            self.assertEqual(
                [program.to_dict() for program in registry.programs()],
                [program.to_dict() for program in restored.programs()],
            )

            program = restored.programs()[0]
            self.assertIsNotNone(
                canonical_program_fingerprint(restored, program)
            )
            program.label += " mutated after pin"
            self.assertIsNone(
                canonical_program_fingerprint(restored, program)
            )

            key = next(iter(raw["programs"]))
            raw["programs"][key]["label"] += " tampered"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "views disagree"):
                SemanticRegistry(path)

    def test_legacy_registry_without_compatibility_flag_retains_v3_adapter(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "semantics.json"
            registry = SemanticRegistry(path, include_builtin_packs=False)
            registry.save()
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw.pop("runtime_handler_compatibility_enabled")
            path.write_text(json.dumps(raw), encoding="utf-8")

            restored = SemanticRegistry(path)

        self.assertTrue(restored.runtime_handler_compatibility_enabled)

    def test_semantic_adapter_rejects_cross_card_or_source_hash_mix(self):
        values = SemanticRegistry().programs()
        first = values[0]
        second = next(
            program for program in values if program.oracle_id != first.oracle_id
        )
        with self.assertRaisesRegex(CardProgramError, "one oracle_id"):
            card_program_from_semantic_programs([first, second])

        grouped = card_programs_from_semantic_programs(values)
        multi = next(
            program for program in grouped.values() if len(program.abilities) > 1
        )
        same_card = list(multi.abilities)
        stale = replace(
            same_card[0],
            provenance={
                **same_card[0].provenance,
                "source_oracle_hash": "0" * 64,
            },
        )
        with self.assertRaisesRegex(CardProgramError, "Oracle hashes"):
            card_program_from_semantic_programs([stale, *same_card[1:]])

    def test_current_card_program_blocks_stale_reviewed_source(self):
        generated = compile_card_program(
            self.db,
            _bolt(),
            capability_registry=self.capabilities,
            capability_profile="commander_duel",
            trust_level="trusted",
        )
        reviewed = replace(
            generated.abilities[0],
            provenance={
                **generated.abilities[0].provenance,
                "source_oracle_hash": "0" * 64,
            },
        )
        registry = SemanticRegistry(include_builtin_packs=False)
        registry.put(reviewed)
        current = compile_card_program(
            self.db,
            _bolt(),
            semantic_registry=registry,
            capability_registry=self.capabilities,
            capability_profile="commander_duel",
            trust_level="trusted",
        )
        self.assertFalse(current.trust_closure["trusted"])
        self.assertTrue(
            any(
                blocker.endswith("stale_oracle_source")
                for blocker in current.trust_closure["blockers"]
            )
        )

    def test_card_cli_compile_explain_audit_diff_overrides_and_coverage(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "mishra.card-program.json"
            invocations = (
                [
                    "card",
                    "compile",
                    "Mishra, Eminent One",
                    "--db",
                    str(DB_PATH),
                    "--output",
                    str(snapshot),
                ],
                [
                    "card",
                    "explain",
                    "Mishra, Eminent One",
                    "--db",
                    str(DB_PATH),
                ],
                [
                    "card",
                    "audit",
                    "Mishra, Eminent One",
                    "--db",
                    str(DB_PATH),
                ],
                [
                    "card",
                    "diff",
                    "Mishra, Eminent One",
                    "--db",
                    str(DB_PATH),
                    "--against",
                    str(snapshot),
                ],
                ["card", "overrides", "--db", str(DB_PATH)],
                [
                    "card",
                    "coverage",
                    "--db",
                    str(DB_PATH),
                    "--limit",
                    "2",
                ],
            )
            results = []
            for args in invocations:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(0, cli_main(args))
                results.append(json.loads(output.getvalue()))
            self.assertEqual(2, results[0]["schema_version"])
            self.assertTrue(results[1]["abilities"])
            self.assertTrue(results[2]["deterministic_roundtrip"])
            self.assertFalse(results[3]["changed"])
            self.assertIn("overrides", results[4])
            self.assertEqual(2, results[5]["cards_considered"])

    def test_explain_and_audit_identify_registered_typed_handlers(self):
        program = next(
            card_program
            for card_program in SemanticRegistry().card_programs()
            if any(
                effect.get("op") == "draw"
                for ability in card_program.abilities
                for effect in ability.effects
            )
        )
        explained = explain_card_program(program)
        mappings = [
            handler
            for ability in explained["abilities"]
            for handler in ability["runtime_handler_mapping"][
                "typed_handlers"
            ]
        ]
        self.assertTrue(
            any(handler["handler_id"] == "generic.draw.v1" for handler in mappings)
        )
        audited = audit_card_program(program)
        audit_mappings = [
            handler
            for ability in audited["runtime_handler_mapping"].values()
            for handler in ability["typed_handlers"]
        ]
        self.assertTrue(
            any(
                handler["capability_dependencies"]
                == ["zone.draw.library_to_hand"]
                for handler in audit_mappings
            )
        )

        runtime_registry = SemanticRegistry()
        register_generated_programs(
            self.db,
            runtime_registry,
            (self.db.lookup("Boon Reflection"),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        runtime_programs = [
            card_program
            for card_program in runtime_registry.card_programs()
            if any(ability.handlers for ability in card_program.abilities)
        ]
        event_handlers = [
            handler
            for runtime_program in runtime_programs
            for ability in explain_card_program(runtime_program)["abilities"]
            for handler in ability["runtime_handler_mapping"][
                "event_handlers"
            ]
        ]
        self.assertEqual(
            {
                "ability.enchant.linked_graveyard_creature.v1": [],
                "continuous.anthem.power_toughness.v1": [
                    "continuous.power_toughness.fixed_anthem"
                ],
                "continuous.attached.fixed-characteristics.v1": [
                    "continuous.attached.fixed_characteristics"
                ],
                "continuous.ability.fixed-query-grant.v1": [
                    "continuous.ability.fixed_query_grant"
                ],
                "prevention.damage.fixed.v1": [
                    "damage.prevention.static_fixed"
                ],
                "replacement.counter.quantity.v2": [
                    "counter.placement.quantity_replacement"
                ],
                "replacement.damage.quantity.v1": [
                    "damage.replacement.static_quantity"
                ],
                "replacement.damage.result.life_floor.v1": [
                    "damage.result.replacement_order"
                ],
                "replacement.life.gain.multiplier.v1": [
                    "life.gain.replacement.static_multiplier"
                ],
                "replacement.token.additional.v2": [
                    "token.creation.additional_replacement"
                ],
                "replacement.zone.destination.v1": [
                    "zone.change.destination_replacement"
                ],
            },
            {
                handler["handler_id"]: handler["registry"][
                    "capability_dependencies"
                ]
                for handler in event_handlers
            },
        )
        self.assertTrue(
            all(
                any(
                    mapping["event_handlers"]
                    for mapping in audit_card_program(runtime_program)[
                        "runtime_handler_mapping"
                    ].values()
                )
                for runtime_program in runtime_programs
            )
        )

    def test_cli_does_not_downgrade_unexpected_compiler_errors(self):
        with patch(
            "quorune.card_programs.adapters.compile_card_program",
            side_effect=ValueError("broken CardProgram structure"),
        ) as compile_program:
            with self.assertRaisesRegex(ValueError, "broken CardProgram"):
                _compile_best_available(
                    self.db,
                    self.db.lookup("Mishra, Eminent One"),
                    registry=SemanticRegistry(),
                    profile="traditional",
                )
        compile_program.assert_called_once()

    def test_coverage_reuses_one_validated_capability_registry(self):
        program = compile_card_program(
            self.db,
            _bolt(),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )

        class TwoCardCorpus:
            def iter_cards(self, **_kwargs):
                return iter((_bolt(), _bolt()))

            def metadata(self):
                return {
                    "schema_version": "2",
                    "oracle_source_sha256": "a" * 64,
                }

        with (
            patch(
                "quorune.card_programs.commands."
                "load_default_capability_registry",
                return_value=self.capabilities,
            ) as load_capabilities,
            patch(
                "quorune.card_programs.commands."
                "_compile_best_available",
                return_value=program,
            ) as compile_best,
        ):
            result = card_program_coverage(
                TwoCardCorpus(),  # type: ignore[arg-type]
                registry=SemanticRegistry(),
                profile="commander_review",
                commander_legal_only=True,
                limit=None,
            )

        self.assertEqual(2, result["cards_considered"])
        self.assertEqual(ORACLE_COMPILER_VERSION, result["compiler_version"])
        self.assertEqual(
            self.capabilities.fingerprint,
            result["capability_registry_fingerprint"],
        )
        load_capabilities.assert_called_once_with()
        self.assertEqual(2, compile_best.call_count)
        self.assertTrue(
            all(
                call.kwargs["capabilities"] is self.capabilities
                for call in compile_best.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()

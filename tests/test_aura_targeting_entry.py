from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from common import (
    ROOT,
    advance_fixture_turn,
    keep_all,
    load_assets,
    make_session,
    set_fixture_turn,
)
from quorune.aura import (
    AuraControllerRelation,
    AuraEnchantSubject,
    AuraEntryChoiceRequired,
    AuraEntryContinuation,
    SimpleEnchantSpec,
    TypedEnchantSpec,
    enchant_spec_from_dict,
    enchant_spec_to_dict,
    legal_aura_target_refs,
    parse_enchant_line,
    parse_simple_enchant_line,
    simple_enchant_spec_from_oracle,
)
from quorune.ability_fragments import (
    ProtectionQualityKind,
    ProtectionSpec,
    ability_fragment_from_dict,
    ability_fragment_to_dict,
)
from quorune.target_forms import TargetCharacteristicForm
from quorune.targets import TargetGroup
from quorune.card_programs import compile_card_program
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.program_generation import (
    register_generated_programs,
)
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.semantics import SemanticProgram
from quorune.errors import GameRuleError
from quorune.model import StackItem
from scripts.build_test_database import build_fixture_database


def aura_record(oracle_id: str, *, restriction: str = "creature") -> CardRecord:
    return CardRecord(
        oracle_id=oracle_id,
        name="Exact Test Aura",
        mana_cost="{U}",
        mana_value=1,
        type_line="Enchantment — Aura",
        oracle_text=(
            f"Enchant {restriction}\n"
            "Enchanted creature gets +1/+1."
        ),
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=("U",),
        color_identity=("U",),
        keywords=("Enchant",),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class SimpleEnchantModelTests(unittest.TestCase):
    def test_model_is_canonical_immutable_and_fingerprinted(self):
        spec = SimpleEnchantSpec(
            " Creature ", AuraControllerRelation.YOU
        )
        self.assertEqual("creature", spec.object_kind)
        self.assertEqual(
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "controller": "you",
                "count": 1,
                "source_exclusion": True,
                "types_all": ["creature"],
            },
            spec.target_schema(),
        )
        restored = SimpleEnchantSpec.from_dict(spec.to_dict())
        self.assertEqual(spec, restored)
        self.assertEqual(spec.fingerprint, restored.fingerprint)
        with self.assertRaisesRegex(ValueError, "unknown"):
            SimpleEnchantSpec.from_dict(
                {**spec.to_dict(), "unexpected": True}
            )
        for malformed in (1, True, {}, None):
            with self.subTest(malformed=malformed):
                with self.assertRaisesRegex(ValueError, "object kind"):
                    SimpleEnchantSpec(malformed)  # type: ignore[arg-type]
        for field, malformed in (
            ("schema_version", True),
            ("object_kind", 1),
            ("controller_relation", False),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    SimpleEnchantSpec.from_dict(
                        {**spec.to_dict(), field: malformed}
                    )

    def test_closed_grammar_accepts_only_reviewed_object_and_relation_shapes(self):
        accepted = {
            "Enchant creature": ("creature", "any"),
            "Enchant land you control": ("land", "you"),
            "Enchant artifact an opponent controls.": (
                "artifact",
                "opponent",
            ),
            "Enchant nonland permanent": (
                "nonland permanent",
                "any",
            ),
            "Enchant artifact or creature": (
                "artifact or creature",
                "any",
            ),
            "Enchant red or green creature": (
                "red or green creature",
                "any",
            ),
            "Enchant tapped creature": ("tapped creature", "any"),
            (
                "Enchant creature (Target a creature as you cast this. "
                "This card enters attached to that creature.)"
            ): ("creature", "any"),
        }
        for line, expected in accepted.items():
            with self.subTest(line=line):
                spec = parse_simple_enchant_line(line)
                self.assertIsNotNone(spec)
                assert spec is not None
                self.assertEqual(expected[0], spec.object_kind)
                self.assertEqual(
                    expected[1], spec.controller_relation.value
                )
        for line in (
            "Enchant player",
            "Enchant creature card in a graveyard",
            "Enchant creature with flying",
            "Enchant creature or Vehicle",
            "Enchant basic land you control",
            "Enchant creature you own",
        ):
            with self.subTest(line=line):
                self.assertIsNone(parse_simple_enchant_line(line))
        self.assertIsNone(
            simple_enchant_spec_from_oracle(
                "Enchant creature\nEnchant land"
            )
        )

        self.assertEqual(
            ["artifact", "creature"],
            SimpleEnchantSpec("artifact or creature").target_schema()[
                "types_any"
            ],
        )
        self.assertEqual(
            ["R", "G"],
            SimpleEnchantSpec("red or green creature").target_schema()[
                "colors_any"
            ],
        )
        self.assertTrue(
            SimpleEnchantSpec("tapped creature").target_schema()["tapped"]
        )

    def test_typed_enchant_grammar_and_fragments_are_closed(self):
        expected = {
            "Enchant player": ("player", "any", "player_relation"),
            "Enchant opponent": ("player", "opponent", "player_relation"),
            "Enchant creature or Vehicle": (
                "permanent",
                "any",
                "characteristic_forms_any",
            ),
            "Enchant creature, planeswalker, or Clue": (
                "permanent",
                "any",
                "characteristic_forms_any",
            ),
            "Enchant basic land you control": (
                "permanent",
                "you",
                "supertypes_any",
            ),
            "Enchant non-Wall creature": (
                "permanent",
                "any",
                "subtypes_none",
            ),
            "Enchant nonblack creature": (
                "permanent",
                "any",
                "colors_none",
            ),
            "Enchant noncommander creature": (
                "permanent",
                "any",
                "commander",
            ),
            "Enchant creature card in a graveyard": (
                "graveyard_card",
                "any",
                "types_all",
            ),
        }
        for line, (subject, relation, field) in expected.items():
            with self.subTest(line=line):
                spec = parse_enchant_line(line)
                self.assertIsInstance(spec, TypedEnchantSpec)
                assert isinstance(spec, TypedEnchantSpec)
                self.assertEqual(subject, spec.subject.value)
                self.assertEqual(
                    relation,
                    (
                        spec.player_relation.value
                        if spec.subject is AuraEnchantSubject.PLAYER
                        else spec.controller_relation.value
                    ),
                )
                self.assertIn(field, spec.target_schema())
                serialized = enchant_spec_to_dict(spec)
                self.assertEqual(spec, enchant_spec_from_dict(serialized))
                fragment = ability_fragment_to_dict(spec)
                self.assertEqual(spec, ability_fragment_from_dict(fragment))

        group = TargetGroup.from_mapping(
            parse_enchant_line("Enchant creature or Vehicle").target_schema()
        )
        self.assertTrue(
            group.matches_type_characteristics(
                types={"artifact"},
                subtypes={"vehicle"},
                supertypes=set(),
            )
        )
        self.assertFalse(
            group.matches_type_characteristics(
                types={"artifact"},
                subtypes={"food"},
                supertypes=set(),
            )
        )

    def test_dynamic_and_malformed_typed_enchant_forms_fail_closed(self):
        for line in (
            "Enchant creature without flying or vigilance",
            "Enchant creature with another Aura attached to it",
            "Enchant nonbasic land",
            "Enchant modified creature",
            "Enchant creature with power X or less",
            "Enchant creature with mana value less than this creature's power",
        ):
            with self.subTest(line=line):
                self.assertIsNone(parse_enchant_line(line))
        spec = TypedEnchantSpec(
            subject=AuraEnchantSubject.PERMANENT,
            characteristic_forms_any=(
                TargetCharacteristicForm(types_all=("creature",)),
                TargetCharacteristicForm(subtypes_any=("vehicle",)),
            ),
        )
        for malformed in (
            {**spec.to_dict(), "unexpected": True},
            {**spec.to_dict(), "subject": "stack"},
            {**spec.to_dict(), "types_all": "land"},
            {
                **spec.to_dict(),
                "characteristic_forms_any": [
                    {
                        "types_all": [],
                        "subtypes_any": [],
                        "supertypes_any": [],
                    }
                ],
            },
        ):
            with self.subTest(malformed=malformed):
                with self.assertRaises(ValueError):
                    TypedEnchantSpec.from_dict(malformed)

    def test_typed_enchant_target_form_mutant_is_killed(self):
        group = TargetGroup.from_mapping(
            parse_enchant_line("Enchant creature or Vehicle").target_schema()
        )

        def assert_vehicle_matches() -> None:
            self.assertTrue(
                group.matches_type_characteristics(
                    types={"artifact"},
                    subtypes={"vehicle"},
                    supertypes=set(),
                )
            )

        assert_vehicle_matches()
        with patch.object(
            TargetCharacteristicForm,
            "matches",
            return_value=False,
        ):
            with self.assertRaises(AssertionError):
                assert_vehicle_matches()

    def test_typed_enchant_compiler_closes_representative_family(self):
        registry = load_default_capability_registry()
        for restriction in (
            "player",
            "opponent",
            "creature or Vehicle",
            "creature, planeswalker, or Clue",
            "basic land you control",
            "non-Wall creature",
            "creature card in a graveyard",
        ):
            with self.subTest(restriction=restriction):
                ir = compile_oracle_card(
                    aura_record(
                        f"fixture:typed-aura:{restriction}",
                        restriction=restriction,
                    ),
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                enchant = ir.faces[0].nodes[0]
                self.assertTrue(enchant.exact)
                self.assertEqual(
                    ("attachment.aura.typed_restriction",),
                    enchant.capability_dependencies,
                )
                self.assertEqual(
                    "ability.static.enchant.typed.v2",
                    enchant.handlers[0]["handler_id"],
                )

    def test_continuation_rejects_malformed_entries(self):
        effect = {"op": "move", "nested": {"values": [1]}}
        value = {
            "schema_version": 1,
            "stack_ref": "S1",
            "source_object_id": "O1",
            "source_logical_object_id": "O1@0",
            "controller": "A",
            "effect": effect,
            "remaining": [],
            "destination": None,
            "note": "",
            "instruction_pointer": 0,
            "semantic_frame": {},
            "spec": SimpleEnchantSpec("creature").to_dict(),
            "advertised_targets": ["C1"],
        }
        restored = AuraEntryContinuation.from_dict(value)
        self.assertEqual(("C1",), restored.advertised_targets)
        effect["nested"]["values"].append(2)
        self.assertEqual(
            (1,), restored.effect["nested"]["values"]
        )
        with self.assertRaisesRegex(ValueError, "remaining"):
            AuraEntryContinuation.from_dict(
                {**value, "remaining": ["not-an-object"]}
            )
        with self.assertRaisesRegex(ValueError, "missing or unknown"):
            AuraEntryContinuation.from_dict(
                {**value, "unknown": True}
            )
        for field, malformed in (
            ("schema_version", True),
            ("destination", 1),
            ("note", {}),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    AuraEntryContinuation.from_dict(
                        {**value, field: malformed}
                    )

    def test_compiler_lowers_only_closed_enchant_grammar_to_trusted_capability(self):
        registry = load_default_capability_registry()
        exact = compile_oracle_card(
            aura_record("fixture:simple-aura"),
            capability_registry=registry,
            capability_profile="commander_review",
        )
        keyword = exact.faces[0].nodes[0]
        self.assertTrue(keyword.exact)
        self.assertEqual(
            ("attachment.aura.simple_object",),
            keyword.capability_dependencies,
        )
        self.assertEqual(
            ["creature"], keyword.target_schema["types_all"]
        )
        self.assertFalse(exact.faces[0].residuals)

        for restriction, field, expected in (
            ("artifact or creature", "types_any", ["artifact", "creature"]),
            ("red or green creature", "colors_any", ["R", "G"]),
            ("tapped creature", "tapped", True),
        ):
            with self.subTest(restriction=restriction):
                qualified = compile_oracle_card(
                    aura_record(
                        f"fixture:qualified-aura:{restriction}",
                        restriction=restriction,
                    ),
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                qualified_keyword = qualified.faces[0].nodes[0]
                self.assertTrue(qualified_keyword.exact)
                self.assertEqual(
                    ("attachment.aura.simple_object",),
                    qualified_keyword.capability_dependencies,
                )
                self.assertEqual(expected, qualified_keyword.target_schema[field])
                self.assertFalse(qualified.faces[0].residuals)

        for restriction in (
            "creature without flying",
            "creature with power 3 or less",
            "creature with another Aura attached to it",
        ):
            with self.subTest(unsupported=restriction):
                unsupported = compile_oracle_card(
                    aura_record(
                        f"fixture:unsupported-aura:{restriction}",
                        restriction=restriction,
                    ),
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                unsupported_keyword = unsupported.faces[0].nodes[0]
                self.assertFalse(unsupported_keyword.exact)
                self.assertEqual(
                    ["unsupported_enchant_restriction"],
                    [value.kind for value in unsupported.faces[0].residuals],
                )


class QualifiedEnchantCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "qualified-enchant.sqlite3"
        build_fixture_database(
            ROOT / "tests" / "fixtures" / "qualified-enchant-cards.json",
            path,
        )
        cls.db = CardDatabase(path)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_real_qualified_enchant_family_is_capability_closed(self):
        expected = {
            "Coma Veil": ("types_any", ["artifact", "creature"]),
            "Controlled Instincts": ("colors_any", ["R", "G"]),
            "Encase in Ice": ("colors_any", ["R", "G"]),
            "Entangling Vines": ("tapped", True),
            "Glimmerdust Nap": ("tapped", True),
            "Ice Over": ("types_any", ["artifact", "creature"]),
            "Malfunction": ("types_any", ["artifact", "creature"]),
        }
        for name, (field, value) in expected.items():
            with self.subTest(card=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
                enchant = next(
                    node
                    for node in ir.faces[0].nodes
                    if "attachment.aura.simple_object"
                    in node.capability_dependencies
                )
                self.assertEqual(value, enchant.target_schema[field])
                program = compile_card_program(
                    self.db,
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="trusted",
                )
                self.assertEqual((), program.residuals)
                self.assertEqual(
                    "capability_closed",
                    program.trust_closure["trust_basis"],
                )


class AuraTargetingEntryEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int = 3034):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.stack.clear()
        set_fixture_turn(engine, 3)
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        return session

    @staticmethod
    def card(engine, seat: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    def fixture(
        self,
        *,
        restriction: str = "creature",
        oracle_text: str | None = None,
    ):
        session = self.make_session()
        engine = session.engine
        aura = self.card(engine, "A", "Island")
        target = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B"
            and not card.is_commander
            and engine.card_record(card) is not None
            and "creature"
            in engine.card_record(card).type_line.casefold()
        )
        engine.move_card(aura.object_id, "hand", log=False)
        engine.move_card(
            target.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        record = aura_record(aura.oracle_id, restriction=restriction)
        if oracle_text is not None:
            record = replace(record, oracle_text=oracle_text)
        original = engine.card_record

        def record_for(value):
            instance = (
                value
                if hasattr(value, "object_id")
                else engine.state.cards[value]
            )
            return (
                record
                if instance.object_id == aura.object_id
                else original(value)
            )

        @contextmanager
        def compiled_record():
            engine_type = type(engine)
            original_trust = engine_type.semantic_program_is_current_trusted

            def trust_fixture_program(host, program):
                return bool(
                    program is not None
                    and program.oracle_id == record.oracle_id
                    and program.trust_level == "trusted"
                ) or original_trust(host, program)

            with patch.object(
                engine, "card_record", side_effect=record_for
            ), patch.object(
                engine_type,
                "semantic_program_is_current_trusted",
                new=trust_fixture_program,
            ):
                register_generated_programs(
                    engine.card_db,
                    engine.semantics,
                    (record,),
                    capability_registry=load_default_capability_registry(),
                    capability_profile=engine.state.config.review_profile,
                    promote_exact_runtime_handlers=True,
                    promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True,
                    promote_exact_capability_declarations=True,
                )
                yield

        return session, aura, target, compiled_record()

    def add_fixture_aura(self, engine, seat: str, zone: str):
        record = aura_record("fixture:simple-aura")
        card = CardInstance(
            object_id=uuid.uuid4().hex,
            ref=f"AURA-{len(engine.state.cards) + 1}",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            known_to=(
                [seat]
                if zone in {"hand", "library"}
                else list(engine.seats)
            ),
            revealed_to=(
                []
                if zone in {"hand", "library"}
                else list(engine.seats)
            ),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    @staticmethod
    def fixture_aura_records(engine):
        engine_type = type(engine)
        original = engine_type.card_record

        def record_for(host, value):
            instance = (
                value
                if hasattr(value, "object_id")
                else host.state.cards[value]
            )
            if instance.oracle_id == "fixture:simple-aura":
                return aura_record(instance.oracle_id)
            return original(host, value)

        @contextmanager
        def compiled_record():
            record = aura_record("fixture:simple-aura")
            original_trust = (
                engine_type.semantic_program_is_current_trusted
            )

            def trust_fixture_program(host, program):
                return bool(
                    program is not None
                    and program.oracle_id == record.oracle_id
                    and program.trust_level == "trusted"
                ) or original_trust(host, program)

            with patch.object(
                engine_type, "card_record", new=record_for
            ), patch.object(
                engine_type,
                "semantic_program_is_current_trusted",
                new=trust_fixture_program,
            ):
                register_generated_programs(
                    engine.card_db,
                    engine.semantics,
                    (record,),
                    capability_registry=load_default_capability_registry(),
                    capability_profile=engine.state.config.review_profile,
                    promote_exact_runtime_handlers=True,
                )
                yield

        return compiled_record()

    def test_aura_offer_requires_and_projects_one_legal_spell_target(self):
        session, aura, target, records = self.fixture()
        engine = session.engine
        engine.state.players["A"].mana_pool["U"] = 1
        with records:
            hints = engine._priority_action_hints("A")
            action = next(
                action
                for action in hints["actions"]
                if action.get("card") == aura.ref
            )
            self.assertEqual([target.ref], action["target_schema"]["legal_refs"])
            before = copy.deepcopy(engine.state.to_dict())
            with self.assertRaisesRegex(GameRuleError, "requires between"):
                with engine.transaction():
                    engine._cast(
                        "A",
                        {
                            "card": aura.ref,
                            "pay": "manual",
                            "payment": {"U": 1},
                        },
                    )
            self.assertEqual(before, engine.state.to_dict())

    def test_tapped_creature_enchant_rechecks_live_attachment_state(self):
        session, aura, target, records = self.fixture(
            restriction="tapped creature"
        )
        engine = session.engine
        engine.state.players["A"].mana_pool["U"] = 1
        with records:
            self.assertNotIn(
                aura.ref,
                {
                    action.get("card")
                    for action in engine._priority_action_hints("A")["actions"]
                },
            )
            target.tapped = True
            action = next(
                action
                for action in engine._priority_action_hints("A")["actions"]
                if action.get("card") == aura.ref
            )
            self.assertEqual(
                [target.ref], action["target_schema"]["legal_refs"]
            )
            engine._cast(
                "A",
                {
                    "card": aura.ref,
                    "targets": [target.ref],
                    "pay": "manual",
                    "payment": {"U": 1},
                },
            )
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            engine.state.priority_player = None
            engine._prepare_stack_resolution()
            self.assertEqual(target.object_id, aura.attached_to)

            target.tapped = False
            self.assertFalse(
                engine._attachment_is_legal(aura, subtypes={"aura"})
            )
            self.assertFalse(engine._stabilize())
            self.assertEqual("graveyard", aura.zone)

    def test_spell_resolution_attaches_and_illegal_target_counters_by_rule(self):
        session, aura, target, records = self.fixture()
        engine = session.engine
        engine.state.players["A"].mana_pool["U"] = 1
        with records:
            engine._cast(
                "A",
                {
                    "card": aura.ref,
                    "targets": [target.ref],
                    "pay": "manual",
                    "payment": {"U": 1},
                },
            )
            item = engine.state.stack[-1]
            self.assertTrue(item.context["aura_spell"])
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            engine.state.priority_player = None
            engine._prepare_stack_resolution()
            self.assertEqual("battlefield", aura.zone)
            self.assertEqual(target.object_id, aura.attached_to)
            self.assertIn(aura.object_id, target.attachments)

        session, aura, target, records = self.fixture()
        engine = session.engine
        engine.state.players["A"].mana_pool["U"] = 1
        with records:
            engine._cast(
                "A",
                {
                    "card": aura.ref,
                    "targets": [target.ref],
                    "pay": "manual",
                    "payment": {"U": 1},
                },
            )
            engine.move_card(
                target.object_id,
                "graveyard",
                reason="remove Aura target",
                log=False,
            )
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            engine.state.priority_player = None
            engine._prepare_stack_resolution()
            self.assertEqual("graveyard", aura.zone)
            self.assertFalse(engine.state.stack)

    def test_player_aura_cast_attachment_projection_and_replay(self):
        session, aura, _target, records = self.fixture(
            restriction="player"
        )
        engine = session.engine
        engine.state.players["A"].mana_pool["U"] = 1
        with records:
            action = next(
                action
                for action in engine._priority_action_hints("A")["actions"]
                if action.get("card") == aura.ref
            )
            self.assertEqual(
                {"A", "B"},
                set(action["target_schema"]["legal_refs"]),
            )
            engine._cast(
                "A",
                {
                    "card": aura.ref,
                    "targets": ["B"],
                    "pay": "manual",
                    "payment": {"U": 1},
                },
            )
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            engine.state.priority_player = None
            engine._prepare_stack_resolution()
            self.assertEqual("player:B", aura.attached_to)
            self.assertIn(aura.object_id, engine.state.players["B"].attachments)
            self.assertTrue(
                engine._attachment_is_legal(aura, subtypes={"aura"})
            )
            for principal in ("pilot:A", "pilot:B"):
                rendered = json.dumps(
                    session.projector._snapshot(principal),
                    sort_keys=True,
                )
                self.assertIn('"at": "B"', rendered)
                self.assertNotIn("player:B", rendered)
                self.assertNotIn(aura.object_id, rendered)

            engine.state.pending_decision = None
            engine.state.priority_player = None
            engine.state.priority_passes = []
            engine._grant_priority("A")
            engine.pump()
            session.initial_checkpoint = checkpoint_envelope(engine.state)
            session.commands.clear()
            session.decisions.clear()
            result = session.act("pilot:A", {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
            with tempfile.TemporaryDirectory() as temporary:
                record_dir = Path(temporary) / "player-aura-replay"
                session.save(record_dir)
                replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_nonspell_player_aura_entry_uses_seat_choice(self):
        session, aura, _target, records = self.fixture(
            restriction="player"
        )
        engine = session.engine
        engine.move_card(aura.object_id, "graveyard", log=False)
        item = StackItem(
            stack_id="test-player-aura-entry",
            ref="S-player-aura-entry",
            kind="spell",
            controller="A",
            label="Return a player Aura",
            semantic_key="test:player-aura-entry",
            visibility=["A", "B"],
        )
        engine.state.stack.append(item)
        with records:
            engine._continue_resolution(
                stack_ref=item.ref,
                effects=[
                    {
                        "op": "move",
                        "card": aura.ref,
                        "destination": "battlefield",
                        "controller": "A",
                    }
                ],
                destination=None,
                note="Player Aura nonspell entry",
            )
            decision = engine.state.pending_decision
            self.assertEqual("aura.entry", decision.kind)
            choice = decision.payload_by_actor["A"]["legal_actions"][0][
                "choice_schema"
            ]["aura_target"]
            self.assertEqual("seat", choice["type"])
            self.assertEqual({"A", "B"}, set(choice["legal_seats"]))
            capability = engine.permissions.capability_for("pilot:A")
            self.assertIsNotNone(capability)
            result = engine.submit(
                token=capability.token,
                principal="pilot:A",
                action="choose",
                payload={"aura_target": "B"},
            )
            self.assertTrue(result.ok)
        self.assertEqual("battlefield", aura.zone)
        self.assertEqual("player:B", aura.attached_to)
        self.assertIn(aura.object_id, engine.state.players["B"].attachments)

    def test_live_mixed_characteristic_legality_uses_effective_types(self):
        session, aura, target, records = self.fixture(
            restriction="creature or Vehicle"
        )
        engine = session.engine
        base_power = engine._numeric_stat(target.object_id, "power")
        with records:
            engine.move_card(
                aura.object_id,
                "battlefield",
                controller="A",
                aura_target_ref=target.ref,
                log=False,
            )
            self.assertTrue(
                engine._attachment_is_legal(aura, subtypes={"aura"})
            )
            self.assertEqual(
                base_power + 1,
                engine._numeric_stat(target.object_id, "power"),
            )
            target.annotations["copy_overrides"] = {
                "type_line": "Artifact — Vehicle"
            }
            self.assertTrue(
                engine._attachment_is_legal(aura, subtypes={"aura"})
            )
            target.annotations["copy_overrides"] = {
                "type_line": "Artifact — Food"
            }
            self.assertFalse(
                engine._attachment_is_legal(aura, subtypes={"aura"})
            )
            self.assertFalse(engine._stabilize())
        self.assertEqual("graveyard", aura.zone)

    def test_typed_aura_and_delayed_draw_compose(self):
        restriction = "non-Wall creature you control"
        session, aura, target, records = self.fixture(
            restriction=restriction,
            oracle_text=(
                f"Enchant {restriction}\n"
                "When this Aura enters, draw a card at the beginning of the "
                "next turn's upkeep."
            ),
        )
        engine = session.engine
        engine.change_control(target.object_id, "A")
        engine.state.players["A"].mana_pool["U"] = 1
        with records:
            engine._cast(
                "A",
                {
                    "card": aura.ref,
                    "targets": [target.ref],
                    "pay": "manual",
                    "payment": {"U": 1},
                },
            )
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            engine.state.priority_player = None
            engine.state.priority_passes = []
            engine._prepare_stack_resolution()
            self.assertEqual(target.object_id, aura.attached_to)
            self.assertTrue(engine.state.stack)

            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            engine.state.priority_player = None
            engine.state.priority_passes = []
            engine._prepare_stack_resolution()
            delayed = next(
                trigger
                for trigger in engine.state.delayed_triggers
                if trigger.label
                == "Draw at the beginning of the next turn's upkeep"
            )
            hand_before = len(engine.state.players["A"].zones["hand"])
            advance_fixture_turn(engine)
            engine.state.active_player = "B"
            matches = engine._matching_delayed_triggers(
                "step.begin",
                {"phase": "beginning", "step": "upkeep", "player": "B"},
            )
            engine._start_trigger_batch(matches, after="grant_priority")
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            engine.state.priority_player = None
            engine.state.priority_passes = []
            engine._prepare_stack_resolution()

        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )
        self.assertFalse(delayed.active)
        self.assertEqual(target.object_id, aura.attached_to)
        self.assertIn(aura.object_id, target.attachments)

    def test_opponent_player_aura_is_four_player_seat_scoped(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=303406,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        aura = self.add_fixture_aura(engine, "A", "hand")
        spec = parse_enchant_line("Enchant opponent")
        self.assertIsInstance(spec, TypedEnchantSpec)
        assert isinstance(spec, TypedEnchantSpec)
        with self.fixture_aura_records(engine):
            self.assertEqual(
                {"B", "C", "D"},
                set(
                    legal_aura_target_refs(
                        engine,
                        aura,
                        spec,
                        controller="A",
                        as_target=True,
                    )
                ),
            )

    def test_player_departure_makes_attached_aura_illegal(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=303407,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        created = engine.create_token(
            "A",
            name="Player Aura",
            characteristics={
                "type_line": "Token Enchantment — Aura",
                "oracle_text": "Enchant player",
                "colors": ["U"],
                "ability_fragments": [
                    ability_fragment_to_dict(
                        TypedEnchantSpec(
                            subject=AuraEnchantSubject.PLAYER
                        )
                    )
                ],
            },
            aura_target_ref="B",
        )
        self.assertEqual(1, len(created))
        aura = engine._resolve_object(
            "A", created[0], zones={"battlefield"}
        )
        self.assertEqual("player:B", aura.attached_to)
        engine.state.players["B"].in_game = False
        self.assertFalse(
            engine._attachment_is_legal(aura, subtypes={"aura"})
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual("outside", aura.zone)
        self.assertNotIn(
            aura.object_id,
            engine.state.players["B"].attachments,
        )

    def test_player_protection_makes_attached_aura_illegal(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=303408,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        created = engine.create_token(
            "A",
            name="Protected Player Aura",
            characteristics={
                "type_line": "Token Enchantment — Aura",
                "oracle_text": "Enchant player",
                "colors": ["U"],
                "ability_fragments": [
                    ability_fragment_to_dict(
                        TypedEnchantSpec(
                            subject=AuraEnchantSubject.PLAYER
                        )
                    )
                ],
            },
            aura_target_ref="B",
        )
        aura = engine._resolve_object(
            "A", created[0], zones={"battlefield"}
        )
        engine.state.players["B"].stats[
            "protection_from_everything_until_next_turn"
        ] = True
        self.assertFalse(
            engine._attachment_is_legal(aura, subtypes={"aura"})
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual("outside", aura.zone)
        self.assertNotIn(
            aura.object_id,
            engine.state.players["B"].attachments,
        )

    def test_graveyard_card_aura_attachment_clears_on_zone_change(self):
        session, aura, target, records = self.fixture(
            restriction="creature card in a graveyard"
        )
        engine = session.engine
        engine.move_card(target.object_id, "graveyard", log=False)
        engine.state.players["A"].mana_pool["U"] = 1
        with records:
            engine._cast(
                "A",
                {
                    "card": aura.ref,
                    "targets": [target.ref],
                    "pay": "manual",
                    "payment": {"U": 1},
                },
            )
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            engine.state.priority_player = None
            engine._prepare_stack_resolution()
            self.assertEqual(target.object_id, aura.attached_to)
            self.assertIn(aura.object_id, target.attachments)
            engine.move_card(target.object_id, "hand", log=False)
            self.assertIsNone(aura.attached_to)
            self.assertFalse(engine._stabilize())
        self.assertEqual("graveyard", aura.zone)

    def test_live_aura_paths_never_recompile_oracle_text(self):
        session, aura, target, records = self.fixture()
        engine = session.engine
        engine.state.players["A"].mana_pool["U"] = 1
        with records, patch(
            "quorune.aura.grammar.simple_enchant_spec_from_oracle",
            side_effect=AssertionError("runtime Oracle compiler invoked"),
        ):
            hints = engine._priority_action_hints("A")
            self.assertIn(
                aura.ref,
                {
                    action.get("card")
                    for action in hints["actions"]
                },
            )
            engine._cast(
                "A",
                {
                    "card": aura.ref,
                    "targets": [target.ref],
                    "pay": "manual",
                    "payment": {"U": 1},
                },
            )
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            engine.state.priority_player = None
            engine._prepare_stack_resolution()
            self.assertEqual(target.object_id, aura.attached_to)

    def test_nonspell_entry_pauses_for_choice_and_resumes_exact_effect(self):
        session, aura, target, records = self.fixture()
        engine = session.engine
        engine.move_card(aura.object_id, "graveyard", log=False)
        item = StackItem(
            stack_id="test-aura-entry",
            ref="S-aura-entry",
            kind="spell",
            controller="A",
            label="Return an Aura",
            semantic_key="test:aura-entry",
            visibility=["A", "B"],
        )
        engine.state.stack.append(item)
        with records:
            engine._continue_resolution(
                stack_ref=item.ref,
                effects=[
                    {
                        "op": "move",
                        "card": aura.ref,
                        "destination": "battlefield",
                        "controller": "A",
                    }
                ],
                destination=None,
                note="Aura nonspell entry",
            )
            self.assertEqual("aura.entry", engine.state.pending_decision.kind)
            self.assertEqual("graveyard", aura.zone)
            capability = engine.permissions.capability_for("pilot:A")
            self.assertIsNotNone(capability)
            result = engine.submit(
                token=capability.token,
                principal="pilot:A",
                action="choose",
                payload={"aura_target": target.ref},
            )
            self.assertTrue(result.ok)
            self.assertEqual("battlefield", aura.zone)
            self.assertEqual(target.object_id, aura.attached_to)
            self.assertNotIn(item, engine.state.stack)

    def test_nonspell_entry_with_no_legal_object_stays_in_origin_zone(self):
        session, aura, target, records = self.fixture()
        engine = session.engine
        engine.move_card(target.object_id, "graveyard", log=False)
        engine.move_card(aura.object_id, "graveyard", log=False)
        with records:
            moved = engine.move_card(
                aura.object_id,
                "battlefield",
                controller="A",
                semantic_events=True,
            )
        self.assertIs(moved, aura)
        self.assertEqual("graveyard", aura.zone)
        self.assertIsNone(aura.attached_to)

    def test_aura_tokens_preflight_attachment_and_no_legal_object_is_not_created(self):
        session, _aura, target, records = self.fixture()
        engine = session.engine
        characteristics = {
            "type_line": "Token Enchantment — Aura",
            "oracle_text": "Enchant creature",
            "colors": ["U"],
            "ability_fragments": [
                ability_fragment_to_dict(
                    SimpleEnchantSpec("creature")
                )
            ],
        }
        with records:
            before = copy.deepcopy(engine.state.to_dict())
            with self.assertRaisesRegex(
                GameRuleError, "requires a legal attachment choice"
            ):
                engine.create_token(
                    "A",
                    name="Test Aura",
                    characteristics=characteristics,
                )
            self.assertEqual(before, engine.state.to_dict())

            created = engine.create_token(
                "A",
                name="Test Aura",
                characteristics=characteristics,
                aura_target_ref=target.ref,
            )
            self.assertEqual(1, len(created))
            token = engine._resolve_object(
                "A", created[0], zones={"battlefield"}
            )
            self.assertEqual(target.object_id, token.attached_to)

            engine.move_card(token.object_id, "graveyard", log=False)
            engine.move_card(target.object_id, "graveyard", log=False)
            self.assertEqual(
                [],
                engine.create_token(
                    "A",
                    name="Test Aura",
                    characteristics=characteristics,
                ),
            )
            self.assertFalse(
                any(
                    card.is_token and card.zone == "battlefield"
                    for card in engine.state.cards.values()
                )
            )

    def test_unsupported_enchant_grammar_fails_before_mutation(self):
        session, aura, target, records = self.fixture(
            restriction="creature with flying"
        )
        engine = session.engine
        before = copy.deepcopy(engine.state.to_dict())
        with records, self.assertRaisesRegex(
            GameRuleError, "compiled Enchant descriptor"
        ):
            engine.move_card(
                aura.object_id,
                "battlefield",
                controller="A",
                aura_target_ref=target.ref,
            )
        self.assertEqual(before, engine.state.to_dict())

    def test_control_relation_and_protection_are_live_attachment_legality(self):
        session, aura, target, records = self.fixture(
            restriction="creature you control"
        )
        engine = session.engine
        engine.change_control(target.object_id, "A")
        with records:
            engine.move_card(
                aura.object_id,
                "battlefield",
                controller="A",
                aura_target_ref=target.ref,
            )
            self.assertTrue(
                engine._attachment_is_legal(aura, subtypes={"aura"})
            )
            engine.change_control(target.object_id, "B")
            self.assertFalse(
                engine._attachment_is_legal(aura, subtypes={"aura"})
            )
            engine.change_control(target.object_id, "A")
            target.annotations["copy_overrides"] = {
                "keywords": ["Protection"],
                "ability_fragments": [
                    ability_fragment_to_dict(
                        ProtectionSpec(
                            ProtectionQualityKind.COLOR,
                            "U",
                        )
                    )
                ],
            }
            self.assertFalse(
                engine._attachment_is_legal(aura, subtypes={"aura"})
            )
            self.assertFalse(engine._stabilize())
            self.assertEqual("graveyard", aura.zone)

    def test_four_player_aura_targets_and_relations_are_seat_correct(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=303404,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        targets = []
        for seat in engine.seats:
            creature = next(
                card
                for card in engine.state.cards.values()
                if card.owner == seat
                and engine.card_record(card) is not None
                and "creature"
                in engine.card_record(card).type_line.casefold()
            )
            engine.move_card(
                creature.object_id,
                "battlefield",
                controller=seat,
                log=False,
            )
            targets.append(creature)
        aura = self.add_fixture_aura(engine, "A", "hand")
        with self.fixture_aura_records(engine):
            any_targets = legal_aura_target_refs(
                engine,
                aura,
                SimpleEnchantSpec("creature"),
                controller="A",
                as_target=True,
            )
        self.assertEqual(
            {card.ref for card in targets}, set(any_targets)
        )
        with self.fixture_aura_records(engine):
            own_targets = legal_aura_target_refs(
                engine,
                aura,
                SimpleEnchantSpec(
                    "creature", AuraControllerRelation.YOU
                ),
                controller="A",
                as_target=True,
            )
        self.assertEqual(
            {card.ref for card in targets if card.controller == "A"},
            set(own_targets),
        )

    def test_aura_entry_choice_projection_and_replay_are_exact(self):
        session = self.make_session(seed=303405)
        engine = session.engine
        target = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B"
            and engine.card_record(card) is not None
            and "creature"
            in engine.card_record(card).type_line.casefold()
        )
        engine.move_card(
            target.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        protected_ref = engine.create_token(
            "B",
            name="Protection Replay Witness",
            characteristics={
                "type_line": "Token Creature — Test",
                "oracle_text": "Protection from blue",
                "keywords": ["Protection"],
                "power": "2",
                "toughness": "2",
                "ability_fragments": [
                    ability_fragment_to_dict(
                        ProtectionSpec(
                            ProtectionQualityKind.COLOR,
                            "U",
                        )
                    )
                ],
            },
        )[0]
        aura = self.add_fixture_aura(engine, "A", "graveyard")
        engine.semantics.put(
            SemanticProgram(
                key="test:aura-replay",
                label="Return Aura replay witness",
                effects=[
                    {
                        "op": "move",
                        "card": aura.ref,
                        "destination": "battlefield",
                        "controller": "A",
                    }
                ],
                trust_level="provisional",
            )
        )
        item = StackItem(
            stack_id="aura-replay-stack",
            ref="S-aura-replay",
            kind="spell",
            controller="A",
            label="Return Aura replay witness",
            semantic_key="test:aura-replay",
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        with self.fixture_aura_records(engine):
            engine._continue_resolution(
                stack_ref=item.ref,
                effects=[
                    {
                        "op": "move",
                        "card": aura.ref,
                        "destination": "battlefield",
                        "controller": "A",
                    }
                ],
                destination=None,
                note="Aura replay witness",
            )
            self.assertEqual(
                "aura.entry", engine.state.pending_decision.kind
            )
            packet = session.packet("pilot:A", full=True)
            rendered = json.dumps(packet, sort_keys=True)
            self.assertIn(target.ref, rendered)
            legal_refs = packet["decision"]["ctx"]["target_schema"][
                "legal_refs"
            ]
            self.assertIn(target.ref, legal_refs)
            self.assertNotIn(protected_ref, legal_refs)
            self.assertNotIn(target.object_id, rendered)
            self.assertNotIn(aura.object_id, rendered)

            session.initial_checkpoint = checkpoint_envelope(engine.state)
            session.commands.clear()
            session.decisions.clear()
            result = session.act(
                "pilot:A",
                {"action_id": "choose", "aura_target": target.ref},
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(target.object_id, aura.attached_to)
            with tempfile.TemporaryDirectory() as temporary:
                record_dir = Path(temporary) / "aura-replay"
                session.save(record_dir)
                replay = replay_record(
                    record_dir, self.db, verify=True
                )
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(1, replay["commands"])


if __name__ == "__main__":
    unittest.main()
    parse_enchant_line,
    ability_fragment_from_dict,

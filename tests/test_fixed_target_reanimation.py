from __future__ import annotations

import copy
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune import CommanderSession
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.reanimation_templates import (
    FIXED_TARGET_REANIMATION_CAPABILITY,
    FIXED_TARGET_REANIMATION_MECHANIC,
    fixed_target_reanimation_effect_template,
)
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import CapabilityRegistry
from quorune.rules.reanimation_capability_shapes import (
    fixed_target_reanimation_node_capabilities,
)
from quorune.semantics import SemanticProgram
from quorune.targets import TargetGroup
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "fixed-target-reanimation-cards.json"


def trusted_registry(value: dict | None = None) -> CapabilityRegistry:
    registry = CapabilityRegistry(
        value or json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    )
    registry.mark_evidence_verified("0" * 64)
    return registry


def card_record(
    text: str,
    *,
    type_line: str = "Sorcery",
    name: str = "Fixed Reanimation Fixture",
) -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000000395",
        name=name,
        mana_cost="{2}{B}",
        mana_value=3.0,
        type_line=type_line,
        oracle_text=text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=("B",),
        color_identity=("B",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class FixedTargetReanimationCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry_value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        self.capabilities = trusted_registry(self.registry_value)

    def compile(self, text: str, *, type_line: str = "Sorcery", capabilities=None):
        return compile_oracle_card(
            card_record(text, type_line=type_line),
            capability_registry=capabilities or self.capabilities,
            capability_profile="commander_review",
        )

    @staticmethod
    def reanimation_nodes(ir):
        return [
            node
            for face in ir.faces
            for node in face.nodes
            if FIXED_TARGET_REANIMATION_MECHANIC in node.mechanics
        ]

    def test_fixed_target_reanimation_compiles_across_spell_trigger_activation_and_modal_contexts(self):
        cases = (
            (
                "Return target creature card from your graveyard to the battlefield.",
                "Sorcery",
            ),
            (
                "When this creature enters, return target artifact card from your graveyard to the battlefield.",
                "Creature — Cleric",
            ),
            (
                "{3}{B}, {T}: Put target creature card from a graveyard onto the battlefield under your control.",
                "Creature — Warlock",
            ),
            (
                "Choose one —\n• Return target creature card from your graveyard to the battlefield.\n• Draw a card.",
                "Sorcery",
            ),
        )
        for text, type_line in cases:
            with self.subTest(text=text):
                compiled = self.compile(text, type_line=type_line)
                self.assertEqual("exact", compiled.status, compiled.material_residuals)
                nodes = self.reanimation_nodes(compiled)
                self.assertTrue(nodes)
                self.assertTrue(all(node.exact for node in nodes))
                self.assertTrue(
                    all(
                        FIXED_TARGET_REANIMATION_CAPABILITY
                        in node.capability_dependencies
                        or "fixed-nonrepeating-modal" in node.mechanics
                        for node in nodes
                    )
                )

    def test_reanimation_target_domains_controller_and_tapped_state_are_typed(self):
        cases = (
            (
                "Return target legendary creature card from your graveyard to the battlefield.",
                "you",
                False,
                "$controller",
            ),
            (
                "Return target Zombie creature card with mana value 3 or less from an opponent's graveyard to the battlefield tapped under its owner's control.",
                "opponent",
                True,
                "$target.owner.0",
            ),
            (
                "Put up to one target artifact or creature card from any graveyard onto the battlefield under your control.",
                "any",
                False,
                "$controller",
            ),
        )
        for text, owner_relation, tapped, controller in cases:
            with self.subTest(text=text):
                template = fixed_target_reanimation_effect_template(text)
                self.assertIsNotNone(template)
                assert template is not None
                template_id, effects, schema, mechanics = template.compiled()
                self.assertIn("reanimate", template_id)
                self.assertEqual(owner_relation, schema["owner_relation"])
                self.assertEqual(tapped, effects[0]["tapped"])
                self.assertEqual(controller, effects[0]["controller"])
                self.assertEqual(
                    (FIXED_TARGET_REANIMATION_CAPABILITY,),
                    fixed_target_reanimation_node_capabilities(
                        effects=effects,
                        target_schema=schema,
                        mechanic_ids=mechanics,
                    ),
                )

    def test_reanimation_closed_type_and_power_qualities_are_typed(self):
        cases = (
            (
                "Return target creature card with power 2 or less from a graveyard to the battlefield.",
                "numeric_characteristic",
            ),
            (
                "Return target creature or Vehicle card from your graveyard to the battlefield.",
                "characteristic_forms_any",
            ),
            (
                "Return target Cave card from your graveyard to the battlefield.",
                "subtypes_any",
            ),
        )
        for text, expected_field in cases:
            with self.subTest(text=text):
                template = fixed_target_reanimation_effect_template(text)
                self.assertIsNotNone(template)
                assert template is not None
                _template_id, effects, schema, mechanics = template.compiled()
                self.assertIn(expected_field, schema)
                self.assertEqual(
                    (FIXED_TARGET_REANIMATION_CAPABILITY,),
                    fixed_target_reanimation_node_capabilities(
                        effects=effects,
                        target_schema=schema,
                        mechanic_ids=mechanics,
                    ),
                )

        numeric_schema = {
            "category": "card",
            "count": 1,
            "types_any": ["creature"],
            "numeric_characteristic": {
                "characteristic": "power",
                "comparison": "at_most",
                "value": 2,
            },
        }
        with self.assertRaisesRegex(ValueError, "hidden/nonpublic zones"):
            TargetGroup.from_mapping(
                {
                    "zone": "hand",
                    **numeric_schema,
                }
            )
        with self.assertRaisesRegex(ValueError, "public permanent or graveyard card"):
            TargetGroup.from_mapping(
                {
                    "zone": "exile",
                    **numeric_schema,
                }
            )

    def test_reanimation_unsupported_and_malformed_shapes_fail_closed(self):
        unsupported = (
            "Return a creature card from your graveyard to the battlefield.",
            "Return all creature cards from your graveyard to the battlefield.",
            "Return target instant card from your graveyard to the battlefield.",
            "Return target creature card with mana value X or less from your graveyard to the battlefield.",
            "Return target creature card from your graveyard to the battlefield with a finality counter on it.",
            "Return target creature card from your graveyard to the battlefield. It gains haste.",
            "Put target creature card from a graveyard onto the battlefield.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(fixed_target_reanimation_effect_template(text))
                self.assertNotEqual("exact", self.compile(text).status)

    def test_reanimation_capability_shape_and_dependency_mutations_fail_closed(self):
        template = fixed_target_reanimation_effect_template(
            "Return target creature card from your graveyard to the battlefield."
        )
        assert template is not None
        _template_id, effects, schema, mechanics = template.compiled()
        mutations = (
            ({**effects[0], "op": "move"}, schema),
            ({**effects[0], "card": "$source"}, schema),
            ({**effects[0], "controller": "A"}, schema),
            ({**effects[0], "tapped": 1}, schema),
            (effects[0], {**schema, "zones": ["hand"]}),
            (effects[0], {**schema, "types_any": ["instant"]}),
        )
        for effect, target_schema in mutations:
            with self.subTest(effect=effect, target_schema=target_schema):
                self.assertEqual(
                    (),
                    fixed_target_reanimation_node_capabilities(
                        effects=(effect,),
                        target_schema=target_schema,
                        mechanic_ids=mechanics,
                    ),
                )

        for dependency_id in (
            "attachment.aura.typed_restriction",
            "target.revalidate_resolution",
            "zone.change.destination_replacement",
        ):
            registry_value = deepcopy(self.registry_value)
            dependency = next(
                row
                for row in registry_value["capabilities"]
                if row["id"] == dependency_id
            )
            dependency["status"] = "blocked"
            compiled = self.compile(
                "Return target creature card from your graveyard to the battlefield.",
                capabilities=trusted_registry(registry_value),
            )
            self.assertNotEqual("exact", compiled.status)


class _RuntimeBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "fixed-target-reanimation.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                FIXTURE_PATH,
            ],
            database,
        )
        cls.db = CardDatabase(database)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, spell: str):
        mishra = copy.deepcopy(self.mishra)
        next(entry for entry in mishra.entries if entry.board == "mainboard").name = spell
        session = make_session(
            self.db,
            mishra,
            copy.deepcopy(self.zimone),
            players=4,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def card(engine, seat: str, *, name: str | None = None, creature=None, exclude=()):
        excluded = set(exclude)
        for card in engine.state.cards.values():
            if (
                card.owner != seat
                or card.object_id in excluded
                or card.zone == "command"
                or not card.is_card_object
                or (name is not None and card.printed_name != name)
            ):
                continue
            if creature is not None:
                types = engine._type_parts(
                    str(engine._effective_card_data(card).get("type_line") or "")
                )[0]
                if ("creature" in types) is not creature:
                    continue
            return card
        raise AssertionError("No matching fixture card")

    def promote(self, engine, *names: str):
        records = tuple(self.db.lookup(name) for name in names)
        for record in records:
            for program in engine.semantics.programs_for_oracle(record.oracle_id):
                engine.semantics.remove(program.key)
        return register_generated_programs(
            self.db,
            engine.semantics,
            records,
            trust_level="trusted",
            capability_registry=trusted_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )

    def ready_spell(self, session, name: str, mana: dict[str, int]):
        engine = session.engine
        source = self.card(engine, "A", name=name)
        engine.move_card(source.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool.update(mana)
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        hints = engine._priority_action_hints("A")
        action = next(
            row for row in hints["actions"] if row.get("card") == source.ref
        )
        engine._issue_priority("A", hints)
        return source, action

    def cast(self, session, action, target_ref: str, payment: dict[str, int]):
        result = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [target_ref],
                "pay": "manual",
                "payment": payment,
            },
        )
        self.assertTrue(result.ok, result.summary)

    def resolve_until_nonpriority(self, session):
        for _ in range(64):
            decision = session.state.pending_decision
            if decision is not None and decision.kind != "priority":
                return decision
            if not session.state.stack:
                return None
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Resolution did not reach a stable choice")

    def resolve_all(self, session):
        for _ in range(96):
            decision = session.state.pending_decision
            if decision is not None and decision.kind == "arbiter.resolve":
                item = session.state.stack[-1]
                program = session.engine.semantics.get(item.semantic_key)
                assert program is not None
                result = session.act(
                    "arbiter",
                    {"action_id": "resolve", "effects": program.effects},
                )
                self.assertTrue(result.ok, result.summary)
                continue
            if decision is not None and decision.kind != "priority":
                return decision
            if not session.state.stack:
                return None
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Resolution did not stabilize")

    def assert_replays(self, session, label: str):
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / label
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])


class FixedTargetReanimationRuntimeTests(_RuntimeBase):
    def test_reanimation_activation_uses_offered_target_and_tap_cost(self):
        session = self.session(39600, spell="Reanimation Adept")
        engine = session.engine
        self.promote(engine, "Reanimation Adept")
        source = self.card(engine, "A", name="Reanimation Adept")
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.state.players["A"].turns_begun = 1
        source.acquired_control_turn_count = 0
        target = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A"
            and card.object_id != source.object_id
            and card.zone != "command"
            and card.is_card_object
            and "creature"
            in engine._type_parts(
                str(engine._effective_card_data(card).get("type_line") or "")
            )[0]
            and float(engine._effective_card_data(card).get("mana_value") or 0)
            <= 3
        )
        engine.move_card(target.object_id, "graveyard", log=False)
        engine.state.players["A"].mana_pool.update({"C": 3, "B": 1})
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        hints = engine._priority_action_hints("A")
        action = next(
            row
            for row in hints["actions"]
            if row.get("source") == source.ref
            and str(row.get("id") or "").startswith("activate:")
        )
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        engine._issue_priority("A", hints)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"C": 3, "B": 1},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(source.tapped)
        self.assertIsNone(self.resolve_all(session))
        self.assertEqual("battlefield", target.zone)
        self.assertTrue(target.tapped)
        self.assert_replays(session, "fixed-target-reanimation-activation")

    def test_reanimation_controller_tapped_and_characteristic_variants_resolve(self):
        for seed, spell, mana, expected_controller, tapped in (
            (39601, "Public Reanimate", {"C": 3, "B": 1}, "A", False),
            (39602, "Owner Tapped Reanimate", {"C": 4, "B": 1}, "B", True),
        ):
            with self.subTest(spell=spell):
                session = self.session(seed, spell=spell)
                engine = session.engine
                self.promote(engine, spell)
                target = self.card(engine, "B", creature=True)
                engine.move_card(target.object_id, "graveyard", log=False)
                source, action = self.ready_spell(session, spell, mana)
                self.assertIn(target.ref, action["target_schema"]["legal_refs"])
                self.cast(session, action, target.ref, mana)
                self.assertIsNone(self.resolve_all(session))
                self.assertEqual("battlefield", target.zone)
                self.assertEqual(expected_controller, target.controller)
                self.assertEqual(tapped, target.tapped)
                self.assertEqual("graveyard", source.zone)

    def test_reanimation_target_revalidates_and_rolls_back(self):
        session = self.session(39603, spell="Fixed Reanimate")
        engine = session.engine
        self.promote(engine, "Fixed Reanimate")
        target = self.card(engine, "A", creature=True)
        engine.move_card(target.object_id, "graveyard", log=False)
        malformed_before = authoritative_state_hash(engine.state)
        malformed_effects = (
            {
                "op": "reanimate",
                "card": target.ref,
                "controller": "A",
                "tapped": 1,
            },
            {
                "op": "reanimate",
                "card": target.ref,
                "controller": "A",
                "_replacement_selections": {},
            },
            {
                "op": "reanimate",
                "card": "",
                "controller": "A",
            },
        )
        for malformed in malformed_effects:
            with self.subTest(malformed=malformed):
                with self.assertRaisesRegex(GameRuleError, "Reanimation"):
                    engine.apply_effect(malformed, actor="A")
                self.assertEqual(
                    malformed_before,
                    authoritative_state_hash(engine.state),
                )
        source, action = self.ready_spell(
            session, "Fixed Reanimate", {"C": 2, "B": 1}
        )
        self.cast(session, action, target.ref, {"C": 2, "B": 1})
        engine.move_card(target.object_id, "exile", log=False)
        engine.move_card(target.object_id, "graveyard", log=False)
        before_logical_id = target.logical_object_id
        before_zone_change_counter = target.zone_change_counter
        self.assertIsNone(self.resolve_all(session))
        self.assertEqual("graveyard", target.zone)
        self.assertEqual(before_logical_id, target.logical_object_id)
        self.assertEqual(before_zone_change_counter, target.zone_change_counter)
        self.assertEqual("graveyard", source.zone)

    def test_reanimation_current_graveyard_characteristics_and_source_control_compose(self):
        session = self.session(39604, spell="Public Reanimate")
        engine = session.engine
        self.promote(engine, "Public Reanimate")
        target = self.card(engine, "B", creature=False)
        engine.move_card(target.object_id, "graveyard", log=False)
        target.annotations["copy_overrides"] = {"type_line": "Creature — Zombie"}
        _source, action = self.ready_spell(
            session, "Public Reanimate", {"C": 3, "B": 1}
        )
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        self.cast(session, action, target.ref, {"C": 3, "B": 1})
        target.annotations.pop("copy_overrides", None)
        self.assertIsNone(self.resolve_all(session))
        self.assertEqual("graveyard", target.zone)

        power_session = self.session(396041, spell="Power Reanimate")
        power_engine = power_session.engine
        self.promote(power_engine, "Power Reanimate")
        low_power = self.card(power_engine, "B", creature=True)
        high_power = self.card(
            power_engine,
            "B",
            creature=True,
            exclude=(low_power.object_id,),
        )
        power_engine.move_card(low_power.object_id, "graveyard", log=False)
        power_engine.move_card(high_power.object_id, "graveyard", log=False)
        low_power.annotations["continuous_power"] = 2
        high_power.annotations["continuous_power"] = 3
        _power_source, power_action = self.ready_spell(
            power_session,
            "Power Reanimate",
            {"C": 3, "B": 1},
        )
        self.assertIn(low_power.ref, power_action["target_schema"]["legal_refs"])
        self.assertNotIn(
            high_power.ref,
            power_action["target_schema"]["legal_refs"],
        )
        self.cast(
            power_session,
            power_action,
            low_power.ref,
            {"C": 3, "B": 1},
        )
        low_power.annotations["continuous_power"] = 3
        self.assertIsNone(self.resolve_all(power_session))
        self.assertEqual("graveyard", low_power.zone)

    def test_reanimation_destination_replacement_and_aura_entry_choices_resume_once(self):
        session = self.session(39605, spell="Public Reanimate")
        engine = session.engine
        self.promote(engine, "Public Reanimate")
        target = self.card(engine, "B", creature=True)
        engine.move_card(target.object_id, "graveyard", log=False)
        replacement_source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A"
            and card.zone != "command"
            and card.printed_name != "Public Reanimate"
        )
        engine.move_card(
            replacement_source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.semantics.put(
            SemanticProgram(
                key="test:reanimation-replacement",
                label="Replace reanimation destination",
                oracle_id=replacement_source.oracle_id,
                ability_id="static:front:reanimation-replacement",
                active_zone="battlefield",
                event="zone.change",
                trust_level="provisional",
                handlers=[
                    {
                        "handler_id": "replacement.zone.destination.v1",
                        "schema_version": 1,
                        "event": "zone.change",
                        "condition": {
                            "destination": "battlefield",
                            "object_kind": "card",
                            "owner_relation": "opponent",
                        },
                        "destination": "exile",
                        "counters": {},
                    }
                ],
            )
        )
        spell, action = self.ready_spell(
            session, "Public Reanimate", {"C": 3, "B": 1}
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.cast(session, action, target.ref, {"C": 3, "B": 1})
        with patch.object(
            type(engine), "semantic_program_is_current_trusted", return_value=True
        ):
            self.assertIsNone(self.resolve_all(session))
        self.assertEqual("exile", target.zone)
        self.assertEqual("graveyard", spell.zone)
        self.assertEqual(
            1,
            sum(
                event.code == "replacement.apply"
                for event in engine.state.events
            ),
        )
        aura_session = self.session(396051, spell="Owner Tapped Reanimate")
        aura_engine = aura_session.engine
        self.promote(
            aura_engine,
            "Owner Tapped Reanimate",
            "Reanimation Aura",
        )
        recipient = self.card(aura_engine, "B", creature=True)
        aura_engine.move_card(
            recipient.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        aura_record = self.db.lookup("Reanimation Aura")
        aura = CardInstance(
            object_id="fixture:reanimation-aura",
            ref="B-REANIMATION-AURA",
            oracle_id=aura_record.oracle_id,
            printed_name=aura_record.name,
            owner="B",
            controller="B",
            zone="graveyard",
            zone_timestamp=aura_engine.state.timestamp_sequence + 1,
            known_to=list(aura_engine.seats),
            revealed_to=list(aura_engine.seats),
        )
        aura_engine.state.cards[aura.object_id] = aura
        aura_engine.state.players["B"].zones["graveyard"].append(aura.object_id)
        aura_spell, aura_action = self.ready_spell(
            aura_session,
            "Owner Tapped Reanimate",
            {"C": 4, "B": 1},
        )
        aura_session.initial_checkpoint = checkpoint_envelope(aura_engine.state)
        aura_session.commands.clear()
        aura_session.decisions.clear()
        self.cast(
            aura_session,
            aura_action,
            aura.ref,
            {"C": 4, "B": 1},
        )
        decision = self.resolve_until_nonpriority(aura_session)
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual("aura.entry", decision.kind)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "reanimation-aura-entry"
            aura_session.save(record_dir)
            loaded = CommanderSession.load(self.db, record_dir)
            packet = loaded.packet("pilot:B", full=True)
            legal_refs = packet["decision"]["ctx"]["target_schema"]["legal_refs"]
            self.assertIn(recipient.ref, legal_refs)
            result = loaded.act(
                "pilot:B",
                {"action_id": "choose", "aura_target": recipient.ref},
            )
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve_all(loaded))
            loaded_aura = loaded.state.cards[aura.object_id]
            self.assertEqual("battlefield", loaded_aura.zone)
            self.assertEqual("B", loaded_aura.controller)
            self.assertTrue(loaded_aura.tapped)
            self.assertEqual(recipient.object_id, loaded_aura.attached_to)
            self.assertEqual("graveyard", loaded.state.cards[aura_spell.object_id].zone)
            loaded.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_reanimation_four_player_projection_and_apnap_are_stable(self):
        session = self.session(39606, spell="Public Reanimate")
        engine = session.engine
        self.promote(engine, "Public Reanimate")
        target = self.card(engine, "B", creature=True)
        engine.move_card(target.object_id, "graveyard", log=False)
        _source, action = self.ready_spell(
            session, "Public Reanimate", {"C": 3, "B": 1}
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.cast(session, action, target.ref, {"C": 3, "B": 1})
        self.assertIsNone(self.resolve_all(session))
        for seat in engine.seats:
            rendered = json.dumps(session.packet(f"pilot:{seat}", full=True))
            self.assertIn(target.ref, rendered)
            self.assertNotIn(target.object_id, rendered)
            self.assertNotIn(target.logical_object_id, rendered)
        self.assert_replays(session, "fixed-target-reanimation-four-player")

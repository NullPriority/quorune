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
from quorune.semantics import SemanticProgram,SemanticRegistry
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
            "Return all Aura cards from your graveyard to the battlefield.",
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


class FixedTargetReanimationSetCompilerTests(unittest.TestCase):
    def test_fixed_plural_reanimation_lifts_the_scalar_predicate_and_requires_all_owners(self):
        from dataclasses import replace
        from test_qualified_zone_event_queries import query_record
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.compiler.fixed_homogeneous_target_sets import fixed_homogeneous_target_set_effect_template
        source=replace(query_record('Return up to three target creature cards with mana value 1 or less from your graveyard to the battlefield.'),
            type_line='Sorcery',power=None,toughness=None)
        registry=load_default_capability_registry()
        ir=compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status)
        node=ir.faces[0].nodes[0]
        self.assertEqual({'op':'reanimate_targets','cards':'$targets','maximum_targets':3,'schema_version':1,
            'battlefield_controller':'actor','battlefield_tapped':False},dict(node.effects[0]))
        self.assertEqual(3,node.target_schema['up_to'])
        self.assertEqual(1,node.target_schema['mana_value_max'])
        raw=json.loads(REGISTRY_PATH.read_text(encoding='utf8'))
        for dependency in ('zone.reanimate.fixed_target_set','zone.reanimate.fixed_target',
                           'resolution.effect.fixed_homogeneous_target_set','zone.change.destination_replacement'):
            changed=deepcopy(raw);row=next(row for row in changed['capabilities'] if row['id']==dependency)
            row['status']='blocked';row['blockers']=['Withdrawn target-set owner']
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=CapabilityRegistry(changed),
                capability_profile='commander_review').status)
        with patch('quorune.compiler.resolution_effect_templates.fixed_homogeneous_target_set_effect_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=registry,
                capability_profile='commander_review').status)
        for text in ('Return up to three target creature cards with total mana value 3 or less from your graveyard to the battlefield.',
                     'Return up to three target creature cards with different names from your graveyard to the battlefield.',
                     'Return up to X target creature cards from your graveyard to the battlefield.'):
            self.assertIsNone(fixed_homogeneous_target_set_effect_template(text))

    def test_simultaneous_move_intent_has_typed_entry_policy_and_preserves_ordinary_defaults(self):
        from dataclasses import FrozenInstanceError
        from quorune.public_zone_moves import PublicBattlefieldController
        from quorune.semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
        ordinary=MoveObjectsSimultaneouslyIntent('A',('ONE',),('graveyard',),'hand','Ordinary return')
        self.assertIsNone(ordinary.battlefield_controller)
        self.assertFalse(ordinary.battlefield_tapped)
        returning=MoveObjectsSimultaneouslyIntent('A',('ONE','TWO'),('graveyard',),'battlefield','Batch return',
            battlefield_controller=PublicBattlefieldController.ACTOR,battlefield_tapped=True)
        self.assertEqual(('ONE','TWO'),returning.object_refs)
        with self.assertRaises(FrozenInstanceError):returning.battlefield_tapped=False
        for changes in ({'battlefield_controller':'actor'},
                        {'battlefield_controller':PublicBattlefieldController.ACTOR,'destination':'hand'},
                        {'battlefield_tapped':1},{'battlefield_controller':None,'battlefield_tapped':True}):
            values={'actor':'A','object_refs':('ONE',),'expected_zones':('graveyard',),'destination':'battlefield',
                'reason':'Batch return','battlefield_controller':PublicBattlefieldController.OWNER,'battlefield_tapped':False}
            with self.assertRaises(ValueError):MoveObjectsSimultaneouslyIntent(**{**values,**changes})

    def test_batch_handler_validates_empty_and_nonempty_instructions_and_retains_scalar_payloads(self):
        from types import SimpleNamespace
        from quorune.semantic_runtime.fixed_target_set_handlers import ReanimateFixedTargetSetHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        from quorune.public_zone_moves import PublicBattlefieldController
        from quorune.compiler.fixed_homogeneous_target_sets import fixed_homogeneous_target_set_effect_template
        context=SimpleNamespace(actor='A',default_reason='Return fixture',
            query=SimpleNamespace(require_known_seat=lambda actor:self.assertEqual('A',actor)))
        handler=ReanimateFixedTargetSetHandler()
        effect={'op':'reanimate_targets','cards':['ONE','TWO'],'maximum_targets':3,'schema_version':1,
            'battlefield_controller':'owner','battlefield_tapped':True}
        intent=handler.lower(effect,context).intents[0]
        self.assertEqual(('ONE','TWO'),intent.object_refs)
        self.assertEqual(PublicBattlefieldController.OWNER,intent.battlefield_controller)
        self.assertTrue(intent.battlefield_tapped)
        self.assertEqual((),handler.lower({**effect,'cards':[]},context).intents)
        for changes in ({'schema_version':True},{'battlefield_tapped':1},{'battlefield_controller':'you'},
                        {'cards':['ONE','ONE']},{'cards':[],'maximum_targets':0},
                        {'cards':[],'announced_target_count':True},{'cards':[],'unknown':True}):
            with self.assertRaises(SemanticNodeError):handler.lower({**effect,**changes},context)
        scalar=fixed_target_reanimation_effect_template('Return up to one target creature card from your graveyard to the battlefield.')
        self.assertEqual(({'op':'reanimate','card':'$target.0','controller':'$controller','tapped':False},),scalar.compiled()[1])
        self.assertIsNone(fixed_homogeneous_target_set_effect_template('Return up to one target creature card from your graveyard to the battlefield.'))
        for text,controller,tapped in (
                ('Return two target creature cards from your graveyard to the battlefield.','actor',False),
                ("Put up to two target creature cards from a single graveyard onto the battlefield tapped under their owners' control.",'owner',True)):
            template=fixed_homogeneous_target_set_effect_template(text)
            self.assertIsNotNone(template)
            self.assertEqual((controller,tapped),(template.effects[0]['battlefield_controller'],template.effects[0]['battlefield_tapped']))

    def test_entry_policy_reaches_the_shared_zone_owner_without_inference(self):
        from types import SimpleNamespace
        from quorune.public_zone_moves import PublicBattlefieldController
        from quorune.semantic_choices.intent_host import SemanticChoiceIntentHostMixin
        from quorune.semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
        cards={ref:SimpleNamespace(ref=ref,object_id='object:'+ref,owner=owner)
            for ref,owner in (('ONE','B'),('TWO','C'))}
        calls=[]
        host=SimpleNamespace(_resolve_object=lambda actor,ref,**kwargs:cards[ref],
            _require_seat=lambda actor,**kwargs:self.assertEqual('A',actor),
            _move_cards_simultaneously=lambda changes,**kwargs:calls.append((changes,kwargs)))
        for policy in PublicBattlefieldController:
            intent=MoveObjectsSimultaneouslyIntent('A',('ONE','TWO'),('graveyard',),'battlefield','Batch return',
                battlefield_controller=policy,battlefield_tapped=True)
            self.assertEqual(('ONE','TWO'),SemanticChoiceIntentHostMixin.move_objects_simultaneously_intent(host,intent))
            changes,arguments=calls[-1]
            self.assertEqual([('object:ONE','battlefield'),('object:TWO','battlefield')],changes)
            self.assertEqual({'object:ONE':'B' if policy is PublicBattlefieldController.OWNER else 'A',
                'object:TWO':'C' if policy is PublicBattlefieldController.OWNER else 'A'},arguments['destination_controllers'])
            self.assertTrue(arguments['tapped'])
        ordinary=MoveObjectsSimultaneouslyIntent('A',('ONE',),('graveyard',),'hand','Ordinary return')
        SemanticChoiceIntentHostMixin.move_objects_simultaneously_intent(host,ordinary)
        self.assertNotIn('destination_controllers',calls[-1][1]);self.assertNotIn('tapped',calls[-1][1])


class FixedTargetReanimationSetNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'plural-reanimation.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/plural-reanimation-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Plural reanimation',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def closed(self,card):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),
            semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])

    def test_original_proclamation_accepts_zero_or_multiple_targets_and_keeps_owner_legality(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        for index,choose in enumerate((False,True)):
            with self.subTest(choose=choose):
                session=H.session(self,959100+index);engine=session.engine
                first=H.add(self,engine,'Memnite',zone='graveyard',ref='RETURN_MEMNITE')
                second=H.add(self,engine,'Ornithopter',zone='graveyard',ref='RETURN_ORNITHOPTER')
                enemy=H.add(self,engine,'Memnite',zone='graveyard',seat='B',ref='RETURN_ENEMY')
                expensive=H.add(self,engine,'Wall of Omens',zone='graveyard',ref='RETURN_TOO_EXPENSIVE')
                source=H.add(self,engine,'Proclamation of Rebirth',zone='hand');self.closed(source)
                action=H.ready(self,session,source,{'W':1,'C':2});H.checkpoint(self,session)
                legal=action['target_schema']['groups'][0]['legal_refs']
                self.assertIn(first.ref,legal);self.assertIn(second.ref,legal)
                self.assertNotIn(enemy.ref,legal);self.assertNotIn(expensive.ref,legal)
                before=authoritative_state_hash(engine.state)
                self.assertFalse(session.act('pilot:B',{'action_id':action['id'],'targets':[first.ref],'pay':'auto'}).ok)
                self.assertFalse(session.act('pilot:A',{'action_id':action['id'],'targets':[enemy.ref],'pay':'auto'}).ok)
                self.assertEqual(before,authoritative_state_hash(engine.state))
                targets=[first.ref,second.ref] if choose else []
                accepted=session.act('pilot:A',{'action_id':action['id'],'targets':targets,'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(H.resolve(self,session))
                for card in (first,second):
                    self.assertEqual('battlefield' if choose else 'graveyard',engine.state.cards[card.object_id].zone)
                    if choose:
                        self.assertEqual('A',engine.state.cards[card.object_id].controller)
                        self.assertFalse(engine.state.cards[card.object_id].tapped)
                self.assertEqual('graveyard',engine.state.cards[source.object_id].zone)
                self.assertEqual('graveyard',engine.state.cards[enemy.object_id].zone)
                H.replay(self,session,load=True)

    def test_original_reveillark_return_waits_for_copy_choice_then_draws_once(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.session(self,959110);engine=session.engine
        existing=H.add(self,engine,'Generic Bound Body',ref='EXISTING_COPY_BODY')
        mirror=H.add(self,engine,'Mirror Image',zone='graveyard',ref='BATCH_RETURN_MIRROR')
        wall=H.add(self,engine,'Wall of Omens',zone='graveyard',ref='BATCH_RETURN_WALL')
        source=H.add(self,engine,'Reveillark',ref='LEAVING_REVEILLARK');self.closed(source)
        secret=H.add(self,engine,'Generic Bound Growth',zone='library',ref='PRIVATE_RETURN_DRAW')
        spell=H.add(self,engine,'Murder',zone='hand');self.closed(spell)
        action=H.ready(self,session,spell,{'B':2,'C':1});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'}).ok)
        self.assertIsNotNone(H.resolve(self,session))
        self.assertEqual('graveyard',engine.state.cards[source.object_id].zone)
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','targets':[mirror.ref,wall.ref]}).ok)
        self.assertIsNotNone(H.resolve(self,session))
        self.assertEqual('replacement.order',session.state.pending_decision.kind)
        for card in (mirror,wall):self.assertEqual('graveyard',engine.state.cards[card.object_id].zone)
        self.assertEqual('library',engine.state.cards[secret.object_id].zone)
        session=H.replay(self,session,load=True);engine=session.engine
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        self.assertFalse(any(wall.ref in row['id'] for row in options))
        selected=next(row['id'] for row in options if existing.ref in row['id'] and not row['id'].startswith('decline:'))
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'choose','replacement':selected}).ok)
        self.assertFalse(session.act('pilot:A',{'action_id':'choose','replacement':'copy:'+wall.ref}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','replacement':selected}).ok)
        self.assertIsNone(H.resolve(self,session))
        for card in (mirror,wall):
            self.assertEqual('battlefield',engine.state.cards[card.object_id].zone)
            self.assertEqual('A',engine.state.cards[card.object_id].controller)
            self.assertFalse(engine.state.cards[card.object_id].tapped)
        self.assertEqual('Generic Bound Body',engine.display_name(mirror.object_id))
        self.assertEqual('hand',engine.state.cards[secret.object_id].zone)
        self.assertNotIn(secret.printed_name,str(session.packet('pilot:B',full=True)))
        H.replay(self,session,load=True)

    def test_original_sigardian_savior_cast_guard_composes_with_targeted_batch_return(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.session(self,959120);engine=session.engine
        first=H.add(self,engine,'Memnite',zone='graveyard',ref='SAVIOR_FIRST')
        second=H.add(self,engine,'Ornithopter',zone='graveyard',ref='SAVIOR_SECOND')
        source=H.add(self,engine,'Sigardian Savior',zone='hand');self.closed(source)
        action=H.ready(self,session,source,{'W':2,'C':3});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertIsNotNone(H.resolve(self,session));self.assertEqual('battlefield',engine.state.cards[source.object_id].zone)
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','targets':[first.ref,second.ref]}).ok)
        self.assertIsNone(H.resolve(self,session))
        for card in (first,second):self.assertEqual('battlefield',engine.state.cards[card.object_id].zone)
        H.replay(self,session,load=True)

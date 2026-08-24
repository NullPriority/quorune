from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session, pass_current
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.modal_templates import (
    FIXED_CHOOSE_ONE_MODAL_CAPABILITY,
    FIXED_CHOOSE_ONE_MODAL_MECHANIC,
    fixed_choose_one_modal_spell_template,
)
from quorune.deck import DeckLoader
from quorune.oracle_ir import compile_oracle_card
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from scripts.build_test_database import build_fixture_database


REAL_MODAL_CARDS = {
    "Abrade": (
        "Choose one —\n"
        "• Abrade deals 3 damage to target creature.\n"
        "• Destroy target artifact."
    ),
    "Light of Hope": (
        "Choose one —\n"
        "• You gain 4 life.\n"
        "• Destroy target enchantment.\n"
        "• Put a +1/+1 counter on target creature."
    ),
    "Cleansing Nova": (
        "Choose one —\n"
        "• Destroy all creatures.\n"
        "• Destroy all artifacts and enchantments."
    ),
    "Grixis Charm": (
        "Choose one —\n"
        "• Return target permanent to its owner's hand.\n"
        "• Target creature gets -4/-4 until end of turn.\n"
        "• Creatures you control get +2/+0 until end of turn."
    ),
    "School Daze": (
        "Choose one —\n"
        "• Do Homework — Draw three cards.\n"
        "• Fight Crime — Counter target spell. Draw a card."
    ),
    "Megaton's Fate": (
        "Choose one —\n"
        "• Disarm — Destroy target artifact. Create four Treasure tokens.\n"
        "• Detonate — Megaton's Fate deals 8 damage to each creature. "
        "Each player gets four rad counters."
    ),
    "Unforgiving Aim": (
        "Choose one —\n"
        "• Destroy target creature with flying.\n"
        "• Destroy target enchantment.\n"
        "• Create a 2/2 black and green Elf creature token."
    ),
    "You See a Pair of Goblins": (
        "Choose one —\n"
        "• Charge Them — Creatures you control get +2/+0 until end of turn.\n"
        "• Befriend Them — Create two 1/1 red Goblin creature tokens."
    ),
}


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-choose-one-modal.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "fixed-choose-one-modal-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class FixedChooseOneModalCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()

    def compile(self, name: str, oracle_text: str, *, type_line: str = "Instant"):
        return compile_oracle_card(
            CardRecord(
                oracle_id="00000000-0000-4000-8000-000000000700",
                name=name,
                mana_cost="{1}{R}",
                mana_value=2.0,
                oracle_text=oracle_text,
                type_line=type_line,
                power=None,
                toughness=None,
                loyalty=None,
                defense=None,
                colors=("R",),
                color_identity=("R",),
                keywords=(),
                produced_mana=(),
                layout="normal",
                released_at="2026-01-01",
                legalities={"commander": "legal"},
                faces=(),
                raw={},
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_actual_fixed_choose_one_corpus_is_capability_closed(self):
        for name, oracle_text in REAL_MODAL_CARDS.items():
            with self.subTest(name=name):
                ir = self.compile(name, oracle_text)
                self.assertEqual("exact", ir.status)
                self.assertEqual(1, len(ir.faces[0].nodes))
                node = ir.faces[0].nodes[0]
                self.assertTrue(
                    str(node.template_id).startswith(
                        "fixed-choose-one-modal-"
                    )
                )
                self.assertEqual((), node.effects)
                self.assertEqual(
                    FIXED_CHOOSE_ONE_MODAL_MECHANIC,
                    node.mechanics[0],
                )
                self.assertIn(
                    FIXED_CHOOSE_ONE_MODAL_CAPABILITY,
                    node.capability_dependencies,
                )
                self.assertEqual(
                    oracle_text,
                    oracle_text[node.span.start : node.span.end],
                )
                modes = node.target_schema["modes"]
                self.assertIn(len(modes), {2, 3})
                self.assertTrue(
                    all(
                        definition["effects"]
                        and definition["mechanics"]
                        for definition in modes.values()
                    )
                )

    def test_unsupported_modal_grammars_remain_material_residuals(self):
        cases = (
            (
                "Choose one —\n"
                "• Destroy target artifact.\n"
                "• Search your library for a card."
            ),
            (
                "Choose one —\n"
                "• Destroy target artifact.\n"
                "This sentence is not a mode."
            ),
            (
                "Spree\n"
                "+ {1} — Destroy target artifact.\n"
                "+ {2} — You gain 4 life."
            ),
        )
        for oracle_text in cases:
            with self.subTest(oracle_text=oracle_text):
                ir = self.compile("Unsupported Modal", oracle_text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
                self.assertFalse(
                    any(
                        str(node.template_id).startswith(
                            "fixed-choose-one-modal-"
                        )
                        for node in ir.faces[0].nodes
                    )
                )

    def test_modal_parser_and_capability_shape_fail_closed(self):
        ir = self.compile("Abrade", REAL_MODAL_CARDS["Abrade"])
        node = ir.faces[0].nodes[0]
        schema = copy.deepcopy(node.target_schema)
        mechanics = node.mechanics
        expected = set(node.capability_dependencies)
        self.assertIn(FIXED_CHOOSE_ONE_MODAL_CAPABILITY, expected)

        malformed = []
        value = copy.deepcopy(schema)
        value["mode_count"] = 2
        malformed.append((value, mechanics))
        value = copy.deepcopy(schema)
        value["modes"].pop("mode_2")
        malformed.append((value, mechanics))
        value = copy.deepcopy(schema)
        value["modes"]["destroy"] = value["modes"].pop("mode_2")
        malformed.append((value, mechanics))
        value = copy.deepcopy(schema)
        value["modes"]["mode_1"]["effects"] = []
        malformed.append((value, mechanics))
        value = copy.deepcopy(schema)
        value["modes"]["mode_1"]["mechanics"] = []
        malformed.append((value, mechanics))
        value = copy.deepcopy(schema)
        value["modes"]["mode_2"]["open_grammar"] = True
        malformed.append((value, mechanics))
        malformed.append((schema, (*mechanics, "unrepresented-mechanic")))
        malformed.append((schema, (*mechanics, mechanics[-1])))

        for target_schema, mechanic_ids in malformed:
            with self.subTest(target_schema=target_schema):
                self.assertEqual(
                    (),
                    capability_dependencies_for_node(
                        effects=(),
                        target_schema=target_schema,
                        mechanic_ids=mechanic_ids,
                    ),
                )

    def test_modal_compiler_mutation_is_killed(self):
        def assert_exact() -> None:
            ir = self.compile("Abrade", REAL_MODAL_CARDS["Abrade"])
            self.assertEqual("exact", ir.status)
            self.assertTrue(
                str(ir.faces[0].nodes[0].template_id).startswith(
                    "fixed-choose-one-modal-"
                )
            )

        assert_exact()
        with patch(
            "quorune.oracle_ir.fixed_choose_one_modal_spell_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class FixedChooseOneModalRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
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

    def session_with_modal_spell(self, *, seed: int):
        mishra = copy.deepcopy(self.mishra)
        next(
            entry
            for entry in mishra.entries
            if entry.board == "mainboard" and entry.name == "Abrade"
        ).name = "Ready to Rumble"
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
    def card(engine, *, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

    @staticmethod
    def prepare_main(session, source):
        engine = session.engine
        engine.move_card(source.object_id, "hand", controller="A", log=False)
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["A"].mana_pool.update({"C": 4, "R": 1})
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

    @staticmethod
    def cast_action(session, source):
        return next(
            action
            for action in session.packet("pilot:A", full=True)["decision"]["ctx"][
                "legal"
            ]["actions"]
            if action["id"] == f"cast:{source.ref}"
        )

    @staticmethod
    def pass_stack(session):
        while session.state.stack:
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Stack resolution stopped without priority")
            pass_current(session)

    def assert_replays(self, session, label: str):
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / label
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_compiled_modal_choice_resolves_only_selected_mode_and_replays(self):
        session = self.session_with_modal_spell(seed=811701)
        engine = session.engine
        source = self.card(engine, owner="A", name="Ready to Rumble")
        artifact = self.card(engine, owner="B", name="Sol Ring")
        creature = self.card(engine, owner="B", name="Birds of Paradise")
        for card in (artifact, creature):
            engine.move_card(card.object_id, "battlefield", controller="B", log=False)
        self.prepare_main(session, source)

        action = self.cast_action(session, source)
        schema = action["target_schema"]
        self.assertEqual(["mode_1", "mode_2"], schema["legal_modes"])
        self.assertIn(
            creature.ref,
            schema["mode_schemas"]["mode_1"]["groups"][0]["legal_refs"],
        )
        self.assertIn(
            artifact.ref,
            schema["mode_schemas"]["mode_2"]["groups"][0]["legal_refs"],
        )
        for seat in "BCD":
            self.assertNotIn(
                action["id"],
                str(session.packet(f"pilot:{seat}", full=True)),
            )

        before = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "modes": ["mode_2"],
                "targets": [creature.ref],
                "pay": "manual",
                "payment": {"C": 4, "R": 1},
            },
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        source = engine.state.cards[source.object_id]
        artifact = engine.state.cards[artifact.object_id]
        creature = engine.state.cards[creature.object_id]
        action = self.cast_action(session, source)
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "modes": ["mode_2"],
                "targets": [artifact.ref],
                "pay": "manual",
                "payment": {"C": 4, "R": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(["mode_2"], engine.state.stack[-1].modes)
        self.pass_stack(session)

        self.assertEqual("graveyard", artifact.zone)
        self.assertEqual("battlefield", creature.zone)
        self.assertEqual(0, creature.marked_damage)
        self.assertEqual("graveyard", source.zone)
        self.assert_replays(session, "fixed-choose-one-destroy-mode")

    def test_modal_target_revalidation_fizzles_without_running_other_mode(self):
        session = self.session_with_modal_spell(seed=811702)
        engine = session.engine
        source = self.card(engine, owner="A", name="Ready to Rumble")
        artifact = self.card(engine, owner="B", name="Sol Ring")
        creature = self.card(engine, owner="B", name="Birds of Paradise")
        for card in (artifact, creature):
            engine.move_card(card.object_id, "battlefield", controller="B", log=False)
        self.prepare_main(session, source)

        action = self.cast_action(session, source)
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "modes": ["mode_2"],
                "targets": [artifact.ref],
                "pay": "manual",
                "payment": {"C": 4, "R": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        engine.move_card(artifact.object_id, "hand", controller="B")
        self.pass_stack(session)

        self.assertEqual("hand", artifact.zone)
        self.assertEqual("battlefield", creature.zone)
        self.assertEqual(0, creature.marked_damage)
        self.assertEqual("graveyard", source.zone)


if __name__ == "__main__":
    unittest.main()

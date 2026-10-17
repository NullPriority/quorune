from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.bestow import BestowError, FixedManaBestowSpec
from quorune.card_programs import compile_card_program
from quorune.carddb import CardDatabase
from quorune.compiler.bestow_nodes import (
    BESTOW_CAPABILITY_ID,
    BESTOW_TEMPLATE_ID,
    fixed_mana_bestow_keyword_node,
)
from quorune.compiler.ir_model import SourceSpan
from quorune.deck import DeckLoader
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


def focused_database(directory: str) -> CardDatabase:
    path = Path(directory) / "bestow.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "bestow-cards.json",
        ],
        path,
    )
    return CardDatabase(path)


class FixedManaBestowCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def lower(self, text: str):
        residuals = []
        node = fixed_mana_bestow_keyword_node(
            node_id="front:n1",
            line=text,
            material_line=text,
            span=SourceSpan(start=0, end=len(text), line=1),
            mechanics=("bestow",),
            capability_registry=None,
            capability_profile="commander_review",
            residuals=residuals,
        )
        self.assertIsNotNone(node)
        return node, residuals

    def test_fixed_mana_bestow_lowers_complete_alternate_cast_shape(self):
        node, residuals = self.lower(
            "Bestow {3}{G} (If you cast this card for its bestow cost, "
            "it's an Aura spell with enchant creature. It becomes a creature "
            "again if it's not attached.)"
        )
        self.assertTrue(node.lowerable)
        self.assertEqual(BESTOW_TEMPLATE_ID, node.template_id)
        self.assertEqual((BESTOW_CAPABILITY_ID,), node.capability_dependencies)
        self.assertIsNone(node.cost)
        spec = FixedManaBestowSpec.from_dict(node.handlers[0]["bestow"])
        option = spec.cast_cost_option()
        self.assertEqual("bestow", option["id"])
        self.assertEqual(
            {"GENERIC": 3, "W": 0, "U": 0, "B": 0, "R": 0, "G": 1, "C": 0},
            option["requirements"],
        )
        self.assertEqual("Enchantment — Aura", option["cast_type_line"])
        self.assertEqual(
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "creature": True,
                "count": 1,
            },
            option["target_schema"],
        )
        self.assertEqual(
            [{"op": "bestow_prepare", "aura": "$card", "card": "$target.0"}],
            option["effects"],
        )
        self.assertEqual(1, len(residuals))
        self.assertIn(BESTOW_CAPABILITY_ID, residuals[0].blockers[0])

    def test_variable_bestow_remains_source_spanned_residual(self):
        text = "Bestow {X}{G}{G}"
        node, residuals = self.lower(text)
        self.assertFalse(node.lowerable)
        self.assertIsNone(node.template_id)
        self.assertIsNone(node.cost)
        self.assertEqual(1, len(residuals))
        self.assertEqual(text, residuals[0].text)
        self.assertIn("fixed ordinary-mana Bestow", residuals[0].blockers)

    def test_bestow_descriptor_rejects_unbound_or_malformed_source_data(self):
        node, _ = self.lower("Bestow {3}{G}")
        descriptor = dict(node.handlers[0]["bestow"])

        for mutation in (
            {"ability_id": "ab2"},
            {"oracle_line": 7},
            {
                "cost_text": "{4}{G}",
                "mana_cost": {
                    "GENERIC": 4,
                    "W": 0,
                    "U": 0,
                    "B": 0,
                    "R": 0,
                    "G": 1,
                    "C": 0,
                },
            },
            {"mana_cost": []},
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaises(BestowError):
                    FixedManaBestowSpec.from_dict({**descriptor, **mutation})

    def test_fixed_mana_bestow_compiles_capability_closed(self):
        record = self.db.lookup("Leafcrown Dryad")
        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertEqual("exact", ir.status)
        node = next(
            node
            for node in ir.faces[0].nodes
            if node.template_id == BESTOW_TEMPLATE_ID
        )
        self.assertTrue(node.exact)
        self.assertEqual((BESTOW_CAPABILITY_ID,), node.capability_dependencies)
        program = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        self.assertEqual((), program.residuals)
        self.assertEqual("capability_closed", program.trust_closure["trust_basis"])

    def test_bestow_compiler_mutation_is_killed(self):
        record = self.db.lookup("Leafcrown Dryad")

        def assert_exact() -> None:
            ir = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            self.assertEqual("exact", ir.status)
            self.assertTrue(
                any(
                    node.template_id == BESTOW_TEMPLATE_ID
                    for node in ir.faces[0].nodes
                )
            )

        assert_exact()
        with patch(
            "quorune.compiler.casting_keyword_nodes.fixed_mana_bestow_keyword_node",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class FixedManaBestowRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
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
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *records):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        registration = register_generated_programs(
            self.db,
            session.engine.semantics,
            records or (self.db.lookup("Leafcrown Dryad"),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertGreaterEqual(registration["programs_generated"], 1)
        return session

    @staticmethod
    def add_card(engine, name: str, *, zone: str = "hand") -> CardInstance:
        record = engine.card_db.lookup(name)
        card = CardInstance(
            object_id=f"bestow-{len(engine.state.cards) + 1}",
            ref=f"BESTOW-{len(engine.state.cards) + 1}",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="B",
            controller="B",
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=["B"] if zone == "hand" else list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players["B"].zones[zone].append(card.object_id)
        return card

    @staticmethod
    def add_target(engine) -> CardInstance:
        ref = engine.create_token(
            "B",
            name="Bestow Target",
            characteristics={
                "type_line": "Token Creature — Bear",
                "power": "1",
                "toughness": "1",
            },
        )[0]
        return engine._resolve_object("B", ref, zones={"battlefield"})

    @staticmethod
    def prepare_main(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = "B"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_player = "B"

    @staticmethod
    def resolve_top(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def cast_bestow(self, engine, card: CardInstance, target: CardInstance) -> None:
        self.prepare_main(engine)
        engine.state.players["B"].mana_pool.update({"C": 3, "G": 1})
        hints = engine._priority_action_hints("B")
        action = next(
            row for row in hints["actions"] if row["id"] == f"cast:{card.ref}"
        )
        self.assertIn(
            "bestow",
            {option["id"] for option in action["cost_options"]},
        )
        engine._cast(
            "B",
            {
                "card": card.ref,
                "cost_option": "bestow",
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"C": 3, "G": 1},
            },
        )

    def test_bestow_cast_attaches_and_applies_static_modifier(self):
        session = self.session(702_103_001)
        engine = session.engine
        dryad = self.add_card(engine, "Leafcrown Dryad")
        target = self.add_target(engine)
        self.cast_bestow(engine, dryad, target)
        self.resolve_top(engine)
        self.assertEqual(target.object_id, dryad.attached_to)
        self.assertEqual(
            {"enchantment"},
            engine._type_parts(engine._effective_card_data(dryad)["type_line"])[0],
        )
        self.assertEqual(3, engine._numeric_stat(target.object_id, "power"))
        self.assertEqual(3, engine._numeric_stat(target.object_id, "toughness"))
        self.assertIn(
            "reach",
            {
                value.casefold()
                for value in engine._effective_card_data(target)["keywords"]
            },
        )

    def test_bestow_cost_modifiers_use_aura_spell_characteristics(self):
        session = self.session(702_103_006)
        engine = session.engine
        dryad = self.add_card(engine, "Leafcrown Dryad")
        self.add_target(engine)
        self.prepare_main(engine)
        engine.state.players["B"].mana_pool.update({"C": 3, "G": 1})

        def reduction(_host, _seat, _card, *, cast_type_line=None):
            return 1 if cast_type_line == "Enchantment — Aura" else 0

        with patch(
            "quorune.rules.casting.costs._static_generic_reduction",
            side_effect=reduction,
        ):
            action = next(
                row
                for row in engine._priority_action_hints("B")["actions"]
                if row["id"] == f"cast:{dryad.ref}"
            )

        bestow = next(
            option
            for option in action["cost_options"]
            if option["id"] == "bestow"
        )
        self.assertEqual(2, bestow["requirements"]["GENERIC"])

    def test_bestow_target_loss_resolves_as_creature(self):
        session = self.session(702_103_002)
        engine = session.engine
        dryad = self.add_card(engine, "Leafcrown Dryad")
        target = self.add_target(engine)
        self.cast_bestow(engine, dryad, target)
        engine.move_card(target.object_id, "graveyard")
        self.resolve_top(engine)
        self.assertEqual("battlefield", dryad.zone)
        self.assertIsNone(dryad.attached_to)
        self.assertIn(
            "creature",
            engine._type_parts(engine._effective_card_data(dryad)["type_line"])[0],
        )

    def test_unattached_bestowed_aura_reverts_to_creature(self):
        session = self.session(702_103_003)
        engine = session.engine
        dryad = self.add_card(engine, "Leafcrown Dryad")
        target = self.add_target(engine)
        self.cast_bestow(engine, dryad, target)
        self.resolve_top(engine)
        engine.move_card(target.object_id, "graveyard")
        self.assertIsNone(dryad.attached_to)
        types = engine._type_parts(
            engine._effective_card_data(dryad)["type_line"]
        )[0]
        self.assertGreaterEqual(types, {"creature", "enchantment"})

    def test_partial_bestow_card_withholds_alternate_cost(self):
        hydra = self.db.lookup("Nyxborn Hydra")
        session = self.session(702_103_004, hydra)
        engine = session.engine
        card = self.add_card(engine, "Nyxborn Hydra")
        self.prepare_main(engine)
        hints = engine._priority_action_hints("B")
        actions = [
            row for row in hints["actions"] if row["id"] == f"cast:{card.ref}"
        ]
        if actions:
            self.assertNotIn(
                "bestow",
                {option["id"] for option in actions[0]["cost_options"]},
            )

    def test_bestow_replay_and_projection_are_identity_safe(self):
        session = self.session(702_103_005)
        engine = session.engine
        dryad = self.add_card(engine, "Leafcrown Dryad")
        target = self.add_target(engine)
        self.cast_bestow(engine, dryad, target)
        self.resolve_top(engine)
        projected = session.projector._snapshot("pilot:A")
        rendered = str(projected)
        self.assertNotIn(dryad.object_id, rendered)
        self.assertNotIn("pending_aura_target", rendered)

        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.started = True
        engine._grant_priority("D")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:D",
            {
                "action_id": "concede",
                "choices": {"confirm_concede": True},
                "plan": "REPLAY_FIXED_MANA_BESTOW",
                "reason": "Verify attached Bestow state replay.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "bestow-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


if __name__ == "__main__":
    unittest.main()

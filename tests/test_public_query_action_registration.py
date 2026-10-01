"""CR 608.2h: existing public quantities execute through normal cast authority.

A represented quantity expression must receive the same trusted registration
as the fixed effect whose canonical shape it refines. Two controlled creatures
draw two cards, not one and not the opponent's creatures. Generic fixtures use
actual offers, payment, current-trusted programs and pre-action exact replay.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
import test_bound_effect_programs as helpers
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.program_generation import generated_programs, _is_closed_effect_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database

FIXTURE = ROOT / "tests/fixtures/public-query-action-cards.json"


def quantity_card(text):
    return CardRecord(oracle_id="fixture:public-quantity-registration", name="Generic Quantity Source",
        mana_cost="{G}", mana_value=1, type_line="Sorcery", oracle_text=text,
        power=None, toughness=None, loyalty=None, defense=None, colors=("G",), color_identity=("G",),
        keywords=(), produced_mana=(), layout="normal", released_at="2026-01-01",
        legalities={"commander":"legal"}, faces=(), raw={})


class PublicQueryRegistrationShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "query-shapes.sqlite3"
        build_fixture_database([FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def program(self, text):
        programs = generated_programs(self.db, quantity_card(text), trust_level="trusted",
            capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual(1, len(programs))
        return programs[0]

    def test_existing_public_quantity_spell_shapes_are_promotable(self):
        for text in (
            "Draw a card for each creature you control.",
            "You gain 1 life for each creature you control.",
            "Generic Quantity Source deals 1 damage to any target for each creature you control.",
            "Create X 1/1 red Goblin creature tokens, where X is the number of Goblins you control.",
            "Target creature gets +X/+X until end of turn, where X is the number of creatures you control.",
        ):
            with self.subTest(text=text):
                self.assertTrue(_is_closed_effect_program(self.program(text)))

    def test_public_quantity_registration_rejects_malformed_and_missing_dependencies(self):
        valid = self.program("Draw a card for each creature you control.")
        for malformed in ("schema", "query", "unknown-operation", "extra-effect", "wrong-target"):
            program = deepcopy(valid)
            if malformed == "schema": program.effects[0]["count"]["coefficient"] = True
            elif malformed == "query": program.effects[0]["count"]["quantity"]["query"]["keywords_all"] = ["flying"]
            elif malformed == "unknown-operation": program.effects[0]["op"] = "unknown"
            elif malformed == "extra-effect": program.effects.append({"op":"unknown"})
            else: program.target_schema = {"zones":["player"], "categories":["permanent"], "count":1}
            with self.subTest(malformed=malformed):
                self.assertFalse(_is_closed_effect_program(program))
        for capability in valid.capability_dependencies:
            program = deepcopy(valid)
            program.capability_dependencies = tuple(c for c in valid.capability_dependencies if c != capability)
            with self.subTest(missing=capability):
                self.assertFalse(_is_closed_effect_program(program))


class PublicQueryActionRegistrationTests(unittest.TestCase):
    session = helpers.BoundEffectProgramRuntimeTests.session
    add = helpers.BoundEffectProgramRuntimeTests.add
    ready = helpers.BoundEffectProgramRuntimeTests.ready
    checkpoint = helpers.BoundEffectProgramRuntimeTests.checkpoint
    resolve = helpers.BoundEffectProgramRuntimeTests.resolve
    replay = helpers.BoundEffectProgramRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "query-action.sqlite3"
        build_fixture_database([helpers.FIXTURE, FIXTURE], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Generic Bound Deck", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 99),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_existing_query_draw_is_current_trusted_and_casts_with_exact_replay(self):
        session = self.session(232010); engine = session.engine
        source = self.add(engine, "Generic Count Draw", zone="hand")
        self.add(engine, "Generic Bound Body", ref="controlled-one")
        self.add(engine, "Generic Bound Body", ref="controlled-two")
        self.add(engine, "Generic Bound Body", seat="B", ref="opponent-body")
        action = self.ready(session, source, {"G": 1})
        # Revalidate the advertised payment against changed authoritative mana.
        engine.state.players["A"].mana_pool["G"] = 0
        before = authoritative_state_hash(engine.state)
        rejected = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        source = engine.state.cards[source.object_id]
        action = self.ready(session, source, {"G": 1})
        for seat in "BCD":
            self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
        self.checkpoint(session)
        old_hand = set(engine.state.players["A"].zones["hand"])
        hand = len(engine.state.players["A"].zones["hand"])
        accepted = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(hand + 1, len(engine.state.players["A"].zones["hand"]))
        self.assertEqual(0, engine.state.players["A"].mana_pool.get("G", 0))
        drawn = set(engine.state.players["A"].zones["hand"]) - old_hand
        self.assertEqual(2, len(drawn))
        for object_id in drawn:
            ref = engine.state.cards[object_id].ref
            self.assertIn(ref, json.dumps(session.packet("pilot:A", full=True)))
            for seat in "BCD":
                self.assertNotIn(ref, json.dumps(session.packet("pilot:" + seat, full=True)))
        self.replay(session, load=True)


if __name__ == "__main__":
    unittest.main()

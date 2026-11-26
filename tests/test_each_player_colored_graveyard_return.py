from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_zone_moves import PublicZoneMoveSetSpec
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record


class EachPlayerColoredGraveyardReturnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "each-player-colored-graveyards.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/each-player-colored-graveyard-return-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Owner graveyard returns",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _witnesses
    session = _witnesses.session
    add = _witnesses.add
    ready = _witnesses.ready
    checkpoint = _witnesses.checkpoint
    resolve = _witnesses.resolve
    replay = _witnesses.replay

    def test_two_color_graveyard_return_closes_the_owner_set_and_rejects_adjacent_scope(self):
        statement = "Each player returns all black and all red creature cards from their graveyard to the battlefield."
        record = replace(query_record(statement), type_line="Sorcery")
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        effect, = node.effects
        spec = PublicZoneMoveSetSpec.from_dict(effect["set"])
        self.assertEqual(("B", "R"), spec.query.colors_any)
        self.assertEqual(("creature",), spec.query.types_all)
        self.assertEqual("owner", spec.battlefield_controller.value)
        self.assertEqual("any", spec.seat_relation.value)
        self.assertIsNone(node.target_schema)
        for text in (statement.replace("Each player", "Each opponent"),
                     statement.replace("all red", "all black"),
                     statement.replace("to the battlefield", "to your hand")):
            with self.subTest(text=text):
                changed = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                              capability_profile="commander_review")
                self.assertNotEqual("exact", changed.status)

    def test_original_balthor_exiles_itself_and_returns_each_owners_two_color_set(self):
        session = self.session(283061)
        engine = session.engine
        source = self.add(engine, "Balthor the Defiled")
        matched = [self.add(engine, name, seat=seat, zone="graveyard", ref="return-" + seat)
                   for seat, name in zip("ABCD", ("Blood Artist", "Raging Goblin", "Rakdos Shred-Freak", "Raging Goblin"), strict=True)]
        wrong = [self.add(engine, "Ornithopter", seat="A", zone="graveyard"),
                 self.add(engine, "Dark Ritual", seat="D", zone="graveyard")]
        private = self.add(engine, "Generic Bound Growth", seat="C", zone="hand", ref="private-C-balthor")
        program = compile_best_available_card_program(self.db, self.db.lookup(source.printed_name, fuzzy=False),
            semantic_registry=engine.semantics, capability_registry=self.registry, capability_profile="commander_review")
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding)
        activation = self.ready(session, source, {"B": 3})
        self.assertIsNone(activation.get("target_schema"))
        self.assertFalse(activation.get("mana_ability"))
        self.assertNotIn(private.ref, str(session.packet("pilot:B", full=True)))
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:B", {"action_id": activation["id"], "pay": "auto"})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", {"action_id": activation["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("exile", session.state.cards[source.object_id].zone)
        self.assertEqual(0, session.state.players["A"].mana_pool.get("B", 0))
        self.assertEqual("activated_ability", session.state.stack[-1].kind)
        for card in matched:
            self.assertEqual("graveyard", session.state.cards[card.object_id].zone)
        session = self.replay(session, load=True)
        self.resolve(session)
        for card in matched:
            current = session.state.cards[card.object_id]
            self.assertEqual("battlefield", current.zone)
            self.assertEqual(card.owner, current.controller)
            self.assertEqual(1, session.state.players[card.owner].zones["battlefield"].count(card.object_id))
        for card in wrong:
            self.assertEqual("graveyard", session.state.cards[card.object_id].zone)
        self.assertNotIn(private.ref, str(session.packet("pilot:A", full=True)))
        self.replay(session, load=True)

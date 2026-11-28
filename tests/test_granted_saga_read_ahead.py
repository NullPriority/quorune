"""Granted Read Ahead uses the existing replacement choices and chapter owner."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.errors import GameRuleError
from quorune.keyword_abilities import normalized_effective_keywords
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.saga_progression import dispatch_saga_chapters
import test_saga_counter_progression as saga_witness
from scripts.build_test_database import build_fixture_database


class GrantedSagaReadAheadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        suite = saga_witness.SagaCounterProgressionTests
        suite.setUpClass()
        cls.directory = tempfile.TemporaryDirectory()
        path = Path(cls.directory.name) / "barbara.sqlite3"
        build_fixture_database(
            [ROOT / "tests/fixtures" / fixture for fixture in (
                "scryfall-exact-lists.json", "counter-replacement-cards.json",
                "read-ahead-saga-cards.json", "ordinary-saga-chapter-programs.json",
                "barbara-wright-pinned-cards.json",
            )],
            path,
        )
        cls.db = CardDatabase(path)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.directory.cleanup()
        saga_witness.SagaCounterProgressionTests.tearDownClass()

    def start(self):
        witness = saga_witness.SagaCounterProgressionTests()
        witness.db = self.db
        return witness, witness.session(714402, players=4)

    def entrance(self, witness, session, *, seat, name="Ordinary Saga Chapter Fixture"):
        engine = session.engine
        card = witness.add_permanent(engine, seat=seat, name=name, ref="printed-ordinary-saga")
        engine.state.players[seat].zones["battlefield"].remove(card.object_id)
        card.zone = "stack"
        witness.begin_read_ahead_entry(session, card)
        return card

    def test_granted_read_ahead_choice_and_replay(self):
        witness, session = self.start()
        engine = session.engine
        witness.add_permanent(engine, seat="C", name="Barbara Wright", ref="granted-read-ahead")
        saga = self.entrance(witness, session, seat="C")
        self.assertEqual("stack", saga.zone)
        for seat in "ABD":
            self.assertIsNone(session.packet(f"pilot:{seat}", full=True)["decision"])
        _decision, options = witness.replacement_options(session, "C")
        self.assertEqual({"1", "2", "3"}, {label.rsplit(":", 1)[-1] for label in options})
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        prior = authoritative_state_hash(session.state)
        rejected = session.act("pilot:C", {"action_id": "choose", "choices": {"replacement": "forged"}})
        self.assertFalse(rejected.ok)
        self.assertEqual(prior, authoritative_state_hash(session.state))
        witness.choose_replacement(session, "C", next(value for value in options if value.endswith(":chapter:3")))
        saga = session.state.cards[saga.object_id]
        self.assertEqual("battlefield", saga.zone)
        self.assertEqual(3, saga.counters["lore"])
        self.assertIn("read ahead", normalized_effective_keywords(engine, saga))
        labels = [item.semantic_key for item in engine.state.stack]
        labels.extend(item.semantic_key for batch in engine.state.pending_trigger_batches for item in batch.items)
        programs = [engine.semantics.get(key).event for key in labels if key in engine.semantics.keys()]
        self.assertEqual(["saga.chapter.3"], programs)
        for _ in range(12):
            if not engine.state.stack and not engine.state.pending_trigger_batches:
                break
            principal, = session.pending_principals()
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", engine.state.cards[saga.object_id].zone)
        with tempfile.TemporaryDirectory() as path:
            path = Path(path) / "granted-read-ahead-replay"
            session.save(path)
            replay = replay_record(path, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(authoritative_state_hash(session.state), replay["final_state_hash"])

    def test_grant_respects_controller_and_current_ability(self):
        for name in ("different_controller", "phased_grant"):
            with self.subTest(name=name):
                witness, session = self.start()
                engine = session.engine
                source = witness.add_permanent(engine, seat="A", name="Barbara Wright", ref="read-ahead-grant")
                source.phased_out = name == "phased_grant"
                saga = self.entrance(witness, session, seat="C" if name == "different_controller" else "A")
                self.assertEqual("battlefield", saga.zone)
                self.assertEqual(1, saga.counters["lore"])
                self.assertNotIn("read ahead", normalized_effective_keywords(engine, saga))

    def test_entry_turn_checks_current_lore_and_retains_version_one(self):
        for version, expected in ((1, (2, 3)), (2, (3,))):
            with self.subTest(version=version):
                witness, session = self.start()
                engine = session.engine
                engine.state.saga_intrinsic_ability_version = version
                witness.add_permanent(engine, seat="A", name="Barbara Wright", ref="read-ahead-grant")
                saga = witness.add_permanent(engine, seat="A", name="Ordinary Saga Chapter Fixture", ref="read-ahead-recip")
                self.assertIn("read ahead", normalized_effective_keywords(engine, saga))
                saga.entered_battlefield_turn_sequence = engine.state.turn_sequence
                saga.counters["lore"] = 3
                items = []
                dispatch_saga_chapters(engine, saga, previous_lore=1, trigger_batch=items)
                self.assertEqual(expected, tuple(int(item.context["chapter"]) for item in items))

    def test_missing_granted_entry_producer_fails_closed(self):
        witness, session = self.start()
        engine = session.engine
        witness.add_permanent(engine, seat="A", name="Barbara Wright", ref="read-ahead-grant")
        with patch("quorune.semantic_runtime.zone_replacement_inputs.granted_read_ahead_entry_chapters", return_value=()):
            with self.assertRaisesRegex(GameRuleError, "requires one trusted typed handler"):
                self.entrance(witness, session, seat="A")

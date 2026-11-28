"""Native original Saga, private searches and current linked exile incarnations."""
from pathlib import Path
from unittest.mock import patch
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.model import CardInstance
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.saga_progression import dispatch_saga_chapters
from quorune.trigger_processing import enqueue_trigger_batch
from scripts.build_test_database import build_fixture_database
import test_saga_counter_progression as saga_witness


class SagaLinkedLibraryNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        owner = saga_witness.SagaCounterProgressionTests
        owner.setUpClass()
        cls.temporary = tempfile.TemporaryDirectory()
        db_path = Path(cls.temporary.name) / "creation.sqlite3"
        build_fixture_database(
            [ROOT / "tests/fixtures" / fixture for fixture in (
                "scryfall-exact-lists.json", "counter-replacement-cards.json",
                "ordinary-saga-chapter-programs.json", "station-symbol-cards.json",
                "creation-avacyn-pinned-cards.json",
            )], db_path,
        )
        cls.db = CardDatabase(db_path)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()
        saga_witness.SagaCounterProgressionTests.tearDownClass()

    def start(self):
        self.witness = saga_witness.SagaCounterProgressionTests()
        self.witness.db = self.db
        self.session = self.witness.session(607303, players=4)
        self.engine = self.session.engine

    def add_private(self, name, ref):
        printed = self.db.lookup(name, fuzzy=False)
        card = CardInstance(
            object_id=f"linked-library:{ref}", ref=ref, oracle_id=printed.oracle_id,
            printed_name=printed.name, owner="A", controller="A", zone="library",
            zone_timestamp=self.engine._next_zone_timestamp(), known_to=[], revealed_to=[],
        )
        self.engine.state.cards[card.object_id] = card
        self.engine.state.players["A"].zones["library"].append(card.object_id)
        return card

    def chapter(self, saga, number, copies=0):
        self.engine.permissions.invalidate_current()
        self.engine.state.pending_decision = None
        self.engine.state.priority_player = None
        self.engine.state.priority_passes = []
        saga.counters["lore"] = number
        batch = []
        dispatch_saga_chapters(self.engine, saga, previous_lore=number - 1, trigger_batch=batch)
        trigger, = batch
        enqueue_trigger_batch(self.engine, batch)
        self.engine._grant_priority("A")
        current = next(value for value in self.engine.state.stack if value.ref == trigger.ref)
        for _ in range(copies):
            self.engine._copy_stack_item(
                controller="A", target=current, targets=[], target_groups={}, reason="Native copied chapter fixture",
            )
        if copies:
            self.engine.permissions.invalidate_current()
            self.engine.state.pending_decision = None
            self.engine.state.priority_player = None
            self.engine.state.priority_passes = []
            self.engine._grant_priority("A")
        self.engine.pump()
        self.session.initial_checkpoint = checkpoint_envelope(self.session.state)
        self.session.commands.clear()
        self.session.decisions.clear()

    def pass_priority(self):
        principal, = self.session.pending_principals()
        result = self.session.act(principal, {"action_id": "pass"})
        self.assertTrue(result.ok, result.summary)

    def decision(self):
        for _ in range(20):
            pending = self.engine.state.pending_decision
            if pending is not None and pending.kind != "priority":
                return pending
            if not self.engine.state.stack:
                return None
            self.pass_priority()
        self.fail("Linked Saga chapter did not settle")

    def choose(self, choice):
        principal, = self.session.pending_principals()
        stopped = self.session.act(principal, choice)
        self.assertTrue(stopped.ok, stopped.summary)

    def replay(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "linked-saga-library-record"
            self.session.save(path)
            result = replay_record(path, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(authoritative_state_hash(self.engine.state), result["final_state_hash"])

    def test_original_linked_saga_copies_search_reveals_and_partitions_all_current_cards(self):
        self.start()
        saga = self.witness.add_permanent(
            self.engine, seat="A", name="The Creation of Avacyn", ref="native-creation",
        )
        other = self.witness.add_permanent(self.engine, seat="A", name="The Creation of Avacyn", ref="other-creation")
        creature = self.add_private("Centaur Courser", "private-creature")
        permanent = self.add_private("Arcane Signet", "private-artifact")
        spell = self.add_private("Divination", "private-spell")
        self.chapter(saga, 1, copies=2)
        for candidate in (creature, permanent, spell):
            self.assertEqual("semantic.search", self.decision().kind)
            for seat in "BCD":
                self.assertIsNone(self.session.packet(f"pilot:{seat}", full=True)["decision"])
            self.choose({"action_id": "choose", "search_cards": [candidate.ref]})
            self.assertEqual("exile", candidate.zone)
            self.assertTrue(candidate.face_down)
            self.assertIn("A", candidate.known_to)
            self.assertNotIn("B", candidate.known_to)
            public_search = next(event for event in reversed(self.engine.state.events) if event.code == "library.search")
            self.assertNotIn(candidate.ref, public_search.details.get("objects", ()))
        self.assertIsNone(self.decision())
        self.replay()

        self.chapter(other, 2)
        self.assertIsNone(self.decision())
        self.assertEqual(40, self.engine.state.players["A"].life)
        self.assertTrue(all(value.face_down for value in (creature, permanent, spell)))

        self.chapter(saga, 2)
        self.assertIsNone(self.decision())
        self.assertEqual(32, self.engine.state.players["A"].life)
        self.assertTrue(all(not value.face_down for value in (creature, permanent, spell)))
        self.replay()

        self.chapter(saga, 3)
        self.assertEqual("semantic.choice", self.decision().kind)
        self.choose({"action_id": "choose", "choice": "apply"})
        self.assertIsNone(self.decision())
        self.assertEqual(("battlefield", "battlefield", "hand"), (creature.zone, permanent.zone, spell.zone))
        self.assertEqual("graveyard", saga.zone)
        self.replay()

    def test_complete_source_triplet_is_required(self):
        from quorune.oracle_ir import compile_oracle_card

        record = self.db.lookup("The Creation of Avacyn", fuzzy=False)
        capabilities = saga_witness.SagaCounterProgressionTests.capabilities
        with patch("quorune.compiler.saga_chapter_grants._linked_library_exile_chapter_plan", return_value=None):
            ir = compile_oracle_card(record, capability_registry=capabilities, capability_profile="commander_review")
        self.assertNotEqual("exact", ir.status)
        self.assertEqual(3, len(ir.material_residuals))

    def test_unrevealed_linked_card_goes_to_owner_without_type_disclosure(self):
        self.start()
        saga = self.witness.add_permanent(
            self.engine, seat="A", name="The Creation of Avacyn", ref="native-creation-hidden",
        )
        card = self.add_private("Centaur Courser", "native-hidden-creature")
        self.chapter(saga, 1)
        self.assertEqual("semantic.search", self.decision().kind)
        self.choose({"action_id": "choose", "search_cards": [card.ref]})
        self.assertIsNone(self.decision())
        self.assertTrue(card.face_down)

        self.chapter(saga, 3)
        self.assertIsNone(self.decision())
        self.assertEqual("hand", card.zone)
        self.assertNotIn("B", card.known_to)
        self.assertEqual(40, self.engine.state.players["A"].life)
        self.replay()

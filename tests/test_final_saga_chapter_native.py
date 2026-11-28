from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.engine import TURN_STEPS
from quorune.model import CardInstance, GameConfig
from quorune.oracle_ir import register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.saga_lifecycle import FINAL_SAGA_CHAPTER_RESOLVED_EVENT
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


class FinalSagaChapterNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        db_path = Path(cls.temporary.name) / "test.sqlite3"
        build_fixture_database(
            [ROOT / "tests/fixtures/bound-effect-program-cards.json",
             ROOT / "tests/fixtures/final-saga-chapter-pinned-cards.json"], db_path,
        )
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def start(self, seats="AB"):
        deck = DeckDefinition("Final chapter native witness", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])
        session = CommanderSession.create(
            self.db, {seat: deepcopy(deck) for seat in seats}, first_player="A", seed=714608,
            config=GameConfig(seed=714608, auto_pass_empty_priority=False),
        )
        keep_all(session)
        self.session = session
        self.engine = session.engine
        self.engine.permissions.invalidate_current()
        self.engine.state.pending_decision = None
        self.engine.state.priority_player = None
        self.engine.state.priority_passes = []
        self.engine.state.started = True
        self.engine.state.active_player = "A"
        self.engine.state.phase = "precombat_main"
        self.engine.state.step = "main"
        self.engine.state.phase_index = TURN_STEPS.index(("precombat_main", "main"))
        return session

    def add(self, name, ref, seat="A", zone="battlefield"):
        record = self.db.lookup(name, fuzzy=False)
        register_generated_programs(
            self.db, self.engine.semantics, (record,),
            capability_registry=self.registry, capability_profile="commander_review",
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True,
        )
        visible = list(self.engine.seats) if zone != "library" else []
        card = CardInstance(
            object_id=f"final-chapter:{ref}", ref=ref, oracle_id=record.oracle_id,
            printed_name=record.name, owner=seat, controller=seat, zone=zone,
            zone_timestamp=self.engine._next_zone_timestamp(), known_to=visible, revealed_to=visible,
        )
        self.engine.state.cards[card.object_id] = card
        self.engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def build_chapter(self, saga, previous):
        from quorune.saga_progression import dispatch_saga_chapters
        from quorune.trigger_processing import enqueue_trigger_batch

        saga.counters["lore"] = previous + 1
        batch = []
        dispatch_saga_chapters(self.engine, saga, previous_lore=previous, trigger_batch=batch)
        self.assertEqual(len(batch), 1)
        enqueue_trigger_batch(self.engine, batch)
        return batch[0]

    def priority(self):
        self.engine._grant_priority("A")
        self.engine.pump()

    def pass_priority(self):
        principal, = self.session.pending_principals()
        result = self.session.act(principal, {"action_id": "pass"})
        self.assertTrue(result.ok, result.summary)

    def pass_until_gone(self, ref):
        for _ in range(12):
            if not any(item.ref == ref for item in self.engine.state.stack):
                return
            self.pass_priority()
        self.fail(f"{ref} did not resolve")

    def observe(self):
        return [item for item in self.engine.state.stack
                if item.context.get("event") == FINAL_SAGA_CHAPTER_RESOLVED_EVENT]

    def replay(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "final-saga-chapter-record"
            self.session.save(path)
            replay = replay_record(path, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(authoritative_state_hash(self.engine.state), replay["final_state_hash"])

    def test_tom_bombadil_final_chapter_reveals_first_saga_once_and_replays(self):
        self.start()
        tom = self.add("Tom Bombadil", "native-tom")
        source = self.add("Final Chapter Test Saga", "native-source")
        suffix = self.add("Generic Bound Plains", "native-private-suffix", zone="library")
        next_saga = self.add("Final Chapter Short Saga", "native-first-saga", zone="library")
        revealed = self.add("Generic Bound Plains", "native-revealed-prefix", zone="library")
        chapter = self.build_chapter(source, 1)
        self.priority()
        self.session.initial_checkpoint = checkpoint_envelope(self.session.state)
        self.session.commands.clear()
        self.session.decisions.clear()
        self.pass_until_gone(chapter.ref)
        first, = self.observe()
        self.assertEqual(first.source_object_id, tom.object_id)
        self.assertEqual(source.zone, "graveyard")
        self.pass_until_gone(first.ref)
        self.assertEqual(next_saga.zone, "battlefield")
        self.assertEqual(self.engine.state.players["A"].zones["library"][0], revealed.object_id)
        self.assertFalse("B" in suffix.known_to, "Uninspected suffix must remain private")
        short_chapter, = self.engine.state.stack
        self.pass_until_gone(short_chapter.ref)
        self.assertEqual([], self.observe())
        self.assertEqual(next_saga.zone, "graveyard")
        self.replay()

    def test_earlier_countered_or_departed_chapter_does_not_trigger(self):
        for scenario in ("earlier", "countered", "departed"):
            with self.subTest(scenario=scenario):
                self.start()
                self.add("Tom Bombadil", "native-tom")
                saga = self.add("Final Chapter Test Saga", "native-saga")
                chapter = self.build_chapter(saga, 0 if scenario == "earlier" else 1)
                self.priority()
                if scenario == "countered":
                    self.engine._counter_stack_item(chapter.ref, as_rule=True)
                    self.engine._stabilize()
                else:
                    if scenario == "departed":
                        self.engine.move_card(saga.object_id, "graveyard", reason="Fixture departure")
                    self.pass_until_gone(chapter.ref)
                self.assertEqual([], self.observe())

    def test_current_saga_controller_is_distinct_from_chapter_controller(self):
        self.start("ABCD")
        self.add("Tom Bombadil", "native-other-tom")
        tom = self.add("Tom Bombadil", "native-tom", seat="C")
        saga = self.add("Final Chapter Test Saga", "native-saga")
        chapter = self.build_chapter(saga, 1)
        self.assertEqual(chapter.controller, "A")
        self.engine.state.players["A"].zones["battlefield"].remove(saga.object_id)
        self.engine.state.players["C"].zones["battlefield"].append(saga.object_id)
        saga.controller = "C"
        self.priority()
        self.pass_until_gone(chapter.ref)
        trigger, = self.observe()
        self.assertEqual((trigger.controller, trigger.source_object_id), ("C", tom.object_id))

    def test_final_chapter_returned_observer_and_state_actions(self):
        self.start()
        returned = self.add("Tom Bombadil", "native-returned", zone="graveyard")
        saga = self.add("Final Chapter Return Saga", "native-saga")
        chapter = self.build_chapter(saga, 1)
        self.priority()
        pending = self.engine.state.pending_decision
        self.assertEqual(pending.kind, "semantic.target")
        result = self.session.act("pilot:A", {"action_id": "choose", "targets": [returned.ref]})
        self.assertTrue(result.ok, result.summary)
        self.pass_until_gone(chapter.ref)
        first, = self.observe()
        self.assertEqual(first.source_object_id, returned.object_id)
        self.assertEqual(saga.zone, "graveyard")

    def test_tom_bombadil_counts_only_lore_on_current_controlled_sagas(self):
        self.start()
        tom = self.add("Tom Bombadil", "native-tom")
        first = self.add("Final Chapter Test Saga", "native-saga-first")
        second = self.add("Final Chapter Test Saga", "native-saga-second")
        opposing = self.add("Final Chapter Test Saga", "native-saga-opposing", seat="B")
        tom.counters["lore"] = 20
        opposing.counters["lore"] = 20
        first.counters.update({"lore": 2, "+1/+1": 20})
        second.counters["lore"] = 1
        protected = {"hexproof", "indestructible"}
        def abilities():
            return {value.casefold() for value in self.engine._effective_card_data(tom)["keywords"]}
        self.assertTrue(protected.isdisjoint(abilities()))
        second.counters["lore"] = 2
        self.assertTrue(protected <= abilities())
        second.phased_out = True
        self.assertTrue(protected.isdisjoint(abilities()))

    def test_missing_committed_completion_suppresses_the_observer(self):
        self.start()
        self.add("Tom Bombadil", "native-tom")
        saga = self.add("Final Chapter Test Saga", "native-saga")
        chapter = self.build_chapter(saga, 1)
        self.priority()
        with patch("quorune.saga_progression.dispatch_resolved_final_saga_chapter"):
            self.pass_until_gone(chapter.ref)
        self.assertEqual([], self.observe())

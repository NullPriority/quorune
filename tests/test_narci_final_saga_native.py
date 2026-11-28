"""Original Narci composes final-chapter mana value with the Saga sacrifice trigger."""
import unittest

from quorune.record import checkpoint_envelope
from quorune.saga_lifecycle import FINAL_SAGA_CHAPTER_RESOLVED_EVENT
import test_final_saga_chapter_native as saga_witness


class NarciFinalSagaNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        saga_witness.FinalSagaChapterNativeTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        saga_witness.FinalSagaChapterNativeTests.tearDownClass()

    def test_original_narci_uses_saga_mana_value_and_draws_after_sacrifice(self):
        witness = saga_witness.FinalSagaChapterNativeTests()
        session = witness.start("ABCD")
        narci = witness.add("Narci, Fable Singer", "native-narci")
        saga = witness.add("Final Chapter Test Saga", "native-saga")
        draw = witness.add("Generic Bound Plains", "native-chapter-draw", zone="library")
        chapter = witness.build_chapter(saga, 1)
        witness.priority()
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        witness.pass_until_gone(chapter.ref)

        self.assertEqual("graveyard", saga.zone)
        decision = session.state.pending_decision
        self.assertEqual("trigger.order", decision.kind)
        available = decision.payload_by_actor["A"]["triggers"]
        self.assertEqual(2, len(available))
        result = session.act("pilot:A", {"action_id": "order", "order": [row["id"] for row in available]})
        self.assertTrue(result.ok, result.summary)
        triggered = [item for item in session.state.stack if item.source_object_id == narci.object_id]
        self.assertEqual(2, len(triggered))
        event, = [item for item in triggered if item.context["event"] == FINAL_SAGA_CHAPTER_RESOLVED_EVENT]
        self.assertEqual(3, event.context["mana_value"])
        self.assertNotEqual(saga.logical_object_id, event.context["card_object_identity"])
        for item in reversed(triggered):
            witness.pass_until_gone(item.ref)

        self.assertEqual([45, 37, 37, 37], [session.state.players[seat].life for seat in "ABCD"])
        self.assertEqual("hand", draw.zone)
        self.assertNotIn("B", draw.known_to)
        witness.replay()

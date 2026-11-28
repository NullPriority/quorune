"""Current flying controls a nontargeted Saga counter batch."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.counter_placement_templates import fixed_counter_placement_set_effect_template
from quorune.counter_placement_sets import controlled_nonflying_counter_payload_dependencies
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class SagaNonflyingCounterSetTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "saga-nonflying.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "counter-replacement-cards.json",
            fixtures / "public-negative-keyword-cards.json",
            fixtures / "reflexive-payment-cards.json",
            fixtures / "saga-nonflying-counter-set-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Saga counter batch", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_only_the_closed_flying_counter_and_control_scope_bind(self):
        from quorune.rules.counter_placement_capability_shapes import fixed_counter_placement_set_node_capabilities

        template = fixed_counter_placement_set_effect_template(
            "Put a flying counter on each creature you control without flying."
        )
        self.assertIsNotNone(template)
        self.assertIsNone(template.target_schema)
        self.assertEqual(("flying",), template.spec.query.keywords_none)
        effect, = template.effects
        self.assertIn("state_query.permanent.public_state_predicate",
                      controlled_nonflying_counter_payload_dependencies(effect))
        for wording in (
            "Put a +1/+1 counter on each creature you control without flying.",
            "Put two flying counters on each creature you control without flying.",
            "Put a flying counter on each creature without flying.",
            "Put a flying counter on each creature you control without vigilance.",
        ):
            with self.subTest(wording=wording):
                self.assertIsNone(fixed_counter_placement_set_effect_template(wording))
        bad_set = replace(template.spec, exclude_source=True).to_dict()
        for mutated in (
            {**effect, "set": bad_set},
            {**effect, "counter": "+1/+1"},
            {**effect, "amount": 2},
        ):
            with self.subTest(mutated=mutated):
                with self.assertRaises(ValueError):
                    controlled_nonflying_counter_payload_dependencies(mutated)
                self.assertEqual((), fixed_counter_placement_set_node_capabilities(
                    effects=(mutated,), target_schema=None, mechanic_ids=template.mechanics,
                ))

    def test_original_chapters_use_current_flying_and_replay_the_counter_batch(self):
        session = self.session(714613901)
        source = self.add(session.engine, "Song of Eärendil", zone="hand")
        ground = self.add(session.engine, "Generic Bound Body", ref="song-current-ground")
        ground2 = self.add(session.engine, "Generic Bound Body", ref="song-second-current-ground")
        counter = self.add(session.engine, "Generic Bound Body", ref="song-existing-counter")
        counter.counters["flying"] = 1
        grants = self.add(session.engine, "Generic Bound Body", ref="song-gets-flying-in-response")
        opposing = self.add(session.engine, "Generic Bound Body", seat="B", ref="song-foreign-ground")
        jump = self.add(session.engine, "Jump", zone="hand")
        doubler = self.add(session.engine, "Doubling Season", zone="hand")
        second = self.add(session.engine, "Generic Reflexive Lore", zone="hand", ref="song-second-lore")
        third = self.add(session.engine, "Generic Reflexive Lore", zone="hand", ref="song-third-lore")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 7, "G": 2, "U": 2})
        self.checkpoint(session)
        top_ids = session.state.players["A"].zones["library"][-2:]
        scry_refs = [session.state.cards[key].ref for key in reversed(top_ids)]

        self.cast(session, "A", source)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        for seat in "BCD":
            foreign = json.dumps(session.packet("pilot:" + seat, full=True))
            self.assertTrue(all(ref not in foreign for ref in scry_refs))
        session = self.replay(session, load=True)
        accepted = session.act("pilot:A", {"action_id": "choose", "cards": {"top": scry_refs, "bottom": []}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(set(top_ids) <= set(session.state.players["A"].zones["hand"]))

        self.cast(session, "A", second, [source.ref])
        self.assertIsNone(self.resolve(session))
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(2, len(tokens))
        bird, = [card for card in tokens if "flying" in session.engine._combat_keywords(card)]
        treasure, = [card for card in tokens if card is not bird]
        self.assertEqual((2, 2), tuple(session.engine._numeric_stat(bird.object_id, stat)
                                      for stat in ("power", "toughness")))

        self.cast(session, "A", doubler)
        self.assertIsNone(self.resolve(session))
        self.cast(session, "A", third, [source.ref])
        self.resolve_top(session)
        self.assertEqual(source.object_id, session.state.stack[-1].source_object_id)
        self.assertFalse(session.state.stack[-1].targets)
        self.cast(session, "A", jump, [grants.ref])
        self.resolve_top(session)
        self.assertIn("flying", session.engine._combat_keywords(session.state.cards[grants.object_id]))
        pending = authoritative_state_hash(session.state)
        session = self.replay(session, load=True)
        self.assertEqual(pending, authoritative_state_hash(session.state))
        self.assertIsNone(self.resolve(session))

        for creature in (ground, ground2):
            row = session.state.cards[creature.object_id]
            self.assertEqual(2, row.counters["flying"])
            self.assertIn("flying", session.engine._combat_keywords(row))
        self.assertEqual(1, session.state.cards[counter.object_id].counters["flying"])
        for untouched in (grants, opposing, bird, treasure):
            self.assertEqual(0, session.state.cards[untouched.object_id].counters.get("flying", 0))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.replay(session)

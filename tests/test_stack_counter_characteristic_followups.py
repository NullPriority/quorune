"""Counter followups use current traits of the original legal stack spell."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.ring_state import current_ring_state
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.stack_counter_followup import STACK_CHARACTERISTIC_FOLLOWUP_VERSION
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class StackCounterCharacteristicFollowupTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "stack-counter-characteristic-followups.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/stack-counter-characteristic-followups-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Original spell characteristics", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_admit_only_the_fixed_target_and_trailing_result(self):
        for name, condition, altered in (
            ("Glorious Gale", "legendary_creature_spell", "legendary spell"),
            ("Jace's Defeat", "jace_planeswalker_spell", "Jace planeswalker spell"),
        ):
            with self.subTest(name=name):
                admission, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(admission["strict_capability_ready"], admission["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                spell, = semantics.programs_for_oracle(card.oracle_id)
                effect, = spell.effects
                self.assertEqual((STACK_CHARACTERISTIC_FOLLOWUP_VERSION, condition),
                                 (effect["schema_version"], effect["subject"]))
                for changed in (
                    replace(spell, effects=[{**effect, "matched": True}]),
                    replace(spell, effects=[{**effect, "followups": []}]),
                    replace(spell, event="step.begin"),
                ):
                    self.assertFalse(bind_semantic_program_runtime(
                        changed, capability_registry=self.registry, profile="commander_review",
                    )["strict"])
                changed_text = card.oracle_text.replace(altered, "green artifact spell")
                self.assertNotEqual("exact", compile_oracle_card(
                    replace(card, oracle_text=changed_text), capability_registry=self.registry,
                    capability_profile="commander_review",
                ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def test_actual_uncounterable_legendary_creature_still_qualifies_the_ring(self):
        game = self.session(70160601)
        source = self.add(game.engine, "Glorious Gale", zone="hand")
        spell = self.add(game.engine, "Thrun, Breaker of Silence", seat="B", zone="hand")
        bearer = self.add(game.engine, "Grizzly Bears")
        for card in (source, spell):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"C": 3, "G": 2})
        game.state.players["A"].mana_pool.update({"C": 1, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", spell)
        target = game.state.stack[-1]
        self.cast_for(game, "A", source, [target.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("stack", self.current(game, spell).zone)
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        response = {"action_id": "choose", "objects": [bearer.ref]}
        self.assertFalse(game.act("pilot:B", response).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", response)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", self.current(game, spell).zone)
        ring = current_ring_state(game.engine, "A")
        self.assertEqual((1, bearer.object_id), (ring.temptation_count, ring.bearer.identity.object_id))
        self.replay(game, load=True)

    def test_actual_nonlegendary_stack_copy_uses_its_own_traits_and_original_still_qualifies(self):
        game = self.session(70160602)
        remove_copy = self.add(game.engine, "Glorious Gale", zone="hand", ref="gale-targeting-copy")
        remove_original = self.add(game.engine, "Glorious Gale", zone="hand", ref="gale-targeting-original")
        original = self.add(game.engine, "Isamaru, Hound of Konda", seat="B", zone="hand", ref="source-legendary-dog")
        double = self.add(game.engine, "Double Major", seat="B", zone="hand")
        for card in (remove_copy, remove_original, double):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"W": 1, "G": 1, "U": 1})
        game.state.players["A"].mana_pool.update({"C": 2, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "B", original, reserve={"G": 1, "U": 1})
        stack_original = game.state.stack[-1]
        self.cast_for(game, "B", double, [stack_original.ref])
        self.resolve_top(game)
        copied, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertNotIn("Legendary", game.engine._target_snapshot(copied.ref)["type_line"])
        self.assertIn("Legendary", game.engine._target_snapshot(stack_original.ref)["type_line"])
        self.cast_for(game, "A", remove_copy, [copied.ref], reserve={"C": 1, "U": 1})
        self.resolve_top(game)
        self.assertIsNone(current_ring_state(game.engine, "A"))
        self.assertFalse(any(item.ref == copied.ref for item in game.state.stack))
        self.assertEqual("stack", self.current(game, original).zone)
        self.cast_for(game, "A", remove_original, [stack_original.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.current(game, original).zone)
        self.assertEqual(1, current_ring_state(game.engine, "A").temptation_count)
        self.replay(game, load=True)

    def test_actual_original_target_removed_in_response_prevents_entire_ring_instruction(self):
        game = self.session(70160603)
        gale = self.add(game.engine, "Glorious Gale", zone="hand")
        original = self.add(game.engine, "Isamaru, Hound of Konda", seat="B", zone="hand", ref="departed-legendary-dog")
        counter = self.add(game.engine, "Counterspell", seat="C", zone="hand")
        self.prepare(game, actor="B", active="B", mana={"W": 1})
        game.state.players["A"].mana_pool.update({"C": 1, "U": 1})
        game.state.players["C"].mana_pool["U"] = 2
        self.assert_closed(game, gale)
        self.assert_closed(game, counter)
        self.checkpoint(game)
        self.cast_for(game, "B", original)
        stack = game.state.stack[-1]
        self.cast_for(game, "A", gale, [stack.ref])
        self.cast_for(game, "C", counter, [stack.ref])
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.current(game, original).zone)
        self.assertIsNone(current_ring_state(game.engine, "A"))
        self.replay(game, load=True)

    def test_actual_blue_stack_target_gets_private_scry_only_for_a_jace_planeswalker(self):
        for name, qualifies in (("Jace Beleren", True), ("Wind Drake", False)):
            with self.subTest(name=name):
                game = self.session(70160604 + qualifies)
                defeat = self.add(game.engine, "Jace's Defeat", zone="hand")
                spell = self.add(game.engine, name, seat="B", zone="hand")
                for card in (defeat, spell):
                    self.assert_closed(game, card)
                self.prepare(game, actor="B", active="B", mana={"C": 2, "U": 2})
                game.state.players["A"].mana_pool.update({"C": 1, "U": 1})
                self.checkpoint(game)
                self.cast_for(game, "B", spell)
                stack = game.state.stack[-1]
                self.cast_for(game, "A", defeat, [stack.ref])
                pending = self.resolve(game)
                self.assertEqual("graveyard", self.current(game, spell).zone)
                top_ids = game.state.players["A"].zones["library"][-2:]
                refs = [game.state.cards[object_id].ref for object_id in reversed(top_ids)]
                if qualifies:
                    self.assertEqual(["A"], pending.actors)
                    self.assertIn(refs[0], json.dumps(game.packet("pilot:A", full=True)["decision"]))
                    for viewer in ("pilot:B", "pilot:C", "spectator"):
                        packet = game.packet(viewer, full=True)
                        self.assertIsNone(packet["decision"])
                        self.assertTrue(all(ref not in json.dumps(packet) for ref in refs))
                    game = self.replay(game, load=True)
                    response = {"action_id": "choose", "cards": {"top": list(reversed(refs)), "bottom": []}}
                    before = authoritative_state_hash(game.state)
                    self.assertFalse(game.act("pilot:B", response).ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                    result = game.act("pilot:A", response)
                    self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual(list(reversed(top_ids)), game.state.players["A"].zones["library"][-2:])
                else:
                    self.assertIsNone(pending)
                self.replay(game, load=True)

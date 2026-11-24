"""Resolve Aether Snap through the ordered all-counter and token results."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Aether Snap"
VERSION_FIELD = "all_permanents_then_token_exile_version"


class AllPermanentCounterRemovalAndTokenExileTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "global-counters-and-token-exile.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/all-permanent-counter-removal-and-token-exile-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Aether Snap witness", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def source_program(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "resolve"]
        return card, program

    def test_entire_original_registers_an_untargeted_ordered_global_result(self):
        card, program = self.source_program()
        self.assertIsNone(program.target_schema)
        self.assertEqual(({"op": "remove_all_counters", "source": "$source", VERSION_FIELD: 1},),
                         tuple(program.effects))
        for text in (
            card.oracle_text.replace("all permanents", "target permanent"),
            card.oracle_text.replace("all tokens", "all creatures"),
            card.oracle_text.replace("all counters", "all charge counters"),
            "Exile all tokens, then remove all counters from all permanents.",
            card.oracle_text + " Rewrite the game history.",
        ):
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                capability_registry=self.registry).status, text)

    def test_raw_global_descriptor_cannot_replace_the_source_scope_or_order(self):
        _, program = self.source_program()
        effect, = program.effects
        for changed in (
            replace(program, event="activate"),
            replace(program, target_schema={"zones": ["player"], "categories": ["player"], "count": 1}),
            replace(program, effects=({**effect, "source": "$target.0"},)),
            replace(program, effects=({**effect, VERSION_FIELD: True},)),
            replace(program, effects=({**effect, "counter": "charge"},)),
            replace(program, effects=({**effect, "reverse_order": True},)),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                  profile="commander_review")
            self.assertFalse(bound["strict"], bound)

    def scene(self, seed, *, phase=False):
        game = self.session(seed)
        engine = game.engine
        spell = self.add(engine, ORIGINAL, zone="hand")
        trinket = self.add(engine, "Strike It Rich", zone="hand")
        suspended = self.add(engine, "Rift Bolt", zone="hand")
        alarm = self.add(engine, "Raise the Alarm", seat="B", zone="hand")
        ripple = self.add(engine, "Reality Ripple", seat="C", zone="hand") if phase else None
        ring = self.add(engine, "Sol Ring", ref="aether-artifact")
        ring.counters.update({"charge": 3, "stun": 2})
        bogle = self.add(engine, "Slippery Bogle", seat="B", ref="aether-nontargeted-hexproof")
        bogle.counters["+1/+1"] = 1
        feeder = self.add(engine, "Spike Feeder", seat="D", ref="aether-zero-toughness")
        feeder.counters["+1/+1"] = 2
        game.state.players["C"].poison = 3
        for card in [spell, trinket, suspended, alarm, bogle, feeder] + ([ripple] if ripple else []):
            self.assert_closed(game, card)
        self.prepare(game, mana={"B": 2, "C": 3, "R": 2})
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        game.state.players["C"].mana_pool.update({"C": 1, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", trinket, [])
        self.resolve_top(game)
        self.priority(game, "A")
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        suspend, = [row for row in rows if row.get("action") == "suspend" and row.get("card") == suspended.ref]
        result = game.act("pilot:A", {"action_id": suspend["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(("exile", 1), (game.state.cards[suspended.object_id].zone,
                                       game.state.cards[suspended.object_id].counters.get("time")))
        self.cast_for(game, "A", spell, [])
        self.cast_for(game, "B", alarm, [])
        self.resolve_top(game)
        tokens = [card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token]
        self.assertEqual(3, len(tokens))
        phased = None
        if ripple:
            phased = next(card for card in tokens if card.owner == "B")
            self.cast_for(game, "C", ripple, [phased.ref])
            self.resolve_top(game)
            self.assertTrue(game.state.cards[phased.object_id].phased_out)
        return game, spell, ring, bogle, feeder, suspended, tokens, phased

    def test_real_counter_removal_precedes_the_token_exile_and_zero_toughness_action(self):
        game, spell, ring, bogle, feeder, suspended, tokens, _ = self.scene(61342701)
        game = self.replay(game, load=True)
        before = game.state.event_sequence
        self.assertIsNone(self.resolve(game))
        self.assertEqual({}, game.state.cards[ring.object_id].counters,
                         "Aether Snap must remove every kind of permanent counter")
        self.assertEqual({}, game.state.cards[bogle.object_id].counters)
        self.assertEqual(3, game.state.players["C"].poison)
        self.assertEqual(("exile", 1), (game.state.cards[suspended.object_id].zone,
                                       game.state.cards[suspended.object_id].counters.get("time")))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[feeder.object_id].zone)
        events = [event for event in game.state.events if event.event_id > before]
        removals = [event for event in events if event.code == "permanent.counters_removed"]
        exiles = [event for event in events if event.code == "zone.move" and event.details.get("to") == "exile"]
        death = next(event for event in events
                     if event.code == "state.creatures_died" and feeder.object_id in event.changed_objects)
        self.assertIn(game.state.cards[feeder.object_id].ref, death.details["put_in_graveyard"])
        self.assertTrue(removals)
        self.assertEqual({card.object_id for card in tokens}, {object_id for event in exiles for object_id in event.changed_objects})
        self.assertLess(max(event.event_id for event in removals), min(event.event_id for event in exiles))
        self.assertLess(max(event.event_id for event in exiles), death.event_id)
        self.replay(game, load=True)

    def test_actual_phased_token_is_omitted_while_the_other_tokens_are_exiled(self):
        game, _, _, _, _, _, tokens, phased = self.scene(61342702, phase=True)
        before = game.state.event_sequence
        self.assertIsNone(self.resolve(game))
        retained = game.state.cards[phased.object_id]
        self.assertEqual(("battlefield", True), (retained.zone, retained.phased_out))
        exiled = {object_id for event in game.state.events
                  if event.event_id > before and event.code == "zone.move" and event.details.get("to") == "exile"
                  for object_id in event.changed_objects}
        self.assertEqual({card.object_id for card in tokens if card.object_id != phased.object_id}, exiled)
        self.replay(game, load=True)

    def test_omitting_the_global_counter_batch_fails_the_real_ordered_result(self):
        with patch("quorune.counter_removal.commit_all_permanent_counter_removals", return_value=()):
            with self.assertRaisesRegex(AssertionError, "Aether Snap must remove every kind of permanent counter"):
                self.test_real_counter_removal_precedes_the_token_exile_and_zero_toughness_action()

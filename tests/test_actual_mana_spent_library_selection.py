"""The actual original spell payment fixes the private inspection count."""

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
from quorune.flashback import FLASHBACK_CAST_OPTION_ID
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.spell_mana_conditions import actual_cast_mana_total_reference
from quorune.spell_mana_counts import MANA_SPENT_COUNTS_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class ActualManaSpentLibrarySelectionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "actual-mana-spent-library-selection.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/actual-mana-spent-library-selection-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Actual paid mana", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_uses_an_actual_payment_and_keeps_flashback(self):
        admitted, semantics = register_card(self.db, self.registry, "Memory Deluge")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup("Memory Deluge", fuzzy=False)
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertTrue(any("casting.flashback.fixed_mana" in p.capability_dependencies for p in programs))
        spell, = (p for p in programs if p.event == "resolve")
        effect, = spell.effects
        self.assertEqual(actual_cast_mana_total_reference(), effect["look_count"])
        self.assertEqual((2, 2, "random"), (effect["minimum"], effect["maximum"], effect["remainder_order"]))
        self.assertIn(MANA_SPENT_COUNTS_CAPABILITY, spell.capability_dependencies)
        for changes in ({"look_count": 4}, {"minimum": 0}, {"remainder_order": "chosen"}):
            self.assertFalse(bind_semantic_program_runtime(
                replace(spell, effects=[{**effect, **changes}]), capability_registry=self.registry,
                profile="commander_review",
            )["strict"])
        changed = replace(card, oracle_text=card.oracle_text.replace("amount of mana spent to cast this spell",
                                                                     "mana value of this spell"))
        self.assertNotEqual("exact", compile_oracle_card(
            changed, capability_registry=self.registry, capability_profile="commander_review",
        ).status)

    def add_top(self, game, count):
        return tuple(self.add(game.engine, "Plains", zone="library", ref=f"paid-library-private-{index}")
                     for index in range(count))

    def cast_alternative(self, game, spell, selector, *, origin):
        self.priority(game, "A")
        row = self.action(game, "A", spell)
        option, = (value for value in row["cost_options"] if selector(value["id"]))
        result = game.act("pilot:A", {
            "action_id": row["id"], "from": origin, "cost_option": option["id"], "pay": "auto",
        })
        self.assertTrue(result.ok, result.summary)

    def inspection(self, game, expected, required):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        offer = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual("fixed_library_selection", offer["ctx"]["operation"])
        refs = [value["id"] for value in offer["ctx"]["objects"]]
        self.assertEqual([card.ref for card in expected], refs)
        schema = offer["legal_actions"][0]["choice_schema"]
        self.assertEqual((required, required), (schema.get("minimum", 1), schema.get("maximum", 1)))
        self.assertEqual(refs, schema["legal_refs"])
        self.assertFalse(schema.get("optional", False))
        for seat in ("B", "C", "D"):
            packet = game.packet("pilot:" + seat, full=True)
            self.assertIsNone(packet["decision"])
            self.assertTrue(all(ref not in json.dumps(packet) for ref in refs))
        return refs

    def reject(self, game, seat, cards):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": "choose", "cards": cards})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def select(self, game, selected, remainder):
        result = game.act("pilot:A", {"action_id": "choose", "cards": [card.ref for card in selected]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        library = game.state.players["A"].zones["library"]
        self.assertEqual({card.object_id for card in remainder}, set(library[:len(remainder)]))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "hand" for card in selected))
        self.assertTrue(all(not game.state.cards[card.object_id].known_to for card in remainder))
        self.replay(game, load=True)

    def test_actual_cost_increase_looks_at_five_and_requires_exactly_two_private_cards(self):
        game = self.session(60126001)
        spell = self.add(game.engine, "Memory Deluge", zone="hand")
        tax = self.add(game.engine, "Sphere of Resistance", seat="B")
        top = self.add_top(game, 6)
        for card in (spell, tax):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "U": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        inspected = top[1:][::-1]
        refs = self.inspection(game, inspected, 2)
        self.reject(game, "B", refs[:2])
        self.reject(game, "A", refs[:1])
        self.reject(game, "A", refs[:3])
        self.reject(game, "A", [refs[0], top[0].ref])
        game = self.replay(game, load=True)
        self.select(game, inspected[:2], inspected[2:])

    def test_actual_copy_of_paid_flashback_looks_at_zero_but_the_original_looks_at_seven(self):
        game = self.session(60126002)
        spell = self.add(game.engine, "Memory Deluge", zone="graveyard")
        copy_spell = self.add(game.engine, "Twincast", zone="hand", seat="B")
        top = self.add_top(game, 7)[::-1]
        for card in (spell, copy_spell):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "U": 2})
        game.state.players["B"].mana_pool["U"] = 2
        before_b = list(game.state.players["B"].zones["library"])
        self.checkpoint(game)
        self.cast_alternative(game, spell, lambda option: option == FLASHBACK_CAST_OPTION_ID, origin="graveyard")
        original = game.state.stack[-1].ref
        self.cast_for(game, "B", copy_spell, [original])
        self.resolve_top(game)
        self.assertEqual("spell_copy", game.state.stack[-1].kind)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(before_b, game.state.players["B"].zones["library"])
        self.assertEqual([original], [item.ref for item in game.state.stack])
        self.inspection(game, top, 2)
        self.select(game, top[:2], top[2:])
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)

    def test_actual_free_cast_looks_at_zero_and_short_library_keeps_the_only_card(self):
        for free in (True, False):
            with self.subTest(free=free):
                game = self.session(60126003 + free)
                spell = self.add(game.engine, "Memory Deluge", zone="hand")
                if free:
                    self.assert_closed(game, self.add(game.engine, "Omniscience"))
                for object_id in tuple(game.state.players["A"].zones["library"]):
                    if not free:
                        game.engine.move_card(object_id, "graveyard", log=False)
                top = self.add_top(game, 1)
                self.assert_closed(game, spell)
                self.prepare(game, mana={} if free else {"C": 2, "U": 2})
                self.checkpoint(game)
                if free:
                    original = list(game.state.players["A"].zones["library"])
                    self.cast_alternative(game, spell, lambda option: option.startswith("battlefield-alternative:"),
                                          origin="hand")
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual(original, game.state.players["A"].zones["library"])
                    self.replay(game, load=True)
                else:
                    self.cast_for(game, "A", spell)
                    self.inspection(game, top, 1)
                    self.reject(game, "A", [])
                    self.select(game, top, ())

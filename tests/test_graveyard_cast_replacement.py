"""Actual spell origin selects exactly one fixed result before existing owners act."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.cast_origin_replacement import GRAVEYARD_CAST_REPLACEMENT_VERSION
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.flashback import FLASHBACK_CAST_OPTION_ID
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = ("Increasing Ambition", "Increasing Devotion", "Increasing Savagery")


class GraveyardCastReplacementTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "graveyard-cast-replacement.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/graveyard-cast-replacement-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Actual casting zones", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_retain_flashback_and_reject_unowned_shapes(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                admitted, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                programs = semantics.programs_for_oracle(card.oracle_id)
                spell, = (p for p in programs if p.event == "resolve")
                wrapper, = spell.effects
                self.assertEqual(GRAVEYARD_CAST_REPLACEMENT_VERSION, wrapper["schema_version"])
                self.assertEqual("$context.cast_origin", wrapper["cast_fact"])
                self.assertTrue(any("casting.flashback.fixed_mana" in p.capability_dependencies for p in programs),
                                [p.capability_dependencies for p in programs])
                for substitute in ({**wrapper, "cast_fact": True},
                                   {**wrapper, "otherwise_effects": [{"op": "lose_life", "amount": 5}]}):
                    self.assertFalse(bind_semantic_program_runtime(
                        replace(spell, effects=[substitute]), capability_registry=self.registry,
                        profile="commander_review",
                    )["strict"])
                changed = replace(card, oracle_text=card.oracle_text.replace("a graveyard", "exile"))
                compiled = compile_oracle_card(changed, capability_registry=self.registry, capability_profile="commander_review")
                self.assertNotEqual("exact", compiled.status)
        card = self.db.lookup("Increasing Savagery", fuzzy=False)
        changed = replace(card, oracle_text=card.oracle_text.replace("that creature instead", "another target creature instead"))
        compiled = compile_oracle_card(changed, capability_registry=self.registry, capability_profile="commander_review")
        self.assertNotEqual("exact", compiled.status)

    def cast_from_graveyard(self, game, spell, *, targets=(), flashback=True):
        self.priority(game, "A")
        offer = self.action(game, "A", spell)
        option, = (row for row in offer["cost_options"]
                   if row["id"] == FLASHBACK_CAST_OPTION_ID) if flashback else (
                       row for row in offer["cost_options"] if row.get("permission_source")
                   )
        accepted = game.act("pilot:A", {
            "action_id": offer["id"], "from": "graveyard", "cost_option": option["id"],
            "pay": "auto", "targets": list(targets),
        })
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", game.state.stack[-1].context["cast_origin"])
        return game.state.stack[-1].ref

    def human_tokens(self, game, seat):
        tokens = [card for card in game.state.cards.values()
                  if card.zone == "battlefield" and card.controller == seat and card.object_kind == "token"]
        for token in tokens:
            data = game.engine._effective_card_data(token)
            self.assertIn("Human", data["type_line"])
            self.assertEqual(["W"], list(data["colors"]))
            self.assertEqual((1, 1), tuple(game.engine._numeric_stat(token.object_id, key)
                                           for key in ("power", "toughness")))
        return len(tokens)

    def test_actual_devotion_hand_creates_five_and_flashback_creates_ten_more(self):
        game = self.session(61415001)
        spell = self.add(game.engine, "Increasing Devotion", zone="hand")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 10, "W": 4})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, reserve={"C": 7, "W": 2})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(5, self.human_tokens(game, "A"))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.cast_from_graveyard(game, game.state.cards[spell.object_id])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(15, self.human_tokens(game, "A"))
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_actual_stack_copy_of_a_kess_graveyard_cast_creates_only_five(self):
        game = self.session(61415002)
        source = self.add(game.engine, "Kess, Dissident Mage")
        spell = self.add(game.engine, "Increasing Devotion", zone="graveyard")
        copy_spell = self.add(game.engine, "Twincast", seat="B", zone="hand")
        for card in (source, spell, copy_spell):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "W": 2})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        original = self.cast_from_graveyard(game, spell, flashback=False)
        self.cast_for(game, "B", copy_spell, [original])
        self.resolve_top(game)
        self.assertEqual("spell_copy", game.state.stack[-1].kind)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual((0, 5), (self.human_tokens(game, "A"), self.human_tokens(game, "B")))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((10, 5), (self.human_tokens(game, "A"), self.human_tokens(game, "B")))
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_actual_ambition_searches_for_one_or_two_privately_and_shuffles_once(self):
        for flashback in (False, True):
            with self.subTest(flashback=flashback):
                game = self.session(61415003 + flashback)
                spell = self.add(game.engine, "Increasing Ambition", zone="graveyard" if flashback else "hand")
                cards = [self.add(game.engine, "Grizzly Bears", zone="library", ref=f"ambition-private-{i}")
                         for i in range(3)]
                self.assert_closed(game, spell)
                self.prepare(game, mana={"C": 7 if flashback else 4, "B": 1})
                self.checkpoint(game)
                if flashback:
                    self.cast_from_graveyard(game, spell)
                else:
                    self.cast_for(game, "A", spell)
                pending = self.resolve(game)
                self.assertEqual(("semantic.search", ["A"]), (pending.kind, pending.actors))
                schema = game.packet("pilot:A", full=True)["decision"]["legal_actions"][0]["choice_schema"]
                count = 2 if flashback else 1
                self.assertEqual((count, count, False),
                                 (schema["minimum"], schema["maximum"], schema["rules_may_fail_to_find"]))
                self.assertTrue({card.ref for card in cards} <= set(schema["legal_refs"]))
                for card in cards:
                    self.assertNotIn(card.ref, json.dumps(game.packet("pilot:B", full=True)))
                before = authoritative_state_hash(game.state)
                picks = [card.ref for card in cards[:count]]
                for actor, refs in (("B", picks), ("A", picks[:-1]), ("A", [card.ref for card in cards])):
                    result = game.act("pilot:" + actor, {"action_id": "choose", "search_cards": refs})
                    self.assertFalse(result.ok, result.summary)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                shuffles = game.state.players["A"].stats.get("shuffle_count", 0)
                result = game.act("pilot:A", {"action_id": "choose", "search_cards": picks})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(shuffles + 1, game.state.players["A"].stats.get("shuffle_count", 0))
                self.assertEqual(["hand"] * count + ["library"] * (3 - count),
                                 [game.state.cards[card.object_id].zone for card in cards])
                self.assertEqual("exile" if flashback else "graveyard", game.state.cards[spell.object_id].zone)
                for card in cards:
                    self.assertNotIn(card.ref, json.dumps(game.packet("pilot:B", full=True)))
                self.replay(game, load=True)

    def test_actual_savagery_keeps_the_announced_creature_for_each_cast(self):
        game = self.session(61415005)
        spell = self.add(game.engine, "Increasing Savagery", zone="hand")
        first = self.add(game.engine, "Grizzly Bears", seat="B", ref="savagery-first")
        second = self.add(game.engine, "Grizzly Bears", ref="savagery-second")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 7, "G": 4})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [first.ref], reserve={"C": 5, "G": 2})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(5, game.state.cards[first.object_id].counters["+1/+1"])
        self.cast_from_graveyard(game, game.state.cards[spell.object_id], targets=[second.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(5, game.state.cards[first.object_id].counters["+1/+1"])
        self.assertEqual(10, game.state.cards[second.object_id].counters["+1/+1"])
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_actual_savagery_with_a_departed_target_has_neither_result(self):
        game = self.session(61415006)
        spell = self.add(game.engine, "Increasing Savagery", zone="graveyard")
        intended = self.add(game.engine, "Grizzly Bears", seat="B", ref="savagery-departs")
        other = self.add(game.engine, "Grizzly Bears", ref="savagery-not-targeted")
        response = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.assert_closed(game, spell)
        self.assert_closed(game, response)
        self.prepare(game, mana={"C": 5, "G": 2})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast_from_graveyard(game, spell, targets=[intended.ref])
        self.cast_for(game, "B", response, [intended.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[intended.object_id].zone)
        for card in (intended, other):
            self.assertFalse(game.state.cards[card.object_id].counters.get("+1/+1"))
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

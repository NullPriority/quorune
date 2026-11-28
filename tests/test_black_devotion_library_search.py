"""Native black-devotion search, disclosure, fixed life loss, and replay."""

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
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.black_devotion_library_search import BOUND_FIELD, CAPABILITY
from scripts.build_test_database import build_fixture_database


class BlackDevotionLibrarySearchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "black-devotion-library-search.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/black-devotion-library-search-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Black devotion search", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile(self, card):
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

    def test_closed_source_entry_search_and_life_sequence(self):
        card = self.db.lookup("Grim Servant", fuzzy=False)
        ir = self.compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = (row for row in ir.faces[0].nodes if row.event == "permanent.enter.self")
        self.assertIn(CAPABILITY, node.capability_dependencies)
        self.assertEqual(1, node.effects[0][BOUND_FIELD])
        self.assertEqual({"op": "lose_life", "player": "$controller", "amount": 3}, node.effects[1])
        for old, new in (("to black", "to red"), ("then shuffle.", "then draw a card."),
                         ("You lose 3 life.", "You lose 2 life.")):
            with self.subTest(changed=old):
                changed = self.compile(replace(card, oracle_text=card.oracle_text.replace(old, new)))
                self.assertFalse(any(CAPABILITY in row.capability_dependencies for row in changed.faces[0].nodes))
        game = self.session(749047000)
        source = self.add(game.engine, card.name)
        self.assert_closed(game, source)
        program, = (row for row in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if row.event == "permanent.enter.self")
        for changed in (replace(program, effects=program.effects[:1]), replace(program, event="permanent.enter"),
                        replace(program, effects=({**program.effects[0], BOUND_FIELD: True}, program.effects[1]))):
            with self.subTest(event=changed.event, effects=changed.effects):
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                      profile="commander_review")
                self.assertFalse(bound["strict"], bound)

    def search(self, seed, *, bounce):
        game = self.session(seed)
        source = self.add(game.engine, "Grim Servant", zone="hand")
        pips = self.add(game.engine, "Vampire Nighthawk")
        self.add(game.engine, "Vampire Nighthawk", seat="C", ref="foreign-devotion")
        eligible = self.add(game.engine, "Llanowar Elves", zone="library", ref="devotion-eligible")
        higher = self.add(game.engine, "Grizzly Bears", zone="library", ref="devotion-high")
        answer = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, answer)
        self.prepare(game, mana={"C": 3, "B": 1})
        game.state.players["B"].mana_pool.update({"U": 1})
        self.checkpoint(game)
        before = (game.state.players["A"].life, game.state.players["A"].stats.get("shuffle_count", 0))
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        if bounce:
            self.cast_for(game, "B", answer, [pips.ref])
            self.resolve_top(game)
            self.assertEqual("hand", game.state.cards[pips.object_id].zone)
        decision = self.resolve(game)
        self.assertEqual("semantic.search", decision.kind)
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual({"mana_value": {"maximum": 1 if bounce else 3}}, ctx["search_spec"]["selector"])
        legal = ctx["legal_actions"][0]["choice_schema"]
        self.assertEqual((0, 1), (legal["minimum"], legal["maximum"]))
        self.assertIn(eligible.ref, legal["legal_refs"])
        self.assertEqual(not bounce, higher.ref in legal["legal_refs"])
        self.assertEqual(before[0], game.state.players["A"].life)
        return game, eligible, higher, before

    def select(self, game, cards, *, seat="A", accepted=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": "choose", "search_cards": [card.ref for card in cards]})
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def check_completion(self, game, before, selected):
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before[0] - 3, game.state.players["A"].life)
        self.assertEqual(before[1] + 1, game.state.players["A"].stats["shuffle_count"])
        event = next(row for row in reversed(game.state.events) if row.code == "library.search")
        self.assertEqual(len(selected), event.details["count"])
        if selected:
            self.assertTrue(event.details["revealed"])
            self.assertEqual([card.ref for card in selected], list(event.details["objects"]))

    def test_actual_response_updates_devotion_and_reveals_only_the_selected_card(self):
        game, eligible, higher, before = self.search(749047001, bounce=True)
        for observer in "BCD":
            packet = game.packet("pilot:" + observer, full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(eligible.ref, json.dumps(packet))
            self.assertNotIn(higher.ref, json.dumps(packet))
        self.select(game, [eligible], seat="B", accepted=False)
        self.select(game, [higher], accepted=False)
        game = self.replay(game, load=True)
        self.select(game, [eligible])
        self.check_completion(game, before, [eligible])
        self.assertEqual("hand", game.state.cards[eligible.object_id].zone)
        self.assertEqual("library", game.state.cards[higher.object_id].zone)
        self.assertNotIn(higher.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.replay(game, load=True)

    def test_actual_fail_to_find_still_shuffles_and_loses_three_life(self):
        game, eligible, higher, before = self.search(749047002, bounce=False)
        self.select(game, [])
        self.check_completion(game, before, [])
        for card in (eligible, higher):
            self.assertEqual("library", game.state.cards[card.object_id].zone)
        self.replay(game, load=True)

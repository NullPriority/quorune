"""Native evidence for two exact consumers of one chosen-player entry."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Sewer Nemesis"


class EntryChosenPlayerGraveyardAndCastMillTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "entry-chosen-player-graveyard-and-cast-mill.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/entry-chosen-player-graveyard-and-cast-mill-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Linked graveyard and chosen caster", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self):
        original = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        all_programs = semantics.programs_for_oracle(original.oracle_id)
        entry, = [program for program in all_programs if program.event == "zone.change"]
        definition, = [program for program in all_programs if program.event == "continuous"]
        cast, = [program for program in all_programs if program.event == "spell.cast"]
        return original, entry, definition, cast

    def test_complete_original_links_the_entry_to_both_exact_consumers(self):
        original, entry, definition, cast = self.programs()
        self.assertEqual(("all", "all", "battlefield"),
                         (entry.active_zone, definition.active_zone, cast.active_zone))
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, cast.coverage)
        self.assertTrue(all(program.target_schema is None for program in (entry, definition, cast)))
        lines = original.oracle_text.splitlines()
        for changed in (
            "\n".join(lines[:-1]),
            "\n".join((lines[0], lines[-1])),
            "\n".join(lines[1:]),
            original.oracle_text.replace("choose a player", "choose an opponent"),
            original.oracle_text.replace("chosen player's graveyard", "chosen player's hand"),
            original.oracle_text.replace("mills a card", "mills two cards"),
        ):
            ir = compile_oracle_card(replace(original, oracle_text=changed), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, changed)

    def test_raw_definition_and_cast_result_reject_an_altered_zone_or_caster(self):
        _, entry, definition, cast = self.programs()
        for changed in (
            replace(entry, handlers=[]),
            replace(definition, active_zone="battlefield"),
            replace(definition, handlers=[]),
            replace(cast, event_condition={"field": "controller", "op": "eq", "value": "$source.controller"}),
            replace(cast, effects=({**cast.effects[0], "count": 2},)),
        ):
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def choose_entry_player(self, game, player):
        choice = game.state.pending_decision
        self.assertEqual(("replacement.order", ["A"]), (choice.kind, choice.actors))
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        by_player = {row["id"].rsplit(":", 1)[-1]: row["id"] for row in rows}
        self.assertEqual(set("ABCD"), set(by_player))
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:C", {"action_id": "choose", "replacement": by_player[player]})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", {"action_id": "choose", "replacement": by_player[player]})
        self.assertTrue(accepted.ok, accepted.summary)

    def cast_nemesis(self, game, source, player, *, reserve=None):
        self.cast_for(game, "A", source, reserve=reserve)
        self.assertEqual("replacement.order", self.resolve(game).kind)
        game = self.replay(game, load=True)
        self.choose_entry_player(game, player)
        return game

    @staticmethod
    def stats(game, source):
        return tuple(game.engine._numeric_stat(source.object_id, field) for field in ("power", "toughness"))

    def test_actual_chosen_player_spell_mills_once_and_updates_both_stats_before_resolution(self):
        game = self.session(60724110)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        first = self.add(game.engine, "Plains", seat="B", zone="graveyard", ref="sewer-first-grave-card")
        second = self.add(game.engine, "Shock", seat="B", zone="graveyard")
        milled = self.add(game.engine, "Giant Spider", seat="B", zone="library", ref="sewer-hidden-top")
        chosen = self.add(game.engine, "Dark Ritual", seat="B", zone="hand", ref="sewer-chosen-spell")
        unrelated = self.add(game.engine, "Dark Ritual", zone="hand", ref="sewer-other-spell")
        for card in (source, first, second, milled, chosen, unrelated):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "B": 2})
        game.state.players["B"].mana_pool["B"] = 1
        self.checkpoint(game)
        self.assertEqual((0, 0), self.stats(game, source))
        game = self.cast_nemesis(game, source, "B", reserve={"B": 1})
        self.assertEqual((2, 2), self.stats(game, source))
        self.assertNotIn(milled.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.cast_for(game, "A", unrelated)
        self.assertEqual(0, self.trigger_count(game, source))
        self.resolve_top(game)
        self.assertEqual((2, 2), self.stats(game, source))
        self.cast_for(game, "B", chosen)
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertIsNone(game.state.stack[-1].card_object_id)
        self.assertEqual("A", game.state.stack[-1].controller)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[milled.object_id].zone)
        self.assertEqual("stack", game.state.cards[chosen.object_id].zone)
        self.assertEqual((3, 3), self.stats(game, source))
        self.resolve_top(game)
        self.assertEqual((4, 4), self.stats(game, source))
        self.replay(game, load=True)

    def test_actual_empty_own_graveyard_is_a_legal_choice_and_the_zero_creature_leaves(self):
        game = self.session(60724120)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3, "B": 1})
        self.checkpoint(game)
        game = self.cast_nemesis(game, source, "A")
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual((0, 0), self.stats(game, source))
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_actual_chosen_player_departure_resets_both_stats_and_the_waiting_mill_does_nothing(self):
        game = self.session(60724130)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        self.add(game.engine, "Giant Spider", seat="B", zone="graveyard")
        spell = self.add(game.engine, "Dark Ritual", seat="B", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 3, "B": 1})
        game.state.players["B"].mana_pool["B"] = 1
        self.checkpoint(game)
        game = self.cast_nemesis(game, source, "B")
        self.assertEqual((1, 1), self.stats(game, source))
        self.cast_for(game, "B", spell)
        self.assertEqual(1, self.trigger_count(game, source))
        self.priority(game, "B")
        concession = game.act("pilot:B", {"action_id": "concede", "confirm_concede": True})
        self.assertTrue(concession.ok, concession.summary)
        self.assertFalse(game.state.players["B"].in_game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual((0, 0), self.stats(game, source))
        self.assertEqual(1, self.trigger_count(game, source))
        game = self.replay(game, load=True)
        before = {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ACD"}
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.assertEqual(before, {seat: tuple(game.state.players[seat].zones["library"]) for seat in "ACD"})
        self.replay(game, load=True)

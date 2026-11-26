"""One public creature choice per owner precedes the optional simultaneous return."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as untap_fixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.graveyard_return_choices import paired_graveyard_return_effect
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class PairedPublicGraveyardReturnTests(InheritedNativeFixture, unittest.TestCase):
    playing = untap_fixture.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "paired-public-graveyard-return.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/paired-public-graveyard-return-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Public paired owner return", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_retains_flying_and_the_controller_end_step_choice(self):
        admitted, semantics = register_card(self.db, self.registry, "Dawnbreak Reclaimer")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup("Dawnbreak Reclaimer", fuzzy=False)
        programs = semantics.programs_for_oracle(card.oracle_id)
        trigger, = (program for program in programs if program.event == "step.begin")
        self.assertIsNone(trigger.target_schema)
        self.assertEqual([paired_graveyard_return_effect()], trigger.effects)
        forged = {**trigger.effects[0], "player": "$active"}
        self.assertFalse(bind_semantic_program_runtime(
            replace(trigger, effects=[forged]), capability_registry=self.registry, profile="commander_review",
        )["strict"])
        changed = replace(card, oracle_text=card.oracle_text.replace("that player chooses", "you choose"))
        self.assertNotEqual("exact", compile_oracle_card(
            changed, capability_registry=self.registry, capability_profile="commander_review",
        ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def end_step_trigger(self, game, source):
        self.until(game, lambda current: current.state.active_player == "A" and current.state.step == "end_step"
                   and any(item.source_object_id == source.object_id for item in current.state.stack))
        item, = (item for item in game.state.stack if item.source_object_id == source.object_id)
        self.assertEqual("A", item.controller)

    def option_ids(self, game, seat):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", [seat]), (pending.kind, pending.actors))
        packet = game.packet("pilot:" + seat, full=True)["decision"]
        return {row["id"] for row in packet["ctx"]["options"]}

    def choose(self, game, seat, selected, *, allowed=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": "choose", "choice": selected})
        self.assertEqual(allowed, result.ok, result.summary)
        if not allowed:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_opponent_card_is_mandatory_and_the_correct_opponent_makes_the_second_choice(self):
        game = self.playing(60825001)
        source = self.add(game.engine, "Dawnbreak Reclaimer", zone="hand")
        first = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard", ref="dawn-opponent-b")
        other = self.add(game.engine, "Grizzly Bears", seat="C", zone="graveyard", ref="dawn-opponent-c")
        own = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="dawn-own-return")
        untouched = self.add(game.engine, "Savannah Lions", zone="graveyard", ref="dawn-own-unselected")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 4, "W": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.assertIn("flying", game.engine._combat_keywords(self.current(game, source)))
        self.end_step_trigger(game, source)
        self.assertEqual({first.ref, other.ref}, self.option_ids(game, "A"))
        self.choose(game, "A", "D", allowed=False)
        self.choose(game, "A", first.ref)
        self.assertEqual({own.ref, untouched.ref}, self.option_ids(game, "B"))
        self.choose(game, "A", own.ref, allowed=False)
        self.choose(game, "C", own.ref, allowed=False)
        self.choose(game, "B", first.ref, allowed=False)
        game = self.replay(game, load=True)
        self.choose(game, "B", own.ref)
        self.assertEqual({"return", "decline"}, self.option_ids(game, "A"))
        self.assertEqual(["graveyard"] * 4,
                         [self.current(game, card).zone for card in (first, other, own, untouched)])
        decline = self.replay(game, load=True)
        self.choose(decline, "A", "decline")
        self.assertIsNone(self.resolve(decline))
        self.assertEqual(["graveyard", "graveyard"], [self.current(decline, card).zone for card in (first, own)])
        self.replay(decline, load=True)

        self.choose(game, "A", "return")
        self.assertIsNone(self.resolve(game))
        self.assertEqual([("battlefield", "B"), ("battlefield", "A")],
                         [(self.current(game, card).zone, self.current(game, card).controller) for card in (first, own)])
        self.assertEqual(self.current(game, first).zone_timestamp, self.current(game, own).zone_timestamp)
        self.assertEqual(["graveyard", "graveyard"], [self.current(game, card).zone for card in (other, untouched)])
        self.replay(game, load=True)

    def test_actual_empty_opponent_creature_sets_let_the_controller_choose_the_other_chooser(self):
        game = self.playing(60825002)
        source = self.add(game.engine, "Dawnbreak Reclaimer")
        own = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        noncreature = self.add(game.engine, "Sol Ring", seat="B", zone="graveyard")
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.end_step_trigger(game, source)
        self.assertEqual({"B", "C", "D"}, self.option_ids(game, "A"))
        self.choose(game, "A", noncreature.ref, allowed=False)
        self.choose(game, "A", "D")
        self.assertEqual({own.ref}, self.option_ids(game, "D"))
        self.choose(game, "B", own.ref, allowed=False)
        self.choose(game, "D", own.ref)
        self.assertEqual({"return", "decline"}, self.option_ids(game, "A"))
        self.choose(game, "A", "return")
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "A"), (self.current(game, own).zone, self.current(game, own).controller))
        self.assertEqual("graveyard", self.current(game, noncreature).zone)
        self.replay(game, load=True)

    def test_actual_source_departure_still_allows_returning_only_the_opponents_chosen_creature(self):
        game = self.playing(60825003)
        source = self.add(game.engine, "Dawnbreak Reclaimer")
        removal = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        their = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard")
        self.assert_closed(game, source)
        self.assert_closed(game, removal)
        self.prepare(game)
        self.checkpoint(game)
        self.end_step_trigger(game, source)
        self.cast_for(game, "B", removal, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", self.current(game, source).zone)
        self.assertEqual({their.ref}, self.option_ids(game, "A"))
        self.choose(game, "A", their.ref)
        self.assertEqual({"return", "decline"}, self.option_ids(game, "A"))
        self.choose(game, "A", "return")
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "B"), (self.current(game, their).zone, self.current(game, their).controller))
        self.replay(game, load=True)

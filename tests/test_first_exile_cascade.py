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
from quorune.impulse_access import grant_temporary_cast_permission
from quorune.impulse_access_model import ImpulseAccessDuration, TemporaryCastPermissionGrant
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.battlefield_cascade_grants import FIRST_EXILE_CASCADE_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

CARD_NAME = "Wild-Magic Sorcerer"
PROFILE = "commander_review"


class FirstExileCascadeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "first-exile-cascade.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/wild-magic-sorcerer-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("First exile Cascade", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def cascade_items(self, game):
        return [item for item in game.state.stack if item.semantic_key == "builtin:cascade"]

    def available_from_exile(self, game, card, *, later=False):
        duration = ImpulseAccessDuration.UNTIL_USED if later else ImpulseAccessDuration.END_OF_TURN
        grant_temporary_cast_permission(
            game.engine, card=card,
            grant=TemporaryCastPermissionGrant(
                "A", duration, game.state.turn_sequence + 1 if later else None, False, "fixture:first-exile",
            ),
            reason="Prepared exact normal-cost exile permission",
        )

    def has_cascade(self, game, card):
        current = game.state.cards[card.object_id]
        return "cascade" in {value.casefold() for value in game.engine._effective_card_data(current)["keywords"]}

    def test_first_paid_exile_spell_keeps_its_ordinal_while_the_second_resolves_and_replays(self):
        game = self.session(702850901)
        host = game.engine
        source = self.add(host, CARD_NAME)
        self.assert_closed(game, source)
        first = self.add(host, "Axebane Stag", zone="exile", ref="ordinal-first-exile")
        second = self.add(host, "Lightning Bolt", zone="exile", ref="ordinal-second-exile")
        ordinary = self.add(host, "Lightning Bolt", zone="hand", ref="ordinal-hand-spell")
        receiver = self.add(host, CARD_NAME, zone="library", ref="ordinal-second-source")
        host.move_card(receiver.object_id, "library", position="top", log=False)
        receiver.known_to.clear()
        receiver.revealed_to.clear()
        self.prepare(game, mana={"C": 7, "G": 1, "R": 3})
        for card in (first, second):
            self.available_from_exile(game, card)
        self.checkpoint(game)
        self.cast_for(game, "A", ordinary, ["C"])
        self.assertEqual([], self.cascade_items(game))
        self.resolve_top(game)
        self.cast_for(game, "A", first)
        occurrence, = self.cascade_items(game)
        self.assertEqual(first.object_id, occurrence.source_object_id)
        self.assertTrue(self.has_cascade(game, first))
        self.cast_for(game, "A", second, ["C"])
        self.assertEqual([occurrence.ref], [item.ref for item in self.cascade_items(game)])
        self.assertFalse(self.has_cascade(game, second))
        self.assertTrue(self.has_cascade(game, first))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(34, game.state.players["C"].life)
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "selection.exile_cast")
        self.assertEqual("exile", game.state.cards[receiver.object_id].zone)
        game = self.replay(game, load=True)
        cast = game.act("pilot:A", {"action_id": "cast"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual([], self.cascade_items(game))
        self.assertFalse(self.has_cascade(game, receiver))
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[receiver.object_id].zone)
        self.assertEqual([], self.cascade_items(game))
        self.assertTrue(self.has_cascade(game, first))
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[first.object_id].zone)
        self.replay(game, load=True)

    def test_earlier_exile_cast_before_the_source_enforces_the_boundary_until_turn_reset(self):
        game = self.session(702850902)
        host = game.engine
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"])[4:]:
                host.move_card(object_id, "library", log=False)
        source = self.add(host, CARD_NAME, zone="hand")
        self.assert_closed(game, source)
        early = self.add(host, "Lightning Bolt", zone="exile", ref="ordinal-before-source")
        later = self.add(host, "Lightning Bolt", zone="exile", ref="ordinal-after-source")
        following = self.add(host, "Lightning Bolt", zone="exile", ref="ordinal-following-turn")
        self.add(host, "Mountain", ref="ordinal-next-turn-mana")
        self.add(host, "Mountain", ref="ordinal-backup-mana")
        self.available_from_exile(game, early)
        self.available_from_exile(game, later)
        self.available_from_exile(game, following, later=True)
        self.prepare(game, mana={"C": 3, "R": 4})
        self.checkpoint(game)
        self.cast_for(game, "A", early, ["B"])
        self.assertEqual([], self.cascade_items(game))
        self.resolve_top(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.cast_for(game, "A", later, ["B"])
        self.assertEqual([], self.cascade_items(game))
        self.assertFalse(self.has_cascade(game, later))
        self.resolve_top(game)
        start = game.state.turn_sequence
        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.turn_sequence > start)
        self.cast_for(game, "A", following, ["B"])
        occurrence, = self.cascade_items(game)
        self.assertEqual(following.object_id, occurrence.source_object_id)
        self.replay(game, load=True)

    def test_first_exile_codec_and_current_history_dependency_fail_closed(self):
        original = self.db.lookup(CARD_NAME, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        self.assertEqual("exact", compile_oracle_card(original, **options).status)
        report, semantics = register_card(self.db, self.registry, CARD_NAME)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = semantics.programs_for_oracle(original.oracle_id)
        descriptor, = program.handlers
        for grant in (
            {**descriptor["grant"], "first_exile_cast": 1},
            {**descriptor["grant"], "origin": "hand"},
            {**descriptor["grant"], "instances": 2},
        ):
            with self.assertRaises(ValueError):
                replace(program, handlers=({**descriptor, "grant": grant},))
        deps = tuple(owner for owner in program.capability_dependencies if owner != FIRST_EXILE_CASCADE_CAPABILITY)
        withdrawn = replace(program, capability_dependencies=deps,
                            capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        report = bind_semantic_program_runtime(withdrawn, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + FIRST_EXILE_CASCADE_CAPABILITY, report["blockers"])
        mutation = replace(original, oracle_text=original.oracle_text.replace("first spell", "second spell"))
        self.assertNotEqual("exact", compile_oracle_card(mutation, **options).status)
        with patch("quorune.compiler.battlefield_cascade_grants.battlefield_cascade_grant_handler",
                   return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(original, **options).status)

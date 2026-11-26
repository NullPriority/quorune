"""The printed exile batches retain their order and normal zone authority."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class FixedMixedZoneExileTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "mixed-zone-exile.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/fixed-mixed-zone-exile-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Mixed zone exile", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def observe_exile_batches(self, game):
        actual = ZoneTransitionOwner.move_cards_simultaneously
        batches = []

        def observe(owner, changes, **kwargs):
            if changes and all(destination == "exile" for _, destination in changes):
                batches.append((tuple(changes), tuple(owner.state.players[seat].life for seat in "ABCD")))
            return actual(owner, changes, **kwargs)

        with patch.object(ZoneTransitionOwner, "move_cards_simultaneously", new=observe):
            self.assertIsNone(self.resolve(game))
        return batches

    def current_hands_and_graveyards(self, game):
        return {object_id for player in game.state.players.values() for zone in ("hand", "graveyard")
                for object_id in player.zones[zone]}

    def test_actual_worldfire_keeps_separate_batches_before_setting_each_life_total(self):
        report, semantics = register_card(self.db, self.registry, "Worldfire")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        record = self.db.lookup("Worldfire", fuzzy=False)
        program, = semantics.programs_for_oracle(record.oracle_id)
        self.assertEqual(("move_public_zone_set", "move_public_zone_set", "set_life_total"),
                         tuple(effect["op"] for effect in program.effects))
        with patch("quorune.compiler.public_zone_move_templates._fixed_mixed_zone_exile_template",
                   return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile="commander_review",
            ).status)

        game = self.playing(61127254)
        spell = self.add(game.engine, "Worldfire", zone="hand")
        first = self.add(game.engine, "Grizzly Bears", seat="B")
        second = self.add(game.engine, "Solitary Confinement", seat="C")
        concealed = self.add(game.engine, "Shock", seat="B", zone="hand")
        dead = self.add(game.engine, "Ornithopter", seat="D", zone="graveyard")
        library = self.add(game.engine, "Island", seat="B", zone="library")
        for card in (spell, first, second, concealed, dead):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"R": 9})
        original_life = (18, 29, 33, 40)
        for seat, life in zip("ABCD", original_life, strict=True):
            game.state.players[seat].life = life
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        before = json.dumps(game.packet("pilot:D", full=True))
        self.assertNotIn(json.dumps(concealed.ref), before)
        ordinary = self.current_hands_and_graveyards(game)
        batches = self.observe_exile_batches(game)
        self.assertEqual(
            [({first.object_id, second.object_id}, original_life), (ordinary, original_life)],
            [(set(object_id for object_id, _ in changes), lives) for changes, lives in batches],
        )
        self.assertEqual((1, 1, 1, 1), tuple(game.state.players[seat].life for seat in "ABCD"))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual("exile", game.state.cards[concealed.object_id].zone)
        self.assertEqual("library", game.state.cards[library.object_id].zone)
        self.replay(game, load=True)

    def test_actual_decree_exiles_one_live_battlefield_and_private_zone_batch(self):
        report, semantics = register_card(self.db, self.registry, "Decree of Annihilation")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        record = self.db.lookup("Decree of Annihilation", fuzzy=False)
        programs = semantics.programs_for_oracle(record.oracle_id)
        self.assertEqual({"resolve", "activate", "card.cycled.self"}, {program.event for program in programs})

        game = self.playing(61127255)
        spell = self.add(game.engine, "Decree of Annihilation", zone="hand")
        both_types = self.add(game.engine, "Ornithopter", seat="B")
        land = self.add(game.engine, "Plains", seat="C", ref="decree-battlefield-land")
        immune_type = self.add(game.engine, "Solitary Confinement", seat="D")
        concealed = self.add(game.engine, "Shock", seat="B", zone="hand")
        dead = self.add(game.engine, "Grizzly Bears", seat="C", zone="graveyard")
        sentinel = self.add(game.engine, "Island", seat="B", zone="library")
        for card in (spell, both_types, land, immune_type, concealed, dead):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"R": 10})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        self.assertNotIn(json.dumps(concealed.ref), json.dumps(game.packet("pilot:D", full=True)))
        expected = self.current_hands_and_graveyards(game) | {both_types.object_id, land.object_id}
        (changes, lives), = self.observe_exile_batches(game)
        self.assertEqual(len(expected), len(changes))
        self.assertEqual(expected, {object_id for object_id, _ in changes})
        self.assertEqual((40, 40, 40, 40), lives)
        self.assertEqual("battlefield", game.state.cards[immune_type.object_id].zone)
        self.assertEqual("library", game.state.cards[sentinel.object_id].zone)
        self.assertTrue(all(game.state.cards[object_id].zone == "exile" for object_id in expected))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_actual_decree_cycling_resolves_its_land_trigger_before_the_draw(self):
        game = self.playing(61127256)
        spell = self.add(game.engine, "Decree of Annihilation", zone="hand")
        first = self.add(game.engine, "Island", seat="A")
        second = self.add(game.engine, "Plains", seat="B", ref="cycle-land")
        creature = self.add(game.engine, "Grizzly Bears", seat="C")
        self.assert_closed(game, spell)
        self.prepare(game, actor="A", active="A", mana={"R": 7})
        self.checkpoint(game)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        cycling, = (action for action in actions if action["id"].startswith("activate:" + spell.ref + ":"))
        accepted = game.act("pilot:A", {"action_id": cycling["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(2, len(game.state.stack))
        self.assertEqual("triggered_ability", game.state.stack[-1].kind)
        drawn_before = len(game.state.players["A"].zones["hand"])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(("graveyard", "graveyard"),
                         tuple(game.state.cards[land.object_id].zone for land in (first, second)))
        self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
        self.assertEqual(drawn_before, len(game.state.players["A"].zones["hand"]))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(drawn_before + 1, len(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)

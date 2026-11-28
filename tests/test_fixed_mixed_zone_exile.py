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
        fixture = ROOT / "tests/fixtures"
        build_fixture_database([fixture / name for name in (
            "fixed-mixed-zone-exile-cards.json", "mana-tapped-land-next-untap-cards.json",
            "additional-conditional-modifier-cards.json",
        )], path)
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

    def test_wedding_ring_cast_copy_and_actual_opponent_own_turn_echoes_replay(self):
        from quorune.record import authoritative_state_hash

        game = self.playing(60320201)
        source = self.add(game.engine, "Wedding Ring", zone="hand")
        revitalize = self.add(game.engine, "Revitalize", seat="B", zone="hand")
        for n in range(2):
            self.add(game.engine, "Plains", seat="B", ref=f"wedding-ring-mana-{n}")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2, "W": 2})
        self.checkpoint(game)
        self.cast(game, "A", source)
        decision = self.resolve(game)
        self.assertEqual("semantic.target", decision.kind)
        before = authoritative_state_hash(game.state)
        for seat, target in (("C", "B"), ("A", "A")):
            result = game.act(f"pilot:{seat}", {"action_id": "choose", "targets": [target]})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "targets": ["B"]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        copy, = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(("Wedding Ring", "B"), (copy.printed_name, copy.controller))
        a_hand = len(game.state.players["A"].zones["hand"])
        self.until(game, lambda state: state.state.active_player == "B" and state.state.phase == "precombat_main")
        self.assertEqual(a_hand + 1, len(game.state.players["A"].zones["hand"]))
        a_life = game.state.players["A"].life
        b_life = game.state.players["B"].life
        self.cast(game, "B", revitalize)
        waiting = self.resolve(game)
        if waiting is not None:
            self.assertEqual("trigger.order", waiting.kind)
            context = game.packet("pilot:A", full=True)["decision"]["ctx"]
            result = game.act("pilot:A", {"action_id": "order", "triggers": [row["id"] for row in context["triggers"]]})
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(game))
        self.assertEqual(a_hand + 2, len(game.state.players["A"].zones["hand"]))
        self.assertEqual((a_life + 3, b_life + 3), (game.state.players["A"].life, game.state.players["B"].life))
        self.replay(game, load=True)

    def test_wedding_ring_flickered_entry_makes_no_copy_and_replays(self):
        game = self.playing(60320202)
        source = self.add(game.engine, "Wedding Ring")
        land = self.add(game.engine, "Plains", ref="wedding-ring-flicker-land")
        blink = self.add(game.engine, "Ghostly Flicker", zone="hand")
        self.add(game.engine, "Wedding Ring", seat="B", ref="independent-opponent-ring")
        off_turn = self.add(game.engine, "Revitalize", seat="B", zone="hand")
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        self.prepare(game, mana={"C": 2, "U": 1})
        self.checkpoint(game)
        original = source.logical_object_id
        self.cast(game, "A", blink, [source.ref, land.ref])
        self.assertIsNone(self.resolve(game))
        self.assertNotEqual(original, game.state.cards[source.object_id].logical_object_id)
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        before = (len(game.state.players["A"].zones["hand"]), game.state.players["A"].life)
        self.cast(game, "B", off_turn)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, (len(game.state.players["A"].zones["hand"]), game.state.players["A"].life))
        self.until(game, lambda state: state.state.active_player == "B" and state.state.phase == "precombat_main")
        self.assertEqual(before[0] + 1, len(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)

    def _hex_magic(self, seed):
        game = self.playing(seed)
        for object_id in tuple(game.state.players["A"].zones["hand"]):
            game.engine.move_card(object_id, "library", log=False)
        spell = self.add(game.engine, "Hex Magic", zone="hand")
        self.assert_closed(game, spell)
        return game, spell

    def test_hex_magic_replaces_a_private_hand_and_plays_original_exiled_cards(self):
        game, spell = self._hex_magic(61127301)
        land = self.add(game.engine, "Plains", zone="hand", ref="hex-original-land")
        shock = self.add(game.engine, "Shock", zone="hand", ref="hex-original-shock")
        outside = self.add(game.engine, "Shock", zone="exile", ref="hex-unrelated-exile")
        other = self.add(game.engine, "Shock", seat="B", zone="hand", ref="hex-other-private-hand")
        second = self.add(game.engine, "Worldfire", zone="library", ref="hex-second-draw")
        first = self.add(game.engine, "Island", zone="library", ref="hex-first-draw")
        self.prepare(game, mana={"R": 13})
        self.checkpoint(game)
        self.cast(game, "A", spell)
        private = json.dumps(game.packet("pilot:B", full=True))
        for card in (land, shock, first, second):
            self.assertNotIn(json.dumps(card.ref), private)
        game = self.replay(game, load=True)
        (changes, _), = self.observe_exile_batches(game)
        self.assertCountEqual(changes, [(land.object_id, "exile"), (shock.object_id, "exile")])
        self.assertEqual([first.object_id, second.object_id], game.state.players["A"].zones["hand"])
        self.assertEqual("hand", game.state.cards[other.object_id].zone)
        private = json.dumps(game.packet("pilot:B", full=True))
        for card in (first, second):
            self.assertNotIn(json.dumps(card.ref), private)
        self.assertIsNone(game.engine._temporary_play_permission("A", game.state.cards[outside.object_id]))
        for card in (land, shock):
            current = game.state.cards[card.object_id]
            self.assertEqual("exile", current.zone)
            self.assertIn(json.dumps(current.ref), private)
            self.assertEqual("until_end_of_next_turn", game.engine._temporary_play_permission("A", current)["duration"])
            self.assertIsNone(game.engine._temporary_play_permission("B", current))
        self.priority(game, "A")
        action = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                      if row["id"] == f"play-land:{land.ref}")
        played = game.act("pilot:A", {"action_id": action["id"]})
        self.assertTrue(played.ok, played.summary)
        self.assertIsNone(self.resolve(game))
        before = game.state.players["B"].life
        self.cast(game, "A", shock, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before - 2, game.state.players["B"].life)
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
        self.cast(game, "A", second)
        self.assertIsNone(self.resolve(game))
        for card in (land, shock):
            current = game.state.cards[card.object_id]
            self.assertEqual("exile", current.zone)
            self.assertIsNone(game.engine._temporary_play_permission("A", current))
        self.replay(game, load=True)

    def test_hex_magic_counts_only_committed_exile_including_zero(self):
        from quorune.replacement.model import ReplacementClass, ReplacementEffect
        from quorune.replacement.operations import SetField
        from quorune.semantic_runtime import zone_replacements

        for keep in (0, 1):
            with self.subTest(keep=keep):
                game, spell = self._hex_magic(61127302 + keep)
                parked = self.add(game.engine, "Plains", zone="hand", ref="hex-exile-instead") if keep else None
                redirected = self.add(game.engine, "Shock", zone="hand", ref="hex-redirected")
                fresh = self.add(game.engine, "Island", zone="library", ref="hex-draw-only-if-actual")
                self.prepare(game, mana={"R": 3})
                self.checkpoint(game)
                self.cast(game, "A", spell)
                collector = zone_replacements.collect_zone_change_replacement_effects
                redirect = ReplacementEffect(
                    effect_id="fixture:hex-redirect", source_id="fixture:hex-redirect",
                    event_kind="zone.change", replacement_class=ReplacementClass.OTHER,
                    conditions={"origin": {"eq": "hand"}, "destination": {"eq": "exile"},
                                "logical_object_id": {"eq": redirected.logical_object_id}},
                    operations=(SetField("destination", "graveyard"),),
                )

                def collect(host, **kwargs):
                    return (*collector(host, **kwargs), redirect)

                with patch.object(zone_replacements, "collect_zone_change_replacement_effects", collect):
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual("graveyard", game.state.cards[redirected.object_id].zone)
                    self.assertEqual("hand" if keep else "library", game.state.cards[fresh.object_id].zone)
                    self.assertEqual(keep, len(game.state.players["A"].zones["hand"]))
                    if parked is not None:
                        self.assertIsNotNone(game.engine._temporary_play_permission(
                            "A", game.state.cards[parked.object_id]))
                    self.replay(game, load=True)

    def test_hex_magic_next_turn_expiry_leaves_the_original_card_exiled(self):
        game, spell = self._hex_magic(61127304)
        parked = self.add(game.engine, "Plains", zone="hand", ref="hex-parked-land")
        self.prepare(game, mana={"R": 3})
        self.checkpoint(game)
        self.cast(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        turns = game.state.players["A"].turns_begun
        for _ in game.engine.active_seats:
            sequence = game.state.turn_sequence
            self.until(game, lambda state: state.state.turn_sequence > sequence)
        self.assertEqual("A", game.state.active_player)
        self.assertEqual(turns + 1, game.state.players["A"].turns_begun)
        self.until(game, lambda state: state.state.phase == "precombat_main")
        self.priority(game, "A")
        keys = {row["id"] for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]}
        self.assertIn(f"play-land:{parked.ref}", keys)
        game = self.replay(game, load=True)
        sequence = game.state.turn_sequence
        self.until(game, lambda state: state.state.active_player == "B" and state.state.turn_sequence > sequence)
        current = game.state.cards[parked.object_id]
        self.assertEqual("exile", current.zone)
        self.assertIsNone(game.engine._temporary_play_permission("A", current))
        self.replay(game, load=True)

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

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_ability_label_composition as journeys
import test_compiled_reviewed_spell_consumers as native


class CompiledReviewedTriggerConsumerTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.CompiledReviewedSpellConsumerTests.session
    add = native.CompiledReviewedSpellConsumerTests.add
    prepare = native.CompiledReviewedSpellConsumerTests.prepare
    checkpoint = native.CompiledReviewedSpellConsumerTests.checkpoint
    priority = native.CompiledReviewedSpellConsumerTests.priority
    actions = native.CompiledReviewedSpellConsumerTests.actions
    binding = native.CompiledReviewedSpellConsumerTests.binding
    reject = native.CompiledReviewedSpellConsumerTests.reject
    settle = native.CompiledReviewedSpellConsumerTests.settle
    replay = native.CompiledReviewedSpellConsumerTests.replay
    cast = native.CompiledReviewedSpellConsumerTests.cast
    advance = journeys.AbilityLabelCompositionTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "compiled-reviewed-triggers.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/compiled-reviewed-trigger-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Compiled reviewed triggers", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def ready(self, session, mana):
        session.state.players["A"].land_plays_remaining = 1
        self.prepare(session, mana=mana)
        self.checkpoint(session)

    def choose(self, session, *, seat="A", **fields):
        command = {"action_id": "choose", **fields}
        self.reject(session, command, seat="D" if seat != "D" else "C")
        result = session.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def play_land(self, session, card):
        self.priority(session, "A")
        row = next(row for row in self.actions(session)
                   if row["id"].startswith("play-land:") and row.get("card") == card.ref)
        self.reject(session, {"action_id": row["id"]}, seat="D")
        result = session.act("pilot:A", {"action_id": row["id"]})
        self.assertTrue(result.ok, result.summary)

    @staticmethod
    def tokens(session, name=None):
        return [card for card in session.state.cards.values()
                if card.is_token and card.zone == "battlefield" and (name is None or card.printed_name == name)]

    def test_compiled_bog_plays_tapped_and_can_exile_either_players_graveyard(self):
        for index, affected in enumerate("AB"):
            with self.subTest(affected=affected):
                session = self.session(965_000 + index)
                source = self.add(session.engine, "Bojuka Bog", zone="hand")
                graves = {seat: self.add(session.engine, "Forest", seat=seat, zone="graveyard", ref="bog-grave-" + seat)
                          for seat in "AB"}
                self.ready(session, {})
                self.assertTrue(self.binding(session, source)["strict_capability_ready"])
                self.play_land(session, source)
                self.assertTrue(session.state.cards[source.object_id].tapped)
                self.assertEqual("semantic.target", self.settle(session).kind)
                session = self.replay(session, load=True)
                self.choose(session, targets=[affected])
                self.assertIsNone(self.settle(session))
                for seat, card in graves.items():
                    self.assertEqual("exile" if seat == affected else "graveyard", session.state.cards[card.object_id].zone)
                self.replay(session, load=True)

    def test_compiled_fireweaver_damages_opponents_only_for_its_controllers_artifacts(self):
        session = self.session(965_010)
        source = self.add(session.engine, "Reckless Fireweaver", zone="hand")
        artifact = self.add(session.engine, "Sol Ring", zone="hand")
        victim = self.add(session.engine, "Opt", seat="B", zone="hand")
        counter = self.add(session.engine, "An Offer You Can't Refuse", zone="hand")
        session.state.players["B"].mana_pool["U"] = 1
        self.ready(session, {"C": 6, "R": 2, "U": 3})
        self.cast(session, source)
        self.assertIsNone(self.settle(session))
        self.cast(session, artifact)
        self.assertIsNone(self.settle(session))
        self.assertEqual([40, 39, 39, 39], [session.state.players[seat].life for seat in "ABCD"])
        self.cast(session, victim, seat="B")
        self.cast(session, counter, [session.state.stack[-1].ref])
        self.assertIsNone(self.settle(session))
        self.assertEqual(2, len(self.tokens(session, "Treasure Token")))
        self.assertEqual([40, 39, 39, 39], [session.state.players[seat].life for seat in "ABCD"])
        self.replay(session, load=True)

    def test_compiled_alarm_untaps_on_creature_entry_even_after_its_source_leaves(self):
        session = self.session(965_020)
        source = self.add(session.engine, "Intruder Alarm", zone="hand")
        bodies = [self.add(session.engine, "Grizzly Bears", seat=seat, ref="alarm-body-" + seat) for seat in "AB"]
        for body in bodies:
            body.tapped = True
        artifact = self.add(session.engine, "Sol Ring", zone="hand")
        creature = self.add(session.engine, "Ambush Viper", seat="B", zone="hand")
        removal = self.add(session.engine, "Disenchant", seat="B", zone="hand")
        session.state.players["B"].mana_pool.update(C=2, G=1, W=1)
        self.ready(session, {"C": 3, "U": 1})
        self.cast(session, source)
        self.assertIsNone(self.settle(session))
        self.cast(session, artifact)
        self.assertIsNone(self.settle(session))
        self.assertTrue(all(session.state.cards[body.object_id].tapped for body in bodies))
        self.cast(session, creature, seat="B")
        for _ in range(20):
            if session.state.cards[creature.object_id].zone == "battlefield":
                break
            result = session.act(session.pending_principals()[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.stack)
        self.cast(session, removal, [source.ref], seat="B")
        session = self.replay(session, load=True)
        self.assertIsNone(self.settle(session))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertTrue(all(not session.state.cards[body.object_id].tapped for body in bodies))
        self.replay(session, load=True)

        session = self.session(965_021)
        source = self.add(session.engine, "Intruder Alarm")
        body = self.add(session.engine, "Grizzly Bears", seat="B")
        body.tapped = True
        self.ready(session, {})
        self.assertTrue(self.binding(session, source)["strict_capability_ready"])
        self.advance(session, lambda s: s.state.active_player == "B" and s.state.phase == "precombat_main")
        self.assertTrue(session.state.cards[body.object_id].tapped)
        self.replay(session, load=True)

    def test_compiled_zulaport_uses_own_simultaneous_deaths_and_ignores_opposing_death(self):
        session = self.session(965_030)
        source = self.add(session.engine, "Zulaport Cutthroat", zone="hand")
        self.add(session.engine, "Grizzly Bears", ref="zulaport-own-body")
        opponent = self.add(session.engine, "Grizzly Bears", seat="B", ref="zulaport-other-body")
        shock = self.add(session.engine, "Shock", zone="hand")
        sweep = self.add(session.engine, "Pyroclasm", zone="hand")
        self.ready(session, {"C": 4, "B": 2, "R": 4})
        self.cast(session, source)
        self.assertIsNone(self.settle(session))
        self.cast(session, shock, [opponent.ref])
        self.assertIsNone(self.settle(session))
        self.assertEqual([40] * 4, [session.state.players[seat].life for seat in "ABCD"])
        self.cast(session, sweep)
        self.assertIsNone(self.settle(session))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual([42, 38, 38, 38], [session.state.players[seat].life for seat in "ABCD"])
        self.replay(session, load=True)

    def test_compiled_ophiomancer_checks_the_intervening_condition_on_each_upkeep(self):
        for index, interrupt in enumerate((False, True)):
            with self.subTest(interrupt=interrupt):
                session = self.session(965_040 + index)
                source = self.add(session.engine, "Ophiomancer", zone="hand")
                self.add(session.engine, "Ambush Viper", seat="B", ref="ophi-opposing-snake")
                response = self.add(session.engine, "Ambush Viper", zone="hand", ref="ophi-own-snake")
                for i in range(2):
                    self.add(session.engine, "Forest", ref="ophi-response-mana-" + str(i))
                self.ready(session, {"C": 2, "B": 1})
                self.cast(session, source)
                self.assertIsNone(self.settle(session))
                self.advance(session, lambda s: s.state.active_player == "B" and s.state.step == "upkeep"
                             and any(item.source_object_id == source.object_id for item in s.state.stack))
                if interrupt:
                    self.cast(session, response)
                session = self.replay(session, load=True)
                self.assertIsNone(self.settle(session))
                snakes = self.tokens(session, "Snake Token")
                self.assertEqual(0 if interrupt else 1, len(snakes))
                if snakes:
                    self.assertEqual("A", snakes[0].controller)
                    self.assertIn("deathtouch", session.engine._combat_keywords(snakes[0]))
                    self.assertEqual({"B"}, set(session.engine._effective_card_data(snakes[0])["colors"]))
                self.advance(session, lambda s: s.state.active_player == "C" and s.state.phase == "precombat_main")
                self.assertEqual(0 if interrupt else 1, len(self.tokens(session, "Snake Token")))
                self.replay(session, load=True)

    def test_compiled_scepter_selects_one_creature_and_expires_control_type_and_haste(self):
        session = self.session(965_050)
        source = self.add(session.engine, "Loki's Scepter", zone="hand")
        target = self.add(session.engine, "Grizzly Bears", seat="B")
        target.tapped = True
        wrong = self.add(session.engine, "Sol Ring", seat="B")
        self.ready(session, {"C": 12, "R": 2})
        self.cast(session, source)
        self.assertEqual("semantic.target", self.settle(session).kind)
        self.reject(session, {"action_id": "choose", "targets": [wrong.ref]})
        session = self.replay(session, load=True)
        self.choose(session, targets=[target.ref])
        self.assertIsNone(self.settle(session))
        current = session.state.cards[target.object_id]
        self.assertEqual("A", current.controller)
        self.assertFalse(current.tapped)
        self.assertIn("Villain", session.engine._effective_card_data(current)["type_line"])
        self.assertIn("haste", session.engine._combat_keywords(current))
        row = next(row for row in self.actions(session) if row["id"].startswith("activate:" + source.ref + ":"))
        command = {"action_id": row["id"], "mana_choice": "G"}
        self.reject(session, command, seat="D")
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, session.state.players["A"].mana_pool["G"])
        self.advance(session, lambda s: s.state.active_player == "B" and s.state.phase == "precombat_main")
        current = session.state.cards[target.object_id]
        self.assertEqual("B", current.controller)
        self.assertNotIn("Villain", session.engine._effective_card_data(current)["type_line"])
        self.assertNotIn("haste", session.engine._combat_keywords(current))
        self.replay(session, load=True)

    def test_compiled_mole_plays_a_graveyard_land_and_keeps_the_created_tokens_ability(self):
        session = self.session(965_060)
        source = self.add(session.engine, "Mole Man, Moloid Master", zone="hand")
        self.add(session.engine, "Fervor")
        land = self.add(session.engine, "Forest", zone="graveyard")
        other = self.add(session.engine, "Mountain", zone="graveyard")
        bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
        session.state.players["B"].mana_pool["U"] = 1
        self.ready(session, {"C": 10, "G": 4})
        self.cast(session, source)
        self.assertIsNone(self.settle(session))
        self.play_land(session, land)
        self.assertIsNone(self.settle(session))
        token, = self.tokens(session, "Moloid")
        self.assertFalse(any(row.get("card") == other.ref for row in self.actions(session)))
        self.reject(session, {"action_id": "play-land:" + other.ref})
        self.cast(session, bounce, [source.ref], seat="B")
        self.assertIsNone(self.settle(session))
        self.advance(session, lambda s: getattr(s.state.pending_decision, "kind", None) == "combat.attackers")
        command = {"action_id": "attack", "atk": {token.ref: "B"}}
        self.reject(session, command, seat="D")
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.settle(session).kind)
        before = len(session.state.players["A"].zones["library"])
        session = self.replay(session, load=True)
        self.choose(session, choice="apply")
        self.assertEqual(before - 1, len(session.state.players["A"].zones["library"]))
        self.assertEqual("hand", session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_compiled_scute_uses_resolution_land_count_and_departed_copiable_values(self):
        for index, remove_land in enumerate((True, False)):
            with self.subTest(remove_land=remove_land):
                session = self.session(965_070 + index)
                source = self.add(session.engine, "Scute Swarm", zone="hand")
                for i in range(5):
                    self.add(session.engine, "Forest", ref="scute-starting-land-" + str(i))
                land = self.add(session.engine, "Forest", zone="hand", ref="scute-sixth-land")
                hidden = self.add(session.engine, "Forest", zone="library", ref="scute-searched-land")
                growth = self.add(session.engine, "Giant Growth", zone="hand")
                response = self.add(session.engine, "Boomerang" if remove_land else "Unsummon", seat="B", zone="hand")
                search = self.add(session.engine, "Rampant Growth", zone="hand")
                session.engine.shuffle_library("A", reason="Prepare a hidden basic-land search")
                session.state.players["B"].mana_pool["U"] = 2
                self.ready(session, {"C": 4, "G": 4})
                self.cast(session, source)
                self.assertIsNone(self.settle(session))
                self.cast(session, growth, [source.ref])
                self.assertIsNone(self.settle(session))
                self.assertEqual(4, session.engine._numeric_stat(source.object_id, "power"))
                self.play_land(session, land)
                self.cast(session, response, [land.ref if remove_land else source.ref], seat="B")
                self.assertIsNone(self.settle(session))
                tokens = self.tokens(session)
                self.assertEqual(1, len(tokens))
                self.assertEqual("Insect Token" if remove_land else "Scute Swarm", tokens[0].printed_name)
                self.assertEqual(1, session.engine._numeric_stat(tokens[0].object_id, "power"))
                if not remove_land:
                    self.assertEqual("hand", session.state.cards[source.object_id].zone)
                    self.cast(session, search)
                    self.assertEqual("semantic.search", self.settle(session).kind)
                    for seat in "BCD":
                        self.assertNotIn(hidden.ref, json.dumps(session.packet("pilot:" + seat, full=True)))
                    session = self.replay(session, load=True)
                    self.choose(session, search_cards=[hidden.ref])
                    self.assertIsNone(self.settle(session))
                    self.assertEqual(2, len(self.tokens(session, "Scute Swarm")))
                self.replay(session, load=True)

    def test_compiled_retreat_selects_a_mode_then_untaps_declines_or_scries_privately(self):
        for index, choice in enumerate(("untap", "decline", "scry")):
            with self.subTest(choice=choice):
                session = self.session(965_080 + index)
                source = self.add(session.engine, "Retreat to Coralhelm", zone="hand")
                target = self.add(session.engine, "Grizzly Bears", seat="B")
                target.tapped = True
                land = self.add(session.engine, "Forest", zone="hand")
                self.ready(session, {"C": 2, "U": 1})
                self.cast(session, source)
                self.assertIsNone(self.settle(session))
                self.play_land(session, land)
                self.assertEqual("semantic.target", self.settle(session).kind)
                session = self.replay(session, load=True)
                self.choose(session, modes=["mode_2" if choice == "scry" else "mode_1"],
                            targets=[] if choice == "scry" else [target.ref])
                self.assertEqual("semantic.choice", self.settle(session).kind)
                for seat in "BCD":
                    self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
                session = self.replay(session, load=True)
                self.choose(session, **({"cards": []} if choice == "scry" else {"choice": choice}))
                self.assertIsNone(self.settle(session))
                self.assertEqual(choice != "untap", session.state.cards[target.object_id].tapped)
                self.replay(session, load=True)

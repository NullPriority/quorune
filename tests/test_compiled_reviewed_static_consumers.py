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
import test_compiled_reviewed_trigger_consumers as native


class CompiledReviewedStaticConsumerTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.CompiledReviewedTriggerConsumerTests.session
    add = native.CompiledReviewedTriggerConsumerTests.add
    prepare = native.CompiledReviewedTriggerConsumerTests.prepare
    checkpoint = native.CompiledReviewedTriggerConsumerTests.checkpoint
    ready = native.CompiledReviewedTriggerConsumerTests.ready
    priority = native.CompiledReviewedTriggerConsumerTests.priority
    actions = native.CompiledReviewedTriggerConsumerTests.actions
    binding = native.CompiledReviewedTriggerConsumerTests.binding
    reject = native.CompiledReviewedTriggerConsumerTests.reject
    settle = native.CompiledReviewedTriggerConsumerTests.settle
    replay = native.CompiledReviewedTriggerConsumerTests.replay
    choose = native.CompiledReviewedTriggerConsumerTests.choose
    tokens = staticmethod(native.CompiledReviewedTriggerConsumerTests.tokens)

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "compiled-reviewed-static.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/compiled-reviewed-static-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Compiled reviewed static consumers", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def cast(self, session, source, targets=(), *, seat="A", **choices):
        self.priority(session, seat)
        row = next(row for row in self.actions(session, seat) if row.get("card") == source.ref)
        if row.get("cost_options") and "cost_option" not in choices:
            choices["cost_option"] = row["cost_options"][0]["id"]
        return native.CompiledReviewedTriggerConsumerTests.cast(self, session, source, targets, seat=seat, **choices)

    def activate(self, session, source, targets=(), *, ability=None, seat="A", **choices):
        self.priority(session, seat)
        rows = [row for row in self.actions(session, seat) if row["id"].startswith("activate:" + source.ref + ":")
                and (ability is None or row["id"].endswith(":" + ability))]
        self.assertEqual(1, len(rows), rows)
        self.assertTrue(self.binding(session, source)["strict_capability_ready"])
        command = {"action_id": rows[0]["id"], "targets": list(targets), "pay": "auto", **choices}
        self.reject(session, command, seat="D" if seat != "D" else "C")
        result = session.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def test_counter_replacements_distinguish_placing_player_and_keep_current_power_mana(self):
        for index, name in enumerate(("Doubling Season", "Doc Samson, Super Psychiatrist")):
            with self.subTest(source=name):
                session = self.session(966_000 + index)
                source = self.add(session.engine, name, zone="hand")
                body = self.add(session.engine, "Llanowar Elves")
                self.add(session.engine, "Fervor")
                other = self.add(session.engine, "Hunger of the Howlpack", seat="B", zone="hand", ref="other-counter-spell")
                own = self.add(session.engine, "Hunger of the Howlpack", zone="hand", ref="own-counter-spell")
                growth = self.add(session.engine, "Giant Growth", zone="hand")
                opt = self.add(session.engine, "Opt", zone="hand")
                counter = self.add(session.engine, "An Offer You Can't Refuse", seat="B", zone="hand")
                session.state.players["B"].mana_pool.update(G=3, U=3)
                self.ready(session, {"C": 30, "G": 10, "U": 8})
                self.cast(session, source)
                self.assertIsNone(self.settle(session))
                self.cast(session, other, [body.ref], seat="B")
                self.assertIsNone(self.settle(session))
                self.assertEqual(2 if name == "Doubling Season" else 1,
                                 session.state.cards[body.object_id].counters["+1/+1"])
                self.cast(session, own, [body.ref])
                self.assertIsNone(self.settle(session))
                self.assertEqual(4 if name == "Doubling Season" else 3,
                                 session.state.cards[body.object_id].counters["+1/+1"])
                if name == "Doubling Season":
                    self.cast(session, opt)
                    self.cast(session, counter, [session.state.stack[-1].ref], seat="B")
                    self.assertIsNone(self.settle(session))
                    self.assertEqual(4, len(self.tokens(session, "Treasure Token")))
                else:
                    self.cast(session, growth, [source.ref])
                    self.assertIsNone(self.settle(session))
                    expected = int(self.db.lookup(name).power) + 3
                    before = session.state.players["A"].mana_pool["U"]
                    self.activate(session, source, mana_choice="U")
                    self.assertFalse(session.state.stack)
                    self.assertEqual(before + expected, session.state.players["A"].mana_pool["U"])
                self.replay(session, load=True)

    def test_prevention_and_damage_doubling_use_their_own_subjects_and_not_life_loss(self):
        for index, name in enumerate(("Daunting Defender", "Furnace of Rath")):
            with self.subTest(source=name):
                session = self.session(966_010 + index)
                source = self.add(session.engine, name, zone="hand")
                body = self.add(session.engine, "Grizzly Bears")
                first = self.add(session.engine, "Shock", zone="hand", ref="damage-first")
                second = self.add(session.engine, "Shock", zone="hand", ref="damage-second")
                loss = self.add(session.engine, "Sign in Blood", zone="hand")
                self.ready(session, {"C": 20, "R": 20, "W": 20, "B": 20})
                self.cast(session, source)
                self.assertIsNone(self.settle(session))
                self.cast(session, first, [source.ref if name == "Daunting Defender" else "B"])
                self.assertIsNone(self.settle(session))
                if name == "Daunting Defender":
                    self.assertEqual(1, session.state.cards[source.object_id].marked_damage)
                    self.cast(session, second, [body.ref])
                    self.assertIsNone(self.settle(session))
                    self.assertEqual("graveyard", session.state.cards[body.object_id].zone)
                else:
                    self.assertEqual(36, session.state.players["B"].life)
                    self.cast(session, loss, ["B"])
                    self.assertIsNone(self.settle(session))
                    self.assertEqual(34, session.state.players["B"].life)
                self.replay(session, load=True)

    def test_helm_and_idol_keep_one_creation_batch_private_draw_and_copy_costs(self):
        session = self.session(966_020)
        helm = self.add(session.engine, "Worldwalker Helm", zone="hand")
        idol = self.add(session.engine, "Idol of Oblivion", zone="hand")
        opt = self.add(session.engine, "Opt", zone="hand")
        counter = self.add(session.engine, "An Offer You Can't Refuse", seat="B", zone="hand")
        session.state.players["B"].mana_pool["U"] = 2
        self.ready(session, {"C": 30, "U": 20})
        self.cast(session, helm)
        self.assertIsNone(self.settle(session))
        self.cast(session, idol)
        self.assertIsNone(self.settle(session))
        self.assertFalse(any(row["id"].endswith(":" + idol.ref + ":ab1") for row in self.actions(session)))
        self.reject(session, {"action_id": "activate:" + idol.ref + ":ab1"})
        self.cast(session, opt)
        self.cast(session, counter, [session.state.stack[-1].ref], seat="B")
        self.assertIsNone(self.settle(session))
        self.assertEqual((2, 1), (len(self.tokens(session, "Treasure Token")), len(self.tokens(session, "Map Token"))))
        before = set(session.state.players["A"].zones["hand"])
        self.activate(session, idol, ability="ab1")
        self.assertTrue(session.state.cards[idol.object_id].tapped)
        session = self.replay(session, load=True)
        self.assertIsNone(self.settle(session))
        drawn = set(session.state.players["A"].zones["hand"]) - before
        self.assertEqual(1, len(drawn))
        ref = session.state.cards[next(iter(drawn))].ref
        for seat in "BCD":
            self.assertNotIn(ref, json.dumps(session.packet("pilot:" + seat, full=True)))
        treasure = self.tokens(session, "Treasure Token")[0]
        self.activate(session, helm, [treasure.ref])
        self.assertTrue(session.state.cards[helm.object_id].tapped)
        self.assertIsNone(self.settle(session))
        self.assertEqual((3, 2), (len(self.tokens(session, "Treasure Token")), len(self.tokens(session, "Map Token"))))
        self.replay(session, load=True)

        session = self.session(966_021)
        source = self.add(session.engine, "Idol of Oblivion", zone="hand")
        self.ready(session, {"C": 20})
        self.cast(session, source)
        self.assertIsNone(self.settle(session))
        self.activate(session, source, ability="ab2")
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        session = self.replay(session, load=True)
        self.assertIsNone(self.settle(session))
        token, = self.tokens(session, "Eldrazi Token")
        self.assertEqual((10, 10), (session.engine._numeric_stat(token.object_id, "power"),
                                   session.engine._numeric_stat(token.object_id, "toughness")))
        self.replay(session, load=True)

    def test_deathrite_targeted_abilities_use_the_stack_and_the_correct_recipients(self):
        for index, (ability, target_name) in enumerate((("ab1", "Forest"), ("ab2", "Opt"), ("ab3", "Grizzly Bears"))):
            with self.subTest(ability=ability):
                session = self.session(966_030 + index)
                source = self.add(session.engine, "Deathrite Shaman", zone="hand")
                self.add(session.engine, "Fervor")
                target = self.add(session.engine, target_name, seat="B", zone="graveyard")
                wrong = self.add(session.engine, "Llanowar Elves" if ability != "ab3" else "Forest",
                                 seat="B", zone="graveyard", ref="deathrite-wrong-type")
                self.ready(session, {"C": 20, "G": 20, "B": 20})
                self.cast(session, source)
                self.assertIsNone(self.settle(session))
                command = {"action_id": "activate:" + source.ref + ":" + ability, "targets": [wrong.ref], "pay": "auto"}
                self.reject(session, command)
                self.activate(session, source, [target.ref], ability=ability)
                self.assertTrue(session.state.stack)
                self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
                session = self.replay(session, load=True)
                decision = self.settle(session)
                if ability == "ab1":
                    self.assertEqual("semantic.choice", decision.kind)
                    self.choose(session, choice="U")
                    self.assertEqual(1, session.state.players["A"].mana_pool["U"])
                else:
                    self.assertIsNone(decision)
                    self.assertEqual([40, 38, 38, 38] if ability == "ab2" else [42, 40, 40, 40],
                                     [session.state.players[seat].life for seat in "ABCD"])
                self.assertEqual("exile", session.state.cards[target.object_id].zone)
                self.replay(session, load=True)

    def test_skullclamp_draws_when_its_own_toughness_grant_causes_the_equipped_death(self):
        session = self.session(966_040)
        source = self.add(session.engine, "Skullclamp", zone="hand")
        body = self.add(session.engine, "Llanowar Elves")
        self.ready(session, {"C": 20})
        self.cast(session, source)
        self.assertIsNone(self.settle(session))
        before = len(session.state.players["A"].draw_history)
        self.activate(session, source, [body.ref])
        self.assertIsNone(self.settle(session))
        self.assertEqual("graveyard", session.state.cards[body.object_id].zone)
        self.assertEqual(before + 2, len(session.state.players["A"].draw_history))
        self.replay(session, load=True)

    def test_greaves_shroud_and_haste_follow_the_equipment_lifetime(self):
        session = self.session(966_050)
        source = self.add(session.engine, "Lightning Greaves", zone="hand")
        body = self.add(session.engine, "Grizzly Bears", zone="hand")
        bounce = self.add(session.engine, "Unsummon", zone="hand")
        removal = self.add(session.engine, "Disenchant", seat="B", zone="hand")
        session.state.players["B"].mana_pool.update(C=2, W=2)
        self.ready(session, {"C": 20, "G": 8, "U": 8})
        self.cast(session, source)
        self.assertIsNone(self.settle(session))
        self.cast(session, body)
        self.assertIsNone(self.settle(session))
        self.activate(session, source, [body.ref])
        self.assertIsNone(self.settle(session))
        data = session.engine._effective_card_data(session.state.cards[body.object_id])
        self.assertTrue({"haste", "shroud"} <= {word.lower() for word in data["keywords"]})
        self.reject(session, {"action_id": "cast:" + bounce.ref, "targets": [body.ref], "pay": "auto"})
        self.cast(session, removal, [source.ref], seat="B")
        self.assertIsNone(self.settle(session))
        self.cast(session, bounce, [body.ref])
        self.assertIsNone(self.settle(session))
        self.assertEqual("hand", session.state.cards[body.object_id].zone)
        self.replay(session, load=True)

    def test_staff_attaches_to_a_shaman_and_the_creature_owns_its_granted_abilities(self):
        session = self.session(966_060)
        source = self.add(session.engine, "Thornbite Staff", zone="hand")
        shaman = self.add(session.engine, "Deathrite Shaman", zone="hand")
        self.add(session.engine, "Fervor")
        victim = self.add(session.engine, "Grizzly Bears", seat="B")
        shock = self.add(session.engine, "Shock", zone="hand")
        self.ready(session, {"C": 30, "G": 10, "B": 10, "R": 10})
        self.cast(session, source)
        self.assertIsNone(self.settle(session))
        self.cast(session, shaman)
        self.assertEqual("semantic.choice", self.settle(session).kind)
        self.choose(session, choice="apply")
        self.assertIsNone(self.settle(session))
        self.assertEqual(shaman.object_id, session.state.cards[source.object_id].attached_to)
        ability = next(a for a in session.engine._activated_abilities(session.state.cards[shaman.object_id])
                       if "deals 1 damage to any target" in a.effect_text.lower())
        self.activate(session, shaman, [victim.ref], ability=ability.ability_id)
        self.assertIsNone(self.settle(session))
        self.assertTrue(session.state.cards[shaman.object_id].tapped)
        self.cast(session, shock, [victim.ref])
        self.assertIsNone(self.settle(session))
        self.assertEqual("graveyard", session.state.cards[victim.object_id].zone)
        self.assertFalse(session.state.cards[shaman.object_id].tapped)
        self.replay(session, load=True)

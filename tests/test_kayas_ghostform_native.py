"""The original Aura through native attachment, actual zone changes, and replay."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.replacement.operations import SetField
from quorune.replacement_effects import ReplacementClass, ReplacementEffect
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime import zone_replacements
from scripts.build_test_database import build_fixture_database


GHOSTFORM = "Kaya's Ghostform"


class KayasGhostformNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "kayas-ghostform.sqlite3"
        fixture = ROOT / "tests" / "fixtures" / "kayas-ghostform-native-cards.json"
        build_fixture_database(fixture, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition(
            "Ghostform witness",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)],
            [commander],
        )

    def ready(self, game, *cards, mana=None, opposing_mana=None):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        if opposing_mana:
            game.state.players["B"].mana_pool.update(opposing_mana)
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def ghostform(self, game, *, ref=None):
        return self.add(game.engine, GHOSTFORM, zone="hand", ref=ref)

    def cast_and_resolve(self, game, card, targets=(), *, actor="A", reserve=None):
        self.cast_for(game, actor, card, targets, reserve=reserve)
        self.resolve_top(game)

    def attach(self, game, aura, subject, *, reserve=None):
        self.cast_and_resolve(game, aura, [self.current(game, subject).ref], reserve=reserve)
        source = self.current(game, aura)
        victim = self.current(game, subject)
        self.assertEqual("battlefield", source.zone)
        self.assertIn(source.attached_to, (victim.object_id, victim.ref))

    def trigger(self, game, aura):
        occurrence, = [item for item in game.state.stack if item.source_object_id == aura.object_id]
        self.assertEqual(("triggered_ability", "A", []), (occurrence.kind, occurrence.controller, occurrence.targets))
        return occurrence

    def assert_returned(self, game, subject, *, owner):
        result = self.current(game, subject)
        self.assertEqual(("battlefield", owner, "A"), (result.zone, result.owner, result.controller))
        return result

    def test_enchant_offer_requires_own_creature_or_planeswalker_and_rechecks_live_attachment(self):
        game = self.session(702050301)
        first = self.ghostform(game, ref="first-ghostform")
        second = self.ghostform(game, ref="second-ghostform")
        bear = self.add(game.engine, "Grizzly Bears", ref="legal-creature")
        walker = self.add(game.engine, "Jace Beleren", ref="legal-planeswalker")
        walker.counters["loyalty"] = 3
        artifact = self.add(game.engine, "Sol Ring", ref="illegal-own-artifact")
        opposing = self.add(game.engine, "Grizzly Bears", seat="B", ref="illegal-opposing-creature")
        flicker = self.add(game.engine, "Cloudshift", zone="hand")
        donate = self.add(game.engine, "Donate", zone="hand")
        self.ready(game, first, second, flicker, donate, mana={"B": 2, "W": 1, "U": 1, "C": 2})

        offered = self.action(game, "A", first)
        legal = offered["target_schema"]["legal_refs"]
        self.assertEqual({bear.ref, walker.ref}, set(legal))
        before = authoritative_state_hash(game.state)
        for target in (artifact.ref, opposing.ref):
            rejected = game.act("pilot:A", {"action_id": offered["id"], "targets": [target], "pay": "auto"})
            self.assertFalse(rejected.ok, rejected.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        wrong = game.act("pilot:B", {"action_id": offered["id"], "targets": [bear.ref], "pay": "auto"})
        self.assertFalse(wrong.ok, wrong.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

        original = bear.logical_object_id
        self.cast_for(game, "A", first, [bear.ref])
        self.cast_and_resolve(game, flicker, [bear.ref])
        self.assertNotEqual(original, self.current(game, bear).logical_object_id)
        self.resolve_top(game)
        self.assertEqual("graveyard", self.current(game, first).zone)
        self.assertEqual([], game.state.stack)

        self.attach(game, second, walker)
        self.cast_and_resolve(game, donate, ["B", walker.ref])
        self.assertEqual(("battlefield", "A", "B"),
                         (self.current(game, walker).zone, self.current(game, walker).owner,
                          self.current(game, walker).controller))
        self.assertEqual("graveyard", self.current(game, second).zone)
        self.assertEqual([], game.state.stack)
        self.replay(game, load=True)

    def test_actual_death_returns_to_aura_controller_and_the_later_blink_is_unlinked(self):
        game = self.session(702050302)
        aura = self.ghostform(game)
        borrow = self.add(game.engine, "Reanimate", zone="hand")
        bear = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard")
        kill = self.add(game.engine, "Murder", zone="hand")
        flicker = self.add(game.engine, "Cloudshift", zone="hand")
        self.ready(game, aura, borrow, kill, flicker, mana={"B": 4, "C": 1, "W": 1})
        self.cast_and_resolve(game, borrow, [bear.ref])
        self.assert_returned(game, bear, owner="B")
        self.attach(game, aura, bear)
        original = self.current(game, bear).logical_object_id

        self.cast_and_resolve(game, kill, [bear.ref], reserve={"W": 1})
        dead = self.current(game, bear)
        self.assertEqual("graveyard", dead.zone)
        self.assertIn(bear.object_id, game.state.players["B"].zones["graveyard"])
        self.assertEqual("graveyard", self.current(game, aura).zone)
        self.trigger(game, aura)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        returned = self.assert_returned(game, bear, owner="B").logical_object_id
        self.assertNotEqual(original, returned)

        self.cast_and_resolve(game, flicker, [self.current(game, bear).ref])
        self.assertNotEqual(returned, self.assert_returned(game, bear, owner="B").logical_object_id)
        self.assertEqual("graveyard", self.current(game, aura).zone)
        self.assertEqual([], game.state.stack)
        self.replay(game, load=True)

    def test_actual_exile_returns_the_exact_arrival_once(self):
        game = self.session(702050303)
        aura = self.ghostform(game)
        bear = self.add(game.engine, "Grizzly Bears")
        exile = self.add(game.engine, "Final Reward", seat="B", zone="hand")
        self.ready(game, aura, exile, mana={"B": 1}, opposing_mana={"C": 4, "B": 1})
        self.attach(game, aura, bear)
        self.cast_and_resolve(game, exile, [bear.ref], actor="B")
        old = self.current(game, bear)
        self.assertEqual("exile", old.zone)
        self.assertEqual("graveyard", self.current(game, aura).zone)
        self.trigger(game, aura)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertNotEqual(old.logical_object_id, self.assert_returned(game, bear, owner="A").logical_object_id)
        self.assertEqual([], game.state.stack)
        self.replay(game, load=True)

    def test_same_event_aura_and_planeswalker_departure_uses_previous_attachment(self):
        game = self.session(702050304)
        aura = self.ghostform(game)
        walker = self.add(game.engine, "Jace Beleren")
        walker.counters["loyalty"] = 3
        purge = self.add(game.engine, "Planar Cleansing", zone="hand")
        self.ready(game, aura, walker, purge, mana={"B": 1, "C": 3, "W": 3})
        self.attach(game, aura, walker)
        previous = self.current(game, walker).logical_object_id
        self.cast_and_resolve(game, purge)
        self.assertTrue(all(self.current(game, card).zone == "graveyard" for card in (aura, walker)))
        self.trigger(game, aura)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        returned = self.assert_returned(game, walker, owner="A")
        self.assertNotEqual(previous, returned.logical_object_id)
        self.assertEqual(3, returned.counters["loyalty"])
        self.assertEqual("graveyard", self.current(game, aura).zone)
        self.replay(game, load=True)

    def test_exiled_commander_may_move_to_command_or_remain_for_return(self):
        for index, choice in enumerate(("command", "remain")):
            with self.subTest(choice=choice):
                game = self.session(702050310 + index)
                aura = self.ghostform(game)
                commander_id, = game.state.players["A"].zones["command"]
                commander = game.state.cards[commander_id]
                exile = self.add(game.engine, "Final Reward", seat="B", zone="hand")
                self.ready(game, aura, exile, mana={"W": 1, "B": 1}, opposing_mana={"C": 4, "B": 1})
                self.cast_and_resolve(game, commander)
                self.attach(game, aura, commander)
                self.cast_and_resolve(game, exile, [commander.ref], actor="B")

                decision = game.state.pending_decision
                self.assertEqual(("state.commander_zone", ["A"]), (decision.kind, decision.actors))
                actual = self.current(game, commander)
                arrival = actual.logical_object_id
                self.assertEqual("exile", actual.zone)
                before = authoritative_state_hash(game.state)
                wrong = game.act("pilot:B", {"action_id": choice})
                self.assertFalse(wrong.ok, wrong.summary)
                self.assertEqual(before, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                accepted = game.act("pilot:A", {"action_id": choice})
                self.assertTrue(accepted.ok, accepted.summary)
                self.trigger(game, aura)
                self.resolve_top(game)
                result = self.current(game, commander)
                self.assertEqual("command" if choice == "command" else "battlefield", result.zone)
                self.assertNotEqual(arrival, result.logical_object_id)
                self.assertTrue(result.is_commander)
                self.assertEqual("graveyard", self.current(game, aura).zone)
                self.replay(game, load=True)

    def test_replaced_death_yields_only_exile_departure_and_redirected_return_stays_redirected(self):
        game = self.session(702050305)
        aura = self.ghostform(game)
        bear = self.add(game.engine, "Grizzly Bears")
        replacement = self.add(game.engine, "Rest in Peace", seat="B")
        kill = self.add(game.engine, "Murder", zone="hand")
        self.ready(game, aura, replacement, kill, mana={"C": 1, "B": 3})
        self.attach(game, aura, bear)
        self.cast_and_resolve(game, kill, [bear.ref])
        self.assertEqual("exile", self.current(game, bear).zone)
        self.assertEqual("exile", self.current(game, aura).zone)
        self.trigger(game, aura)

        redirect = ReplacementEffect(
            effect_id="test:ghostform-return-redirect",
            source_id="test:return-to-hand",
            event_kind="zone.change",
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "object_ref": {"eq": bear.ref},
                "origin": {"eq": "exile"},
                "destination": {"eq": "battlefield"},
            },
            operations=(SetField("destination", "hand"),),
        )
        collect = zone_replacements.collect_zone_change_replacement_effects

        def collect_with_redirect(host, **kwargs):
            return (*collect(host, **kwargs), redirect)

        with patch.object(zone_replacements, "collect_zone_change_replacement_effects", collect_with_redirect):
            self.resolve_top(game)
            self.assertEqual("hand", self.current(game, bear).zone)
            self.assertIn(bear.object_id, game.state.players["A"].zones["hand"])
            self.assertEqual("exile", self.current(game, aura).zone)
            self.assertEqual([], game.state.stack)
            self.assertTrue(any(
                event.code == "replacement.apply" and event.details.get("effect_id") == redirect.effect_id
                and event.details.get("destination") == "hand"
                for event in game.state.events
            ))
            self.replay(game, load=True)

    def test_reentered_subject_cannot_be_reclaimed_by_earlier_departure_trigger(self):
        game = self.session(702050306)
        aura = self.ghostform(game)
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        game.engine.change_control(bear.object_id, "A", reason="foreign-owned attachment fixture")
        first = self.add(game.engine, "Murder", zone="hand", ref="initial-death")
        second = self.add(game.engine, "Murder", zone="hand", ref="second-death")
        competing_return = self.add(game.engine, "Reanimate", seat="B", zone="hand")
        instant_timing = self.add(game.engine, "Vedalken Orrery", seat="B")
        self.ready(
            game, aura, first, second, competing_return, instant_timing,
            mana={"B": 5, "C": 2}, opposing_mana={"B": 1},
        )

        self.attach(game, aura, bear)
        self.cast_and_resolve(game, first, [bear.ref], reserve={"B": 2, "C": 1})
        original = self.current(game, bear).logical_object_id
        old_trigger = self.trigger(game, aura).ref
        self.cast_and_resolve(game, competing_return, [bear.ref], actor="B")
        self.assertEqual(("battlefield", "B"), (self.current(game, bear).zone, self.current(game, bear).controller))
        self.cast_and_resolve(game, second, [bear.ref])
        replacement = self.current(game, bear).logical_object_id
        self.assertNotEqual(original, replacement)
        self.assertEqual("graveyard", self.current(game, bear).zone)
        self.assertEqual(old_trigger, self.trigger(game, aura).ref)

        game = self.replay(game, load=True)
        self.resolve_top(game)
        current = self.current(game, bear)
        self.assertEqual(("graveyard", "B", replacement), (current.zone, current.owner, current.logical_object_id))
        self.replay(game, load=True)

    def test_immediate_blink_triggers_but_leaves_the_new_incarnation_unchanged(self):
        game = self.session(702050307)
        aura = self.ghostform(game)
        bear = self.add(game.engine, "Grizzly Bears")
        flicker = self.add(game.engine, "Cloudshift", zone="hand")
        self.ready(game, aura, flicker, mana={"B": 1, "W": 1})
        self.attach(game, aura, bear)
        before = self.current(game, bear).logical_object_id
        self.cast_and_resolve(game, flicker, [bear.ref])
        new = self.assert_returned(game, bear, owner="A").logical_object_id
        self.assertNotEqual(before, new)
        self.trigger(game, aura)
        self.assertEqual("graveyard", self.current(game, aura).zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(new, self.assert_returned(game, bear, owner="A").logical_object_id)
        self.assertEqual("graveyard", self.current(game, aura).zone)
        self.replay(game, load=True)

    def test_token_can_be_enchanted_but_cannot_return_after_actual_death(self):
        game = self.session(702050308)
        aura = self.ghostform(game)
        muster = self.add(game.engine, "Raise the Alarm", zone="hand")
        kill = self.add(game.engine, "Murder", zone="hand")
        self.ready(game, aura, muster, kill, mana={"B": 3, "W": 1, "C": 2})
        self.cast_and_resolve(game, muster, reserve={"B": 3, "C": 1})
        tokens = [card for card in game.state.cards.values()
                  if card.owner == "A" and card.is_token and card.zone == "battlefield"]
        self.assertEqual(2, len(tokens))
        token = tokens[0]
        self.attach(game, aura, token)
        self.cast_and_resolve(game, kill, [token.ref])
        self.trigger(game, aura)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertNotIn(token.object_id, game.state.players["A"].zones["battlefield"])
        self.assertNotIn(token.object_id, game.state.players["A"].zones["graveyard"])
        self.assertEqual("graveyard", self.current(game, aura).zone)
        self.assertEqual("battlefield", self.current(game, tokens[1]).zone)
        self.replay(game, load=True)

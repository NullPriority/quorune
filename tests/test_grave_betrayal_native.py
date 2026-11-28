"""Grave Betrayal through normal commands, public entry facts, and exact replay."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.replacement import CreateAffectedObjectCounter
from quorune.replacement.operations import SetField
from quorune.replacement_effects import ReplacementClass, ReplacementEffect
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime import zone_replacements
from scripts.build_test_database import build_fixture_database


class GraveBetrayalNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "grave-betrayal.sqlite3"
        fixture = ROOT / "tests" / "fixtures" / "grave-betrayal-native-cards.json"
        build_fixture_database(fixture, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition(
            "Grave Betrayal witness",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)],
            [commander],
        )

    def ready(self, game, *cards, mana=None, opponent_mana=None):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        if opponent_mana:
            game.state.players["B"].mana_pool.update(opponent_mana)
        for player in game.state.players.values():
            for object_id in list(player.zones["hand"])[5:]:
                if game.state.cards[object_id].printed_name == "Plains":
                    game.engine.move_card(object_id, "library", position="bottom", log=False)
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def colors(self, game, card):
        return set(game.engine._effective_card_data(self.current(game, card))["colors"])

    def types(self, game, card):
        return game.engine._effective_type_parts(self.current(game, card))[:2]

    def delays(self, game, source):
        return [
            delay for delay in game.state.delayed_triggers
            if delay.active and delay.source_object_id == source.object_id and delay.event_kind == "step.begin"
        ]

    def source_stack(self, game, source):
        return [item for item in game.state.stack if item.source_object_id == source.object_id]

    def kill(self, game, spell, victim, *, seat="A", reserve=None):
        current = self.current(game, victim)
        self.cast_for(game, seat, spell, [current.ref], reserve=reserve)
        self.resolve_top(game)

    def resolve_death(self, game, source):
        item, = self.source_stack(game, source)
        self.assertEqual(("triggered_ability", "A", []), (item.kind, item.controller, item.targets))
        self.resolve_top(game)
        delay, = self.delays(game, source)
        self.assertEqual(("A", True), (delay.controller, delay.once))
        return delay

    def begin_delays(self, game, source, active, *, count=1):
        refs = {delay.ref for delay in self.delays(game, source)}
        self.assertEqual(count, len(refs))

        def scheduled_stack(current):
            return [item for item in self.source_stack(current, source)
                    if item.context.get("delayed_trigger_ref") in refs]

        self.until(game, lambda current: current.state.step == "end_step" and bool(scheduled_stack(current)))
        self.assertEqual(active, game.state.active_player)
        items = scheduled_stack(game)
        self.assertEqual(count, len(items))
        self.assertEqual({("A", "triggered_ability")}, {(item.controller, item.kind) for item in items})
        self.assertTrue(all(not item.targets for item in items))
        return items

    def assert_returned(self, game, card, *, owner):
        current = self.current(game, card)
        self.assertEqual(("battlefield", owner, "A"), (current.zone, current.owner, current.controller))
        self.assertEqual(1, current.counters.get("+1/+1", 0))
        return current

    def test_actual_arrival_adds_black_and_zombie_before_champion_and_lasts_until_new_incarnation(self):
        game = self.session(611020101)
        source = self.add(game.engine, "Grave Betrayal", zone="hand")
        observer = self.add(game.engine, "Champion of the Perished")
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        kill = self.add(game.engine, "Murder", zone="hand")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        self.add(game.engine, "Plains", ref="blink-white")
        bear.counters["+1/+1"] = 3
        self.ready(game, source, observer, kill, blink, mana={"B": 4, "C": 6})

        self.cast_for(game, "A", source, reserve={"B": 2, "C": 1})
        self.resolve_top(game)
        self.assertEqual("battlefield", self.current(game, source).zone)
        self.kill(game, kill, bear)
        self.assertEqual("graveyard", self.current(game, bear).zone)
        self.assertIn(bear.object_id, game.state.players["B"].zones["graveyard"])
        self.assertEqual(0, self.current(game, observer).counters.get("+1/+1", 0))
        self.resolve_death(game, source)
        game = self.replay(game, load=True)

        self.begin_delays(game, source, "A")
        self.resolve_top(game)
        identity = self.assert_returned(game, bear, owner="B").logical_object_id
        self.assertEqual({"B", "G"}, self.colors(game, bear))
        self.assertEqual(({"creature"}, {"bear", "zombie"}), self.types(game, bear))
        trigger, = self.source_stack(game, observer)
        self.assertIn("zombie", trigger.context["subtypes"])
        self.assertEqual(self.colors(game, bear), set(trigger.context["colors"]))
        self.resolve_top(game)
        self.assertEqual(1, self.current(game, observer).counters.get("+1/+1", 0))

        self.until(game, lambda current: current.state.active_player == "B")
        self.assertEqual(identity, self.assert_returned(game, bear, owner="B").logical_object_id)
        self.assertEqual({"B", "G"}, self.colors(game, bear))
        self.assertIn("zombie", self.types(game, bear)[1])
        self.cast_for(game, "A", blink, [self.current(game, bear).ref])
        self.resolve_top(game)
        fresh = self.current(game, bear)
        self.assertEqual(("battlefield", "B", "A"), (fresh.zone, fresh.owner, fresh.controller))
        self.assertNotEqual(identity, fresh.logical_object_id)
        self.assertEqual({"G"}, self.colors(game, bear))
        self.assertEqual(({"creature"}, {"bear"}), self.types(game, bear))
        self.assertEqual(0, fresh.counters.get("+1/+1", 0))
        self.assertEqual([], self.source_stack(game, observer))
        self.assertEqual(1, self.current(game, observer).counters.get("+1/+1", 0))
        self.replay(game, load=True)

    def test_actual_borrowed_own_creature_dies_and_source_departure_does_not_cancel_delayed_return(self):
        game = self.session(611020102)
        source = self.add(game.engine, "Grave Betrayal")
        bear = self.add(game.engine, "Grizzly Bears")
        donate = self.add(game.engine, "Donate", zone="hand")
        kill = self.add(game.engine, "Murder", zone="hand")
        source_removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.ready(
            game, source, donate, kill, source_removal,
            mana={"B": 2, "U": 1, "C": 3}, opponent_mana={"W": 1, "C": 1},
        )

        self.cast_for(game, "A", donate, ["B", bear.ref], reserve={"B": 2, "C": 1})
        self.resolve_top(game)
        self.assertEqual(("A", "B"), (self.current(game, bear).owner, self.current(game, bear).controller))
        self.kill(game, kill, bear)
        self.assertIn(bear.object_id, game.state.players["A"].zones["graveyard"])
        self.assertEqual(1, len(self.source_stack(game, source)))

        self.cast_for(game, "B", source_removal, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", self.current(game, source).zone)
        self.resolve_death(game, source)
        game = self.replay(game, load=True)
        self.begin_delays(game, source, "A")
        self.resolve_top(game)
        self.assert_returned(game, bear, owner="A")
        self.replay(game, load=True)

    def test_actual_commander_and_tokens_die_but_their_delays_cannot_return(self):
        game = self.session(611020103)
        source = self.add(game.engine, "Grave Betrayal")
        raise_alarm = self.add(game.engine, "Raise the Alarm", seat="B", zone="hand")
        wrath = self.add(game.engine, "Wrath of God", zone="hand")
        commander_id, = game.state.players["B"].zones["command"]
        commander = game.engine.move_card(commander_id, "battlefield", controller="B", log=False)
        self.ready(
            game, source, raise_alarm, wrath, commander,
            mana={"W": 2, "C": 2}, opponent_mana={"W": 1, "C": 1},
        )

        self.cast_for(game, "B", raise_alarm)
        self.resolve_top(game)
        tokens = [card for card in game.state.cards.values()
                  if card.is_token and card.zone == "battlefield" and card.controller == "B"]
        self.assertEqual(2, len(tokens))
        self.cast_for(game, "A", wrath)
        self.resolve_top(game)
        decision = game.state.pending_decision
        self.assertEqual(("state.commander_zone", ["B"]), (decision.kind, decision.actors))
        self.assertEqual("graveyard", self.current(game, commander).zone)

        command = {"action_id": "command"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("command", self.current(game, commander).zone)
        for token in tokens:
            self.assertNotIn(token.object_id, game.state.players["B"].zones["graveyard"])
        self.until(game, lambda current: len(self.delays(current, source)) == 3)
        game = self.replay(game, load=True)
        self.begin_delays(game, source, "A", count=3)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("command", self.current(game, commander).zone)
        for token in tokens:
            self.assertNotIn(token.object_id, game.state.players["A"].zones["battlefield"])
        self.assertEqual([], self.delays(game, source))
        self.replay(game, load=True)

    def test_actual_reentered_graveyard_incarnation_is_not_returned(self):
        game = self.session(611020104)
        source = self.add(game.engine, "Grave Betrayal")
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        reanimate = self.add(game.engine, "Reanimate", zone="hand")
        first = self.add(game.engine, "Murder", zone="hand", ref="first-lethal-spell")
        second = self.add(game.engine, "Murder", zone="hand", ref="second-lethal-spell")
        self.ready(game, source, reanimate, first, second, mana={"B": 5, "C": 2})

        self.kill(game, first, bear, reserve={"B": 3, "C": 1})
        original = self.current(game, bear).logical_object_id
        delay = self.resolve_death(game, source)
        self.cast_for(game, "A", reanimate, [self.current(game, bear).ref], reserve={"B": 2, "C": 1})
        self.resolve_top(game)
        self.assertEqual(("battlefield", "B", "A"),
                         (self.current(game, bear).zone, self.current(game, bear).owner, self.current(game, bear).controller))
        self.kill(game, second, bear)
        replacement = self.current(game, bear).logical_object_id
        self.assertEqual("graveyard", self.current(game, bear).zone)
        self.assertNotEqual(original, replacement)
        self.assertEqual([], self.source_stack(game, source))
        self.assertEqual([delay.ref], [entry.ref for entry in self.delays(game, source)])

        game = self.replay(game, load=True)
        self.begin_delays(game, source, "A")
        self.resolve_top(game)
        self.assertEqual(("graveyard", replacement),
                         (self.current(game, bear).zone, self.current(game, bear).logical_object_id))
        self.replay(game, load=True)

    def test_late_death_waits_for_the_next_actual_end_step(self):
        game = self.session(611020105)
        source = self.add(game.engine, "Grave Betrayal")
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        kill = self.add(game.engine, "Murder", zone="hand")
        self.add(game.engine, "Swamp", ref="ending-first-swamp")
        self.add(game.engine, "Swamp", ref="ending-second-swamp")
        self.add(game.engine, "Plains", ref="ending-generic")
        self.ready(game, source, kill)
        self.until(game, lambda current: current.state.step == "end_step" and current.state.active_player == "A")

        self.kill(game, kill, bear)
        self.resolve_death(game, source)
        self.assertEqual(("A", "end_step", "graveyard"),
                         (game.state.active_player, game.state.step, self.current(game, bear).zone))
        self.assertEqual([], self.source_stack(game, source))
        game = self.replay(game, load=True)
        self.begin_delays(game, source, "B")
        self.resolve_top(game)
        self.assert_returned(game, bear, owner="B")
        self.replay(game, load=True)

    def test_actual_return_adds_one_counter_over_intrinsic_entry_counters(self):
        game = self.session(611020106)
        source = self.add(game.engine, "Grave Betrayal")
        worker = self.add(game.engine, "Arcbound Worker", seat="B")
        worker.counters["+1/+1"] = 4
        kill = self.add(game.engine, "Murder", zone="hand")
        self.ready(game, source, worker, kill, mana={"B": 2, "C": 1})

        self.kill(game, kill, worker)
        self.assertEqual("graveyard", self.current(game, worker).zone)
        self.resolve_death(game, source)
        self.begin_delays(game, source, "A")
        self.resolve_top(game)
        current = self.current(game, worker)
        self.assertEqual(("battlefield", "B", "A"), (current.zone, current.owner, current.controller))
        self.assertEqual(2, current.counters.get("+1/+1", 0))
        self.assertEqual({"B"}, self.colors(game, worker))
        self.assertEqual(({"artifact", "creature"}, {"construct", "zombie"}), self.types(game, worker))
        self.replay(game, load=True)

    def test_animated_artifact_returns_as_black_and_uses_zombie_only_while_creature(self):
        game = self.session(611020107)
        source = self.add(game.engine, "Grave Betrayal")
        ring = self.add(game.engine, "Sol Ring", seat="B")
        first = self.add(game.engine, "Karn's Touch", zone="hand", ref="first-artifact-animation")
        second = self.add(game.engine, "Karn's Touch", seat="B", zone="hand", ref="later-artifact-animation")
        kill = self.add(game.engine, "Murder", zone="hand")
        self.add(game.engine, "Island", seat="B", ref="blue-for-later-animation-1")
        self.add(game.engine, "Island", seat="B", ref="blue-for-later-animation-2")
        self.ready(game, source, first, second, kill, mana={"B": 2, "C": 1, "U": 2})

        self.cast_for(game, "A", first, [ring.ref], reserve={"B": 2, "C": 1})
        self.resolve_top(game)
        self.assertIn("creature", self.types(game, ring)[0])
        self.kill(game, kill, ring)
        self.assertEqual(({"artifact"}, set()), self.types(game, ring))
        self.resolve_death(game, source)
        self.begin_delays(game, source, "A")
        self.resolve_top(game)
        identity = self.assert_returned(game, ring, owner="B").logical_object_id
        self.assertEqual(({"artifact"}, set()), self.types(game, ring))
        self.assertEqual({"B"}, self.colors(game, ring))

        self.cast_for(game, "B", second, [self.current(game, ring).ref])
        self.resolve_top(game)
        self.assertEqual(identity, self.current(game, ring).logical_object_id)
        self.assertEqual(({"artifact", "creature"}, {"zombie"}), self.types(game, ring))
        self.assertEqual({"B"}, self.colors(game, ring))
        self.until(game, lambda current: current.state.active_player == "B")
        self.assertEqual(identity, self.assert_returned(game, ring, owner="B").logical_object_id)
        self.assertEqual(({"artifact"}, set()), self.types(game, ring))
        self.assertEqual({"B"}, self.colors(game, ring))
        self.replay(game, load=True)

    def test_competing_delays_return_once_in_apnap_order(self):
        game = self.session(611020108)
        first_source = self.add(game.engine, "Grave Betrayal", ref="active-betrayal")
        second_source = self.add(game.engine, "Grave Betrayal", seat="C", ref="nonactive-betrayal")
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        kill = self.add(game.engine, "Murder", zone="hand")
        self.ready(game, first_source, second_source, kill, mana={"B": 2, "C": 1})

        self.kill(game, kill, bear)
        self.assertEqual(["A", "C"], [item.controller for item in game.state.stack])
        self.assertEqual("graveyard", self.current(game, bear).zone)
        self.resolve_top(game)
        self.resolve_top(game)
        refs = {delay.ref for source in (first_source, second_source) for delay in self.delays(game, source)}
        self.assertEqual(2, len(refs))
        game = self.replay(game, load=True)

        def delayed_stack(current):
            return [item for item in current.state.stack if item.context.get("delayed_trigger_ref") in refs]

        self.until(game, lambda current: current.state.step == "end_step" and len(delayed_stack(current)) == 2)
        self.assertEqual("A", game.state.active_player)
        self.assertEqual(["A", "C"], [item.controller for item in delayed_stack(game)])
        self.resolve_top(game)
        current = self.current(game, bear)
        identity = current.logical_object_id
        self.assertEqual(("battlefield", "B", "C"), (current.zone, current.owner, current.controller))
        self.assertEqual(1, current.counters.get("+1/+1", 0))
        self.resolve_top(game)
        current = self.current(game, bear)
        self.assertEqual(("battlefield", "B", "C", identity),
                         (current.zone, current.owner, current.controller, current.logical_object_id))
        self.assertEqual(1, current.counters.get("+1/+1", 0))
        self.replay(game, load=True)

    def test_actual_return_redirect_keeps_its_own_counter_without_entry_riders(self):
        game = self.session(611020109)
        source = self.add(game.engine, "Grave Betrayal")
        bear = self.add(game.engine, "Grizzly Bears", seat="B")
        kill = self.add(game.engine, "Murder", zone="hand")
        self.ready(game, source, kill, mana={"B": 2, "C": 1})
        self.kill(game, kill, bear)
        self.assertEqual("graveyard", self.current(game, bear).zone)
        self.resolve_death(game, source)

        redirect = ReplacementEffect(
            effect_id="test:betrayal-return-redirect",
            source_id="test:replacement-void-counter",
            event_kind="zone.change",
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "object_ref": {"eq": bear.ref},
                "origin": {"eq": "graveyard"},
                "destination": {"eq": "battlefield"},
            },
            operations=(
                SetField("destination", "exile"),
                CreateAffectedObjectCounter("void", 1, "C", "test:replacement-void-counter"),
            ),
        )
        collect = zone_replacements.collect_zone_change_replacement_effects

        def collect_with_redirect(host, **kwargs):
            return (*collect(host, **kwargs), redirect)

        with patch.object(zone_replacements, "collect_zone_change_replacement_effects", collect_with_redirect):
            self.begin_delays(game, source, "A")
            self.resolve_top(game)
            current = self.current(game, bear)
            self.assertEqual(("exile", "B", "B"), (current.zone, current.owner, current.controller))
            self.assertEqual({"void": 1}, current.counters)
            self.assertEqual({"G"}, self.colors(game, bear))
            self.assertEqual(({"creature"}, {"bear"}), self.types(game, bear))
            self.assertTrue(any(
                event.code == "replacement.apply" and event.details.get("effect_id") == redirect.effect_id
                and event.details.get("destination") == "exile"
                for event in game.state.events
            ))
            self.replay(game, load=True)

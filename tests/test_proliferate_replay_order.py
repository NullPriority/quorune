"""Native replay preserves ordinary and sequential Proliferate choices."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT, packet_without_decision_capability
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.counter_placement_event_model import PROLIFERATE_ORDER_VERSION_FIELD
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class OriginalProliferateReplayOrderTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "proliferate-order.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/proliferate-replay-order-cards.json"], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Proliferate order", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_actual_thrummingbird_choice_order_survives_checkpoint_and_resume(self):
        game = self.session(70134091)
        bird = self.add(game.engine, "Thrummingbird")
        bird.acquired_control_turn_count = -1
        self.assert_closed(game, bird)
        first = self.add(game.engine, "Island", seat="B", ref="z-countered-permanent")
        second = self.add(game.engine, "Island", seat="C", ref="a-countered-permanent")
        for permanent in (first, second):
            permanent.counters["charge"] = 1
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(bird, "B")])
        self.until(game, lambda current: self.trigger_count(current, bird) == 1)
        pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        packet = game.packet("pilot:A", full=True)["decision"]
        refs = tuple(packet["legal_actions"][0]["choice_schema"]["legal_refs"])
        self.assertEqual((second.ref, first.ref), refs)
        self.assertEqual(1, pending.continuation["effect"][PROLIFERATE_ORDER_VERSION_FIELD])
        restored = self.replay(game, load=True)
        packet = restored.packet("pilot:A", full=True)["decision"]
        self.assertEqual(refs, tuple(packet["legal_actions"][0]["choice_schema"]["legal_refs"]))
        action = restored.act("pilot:A", {"action_id": "choose", "objects": [first.ref, second.ref]})
        self.assertTrue(action.ok, action.summary)
        for permanent in (first, second):
            self.assertEqual(2, restored.state.cards[permanent.object_id].counters["charge"])
        self.replay(restored, load=True)


class SequentialProliferateReplayTests(InheritedNativeFixture, unittest.TestCase):
    source_name = "Generic Sequential Proliferate Artifact"
    cast_price = 1
    activation_price = 3
    commander = "Isamaru, Hound of Konda"
    creature = "Grizzly Bears"
    land = "Island"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "sequential-proliferate.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/proliferate-replay-order-cards.json",
            ROOT / "tests/fixtures/ability-source-counter-cards.json",
            ROOT / "tests/fixtures/counter-replacement-cards.json",
            ROOT / "tests/fixtures/sequential-proliferate-witness-cards.json",
        ], db)
        cls.db = CardDatabase(db)
        cls.configure_deck()

    @classmethod
    def configure_deck(cls):
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Sequential Proliferate", [
            DeckEntry(cls.commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [cls.commander])

    def command(self, game, actor="A", **command):
        result = game.act(f"pilot:{actor}", command)
        self.assertTrue(result.ok, result.summary)
        return result

    def activation_offer(self, game, source):
        self.priority(game, "A")
        decision = game.packet("pilot:A", full=True)["decision"]
        return next(
            (row for row in decision["ctx"]["legal"]["actions"]
             if row["id"].startswith(f"activate:{source.ref}:")),
            None,
        )

    def activate(self, game, source):
        offer = self.activation_offer(game, source)
        self.assertIsNotNone(offer)
        mana = game.state.players["A"].mana_pool["C"]
        self.command(game, action_id=offer["id"], pay="auto")
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(mana - self.activation_price, game.state.players["A"].mana_pool.get("C", 0))
        item = game.state.stack[-1]
        self.assertEqual("activated_ability", item.kind)
        self.assertFalse(item.targets)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        return self.proliferate_offer(game)

    def proliferate_offer(self, game):
        decision = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual("semantic.choice", decision["kind"])
        self.assertEqual("proliferate", decision["ctx"]["operation"])
        self.assertEqual(("pilot:A",), tuple(game.pending_principals()))
        pending = game.state.pending_decision
        self.assertEqual(1, pending.continuation["effect"][PROLIFERATE_ORDER_VERSION_FIELD])
        return tuple(decision["legal_actions"][0]["choice_schema"]["legal_refs"])

    def choose(self, game, *refs):
        self.command(game, action_id="choose", objects=list(refs))

    def choose_replacement(self, game, source_ref):
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        option, = (row for row in options if row["source"] == source_ref and not row["decline"])
        self.command(game, action_id="choose", replacement=option["id"])

    def test_separate_native_choices_resume_before_state_based_actions(self):
        game = self.session(70134101)
        source = self.add(game.engine, self.source_name, zone="hand")
        first = self.add(game.engine, self.creature, seat="B", ref="first-countered-bear")
        second = self.add(game.engine, self.creature, seat="C", ref="second-countered-bear")
        private = self.add(game.engine, self.land, seat="D", zone="hand", ref="private-proliferate-card")
        second.counters.update({"charge": 2, "stun": 1})
        game.state.players["B"].poison = 1
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": self.cast_price + self.activation_price})
        self.checkpoint(game)

        self.cast_for(game, "A", source, reserve={"C": self.activation_price})
        self.until(game, lambda current: (
            current.state.pending_decision is not None
            and current.state.pending_decision.kind == "semantic.target"
        ))
        self.command(game, action_id="choose", targets=["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"-1/-1": 1}, game.state.cards[first.object_id].counters)
        self.assertEqual({"charge": 2, "stun": 1}, game.state.cards[second.object_id].counters)

        refs = self.activate(game, source)
        self.assertEqual((first.ref, second.ref, "B"), refs)
        projected = packet_without_decision_capability(game.packet("pilot:A", full=True))
        self.assertNotIn(private.ref, str(projected))
        before = authoritative_state_hash(game.state)
        for actor, payload in (
            ("B", {"action_id": "pass"}),
            ("B", {"action_id": "choose", "objects": [first.ref]}),
            ("A", {"action_id": "choose", "objects": [private.ref]}),
            ("A", {"action_id": "choose", "objects": [first.ref, first.ref]}),
        ):
            self.assertFalse(game.act(f"pilot:{actor}", payload).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))

        game = self.replay(game, load=True)
        self.assertEqual(refs, self.proliferate_offer(game))
        self.choose(game, first.ref, "B")
        self.assertEqual(refs, self.proliferate_offer(game))
        self.assertEqual(2, game.state.players["B"].poison)
        self.assertEqual("battlefield", game.state.cards[first.object_id].zone)
        self.assertEqual({"-1/-1": 2}, game.state.cards[first.object_id].counters)
        self.assertEqual({"charge": 2, "stun": 1}, game.state.cards[second.object_id].counters)

        game = self.replay(game, load=True)
        self.assertEqual(refs, self.proliferate_offer(game))
        self.choose(game, second.ref, "B")
        self.assertEqual(3, game.state.players["B"].poison)
        self.assertEqual({"charge": 3, "stun": 2}, game.state.cards[second.object_id].counters)
        self.assertEqual("graveyard", game.state.cards[first.object_id].zone)
        self.replay(game, load=True)

    def test_each_native_choice_restarts_counter_replacement_order(self):
        game = self.session(70134102)
        source = self.add(game.engine, self.source_name)
        first = self.add(game.engine, self.land, ref="replacement-first-charge")
        second = self.add(game.engine, self.land, ref="replacement-second-charge")
        doubler = self.add(game.engine, "Doubling Season")
        increaser = self.add(game.engine, "Doc Samson, Super Psychiatrist")
        for permanent in (first, second):
            permanent.counters["charge"] = 1
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": self.activation_price})
        self.checkpoint(game)
        self.activate(game, source)

        self.choose(game, first.ref)
        self.assertEqual(1, game.state.cards[first.object_id].counters["charge"])
        game = self.replay(game, load=True)
        self.choose_replacement(game, increaser.ref)
        self.proliferate_offer(game)
        self.assertEqual(5, game.state.cards[first.object_id].counters["charge"])
        self.assertEqual(1, game.state.cards[second.object_id].counters["charge"])

        game = self.replay(game, load=True)
        self.choose(game, second.ref)
        self.assertEqual(1, game.state.cards[second.object_id].counters["charge"])
        game = self.replay(game, load=True)
        self.choose_replacement(game, doubler.ref)
        self.assertEqual(5, game.state.cards[first.object_id].counters["charge"])
        self.assertEqual(4, game.state.cards[second.object_id].counters["charge"])
        self.replay(game, load=True)

    def test_empty_first_choice_allows_second_choice_after_paying_once(self):
        game = self.session(70134103)
        source = self.add(game.engine, self.source_name)
        first = self.add(game.engine, self.land, seat="B", ref="empty-first-permanent")
        first.counters["charge"] = 1
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": self.activation_price})
        self.checkpoint(game)
        self.assertEqual((first.ref,), self.activate(game, source))
        self.choose(game)
        self.assertEqual((first.ref,), self.proliferate_offer(game))
        self.assertEqual(1, game.state.cards[first.object_id].counters["charge"])

        game = self.replay(game, load=True)
        self.choose(game, first.ref)
        self.assertEqual(2, game.state.cards[first.object_id].counters["charge"])
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(0, game.state.players["A"].mana_pool.get("C", 0))
        self.assertIsNone(self.activation_offer(game, game.state.cards[source.object_id]))
        self.replay(game, load=True)

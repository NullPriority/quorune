"""Native replay preserves ordinary and sequential Proliferate choices."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT, packet_without_decision_capability
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.counter_placement_event_model import PROLIFERATE_ORDER_VERSION_FIELD
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_subscriptions import FixedEventSubscriptionSet
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


FRANK = "Agent Frank Horrigan"


class SourceEntryAndAttackProliferateTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "source-entry-attack-proliferate.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "proliferate-replay-order-cards.json",
            fixtures / "source-attack-alone-cards.json",
            fixtures / "activation-paid-creature-subtype-cards.json",
            fixtures / "attacking-card-entry-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Entry and attack proliferation", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _act(self, game, *, actor="A", **command):
        result = game.act("pilot:" + actor, command)
        self.assertTrue(result.ok, result.summary)

    def _offer(self, game, *, actor="A"):
        self.assertEqual(("pilot:" + actor,), tuple(game.pending_principals()))
        offered = game.packet("pilot:" + actor, full=True)["decision"]
        self.assertEqual(("semantic.choice", "proliferate"), (offered["kind"], offered["ctx"]["operation"]))
        pending = game.state.pending_decision
        self.assertEqual(1, pending.continuation["effect"][PROLIFERATE_ORDER_VERSION_FIELD])
        return pending.decision_id, tuple(offered["legal_actions"][0]["choice_schema"]["legal_refs"])

    def _check_no_priority(self, game, other_actor, ref):
        before = authoritative_state_hash(game.state)
        for action in ({"action_id": "pass"}, {"action_id": "choose", "objects": [ref]}):
            self.assertFalse(game.act("pilot:" + other_actor, action).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))

    def _stop_at_proliferate(self, game, source, event):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual("semantic.choice", pending.kind)
        matching = [item for item in game.state.stack if item.source_object_id == source.object_id]
        item, = matching
        self.assertEqual(("triggered_ability", event), (item.kind, item.context["event"]))
        self.assertFalse(item.targets)
        return item.semantic_key

    def test_original_compiler_keeps_the_exact_two_subscriptions_and_each_leaf(self):
        record = self.db.lookup(FRANK, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, FRANK)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        trigger, = [program for program in semantics.programs_for_oracle(record.oracle_id)
                    if any(effect.get("op") == "proliferate" for effect in program.effects)]
        expected = {"op": "proliferate", PROLIFERATE_ORDER_VERSION_FIELD: 1}
        self.assertEqual([expected, expected], trigger.effects)
        subscriptions = FixedEventSubscriptionSet.from_condition(trigger.event_condition)
        self.assertEqual(("permanent.enter", "creature.attacks"), tuple(row.event for row in subscriptions.subscriptions))
        for subscription in subscriptions.subscriptions:
            self.assertEqual({"field": "card", "op": "eq", "value": "$source.ref"}, subscription.condition)
        condition = thaw_value(trigger.event_condition)
        condition["event_subscriptions"][1]["condition"]["value"] = "another-source"
        for mutant in (replace(trigger, effects=(expected,)), replace(trigger, event_condition=condition),
                       replace(trigger, effects=(expected, {"op": "proliferate"}))):
            self.assertFalse(bind_semantic_program_runtime(
                mutant, capability_registry=self.registry, profile="commander_review",
            )["strict"])
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status)
        node, = [node for face in ir.faces for node in face.nodes if node.kind == "triggered_ability"]
        self.assertEqual(node.text, record.oracle_text[node.span.start:node.span.end])
        with patch("quorune.compiler.fixed_effect_clause_sequences.fixed_proliferate_sequence_template", return_value=None):
            self.assertEqual("partial", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile="commander_review",
            ).status)

    def test_native_entry_and_declared_attack_each_resume_two_current_counter_choices(self):
        game = self.session(70134111)
        frank = self.add(game.engine, FRANK, zone="hand")
        fervor = self.add(game.engine, "Fervor")
        first = self.add(game.engine, "Island", seat="B", ref="z-countered-land")
        second = self.add(game.engine, "Island", seat="C", ref="a-countered-land")
        first.counters.update({"charge": 1, "shield": 1})
        second.counters["charge"] = 2
        game.state.players["B"].poison = 1
        for source in (frank, fervor):
            self.assert_closed(game, source)
        self.prepare(game, mana={"C": 5, "B": 1, "G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", frank)
        entry_key = self._stop_at_proliferate(game, frank, "permanent.enter")
        self.assertIn("trample", game.engine._combat_keywords(game.state.cards[frank.object_id]))
        self.assertNotIn("indestructible", game.engine._combat_keywords(game.state.cards[frank.object_id]))
        offer_one, refs = self._offer(game)
        self.assertEqual((second.ref, first.ref, "B"), refs)
        self._act(game, action_id="choose", objects=[first.ref, "B"])
        offer_two, remaining = self._offer(game)
        self.assertNotEqual(offer_one, offer_two)
        self.assertEqual(refs, remaining)
        self.assertEqual({"charge": 2, "shield": 2}, game.state.cards[first.object_id].counters)
        self.assertEqual(2, game.state.players["B"].poison)
        self._check_no_priority(game, "B", first.ref)
        game = self.replay(game, load=True)
        self.assertEqual((offer_two, refs), self._offer(game))
        self._act(game, action_id="choose", objects=["B", second.ref, first.ref])
        self.assertEqual({"charge": 3, "shield": 3}, game.state.cards[first.object_id].counters)
        self.assertEqual({"charge": 3}, game.state.cards[second.object_id].counters)
        self.assertEqual(3, game.state.players["B"].poison)

        self.attack(game, [(game.state.cards[frank.object_id], "D")])
        self.assertIn("indestructible", game.engine._combat_keywords(game.state.cards[frank.object_id]))
        self.assertEqual(entry_key, self._stop_at_proliferate(game, frank, "creature.attacks"))
        first_attack_choice, refs = self._offer(game)
        self._act(game, action_id="choose", objects=[second.ref])
        self.assertEqual({"charge": 4}, game.state.cards[second.object_id].counters)
        second_attack_choice, _ = self._offer(game)
        self.assertNotEqual(first_attack_choice, second_attack_choice)
        game = self.replay(game, load=True)
        self.assertEqual((second_attack_choice, refs), self._offer(game))
        self._act(game, action_id="choose", objects=["B"])
        self.assertEqual(4, game.state.players["B"].poison)
        self.assertEqual({"charge": 4}, game.state.cards[second.object_id].counters)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_attack_indestructibility_crosses_control_but_not_battlefield_incarnation(self):
        game = self.session(70134112)
        frank = self.add(game.engine, FRANK)
        frank.acquired_control_turn_count = -1
        receiver = self.add(game.engine, "Island", ref="countered-land")
        receiver.counters["charge"] = 1
        theft = self.add(game.engine, "Act of Treason", seat="B", zone="hand")
        flash = self.add(game.engine, "Vedalken Orrery", seat="B")
        blink = self.add(game.engine, "Cloudshift", seat="B", zone="hand")
        mountains = [self.add(game.engine, "Mountain", seat="B", ref=f"theft-mountain-{index}") for index in range(3)]
        self.add(game.engine, "Plains", seat="B", ref="blink-plains")
        for source in (frank, flash, blink, theft):
            self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(frank, "D")])
        self._stop_at_proliferate(game, frank, "creature.attacks")
        for _ in range(2):
            self._offer(game)
            self._act(game, action_id="choose", objects=[])
        incarnation = game.state.cards[frank.object_id].logical_object_id
        self.priority(game, "B")
        for land in mountains:
            options = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
            mana, = [row["id"] for row in options if row["id"].startswith(f"activate:{land.ref}:")]
            self._act(game, actor="B", action_id=mana, pay="auto")
        self.cast_for(game, "B", theft, [frank.ref])
        self.resolve_top(game)
        moved = game.state.cards[frank.object_id]
        self.assertEqual(("B", incarnation), (moved.controller, moved.logical_object_id))
        self.assertIn("indestructible", game.engine._combat_keywords(moved))
        self.cast_for(game, "B", blink, [frank.ref])
        self.resolve_top(game)
        returned = game.state.cards[frank.object_id]
        self.assertEqual("B", returned.controller)
        self.assertNotEqual(incarnation, returned.logical_object_id)
        keywords = game.engine._combat_keywords(returned)
        self.assertIn("trample", keywords)
        self.assertNotIn("indestructible", keywords)
        self._stop_at_proliferate(game, frank, "permanent.enter")
        for _ in range(2):
            self._offer(game, actor="B")
            self._act(game, actor="B", action_id="choose", objects=[])
        self.replay(game, load=True)

    def test_put_onto_battlefield_attacking_is_only_one_entry_occurrence(self):
        game = self.session(70134113)
        frank = self.add(game.engine, FRANK, zone="graveyard")
        nephilim = self.add(game.engine, "Yore-Tiller Nephilim")
        nephilim.acquired_control_turn_count = -1
        receiver = self.add(game.engine, "Island", ref="countered-land")
        receiver.counters["charge"] = 1
        for source in (frank, nephilim):
            self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(nephilim, "B")])
        self.assertEqual("semantic.target", game.state.pending_decision.kind)
        self._act(game, action_id="choose", targets=[frank.ref])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self._act(game, action_id="choose", choice="B")
        current = game.state.cards[frank.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertIn(current.object_id, game.state.combat.attackers)
        self.assertNotIn("indestructible", game.engine._combat_keywords(current))
        self._stop_at_proliferate(game, frank, "permanent.enter")
        for _ in range(2):
            self._offer(game)
            self._act(game, action_id="choose", objects=[receiver.ref])
        self.assertEqual({"charge": 3}, game.state.cards[receiver.object_id].counters)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)


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

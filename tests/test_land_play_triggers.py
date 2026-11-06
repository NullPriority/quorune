from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_public_action_event_bindings import fixed_public_action_event_binding_spec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.event_subscriptions import FixedEventSubscriptionSet, LAND_PLAY_EVENT
from scripts.build_test_database import build_fixture_database


class LandPlayTriggerCompilerTests(unittest.TestCase):
    def test_land_and_cast_subscriptions_keep_their_own_origin_filters(self):
        binding = fixed_public_action_event_binding_spec(
            "Whenever you play a land from exile or cast a spell, draw a card.",
        )
        self.assertIsNotNone(binding)
        subscriptions = FixedEventSubscriptionSet.from_condition(binding.condition)
        self.assertEqual(LAND_PLAY_EVENT, binding.event)
        self.assertEqual(
            (
                {"field": "player", "op": "eq", "value": "$source.controller"},
                {"field": "from", "op": "eq", "value": "exile"},
            ),
            tuple(dict(part) for part in subscriptions.subscription_for(LAND_PLAY_EVENT).condition["all"]),
        )
        self.assertEqual(
            {"field": "controller", "op": "eq", "value": "$source.controller"},
            dict(subscriptions.subscription_for("spell.cast").condition),
        )
        self.assertIsNone(fixed_public_action_event_binding_spec(
            "Whenever an opponent plays a land from their library, draw a card.",
        ))
        self.assertIsNone(fixed_public_action_event_binding_spec(
            "Whenever you put a land onto the battlefield, draw a card.",
        ))


class LandPlayTriggerNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "land-play-triggers.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/land-play-trigger-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Land-play triggers", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 32),
        ], ["Generic Bound Commander"])

    def play_land(self, session, card):
        self.priority(session, "A")
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer = next(row for row in actions if row["id"].startswith("play-land:") and row.get("card") == card.ref)
        command = {"action_id": offer["id"]}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_play_waits_for_apnap_and_burgeoning_entry_does_not_repeat_it(self):
        session = self.session(718001)
        session.state.config.semantic_policy = "trusted_only"
        horn = self.add(session.engine, "Horn of Greed", seat="B")
        burgeoning = self.add(session.engine, "Burgeoning", seat="B")
        wurm = self.add(session.engine, "Dirtcowl Wurm", seat="C")
        pangosaur = self.add(session.engine, "Pangosaur")
        baloth = self.add(session.engine, "Canopy Baloth")
        played_land = self.add(session.engine, "Mountain", zone="hand", ref="played-land")
        placed_land = self.add(session.engine, "Mountain", seat="B", zone="hand", ref="placed-land")
        for card in (horn, burgeoning, wurm, pangosaur, baloth):
            self.assert_closed(session, card)
        program = next(item for item in session.engine.semantics.programs_for_oracle(horn.oracle_id)
                       if item.event == LAND_PLAY_EVENT)
        required = "trigger.event.normalized_public_action"
        remaining = [item for item in program.capability_dependencies if item != required]
        stripped = replace(program, capability_dependencies=remaining,
                           capability_closure=self.registry.closure(remaining, profile="commander_review").to_dict())
        rejected = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile="commander_review")
        self.assertIn(f"capability:undeclared_runtime_dependency:{required}", rejected["blockers"])

        self.prepare(session)
        self.checkpoint(session)
        hands = {seat: set(session.state.players[seat].zones["hand"]) for seat in "ABCD"}
        self.play_land(session, played_land)
        for seat, sources in (("A", (pangosaur, baloth)), ("B", (horn, burgeoning))):
            decision = session.state.pending_decision
            self.assertEqual("trigger.order", decision.kind)
            self.assertEqual([seat], list(decision.actors))
            triggers = decision.payload_by_actor[seat]["triggers"]
            self.assertEqual(len(sources), len(triggers))
            result = session.act(f"pilot:{seat}", {"action_id": "order", "triggers": [
                next(row["id"] for row in triggers if card.printed_name in row["label"])
                for card in sources
            ]})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            [card.object_id for card in (pangosaur, baloth, horn, burgeoning, wurm)],
            [item.source_object_id for item in session.state.stack],
        )
        self.resolve_top(session)
        self.assertEqual(1, session.state.cards[wurm.object_id].counters["+1/+1"])

        pending = self.resolve(session)
        self.assertEqual("semantic.choice", pending.kind)
        self.assertEqual(["B"], list(pending.actors))
        self.assertIsNone(session.packet("pilot:A", full=True)["decision"])
        session = self.replay(session, load=True)
        result = session.act("pilot:B", {"action_id": "choose", "card": placed_land.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[placed_land.object_id].zone)
        self.assertEqual("hand", session.state.cards[pangosaur.object_id].zone)
        self.assertEqual(6, session.engine._numeric_stat(baloth.object_id, "power"))
        self.assertEqual(1, session.state.cards[wurm.object_id].counters["+1/+1"])
        self.assertEqual(hands["B"] - {placed_land.object_id}, set(session.state.players["B"].zones["hand"]))
        drawn, = set(session.state.players["A"].zones["hand"]) - hands["A"] - {pangosaur.object_id}
        self.assertNotIn(session.state.cards[drawn].ref, json.dumps(session.packet("pilot:B", full=True)["state"]))
        self.replay(session, load=True)

    def test_actual_flubs_discards_after_a_cast_and_draws_after_a_play(self):
        session = self.session(718002)
        session.state.config.semantic_policy = "trusted_only"
        for object_id in tuple(session.state.players["A"].zones["hand"]):
            session.engine.move_card(object_id, "library", log=False)
        flubs = self.add(session.engine, "Flubs, the Fool")
        spell = self.add(session.engine, "Ornithopter", zone="hand")
        land = self.add(session.engine, "Mountain", zone="hand")
        discard = self.add(session.engine, "Generic Bound Body", zone="hand")
        self.assert_closed(session, flubs)
        self.prepare(session)
        self.checkpoint(session)

        self.cast(session, "A", spell)
        self.assertIsNotNone(self.resolve(session))
        self.assertEqual(["pilot:A"], list(session.pending_principals()))
        before = authoritative_state_hash(session.state)
        command = {"action_id": "choose", "cards": [discard.ref]}
        self.assertFalse(session.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[discard.object_id].zone)
        self.assertEqual([land.object_id], session.state.players["A"].zones["hand"])

        self.play_land(session, land)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, len(session.state.players["A"].zones["hand"]))
        self.assertEqual("battlefield", session.state.cards[land.object_id].zone)
        self.replay(session, load=True)

    def test_actual_pia_observes_both_exile_actions_and_ignores_hand_actions(self):
        session = self.session(718003)
        session.state.config.semantic_policy = "trusted_only"
        pia = self.add(session.engine, "Pia Nalaar, Consul of Revival")
        exploration = self.add(session.engine, "Exploration")
        impulse = self.add(session.engine, "Reckless Impulse", zone="hand")
        hand_land = self.add(session.engine, "Mountain", zone="hand", ref="pia-hand-land")
        exile_land = self.add(session.engine, "Mountain", zone="library", ref="pia-exile-land")
        exile_spell = self.add(session.engine, "Ornithopter", zone="library", ref="pia-exile-spell")
        for card in (pia, exploration, impulse):
            self.assert_closed(session, card)
        self.prepare(session, mana={"C": 1, "R": 1})
        self.checkpoint(session)

        self.play_land(session, hand_land)
        self.assertFalse(session.state.stack)
        self.cast(session, "A", impulse)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(["exile", "exile"], [session.state.cards[card.object_id].zone
                         for card in (exile_land, exile_spell)])
        self.assertFalse(any(card.is_token for card in session.state.cards.values()))

        self.play_land(session, exile_land)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, sum(card.is_token for card in session.state.cards.values()))
        self.cast(session, "A", exile_spell)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(2, len(tokens))
        for token in tokens:
            data = session.engine._effective_card_data(token)
            self.assertIn("Thopter", data["type_line"])
            self.assertIn("Flying", data["keywords"])
            self.assertIn("Haste", data["keywords"])
        self.replay(session, load=True)

    def test_actual_endstone_draws_for_a_play_and_sets_half_starting_life_rounded_up(self):
        session = self.session(718004)
        session.state.config.semantic_policy = "trusted_only"
        session.state.config.starting_life = 41
        session.state.players["A"].life = 37
        source = self.add(session.engine, "The Endstone")
        land = self.add(session.engine, "Mountain", zone="hand")
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        hand_count = len(session.state.players["A"].zones["hand"])

        self.play_land(session, land)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand_count, len(session.state.players["A"].zones["hand"]))
        self.until(session, lambda current: any(item.source_object_id == source.object_id
                                                  for item in current.state.stack))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(21, session.state.players["A"].life)
        self.replay(session, load=True)

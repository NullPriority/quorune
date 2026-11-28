"""Native control and intervening end-step condition for Agent of Treachery."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.characteristic_fragments import CharacteristicQuantityScope
from quorune.continuous_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD,
    FixedPublicStateConditionKind,
    FixedPublicStateConditionSpec,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_predicate import NONOWNER_CONTROL_QUERY_CAPABILITY, ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_bounded_hand_casts as hand_cast
from test_controller_creature_keyword_absence import register_card


CARD = "Agent of Treachery"
PROFILE = "commander_review"


class AgentOfTreacheryNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = hand_cast.BoundedHandCastRuntimeTests.start

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "agent-of-treachery.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "agent-of-treachery-cards.json",
            fixtures / "bound-effect-program-cards.json",
            fixtures / "bounded-hand-cast-cards.json",
            fixtures / "same-turn-graveyard-return-sets-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Agent of Treachery control",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def controllers(self, game, cards):
        return tuple(self.current(game, card).controller for card in cards)

    def after_two_gains(self, seed):
        game = self.start(seed)
        agent = self.add(game.engine, CARD, zone="hand", ref="agent-treachery-source")
        act = self.add(game.engine, "Act of Treason", zone="hand")
        expertise = self.add(game.engine, "Kari Zev's Expertise", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        artifact = self.add(game.engine, "Sol Ring", seat="B", ref="agent-foreign-artifact")
        self.add(game.engine, "Island", seat="B")
        self.add(game.engine, "Grizzly Bears", ref="agent-owned-decoy")
        c_creature = self.add(game.engine, "Grizzly Bears", seat="C", ref="agent-foreign-c-bear")
        d_creature = self.add(game.engine, "Grizzly Bears", seat="D", ref="agent-foreign-d-bear")
        cards = (artifact, c_creature, d_creature)
        draw_cards = tuple(self.add(game.engine, "Plains", zone="library", ref=f"agent-private-draw-{index}")
                           for index in range(3))
        for card in (agent, act, expertise, bounce):
            self.assert_closed(game, card)

        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 8, "U": 2, "R": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", agent, reserve={"C": 3, "R": 3})
        self.target(game, "A", [artifact.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("B", "A"), (self.current(game, artifact).owner, self.current(game, artifact).controller))

        self.cast_for(game, "A", act, [c_creature.ref], reserve={"C": 1, "R": 2})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("A", "A", "D"), self.controllers(game, cards))
        self.assertEqual(("B", "C", "D"), tuple(self.current(game, card).owner for card in cards))
        return game, agent, cards, expertise, bounce, draw_cards

    def gain_third(self, game, expertise, cards):
        self.cast(game, "A", expertise, [cards[-1].ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("A", "A", "A"), self.controllers(game, cards))

    def end_step(self, game):
        self.until(game, lambda current: current.state.active_player == "A" and current.state.step == "end_step")

    def draw_trigger(self, game, agent):
        self.end_step(game)
        (trigger,) = game.state.stack
        self.assertEqual(("triggered_ability", "A", agent.object_id),
                         (trigger.kind, trigger.controller, trigger.source_object_id))
        self.assertFalse(trigger.targets)
        return trigger.ref

    def test_complete_original_binds_indefinite_control_and_intervening_end_step(self):
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        original = self.db.lookup(CARD, fuzzy=False)
        compiled = compile_oracle_card(original, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        programs = semantics.programs_for_oracle(original.oracle_id)
        self.assertEqual(2, len(programs))
        entry, = (program for program in programs if program.target_schema is not None)
        draw, = (program for program in programs if program.target_schema is None)
        self.assertEqual({"battlefield"}, set(entry.target_schema["zones"]))
        self.assertEqual({"permanent"}, set(entry.target_schema["categories"]))
        self.assertEqual("step.begin", draw.event)
        (atom,) = (term for term in draw.event_condition["all"]
                   if term.get("field") == FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD)
        self.assertEqual(("truthy", True), (atom["op"], atom["value"]))
        condition = FixedPublicStateConditionSpec.from_dict(atom["condition"])
        self.assertEqual((FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST, 3),
                         (condition.kind, condition.amount))
        self.assertEqual(CharacteristicQuantityScope.CONTROLLER_ZONE, condition.quantity.scope)
        self.assertEqual(ObjectQuerySpec(zones=("battlefield",), controlled_by_nonowner=True),
                         condition.quantity.query)
        self.assertIn(NONOWNER_CONTROL_QUERY_CAPABILITY, draw.capability_dependencies)
        deps = tuple(dep for dep in draw.capability_dependencies if dep != NONOWNER_CONTROL_QUERY_CAPABILITY)
        missing = replace(draw, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{NONOWNER_CONTROL_QUERY_CAPABILITY}", bound["blockers"])

        modified = replace(original, oracle_text=original.oracle_text + "\nRewrite history.")
        self.assertNotEqual("exact", compile_oracle_card(
            modified, capability_registry=self.registry, capability_profile=PROFILE,
        ).status)

    def test_actual_entry_control_survives_agent_departure_and_draw_stays_private(self):
        game, agent, cards, expertise, bounce, draw_cards = self.after_two_gains(6034001)
        self.gain_third(game, expertise, cards)
        originals = tuple(card.object_id for card in draw_cards)
        self.assertEqual(set(originals), set(game.state.players["A"].zones["library"][-3:]))
        trigger = self.draw_trigger(game, agent)
        self.cast(game, "B", bounce, [agent.ref])
        self.resolve_top(game)
        self.assertEqual("hand", self.current(game, agent).zone)
        self.assertEqual((trigger,), tuple(item.ref for item in game.state.stack))
        self.assertEqual(("A", "A", "A"), self.controllers(game, cards))

        game = self.replay(game, load=True)
        drawn = len(game.state.players["A"].draw_history)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(drawn + 3, len(game.state.players["A"].draw_history))
        self.assertEqual({agent.object_id, *originals}, set(game.state.players["A"].zones["hand"]))
        owner_packet = json.dumps(game.packet("pilot:A", full=True))
        for card in draw_cards:
            self.assertIn(card.ref, owner_packet)
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            for card in draw_cards:
                self.assertNotIn(card.ref, packet)

        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > turn and current.state.active_player == "B")
        self.assertEqual(("A", "C", "D"), self.controllers(game, cards))
        self.assertEqual("B", self.current(game, cards[0]).owner)
        self.assertEqual("hand", self.current(game, agent).zone)
        self.replay(game)

    def test_actual_end_step_requires_three_nonowned_permanents_at_both_boundaries(self):
        game, agent, cards, expertise, bounce, draw_cards = self.after_two_gains(6034002)
        with_three = self.replay(game, load=True)
        before = len(game.state.players["A"].draw_history)
        self.end_step(game)
        self.assertEqual("battlefield", self.current(game, agent).zone)
        self.assertFalse(game.state.stack)
        self.assertEqual(before, len(game.state.players["A"].draw_history))
        self.replay(game)

        self.gain_third(with_three, expertise, cards)
        trigger = self.draw_trigger(with_three, agent)
        returned = cards[1]
        self.cast(with_three, "B", bounce, [returned.ref])
        self.resolve_top(with_three)
        self.assertEqual(("hand", "C"), (self.current(with_three, returned).zone,
                                         self.current(with_three, returned).owner))
        self.assertEqual((trigger,), tuple(item.ref for item in with_three.state.stack))
        self.assertTrue(all(self.current(with_three, card).controller == "A" for card in (cards[0], cards[2])))
        with_three = self.replay(with_three, load=True)
        self.assertIsNone(self.resolve(with_three))
        self.assertFalse(with_three.state.stack)
        self.assertEqual(before, len(with_three.state.players["A"].draw_history))
        self.assertTrue(all(self.current(with_three, card).zone == "library" for card in draw_cards))
        self.replay(with_three)

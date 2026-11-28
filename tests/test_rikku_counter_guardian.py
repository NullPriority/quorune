"""Actual movement from an opponent and scoped blocking after a counter placement."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_fixed_one_counter_movement as movement_fixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.declaration_rule_effects import ResolutionDeclarationRuleEffect
from quorune.deck import DeckDefinition, DeckEntry
from quorune.fixed_counter_movement_model import CHOSEN_OPPONENT_COUNTER_MOVEMENT_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.block_prohibitions import block_prohibition_verdict
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.counter_placement_blocking import COUNTER_PLACEMENT_BLOCKING_CAPABILITY
from quorune.semantic_runtime.activated_abilities import activated_abilities_from_descriptors
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
NAME = "Rikku, Resourceful Guardian"


class RikkuCounterGuardianTests(InheritedNativeFixture, unittest.TestCase):
    reject = movement_fixture.FixedOneCounterMovementTests.reject
    activate = movement_fixture.FixedOneCounterMovementTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "rikku-guardian.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/rikku-counter-guardian-cards.json",
            ROOT / "tests/fixtures/fixed-one-counter-movement-cards.json",
            ROOT / "tests/fixtures/counter-placement-event-cards.json",
            ROOT / "tests/fixtures/attached-counter-prohibition-cards.json",
            ROOT / "tests/fixtures/fixed-direct-phasing-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Rikku", [DeckEntry(commander, 1, "commander"),
            DeckEntry("Plains", 30)], [commander])

    def start(self, game, *cards, active="A"):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor="A", active=active)
        self.checkpoint(game)

    def live(self, game, card):
        return game.state.cards[card.object_id]

    def rules(self, game):
        return [rule for rule in game.state.continuous_effects if isinstance(rule, ResolutionDeclarationRuleEffect)
                and rule.block_opponents_of is not None]

    def test_printed_rikku_requires_original_controller_roles_and_a_committed_event(self):
        card = self.db.lookup(NAME, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        ability, = (p for p in programs if CHOSEN_OPPONENT_COUNTER_MOVEMENT_CAPABILITY in p.capability_dependencies)
        trigger, = (p for p in programs if COUNTER_PLACEMENT_BLOCKING_CAPABILITY in p.capability_dependencies)
        activation, = activated_abilities_from_descriptors(ability.handlers)
        self.assertTrue(activation.tap_source and activation.sorcery_speed)
        self.assertEqual({"GENERIC": 1}, {key: amount for key, amount in activation.mana.items() if amount})
        self.assertEqual(["opponent", "you"], [group["controller_relation"] for group in ability.target_schema["groups"]])
        self.assertIsNone(trigger.target_schema)
        for program, cap in ((ability, CHOSEN_OPPONENT_COUNTER_MOVEMENT_CAPABILITY),
                             (trigger, COUNTER_PLACEMENT_BLOCKING_CAPABILITY)):
            deps = tuple(value for value in program.capability_dependencies if value != cap)
            changed = replace(program, capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
            self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)["strict"])
        changes = (("an opponent controls", "you control"), ("your opponents control", "you control"),
                   ("Activate only as a sorcery.", ""), ("one or more counters", "a counter"))
        for old, new in changes:
            with self.subTest(old=old):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE).status)

    def test_steal_moves_the_chosen_kind_and_the_trigger_survives_rikkus_departure(self):
        game = self.session(79582001)
        source = self.add(game.engine, NAME)
        source.acquired_control_turn_count = -1
        donor = self.add(game.engine, "Grizzly Bears", seat="B", ref="rikku-opposing-donor")
        donor.counters.update({"charge": 2, "quest": 1})
        recipient = self.add(game.engine, "Grizzly Bears", ref="rikku-controlled-recipient")
        alien = self.add(game.engine, "Grizzly Bears", seat="C", ref="rikku-wrong-recipient")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="rikku-unseen-bounce-card")
        self.add(game.engine, "Forest")
        self.add(game.engine, "Island", seat="B")
        self.start(game, source, bounce)
        self.assertNotIn(bounce.ref, json.dumps(game.packet("pilot:A", full=True)))
        command = {"action_id": f"activate:{source.ref}:ab2", "pay": "auto"}
        for targets in ([recipient.ref, donor.ref], [donor.ref, alien.ref], [donor.ref, donor.ref]):
            self.reject(game, "A", {**command, "targets": targets})
        self.activate(game, source, "ab2", [donor.ref, recipient.ref])
        self.assertTrue(self.live(game, source).tapped)
        choice = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (choice.kind, choice.actors))
        offer, = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal_actions"]
        self.assertEqual(["charge", "quest"], offer["choice_schema"]["legal_values"])
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        selecting = {"action_id": "choose", "counter_kind": "charge"}
        self.reject(game, "B", selecting)
        self.reject(game, "A", {**selecting, "counter_kind": "flying"})
        game = self.replay(game, load=True)
        result = game.act("pilot:A", selecting)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(({"charge": 1, "quest": 1}, {"charge": 1}),
                         (self.live(game, donor).counters, self.live(game, recipient).counters))
        self.assertEqual(1, self.trigger_count(game, source))
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", self.live(game, source).zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(self.rules(game)))
        rule, = self.rules(game)
        self.assertEqual("A", rule.block_opponents_of)
        self.assertEqual(recipient.logical_object_id, rule.locked_objects[0].logical_object_id)
        game = self.replay(game, load=True)
        self.assertEqual("A", self.rules(game)[0].block_opponents_of)

    def test_counter_trigger_observes_one_mixed_placement_and_drops_a_returned_recipient(self):
        game = self.session(79582002)
        source = self.add(game.engine, NAME)
        donor = self.add(game.engine, "Grizzly Bears", seat="B", ref="rikku-mixed-recipient")
        spell = self.add(game.engine, "Generic Counter Event Mixed Kinds", zone="hand")
        blink = self.add(game.engine, "Cloudshift", seat="B", zone="hand")
        self.add(game.engine, "Plains", seat="B")
        self.start(game, source, spell, blink)
        self.cast(game, "A", spell, [donor.ref])
        self.resolve_top(game)
        self.assertEqual({"+1/+1": 1, "vigilance": 1}, self.live(game, donor).counters)
        self.assertEqual(1, self.trigger_count(game, source))
        identity = self.live(game, donor).logical_object_id
        self.cast(game, "B", blink, [donor.ref])
        self.resolve_top(game)
        self.assertNotEqual(identity, self.live(game, donor).logical_object_id)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], self.rules(game))
        self.replay(game, load=True)

    def test_opponent_block_rule_uses_trigger_controller_and_allows_an_unrelated_block(self):
        game = self.session(79582003)
        source = self.add(game.engine, NAME)
        protected = self.add(game.engine, "Grizzly Bears", seat="B", ref="rikku-protected-foreign-attacker")
        ordinary = self.add(game.engine, "Grizzly Bears", seat="B", ref="rikku-ordinary-foreign-attacker")
        own_blocker = self.add(game.engine, "Grizzly Bears", ref="rikku-allied-blocker")
        opposing_blocker = self.add(game.engine, "Grizzly Bears", seat="C", ref="rikku-opposing-blocker")
        spell = self.add(game.engine, "Battlegrowth", zone="hand")
        late = self.add(game.engine, "Battlegrowth", zone="hand", ref="rikku-later-battlegrowth")
        self.add(game.engine, "Forest")
        self.add(game.engine, "Forest", ref="rikku-later-battlegrowth-mana")
        for attacker in (protected, ordinary):
            attacker.acquired_control_turn_count = -1
        self.start(game, source, spell, late, active="B")
        self.cast(game, "A", spell, [protected.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(self.rules(game)))
        rule, = self.rules(game)
        self.assertEqual("A", rule.block_opponents_of)
        self.assertIsNone(block_prohibition_verdict(game.engine, self.live(game, protected), self.live(game, own_blocker)))
        self.assertEqual(False, block_prohibition_verdict(game.engine,
            self.live(game, protected), self.live(game, opposing_blocker))[0])
        self.until(game, lambda session: session.state.pending_decision.kind == "combat.attackers")
        attackers = {"action_id": "attack", "attackers": {protected.ref: "C", ordinary.ref: "C"}}
        result = game.act("pilot:B", attackers)
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda session: session.state.pending_decision.kind == "combat.blockers")
        game = self.replay(game, load=True)
        self.reject(game, "C", {"action_id": "block", "blocks": {opposing_blocker.ref: protected.ref}})
        result = game.act("pilot:C", {"action_id": "block", "blocks": {opposing_blocker.ref: ordinary.ref}})
        self.assertTrue(result.ok, result.summary)
        self.cast(game, "A", late, [ordinary.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual([opposing_blocker.object_id], game.state.combat.blockers[ordinary.object_id])
        self.assertEqual(2, len(self.rules(game)))
        game = self.replay(game, load=True)
        turn = game.state.turn_sequence
        self.until(game, lambda session: session.state.turn_sequence > turn)
        self.assertEqual([], self.rules(game))
        self.replay(game, load=True)

    def test_foreign_or_prevented_placement_does_not_trigger_rikku(self):
        for prevented in (False, True):
            with self.subTest(prevented=prevented):
                game = self.session(79582010 + prevented)
                source = self.add(game.engine, NAME)
                recipient = self.add(game.engine, "Grizzly Bears")
                actor = "A" if prevented else "B"
                spell = self.add(game.engine, "Battlegrowth", seat=actor, zone="hand")
                self.add(game.engine, "Forest", seat=actor)
                providers = (self.add(game.engine, "Solemnity", seat="D"),) if prevented else ()
                self.start(game, source, spell, *providers)
                self.cast(game, actor, spell, [recipient.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(int(not prevented), self.live(game, recipient).counters.get("+1/+1", 0))
                self.assertEqual([], self.rules(game))
                self.replay(game, load=True)

    def test_rikku_steal_witness_kills_a_wrong_counter_kind_mutation(self):
        from quorune.semantic_choices import fixed_counter_movement as owner

        original = owner._intent

        def wrong(effect, stack_ref, *, counter_name="+1/+1"):
            return original(effect, stack_ref, counter_name="quest")

        with patch.object(owner, "_intent", side_effect=wrong), self.assertRaisesRegex(AssertionError, "charge"):
            self.test_steal_moves_the_chosen_kind_and_the_trigger_survives_rikkus_departure()

    def test_rikku_block_witness_kills_turning_the_restriction_into_generic_evasion(self):
        from quorune import continuous_effect_state as owner

        original = owner.create_resolution_declaration_rule_effect

        def any_blocker(host, *, restriction, block_opponents_of=None, **kwargs):
            from quorune.rules.temporary_declaration_restrictions import temporary_declaration_restriction
            if block_opponents_of is not None:
                restriction = temporary_declaration_restriction("unblockable")
            return original(host, restriction=restriction, **kwargs)

        # The choice preserves the controller in the typed journal. A generic
        # unblockable rule both loses that state and bars the original controller.
        with patch("quorune.rules.temporary_declaration_restrictions.create_resolution_declaration_rule_effect",
                   side_effect=any_blocker), self.assertRaises(AssertionError):
            self.test_opponent_block_rule_uses_trigger_controller_and_allows_an_unrelated_block()

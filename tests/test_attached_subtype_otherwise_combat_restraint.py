"""The attached creature's current subtype selects the printed stat or combat branch."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateConditionSpec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Bonds of Faith"
TEMPLATE = "static-attached-subtype-otherwise-declaration-v1"
PROFILE = "commander_review"


class AttachedSubtypeOtherwiseCombatRestraintTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "attached-subtype-otherwise.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/attached-subtype-otherwise-combat-restraint-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Live attached creature subtype", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self):
        record = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [entry for entry in semantics.programs_for_oracle(record.oracle_id)
                    if entry.provenance.get("template_id") == TEMPLATE]
        return record, program

    def test_complete_original_keeps_enchant_and_complementary_typed_branches(self):
        card, program = self.original_program()
        positive, negative = program.handlers
        self.assertEqual("continuous.characteristics.fixed-public-state.v1", positive["handler_id"])
        self.assertEqual("ability.static.bound-declaration-rules.v1", negative["handler_id"])
        self.assertEqual((2, 2), (positive["modifier"]["power"], positive["modifier"]["toughness"]))
        printed_positive = FixedPublicStateConditionSpec.from_dict(positive["source_condition"])
        fragment = negative["fragment"]["value"]
        printed_negative = FixedPublicStateConditionSpec.from_dict(fragment["source_condition"])
        self.assertIs(printed_positive.kind, FixedPublicStateConditionKind.ATTACHED_MATCHES_QUERY)
        self.assertEqual(("human",), printed_positive.predicate.subtypes_any)
        self.assertEqual(printed_positive.predicate, replace(
            printed_negative.predicate, excluded_subtypes=(), subtypes_any=("human",),
        ))
        self.assertEqual(("human",), printed_negative.predicate.excluded_subtypes)
        self.assertEqual("attached", fragment["scope"])
        only_rule, = fragment["rules"]
        self.assertEqual({"attack", "block"}, set(only_rule["value"]["declarations"]))
        ir = compile_oracle_card(card, capability_registry=self.registry)
        self.assertTrue(any(node.template_id == "printed-keyword-list-v1" for face in ir.faces for node in face.nodes))
        for altered in (
            card.oracle_text.replace("Otherwise, it can't attack or block.", "Otherwise, destroy it."),
            card.oracle_text.replace("a Human", "a Human or a Soldier"),
            card.oracle_text.replace("it can't attack or block", "you can't attack or block"),
        ):
            result = compile_oracle_card(replace(card, oracle_text=altered), capability_registry=self.registry)
            self.assertNotEqual("exact", result.status)

    def test_raw_merged_branches_refuse_omission_or_a_different_subtype(self):
        _, program = self.original_program()
        positive, negative = program.handlers
        wrong = deepcopy(negative)
        wrong["fragment"]["value"]["source_condition"]["predicate"]["excluded_subtypes"] = ["elf"]
        for handlers in ((positive,), (positive, wrong), (negative, positive)):
            result = bind_semantic_program_runtime(replace(program, handlers=handlers),
                capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result["strict"], result)

    def scene(self, seed, *, active="A"):
        game = self.session(seed)
        engine = game.engine
        bonds = self.add(engine, NAME, zone="hand")
        human = self.add(engine, "Elite Vanguard")
        spare = self.add(engine, "Grizzly Bears", ref="bonds-other-attacker-or-blocker")
        attacker = self.add(engine, "Grizzly Bears", seat="B", ref="bonds-opposing-attacker")
        for card in (human, spare, attacker):
            card.acquired_control_turn_count = -1
        frog = self.add(engine, "Turn to Frog", seat="B", zone="hand")
        hidden = self.add(engine, "Counterspell", seat="D", zone="hand", ref="bonds-hidden")
        if active == "B":
            self.add(engine, "Vedalken Orrery")
        for index in range(2):
            self.add(engine, "Plains", ref=f"bonds-pay-a-{index}")
        self.add(engine, "Island", seat="B")
        self.add(engine, "Plains", seat="B")
        for card in (bonds, frog):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active=active)
        self.checkpoint(game)
        self.cast_for(game, "A", bonds, [human.ref])
        self.resolve_top(game)
        self.assertEqual((4, 3), self.stats(game, human))
        return game, bonds, human, spare, attacker, frog, hidden

    def stats(self, game, card):
        data = game.engine._effective_card_data(game.state.cards[card.object_id])
        return int(data["power"]), int(data["toughness"])

    def change_type(self, game, bonds, human, frog):
        self.cast_for(game, "B", frog, [human.ref])
        self.resolve_top(game)
        self.assertEqual((1, 1), self.stats(game, human))
        self.assertEqual(human.object_id, game.state.cards[bonds.object_id].attached_to)

    def attacker_decision(self, game):
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")

    def nonhuman_attack(self, game, human, spare):
        self.attacker_decision(game)
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": "attack", "attackers": {human.ref: "C"}})
        self.assertFalse(rejected.ok, "A non-Human cannot be newly declared as an attacker")
        self.assertEqual(before, authoritative_state_hash(game.state))
        alternative = game.act("pilot:A", {"action_id": "attack", "attackers": {spare.ref: "C"}})
        self.assertTrue(alternative.ok, alternative.summary)

    def test_actual_type_change_rejects_new_attack_but_keeps_the_already_declared_attacker(self):
        game, bonds, human, spare, _attacker, frog, _hidden = self.scene(50810133)
        self.change_type(game, bonds, human, frog)
        self.nonhuman_attack(game, human, spare)
        self.replay(game, load=True)

        game, bonds, human, _spare, _attacker, frog, hidden = self.scene(50810134)
        self.attacker_decision(game)
        accepted = game.act("pilot:A", {"action_id": "attack", "attackers": {human.ref: "C"}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.change_type(game, bonds, human, frog)
        self.assertEqual("C", game.state.combat.attackers[human.object_id])
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(hidden.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_nonhuman_block_is_rejected_without_consuming_the_offer(self):
        game, bonds, human, spare, attacker, frog, _hidden = self.scene(50910131, active="B")
        self.attacker_decision(game)
        attack = game.act("pilot:B", {"action_id": "attack", "attackers": {attacker.ref: "A"}})
        self.assertTrue(attack.ok, attack.summary)
        self.change_type(game, bonds, human, frog)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": "block", "blocks": {human.ref: attacker.ref}})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", {"action_id": "block", "blocks": {spare.ref: attacker.ref}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual([spare.object_id], game.state.combat.blockers[attacker.object_id])
        self.replay(game, load=True)

    def test_actual_frog_attack_witness_kills_omitting_the_negative_branch(self):
        game, bonds, human, spare, _attacker, frog, _hidden = self.scene(50810135)
        self.change_type(game, bonds, human, frog)
        original = FixedPublicStateConditionSpec.matches

        def omit_negative(condition, facts):
            if (condition.kind is FixedPublicStateConditionKind.ATTACHED_MATCHES_QUERY
                    and condition.predicate.excluded_subtypes == ("human",)):
                return False
            return original(condition, facts)

        with patch.object(FixedPublicStateConditionSpec, "matches", omit_negative):
            with self.assertRaisesRegex(AssertionError, "A non-Human cannot be newly declared"):
                self.nonhuman_attack(game, human, spare)

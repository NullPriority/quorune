"""An actual graveyard cast makes two independently retargetable stack copies."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.cast_origin_replacement import GRAVEYARD_CAST_COPY_REPLACEMENT_VERSION
from quorune.deck import DeckDefinition, DeckEntry
from quorune.flashback import FLASHBACK_CAST_OPTION_ID
from quorune.graveyard_cast_reanimation_copy import (
    GRAVEYARD_REANIMATION_COPY_CAPABILITY, GRAVEYARD_REANIMATION_COPY_VERSION,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


RECLAMATION = "Sevinne's Reclamation"


class GraveyardCastStackCopiesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "graveyard-cast-stack-copies.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/graveyard-cast-stack-copies-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Two real stack copies", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_the_same_announced_target_and_separate_flashback(self):
        admitted, semantics = register_card(self.db, self.registry, "Increasing Vengeance")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup("Increasing Vengeance", fuzzy=False)
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertTrue(any("casting.flashback.fixed_mana" in p.capability_dependencies for p in programs))
        spell, = (p for p in programs if p.event == "resolve")
        result, = spell.effects
        base, = result["otherwise_effects"]
        self.assertEqual(GRAVEYARD_CAST_COPY_REPLACEMENT_VERSION, result["schema_version"])
        self.assertEqual([base, base], result["effects"])
        self.assertEqual({"op": "copy_stack_item", "stack": "$target.0"}, base)
        self.assertEqual("you", spell.target_schema["controller_relation"])
        changed = deepcopy(result)
        changed["effects"][1]["stack"] = "$target.1"
        for invalid in (replace(spell, effects=[changed]),
                        replace(spell, target_schema={k: v for k, v in spell.target_schema.items() if k != "controller_relation"})):
            self.assertFalse(bind_semantic_program_runtime(
                invalid, capability_registry=self.registry, profile="commander_review",
            )["strict"])
        altered = replace(card, oracle_text=card.oracle_text.replace("twice instead", "three times instead"))
        self.assertNotEqual("exact", compile_oracle_card(
            altered, capability_registry=self.registry, capability_profile="commander_review",
        ).status)

    def life(self, game):
        return tuple(game.state.players[seat].life for seat in "ABCD")

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:" + seat, command)
        self.assertFalse(denied.ok, denied.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def use_flashback(self, game, spell, target):
        self.priority(game, "A")
        offer = self.action(game, "A", spell)
        self.assertIn(FLASHBACK_CAST_OPTION_ID, {value["id"] for value in offer["cost_options"]})
        accepted = game.act("pilot:A", {"action_id": offer["id"], "from": "graveyard",
                                        "cost_option": FLASHBACK_CAST_OPTION_ID, "pay": "auto", "targets": [target]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", game.state.stack[-1].context["cast_origin"])

    def choose_copy_target(self, game, original, default, chosen):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        packet = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual("copy_stack_item", packet["operation"])
        self.assertEqual(original, packet["target_stack"])
        self.assertEqual([default], list(packet["default_targets"]))
        command = {"action_id": "choose", "targets": [chosen]}
        self.reject(game, "B", command)
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)

    def test_actual_hand_cast_makes_one_copy_and_rejects_an_opposing_spell_target(self):
        game = self.session(70710071)
        vengeance = self.add(game.engine, "Increasing Vengeance", zone="hand")
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand", ref="vengeance-hand-original")
        other = self.add(game.engine, "Lightning Bolt", zone="hand", seat="B", ref="vengeance-wrong-controller")
        for card in (vengeance, bolt, other):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 3})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", bolt, ["C"], reserve={"R": 2})
        original = game.state.stack[-1].ref
        self.cast_for(game, "B", other, ["D"])
        opposing = game.state.stack[-1].ref
        self.priority(game, "A")
        offer = self.action(game, "A", vengeance)
        self.reject(game, "A", {"action_id": offer["id"], "targets": [opposing], "pay": "auto"})
        self.cast_for(game, "A", vengeance, [original])
        self.choose_copy_target(game, original, "C", "B")
        self.assertEqual(1, sum(item.kind == "spell_copy" for item in game.state.stack))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((40, 37, 37, 37), self.life(game))
        self.assertEqual("graveyard", game.state.cards[vengeance.object_id].zone)
        self.replay(game, load=True)

    def test_actual_flashback_offers_two_independent_assignments_before_priority_and_replays(self):
        game = self.session(70710072)
        vengeance = self.add(game.engine, "Increasing Vengeance", zone="graveyard")
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand")
        for card in (vengeance, bolt):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "R": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", bolt, ["B"], reserve={"C": 3, "R": 2})
        original = game.state.stack[-1].ref
        self.use_flashback(game, vengeance, original)
        self.choose_copy_target(game, original, "B", "C")
        self.assertEqual("semantic.choice", game.state.pending_decision.kind)
        self.assertEqual((40, 40, 40, 40), self.life(game))
        first, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual(["C"], list(first.targets))
        game = self.replay(game, load=True)
        self.choose_copy_target(game, original, "B", "D")
        self.assertEqual([["C"], ["D"]], [list(item.targets) for item in game.state.stack if item.kind == "spell_copy"])
        self.assertEqual("exile", game.state.cards[vengeance.object_id].zone)
        self.resolve_top(game)
        self.assertEqual((40, 40, 40, 37), self.life(game))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((40, 37, 37, 37), self.life(game))
        self.replay(game, load=True)

    def test_actual_flashback_creates_no_copy_after_the_announced_target_is_countered(self):
        game = self.session(70710073)
        vengeance = self.add(game.engine, "Increasing Vengeance", zone="graveyard")
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand")
        counter = self.add(game.engine, "Counterspell", zone="hand", seat="B")
        for card in (vengeance, bolt, counter):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "R": 3})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast_for(game, "A", bolt, ["B"], reserve={"C": 3, "R": 2})
        original = game.state.stack[-1].ref
        self.use_flashback(game, vengeance, original)
        self.cast_for(game, "B", counter, [original])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((40, 40, 40, 40), self.life(game))
        self.assertEqual("exile", game.state.cards[vengeance.object_id].zone)
        self.replay(game, load=True)

    def setup_reclamation(self, seed, *, zone="graveyard", returning="Wall of Omens"):
        game = self.session(seed)
        spell = self.add(game.engine, RECLAMATION, zone=zone)
        original_target = self.add(game.engine, returning, zone="graveyard", ref="first-reclamation-target")
        other_target = self.add(game.engine, "Plains", zone="graveyard", ref="second-reclamation-target")
        expensive = self.add(game.engine, "Hill Giant", zone="graveyard", ref="too-large-for-reclamation")
        hostile = self.add(game.engine, "Plains", seat="B", zone="graveyard", ref="foreign-reclamation-target")
        for card in (spell, original_target):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "W": 1})
        self.checkpoint(game)
        return game, spell, original_target, other_target, expensive, hostile

    def choose_reclamation_copy(self, game, choice):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual("offer_optional_effect", context["operation"])
        command = {"action_id": "choose", "choice": choice}
        self.reject(game, "B", command)
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)

    def test_reclamation_original_bind_preserves_flashback_and_rejects_forged_copy_scope(self):
        admission, semantics = register_card(self.db, self.registry, RECLAMATION)
        self.assertTrue(admission["strict_capability_ready"], admission["blockers"])
        card = self.db.lookup(RECLAMATION, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status)
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertTrue(any("casting.flashback.fixed_mana" in p.capability_dependencies for p in programs))
        program, = (p for p in programs if p.event == "resolve")
        self.assertIn(GRAVEYARD_REANIMATION_COPY_CAPABILITY, program.capability_dependencies)
        wrapper, = program.effects
        self.assertEqual(GRAVEYARD_REANIMATION_COPY_VERSION, wrapper["schema_version"])
        self.assertEqual(wrapper["otherwise_effects"][0], wrapper["effects"][0])
        self.assertEqual(3, program.target_schema["mana_value_max"])
        self.assertEqual("you", program.target_schema["owner_relation"])
        other_stack = deepcopy(wrapper)
        other_stack["effects"][1]["effects"][0]["stack"] = "$target.0"
        for invalid in (replace(program, effects=[other_stack]),
                        replace(program, target_schema={**program.target_schema, "owner_relation": "any"})):
            ruling = bind_semantic_program_runtime(
                invalid, capability_registry=self.registry, profile="commander_review",
            )
            self.assertFalse(ruling["strict"])

    def test_reclamation_flashback_retargets_before_returned_permanent_trigger_and_replays(self):
        # Commander 2019 release notes put copy targeting before the returned permanent's
        # ETB can resolve, and its trigger must resolve before the copy.
        game, spell, original, other, expensive, hostile = self.setup_reclamation(70710074)
        hand = len(game.state.players["A"].zones["hand"])
        self.use_flashback(game, spell, original.ref)
        source_stack = game.state.stack[-1].ref
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual("battlefield", game.state.cards[original.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
        self.assertEqual(hand, len(game.state.players["A"].zones["hand"]))
        game = self.replay(game, load=True)
        self.choose_reclamation_copy(game, "apply")
        packet = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual("copy_stack_item", packet["operation"])
        self.assertEqual(source_stack, packet["target_stack"])
        self.assertEqual([original.ref], list(packet["default_targets"]))
        self.assertEqual(hand, len(game.state.players["A"].zones["hand"]))
        for forbidden in (expensive.ref, hostile.ref):
            self.reject(game, "A", {"action_id": "choose", "targets": [forbidden]})
        game = self.replay(game, load=True)
        accepted = game.act("pilot:A", {"action_id": "choose", "targets": [other.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        copied, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual([other.ref], list(copied.targets))
        self.assertEqual("triggered_ability", game.state.stack[-1].kind)
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        self.resolve_top(game)
        self.assertEqual(hand + 1, len(game.state.players["A"].zones["hand"]))
        self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[other.object_id].zone)
        self.assertEqual(1, sum(event.code == "stack.copy" for event in game.state.events))
        self.replay(game, load=True)

    def test_reclamation_hand_and_decline_only_return_the_original_and_replay(self):
        for index, zone in enumerate(("hand", "graveyard")):
            with self.subTest(zone=zone):
                game, spell, original, other, _, _ = self.setup_reclamation(
                    70710075 + index, zone=zone, returning="Plains",
                )
                if zone == "hand":
                    self.cast_for(game, "A", spell, [original.ref])
                    self.assertIsNone(self.resolve(game))
                else:
                    self.use_flashback(game, spell, original.ref)
                    self.assertEqual("semantic.choice", self.resolve(game).kind)
                    self.choose_reclamation_copy(game, "decline")
                self.assertEqual("battlefield", game.state.cards[original.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
                self.assertEqual("exile" if zone == "graveyard" else "graveyard",
                                 game.state.cards[spell.object_id].zone)
                self.assertFalse(any(event.code == "stack.copy" for event in game.state.events))
                self.replay(game, load=True)

    def test_reclamation_copy_may_retain_the_now_illegal_target_and_fails_to_resolve(self):
        game, spell, original, other, _, _ = self.setup_reclamation(70710077, returning="Plains")
        self.use_flashback(game, spell, original.ref)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.choose_reclamation_copy(game, "apply")
        decision = game.act("pilot:A", {"action_id": "choose"})
        self.assertTrue(decision.ok, decision.summary)
        copied, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual([original.ref], list(copied.targets))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[original.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
        self.assertEqual(1, sum(event.code == "stack.copy" for event in game.state.events))
        self.replay(game, load=True)

    def test_reclamation_original_loses_sole_target_and_cannot_offer_copy(self):
        game, spell, original, other, _, _ = self.setup_reclamation(70710078, returning="Plains")
        crypt = self.add(game.engine, "Tormod's Crypt", seat="B")
        self.assert_closed(game, crypt)
        self.checkpoint(game)
        self.use_flashback(game, spell, original.ref)
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer = next(option for option in actions if option["id"].startswith(f"activate:{crypt.ref}:"))
        result = game.act("pilot:B", {"action_id": offer["id"], "targets": ["A"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[original.object_id].zone)
        self.assertEqual("exile", game.state.cards[other.object_id].zone)
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        self.assertFalse(any(event.code == "stack.copy" for event in game.state.events))
        self.replay(game, load=True)

    def test_reclamation_hand_copy_guard_mutation_is_detected(self):
        game, spell, original, _, _, _ = self.setup_reclamation(70710079, zone="hand", returning="Plains")
        self.cast_for(game, "A", spell, [original.ref])
        with patch("quorune.semantic_runtime.values.was_cast_from_origin", return_value=True):
            pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        self.assertEqual("offer_optional_effect", game.packet("pilot:A", full=True)["decision"]["ctx"]["operation"])

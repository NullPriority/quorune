"""One attack, a simultaneous fixed counter group, and its own target's evasion."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.attack_counter_unblockable import ATTACK_COUNTER_UNBLOCKABLE_CAPABILITY
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.temporary_declaration_restrictions import current_declaration_restrictions
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class AttackCounterUnblockableTests(InheritedNativeFixture, unittest.TestCase):
    scout = "Trial Tunneling Scout"
    body = "Trial Trail Companion"
    bounce = "Trial Trail Retreat"
    prevent = "Trial Counter Impasse"
    plains = "Generic Bound Plains"
    island = "Trial Survey Island"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "attack-counter.sqlite3"
        provider = ROOT / "tests/fixtures"
        build_fixture_database([
            provider / "bound-effect-program-cards.json",
            provider / "source-counter-reflexive-cards.json",
            provider / "source-optional-creature-unblockable-cards.json",
            provider / "attack-counter-unblockable-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Optional attack counter", [
            DeckEntry(commander, 1, "commander"), DeckEntry(cls.plains, 30),
        ], [commander])

    def scene(self, seed, *, counter_prevention=False):
        game = self.session(seed)
        source = self.add(game.engine, self.scout, ref="COUNTER-ATTACK-SOURCE")
        target = self.add(game.engine, self.body, ref="OTHER-ATTACKER")
        idle = self.add(game.engine, self.body, ref="IDLE-CREATURE")
        blocker = self.add(game.engine, self.body, seat="B", ref="DEFENDING-CREATURE")
        bounce = self.add(game.engine, self.bounce, seat="B", zone="hand", ref="PRIVATE-COMBAT-RESPONSE")
        self.add(game.engine, self.island, seat="B")
        for creature in (source, target):
            creature.acquired_control_turn_count = -1
        for card in (source, bounce):
            self.assert_closed(game, card)
        if counter_prevention:
            prevention = self.add(game.engine, self.prevent, seat="C", ref="COUNTER-PREVENTION")
            self.assert_closed(game, prevention)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={})
        self.checkpoint(game)
        self.attack(game, [(source, "B"), (target, "B")])
        pending = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        return game, source, target, idle, blocker, bounce

    def choose(self, game, refs, *, actor="A"):
        return game.act("pilot:"+actor, {"action_id": "choose", "targets": list(refs)})

    def counts(self, game, *cards):
        return tuple(game.state.cards[card.object_id].counters.get("+1/+1", 0) for card in cards)

    def restriction_refs(self, game):
        return {component.source.ref for component in current_declaration_restrictions(game.engine)
                if component.template.template_id == "intrinsic-unblockable-v1"}

    def test_other_attacker_gets_evasion_and_both_receive_counters(self):
        game, source, target, idle, blocker, bounce = self.scene(508115001)
        before = authoritative_state_hash(game.state)
        self.assertNotIn(bounce.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.assertFalse(self.choose(game, (target.ref,), actor="C").ok)
        for ref in (source.ref, idle.ref, blocker.ref):
            self.assertFalse(self.choose(game, (ref,)).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        choice = self.choose(game, (target.ref,))
        self.assertTrue(choice.ok, choice.summary)
        self.assertEqual((0, 0), self.counts(game, source, target))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((1, 1), self.counts(game, source, target))
        self.assertEqual({target.ref}, self.restriction_refs(game))
        self.until(game, lambda session: session.state.pending_decision.kind == "combat.blockers")
        before = authoritative_state_hash(game.state)
        invalid = game.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: target.ref}})
        self.assertFalse(invalid.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        valid = game.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: source.ref}})
        self.assertTrue(valid.ok, valid.summary)
        self.replay(game, load=True)

    def test_zero_targets_keeps_only_the_source_counter(self):
        game, source, target, _, _, _ = self.scene(508115002)
        choice = self.choose(game, ())
        self.assertTrue(choice.ok, choice.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((1, 0), self.counts(game, source, target))
        self.assertEqual(set(), self.restriction_refs(game))
        self.replay(game, load=True)

    def test_only_the_chosen_target_can_stop_the_original_trigger(self):
        for remove_target in (True, False):
            with self.subTest(remove_target=remove_target):
                game, source, target, _, _, bounce = self.scene(508115003 + remove_target)
                choice = self.choose(game, (target.ref,))
                self.assertTrue(choice.ok, choice.summary)
                removed = target if remove_target else source
                self.cast(game, "B", bounce, [removed.ref])
                self.resolve_top(game)
                self.assertEqual("hand", game.state.cards[removed.object_id].zone)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual((0, 0 if remove_target else 1), self.counts(game, source, target))
                self.assertEqual(set() if remove_target else {target.ref}, self.restriction_refs(game))
                self.replay(game, load=True)

    def test_preventing_counters_still_grants_evasion_to_the_selected_attacker(self):
        game, source, target, _, _, _ = self.scene(508115005, counter_prevention=True)
        choice = self.choose(game, (target.ref,))
        self.assertTrue(choice.ok, choice.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((0, 0), self.counts(game, source, target))
        self.assertEqual({target.ref}, self.restriction_refs(game))
        self.replay(game, load=True)

    def test_printed_attack_requires_the_complete_paired_owners(self):
        record = self.db.lookup(self.scout)

        def compile_card(card, registry):
            return compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")

        ir = compile_card(record, self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = (n for n in ir.faces[0].nodes if n.event == "creature.attacks")
        self.assertEqual(["$source.zone_object", "$target.0"], list(node.effects[0]["cards"]))
        self.assertEqual("$targets", node.effects[1]["cards"])
        self.assertIn(ATTACK_COUNTER_UNBLOCKABLE_CAPABILITY, node.capability_dependencies)
        for text in (record.oracle_text.replace("That creature can't", "Those creatures can't"),
                     record.oracle_text.replace("target attacking", "target blocking"),
                     record.oracle_text + " Draw a card."):
            self.assertNotEqual("exact", compile_card(replace(record, oracle_text=text), self.registry).status)
        data = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        capability = next(row for row in data["capabilities"] if row["id"] == ATTACK_COUNTER_UNBLOCKABLE_CAPABILITY)
        capability.update(status="blocked", blockers=["Paired attack result authority withdrawn."])
        registry = CapabilityRegistry(data)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=data, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_card(record, registry).status)
        with patch("quorune.compiler.attack_counter_unblockable.attack_counter_unblockable_result", return_value=None):
            self.assertNotEqual("exact", compile_card(record, self.registry).status)

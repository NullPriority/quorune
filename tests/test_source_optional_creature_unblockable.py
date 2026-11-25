"""Clue sacrifice and the original source's independent optional target."""

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
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.source_optional_unblockable import SOURCE_OPTIONAL_UNBLOCKABLE_CAPABILITY
from quorune.rules.temporary_declaration_restrictions import current_declaration_restrictions
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class SourceOptionalCreatureUnblockableTests(InheritedNativeFixture, unittest.TestCase):
    investigator = "Trial Clue Trailblazer"
    creature = "Trial Trail Companion"
    bounce = "Trial Trail Retreat"
    basic = "Generic Bound Plains"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "optional-creature.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "source-optional-creature-unblockable-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Optional source declaration", [
            DeckEntry(commander, 1, "commander"), DeckEntry(cls.basic, 30),
        ], [commander])

    def scene(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, self.investigator, zone="hand", ref="CLUE-DECLARATION-SOURCE")
        selected = self.add(game.engine, self.creature, ref="POSSIBLE-RECIPIENT")
        unaffected = self.add(game.engine, self.creature, ref="UNAFFECTED-ATTACKER")
        blocker = self.add(game.engine, self.creature, seat="B", ref="ORIGINAL-BLOCKER")
        bounce = self.add(game.engine, self.bounce, seat="B", zone="hand", ref="BOUNCE-RESPONSE")
        selected.acquired_control_turn_count = unaffected.acquired_control_turn_count = -1
        for card in (source, bounce):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        game.state.players["B"].mana_pool["U"] = 1
        self.prepare(game, mana={"C": 4, "U": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        clue, = [card for card in game.state.cards.values()
                 if card.zone == "battlefield" and card.controller == "A"
                 and card.is_token and "clue" in game.engine._effective_type_parts(card)[1]]
        self.priority(game, "A")
        action = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                      if row["id"].startswith(f"activate:{clue.ref}:"))
        paid = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertNotEqual("battlefield", game.state.cards[clue.object_id].zone)
        pending = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        return game, source, selected, unaffected, blocker, bounce

    def choose_targets(self, game, refs):
        accepted = game.act("pilot:A", {"action_id": "choose", "targets": list(refs)})
        self.assertTrue(accepted.ok, accepted.summary)

    def unaffected_by_blocking(self, game):
        return {r.source.ref for r in current_declaration_restrictions(game.engine)
                if r.template.template_id == "intrinsic-unblockable-v1"}

    def test_clue_with_zero_targets_keeps_its_untargeted_source_and_replays(self):
        game, source, _, _, _, _ = self.scene(5092301)
        before = authoritative_state_hash(game.state)
        for actor, refs in (("pilot:A", [source.ref]), ("pilot:B", [])):
            rejected = game.act(actor, {"action_id": "choose", "targets": refs})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.choose_targets(game, ())
        self.assertIsNone(self.resolve(game))
        self.assertEqual({source.ref}, self.unaffected_by_blocking(game))
        self.replay(game, load=True)

    def test_one_other_target_and_source_are_unblockable_in_native_combat(self):
        game, source, selected, unaffected, blocker, _ = self.scene(5092302)
        self.choose_targets(game, (selected.ref,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual({source.ref, selected.ref}, self.unaffected_by_blocking(game))
        game = self.replay(game, load=True)
        self.attack(game, [(selected, "B"), (unaffected, "B")])
        self.until(game, lambda session: session.state.pending_decision.kind == "combat.blockers")
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: selected.ref}})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: unaffected.ref}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.replay(game, load=True)

    def test_sole_illegal_target_stops_the_entire_result(self):
        game, source, selected, _, _, bounce = self.scene(5092303)
        self.choose_targets(game, (selected.ref,))
        self.cast(game, "B", bounce, [selected.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[selected.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertNotIn(source.ref, self.unaffected_by_blocking(game))
        self.replay(game, load=True)

    def test_live_other_controller_target_survives_source_reincarnation(self):
        game, source, _, _, foreign, bounce = self.scene(5092304)
        original = game.state.cards[source.object_id].logical_object_id
        self.choose_targets(game, (foreign.ref,))
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        ZoneTransitionOwner(game.engine).move_card(source.object_id, "battlefield")
        self.assertNotEqual(original, game.state.cards[source.object_id].logical_object_id)
        self.checkpoint(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({foreign.ref}, self.unaffected_by_blocking(game))
        self.replay(game, load=True)

    def test_source_optional_declaration_needs_its_exact_trigger_and_verified_authority(self):
        record = self.db.lookup(self.investigator)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        trigger, = (n for n in ir.faces[0].nodes if n.event == "permanent.sacrificed")
        self.assertEqual(1, trigger.target_schema["up_to"])
        self.assertIn(SOURCE_OPTIONAL_UNBLOCKABLE_CAPABILITY, trigger.capability_dependencies)
        value = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        cap = next(r for r in value["capabilities"] if r["id"] == SOURCE_OPTIONAL_UNBLOCKABLE_CAPABILITY)
        cap.update(status="blocked", blockers=["Source and optional creature declaration authority withdrawn."])
        registry = CapabilityRegistry(value)
        decl = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=value, declaration_source=decl, discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=registry,
                                                       capability_profile="commander_review").status)
        with patch("quorune.compiler.source_optional_creature_unblockable.source_optional_creature_unblockable", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=self.registry,
                                                           capability_profile="commander_review").status)

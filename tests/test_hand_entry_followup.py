from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
import test_fixed_reveal_additional_costs as reveal
from test_granted_skulk import record
import test_reanimation_followup as linked
import test_self_public_zone_casting as actions
import test_splice as splice

from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.hand_entry_followup import (
    HAND_ENTRY_FOLLOWUP_CAPABILITY, decode_hand_entry_followup, hand_entry_followup_runtime_dependencies,
)
from quorune.turn_step_owner import TURN_STEPS
from scripts.build_test_database import build_fixture_database


ENTRY = "You may put a creature card from your hand onto the battlefield."
LINKED = ENTRY + " That creature gains haste. Sacrifice that creature at the beginning of the next end step."


class HandEntryFollowupCompilerTests(unittest.TestCase):
    def test_linked_hand_entry_closes_only_the_complete_receiver_and_rider(self):
        registry = load_default_capability_registry()
        text = LINKED.replace("a creature card", "an Elemental creature card")
        ir = compile_oracle_card(record(text, "Instant"), capability_registry=registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        effect, = node.effects
        self.assertEqual(text, node.text)
        self.assertIsNone(node.target_schema)
        self.assertEqual(["elemental"], effect["query"]["subtypes_all"])
        self.assertEqual("sacrifice", decode_hand_entry_followup(effect).departure)
        self.assertIn(HAND_ENTRY_FOLLOWUP_CAPABILITY, node.capability_dependencies)
        self.assertIn(HAND_ENTRY_FOLLOWUP_CAPABILITY, hand_entry_followup_runtime_dependencies(
            SimpleNamespace(effects=({"nested": [effect]},), target_schema=None)))
        for text in (LINKED.replace("That creature gains", "That artifact gains"),
            LINKED.replace("Sacrifice that creature", "Its controller sacrifices it"),
            LINKED.replace("the next end step", "your next end step"),
            LINKED + " If you do, return this card to the battlefield."):
            with self.subTest(text=text):
                other = compile_oracle_card(record(text, "Instant"), capability_registry=registry)
                self.assertNotEqual("exact", other.status)
        for altered in ({**effect, "schema_version": True}, {**effect, "followup": {**effect["followup"], "power_boost": 2}},
            {key: value for key, value in effect.items() if key != "followup"}):
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                hand_entry_followup_runtime_dependencies(SimpleNamespace(effects=(altered,), target_schema=None))

    def test_withdrawing_delayed_authority_preserves_ordinary_private_entry(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == "trigger.delayed.fixed_object_action")
        row.update(status="blocked", blockers=["withdrawn for the dependency witness"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(record(LINKED, "Instant"), capability_registry=registry).status)
        self.assertEqual("exact", compile_oracle_card(record(ENTRY, "Instant"), capability_registry=registry).status)

    def test_omitting_the_hand_entry_followup_leaves_material_residuals(self):
        registry = load_default_capability_registry()
        card = record(LINKED, "Instant")
        self.assertEqual("exact", compile_oracle_card(card, capability_registry=registry).status)
        with patch("quorune.compiler.resolution_effect_templates.fixed_private_hand_entry_followup_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry).status)


class HandEntryFollowupNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = reveal.FixedRevealCostRuntimeTests.session
    add = reveal.FixedRevealCostRuntimeTests.add
    checkpoint = reveal.FixedRevealCostRuntimeTests.checkpoint
    resolve = reveal.FixedRevealCostRuntimeTests.resolve
    replay = reveal.FixedRevealCostRuntimeTests.replay
    prepare = actions.SelfPublicZoneRuntimeTests.prepare
    cast = actions.SelfPublicZoneRuntimeTests.cast
    assert_closed = actions.SelfPublicZoneRuntimeTests.assert_closed
    native_until = linked.ReanimationFollowupNativeTests.native_until
    until = linked.ReanimationFollowupNativeTests.until
    has_haste = staticmethod(linked.ReanimationFollowupNativeTests.has_haste)
    start_splice = splice.SpliceRuntimeTests.start_splice
    splice_actions = splice.SpliceRuntimeTests.splice_actions
    add_splice = splice.SpliceRuntimeTests.add_splice

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "hand-entry-followup.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/hand-entry-followup-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Linked hand entry witness", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def activate_entry(self, session, source):
        actions_now = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions_now if row["id"].startswith(f"activate:{source.ref}:"))
        accepted = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("semantic.choice", self.resolve(session).kind)

    def offered_refs(self, session):
        context = session.packet("pilot:A", full=True)["decision"]["ctx"]
        return {row["id"] for row in context["objects"]}

    def choose(self, session, card):
        accepted = session.act("pilot:A", {"action_id": "choose", **({"card": card.ref} if card else {})})
        self.assertTrue(accepted.ok, accepted.summary)

    def test_sneak_attack_keeps_declined_cards_private_and_resumes_the_selected_entry(self):
        session = self.session(400701)
        engine = session.engine
        source = self.add(engine, "Sneak Attack")
        clone = self.add(engine, "Clone", zone="hand", ref="private-selected-clone")
        copy_source = self.add(engine, "Generic Bound Body", seat="B", ref="public-clone-subject")
        self.assert_closed(session, source)
        self.prepare(session, mana={"R": 2})
        self.checkpoint(session)
        self.activate_entry(session, source)
        self.assertEqual({clone.ref}, self.offered_refs(session))
        self.assertNotIn(clone.ref, json.dumps(session.packet("pilot:D", full=True)))
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:D", {"action_id": "choose", "card": clone.ref}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.choose(session, None)
        self.assertEqual("hand", session.state.cards[clone.object_id].zone)
        self.assertFalse(session.state.delayed_triggers)

        self.activate_entry(session, source)
        session = self.replay(session, load=True)
        self.choose(session, clone)
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        self.assertEqual("hand", session.state.cards[clone.object_id].zone)
        self.assertFalse(session.state.delayed_triggers)
        session = self.replay(session, load=True)
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        copy_choice = next(option["id"] for option in options if copy_source.ref in option["id"])
        accepted = session.act("pilot:A", {"action_id": "choose", "replacement": copy_choice})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("Generic Bound Body", session.engine.display_name(clone.object_id))
        self.assertTrue(self.has_haste(session, clone))
        trigger, = session.state.delayed_triggers
        self.assertEqual(source.object_id, trigger.source_object_id)
        self.until(session, lambda game: game.state.step == "end_step" and bool(game.state.stack))
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[clone.object_id].zone)
        self.replay(session, load=True)

    def test_soulstoke_requires_an_elemental_creature_and_temporary_haste_expires_first(self):
        session = self.session(400702)
        engine = session.engine
        source = self.add(engine, "Incandescent Soulstoke")
        creature = self.add(engine, "Changeling Outcast", zone="hand")
        kindred = self.add(engine, "Nameless Inversion", zone="hand")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 1, "R": 1})
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.phase, engine.state.step = "ending", "end_step"
        engine.state.phase_index = TURN_STEPS.index(("ending", "end_step"))
        engine._grant_priority("A")
        engine.pump()
        self.checkpoint(session)
        self.activate_entry(session, source)
        self.assertEqual({creature.ref}, self.offered_refs(session))
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {"action_id": "choose", "card": kindred.ref}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.choose(session, creature)
        self.assertTrue(self.has_haste(session, creature))
        self.until(session, lambda game: game.state.active_player == "B" and game.state.step == "upkeep")
        self.assertEqual("battlefield", session.state.cards[creature.object_id].zone)
        self.assertFalse(self.has_haste(session, creature))
        self.until(session, lambda game: game.state.step == "end_step" and bool(game.state.stack))
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[creature.object_id].zone)
        self.replay(session, load=True)

    def test_arms_race_admits_an_artifact_without_making_it_a_creature(self):
        session = self.session(400703)
        source = self.add(session.engine, "Arms Race")
        vehicle = self.add(session.engine, "Sky Skiff", zone="hand")
        self.add(session.engine, "Generic Bound Body", zone="hand")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 3, "R": 1})
        self.checkpoint(session)
        self.activate_entry(session, source)
        self.assertEqual({vehicle.ref}, self.offered_refs(session))
        self.choose(session, vehicle)
        self.assertTrue(self.has_haste(session, vehicle))
        self.assertNotIn("Creature", session.engine._effective_card_data(vehicle)["type_line"])
        self.until(session, lambda game: game.state.step == "end_step" and bool(game.state.stack))
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[vehicle.object_id].zone)
        self.replay(session, load=True)

    def test_spliced_through_the_breach_keeps_the_private_choice_and_host_source(self):
        session = self.session(400704)
        engine = session.engine
        host_spell = self.add(engine, "Reach Through Mists", zone="hand")
        donor = self.add(engine, "Through the Breach", zone="hand")
        creature = self.add(engine, "Generic Bound Body", zone="hand", ref="private-spliced-entry")
        self.assert_closed(session, donor)
        self.prepare(session, mana={"U": 1, "C": 2, "R": 2})
        self.checkpoint(session)
        self.start_splice(session, host_spell)
        self.add_splice(session, donor)
        accepted = session.act("pilot:A", {"action_id": "cast", "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual({creature.ref}, self.offered_refs(session))
        self.assertNotIn(creature.ref, json.dumps(session.packet("pilot:C", full=True)))
        self.choose(session, creature)
        self.assertTrue(self.has_haste(session, creature))
        trigger, = session.state.delayed_triggers
        self.assertEqual(host_spell.object_id, trigger.source_object_id)
        self.assertEqual("hand", session.state.cards[donor.object_id].zone)
        self.replay(session, load=True)

    def test_quinjet_announces_its_mode_before_the_private_hero_choice(self):
        session = self.session(400705)
        source = self.add(session.engine, "Avengers Quinjet", zone="hand")
        hero = self.add(session.engine, "Changeling Outcast", zone="hand", ref="private-hero")
        graveyard_hero = self.add(session.engine, "Changeling Outcast", zone="graveyard", ref="graveyard-hero")
        self.add(session.engine, "Goblin Piker", zone="hand")
        self.assert_closed(session, source)
        self.prepare(session, mana={"C": 5})
        self.checkpoint(session)
        self.cast(session, "A", source)
        self.until(session, lambda game: game.state.pending_decision.kind == "semantic.target")
        self.assertNotIn(hero.ref, json.dumps(session.packet("pilot:D", full=True)))
        accepted = session.act("pilot:A", {"action_id": "choose", "modes": ["mode_1"], "targets": []})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual({hero.ref}, self.offered_refs(session))
        self.assertNotIn(hero.ref, json.dumps(session.packet("pilot:D", full=True)))
        self.choose(session, hero)
        self.assertEqual("battlefield", session.state.cards[hero.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[graveyard_hero.object_id].zone)
        self.assertFalse(self.has_haste(session, hero))
        self.assertFalse(session.state.delayed_triggers)
        self.replay(session, load=True)

    def test_warren_offers_a_fresh_private_goblin_choice_after_each_hit(self):
        session = self.session(400706)
        source = self.add(session.engine, "Warren Instigator")
        source.acquired_control_turn_count = -1
        goblin = self.add(session.engine, "Goblin Piker", zone="hand", ref="private-first-hit-goblin")
        changeling = self.add(session.engine, "Changeling Outcast", zone="hand", ref="private-second-hit-goblin")
        self.add(session.engine, "Generic Bound Body", zone="hand")
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        self.until(session, lambda game: game.state.pending_decision.kind == "combat.attackers")
        accepted = session.act("pilot:A", {"action_id": "attack", "attackers": {source.ref: "B"}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.until(session, lambda game: game.state.pending_decision.kind == "semantic.choice")
        self.assertEqual(39, session.state.players["B"].life)
        self.assertEqual({goblin.ref, changeling.ref}, self.offered_refs(session))
        self.choose(session, goblin)
        self.until(session, lambda game: game.state.pending_decision.kind == "semantic.choice")
        self.assertEqual(38, session.state.players["B"].life)
        self.assertEqual({changeling.ref}, self.offered_refs(session))
        self.assertNotIn(changeling.ref, json.dumps(session.packet("pilot:D", full=True)))
        self.choose(session, changeling)
        for card in (goblin, changeling):
            self.assertEqual("battlefield", session.state.cards[card.object_id].zone)
            self.assertNotIn(card.object_id, session.state.combat.attackers)
            self.assertFalse(self.has_haste(session, card))
        self.assertFalse(session.state.delayed_triggers)
        self.replay(session, load=True)

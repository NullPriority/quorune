"""Native evidence for a combat damage dealer's exact entry this turn."""

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
from quorune.compiler import public_trigger_conditions
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.combat_damage_entry_history import (
    COMBAT_DAMAGE_ENTRY_CONTEXT, COMBAT_DAMAGE_ENTRY_COVERAGE, is_combat_damage_entry_condition,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
SAMUT = "Samut, Vizier of Naktamun"
GOBLIN = "Raging Goblin"
VIPER = "Ambush Viper"
HISTORY = "condition.history.fixed_public_query"
INTERVENING = "trigger.condition.fixed_public_state"
DRAW = "zone.draw.library_to_hand"


class CombatDamageEntryHistoryTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-source-counter-cards.json": {"Sol Ring", "Shock"},
            "activated-self-transform-cards.json": {"Act of Treason"},
            "additional-continuous-carriers-cards.json": {"Walking Corpse"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "combat-damage-entry-history-cards.json": {SAMUT, GOBLIN, VIPER, "Mountain"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        subsets = []
        for name, cards in support.items():
            doc = json.loads((FIXTURES / name).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in cards]
            assert cards == {row["name"] for row in doc["cards"]}, name
            ids = {row["oracle_id"] for row in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(doc), encoding="utf-8")
            subsets.append(part)
        path = folder / "combat-damage-entry-history.sqlite3"
        build_fixture_database(subsets, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Combat dealer entry", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self):
        card = self.db.lookup(SAMUT, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, SAMUT)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        trigger, = [value for value in semantics.programs_for_oracle(card.oracle_id) if value.event == "damage.dealt"]
        return card, trigger

    def test_complete_samut_keeps_keywords_and_one_untargeted_private_damage_entry_draw(self):
        card, trigger = self._original()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual("battlefield", trigger.active_zone)
        self.assertIsNone(trigger.target_schema)
        self.assertTrue(is_combat_damage_entry_condition(trigger.event_condition))
        self.assertEqual([{"op": "draw", "player": "$controller", "count": 1, "private": True}], thaw_value(trigger.effects))
        self.assertLessEqual({COMBAT_DAMAGE_ENTRY_COVERAGE, "intervening_condition"}, set(trigger.coverage))
        self.assertLessEqual({HISTORY, INTERVENING, DRAW}, set(trigger.capability_dependencies))
        keyword, = [node for face in ir.faces for node in face.nodes if node.kind == "keyword_ability"]
        self.assertLessEqual({"first strike", "vigilance", "haste"}, set(keyword.mechanics))

    def test_combat_entry_trigger_rejects_other_dealt_domains_and_extra_results(self):
        card, trigger = self._original()
        shape = thaw_value(trigger.event_condition)
        shape["all"][2]["value"] = 1
        self.assertFalse(is_combat_damage_entry_condition(shape))
        child, = thaw_value(trigger.effects)
        for proposal in (replace(trigger, event_condition=shape), replace(trigger, effects=({**child, "count": 2},))):
            report = bind_semantic_program_runtime(proposal, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(report["strict"], report)
        for wording in (
            card.oracle_text.replace("deals combat damage to a player", "deals damage to a player"),
            card.oracle_text.replace("that creature entered this turn", "this creature entered this turn"),
            card.oracle_text.replace("draw a card", "draw two cards"),
        ):
            ir = compile_oracle_card(replace(card, oracle_text=wording), capability_registry=self.registry,
                                     capability_profile=PROFILE)
            self.assertNotEqual("exact", ir.status, wording)

    def _hand(self, game):
        return set(game.state.players["A"].zones["hand"])

    def _assert_private_draw(self, game, before):
        added, = self._hand(game) - before
        ref = game.state.cards[added].ref
        self.assertIn(ref, json.dumps(game.packet("pilot:A", full=True)))
        for seat in "BCD":
            self.assertNotIn(ref, json.dumps(game.packet(f"pilot:{seat}", full=True)))

    def _entered(self, game, card):
        identity = game.state.cards[card.object_id].logical_object_id
        return [event for event in game.state.turn_history.events
                if event.kind == "permanent_entered" and event.object_incarnation == identity]

    def _combat_trigger(self, game, samut, attackers):
        self.attack(game, [(card, "B") for card in attackers])
        for _ in range(28):
            watched = [item for item in game.state.stack if item.kind == "triggered_ability"
                       and item.source_object_id == samut.object_id]
            if watched:
                item, = watched
                return item
            decision = game.state.pending_decision
            if decision.kind == "priority":
                command = {"action_id": "pass"}
            elif decision.kind == "combat.blockers":
                command = {"action_id": "block", "blocks": {}}
            else:
                self.fail(f"Expected a combat decision, got {decision.kind}")
            result = game.act(game.pending_principals()[0], command)
            self.assertTrue(result.ok, result.summary)
        self.fail("Expected the actual qualifying damage trigger")

    def test_actual_samut_distinguishes_the_entering_dealer_from_itself_and_the_old_attacker(self):
        game = self.session(77157010)
        samut = self.add(game.engine, SAMUT, zone="hand")
        fresh = self.add(game.engine, GOBLIN, zone="hand")
        old = self.add(game.engine, "Walking Corpse")
        old.acquired_control_turn_count = -1
        self.prepare(game, mana={"C": 1, "R": 2, "G": 1})
        self.checkpoint(game)
        self.priority(game, "A")
        offer = self.action(game, "A", samut)
        outcome = game.act("pilot:A", {"action_id": offer["id"], "pay": "manual", "payment": {"C": 1, "R": 1, "G": 1}})
        self.assertTrue(outcome.ok, outcome.summary)
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", fresh)
        self.assertIsNone(self.resolve(game))

        current_samut = game.state.cards[samut.object_id]
        self.assertLessEqual({"first strike", "vigilance", "haste"}, set(game.engine._combat_keywords(current_samut)))
        self.assertEqual((1, 1, 0), tuple(len(self._entered(game, card)) for card in (samut, fresh, old)))
        before = self._hand(game)
        trigger = self._combat_trigger(game, samut, [fresh, old])
        self.assertEqual(game.state.cards[fresh.object_id].logical_object_id,
                         trigger.context[COMBAT_DAMAGE_ENTRY_CONTEXT]["logical_object_id"])
        self.assertNotEqual(trigger.context["source_logical_object_id"],
                            trigger.context[COMBAT_DAMAGE_ENTRY_CONTEXT]["logical_object_id"])
        self.assertEqual(37, game.state.players["B"].life)
        self.assertIsNone(self.resolve(game))
        self._assert_private_draw(game, before)
        self.replay(game, load=True)

    def test_actual_samut_rechecks_the_damage_dealer_after_its_departure(self):
        game = self.session(77157020)
        samut = self.add(game.engine, SAMUT)
        fresh = self.add(game.engine, GOBLIN, zone="hand")
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        self.add(game.engine, "Mountain", seat="B")
        self.prepare(game, mana={"R": 1})
        self.checkpoint(game)
        self.cast(game, "A", fresh)
        self.assertIsNone(self.resolve(game))
        before = self._hand(game)
        trigger = self._combat_trigger(game, samut, [fresh])
        dealer = game.state.cards[fresh.object_id]
        identity = dealer.logical_object_id
        self.cast(game, "B", shock, [dealer.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[fresh.object_id].zone)
        self.assertNotEqual(identity, game.state.cards[fresh.object_id].logical_object_id)
        self.assertEqual(identity, trigger.context[COMBAT_DAMAGE_ENTRY_CONTEXT]["logical_object_id"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self._assert_private_draw(game, before)
        self.replay(game, load=True)

    def test_actual_samut_reads_the_same_dealers_entry_before_a_control_change(self):
        game = self.session(77157030)
        samut = self.add(game.engine, SAMUT)
        creature = self.add(game.engine, VIPER, seat="B", zone="hand")
        theft = self.add(game.engine, "Act of Treason", zone="hand")
        self.prepare(game, mana={"C": 2, "R": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "G": 1})
        self.checkpoint(game)
        self.cast(game, "B", creature)
        self.assertIsNone(self.resolve(game))
        _entry, = self._entered(game, creature)
        self.assertEqual("B", _entry.actor)
        self.cast(game, "A", theft, [creature.ref])
        self.assertIsNone(self.resolve(game))
        controlled = game.state.cards[creature.object_id]
        self.assertEqual(("A", _entry.object_incarnation), (controlled.controller, controlled.logical_object_id))
        before = self._hand(game)
        self._combat_trigger(game, samut, [controlled])
        self.assertEqual(38, game.state.players["B"].life)
        self.assertIsNone(self.resolve(game))
        self._assert_private_draw(game, before)
        self.replay(game, load=True)

    def test_omitting_the_damage_dealer_condition_restores_the_original_residual(self):
        with patch.object(public_trigger_conditions, "_combat_damage_entry_binding", return_value=None):
            report, _ = register_card(self.db, self.registry, SAMUT)
            self.assertFalse(report["strict_capability_ready"], report)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_entry_condition_history_and_draw_withdrawals_deny_the_whole_samut(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap in (HISTORY, INTERVENING, DRAW):
            with self.subTest(capability=cap):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Scoped combat entry condition authority withdrawal"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                report, _ = register_card(self.db, registry, SAMUT)
                self.assertFalse(report["strict_capability_ready"], report)
                control, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()

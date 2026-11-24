"""Native evidence for optional cycled-source damage from the current Goblin count."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.characteristic_fragments import CharacteristicQuantityScope
from quorune.compiler import source_self_effect_templates
from quorune.rules.stack_damage_sources import SOURCE_DAMAGE_CONTEXT
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.query_effect_amount_model import PublicQueryAmountSpec
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
INCINERATOR = "Gempalm Incinerator"
GOBLIN = "Raging Goblin"
SPIDER = "Giant Spider"
KINDRED = "Boggart Shenanigans"
QUERY = "quantity_expression.public_query_effect_amount"
OPTIONAL = "effect.choice.optional_fixed"
SOURCE = "damage.source.stack_current_or_lki"
CYCLING = "activation.cycling.hand"


class CycledGoblinDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-source-counter-cards.json": {"Sol Ring"},
            "activated-self-transform-cards.json": {"Cremate"},
            "attacking-player-quantity-cards.json": {SPIDER},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "creature-subtype-addition-cards.json": {GOBLIN, KINDRED, "Unsummon"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "source-self-optional-cards.json": {INCINERATOR},
        }
        pieces = []
        for name, names in support.items():
            doc = json.loads((FIXTURES / name).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert names == {row["name"] for row in doc["cards"]}, name
            ids = {row["oracle_id"] for row in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            path = folder / name
            path.write_text(json.dumps(doc), encoding="utf-8")
            pieces.append(path)
        path = folder / "cycled-goblin-damage.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Cycled Goblin damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self):
        card = self.db.lookup(INCINERATOR, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, INCINERATOR)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = tuple(semantics.programs_for_oracle(card.oracle_id))
        trigger, = [program for program in programs if program.event == "card.cycled.self"]
        return card, trigger, programs

    def test_complete_original_keeps_the_same_creature_target_optional_source_and_cycling(self):
        card, trigger, programs = self._original()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual("hand", trigger.active_zone)
        self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"],
                          "count": 1, "types_any": ["creature"]}, thaw_value(trigger.target_schema))
        optional, = trigger.effects
        self.assertEqual("offer_optional_effect", optional["op"])
        self.assertEqual("$controller", optional["player"])
        child, = optional["effects"]
        self.assertEqual(("damage", "$source", "$target.0"), (child["op"], child["source"], child["target"]))
        amount = PublicQueryAmountSpec.from_dict(child["amount"])
        self.assertEqual(CharacteristicQuantityScope.ALL_ZONES, amount.quantity.scope)
        self.assertEqual(("battlefield",), amount.quantity.query.zones)
        self.assertEqual(("goblin",), amount.quantity.query.subtypes_all)
        self.assertEqual((), amount.quantity.query.types_all)
        self.assertIsNone(amount.quantity.query.controller)
        self.assertLessEqual({QUERY, OPTIONAL, SOURCE, "target.revalidate_resolution"},
                             set(trigger.capability_dependencies))
        cycle, = [program for program in programs if CYCLING in program.capability_dependencies]
        self.assertEqual([{"op": "draw", "player": "$controller", "count": 1, "private": True}],
                         thaw_value(cycle.effects))

    def test_exact_cycled_amount_scope_rejects_other_formulas_targets_and_siblings(self):
        card, _, _ = self._original()
        changes = (
            ("Goblins on the battlefield", "Goblins you control"),
            ("Goblins on the battlefield", "Goblins that attacked this game"),
            ("Goblins on the battlefield", "Elves on the battlefield"),
            ("target creature", "any target"),
            ("on the battlefield.", "on the battlefield. Draw a card."),
        )
        for old, new in changes:
            text = card.oracle_text.replace(old, new)
            self.assertNotEqual(card.oracle_text, text)
            ir = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry,
                                     capability_profile=PROFILE)
            self.assertTrue(ir.material_residuals, text)

    def _hand(self, game):
        return set(game.state.players["A"].zones["hand"])

    def _assert_private_draw(self, game, before):
        added, = self._hand(game) - before
        ref = game.state.cards[added].ref
        self.assertIn(ref, json.dumps(game.packet("pilot:A", full=True)))
        for seat in "BCD":
            self.assertNotIn(ref, json.dumps(game.packet(f"pilot:{seat}", full=True)))

    def _cycle(self, game, source):
        self.priority(game, "A")
        offered, = [row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    if row["id"].startswith(f"activate:{source.ref}:")]
        before = len(game.state.players["A"].draw_history)
        result = game.act("pilot:A", {"action_id": offered["id"], "pay": "manual", "payment": {"C": 1, "R": 1}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(before, len(game.state.players["A"].draw_history))

    def _target(self, game, selected, legal):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(set(legal), set(pending.payload_by_actor["A"]["target_schema"]["legal_refs"]))
        result = game.act("pilot:A", {"action_id": "choose", "targets": [selected]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(["activated_ability", "triggered_ability"], [item.kind for item in game.state.stack])

    def _optional(self, game, choice, *, verify_foreign=False):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        if verify_foreign:
            before = authoritative_state_hash(game.state)
            denied = game.act("pilot:B", {"action_id": "choose", "choice": choice})
            self.assertFalse(denied.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "choice": choice})
        self.assertTrue(result.ok, result.summary)
        return game

    def test_actual_gempalm_counts_current_goblins_including_kindred_after_source_departure(self):
        game = self.session(77160010)
        source = self.add(game.engine, INCINERATOR, zone="hand")
        own = self.add(game.engine, GOBLIN, ref="gempalm-own-goblin")
        opposing = self.add(game.engine, GOBLIN, seat="B", ref="gempalm-opposing-goblin")
        kindred = self.add(game.engine, KINDRED, seat="B")
        target = self.add(game.engine, SPIDER, seat="C")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        exile = self.add(game.engine, "Cremate", seat="B", zone="hand")
        self.prepare(game, mana={"C": 1, "R": 1})
        game.state.players["B"].mana_pool.update({"U": 1, "B": 1})
        self.checkpoint(game)
        self.assertNotIn("Creature", game.engine._effective_card_data(kindred)["type_line"])
        before = self._hand(game)
        self._cycle(game, source)
        self._target(game, target.ref, [own.ref, opposing.ref, target.ref])
        original = game.state.stack[-1].context[SOURCE_DAMAGE_CONTEXT]
        self.assertEqual(("graveyard", game.state.cards[source.object_id].logical_object_id),
                         (original["zone"], original["logical_object_id"]))
        self.cast(game, "B", bounce, [opposing.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[opposing.object_id].zone)
        self.cast(game, "B", exile, [source.ref])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        self.assertNotEqual(original["logical_object_id"], game.state.cards[source.object_id].logical_object_id)
        self.assertEqual(original, game.state.stack[-1].context[SOURCE_DAMAGE_CONTEXT])
        self.assertEqual(0, game.state.cards[target.object_id].marked_damage)
        self.assertEqual(set(), self._hand(game) - before)
        game = self._optional(game, "apply", verify_foreign=True)
        self.assertEqual(2, game.state.cards[target.object_id].marked_damage)
        self.assertEqual(set(), self._hand(game) - before)
        self.assertIsNone(self.resolve(game))
        self._assert_private_draw(game, before)
        self.replay(game, load=True)

    def test_actual_gempalm_can_decline_positive_damage_and_still_draw(self):
        game = self.session(77160020)
        source = self.add(game.engine, INCINERATOR, zone="hand")
        goblin = self.add(game.engine, GOBLIN, seat="B")
        target = self.add(game.engine, SPIDER, seat="C")
        self.prepare(game, mana={"C": 1, "R": 1})
        self.checkpoint(game)
        before = self._hand(game)
        self._cycle(game, source)
        self._target(game, target.ref, [goblin.ref, target.ref])
        game = self._optional(game, "decline")
        self.assertEqual(0, game.state.cards[target.object_id].marked_damage)
        self.assertIsNone(self.resolve(game))
        self._assert_private_draw(game, before)
        self.replay(game, load=True)

    def test_actual_gempalm_zero_creates_no_damage_event_and_preserves_its_draw(self):
        game = self.session(77160030)
        source = self.add(game.engine, INCINERATOR, zone="hand")
        target = self.add(game.engine, SPIDER, seat="B")
        self.prepare(game, mana={"C": 1, "R": 1})
        self.checkpoint(game)
        before = self._hand(game)
        self._cycle(game, source)
        self._target(game, target.ref, [target.ref])
        game = self._optional(game, "apply")
        self.assertEqual(0, game.state.cards[target.object_id].marked_damage)
        self.assertFalse(any(event.kind == "permanent_damaged" for event in game.state.turn_history.events))
        self.assertIsNone(self.resolve(game))
        self._assert_private_draw(game, before)
        self.replay(game, load=True)

    def test_actual_gempalm_cycles_without_an_eligible_creature_or_after_losing_the_target(self):
        for target_present in (False, True):
            with self.subTest(target_present=target_present):
                game = self.session(77160040 + target_present)
                source = self.add(game.engine, INCINERATOR, zone="hand")
                self.add(game.engine, KINDRED, seat="B")
                target = self.add(game.engine, SPIDER, seat="B") if target_present else None
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                self.prepare(game, mana={"C": 1, "R": 1})
                game.state.players["B"].mana_pool["U"] = 1
                self.checkpoint(game)
                before = self._hand(game)
                self._cycle(game, source)
                if target is not None:
                    self._target(game, target.ref, [target.ref])
                    self.cast(game, "B", bounce, [target.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[target.object_id].zone)
                    self.resolve_top(game)
                self.assertEqual(["activated_ability"], [item.kind for item in game.state.stack])
                self.assertIsNone(self.resolve(game))
                self._assert_private_draw(game, before)
                self.replay(game, load=True)

    def test_omitting_the_exact_goblin_query_lowering_restores_the_original_residual(self):
        with patch.object(source_self_effect_templates, "_cycled_goblin_damage_amount", return_value=None):
            report, _ = register_card(self.db, self.registry, INCINERATOR)
            self.assertFalse(report["strict_capability_ready"], report)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_quantity_choice_and_source_withdrawals_deny_the_complete_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap in (QUERY, OPTIONAL, SOURCE):
            with self.subTest(capability=cap):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Scoped cycled Goblin damage authority withdrawal"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                report, _ = register_card(self.db, registry, INCINERATOR)
                self.assertFalse(report["strict_capability_ready"], report)
                control, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()

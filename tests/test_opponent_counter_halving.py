"""Native evidence for opponent counter halving and its ordered replacement result."""

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
from quorune.compiler import counter_replacement_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.applicability import replacement_choice
from quorune.replacement.application import apply_replacement
from quorune.replacement.model import (
    COUNTER_PLACEMENT_ROUNDED_TO_ZERO_FIELD, ReplaceableEvent,
    ReplacementClass, ReplacementEffect, ReplacementEffectError,
)
from quorune.replacement.operations import (
    HalveCounterPlacementAmount, MultiplyAmount, ReplacementOperationError, operation_from_dict,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.counter_replacements import (
    CounterReplacementSourceContext, OPPONENT_COUNTER_HALVING_HANDLER_ID, OpponentCounterHalvingHandler,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
RAIDER = "Vorinclex, Monstrous Raider"
SPIDER = "Giant Spider"
PROLOGUE = "Prologue to Phyresis"
PLACEMENT = "counter.placement.quantity_replacement"


class OpponentCounterHalvingTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-source-counter-cards.json": {"Sol Ring"},
            "all-counter-self-cost-cards.json": {PROLOGUE},
            "attacking-player-quantity-cards.json": {SPIDER},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "counter-placement-event-cards.json": {"Battlegrowth", "Branching Evolution"},
            "counter-replacement-cards.json": {RAIDER},
            "self-loyalty-instant-permission-cards.json": {"The Wandering Emperor"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        pieces = []
        for name, names in support.items():
            record = json.loads((FIXTURES / name).read_bytes())
            record["cards"] = [row for row in record["cards"] if row["name"] in names]
            assert names == {row["name"] for row in record["cards"]}, name
            ids = {row["oracle_id"] for row in record["cards"]}
            record["rulings"] = [row for row in record.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(record), encoding="utf-8")
            pieces.append(part)
        path = folder / "opponent-counter-halving.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Opponent counter halving", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self):
        card = self.db.lookup(RAIDER, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, RAIDER)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = tuple(semantics.programs_for_oracle(card.oracle_id))
        return card, programs

    def test_complete_original_keeps_both_counter_arms_and_the_printed_keywords(self):
        card, programs = self._original()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        scopes = [p for p in programs if p.event == "counter.place"]
        self.assertEqual(2, len(scopes))
        self.assertTrue(all(PLACEMENT in p.capability_dependencies for p in scopes))
        descriptors = [wire for p in scopes for wire in p.handlers]
        half, = [wire for wire in descriptors if wire["handler_id"] == OPPONENT_COUNTER_HALVING_HANDLER_ID]
        doubled, = [wire for wire in descriptors if wire["handler_id"] == "replacement.counter.quantity.v2"]
        self.assertEqual({"handler_id": OPPONENT_COUNTER_HALVING_HANDLER_ID,
                          "schema_version": 1, "event": "counter.place"}, dict(half))
        self.assertEqual("source_controller", doubled["condition"]["placing_player_relation"])
        self.assertEqual(2, doubled["modification"]["multiplier"])
        self.assertTrue(any({"combat.attack.haste", "combat.damage.assignment.trample"} <= set(p.capability_dependencies)
                            for p in programs))

    def test_halving_wire_is_exact_and_zero_has_no_later_placement_replacement(self):
        half = OpponentCounterHalvingHandler()
        descriptor = {"handler_id": half.handler_id, "schema_version": 1, "event": "counter.place"}
        effect = half.replacement_effect(descriptor, CounterReplacementSourceContext("raider", "A"))
        self.assertEqual(effect.to_dict(), ReplacementEffect.from_dict(effect.to_dict()).to_dict())
        self.assertEqual(HalveCounterPlacementAmount(), operation_from_dict({"op": "halve_counter_placement_amount"}))
        with self.assertRaisesRegex(ReplacementOperationError, "unknown divisor"):
            operation_from_dict({"op": "halve_counter_placement_amount", "divisor": 3})
        invalid = replace(effect, event_kind="damage", conditions={})
        damage = ReplaceableEvent("damage-event", "damage", "B", {"amount": 3})
        with self.assertRaises(ReplacementEffectError):
            apply_replacement(replacement_choice(damage, (invalid,)), (invalid,), invalid.effect_id)
        double = ReplacementEffect(
            "double", "other_source", "counter.place", ReplacementClass.OTHER,
            operations=(MultiplyAmount("amount", 2),),
        )
        for amount, expected in ((1, 0), (3, 1), (4, 2)):
            with self.subTest(amount=amount):
                event = ReplaceableEvent("counter-event", "counter.place", "B",
                    {"amount": amount, "placing_player": "B", "target_kind": "player", "counter_name": "poison"})
                selection = replacement_choice(event, (effect, double))
                changed = apply_replacement(selection, (effect, double), effect.effect_id)
                self.assertEqual(expected, changed.payload["amount"])
                if not expected:
                    self.assertIs(changed.payload[COUNTER_PLACEMENT_ROUNDED_TO_ZERO_FIELD], True)
                    self.assertIsNone(replacement_choice(changed, (effect, double)))
                else:
                    self.assertNotIn(COUNTER_PLACEMENT_ROUNDED_TO_ZERO_FIELD, changed.payload)
                    self.assertEqual((double.effect_id,), replacement_choice(changed, (effect, double)).options)

    def _amount(self, game, target):
        return game.state.cards[target.object_id].counters.get("+1/+1", 0)

    def test_actual_vorinclex_uses_the_placing_player_for_both_permanent_and_player_counters(self):
        game = self.session(77162010)
        source = self.add(game.engine, RAIDER)
        a_spider = self.add(game.engine, SPIDER, ref="halving-own-target")
        b_spider = self.add(game.engine, SPIDER, seat="B", ref="halving-other-target")
        a_growth = self.add(game.engine, "Battlegrowth", zone="hand", ref="halving-own-growth")
        b_growth = self.add(game.engine, "Battlegrowth", zone="hand", seat="B", ref="halving-other-growth")
        a_poison = self.add(game.engine, PROLOGUE, zone="hand", ref="halving-own-prologue")
        b_poison = self.add(game.engine, PROLOGUE, zone="hand", seat="B", ref="halving-other-prologue")
        self.prepare(game, mana={"C": 1, "U": 1, "G": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "U": 1, "G": 1})
        self.checkpoint(game)
        self.assertLessEqual({"trample", "haste"}, set(game.engine._combat_keywords(source)))
        self.cast(game, "A", a_growth, [b_spider.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, self._amount(game, b_spider))
        self.cast(game, "B", b_growth, [a_spider.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, self._amount(game, a_spider))
        self.cast(game, "B", b_poison)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([0, 0, 0, 0], [game.state.players[seat].poison for seat in "ABCD"])
        self.cast(game, "A", a_poison)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([0, 2, 2, 2], [game.state.players[seat].poison for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_vorinclex_preserves_the_affected_players_order_and_zero_ends_placement(self):
        for halve_first in (False, True):
            with self.subTest(halve_first=halve_first):
                game = self.session(77162020 + halve_first)
                self.add(game.engine, RAIDER)
                self.add(game.engine, "Branching Evolution", seat="B")
                target = self.add(game.engine, SPIDER, seat="B")
                growth = self.add(game.engine, "Battlegrowth", seat="B", zone="hand")
                self.prepare(game)
                game.state.players["B"].mana_pool["G"] = 1
                self.checkpoint(game)
                self.cast(game, "B", growth, [target.ref])
                pending = self.resolve(game)
                self.assertIsNotNone(pending)
                self.assertEqual(("replacement.order", ["B"]), (pending.kind, pending.actors))
                options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
                half, = [row["id"] for row in options if row["id"].startswith(OPPONENT_COUNTER_HALVING_HANDLER_ID)]
                double, = [row["id"] for row in options if row["id"] != half]
                selected = half if halve_first else double
                self.assertEqual(0, self._amount(game, target))
                command = {"action_id": "choose", "replacement": selected}
                digest = authoritative_state_hash(game.state)
                wrong = game.act("pilot:A", command)
                self.assertFalse(wrong.ok)
                self.assertEqual(digest, authoritative_state_hash(game.state))
                self.assertNotIn(selected, json.dumps(game.packet("pilot:A", full=True)))
                game = self.replay(game, load=True)
                result = game.act("pilot:B", command)
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(0 if halve_first else 1, self._amount(game, target))
                self.replay(game, load=True)

    def test_actual_vorinclex_rounds_down_opposing_entry_and_preserves_zero_paid_loyalty(self):
        game = self.session(77162030)
        self.add(game.engine, RAIDER)
        emperor = self.add(game.engine, "The Wandering Emperor", seat="B", zone="hand")
        self.prepare(game, actor="A", active="B")
        game.state.players["B"].mana_pool.update({"C": 2, "W": 2})
        self.checkpoint(game)
        self.cast(game, "B", emperor)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[emperor.object_id]
        self.assertEqual(("battlefield", 1), (current.zone, current.counters["loyalty"]))
        self.priority(game, "B")
        offered, = [row for row in game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                    if row["id"] == f"activate:{emperor.ref}:ab3"]
        result = game.act("pilot:B", {"action_id": offered["id"], "targets": []})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("activated_ability", game.state.stack[-1].kind)
        self.assertEqual(1, game.state.cards[emperor.object_id].counters["loyalty"])
        self.assertIsNone(self.resolve(game))
        self.priority(game, "B")
        offered_ids = [row["id"] for row in game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]]
        self.assertFalse(any(value.startswith(f"activate:{emperor.ref}:") for value in offered_ids))
        self.replay(game, load=True)

    def test_omitting_the_exact_opposing_amount_restores_the_original_residual(self):
        with patch.object(counter_replacement_templates, "_OPPONENT_HALVES",
                          counter_replacement_templates.re.compile(r"(?!)")):
            report, _ = register_card(self.db, self.registry, RAIDER)
            self.assertFalse(report["strict_capability_ready"], report)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_counter_quantity_authority_withdrawal_denies_the_complete_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        document = deepcopy(raw)
        next(row for row in document["capabilities"] if row["id"] == PLACEMENT).update(
            status="blocked", blockers=["Scoped opponent counter halving authority withdrawal"])
        registry = CapabilityRegistry(document)
        index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((PLACEMENT,), profile=PROFILE)
        self.assertIn(f"status:{PLACEMENT}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        report, _ = register_card(self.db, registry, RAIDER)
        self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, "Sol Ring")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()

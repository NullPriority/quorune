"""Native evidence for the fixed counter of an ability another player controls."""

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
from quorune.compiler import resolution_effect_templates
from quorune.compiler.counter_templates import other_controller_ability_counter_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.stack_counter import other_controller_ability_counter_schema
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
NIMBLE = "Nimble Obstructionist"
SORCERER = "Prodigal Sorcerer"
VISIONARY = "Elvish Visionary"
COUNTER = "stack.counter.effect"
TARGET = "target.revalidate_resolution"
CYCLING = "activation.cycling.hand"


class OtherControllerAbilityCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-source-counter-cards.json": {"Sol Ring", "Shock", VISIONARY},
            "activation-paid-sacrifice-stat-cards.json": {"Stifle"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "fixed-qualified-stack-copy-cards.json": {SORCERER},
            "other-controller-ability-counter-cards.json": {NIMBLE},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        subsets = []
        for name, names in support.items():
            record = json.loads((FIXTURES / name).read_bytes())
            record["cards"] = [row for row in record["cards"] if row["name"] in names]
            assert {row["name"] for row in record["cards"]} == names, name
            ids = {row["oracle_id"] for row in record["cards"]}
            record["rulings"] = [row for row in record.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(record), encoding="utf-8")
            subsets.append(part)
        path = folder / "other-controller-counter.sqlite3"
        build_fixture_database(subsets, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Other-controller ability counter", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self):
        card = self.db.lookup(NIMBLE, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, NIMBLE)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = tuple(semantics.programs_for_oracle(card.oracle_id))
        trigger, = [program for program in programs if program.event == "card.cycled.self"]
        return card, trigger, programs

    def test_complete_nimble_keeps_cycling_flash_flying_and_the_exact_other_controller_counter(self):
        card, trigger, programs = self._original()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual("hand", trigger.active_zone)
        self.assertEqual(other_controller_ability_counter_schema(), thaw_value(trigger.target_schema))
        self.assertEqual([{"op": "counter_stack_target", "stack": "$target.0"}], thaw_value(trigger.effects))
        self.assertLessEqual({COUNTER, TARGET, "trigger.event.normalized_public_action"},
                             set(trigger.capability_dependencies))
        activation, = [program for program in programs if CYCLING in program.capability_dependencies]
        self.assertEqual("hand", activation.active_zone)
        self.assertEqual([{"op": "draw", "player": "$controller", "count": 1, "private": True}],
                         thaw_value(activation.effects))
        self.assertTrue(any("timing.cast.printed_flash" in program.capability_dependencies for program in programs))
        self.assertTrue(any("combat.block.flying" in program.capability_dependencies for program in programs))

    def test_qualified_counter_rejects_different_controllers_counts_and_payment_forms(self):
        _, trigger, _ = self._original()
        schema = thaw_value(trigger.target_schema)
        for changes in ({"controller_relation": "you"}, {"count": 2}, {"count": True}):
            with self.subTest(changes=changes):
                altered = replace(trigger, target_schema={**schema, **changes})
                report = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(report["strict"], report)
        for text in (
            "Counter target activated or triggered ability you control.",
            "Counter target spell you don't control.",
            "Counter target triggered ability you don't control.",
            "Counter target activated or triggered ability you don't control unless its controller pays {1}.",
        ):
            self.assertIsNone(other_controller_ability_counter_effect_template(text), text)

    def _hand(self, game, seat="A"):
        return set(game.state.players[seat].zones["hand"])

    def _assert_private_cycle_draw(self, game, before):
        added, = self._hand(game) - before
        ref = game.state.cards[added].ref
        self.assertIn(ref, json.dumps(game.packet("pilot:A", full=True)))
        for seat in "BCD":
            self.assertNotIn(ref, json.dumps(game.packet(f"pilot:{seat}", full=True)))

    def _activate(self, game, seat, source, targets=(), *, payment=None):
        self.priority(game, seat)
        actions = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")]
        command = {"action_id": offered["id"], "targets": list(targets), "pay": "auto"}
        if payment is not None:
            command.update(pay="manual", payment=payment)
        result = game.act(f"pilot:{seat}", command)
        self.assertTrue(result.ok, result.summary)

    def _cycle(self, game, source):
        before = len(game.state.players["A"].draw_history)
        self._activate(game, "A", source, payment={"C": 2, "U": 1})
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(before, len(game.state.players["A"].draw_history))

    def _choose(self, game, target, expected, rejected=()):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(set(expected), set(pending.payload_by_actor["A"]["target_schema"]["legal_refs"]))
        for seat, ref in rejected:
            digest = authoritative_state_hash(game.state)
            wrong = game.act(f"pilot:{seat}", {"action_id": "choose", "targets": [ref]})
            self.assertFalse(wrong.ok)
            self.assertEqual(digest, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": "choose", "targets": [target]})
        self.assertTrue(result.ok, result.summary)

    def _sorcerer(self, game, seat):
        source = self.add(game.engine, SORCERER, seat=seat, ref=f"{seat.lower()}-ability-source")
        source.acquired_control_turn_count = -1
        return source

    def _assert_cycling_under_trigger(self, game, source):
        draw, trigger = game.state.stack[-2:]
        self.assertEqual(("activated_ability", "triggered_ability"), (draw.kind, trigger.kind))
        self.assertEqual((source.object_id, source.object_id), (draw.source_object_id, trigger.source_object_id))
        return draw.ref

    def test_actual_nimble_accepts_only_other_activated_abilities_and_counters_before_the_cycle_draw(self):
        game = self.session(77158010)
        nimble = self.add(game.engine, NIMBLE, zone="hand")
        shock = self.add(game.engine, "Shock", zone="hand")
        first, second, third = [self._sorcerer(game, seat) for seat in "ABC"]
        self.prepare(game, mana={"C": 2, "R": 1, "U": 1})
        self.checkpoint(game)
        self.cast(game, "A", shock, ["D"])
        spell = game.state.stack[-1].ref
        self._activate(game, "A", first, ["D"])
        own = game.state.stack[-1].ref
        self._activate(game, "B", second, ["A"])
        target = game.state.stack[-1].ref
        self._activate(game, "C", third, ["D"])
        eligible = game.state.stack[-1].ref

        before = self._hand(game)
        self._cycle(game, nimble)
        game = self.replay(game, load=True)
        self._choose(game, target, [target, eligible], [("A", own), ("A", spell), ("B", target)])
        draw = self._assert_cycling_under_trigger(game, nimble)
        self.resolve_top(game)
        self.assertNotIn(target, {item.ref for item in game.state.stack})
        self.assertEqual(draw, game.state.stack[-1].ref)
        self.assertEqual(set(), self._hand(game) - before)
        self.resolve_top(game)
        self._assert_private_cycle_draw(game, before)
        self.assertEqual(40, game.state.players["A"].life)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(36, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_actual_nimble_counters_the_other_players_trigger_without_their_draw(self):
        game = self.session(77158020)
        nimble = self.add(game.engine, NIMBLE, zone="hand")
        visionary = self.add(game.engine, VISIONARY, seat="B", zone="hand")
        self.prepare(game, active="B", actor="A", mana={"C": 2, "U": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "G": 1})
        self.checkpoint(game)
        self.cast(game, "B", visionary)
        self.resolve_top(game)
        target = game.state.stack[-1]
        self.assertEqual(("triggered_ability", "B"), (target.kind, target.controller))
        before = self._hand(game)
        opposing_before = self._hand(game, "B")
        self._cycle(game, nimble)
        self._choose(game, target.ref, [target.ref])
        self._assert_cycling_under_trigger(game, nimble)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(opposing_before, self._hand(game, "B"))
        self._assert_private_cycle_draw(game, before)
        self.replay(game, load=True)

    def test_actual_nimble_can_cycle_with_no_eligible_counter_target(self):
        game = self.session(77158030)
        nimble = self.add(game.engine, NIMBLE, zone="hand")
        own = self._sorcerer(game, "A")
        self.prepare(game, mana={"C": 2, "U": 1})
        self.checkpoint(game)
        self._activate(game, "A", own, ["B"])
        before = self._hand(game)
        self._cycle(game, nimble)
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertEqual(["activated_ability", "activated_ability"], [item.kind for item in game.state.stack])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(39, game.state.players["B"].life)
        self._assert_private_cycle_draw(game, before)
        self.replay(game, load=True)

    def test_actual_nimble_draws_after_its_counter_target_leaves_the_stack(self):
        game = self.session(77158040)
        nimble = self.add(game.engine, NIMBLE, zone="hand")
        opposing = self._sorcerer(game, "B")
        stifle = self.add(game.engine, "Stifle", seat="C", zone="hand")
        self.prepare(game, mana={"C": 2, "U": 1})
        game.state.players["C"].mana_pool["U"] = 1
        self.checkpoint(game)
        self._activate(game, "B", opposing, ["A"])
        target = game.state.stack[-1].ref
        before = self._hand(game)
        self._cycle(game, nimble)
        self._choose(game, target, [target])
        self._assert_cycling_under_trigger(game, nimble)
        self.cast(game, "C", stifle, [target])
        self.resolve_top(game)
        self.assertNotIn(target, {item.ref for item in game.state.stack})
        self.assertEqual(set(), self._hand(game) - before)
        self.resolve_top(game)
        self.assertEqual(set(), self._hand(game) - before)
        self.assertIsNone(self.resolve(game))
        self._assert_private_cycle_draw(game, before)
        self.assertEqual(40, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_nimble_can_still_be_cast_with_flash_and_has_flying(self):
        game = self.session(77158050)
        nimble = self.add(game.engine, NIMBLE, zone="hand")
        self.prepare(game, active="B", actor="A", mana={"C": 2, "U": 1})
        self.checkpoint(game)
        self.assertEqual("B", game.state.active_player)
        self.cast(game, "A", nimble)
        self.assertIsNone(self.resolve(game))
        live = game.state.cards[nimble.object_id]
        self.assertEqual("battlefield", live.zone)
        self.assertIn("flying", game.engine._combat_keywords(live))
        self.replay(game, load=True)

    def test_omitting_the_exact_counter_lowering_restores_nimbles_material_residual(self):
        fixtures = tuple(parser for parser in resolution_effect_templates._DIRECT_RESOLUTION_EFFECT_COMPILERS
                         if parser is not other_controller_ability_counter_effect_template)
        with patch.object(resolution_effect_templates, "_DIRECT_RESOLUTION_EFFECT_COMPILERS", fixtures):
            report, _ = register_card(self.db, self.registry, NIMBLE)
            self.assertFalse(report["strict_capability_ready"], report)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_counter_target_and_cycling_withdrawals_deny_the_complete_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap in (COUNTER, TARGET, CYCLING):
            with self.subTest(capability=cap):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Scoped qualified ability counter authority withdrawal"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                report, _ = register_card(self.db, registry, NIMBLE)
                self.assertFalse(report["strict_capability_ready"], report)
                control, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()

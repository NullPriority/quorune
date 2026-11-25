"""Pinned any-player upkeep and activating-player draw-step abilities."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.activation_condition_model import (
    PUBLIC_ACTIVATION_CAPABILITY, PUBLIC_ACTIVATION_FIELD, PUBLIC_TIMED_ACTIVATION_CAPABILITY,
    PublicActivationTiming, public_activation_descriptor, public_activation_program_timing,
)
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.activation.model import ActivationProposalError, ActivationProposalRequest
from quorune.rules.activation.proposal import build_activation_offer, build_activation_proposal
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
WELL = "Well of Knowledge"
HOURGLASS = "Infinite Hourglass"
CLOCK = "Armageddon Clock"
LEGACY = ("Excavation", "Endbringer's Revel")
TIMINGS = {
    WELL: PublicActivationTiming.ACTIVATORS_DRAW_STEP,
    HOURGLASS: PublicActivationTiming.ANY_UPKEEP,
    CLOCK: PublicActivationTiming.ANY_UPKEEP,
}


class TimedPublicActivationTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name)
        legacy = json.loads((FIXTURES / "activation-view-equivalence-cards.json").read_bytes())
        legacy.update(cards=[card for card in legacy["cards"] if card["name"] in LEGACY], rulings=[])
        legacy_path = path / "public-v1.json"
        legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
        database = path / "timed-public-activations.sqlite3"
        build_fixture_database([
            FIXTURES / "bound-effect-program-cards.json",
            FIXTURES / "timed-public-activation-cards.json",
            legacy_path,
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Public Step Windows", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_permission_versions_have_closed_disjoint_windows(self):
        fields = {"event": "activate", "active_zone": "battlefield", "effects": ({"op": "draw"},)}
        for timing in PublicActivationTiming:
            descriptor = public_activation_descriptor(timing)
            version = 2 if timing in TIMINGS.values() else 1
            self.assertEqual({"schema_version": version, "timing": timing.value}, descriptor)
            program = SimpleNamespace(**fields, cost_schema={PUBLIC_ACTIVATION_FIELD: descriptor})
            self.assertIs(timing, public_activation_program_timing(program))
            for updates in ({"schema_version": 3 - version}, {"schema_version": True},
                            {"timing": "during_any_draw"}, {"phase": "main"}):
                with self.subTest(timing=timing, updates=updates), self.assertRaises(ValueError):
                    public_activation_program_timing(SimpleNamespace(
                        **fields, cost_schema={PUBLIC_ACTIVATION_FIELD: {**descriptor, **updates}},
                    ))

    def test_exact_timing_grammar_cannot_drop_unrepresented_cost_or_effect_material(self):
        well = self.db.lookup(WELL, fuzzy=False)
        for text in (
            "{2}: Draw a card. Any player may activate this ability but only during their turn.",
            "{2}: Draw a card. Any player may activate this ability but only during any draw step.",
            "{2}: Draw a card. Any player may activate this ability but only during an opponent's upkeep.",
            "{2}: Draw a card. Skip an unrepresented step. Any player may activate this ability but only during their draw step.",
            "{2}: Draw a card. Any player may activate this ability but only during their draw step. Restart the game.",
            "{T}: Add {C}. Any player may activate this ability but only during any upkeep step.",
        ):
            with self.subTest(text=text):
                program = compile_oracle_card(
                    replace(well, oracle_text=text), capability_registry=self.registry, capability_profile=PROFILE,
                )
                self.assertNotEqual("exact", program.status)

    def test_intact_originals_admit_natively_and_infer_the_new_timing_minimum(self):
        for name, timing in TIMINGS.items():
            with self.subTest(card=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = [p for p in semantics.programs_for_oracle(card.oracle_id)
                            if PUBLIC_TIMED_ACTIVATION_CAPABILITY in p.capability_dependencies]
                self.assertIs(timing, public_activation_program_timing(program))
                self.assertIn(PUBLIC_ACTIVATION_CAPABILITY, program.capability_dependencies)
                unlabelled = replace(program, coverage=[])
                self.assertTrue(bind_semantic_program_runtime(
                    unlabelled, capability_registry=self.registry, profile=PROFILE,
                )["strict"])
                deps = tuple(d for d in program.capability_dependencies if d != PUBLIC_TIMED_ACTIVATION_CAPABILITY)
                unowned = replace(unlabelled, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                rejected = bind_semantic_program_runtime(unowned, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(rejected["strict"])
                self.assertIn("capability:undeclared_runtime_dependency:" + PUBLIC_TIMED_ACTIVATION_CAPABILITY,
                              rejected["blockers"])

    def _lands(self, game, seat, count, name="Forest"):
        return tuple(self.add(game.engine, name, seat=seat, ref=f"timed-land-{seat}-{index}")
                     for index in range(count))

    def _start(self, game):
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        self.checkpoint(game)

    def _step(self, game, active, step):
        self.until(game, lambda s: s.state.active_player == active and s.state.phase == "beginning"
                   and s.state.step == step and s.state.pending_decision.kind == "priority")

    def _offers(self, game, actor, source):
        actions = game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]
        return [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")]

    def _reject(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _wrong_window(self, game, actor, source):
        self.priority(game, actor)
        self.assertEqual([], self._offers(game, actor, source))
        engine = game.engine
        ability, = engine._activated_abilities(source)
        row = build_activation_offer(engine, actor, source, ability)
        self.assertEqual("public_activation_step_window", row.reason)
        before = authoritative_state_hash(game.state)
        request = ActivationProposalRequest.from_submission(actor, {
            "source": source.ref, "ability": ability.ability_id, "pay": "auto",
        })
        with self.assertRaises(ActivationProposalError) as rejected:
            build_activation_proposal(engine, request)
        self.assertEqual("public_activation_step_window", rejected.exception.reason)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self._reject(game, actor, {"action_id": f"activate:{source.ref}:{ability.ability_id}", "pay": "auto"})

    def _activate(self, game, actor, source, cost):
        self.priority(game, actor)
        offer, = self._offers(game, actor, source)
        self.assertEqual({"GENERIC": cost}, offer["cost_summary"]["m"])
        command = {"action_id": offer["id"], "pay": "auto"}
        self._reject(game, "D", command)
        result = game.act("pilot:" + actor, command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(actor, game.state.stack[-1].controller)
        self.assertEqual(source.object_id, game.state.stack[-1].source_object_id)

    def test_real_well_only_the_active_draw_player_can_pay_twice_and_draw_privately(self):
        game = self.session(60604010)
        well = self.add(game.engine, WELL, seat="C")
        lands = self._lands(game, "B", 4)
        foreign = self._lands(game, "C", 2)
        hidden = tuple(self.add(game.engine, "Grizzly Bears", zone="library", seat="B", ref=f"timed-private-{i}")
                       for i in range(3))
        self.assert_closed(game, well)
        self._start(game)
        self._wrong_window(game, "B", well)
        self._step(game, "B", "upkeep")
        self._wrong_window(game, "B", well)
        hand = len(game.state.players["B"].zones["hand"])
        owner_hand = len(game.state.players["C"].zones["hand"])
        self._step(game, "B", "draw")
        self.assertEqual(hand + 1, len(game.state.players["B"].zones["hand"]))
        for repeat in range(2):
            self._activate(game, "B", well, 2)
            self.assertEqual(hand + 1 + repeat, len(game.state.players["B"].zones["hand"]))
            self._wrong_window(game, "C", well)
            if repeat == 0:
                game = self.replay(game, load=True)
            self.resolve_top(game)
            self.assertEqual(hand + 2 + repeat, len(game.state.players["B"].zones["hand"]))
        self.assertEqual(owner_hand, len(game.state.players["C"].zones["hand"]))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "hand" for card in hidden))
        self.assertTrue(all(game.state.cards[land.object_id].tapped for land in lands))
        self.assertFalse(any(game.state.cards[land.object_id].tapped for land in foreign))
        for actor in "ACD":
            packet = json.dumps(game.packet("pilot:" + actor, full=True))
            for card in hidden:
                self.assertNotIn(card.ref, packet)
        self.replay(game, load=True)

    def test_real_hourglass_nonactive_foreign_player_can_pay_at_zero_then_remove_live_bonus(self):
        game = self.session(60604020)
        hourglass = self.add(game.engine, HOURGLASS, seat="B")
        bear = self.add(game.engine, "Grizzly Bears")
        lands = self._lands(game, "C", 6)
        other_lands = self._lands(game, "A", 3)
        starter = game.state.players["B"].zones["hand"][-1]
        game.engine.move_card(starter, "library", log=False)
        self.assert_closed(game, hourglass)
        self._start(game)
        self._wrong_window(game, "C", hourglass)
        self._step(game, "B", "upkeep")
        self.assertEqual(1, self.trigger_count(game, hourglass))
        self.assertEqual(0, game.state.cards[hourglass.object_id].counters.get("time", 0))
        self._activate(game, "C", hourglass, 3)
        self.resolve_top(game)
        self.assertEqual(0, game.state.cards[hourglass.object_id].counters.get("time", 0))
        self.assertEqual(2, game.engine._numeric_stat(bear.object_id, "power"))
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[hourglass.object_id].counters.get("time", 0))
        self.assertEqual(3, game.engine._numeric_stat(bear.object_id, "power"))
        self._activate(game, "C", hourglass, 3)
        self.assertEqual(3, game.engine._numeric_stat(bear.object_id, "power"))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(0, game.state.cards[hourglass.object_id].counters.get("time", 0))
        self.assertEqual(2, game.engine._numeric_stat(bear.object_id, "power"))
        self.assertTrue(all(game.state.cards[land.object_id].tapped for land in lands))
        self._step(game, "C", "upkeep")
        self.assertEqual(0, self.trigger_count(game, hourglass))
        self._activate(game, "A", hourglass, 3)
        self.resolve_top(game)
        self.assertTrue(all(game.state.cards[land.object_id].tapped for land in other_lands))
        self.replay(game, load=True)

    def test_real_clock_uses_post_upkeep_removal_count_for_draw_step_damage(self):
        game = self.session(60604030)
        clock = self.add(game.engine, CLOCK, seat="B")
        clock.counters["doom"] = 1
        self._lands(game, "C", 4)
        self._lands(game, "B", 4)
        self.assert_closed(game, clock)
        self._start(game)
        life = {actor: game.state.players[actor].life for actor in "ABCD"}
        self._step(game, "B", "upkeep")
        self.resolve_top(game)
        self.assertEqual(2, game.state.cards[clock.object_id].counters["doom"])
        self._activate(game, "C", clock, 4)
        self.assertEqual(2, game.state.cards[clock.object_id].counters["doom"])
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[clock.object_id].counters["doom"])
        hand = len(game.state.players["B"].zones["hand"])
        self._step(game, "B", "draw")
        self.assertEqual(1, self.trigger_count(game, clock))
        self.assertEqual(hand + 1, len(game.state.players["B"].zones["hand"]))
        self.assertEqual(life, {actor: game.state.players[actor].life for actor in "ABCD"})
        self._wrong_window(game, "B", clock)
        self.resolve_top(game)
        self.assertEqual({actor: amount - 1 for actor, amount in life.items()},
                         {actor: game.state.players[actor].life for actor in "ABCD"})
        self.replay(game, load=True)

    def test_real_departure_after_public_payment_keeps_the_ability_and_resolves_safely(self):
        game = self.session(60604040)
        clock = self.add(game.engine, CLOCK, seat="B")
        payment = self._lands(game, "C", 4)
        removal = self.add(game.engine, "Disenchant", seat="D", zone="hand")
        self._lands(game, "D", 2, "Generic Bound Plains")
        self.assert_closed(game, clock)
        self._start(game)
        self._step(game, "B", "upkeep")
        self.resolve_top(game)
        self._activate(game, "C", clock, 4)
        self.cast(game, "D", removal, [clock.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[clock.object_id].zone)
        self.assertEqual(1, len(game.state.stack))
        self.assertEqual("C", game.state.stack[-1].controller)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[clock.object_id].zone)
        self.assertTrue(all(game.state.cards[land.object_id].tapped for land in payment))
        self.replay(game, load=True)

    def test_native_wrong_window_witness_detects_deleted_step_check(self):
        game = self.session(60604050)
        well = self.add(game.engine, WELL)
        self._lands(game, "A", 2)
        self.assert_closed(game, well)
        self._start(game)
        self._wrong_window(game, "A", well)
        with patch.object(PublicActivationTiming, "permits_step", return_value=True):
            with self.assertRaises(AssertionError):
                self._wrong_window(game, "A", well)

    def test_genuine_public_step_withdrawal_keeps_priority_and_sorcery_permissions(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = deepcopy(json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes()))
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        target = next(row for row in raw["capabilities"] if row["id"] == PUBLIC_TIMED_ACTIVATION_CAPABILITY)
        target.update(status="blocked", blockers=["Scoped public step permission withdrawal"])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((PUBLIC_TIMED_ACTIVATION_CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + PUBLIC_TIMED_ACTIVATION_CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in TIMINGS:
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], (name, report))
        for name, timing in zip(LEGACY, (PublicActivationTiming.PRIORITY, PublicActivationTiming.SORCERY)):
            report, semantics = register_card(self.db, registry, name)
            self.assertTrue(report["strict_capability_ready"], (name, report["blockers"]))
            card = self.db.lookup(name, fuzzy=False)
            public, = [p for p in semantics.programs_for_oracle(card.oracle_id)
                       if PUBLIC_ACTIVATION_CAPABILITY in p.capability_dependencies]
            self.assertIs(timing, public_activation_program_timing(public))


if __name__ == "__main__":
    unittest.main()

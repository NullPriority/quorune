"""CR 602.5b and 608.2h: a timed Thrull payment and two source counter effects."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.activation_paid_characteristic_model import ACTIVATION_PAID_SUBTYPE_CONTEXT
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import TurnEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.paid_sacrifice_conditions import ACTIVATION_PAID_THRULL_COUNTERS_CAPABILITY as CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Ebon Praetor"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class ActivationPaidThrullCountersTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "activation-paid-thrull.sqlite3"
        build_fixture_database([basic, FIXTURES / "activation-paid-thrull-counter-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Paid Thrull counters", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertTrue(any(p.event == "step.begin" for p in programs))
        ability, = [p for p in programs if p.event == "activate"]
        return card, ability

    def test_exact_original_retains_price_timing_upkeep_and_typed_counter_order(self):
        card, program = self.program()
        self.assertIn(CAPABILITY, program.capability_dependencies)
        self.assertIn("activation.condition.phase_window", program.capability_dependencies)
        self.assertIn("activation.usage.once_per_turn", program.capability_dependencies)
        self.assertEqual("once_per_turn", program.cost_schema["activation_limit"])
        self.assertIsNone(program.target_schema)
        removal, wrapper = program.effects
        self.assertEqual({"op": "remove_counters", "card": "$source.zone_object",
                          "counter": "-2/-2", "amount": 1, "source": "$source"}, removal)
        self.assertEqual(("thrull", 2), (wrapper["condition"]["trait"], wrapper["cast_fact"]["schema_version"]))
        child, = wrapper["effects"]
        self.assertEqual({"op": "place_counters", "card": "$source.zone_object",
                          "counter": "+1/+0", "amount": 1, "source": "$source"}, child)
        for text in (
            card.oracle_text.replace("was a Thrull", "was a Human"),
            card.oracle_text.replace("a -2/-2 counter from", "a +1/+1 counter from"),
            card.oracle_text.replace(" and only once each turn", ""),
            card.oracle_text.replace("+1/+0 counter", "+1/+1 counter"),
        ):
            with self.subTest(text=text):
                value = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry,
                                            capability_profile=PROFILE)
                self.assertFalse(any(CAPABILITY in n.capability_dependencies for f in value.faces for n in f.nodes))

    def test_raw_recipe_rejects_changed_counter_limit_or_paid_reference(self):
        _, program = self.program()
        removal, condition = program.effects
        for changed in (
            replace(program, cost_schema={k: v for k, v in program.cost_schema.items() if k != "activation_limit"}),
            replace(program, effects=(condition, removal)),
            replace(program, effects=({**removal, "amount": True}, condition)),
            replace(program, effects=(removal, {**condition, "cast_fact": True})),
        ):
            with self.subTest(changed=changed.to_dict()):
                result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)
        dependencies = [cap for cap in program.capability_dependencies if cap != CAPABILITY]
        changed = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def scene(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, NAME)
        thrull = self.add(game.engine, "Basal Thrull")
        human = self.add(game.engine, "Elite Vanguard")
        foreign = self.add(game.engine, "Mindstab Thrull", seat="B")
        private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="thrull-private")
        self.assert_closed(game, source)
        self.prepare(game)
        return game, source, thrull, human, foreign, private

    def upkeep(self, game, active):
        next_turn = game.state.turn_sequence + 1
        game.engine._begin_turn(TurnEntry(turn_id=f"praetor-{active}-{next_turn}", player=active,
                                          created_sequence=game.state.turn_sequence))
        game.engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.engine.pump()
        self.assertEqual("upkeep", game.state.step)
        self.checkpoint(game)

    def command(self, game, source, paid):
        current = game.state.cards[source.object_id]
        ability, = (a for a in game.engine._activated_abilities(current) if a.choices)
        return {"action_id": f"activate:{current.ref}:{ability.ability_id}", "cost_objects": [paid.ref], "pay": "auto"}

    @staticmethod
    def offered(game, action_id):
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return any(row["id"] == action_id for row in actions)

    def reject(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act(actor, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def pay(self, game, source, body):
        self.priority(game, "A")
        command = self.command(game, source, body)
        self.assertTrue(self.offered(game, command["action_id"]))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        return game.state.stack[-1].context[ACTIVATION_PAID_SUBTYPE_CONTEXT]

    def test_actual_thrull_bonus_applies_with_no_counter_to_remove_and_once_per_turn(self):
        game, source, thrull, spare, foreign, private = self.scene(50306020)
        self.upkeep(game, "A")
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual(0, game.state.cards[source.object_id].counters.get("-2/-2", 0))
        self.reject(game, "pilot:A", self.command(game, source, foreign))
        facts = self.pay(game, source, thrull)
        self.assertIn("thrull", facts["characteristics"]["subtypes"])
        self.assertEqual("graveyard", game.state.cards[thrull.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertEqual(1, current.counters.get("+1/+0", 0))
        self.assertEqual(0, current.counters.get("-2/-2", 0))
        self.priority(game, "A")
        again = self.command(game, source, spare)
        self.assertFalse(self.offered(game, again["action_id"]))
        self.reject(game, "pilot:A", again)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[source.object_id].counters["-2/-2"])
        for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_non_thrull_removes_available_counter_and_source_can_pay(self):
        for self_paid in (False, True):
            with self.subTest(self_paid=self_paid):
                game, source, _, human, _, _ = self.scene(50306021 + self_paid)
                self.upkeep(game, "A")
                self.assertIsNone(self.resolve(game))
                self.assertEqual(1, game.state.cards[source.object_id].counters["-2/-2"])
                facts = self.pay(game, source, source if self_paid else human)
                self.assertNotIn("thrull", facts["characteristics"]["subtypes"])
                self.assertIsNone(self.resolve(game))
                current = game.state.cards[source.object_id]
                self.assertEqual("graveyard" if self_paid else "battlefield", current.zone)
                self.assertEqual(0, current.counters.get("-2/-2", 0))
                self.assertEqual(0, current.counters.get("+1/+0", 0))
                self.replay(game)

    def test_actual_off_turn_upkeep_and_main_phase_omit_and_reject_activation(self):
        game, source, thrull, _, _, _ = self.scene(50306023)
        self.checkpoint(game)
        main = self.command(game, source, thrull)
        self.assertFalse(self.offered(game, main["action_id"]))
        self.reject(game, "pilot:A", main)
        self.upkeep(game, "B")
        self.priority(game, "A")
        foreign = self.command(game, source, thrull)
        self.assertFalse(self.offered(game, foreign["action_id"]))
        self.reject(game, "pilot:A", foreign)
        self.assertEqual("battlefield", game.state.cards[thrull.object_id].zone)
        self.replay(game)

    def test_actual_thrull_witness_kills_missing_paid_subtype_mutation(self):
        from quorune import activation_paid_characteristic_model

        read = activation_paid_characteristic_model.resolve_activation_paid_creature_characteristics
        with patch.object(activation_paid_characteristic_model, "resolve_activation_paid_creature_characteristics",
                          side_effect=lambda item: replace(read(item), subtypes=())):
            with self.assertRaises(AssertionError):
                self.test_actual_thrull_bonus_applies_with_no_counter_to_remove_and_once_per_turn()

    def test_verified_withdrawal_preserves_other_activated_paid_characteristics(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Thrull counter outcome withdrawn"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
                               discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        for name in (NAME, "Falkenrath Torturer", "Gr\u00edma Wormtongue"):
            result, _ = register_card(self.db, registry, name)
            self.assertEqual(name != NAME, result["strict_capability_ready"], result["blockers"])

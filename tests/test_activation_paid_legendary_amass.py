"""CR 602.2b and 701.47a: targeted life loss and a sealed legendary cost."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.paid_sacrifice_conditions import ACTIVATION_PAID_LEGENDARY_AMASS_CAPABILITY as CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Gr\u00edma Wormtongue"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class ActivationPaidLegendaryAmassTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        basic = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        basic["cards"] = [row for row in basic["cards"] if row["name"] == "Generic Bound Plains"]
        basic["rulings"] = []
        base = folder / "base.json"
        base.write_text(json.dumps(basic), encoding="utf-8")
        path = folder / "activation-paid-legendary-amass.sqlite3"
        build_fixture_database([base, FIXTURES / "activation-paid-legendary-amass-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Paid legendary Amass", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertTrue(any("life.gain.prohibition.static" in p.capability_dependencies for p in programs))
        activation, = [p for p in programs if p.event == "activate"]
        return card, activation

    def test_complete_original_retains_the_target_price_static_sibling_and_legendary_branch(self):
        card, program = self.program()
        self.assertEqual({"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any"},
                         program.target_schema)
        self.assertTrue(program.cost_schema["tap_source"])
        self.assertEqual(1, program.cost_schema["choices"][0]["other"])
        self.assertIn(CAPABILITY, program.capability_dependencies)
        loss, conditional = program.effects
        self.assertEqual({"op": "lose_life", "player": "$target.0", "amount": 1}, loss)
        self.assertEqual((2, "legendary"), (conditional["cast_fact"]["schema_version"], conditional["condition"]["trait"]))
        self.assertEqual([{"op": "amass", "subtype": "Orc", "amount": 2}], list(conditional["effects"]))
        for text in (
            card.oracle_text.replace("Sacrifice another", "Sacrifice a"),
            card.oracle_text.replace("Target player", "Target opponent"),
            card.oracle_text.replace("was legendary", "was a Human"),
            card.oracle_text.replace("Orcs 2", "Zombies 2"),
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry,
                                         capability_profile=PROFILE)
                self.assertFalse(any(CAPABILITY in n.capability_dependencies for f in ir.faces for n in f.nodes))

    def test_raw_target_or_activated_price_cannot_diverge_from_the_printed_recipe(self):
        _, program = self.program()
        loss, conditional = program.effects
        for changed in (
            replace(program, handlers=()),
            replace(program, target_schema={**program.target_schema, "player_relation": "opponent"}),
            replace(program, cost_schema={**program.cost_schema, "tap_source": False}),
            replace(program, effects=(conditional, loss)),
            replace(program, effects=(loss, {**conditional, "effects": [{"op": "amass", "subtype": "Orc", "amount": True}]})),
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

    def scene(self, *, legendary):
        game = self.session(701470620 + legendary)
        source = self.add(game.engine, NAME)
        source.acquired_control_turn_count = -1
        paid = self.add(game.engine, "Yargle, Glutton of Urborg" if legendary else "Elite Vanguard")
        foreign = self.add(game.engine, "Elite Vanguard", seat="B", ref="foreign-price")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        private = self.add(game.engine, "Counterspell", seat="D", zone="hand", ref="amass-private")
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in options if row["id"].startswith(f"activate:{source.ref}:")]
        self.assertIn("C", action["target_schema"]["legal_refs"])
        command = {"action_id": action["id"], "targets": ["C"], "cost_objects": [paid.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        for principal, payment in (("pilot:B", [paid.ref]), ("pilot:A", [source.ref]), ("pilot:A", [foreign.ref])):
            self.assertFalse(game.act(principal, {**command, "cost_objects": payment}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual("graveyard", game.state.cards[paid.object_id].zone)
        facts = game.state.stack[-1].context[ACTIVATION_PAID_SUBTYPE_CONTEXT]
        self.assertEqual(legendary, "legendary" in facts["characteristics"]["supertypes"])
        return game, source, paid, bounce, private

    @staticmethod
    def armies(game):
        return [card for card in game.state.cards.values() if card.controller == "A" and card.zone == "battlefield"
                and card.is_token and "army" in game.engine._type_parts(game.engine._effective_card_data(card)["type_line"])[1]]

    def test_actual_guarded_amass_uses_paid_legendary_fact_even_after_source_leaves(self):
        for legendary in (False, True):
            with self.subTest(legendary=legendary):
                game, source, _, bounce, private = self.scene(legendary=legendary)
                if legendary:
                    self.cast_for(game, "B", bounce, [source.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[source.object_id].zone)
                game = self.replay(game, load=True)
                start = len(game.state.events)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(39, game.state.players["C"].life)
                armies = self.armies(game)
                self.assertEqual(int(legendary), len(armies))
                if legendary:
                    army, = armies
                    self.assertEqual("Orc Army", army.printed_name)
                    self.assertEqual(2, army.counters["+1/+1"])
                    codes = [e.code for e in game.state.events[start:]]
                    self.assertLess(codes.index("effect.life"), codes.index("keyword_action.amass.completed"))
                for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
                    self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
                self.replay(game)

    def test_actual_sole_target_loss_stops_both_results_without_refunding_cost(self):
        game, source, paid, _, _ = self.scene(legendary=True)
        self.priority(game, "C")
        accepted = game.act("pilot:C", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(accepted.ok, accepted.summary)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual("graveyard", game.state.cards[paid.object_id].zone)
        self.assertEqual([], self.armies(game))
        self.replay(game)

    def test_actual_legendary_payment_witness_kills_missing_supertype_mutation(self):
        from quorune import activation_paid_characteristic_model

        read = activation_paid_characteristic_model.resolve_activation_paid_creature_characteristics
        with patch.object(activation_paid_characteristic_model, "resolve_activation_paid_creature_characteristics",
                          side_effect=lambda item: replace(read(item), supertypes=())):
            game, _, _, _, _ = self.scene(legendary=True)
            self.assertIsNone(self.resolve(game))
            self.assertEqual(39, game.state.players["C"].life)
            with self.assertRaises(AssertionError):
                self.assertEqual(1, len(self.armies(game)))

    def test_verified_legendary_owner_withdrawal_preserves_subtype_activation(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Paid legendary Amass authority withdrawn"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
                               discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        for name in (NAME, "Falkenrath Aristocrat", "Falkenrath Torturer"):
            result, _ = register_card(self.db, registry, name)
            self.assertEqual(name != NAME, result["strict_capability_ready"], result["blockers"])

"""CR 701.60: paid designation, rather than a keyword, selects one draw result."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.activation_paid_characteristic_model import (
    ACTIVATION_PAID_SUBTYPE_CONTEXT, ActivationPaidCreatureCharacteristics,
)
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.paid_sacrifice_conditions import ACTIVATION_PAID_SUSPECTED_DRAW_CAPABILITY as CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Agency Coroner"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class ActivationPaidSuspectedDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "activation-paid-suspected.sqlite3"
        build_fixture_database([basic, FIXTURES / "activation-paid-suspected-draw-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Paid suspected creature", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        return card, program

    def test_complete_original_uses_another_creature_and_exactly_one_draw_branch(self):
        card, program = self.program()
        self.assertIsNone(program.target_schema)
        self.assertIn(CAPABILITY, program.capability_dependencies)
        self.assertEqual({"B": 1, "GENERIC": 2}, {k: v for k, v in program.cost_schema["mana"].items() if v})
        self.assertEqual(1, program.cost_schema["choices"][0]["other"])
        effect, = program.effects
        self.assertEqual((3, "suspected"), (effect["cast_fact"]["schema_version"], effect["condition"]["trait"]))
        self.assertEqual(({"op": "draw", "player": "$controller", "count": 2, "private": True},), tuple(effect["effects"]))
        self.assertEqual(({"op": "draw", "player": "$controller", "count": 1, "private": True},), tuple(effect["otherwise_effects"]))
        for text in (
            card.oracle_text.replace("Sacrifice another", "Sacrifice a"),
            card.oracle_text.replace("was suspected", "was a Human"),
            card.oracle_text.replace("cards instead", "cards"),
            card.oracle_text.replace("draw two", "draw three"),
        ):
            with self.subTest(text=text):
                value = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry,
                                            capability_profile=PROFILE)
                self.assertFalse(any(CAPABILITY in n.capability_dependencies for f in value.faces for n in f.nodes))

    def test_raw_result_rejects_legacy_reference_wrong_amount_or_changed_price(self):
        _, program = self.program()
        effect, = program.effects
        child, = effect["effects"]
        for changed in (
            replace(program, handlers=()),
            replace(program, cost_schema={**program.cost_schema, "mana": {**program.cost_schema["mana"], "GENERIC": 1}}),
            replace(program, effects=({**effect, "cast_fact": {**effect["cast_fact"], "schema_version": 2}},)),
            replace(program, effects=({**effect, "effects": [{**child, "count": 3}]},)),
            replace(program, effects=({**effect, "cast_fact": True},)),
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

    def pay(self, game, source, paid, suspected):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")]
        command = {"action_id": action["id"], "cost_objects": [paid.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertFalse(game.act("pilot:A", {**command, "cost_objects": [source.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[paid.object_id].zone)
        self.assertIsNone(game.state.cards[paid.object_id].suspected_at)
        fact = game.state.stack[-1].context[ACTIVATION_PAID_SUBTYPE_CONTEXT]
        self.assertEqual((2, suspected), (fact["schema_version"], fact["suspected"]))
        with self.assertRaises(ValueError):
            ActivationPaidCreatureCharacteristics.from_dict({**fact, "suspected": int(suspected)})
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))

    def test_actual_suspected_creature_still_draws_two_after_ability_loss_and_source_bounce(self):
        game = self.session(701600311)
        source = self.add(game.engine, NAME)
        person = self.add(game.engine, "Person of Interest", zone="hand")
        frog = self.add(game.engine, "Turn to Frog", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="coroner-private")
        for card in (source, person, frog):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "R": 1, "U": 1, "B": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", person, reserve={"C": 2, "U": 1, "B": 1})
        self.assertIsNone(self.resolve(game))
        body = game.state.cards[person.object_id]
        self.assertIsInstance(body.suspected_at, int)
        self.assertIn("menace", game.engine._combat_keywords(body))
        self.cast_for(game, "A", frog, [body.ref], reserve={"C": 2, "B": 1})
        self.assertIsNone(self.resolve(game))
        body = game.state.cards[person.object_id]
        self.assertIsInstance(body.suspected_at, int)
        self.assertIn("Frog", game.engine._effective_card_data(body)["type_line"])
        self.assertNotIn("menace", game.engine._combat_keywords(body))
        remaining_mana = game.state.players["A"].mana_pool
        self.assertEqual({"C": 2, "B": 1}, {color: amount for color, amount in remaining_mana.items() if amount})
        self.pay(game, source, body, True)
        self.cast_for(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        before = set(game.state.players["A"].zones["hand"])
        self.assertIsNone(self.resolve(game))
        draws = set(game.state.players["A"].zones["hand"]) - before
        self.assertEqual(2, len(draws))
        for viewer in ("pilot:B", "pilot:D", "spectator"):
            packet = json.dumps(game.packet(viewer, full=True))
            self.assertNotIn(private.ref, packet)
            for oid in draws:
                self.assertNotIn(game.state.cards[oid].ref, packet)
        self.replay(game, load=True)

    def test_actual_ordinary_menace_payment_selects_the_one_card_result(self):
        game = self.session(701600312)
        source = self.add(game.engine, NAME)
        menace = self.add(game.engine, "Alley Strangler")
        self.assert_closed(game, source)
        self.assert_closed(game, menace)
        self.prepare(game, mana={"C": 2, "B": 1})
        self.checkpoint(game)
        self.assertIsNone(menace.suspected_at)
        self.assertIn("menace", game.engine._combat_keywords(menace))
        self.pay(game, source, menace, False)
        before = len(game.state.players["A"].zones["hand"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before + 1, len(game.state.players["A"].zones["hand"]))
        self.replay(game)

    def test_native_suspected_witness_kills_default_false_mutation(self):
        from quorune import activation_paid_characteristic_model

        with patch.object(activation_paid_characteristic_model, "resolve_activation_paid_creature_suspected",
                          return_value=False):
            with self.assertRaisesRegex(AssertionError, "2 != 1"):
                self.test_actual_suspected_creature_still_draws_two_after_ability_loss_and_source_bounce()

    def test_verified_withdrawal_keeps_other_paid_characteristic_programs(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Paid suspected branch authority withdrawn"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
                               discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        for name in (NAME, "Falkenrath Torturer", "Gr\u00edma Wormtongue", "Person of Interest"):
            result, _ = register_card(self.db, registry, name)
            self.assertEqual(name != NAME, result["strict_capability_ready"], result["blockers"])

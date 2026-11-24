"""Pinned CR 608.2h and 701.21a: inspect the creature that actually paid."""

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
from quorune.paid_sacrifice_conditions import ACTIVATION_PAID_SUBTYPE_CAPABILITY as CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAMES = ("Falkenrath Aristocrat", "Falkenrath Torturer")
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class ActivationPaidCreatureSubtypeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        basic = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        basic["cards"] = [row for row in basic["cards"] if row["name"] == "Generic Bound Plains"]
        basic["rulings"] = []
        base = folder / "base.json"
        base.write_text(json.dumps(basic), encoding="utf-8")
        path = folder / "activation-paid-creature-subtype.sqlite3"
        build_fixture_database([base, FIXTURES / "activation-paid-creature-subtype-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Paid creature subtype", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original(self, name):
        card = self.db.lookup(name, fuzzy=False)
        admission, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(admission["strict_capability_ready"], admission["blockers"])
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id) if p.event == "activate")
        return card, program

    def test_complete_originals_keep_exact_price_source_grant_and_paid_trait(self):
        for name, keyword in zip(NAMES, ("Indestructible", "Flying"), strict=True):
            with self.subTest(name=name):
                card, program = self.original(name)
                self.assertIsNone(program.target_schema)
                self.assertEqual(1, len(program.cost_schema["choices"]))
                self.assertIn(CAPABILITY, program.capability_dependencies)
                grant, conditional = program.effects
                self.assertEqual(("$source.zone_object", [keyword]), (grant["card"], grant["keywords"]))
                self.assertEqual((2, "human"), (conditional["cast_fact"]["schema_version"], conditional["condition"]["trait"]))
                self.assertEqual(("$source.zone_object", "+1/+1", 1),
                    tuple(conditional["effects"][0][field] for field in ("card", "counter", "amount")))
                self.assertEqual("exact", compile_oracle_card(
                    card, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)
        card, _ = self.original(NAMES[0])
        for text in (
            card.oracle_text.replace("Sacrifice a creature:", "Sacrifice another creature:"),
            card.oracle_text.replace("a Human", "legendary"),
            card.oracle_text.replace("+1/+1 counter", "+1/+0 counter"),
            card.oracle_text + " Rewrite history.",
        ):
            with self.subTest(text=text):
                result = compile_oracle_card(replace(card, oracle_text=text),
                                             capability_registry=self.registry, capability_profile=PROFILE)
                self.assertFalse(any(CAPABILITY in n.capability_dependencies for f in result.faces for n in f.nodes))

    def test_raw_paid_subtype_cannot_change_price_origin_or_source_binding(self):
        _, program = self.original(NAMES[0])
        prefix, condition = program.effects
        child, = condition["effects"]
        for changed in (
            replace(program, event="resolve", handlers=()),
            replace(program, active_zone="graveyard", handlers=()),
            replace(program, effects=({**prefix, "card": "$target.0"}, condition)),
            replace(program, effects=(prefix, {**condition, "cast_fact": True})),
            replace(program, effects=(prefix, {**condition, "cast_fact": {**condition["cast_fact"], "schema_version": 1}})),
            replace(program, effects=(prefix, {**condition, "effects": [{**child, "amount": True}]})),
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

    def pay(self, game, source, paid, *, invalid=None):
        self.priority(game, "A")
        current = game.state.cards[source.object_id]
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in options if row["id"].startswith(f"activate:{current.ref}:")]
        command = {"action_id": action["id"], "cost_objects": [paid.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        if invalid is not None:
            rejected = game.act("pilot:A", {**command, "cost_objects": [invalid.ref]})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_human_and_changeling_pay_and_apply_the_ordered_originals(self):
        for i, (name, body, keyword) in enumerate(zip(NAMES, ("Elite Vanguard", "Universal Automaton"),
                ("indestructible", "flying"), strict=True)):
            with self.subTest(name=name):
                game = self.session(602207301 + i)
                source = self.add(game.engine, name)
                paid = self.add(game.engine, body)
                foreign = self.add(game.engine, "Elite Vanguard", seat="B", ref="foreign-human")
                private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="sacrifice-private")
                if i:
                    replacement = self.add(game.engine, "Rest in Peace")
                    self.assert_closed(game, replacement)
                self.assert_closed(game, source)
                self.prepare(game)
                self.checkpoint(game)
                self.assertNotIn(keyword, game.engine._combat_keywords(source))
                self.pay(game, source, paid, invalid=foreign)
                self.assertEqual("exile" if i else "graveyard", game.state.cards[paid.object_id].zone)
                fact = game.state.stack[-1].context[ACTIVATION_PAID_SUBTYPE_CONTEXT]
                self.assertIn("human", fact["characteristics"]["subtypes"])
                self.assertEqual(paid.ref, fact["paid_ref"])
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                current = game.state.cards[source.object_id]
                self.assertIn(keyword, game.engine._combat_keywords(current))
                self.assertEqual(1, current.counters["+1/+1"])
                for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
                    self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
                self.replay(game, load=True)

    def test_actual_frog_payment_uses_last_battlefield_type_before_replay(self):
        game = self.session(602207303)
        source = self.add(game.engine, NAMES[0])
        paid = self.add(game.engine, "Elite Vanguard")
        frog = self.add(game.engine, "Turn to Frog", zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 1, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", frog, [paid.ref])
        self.assertIsNone(self.resolve(game))
        self.assertIn("Frog", game.engine._effective_card_data(game.state.cards[paid.object_id])["type_line"])
        self.pay(game, source, paid)
        self.assertEqual("graveyard", game.state.cards[paid.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[source.object_id]
        self.assertIn("indestructible", game.engine._combat_keywords(current))
        self.assertEqual(0, current.counters.get("+1/+1", 0))
        self.replay(game)

    def test_actual_self_payment_and_source_reentry_do_not_receive_old_effects(self):
        for self_paid in (False, True):
            with self.subTest(self_paid=self_paid):
                game = self.session(602207304 + self_paid)
                source = self.add(game.engine, NAMES[1])
                human = self.add(game.engine, "Elite Vanguard")
                blink = self.add(game.engine, "Cloudshift", zone="hand")
                self.assert_closed(game, source)
                self.prepare(game, mana={"W": 1})
                self.checkpoint(game)
                identity = source.logical_object_id
                self.pay(game, source, source if self_paid else human)
                if not self_paid:
                    self.cast_for(game, "A", blink, [source.ref])
                    self.resolve_top(game)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                current = game.state.cards[source.object_id]
                self.assertNotEqual(identity, current.logical_object_id)
                self.assertEqual("graveyard" if self_paid else "battlefield", current.zone)
                self.assertNotIn("flying", game.engine._combat_keywords(current))
                self.assertEqual(0, current.counters.get("+1/+1", 0))
                self.replay(game)

    def test_actual_frog_payment_witness_kills_printed_human_mutation(self):
        from quorune import activation_paid_characteristic_model

        read = activation_paid_characteristic_model.resolve_activation_paid_creature_characteristics

        def printed_human(item):
            return replace(read(item), subtypes=("human", "soldier"))

        with patch.object(activation_paid_characteristic_model, "resolve_activation_paid_creature_characteristics",
                          side_effect=printed_human):
            with self.assertRaises(AssertionError):
                self.test_actual_frog_payment_uses_last_battlefield_type_before_replay()

    def test_verified_withdrawal_retains_independent_paid_statistics(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Paid activation subtype authority withdrawn"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
                               discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        for name in (*NAMES, "Greater Good", "Elite Vanguard"):
            result, _ = register_card(self.db, registry, name)
            self.assertEqual(name not in NAMES, result["strict_capability_ready"], result["blockers"])

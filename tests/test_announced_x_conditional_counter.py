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
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_alternative_costs import ANNOUNCED_X_PITCH_PRICE_VERSION, FixedPublicAlternativeCostSpec
from quorune.query_effect_amount_model import CastXAmountSpec, DECLARED_AMOUNT_CAPABILITY
from quorune.stack_counter import ANNOUNCED_X_COUNTER_FIELD
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
COUNTER = "stack.counter.effect"
NAME = "Disrupting Shoal"


class AnnouncedXConditionalCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Unsummon"},
            "ability-source-counter-cards.json": {"Sol Ring", "Twincast"},
            "activation-result-views-cards.json": {"Control Magic"},
            "additional-conditional-modifier-cards.json": {"Revitalize"},
            "announced-x-pitch-alternative-cards.json": {NAME, "Nourishing Shoal"},
            "bounded-hand-cast-cards.json": {"Ancestral Vision"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "targeted-spell-control-cards.json": {"Scornful Egotist"},
        }
        pieces = []
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names, (filename, names)
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            pieces.append(piece)
        database = folder / "announced-x-conditional-counter.sqlite3"
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Resolution mana-value counter", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        outcome, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(outcome["strict_capability_ready"], outcome)
        return card, {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)}

    def test_complete_disrupting_binds_announced_x_only_at_resolution_and_keeps_pitch(self):
        card, programs = self.original_program()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual({"cast.cost", "resolve"}, programs.keys())
        price = FixedPublicAlternativeCostSpec.from_dict(thaw_value(programs["cast.cost"].handlers[0]["alternative_cost"]))
        self.assertEqual(ANNOUNCED_X_PITCH_PRICE_VERSION, price.schema_version)
        counter = programs["resolve"]
        self.assertEqual([{"op": "counter_stack_target", "stack": "$target.0",
                           ANNOUNCED_X_COUNTER_FIELD: CastXAmountSpec().to_dict()}], thaw_value(counter.effects))
        self.assertEqual({"zones": ["stack"], "categories": ["spell"], "count": 1, "source_exclusion": True},
                         thaw_value(counter.target_schema))
        self.assertLessEqual({DECLARED_AMOUNT_CAPABILITY, COUNTER, "target.revalidate_resolution"},
                             set(counter.capability_dependencies))

    def test_disrupting_condition_rejects_widened_effect_and_target_predicates(self):
        _, programs = self.original_program()
        counter = programs["resolve"]
        effect, = thaw_value(counter.effects)
        schema = thaw_value(counter.target_schema)
        for parts in (
            {"effects": [{**effect, ANNOUNCED_X_COUNTER_FIELD: CastXAmountSpec(coefficient=-1).to_dict()}]},
            {"effects": [{**effect, ANNOUNCED_X_COUNTER_FIELD: 1}]},
            {"effects": [{**effect, "stack": "$source"}]},
            {"target_schema": {**schema, "mana_value": 1}},
            {"target_schema": {**schema, "categories": ["spell", "ability"]}},
        ):
            with self.subTest(parts=parts):
                binding = bind_semantic_program_runtime(replace(counter, **parts),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(binding["strict"], binding)

        caps = tuple(cap for cap in counter.capability_dependencies if cap != DECLARED_AMOUNT_CAPABILITY)
        closure = self.registry.closure(caps, profile=PROFILE).to_dict()
        self.assertTrue(closure["trusted"], closure["blockers"])
        stripped = replace(counter, capability_dependencies=caps, capability_closure=closure,
                           provenance={**counter.provenance, "capability_closure_fingerprint": closure["fingerprint"]})
        binding = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + DECLARED_AMOUNT_CAPABILITY, binding["blockers"])

    def _spell(self, game, card):
        item, = [item for item in game.state.stack if item.card_object_id == card.object_id]
        return item

    def _pitch(self, game, source, payment, target, x):
        self.priority(game, "A")
        action = self.action(game, "A", source)
        option, = [row for row in action["cost_options"] if row["kind"] == "alternate"
                   and row["choice_schema"]["x"]["default"] == x]
        self.assertIn(target, json.dumps(action["target_schema"]))
        cast = {"action_id": action["id"], "cost_option": option["id"], "x": x,
                "exile_cards": [payment.ref], "targets": [target], "pay": "auto"}
        initial = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", cast).ok)
        self.assertFalse(game.act("pilot:A", {**cast, "targets": ["D"]}).ok)
        self.assertEqual(initial, authoritative_state_hash(game.state))
        result = game.act("pilot:A", cast)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(x, self._spell(game, source).x_value)
        self.assertEqual(0, self._spell(game, source).context["mana_spent_total"])
        self.assertEqual("exile", game.state.cards[payment.object_id].zone)

    def test_actual_disrupting_accepts_a_mismatch_and_only_counters_the_equal_value_spell(self):
        for matched in (False, True):
            with self.subTest(matched=matched):
                game = self.session(77128001 + matched)
                source = self.add(game.engine, NAME, zone="hand")
                payment = self.add(game.engine, "Unsummon", zone="hand", ref="disrupting-one-blue")
                target = self.add(game.engine, "Sol Ring" if matched else "Revitalize", seat="B", zone="hand")
                for card in (source, payment, target):
                    self.assert_closed(game, card)
                self.prepare(game, active="B", actor="B", mana={"C": 1, "W": 1})
                self.checkpoint(game)
                self.cast(game, "B", target)
                ref = self._spell(game, target).ref
                self._pitch(game, source, payment, ref, 1)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual(not matched, any(item.ref == ref for item in game.state.stack))
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
                self.assertEqual(40 if matched else 43, game.state.players["B"].life)
                self.replay(game, load=True)

    def test_actual_disrupting_zero_compares_a_face_down_spells_public_zero_value(self):
        game = self.session(77128003)
        source = self.add(game.engine, NAME, zone="hand")
        payment = self.add(game.engine, "Ancestral Vision", zone="hand")
        hidden = self.add(game.engine, "Scornful Egotist", seat="B", zone="hand")
        for card in (source, payment, hidden):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"C": 3})
        self.checkpoint(game)
        cast = game.act("pilot:B", {"action_id": "cast-morph:" + hidden.ref, "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertNotIn("Scornful Egotist", json.dumps(game.packet("pilot:A", full=True)))
        self._pitch(game, source, payment, self._spell(game, hidden).ref, 0)
        self.assertNotIn("Scornful Egotist", json.dumps(game.packet("pilot:C", full=True)))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[hidden.object_id].zone)
        self.replay(game, load=True)

    def test_actual_disrupting_counter_uses_the_copied_spells_announced_x(self):
        game = self.session(77128004)
        source = self.add(game.engine, NAME, zone="hand")
        payment = self.add(game.engine, "Control Magic", zone="hand")
        original = self.add(game.engine, "Nourishing Shoal", seat="B", zone="hand")
        copy = self.add(game.engine, "Twincast", seat="C", zone="hand")
        for card in (source, original, copy):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"C": 2, "G": 2})
        game.state.players["C"].mana_pool["U"] = 2
        self.checkpoint(game)
        action = self.action(game, "B", original)
        normal, = [row for row in action["cost_options"] if row["kind"] == "mana"]
        cast = game.act("pilot:B", {"action_id": action["id"], "cost_option": normal["id"], "x": 2, "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        original_ref = self._spell(game, original).ref
        self.cast(game, "C", copy, [original_ref])
        self.resolve_top(game)
        copied, = [item for item in game.state.stack if item.kind == "spell_copy"]
        self.assertEqual((2, "C"), (copied.x_value, copied.controller))
        self._pitch(game, source, payment, copied.ref, 4)
        self.resolve_top(game)
        self.assertTrue(any(item.ref == original_ref for item in game.state.stack))
        self.assertFalse(any(item.ref == copied.ref for item in game.state.stack))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((42, 40), (game.state.players["B"].life, game.state.players["C"].life))
        self.replay(game, load=True)

    def test_omission_of_the_exact_x_counter_leaves_disrupting_partial(self):
        with patch("quorune.stack_counter.announced_x_counter_capabilities", return_value=()):
            result, _ = register_card(self.db, self.registry, NAME)
            self.assertFalse(result["strict_capability_ready"], result)
            unaffected, _ = register_card(self.db, self.registry, "Revitalize")
            self.assertTrue(unaffected["strict_capability_ready"], unaffected["blockers"])

    def test_genuine_withdrawals_require_declared_x_and_counter_owners_for_disrupting(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        original = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (DECLARED_AMOUNT_CAPABILITY, COUNTER):
            with self.subTest(capability=capability):
                document = deepcopy(original)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Announced X counter authority withdrawn"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                result, _ = register_card(self.db, registry, NAME)
                self.assertFalse(result["strict_capability_ready"], result)
                unaffected, _ = register_card(self.db, registry, "Revitalize")
                self.assertTrue(unaffected["strict_capability_ready"], unaffected["blockers"])

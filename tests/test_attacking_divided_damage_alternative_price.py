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
from quorune.public_alternative_costs import (
    FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY,
    FixedPublicAlternativeCondition,
    FixedPublicAlternativeCostError,
    FixedPublicAlternativeCostSpec,
)
from quorune.announced_damage import ANNOUNCED_DAMAGE_CAPABILITY, DAMAGE_ALLOCATION_FIELD
from quorune.live_battlefield_quantity_model import LIVE_BATTLEFIELD_QUANTITY_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.util import mana_cost_to_vector
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
NAME = "Arrow Volley Trap"


class AttackingDividedDamageAlternativePriceTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Island", "Plains"},
            "ability-source-counter-cards.json": {"Grizzly Bears", "Shock"},
            "activation-restriction-material-cards.json": {"Memnite"},
            "activation-result-views-cards.json": {"Mountain"},
            "additional-conditional-modifier-cards.json": {"Revitalize"},
            "additional-continuous-carriers-cards.json": {"Hill Giant"},
            "any-number-target-cards.json": {"Hail of Arrows"},
            "attacking-divided-damage-price-cards.json": {NAME},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        pieces = []
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            pieces.append(piece)
        database = folder / "attacking-divided-damage-alternative-price.sqlite3"
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Attacking combat division", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        outcome, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(outcome["strict_capability_ready"], outcome)
        return card, {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)}

    def test_original_binds_four_attacker_price_and_fixed_attacking_split(self):
        card, programs = self.original_program()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual({"cast.cost", "resolve"}, programs.keys())
        price = programs["cast.cost"]
        raw = thaw_value(price.handlers[0]["alternative_cost"])
        spec = FixedPublicAlternativeCostSpec.from_dict(raw)
        self.assertEqual((5, "at_least_four_attacking_creatures", "{1}{W}"),
                         (spec.condition.schema_version, spec.condition.kind.value, spec.cost_text))
        self.assertEqual(raw, spec.to_dict())
        self.assertLessEqual({FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY, LIVE_BATTLEFIELD_QUANTITY_CAPABILITY},
                             set(price.capability_dependencies))
        outcome = programs["resolve"]
        self.assertIn(ANNOUNCED_DAMAGE_CAPABILITY, outcome.capability_dependencies)
        effect, = outcome.effects
        self.assertEqual(("damage_fixed_set", 5), (effect["op"], effect["amount"]))
        schema = thaw_value(outcome.target_schema)
        self.assertEqual({"schema_version": 1, "total": 5}, schema[DAMAGE_ALLOCATION_FIELD])
        group, = schema["groups"]
        self.assertEqual((0, 5, "attacking", ["creature"]),
                         (group["min"], group["max"], group["combat_state"], group["types_any"]))

    def test_arrow_price_rejects_widened_condition_and_unverified_live_quantity(self):
        _, programs = self.original_program()
        price = programs["cast.cost"]
        raw = thaw_value(price.handlers[0]["alternative_cost"])
        condition = raw["condition"]
        for value in ({"schema_version": 4}, {"schema_version": 5.0},
                      {"kind": "always"}, {"threshold": 3}, {"basic_land_type": "island"}):
            with self.subTest(value=value), self.assertRaises(FixedPublicAlternativeCostError):
                FixedPublicAlternativeCondition.from_dict({**condition, **value})
        vector, _ = mana_cost_to_vector("{W}")
        with self.assertRaises(FixedPublicAlternativeCostError):
            FixedPublicAlternativeCostSpec.from_dict({**raw, "cost_text": "{W}", "mana_requirements": vector})
        caps = tuple(cap for cap in price.capability_dependencies if cap != LIVE_BATTLEFIELD_QUANTITY_CAPABILITY)
        closure = self.registry.closure(caps, profile=PROFILE).to_dict()
        self.assertTrue(closure["trusted"], closure["blockers"])
        stripped = replace(price, capability_dependencies=caps, capability_closure=closure,
                           provenance={**price.provenance, "capability_closure_fingerprint": closure["fingerprint"]})
        result = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + LIVE_BATTLEFIELD_QUANTITY_CAPABILITY,
                      result["blockers"])

    def _quote(self, game, trap):
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in actions if row["id"] == "cast:" + trap.ref)

    def _price(self, action, kind, expected):
        option, = [price for price in action["cost_options"] if price["kind"] == kind]
        self.assertEqual(expected, {key: count for key, count in option["requirements"].items() if count})
        return {"action_id": action["id"], "cost_option": option["id"], "pay": "auto"}

    def _reject(self, game, command, seat="B"):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _attackers(self, game):
        attackers = [self.add(game.engine, name, ref="arrow-attacker-" + str(index))
                     for index, name in enumerate(("Memnite", "Hill Giant", "Grizzly Bears", "Grizzly Bears"))]
        for card in attackers:
            self.assert_closed(game, card)
            card.acquired_control_turn_count = -1
        return attackers

    def test_actual_arrow_division_stays_in_its_announced_slots_after_a_target_dies(self):
        game = self.session(77126001)
        trap = self.add(game.engine, NAME, seat="B", zone="hand")
        small, giant, first_bear, second_bear = attackers = self._attackers(game)
        idle = self.add(game.engine, "Hill Giant", ref="arrow-nonattacker")
        shock = self.add(game.engine, "Shock", seat="C", zone="hand")
        private = self.add(game.engine, "Memnite", seat="D", zone="hand", ref="arrow-private-hand")
        sources = [
            self.add(game.engine, "Plains", seat="B"),
            self.add(game.engine, "Island", seat="B"),
            self.add(game.engine, "Mountain", seat="C"),
        ]
        for card in (trap, idle, shock, *sources):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, list(zip(attackers, "CDDB")))
        action = self._quote(game, trap)
        cast = self._price(action, "alternate", {"GENERIC": 1, "W": 1})
        valid = {**cast, "targets": [small.ref, giant.ref],
                 DAMAGE_ALLOCATION_FIELD: {small.ref: 3, giant.ref: 2}}
        self._reject(game, valid, seat="A")
        for invalid in (
            {**valid, DAMAGE_ALLOCATION_FIELD: {small.ref: 0, giant.ref: 5}},
            {**valid, DAMAGE_ALLOCATION_FIELD: {small.ref: 2, giant.ref: 2}},
            {**valid, "targets": [small.ref, small.ref], DAMAGE_ALLOCATION_FIELD: {small.ref: 5}},
            {**valid, "targets": [small.ref, idle.ref], DAMAGE_ALLOCATION_FIELD: {small.ref: 3, idle.ref: 2}},
        ):
            self._reject(game, invalid)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:B", full=True)))
        accepted = game.act("pilot:B", valid)
        self.assertTrue(accepted.ok, accepted.summary)
        game = self.replay(game, load=True)
        self.cast(game, "C", shock, [small.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[small.object_id].zone)
        self.assertEqual(("battlefield", 2), (game.state.cards[giant.object_id].zone, game.state.cards[giant.object_id].marked_damage))
        self.assertEqual([0, 0, 0], [game.state.cards[card.object_id].marked_damage for card in (first_bear, second_bear, idle)])
        self.assertTrue(all(game.state.cards[card.object_id].tapped for card in sources))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.replay(game, load=True)

    def test_actual_arrow_accepts_zero_targets_at_both_printed_and_four_attacker_prices(self):
        for count in (3, 4):
            with self.subTest(attackers=count):
                game = self.session(77126002 + count)
                trap = self.add(game.engine, NAME, seat="B", zone="hand")
                attackers = self._attackers(game)
                plains = [self.add(game.engine, "Plains", seat="B", ref="arrow-funded-" + str(i)) for i in range(5)]
                for card in (trap, *plains):
                    self.assert_closed(game, card)
                self.prepare(game)
                self.checkpoint(game)
                self.attack(game, list(zip(attackers[:count], "BCDD")))
                action = self._quote(game, trap)
                printed = self._price(action, "mana", {"GENERIC": 3, "W": 2})
                if count == 3:
                    self.assertNotIn("alternate", {price["kind"] for price in action["cost_options"]})
                    self._reject(game, {**printed, "cost_option": "plain-alternative-ab1", "targets": []})
                    cast = printed
                else:
                    cast = self._price(action, "alternate", {"GENERIC": 1, "W": 1})
                self._reject(game, {**cast, "targets": [], DAMAGE_ALLOCATION_FIELD: {attackers[0].ref: 5}})
                result = game.act("pilot:B", {**cast, "targets": [], DAMAGE_ALLOCATION_FIELD: {}})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard", game.state.cards[trap.object_id].zone)
                self.assertEqual([0] * 4, [game.state.cards[card.object_id].marked_damage for card in attackers])
                self.assertEqual(5 if count == 3 else 2, sum(game.state.cards[card.object_id].tapped for card in plains))
                self.replay(game, load=True)

    def test_omission_of_either_arrow_component_restores_whole_card_residual(self):
        from quorune.compiler import announced_damage_templates as templates

        retained_scope = {key: value for key, value in templates._DIRECT_RECIPIENTS.items()
                          if key != "target attacking creatures"}
        for change in (
            patch("quorune.compiler.fixed_public_alternative_costs._live_attacking_price", return_value=None),
            patch.dict(templates._DIRECT_RECIPIENTS, retained_scope, clear=True),
        ):
            with change:
                outcome, _ = register_card(self.db, self.registry, NAME)
                self.assertFalse(outcome["strict_capability_ready"], outcome)
                retained, _ = register_card(self.db, self.registry, "Hail of Arrows")
                self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

    def test_genuine_withdrawals_require_live_price_and_announced_arrow_damage_owners(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        original = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY, LIVE_BATTLEFIELD_QUANTITY_CAPABILITY,
                           ANNOUNCED_DAMAGE_CAPABILITY):
            with self.subTest(capability=capability):
                document = deepcopy(original)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Arrow Volley typed authority withdrawn"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                outcome, _ = register_card(self.db, registry, NAME)
                self.assertFalse(outcome["strict_capability_ready"], outcome)
                retained, _ = register_card(self.db, registry, "Revitalize")
                self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

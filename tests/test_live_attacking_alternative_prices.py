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
    fixed_public_alternative_condition_met,
)
from quorune.live_battlefield_quantity_model import LIVE_BATTLEFIELD_QUANTITY_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.util import mana_cost_to_vector
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
PRICES = {
    "Pitfall Trap": ("exactly_one_attacking_creature", "{W}", "destroy"),
    "Lethargy Trap": ("at_least_three_attacking_creatures", "{U}", "modify_all_matching_permanents_until_end_of_turn"),
}


class LiveAttackingAlternativePriceTests(InheritedNativeFixture, unittest.TestCase):
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
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "live-attacking-alternative-price-cards.json": {"Flying Men", *PRICES},
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
        database = folder / "live-attacking-alternative-prices.sqlite3"
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current combat prices", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_programs(self, name):
        card = self.db.lookup(name, fuzzy=False)
        outcome, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(outcome["strict_capability_ready"], outcome)
        return card, {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)}

    def test_both_live_attacking_prices_bind_exact_counts_and_preserve_their_results(self):
        for name, (condition, cost, operation) in PRICES.items():
            with self.subTest(card=name):
                card, programs = self.original_programs(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual({"cast.cost", "resolve"}, programs.keys())
                price = programs["cast.cost"]
                raw = thaw_value(price.handlers[0]["alternative_cost"])
                spec = FixedPublicAlternativeCostSpec.from_dict(raw)
                self.assertEqual((5, condition, cost),
                                 (spec.condition.schema_version, spec.condition.kind.value, spec.cost_text))
                self.assertEqual(raw, spec.to_dict())
                dependencies = {FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY, LIVE_BATTLEFIELD_QUANTITY_CAPABILITY}
                self.assertLessEqual(dependencies, set(price.capability_dependencies))
                self.assertEqual([operation], [effect["op"] for effect in programs["resolve"].effects])

    def test_live_attacking_prices_reject_widened_specs_and_independently_require_the_count_owner(self):
        for name in PRICES:
            with self.subTest(card=name):
                _, programs = self.original_programs(name)
                price = programs["cast.cost"]
                raw = thaw_value(price.handlers[0]["alternative_cost"])
                condition = raw["condition"]
                for changes in ({"schema_version": 1}, {"schema_version": 4}, {"schema_version": 5.0},
                                {"kind": "always"}, {"threshold": 2}, {"basic_land_type": "island"}):
                    with self.subTest(changes=changes), self.assertRaises(FixedPublicAlternativeCostError):
                        FixedPublicAlternativeCondition.from_dict({**condition, **changes})
                requirements, _ = mana_cost_to_vector("{C}")
                with self.assertRaises(FixedPublicAlternativeCostError):
                    FixedPublicAlternativeCostSpec.from_dict({**raw, "cost_text": "{C}", "mana_requirements": requirements})
                retained = tuple(cap for cap in price.capability_dependencies if cap != LIVE_BATTLEFIELD_QUANTITY_CAPABILITY)
                closure = self.registry.closure(retained, profile=PROFILE).to_dict()
                self.assertTrue(closure["trusted"], closure["blockers"])
                stripped = replace(price, capability_dependencies=retained, capability_closure=closure,
                                   provenance={**price.provenance, "capability_closure_fingerprint": closure["fingerprint"]})
                outcome = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile=PROFILE)
                self.assertIn("capability:undeclared_runtime_dependency:" + LIVE_BATTLEFIELD_QUANTITY_CAPABILITY,
                              outcome["blockers"])

    def _quote(self, game, trap):
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next((row for row in actions if row["id"] == "cast:" + trap.ref), None)

    def _price(self, game, trap, mana):
        action = self._quote(game, trap)
        self.assertIsNotNone(action)
        choice, = [price for price in action["cost_options"] if price["kind"] == "alternate"]
        self.assertEqual(mana, {key: count for key, count in choice["requirements"].items() if count})
        return {"action_id": action["id"], "cost_option": choice["id"], "pay": "auto"}

    def _reject(self, game, command):
        before = authoritative_state_hash(game.state)
        invalid = game.act("pilot:B", command)
        self.assertFalse(invalid.ok, invalid.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _no_price(self, game, trap, targets=()):
        action = self._quote(game, trap)
        self.assertNotIn("alternate", {row["kind"] for row in (action or {}).get("cost_options", ())})
        self._reject(game, {"action_id": "cast:" + trap.ref, "cost_option": "plain-alternative-ab1",
                            "targets": list(targets), "pay": "auto"})

    def test_actual_pitfall_with_only_one_attacker_counts_other_defenders_and_keeps_flying_illegal(self):
        _, programs = self.original_programs("Pitfall Trap")
        condition = thaw_value(programs["cast.cost"].handlers[0]["alternative_cost"]["condition"])
        for flying in (False, True):
            with self.subTest(flying=flying):
                game = self.session(77124001 + flying)
                trap = self.add(game.engine, "Pitfall Trap", seat="B", zone="hand")
                ground = self.add(game.engine, "Grizzly Bears")
                flyer = self.add(game.engine, "Flying Men")
                land = self.add(game.engine, "Plains", seat="B")
                for card in (trap, ground, flyer, land):
                    self.assert_closed(game, card)
                for card in (ground, flyer):
                    card.acquired_control_turn_count = -1
                self.prepare(game)
                self.checkpoint(game)
                self.assertFalse(fixed_public_alternative_condition_met(game.engine, "B", trap, condition))
                self._no_price(game, trap, [ground.ref])
                attacking = flyer if flying else ground
                self.attack(game, [(attacking, "D")])
                self.assertTrue(fixed_public_alternative_condition_met(game.engine, "B", trap, condition))
                if flying:
                    self._no_price(game, trap, [flyer.ref])
                    self.assertEqual("battlefield", game.state.cards[flyer.object_id].zone)
                else:
                    cast = self._price(game, trap, {"W": 1})
                    self._reject(game, {**cast, "targets": [flyer.ref]})
                    accepted = game.act("pilot:B", {**cast, "targets": [ground.ref]})
                    self.assertTrue(accepted.ok, accepted.summary)
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual("graveyard", game.state.cards[ground.object_id].zone)
                    self.assertTrue(game.state.cards[land.object_id].tapped)
                self.replay(game, load=True)

    def test_actual_lethargy_uses_current_attackers_and_keeps_the_paid_price_after_an_attacker_dies(self):
        for remove_first in (True, False):
            with self.subTest(remove_first=remove_first):
                game = self.session(77124004 + remove_first)
                trap = self.add(game.engine, "Lethargy Trap", seat="B", zone="hand")
                bears = self.add(game.engine, "Grizzly Bears", ref="lethargy-first-attacker")
                giant = self.add(game.engine, "Hill Giant")
                small = self.add(game.engine, "Memnite", ref="lethargy-removed-attacker")
                idle = self.add(game.engine, "Grizzly Bears", ref="lethargy-nonattacker")
                land = self.add(game.engine, "Island", seat="B")
                fuel = self.add(game.engine, "Mountain", seat="C")
                shock = self.add(game.engine, "Shock", seat="C", zone="hand")
                private = self.add(game.engine, "Memnite", seat="D", zone="hand", ref="lethargy-private")
                for card in (trap, bears, giant, small, idle, land, fuel, shock):
                    self.assert_closed(game, card)
                for card in (bears, giant, small):
                    card.acquired_control_turn_count = -1
                self.prepare(game)
                self.checkpoint(game)
                self.attack(game, [(bears, "B"), (giant, "C"), (small, "D")])
                if not remove_first:
                    cast = self._price(game, trap, {"U": 1})
                    self.assertNotIn(private.ref, json.dumps(game.packet("pilot:B", full=True)))
                    accepted = game.act("pilot:B", cast)
                    self.assertTrue(accepted.ok, accepted.summary)
                self.cast(game, "C", shock, [small.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard", game.state.cards[small.object_id].zone)
                if remove_first:
                    self._no_price(game, trap)
                    self.assertFalse(game.state.cards[land.object_id].tapped)
                else:
                    self.assertTrue(game.state.cards[land.object_id].tapped)
                self.assertEqual((2, 3, 2) if remove_first else (-1, 0, 2),
                                 tuple(game.engine._numeric_stat(card.object_id, "power") for card in (bears, giant, idle)))
                self.replay(game, load=True)

    def test_omission_restores_both_live_attacking_price_residuals(self):
        with patch("quorune.compiler.fixed_public_alternative_costs._live_attacking_price", return_value=None):
            for name in PRICES:
                outcome, _ = register_card(self.db, self.registry, name)
                self.assertFalse(outcome["strict_capability_ready"], outcome)
            retained, _ = register_card(self.db, self.registry, "Revitalize")
            self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

    def test_genuine_withdrawals_block_both_live_attacking_price_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        original = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY, LIVE_BATTLEFIELD_QUANTITY_CAPABILITY):
            with self.subTest(capability=capability):
                document = deepcopy(original)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Live attacking price authority withdrawn"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in PRICES:
                    outcome, _ = register_card(self.db, registry, name)
                    self.assertFalse(outcome["strict_capability_ready"], outcome)
                retained, _ = register_card(self.db, registry, "Revitalize")
                self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

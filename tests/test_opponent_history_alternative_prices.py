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
    FixedPublicAlternativeConditionKind,
    FixedPublicAlternativeCostError,
    FixedPublicAlternativeCostSpec,
)
from quorune.public_history_query_model import PUBLIC_HISTORY_QUERY_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.util import mana_cost_to_vector
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
PRICES = {
    "Ravenous Trap": ("opponent_graveyard_arrivals", "{0}"),
    "Runeflare Trap": ("opponent_drew_three", "{R}"),
    "Whiplash Trap": ("opponent_creature_arrivals", "{U}"),
}


class OpponentHistoryAlternativePriceTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-source-counter-cards.json": {"Shock"},
            "activation-restriction-material-cards.json": {"Memnite"},
            "additional-conditional-modifier-cards.json": {"Revitalize"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "empty-battlefield-condition-cards.json": {"Ashcoat Bear"},
            "graveyard-exile-replacement-cards.json": {"Millstone"},
            "opponent-history-alternative-price-cards.json": {"Ravenous Trap", "Runeflare Trap", "Whiplash Trap"},
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
        database = folder / "opponent-history-alternative-prices.sqlite3"
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Opponent turn-history prices", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_programs(self, name):
        card = self.db.lookup(name, fuzzy=False)
        outcome, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(outcome["strict_capability_ready"], outcome)
        return card, {p.event: p for p in semantics.programs_for_oracle(card.oracle_id)}

    def test_complete_traps_bind_exact_alternatives_and_independent_results(self):
        for name, expected in PRICES.items():
            with self.subTest(name=name):
                card, programs = self.original_programs(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual({"cast.cost", "resolve"}, programs.keys())
                price = programs["cast.cost"]
                value = thaw_value(price.handlers[0]["alternative_cost"])
                spec = FixedPublicAlternativeCostSpec.from_dict(value)
                self.assertEqual((4, *expected), (spec.condition.schema_version, spec.condition.kind.value, spec.cost_text))
                self.assertEqual(value, spec.to_dict())
                self.assertEqual(name != "Runeflare Trap", PUBLIC_HISTORY_QUERY_CAPABILITY in price.capability_dependencies)
                self.assertTrue(programs["resolve"].effects)

    def test_history_prices_reject_widened_conditions_costs_and_missing_runtime_authority(self):
        _, programs = self.original_programs("Whiplash Trap")
        price = programs["cast.cost"]
        raw = thaw_value(price.handlers[0]["alternative_cost"])
        condition = raw["condition"]
        for value in (
            {**condition, "schema_version": 1},
            {**condition, "schema_version": 4.0},
            {**condition, "kind": "always"},
            {**condition, "basic_land_type": "island"},
        ):
            with self.subTest(value=value), self.assertRaises(FixedPublicAlternativeCostError):
                FixedPublicAlternativeCondition.from_dict(value)
        requirements, _ = mana_cost_to_vector("{R}")
        with self.assertRaises(FixedPublicAlternativeCostError):
            FixedPublicAlternativeCostSpec.from_dict({**raw, "cost_text": "{R}", "mana_requirements": requirements})
        earlier = FixedPublicAlternativeCondition(FixedPublicAlternativeConditionKind.ALWAYS)
        self.assertEqual({"schema_version": 1, "kind": "always", "basic_land_type": None}, earlier.to_dict())

        retained = tuple(cap for cap in price.capability_dependencies if cap != PUBLIC_HISTORY_QUERY_CAPABILITY)
        closure = self.registry.closure(retained, profile=PROFILE).to_dict()
        self.assertTrue(closure["trusted"], closure["blockers"])
        stripped = replace(price, capability_dependencies=retained, capability_closure=closure,
                           provenance={**price.provenance, "capability_closure_fingerprint": closure["fingerprint"]})
        outcome = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + PUBLIC_HISTORY_QUERY_CAPABILITY, outcome["blockers"])

    @staticmethod
    def _fund(game, seat, mana):
        game.state.players[seat].mana_pool.update(mana)

    def _actions(self, game, seat):
        self.priority(game, seat)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def _no_alternate(self, game, card, targets):
        action_id = "cast:" + card.ref
        self.assertNotIn(action_id, {row["id"] for row in self._actions(game, "A")})
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": action_id, "targets": targets,
                                        "cost_option": "plain-alternative-ab1", "pay": "auto"})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _alternative(self, game, card, expected):
        action = next(row for row in self._actions(game, "A") if row["id"] == "cast:" + card.ref)
        option, = [row for row in action["cost_options"] if row["kind"] == "alternate"]
        self.assertEqual(expected, {key: amount for key, amount in option["requirements"].items() if amount})
        return action, option

    def _commit(self, game, action, option, targets):
        result = game.act("pilot:A", {"action_id": action["id"], "cost_option": option["id"],
                                     "targets": targets, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def _resolve_cast(self, game, seat, spell, targets=()):
        self.cast(game, seat, spell, targets)
        self.assertIsNone(self.resolve(game))

    def test_actual_runeflare_requires_one_opponent_to_draw_three_and_keeps_hand_private(self):
        game = self.session(77119001)
        trap = self.add(game.engine, "Runeflare Trap", zone="hand")
        draws = [self.add(game.engine, "Revitalize", seat="B", zone="hand", ref=f"runeflare-draw-{i}") for i in range(3)]
        other = self.add(game.engine, "Revitalize", seat="C", zone="hand", ref="runeflare-draw-other")
        private = self.add(game.engine, "Revitalize", seat="C", zone="hand", ref="runeflare-private")
        for card in (trap, *draws, other):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1})
        self._fund(game, "B", {"C": 3, "W": 3})
        self._fund(game, "C", {"C": 1, "W": 1})
        self.checkpoint(game)
        for spell in draws[:2]:
            self._resolve_cast(game, "B", spell)
        self._resolve_cast(game, "C", other)
        self._no_alternate(game, trap, ["C"])
        self._resolve_cast(game, "B", draws[2])
        self.priority(game, "B")
        departed = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(departed.ok, departed.summary)
        self.assertFalse(game.state.players["B"].in_game)
        cards_in_hand = len(game.state.players["C"].zones["hand"])
        before_life = game.state.players["C"].life
        action, option = self._alternative(game, trap, {"R": 1})
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self._commit(game, action, option, ["C"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before_life - cards_in_hand, game.state.players["C"].life)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_actual_ravenous_counts_one_opponents_card_arrivals_and_targets_another_graveyard(self):
        game = self.session(77119002)
        millstone = self.add(game.engine, "Millstone")
        trap = self.add(game.engine, "Ravenous Trap", zone="hand")
        later = self.add(game.engine, "Revitalize", seat="B", zone="hand", ref="ravenous-third")
        other = self.add(game.engine, "Revitalize", seat="C", zone="hand", ref="ravenous-other-graveyard")
        for card in (millstone, trap, later, other):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2})
        self._fund(game, "B", {"C": 1, "W": 1})
        self._fund(game, "C", {"C": 1, "W": 1})
        self.checkpoint(game)
        action, = [row for row in self._actions(game, "A") if row["id"].startswith("activate:" + millstone.ref + ":")]
        result = game.act("pilot:A", {"action_id": action["id"], "targets": ["B"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self._resolve_cast(game, "C", other)
        self.assertEqual((2, 1), tuple(len(game.state.players[s].zones["graveyard"]) for s in "BC"))
        self._no_alternate(game, trap, ["C"])
        self._resolve_cast(game, "B", later)
        self.assertEqual(3, len(game.state.players["B"].zones["graveyard"]))
        self.priority(game, "B")
        departed = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(departed.ok, departed.summary)
        self.assertFalse(game.state.players["B"].in_game)
        action, option = self._alternative(game, trap, {})
        self._commit(game, action, option, ["C"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[other.object_id].zone)
        self.replay(game, load=True)

    def test_actual_whiplash_counts_earlier_creature_arrivals_and_needs_two_targets(self):
        game = self.session(77119003)
        trap = self.add(game.engine, "Whiplash Trap", zone="hand")
        shock = self.add(game.engine, "Shock", zone="hand")
        first = self.add(game.engine, "Ashcoat Bear", seat="B", zone="hand", ref="whiplash-first")
        second = self.add(game.engine, "Ashcoat Bear", seat="B", zone="hand", ref="whiplash-second")
        other = self.add(game.engine, "Ashcoat Bear", seat="C", zone="hand", ref="whiplash-other")
        old = self.add(game.engine, "Memnite", ref="whiplash-old")
        for card in (trap, shock, first, second, other, old):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 1, "R": 1})
        self._fund(game, "B", {"C": 2, "G": 2})
        self._fund(game, "C", {"C": 1, "G": 1})
        self.checkpoint(game)
        self._resolve_cast(game, "B", first)
        self._resolve_cast(game, "C", other)
        self._no_alternate(game, trap, [old.ref, other.ref])
        self._resolve_cast(game, "B", second)
        self._resolve_cast(game, "A", shock, [second.ref])
        self.assertEqual("graveyard", game.state.cards[second.object_id].zone)
        action, option = self._alternative(game, trap, {"U": 1})
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": action["id"], "cost_option": option["id"],
                                        "targets": [old.ref], "pay": "auto"})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self._commit(game, action, option, [old.ref, other.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["hand", "hand", "battlefield"],
                         [game.state.cards[card.object_id].zone for card in (old, other, first)])
        self.replay(game, load=True)

    def test_actual_printed_price_remains_available_without_the_history_condition(self):
        game = self.session(77119004)
        trap = self.add(game.engine, "Runeflare Trap", zone="hand")
        self.assert_closed(game, trap)
        self.prepare(game, mana={"C": 4, "R": 2})
        self.checkpoint(game)
        action = next(row for row in self._actions(game, "A") if row["id"] == "cast:" + trap.ref)
        option, = action["cost_options"]
        self.assertEqual("mana", option["kind"])
        self.assertEqual(4, option["requirements"]["GENERIC"])
        self.assertEqual(2, option["requirements"]["R"])
        size = len(game.state.players["C"].zones["hand"])
        self._commit(game, action, option, ["C"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40 - size, game.state.players["C"].life)
        self.replay(game, load=True)

    def test_omission_restores_each_independent_price_residual(self):
        with patch("quorune.compiler.fixed_public_alternative_costs._opponent_history_price", return_value=None):
            for name in PRICES:
                result, _ = register_card(self.db, self.registry, name)
                self.assertFalse(result["strict_capability_ready"], result)
            retained, _ = register_card(self.db, self.registry, "Revitalize")
            self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

    def test_verified_withdrawals_require_fixed_price_and_only_relevant_history_owner(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY, PUBLIC_HISTORY_QUERY_CAPABILITY):
            with self.subTest(capability=capability):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Opponent history price authority withdrawn"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in PRICES:
                    result, _ = register_card(self.db, registry, name)
                    expected = name == "Runeflare Trap" and capability == PUBLIC_HISTORY_QUERY_CAPABILITY
                    self.assertEqual(expected, result["strict_capability_ready"], result)
                retained, _ = register_card(self.db, registry, "Revitalize")
                self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

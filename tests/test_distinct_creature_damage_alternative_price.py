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
from quorune.damage_turn_history import COMBAT_DAMAGE_HISTORY_MARKER
from quorune.public_history_query_model import PUBLIC_HISTORY_QUERY_CAPABILITY, PublicHistoryQuerySpec
from quorune.turn_history_model import TurnHistory, TurnHistoryEvent
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.util import mana_cost_to_vector
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
NAME = "Inferno Trap"


class DistinctCreatureDamageAlternativePriceTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-source-counter-cards.json": {"Grizzly Bears", "Shock"},
            "activation-restriction-material-cards.json": {"Memnite"},
            "activation-result-views-cards.json": {"Mountain"},
            "additional-conditional-modifier-cards.json": {"Revitalize"},
            "additional-continuous-carriers-cards.json": {"Hill Giant"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "distinct-creature-damage-alternative-price-cards.json": {NAME, "Triskelion"},
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
        database = folder / "distinct-creature-damage-alternative-price.sqlite3"
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Distinct creature damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        outcome, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(outcome["strict_capability_ready"], outcome)
        return card, {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)}

    def test_original_binds_distinct_damage_price_and_independent_creature_target(self):
        card, programs = self.original_program()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual({"cast.cost", "resolve"}, programs.keys())
        price = programs["cast.cost"]
        raw = thaw_value(price.handlers[0]["alternative_cost"])
        spec = FixedPublicAlternativeCostSpec.from_dict(raw)
        self.assertEqual((6, "damaged_by_two_creatures", "{R}"),
                         (spec.condition.schema_version, spec.condition.kind.value, spec.cost_text))
        self.assertEqual(raw, spec.to_dict())
        dependencies = {FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY, PUBLIC_HISTORY_QUERY_CAPABILITY}
        self.assertLessEqual(dependencies, set(price.capability_dependencies))
        result, = programs["resolve"].effects
        self.assertEqual(("damage", 4), (result["op"], result["amount"]))
        self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"], "count": 1, "types_any": ["creature"]},
                         thaw_value(programs["resolve"].target_schema))

        retained = tuple(cap for cap in price.capability_dependencies if cap != PUBLIC_HISTORY_QUERY_CAPABILITY)
        closure = self.registry.closure(retained, profile=PROFILE).to_dict()
        self.assertTrue(closure["trusted"], closure["blockers"])
        uncovered = replace(price, capability_dependencies=retained, capability_closure=closure,
                           provenance={**price.provenance, "capability_closure_fingerprint": closure["fingerprint"]})
        outcome = bind_semantic_program_runtime(uncovered, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + PUBLIC_HISTORY_QUERY_CAPABILITY, outcome["blockers"])

    def test_exact_damage_history_and_price_reject_wider_or_missing_facts(self):
        _, programs = self.original_program()
        raw = thaw_value(programs["cast.cost"].handlers[0]["alternative_cost"])
        condition = raw["condition"]
        for changes in ({"schema_version": 4}, {"schema_version": 5}, {"schema_version": 6.0},
                        {"kind": "always"}, {"threshold": 2}, {"basic_land_type": "island"}):
            with self.subTest(changes=changes), self.assertRaises(FixedPublicAlternativeCostError):
                FixedPublicAlternativeCondition.from_dict({**condition, **changes})
        requirements, _ = mana_cost_to_vector("{U}")
        with self.assertRaises(FixedPublicAlternativeCostError):
            FixedPublicAlternativeCostSpec.from_dict({**raw, "cost_text": "{U}", "mana_requirements": requirements})
        query = PublicHistoryQuerySpec(event="player_damaged", relation="controller", types_all=("creature",))
        self.assertEqual(query, PublicHistoryQuerySpec.from_dict(query.to_dict()))
        for changes in ({"relation": "opponents"}, {"types_all": ["instant"]}, {"types_any": ["land"]},
                        {"reduction": "sum_amount"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                PublicHistoryQuerySpec.from_dict({**query.to_dict(), **changes})

        first = TurnHistoryEvent("player_damaged", actor="A", object_incarnation="cast-first", target="A",
                                 target_kind="player", types=("creature",), amount=1)
        second = replace(first, actor="B", object_incarnation="cast-second")
        players = {seat: SimpleNamespace(in_game=True) for seat in "ABCD"}
        for events, expected in (
            ([], False), ([first, first], False), ([first, replace(second, amount=0)], False),
            ([first, replace(second, target="D")], False), ([first, replace(second, types=("instant",))], False),
            ([first, replace(second, object_incarnation=None)], False), ([first, second], True),
        ):
            with self.subTest(events=events):
                history = TurnHistory(turn_sequence=4, events=events)
                state = SimpleNamespace(turn_sequence=4, turn_history=history, players=players)
                self.assertEqual(expected, fixed_public_alternative_condition_met(
                    SimpleNamespace(state=state), "A", None, condition,
                ))

    def _actual_sources(self, game, recipient):
        return [(fact.object_incarnation, fact.types) for fact in game.state.turn_history.events
                if fact.kind == "player_damaged" and fact.target == recipient and fact.amount > 0]

    def _quote(self, game, seat, trap):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        return next((row for row in actions if row["id"] == "cast:" + trap.ref), None)

    def _reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _reject_alt(self, game, seat, trap, target):
        action = self._quote(game, seat, trap)
        self.assertNotIn("alternate", {price["kind"] for price in (action or {}).get("cost_options", ())})
        self._reject(game, seat, {"action_id": "cast:" + trap.ref, "cost_option": "plain-alternative-ab1",
                                 "targets": [target.ref], "pay": "auto"})

    def _cast_alt(self, game, seat, trap, target):
        action = self._quote(game, seat, trap)
        self.assertIsNotNone(action)
        option, = [price for price in action["cost_options"] if price["kind"] == "alternate"]
        self.assertEqual({"R": 1}, {key: count for key, count in option["requirements"].items() if count})
        cast = {"action_id": action["id"], "cost_option": option["id"], "pay": "auto"}
        self._reject(game, seat, {**cast, "targets": ["D"]})
        accepted = game.act("pilot:" + seat, {**cast, "targets": [target.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)

    def _ping(self, game, seat, source, recipient):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in actions if row["id"].startswith("activate:" + source.ref + ":")]
        result = game.act("pilot:" + seat, {"action_id": action["id"], "targets": [recipient], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def test_actual_inferno_uses_distinct_creature_sources_including_own_and_departed_controller(self):
        game = self.session(77125001)
        trap = self.add(game.engine, NAME, zone="hand")
        own = self.add(game.engine, "Triskelion", zone="battlefield", ref="inferno-own-repeat")
        other = self.add(game.engine, "Triskelion", seat="B", ref="inferno-second-same-name")
        shock = self.add(game.engine, "Shock", seat="C", zone="hand")
        land = self.add(game.engine, "Mountain", ref="inferno-caster-mountain")
        fuel = self.add(game.engine, "Mountain", seat="C", ref="inferno-other-mountain")
        target = self.add(game.engine, "Hill Giant", seat="D")
        private = self.add(game.engine, "Memnite", seat="D", zone="hand", ref="inferno-private-hand")
        for card in (trap, own, other, shock, land, fuel, target):
            self.assert_closed(game, card)
        for card in (own, other):
            card.counters["+1/+1"] = 3
        own_id, other_id = own.logical_object_id, other.logical_object_id
        self.prepare(game)
        self.checkpoint(game)
        self._ping(game, "A", own, "A")
        self._ping(game, "A", own, "A")
        self._reject_alt(game, "A", trap, target)

        self._ping(game, "B", other, "D")
        self.cast(game, "C", shock, ["A"])
        self.assertIsNone(self.resolve(game))
        self._reject_alt(game, "A", trap, target)
        self.assertEqual(36, game.state.players["A"].life)
        self.assertEqual([own_id, own_id], [source for source, types in self._actual_sources(game, "A")
                                           if "creature" in types])
        self._ping(game, "B", other, "A")
        self.assertEqual([own_id, own_id, other_id], [source for source, types in self._actual_sources(game, "A")
                                                     if "creature" in types])
        self.priority(game, "B")
        departure = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(departure.ok, departure.summary)
        self.assertFalse(game.state.players["B"].in_game)
        self._cast_alt(game, "A", trap, target)
        self.assertEqual(35, game.state.players["A"].life)
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_actual_simultaneous_combat_damage_from_two_creatures_authorizes_inferno(self):
        game = self.session(77125002)
        trap = self.add(game.engine, NAME, seat="B", zone="hand")
        first = self.add(game.engine, "Grizzly Bears", ref="inferno-first-combat")
        second = self.add(game.engine, "Grizzly Bears", ref="inferno-second-combat")
        target = self.add(game.engine, "Hill Giant", seat="D")
        land = self.add(game.engine, "Mountain", seat="B")
        for card in (trap, first, second, target, land):
            self.assert_closed(game, card)
        for card in (first, second):
            card.acquired_control_turn_count = -1
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(first, "B"), (second, "B")])
        self._reject_alt(game, "B", trap, target)
        self.until(game, lambda current: len(self._actual_sources(current, "B")) >= 2)
        facts = self._actual_sources(game, "B")
        self.assertEqual({first.logical_object_id, second.logical_object_id}, {source for source, _ in facts})
        self.assertTrue(all(COMBAT_DAMAGE_HISTORY_MARKER in types for _, types in facts))
        self.assertEqual(36, game.state.players["B"].life)
        self._cast_alt(game, "B", trap, target)
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.replay(game, load=True)

    def test_omission_restores_infernos_distinct_creature_damage_price_residual(self):
        with patch("quorune.compiler.fixed_public_alternative_costs._creature_damage_price", return_value=None):
            outcome, _ = register_card(self.db, self.registry, NAME)
            self.assertFalse(outcome["strict_capability_ready"], outcome)
            retained, _ = register_card(self.db, self.registry, "Revitalize")
            self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

    def test_genuine_withdrawals_require_price_and_distinct_damage_history_owners(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        original = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY, PUBLIC_HISTORY_QUERY_CAPABILITY):
            with self.subTest(capability=capability):
                document = deepcopy(original)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Distinct creature damage price authority withdrawn"])
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

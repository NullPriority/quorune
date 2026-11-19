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
from quorune.public_history_query_model import PUBLIC_HISTORY_QUERY_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.turn_history import roll_turn_history
from quorune.turn_history_model import CAST_CREATURE_COUNTERED_EVENT, TurnHistory, TurnHistoryEvent
from quorune.util import mana_cost_to_vector
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
NAME = "Summoning Trap"


class CreatureCounterAlternativePriceTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-source-counter-cards.json": {"Counterspell", "Shock", "Sol Ring"},
            "activation-restriction-material-cards.json": {"Memnite"},
            "additional-conditional-modifier-cards.json": {"Revitalize"},
            "battlefield-library-selection-cards.json": {NAME},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "cast-color-alternative-price-cards.json": {"Mana Tithe"},
            "finality-counter-cards.json": {"Sea-Dasher Octopus"},
            "mandatory-single-stack-target-change-cards.json": {"Tolarian Terror"},
            "mixed-stack-counter-cards.json": {"Carnage Tyrant"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "targeted-spell-control-cards.json": {"Aethersnatch"},
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
        database = folder / "creature-counter-alternative-price.sqlite3"
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Committed creature spell counters", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        outcome, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(outcome["strict_capability_ready"], outcome)
        return card, {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)}

    def test_complete_original_binds_zero_price_and_preserves_independent_top_seven(self):
        card, programs = self.original_program()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual({"cast.cost", "resolve"}, programs.keys())
        price = programs["cast.cost"]
        raw = thaw_value(price.handlers[0]["alternative_cost"])
        spec = FixedPublicAlternativeCostSpec.from_dict(raw)
        self.assertEqual((4, "opponent_countered_creature_cast", "{0}"),
                         (spec.condition.schema_version, spec.condition.kind.value, spec.cost_text))
        self.assertEqual(raw, spec.to_dict())
        dependencies = {FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY, PUBLIC_HISTORY_QUERY_CAPABILITY}
        self.assertLessEqual(dependencies, set(price.capability_dependencies))
        result, = programs["resolve"].effects
        self.assertEqual(("fixed_library_selection", 7, 0, 1, "battlefield", "library_bottom", "chosen"),
                         tuple(result[key] for key in ("op", "look_count", "minimum", "maximum",
                                                       "selected_destination", "remainder_destination", "remainder_order")))
        self.assertFalse(result["public_reveal"])
        self.assertFalse(result["selected_reveal"])

        retained = tuple(cap for cap in price.capability_dependencies if cap != PUBLIC_HISTORY_QUERY_CAPABILITY)
        closure = self.registry.closure(retained, profile=PROFILE).to_dict()
        self.assertTrue(closure["trusted"], closure["blockers"])
        stripped = replace(price, capability_dependencies=retained, capability_closure=closure,
                           provenance={**price.provenance, "capability_closure_fingerprint": closure["fingerprint"]})
        outcome = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + PUBLIC_HISTORY_QUERY_CAPABILITY, outcome["blockers"])

    def test_counter_facts_require_exact_mode_caster_incarnation_and_source_controller(self):
        current = TurnHistory(turn_sequence=4, creature_spell_counter_history_version=1)
        historical = TurnHistory(turn_sequence=4)
        historical_data = historical.to_dict()
        self.assertNotIn("creature_spell_counter_history_version", historical_data)
        self.assertEqual(historical_data, TurnHistory.from_dict(historical_data).to_dict())
        fact = TurnHistoryEvent(CAST_CREATURE_COUNTERED_EVENT, actor="A", object_incarnation="cast-1",
                                target="B", target_kind="player")
        current.events.append(fact)
        self.assertEqual(current, TurnHistory.from_dict(current.to_dict()))
        rolled = roll_turn_history(current, next_turn_sequence=5, previous_active_player="A")
        self.assertEqual((1, 5, []), (rolled.creature_spell_counter_history_version, rolled.turn_sequence, rolled.events))

        for changes in (
            {"actor": None}, {"object_incarnation": None}, {"target": None}, {"types": ["creature"]},
            {"target_kind": "stack"}, {"target_object_incarnation": "private"}, {"amount": 1},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                TurnHistoryEvent.from_dict({**fact.to_dict(), **changes})
        for version in (None, True, 2):
            with self.subTest(version=version), self.assertRaises(ValueError):
                TurnHistory.from_dict({**current.to_dict(), "creature_spell_counter_history_version": version})
        with self.assertRaises(ValueError):
            TurnHistory.from_dict({**historical_data, "events": [fact.to_dict()]})
        with self.assertRaises(ValueError):
            TurnHistory(turn_sequence=4, creature_spell_counter_history_version=1, events=[fact, fact])

    def test_wrong_price_version_or_counter_relation_fails_closed(self):
        _, programs = self.original_program()
        raw = thaw_value(programs["cast.cost"].handlers[0]["alternative_cost"])
        condition = raw["condition"]
        for fields in ({"schema_version": 1}, {"schema_version": True},
                       {"kind": "always"}, {"basic_land_type": "island"}):
            with self.subTest(fields=fields), self.assertRaises(FixedPublicAlternativeCostError):
                FixedPublicAlternativeCondition.from_dict({**condition, **fields})
        requirements, _ = mana_cost_to_vector("{G}")
        with self.assertRaises(FixedPublicAlternativeCostError):
            FixedPublicAlternativeCostSpec.from_dict({**raw, "cost_text": "{G}", "mana_requirements": requirements})

        fact = TurnHistoryEvent(CAST_CREATURE_COUNTERED_EVENT, actor="A", object_incarnation="cast-1",
                                target="B", target_kind="player")
        players = {seat: SimpleNamespace(in_game=seat != "D") for seat in "ABCD"}
        for history, expected in (
            (TurnHistory(turn_sequence=4), False),
            (TurnHistory(turn_sequence=4, creature_spell_counter_history_version=1, events=[fact]), True),
            (TurnHistory(turn_sequence=4, creature_spell_counter_history_version=1,
                         events=[replace(fact, target="A")]), False),
            (TurnHistory(turn_sequence=4, creature_spell_counter_history_version=1,
                         events=[replace(fact, target="D")]), True),
            (TurnHistory(turn_sequence=4, creature_spell_counter_history_version=1,
                         events=[replace(fact, actor="C")]), False),
            (TurnHistory(turn_sequence=3, creature_spell_counter_history_version=1, events=[fact]), False),
        ):
            with self.subTest(history=history):
                state = SimpleNamespace(turn_sequence=4, players=players, turn_history=history)
                host = SimpleNamespace(state=state, active_seats=("A", "B", "C"))
                self.assertEqual(expected, fixed_public_alternative_condition_met(host, "A", None, condition))

        past = TurnHistory(turn_sequence=4, creature_spell_counter_history_version=1, events=[replace(fact, target="D")])
        state = SimpleNamespace(turn_sequence=4, players=players, turn_history=past)
        host = SimpleNamespace(state=state, active_seats=("A", "B", "C"))
        legacy = {**condition, "schema_version": 3}
        self.assertFalse(fixed_public_alternative_condition_met(host, "A", None, legacy))

    def _spell(self, game, card):
        item, = (item for item in game.state.stack if item.card_object_id == card.object_id)
        return item

    def _choose(self, game, seat, **fields):
        result = game.act("pilot:" + seat, {"action_id": "choose", **fields})
        self.assertTrue(result.ok, result.summary)

    def _counter_facts(self, game):
        return [(fact.actor, fact.target, fact.object_incarnation)
                for fact in game.state.turn_history.events if fact.kind == CAST_CREATURE_COUNTERED_EVENT]

    def _price(self, game, seat, trap):
        self.priority(game, seat)
        action_id = "cast:" + trap.ref
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next((row for row in actions if row["id"] == action_id), None)
        prices = [price for price in (action or {}).get("cost_options", ()) if price["kind"] == "alternate"]
        if not prices:
            return None
        price, = prices
        self.assertFalse(any(price["requirements"].values()))
        return action, price

    def _reject_zero_price(self, game, seat, trap):
        self.assertIsNone(self._price(game, seat, trap))
        before = authoritative_state_hash(game.state)
        refusal = game.act("pilot:" + seat, {"action_id": "cast:" + trap.ref, "cost_option": "plain-alternative-ab1",
                                             "targets": [], "pay": "auto"})
        self.assertFalse(refusal.ok, refusal.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _cast_zero_and_choose_creature(self, game, trap, chosen=None):
        action, price = self._price(game, "A", trap)
        top = list(reversed(game.state.players["A"].zones["library"]))[:7]
        moved = [] if chosen is None else [chosen.ref]
        bottom = [game.state.cards[oid].ref for oid in reversed(top) if chosen is None or oid != chosen.object_id]
        accepted = game.act("pilot:A", {"action_id": action["id"], "cost_option": price["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        if chosen is not None:
            self.assertNotIn(chosen.ref, json.dumps(game.packet("pilot:D", full=True)))
        response = {"cards": {"battlefield": moved, "bottom": bottom}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", **response}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self._choose(game, "A", **response)
        self.assertIsNone(self.resolve(game))
        if chosen is not None:
            self.assertEqual("battlefield", game.state.cards[chosen.object_id].zone)
        library = game.state.players["A"].zones["library"]
        self.assertEqual(bottom, [game.state.cards[oid].ref for oid in library[:len(bottom)]])

    def test_actual_opponents_ward_qualifies_even_when_the_original_caster_declines_payment(self):
        game = self.session(77122010)
        ward = self.add(game.engine, "Tolarian Terror")
        caster = self.add(game.engine, "Sea-Dasher Octopus", zone="hand")
        trap = self.add(game.engine, NAME, zone="hand")
        private = self.add(game.engine, "Memnite", zone="library", ref="summoning-private-choice")
        for card in (ward, caster, trap, private):
            self.assert_closed(game, card)
        game.engine.change_control(ward.object_id, "B", reason="Initial opponent Ward controller")
        self.prepare(game, mana={"C": 1, "U": 1})
        self.checkpoint(game)
        self._reject_zero_price(game, "A", trap)
        offered = self.action(game, "A", caster)
        mutate, = [row for row in offered["cost_options"] if row["id"].startswith("mutate")]
        accepted = game.act("pilot:A", {"action_id": offered["id"], "cost_option": mutate["id"],
                                        "targets": [ward.ref], "pay": "manual", "payment": {"C": 1, "U": 1}})
        self.assertTrue(accepted.ok, accepted.summary)
        incarnation = game.state.cards[caster.object_id].logical_object_id
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("ward_counter_unless_pay", pending.payload_by_actor["A"]["operation"])
        self.assertEqual([], self._counter_facts(game))
        game = self.replay(game, load=True)
        self._choose(game, "A", pay=False)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[caster.object_id].zone)
        self.assertEqual([("A", "B", incarnation)], self._counter_facts(game))
        self._cast_zero_and_choose_creature(game, trap, private)
        self.replay(game, load=True)

    def test_actual_new_controller_pays_or_declines_but_only_original_caster_qualifies(self):
        for pay in (True, False):
            with self.subTest(pay=pay):
                game = self.session(77122011 + pay)
                creature = self.add(game.engine, "Memnite", zone="hand")
                trap = self.add(game.engine, NAME, zone="hand", ref="summoning-original-caster")
                other_trap = self.add(game.engine, NAME, seat="B", zone="hand", ref="summoning-current-controller")
                takeover = self.add(game.engine, "Aethersnatch", seat="B", zone="hand")
                tax = self.add(game.engine, "Mana Tithe", seat="C", zone="hand")
                for card in (creature, trap, other_trap, takeover, tax):
                    self.assert_closed(game, card)
                self.prepare(game)
                game.state.players["B"].mana_pool.update({"C": 5, "U": 2})
                game.state.players["C"].mana_pool["W"] = 1
                self.checkpoint(game)
                self.cast(game, "A", creature)
                incarnation = game.state.cards[creature.object_id].logical_object_id
                target = self._spell(game, creature).ref
                self.cast(game, "B", takeover, [target])
                self.resolve_top(game)
                self.assertEqual("B", self._spell(game, creature).controller)
                self.cast(game, "C", tax, [target])
                pending = self.resolve(game)
                self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
                self.assertEqual([], self._counter_facts(game))
                self._choose(game, "B", pay=pay)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield" if pay else "graveyard", game.state.cards[creature.object_id].zone)
                self.assertEqual([] if pay else [("A", "C", incarnation)], self._counter_facts(game))
                self._reject_zero_price(game, "B", other_trap)
                if pay:
                    self._reject_zero_price(game, "A", trap)
                else:
                    self.priority(game, "C")
                    departed = game.act("pilot:C", {"action_id": "concede", "choices": {"confirm_concede": True}})
                    self.assertTrue(departed.ok, departed.summary)
                    self.assertFalse(game.state.players["C"].in_game)
                    self._cast_zero_and_choose_creature(game, trap)
                self.replay(game, load=True)

    def test_actual_self_counter_noncreature_and_failed_counter_do_not_allow_zero_price(self):
        game = self.session(77122013)
        trap = self.add(game.engine, NAME, zone="hand")
        ring = self.add(game.engine, "Sol Ring", zone="hand")
        creature = self.add(game.engine, "Memnite", zone="hand")
        tyrant = self.add(game.engine, "Carnage Tyrant", zone="hand")
        own = self.add(game.engine, "Counterspell", zone="hand", ref="summoning-self-counter")
        other = [self.add(game.engine, "Counterspell", seat="B", zone="hand", ref=f"summoning-counter-{i}")
                 for i in range(2)]
        for card in (trap, ring, creature, tyrant, own, *other):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "G": 2, "U": 2})
        game.state.players["B"].mana_pool["U"] = 4
        self.checkpoint(game)
        offered = self.action(game, "A", ring)
        accepted = game.act("pilot:A", {"action_id": offered["id"], "pay": "manual", "payment": {"C": 1}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.cast(game, "B", other[0], [self._spell(game, ring).ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[ring.object_id].zone)
        self.assertEqual([], self._counter_facts(game))
        self._reject_zero_price(game, "A", trap)
        ordinary = self.action(game, "A", trap)
        self.assertIn("mana", {row["kind"] for row in ordinary["cost_options"]})

        self.cast(game, "A", creature)
        incarnation = game.state.cards[creature.object_id].logical_object_id
        self.cast(game, "A", own, [self._spell(game, creature).ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[creature.object_id].zone)
        self.assertEqual([("A", "A", incarnation)], self._counter_facts(game))
        self._reject_zero_price(game, "A", trap)

        self.cast(game, "A", tyrant)
        self.cast(game, "B", other[1], [self._spell(game, tyrant).ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[tyrant.object_id].zone)
        self.assertEqual([("A", "A", incarnation)], self._counter_facts(game))
        self._reject_zero_price(game, "A", trap)
        self.replay(game, load=True)

    def test_omission_restores_the_counter_price_residual(self):
        with patch("quorune.compiler.fixed_public_alternative_costs._opponent_history_price", return_value=None):
            outcome, _ = register_card(self.db, self.registry, NAME)
            self.assertFalse(outcome["strict_capability_ready"], outcome)
            retained, _ = register_card(self.db, self.registry, "Revitalize")
            self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

    def test_genuine_withdrawals_require_both_creature_counter_price_owners(self):
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
                    status="blocked", blockers=["Creature spell counter price authority withdrawn"])
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

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
from quorune.stack_target_change_model import SingleStackTargetChangeSpec
from quorune.turn_history import roll_turn_history
from quorune.turn_history_model import TurnHistory, TurnHistoryEvent
from quorune.util import mana_cost_to_vector
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
NAME = "Ricochet Trap"


class CastColorAlternativePriceTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Unsummon"},
            "ability-source-counter-cards.json": {"Shock", "Sol Ring"},
            "all-kind-counter-cards.json": {"Reverberate"},
            "activation-restriction-material-cards.json": {"Memnite"},
            "additional-conditional-modifier-cards.json": {"Revitalize"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "cast-color-alternative-price-cards.json": {"Mana Tithe", NAME},
            "nonmana-morph-cards.json": {"Fathom Seer"},
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
        database = folder / "cast-color-alternative-price.sqlite3"
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Committed cast colors", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        outcome, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(outcome["strict_capability_ready"], outcome)
        return card, {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)}

    def test_complete_original_binds_red_price_and_independent_mandatory_target_change(self):
        card, programs = self.original_program()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual({"cast.cost", "resolve"}, programs.keys())
        price = programs["cast.cost"]
        raw = thaw_value(price.handlers[0]["alternative_cost"])
        spec = FixedPublicAlternativeCostSpec.from_dict(raw)
        self.assertEqual((4, "opponent_blue_cast", "{R}"),
                         (spec.condition.schema_version, spec.condition.kind.value, spec.cost_text))
        self.assertEqual(raw, spec.to_dict())
        dependencies = {FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY, PUBLIC_HISTORY_QUERY_CAPABILITY}
        self.assertLessEqual(dependencies, set(price.capability_dependencies))
        target_change = SingleStackTargetChangeSpec(spell_or_ability=False)
        resolution = programs["resolve"]
        self.assertEqual(list(target_change.effects), thaw_value(resolution.effects))
        self.assertEqual(target_change.target_schema, thaw_value(resolution.target_schema))

        retained = tuple(cap for cap in price.capability_dependencies if cap != PUBLIC_HISTORY_QUERY_CAPABILITY)
        closure = self.registry.closure(retained, profile=PROFILE).to_dict()
        self.assertTrue(closure["trusted"], closure["blockers"])
        stripped = replace(price, capability_dependencies=retained, capability_closure=closure,
                           provenance={**price.provenance, "capability_closure_fingerprint": closure["fingerprint"]})
        outcome = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + PUBLIC_HISTORY_QUERY_CAPABILITY, outcome["blockers"])

    def test_as_cast_history_distinguishes_colorless_unknown_and_invalid_facts(self):
        unknown = TurnHistoryEvent("spell_cast", actor="B", object_incarnation="unknown")
        historical = TurnHistory(turn_sequence=4, events=[unknown])
        old = historical.to_dict()
        self.assertNotIn("spell_colors", unknown.to_dict())
        self.assertNotIn("spell_color_history_version", old)
        self.assertEqual(old, TurnHistory.from_dict(old).to_dict())

        colorless = replace(unknown, spell_colors=())
        blue = replace(unknown, object_incarnation="blue", spell_colors=("U",))
        multicolor = replace(unknown, object_incarnation="multi", spell_colors=("W", "U", "R"))
        current = TurnHistory(turn_sequence=4, spell_color_history_version=1, events=[colorless, blue, multicolor])
        serialized = current.to_dict()
        self.assertEqual([[], ["U"], ["W", "U", "R"]], [entry["spell_colors"] for entry in serialized["events"]])
        self.assertEqual(current, TurnHistory.from_dict(serialized))
        rolled = roll_turn_history(current, next_turn_sequence=5, previous_active_player="B")
        self.assertEqual((1, 5, [], {"B": 3}),
                         (rolled.spell_color_history_version, rolled.turn_sequence,
                          rolled.events, rolled.previous_spell_cast_counts))
        self.assertIsNone(roll_turn_history(historical, next_turn_sequence=5, previous_active_player="B")
                          .spell_color_history_version)

        for fields in (
            {"spell_colors": None}, {"spell_colors": "U"}, {"spell_colors": ["U", "U"]},
            {"spell_colors": ["R", "U"]}, {"spell_colors": ["C"]}, {"actor": None},
            {"object_incarnation": None}, {"kind": "spell_copy"},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                TurnHistoryEvent.from_dict({**blue.to_dict(), **fields})
        for version in (None, True, 2):
            with self.subTest(version=version), self.assertRaises(ValueError):
                TurnHistory.from_dict({**serialized, "spell_color_history_version": version})
        without_mode = {key: value for key, value in serialized.items() if key != "spell_color_history_version"}
        with self.assertRaises(ValueError):
            TurnHistory.from_dict(without_mode)

    def test_price_fails_closed_on_unavailable_color_and_malformed_terms(self):
        _, programs = self.original_program()
        raw = thaw_value(programs["cast.cost"].handlers[0]["alternative_cost"])
        condition = raw["condition"]
        for fields in ({"schema_version": 1}, {"schema_version": True},
                       {"kind": "always"}, {"basic_land_type": "island"}):
            with self.subTest(fields=fields), self.assertRaises(FixedPublicAlternativeCostError):
                FixedPublicAlternativeCondition.from_dict({**condition, **fields})
        requirements, _ = mana_cost_to_vector("{U}")
        with self.assertRaises(FixedPublicAlternativeCostError):
            FixedPublicAlternativeCostSpec.from_dict({**raw, "cost_text": "{U}", "mana_requirements": requirements})

        unknown = TurnHistoryEvent("spell_cast", actor="B", object_incarnation="unknown")
        blue = replace(unknown, object_incarnation="blue", spell_colors=("U",))
        players = {seat: SimpleNamespace(in_game=True) for seat in "ABCD"}
        for history, expected in (
            (TurnHistory(turn_sequence=4, events=[unknown]), False),
            (TurnHistory(turn_sequence=4, spell_color_history_version=1, events=[blue, unknown]), False),
            (TurnHistory(turn_sequence=4, spell_color_history_version=1, events=[blue]), True),
            (TurnHistory(turn_sequence=3, spell_color_history_version=1, events=[blue]), False),
        ):
            with self.subTest(history=history):
                host = SimpleNamespace(state=SimpleNamespace(turn_sequence=4, players=players, turn_history=history))
                self.assertEqual(expected, fixed_public_alternative_condition_met(host, "A", None, condition))

    def _spell(self, game, card):
        item, = (item for item in game.state.stack if item.card_object_id == card.object_id)
        return item

    def _choose(self, game, seat, **fields):
        result = game.act("pilot:" + seat, {"action_id": "choose", **fields})
        self.assertTrue(result.ok, result.summary)

    def _actual_casts(self, game):
        return [(event.actor, event.spell_colors) for event in game.state.turn_history.events
                if event.kind == "spell_cast"]

    def _reject_ineligible_red_price(self, game, trap, target):
        self.priority(game, "A")
        action_id = "cast:" + trap.ref
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertNotIn(action_id, {row["id"] for row in actions})
        before = authoritative_state_hash(game.state)
        refused = game.act("pilot:A", {"action_id": action_id, "targets": [target],
                                       "cost_option": "plain-alternative-ab1", "pay": "auto"})
        self.assertFalse(refused.ok, refused.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_countered_blue_cast_enables_red_price_for_a_different_spell(self):
        game = self.session(77120001)
        trap = self.add(game.engine, NAME, zone="hand")
        creature = self.add(game.engine, "Memnite")
        blue = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        ring = self.add(game.engine, "Sol Ring", seat="B", zone="hand")
        counter = self.add(game.engine, "Mana Tithe", seat="C", zone="hand")
        shock = self.add(game.engine, "Shock", seat="D", zone="hand")
        for card in (trap, creature, blue, ring, counter, shock):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"U": 2})
        for seat, color in (("A", "R"), ("C", "W"), ("D", "R")):
            game.state.players[seat].mana_pool[color] = 1
        self.checkpoint(game)
        self.cast(game, "B", ring)
        self.assertEqual(1, game.state.players["B"].mana_pool["U"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual([("B", ())], self._actual_casts(game))
        self.cast(game, "D", shock, ["C"])
        target = self._spell(game, shock)
        self._reject_ineligible_red_price(game, trap, target.ref)

        self.cast(game, "B", blue, [creature.ref])
        self.cast(game, "C", counter, [self._spell(game, blue).ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        self._choose(game, "B", pay=False)
        self.assertEqual("graveyard", game.state.cards[blue.object_id].zone)
        self.assertEqual([("B", ()), ("D", ("R",)), ("B", ("U",)), ("C", ("W",))], self._actual_casts(game))

        self.priority(game, "B")
        departed = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(departed.ok, departed.summary)
        self.assertFalse(game.state.players["B"].in_game)

        self.priority(game, "A")
        action = self.action(game, "A", trap)
        option, = [price for price in action["cost_options"] if price["kind"] == "alternate"]
        paid = {key: count for key, count in option["requirements"].items() if count}
        self.assertEqual({"R": 1}, paid)
        cast = game.act("pilot:A", {"action_id": action["id"], "cost_option": option["id"],
                                    "targets": [target.ref], "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        choice = pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
        self.assertFalse(choice["optional"])
        self.assertNotIn("C", choice["target_schema"]["legal_refs"])
        self.assertIn("A", choice["target_schema"]["legal_refs"])
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "targets": ["C"]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self._choose(game, "A", targets=["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((38, 40), (game.state.players["A"].life, game.state.players["C"].life))
        self.assertEqual(0, game.state.players["A"].mana_pool["R"])
        self.replay(game, load=True)

    def test_actual_face_down_own_cast_and_opponents_blue_copy_leave_price_unavailable(self):
        game = self.session(77120002)
        trap = self.add(game.engine, NAME, zone="hand")
        morph = self.add(game.engine, "Fathom Seer", seat="B", zone="hand")
        original = self.add(game.engine, "Unsummon", zone="hand")
        reverberate = self.add(game.engine, "Reverberate", seat="B", zone="hand")
        for card in (trap, morph, original, reverberate):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"C": 3, "R": 2})
        game.state.players["A"].mana_pool.update({"U": 1, "R": 1})
        self.checkpoint(game)

        cast = game.act("pilot:B", {"action_id": "cast-morph:" + morph.ref,
                                   "pay": "manual", "payment": {"C": 3}})
        self.assertTrue(cast.ok, cast.summary)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[morph.object_id].face_down)
        self.assertEqual(2, game.state.players["B"].mana_pool["R"])
        for observer in "ACD":
            self.assertNotIn("Fathom Seer", json.dumps(game.packet("pilot:" + observer, full=True)))
        self.cast(game, "A", original, [morph.ref])
        self.cast(game, "B", reverberate, [self._spell(game, original).ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        self._choose(game, "B")
        copied, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual("B", copied.controller)
        self.assertEqual({"U"}, game.engine._source_colors_for_ref(copied.ref))
        self.assertEqual([("B", ()), ("A", ("U",)), ("B", ("R",))], self._actual_casts(game))
        self._reject_ineligible_red_price(game, trap, copied.ref)
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_omission_restores_the_price_residual(self):
        with patch("quorune.compiler.fixed_public_alternative_costs._opponent_history_price", return_value=None):
            outcome, _ = register_card(self.db, self.registry, NAME)
            self.assertFalse(outcome["strict_capability_ready"], outcome)
            retained, _ = register_card(self.db, self.registry, "Revitalize")
            self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

    def test_genuine_withdrawals_require_both_price_and_history_owners(self):
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
                    status="blocked", blockers=["As-cast color price authority withdrawn"])
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

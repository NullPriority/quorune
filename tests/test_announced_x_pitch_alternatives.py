from copy import deepcopy
from dataclasses import replace
import json
import re
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
    ANNOUNCED_X_PITCH_POLICY,
    FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY,
    FixedPublicAlternativeCostError,
    FixedPublicAlternativeCostSpec,
)
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.casting_additional_costs import fixed_zone_change_additional_cost
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
HAND_EXILE = "casting.additional_cost.zone_change.fixed_exile"
PRICES = {
    "Blazing Shoal": ("R", "modify_stats_until_end_of_turn"),
    "Nourishing Shoal": ("G", "life"),
    "Sickening Shoal": ("B", "modify_stats_until_end_of_turn"),
}


class AnnouncedXPitchAlternativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Forest"},
            "ability-source-counter-cards.json": {"Grizzly Bears"},
            "activation-public-state-cards.json": {"Doom Blade", "Giant Growth"},
            "activation-restriction-material-cards.json": {"Lightning Bolt"},
            "additional-conditional-modifier-cards.json": {"Revitalize"},
            "announced-x-pitch-alternative-cards.json": {
                *PRICES, "Hooded Hydra", "Kobolds of Kher Keep", "Shining Shoal",
            },
            "announced-x-source-discard-cards.json": {"Colossal Dreadmaw", "Generic Bound Plains"},
            "fixed-stack-mana-value-cards.json": {"Disdainful Stroke"},
            "singular-characteristic-set-cards.json": {"Runeclaw Bear"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
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
        database = folder / "announced-x-pitch-alternatives.sqlite3"
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Announced X pitch casts", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_programs(self, name):
        card = self.db.lookup(name, fuzzy=False)
        outcome, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(outcome["strict_capability_ready"], outcome)
        return card, {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)}

    def test_complete_shoal_pitch_descriptors_preserve_each_result_and_partial_sibling(self):
        for name, (color, operation) in PRICES.items():
            with self.subTest(card=name):
                card, programs = self.original_programs(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual({"cast.cost", "resolve"}, programs.keys())
                price = programs["cast.cost"]
                raw = thaw_value(price.handlers[0]["alternative_cost"])
                spec = FixedPublicAlternativeCostSpec.from_dict(raw)
                self.assertEqual((2, None, "always"), (spec.schema_version, spec.cost_text, spec.condition.kind.value))
                self.assertEqual(raw, spec.to_dict())
                self.assertFalse(any(dict(spec.mana_requirements).values()))
                cost = fixed_zone_change_additional_cost(raw["additional_costs"][0])
                self.assertEqual((("hand",), "$actor", (color,), 1),
                                 (cost.predicate.zones, cost.predicate.owner, cost.predicate.colors_all, cost.count))
                self.assertEqual(ANNOUNCED_X_PITCH_POLICY, spec.cast_cost_option()["x_value_policy"])
                self.assertLessEqual({FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY, HAND_EXILE},
                                     set(price.capability_dependencies))
                self.assertEqual([operation], [effect["op"] for effect in programs["resolve"].effects])
        name = "Shining Shoal"
        card = self.db.lookup(name, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertTrue(ir.material_residuals)
        self.assertTrue(any(node.event == "cast.cost" for face in ir.faces for node in face.nodes))
        result, _ = register_card(self.db, self.registry, name)
        self.assertFalse(result["strict_capability_ready"], result)

    def test_shoal_pitch_descriptors_reject_cost_widening_and_preserve_version_one_shape(self):
        _, programs = self.original_programs("Blazing Shoal")
        price = programs["cast.cost"]
        raw = thaw_value(price.handlers[0]["alternative_cost"])
        for changes in (
            {"schema_version": 2.0}, {"additional_costs": []},
            {"condition": {**raw["condition"], "kind": "your_turn"}},
            {"additional_costs": [{**raw["additional_costs"][0], "count": 2}]},
            {"additional_costs": [{**raw["additional_costs"][0], "predicate": {
                **raw["additional_costs"][0]["predicate"], "colors_all": ["R", "G"],
            }}]},
        ):
            with self.subTest(changes=changes), self.assertRaises(FixedPublicAlternativeCostError):
                FixedPublicAlternativeCostSpec.from_dict({**raw, **changes})
        historical = {**raw, "schema_version": 1}
        legacy = FixedPublicAlternativeCostSpec.from_dict(historical)
        self.assertEqual(historical, legacy.to_dict())
        self.assertNotIn("x_value_policy", legacy.cast_cost_option())

        caps = tuple(cap for cap in price.capability_dependencies if cap != HAND_EXILE)
        closure = self.registry.closure(caps, profile=PROFILE).to_dict()
        self.assertTrue(closure["trusted"], closure["blockers"])
        changed = replace(price, capability_dependencies=caps, capability_closure=closure,
                          provenance={**price.provenance, "capability_closure_fingerprint": closure["fingerprint"]})
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + HAND_EXILE, result["blockers"])

    def _options(self, game, source):
        self.priority(game, "A")
        action = self.action(game, "A", source)
        options = {entry["choice_schema"]["x"]["default"]: entry for entry in action["cost_options"]
                   if entry["kind"] == "alternate"}
        return action, options

    def _pitch(self, action, option, x, card, targets=()):
        return {"action_id": action["id"], "cost_option": option["id"], "x": x,
                "exile_cards": [card.ref], "targets": list(targets), "pay": "auto"}

    def _reject(self, game, command, seat="A"):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _cast(self, game, command, source, x):
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        item, = [item for item in game.state.stack if item.card_object_id == source.object_id]
        self.assertEqual(x, item.x_value)
        return item

    def test_actual_nourishing_quotes_exact_x_and_keeps_its_announced_stack_value(self):
        game = self.session(77127001)
        source = self.add(game.engine, "Nourishing Shoal", zone="hand")
        growth = self.add(game.engine, "Giant Growth", zone="hand", ref="pitch-visible-to-a")
        hydra = self.add(game.engine, "Hooded Hydra", zone="hand")
        big = self.add(game.engine, "Colossal Dreadmaw", zone="hand")
        colorless = self.add(game.engine, "Forest", zone="hand")
        other = self.add(game.engine, "Giant Growth", seat="C", zone="hand", ref="pitch-forbidden-other-hand")
        counter = self.add(game.engine, "Disdainful Stroke", seat="B", zone="hand")
        for row in (source, counter):
            self.assert_closed(game, row)
        self.prepare(game)
        game.state.players["B"].mana_pool.update({"C": 1, "U": 1})
        self.checkpoint(game)
        action, options = self._options(game, source)
        self.assertEqual({1, 2, 6}, options.keys())
        for x, candidate in ((1, growth), (2, hydra), (6, big)):
            schema = options[x]["choice_schema"]
            self.assertEqual({"type": "integer", "minimum": x, "maximum": x, "default": x}, schema["x"])
            self.assertEqual([candidate.ref], schema["exile_cards"]["legal_refs"])
        for ref in (growth.ref, hydra.ref, big.ref):
            self.assertNotIn(ref, json.dumps(game.packet("pilot:B", full=True)))

        valid = self._pitch(action, options[2], 2, hydra)
        self._reject(game, {**valid, "x": 1})
        self._reject(game, self._pitch(action, options[2], 2, source))
        self._reject(game, self._pitch(action, options[2], 2, colorless))
        self._reject(game, self._pitch(action, options[1], 1, other))
        self._reject(game, valid, seat="C")
        item = self._cast(game, valid, source, 2)
        self.assertEqual(0, item.context["mana_spent_total"])
        self.assertEqual("exile", game.state.cards[hydra.object_id].zone)
        self.assertEqual("hand", game.state.cards[big.object_id].zone)
        self.priority(game, "B")
        counter_offer = self.action(game, "B", counter)
        self.assertIn(item.ref, json.dumps(counter_offer["target_schema"]))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(42, game.state.players["A"].life)
        self.assertNotIn(big.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.replay(game, load=True)

    def test_actual_blazing_accepts_an_exact_zero_value_card_and_retains_printed_x(self):
        for use_pitch in (True, False):
            with self.subTest(pitch=use_pitch):
                game = self.session(77127002 + use_pitch)
                source = self.add(game.engine, "Blazing Shoal", zone="hand")
                zero = self.add(game.engine, "Kobolds of Kher Keep", zone="hand")
                bolt = self.add(game.engine, "Lightning Bolt", zone="hand")
                target = self.add(game.engine, "Grizzly Bears")
                self.assert_closed(game, source)
                self.prepare(game, mana={} if use_pitch else {"C": 2, "R": 2})
                self.checkpoint(game)
                action, options = self._options(game, source)
                self.assertEqual({0, 1}, options.keys())
                if use_pitch:
                    cast = self._pitch(action, options[0], 0, zero, [target.ref])
                    without_x = {key: item for key, item in cast.items() if key != "x"}
                    self._reject(game, without_x)
                    self._cast(game, cast, source, 0)
                    self.assertEqual("exile", game.state.cards[zero.object_id].zone)
                else:
                    normal, = [entry for entry in action["cost_options"] if entry["kind"] == "mana"]
                    self._cast(game, {"action_id": action["id"], "cost_option": normal["id"],
                                      "x": 2, "targets": [target.ref], "pay": "auto"}, source, 2)
                    self.assertEqual("hand", game.state.cards[zero.object_id].zone)
                self.assertEqual("hand", game.state.cards[bolt.object_id].zone)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(2 if use_pitch else 4, game.engine._numeric_stat(target.object_id, "power"))
                self.replay(game, load=True)

    def test_actual_sickening_rejects_same_mana_value_wrong_color_and_applies_negative_x(self):
        game = self.session(77127004)
        source = self.add(game.engine, "Sickening Shoal", zone="hand")
        black = self.add(game.engine, "Doom Blade", zone="hand")
        wrong_color = self.add(game.engine, "Runeclaw Bear", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        action, options = self._options(game, source)
        self.assertEqual({2}, options.keys())
        self._reject(game, self._pitch(action, options[2], 2, wrong_color, [target.ref]))
        self._reject(game, self._pitch(action, options[2], 2, black, ["B"]))
        self._cast(game, self._pitch(action, options[2], 2, black, [target.ref]), source, 2)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["exile", "hand", "graveyard"],
                         [game.state.cards[card.object_id].zone for card in (black, wrong_color, target)])
        self.replay(game, load=True)

    def test_omission_restores_the_three_independent_shoal_price_residuals(self):
        with patch("quorune.compiler.fixed_public_alternative_costs._ANNOUNCED_X_PITCH", re.compile(r"(?!)")):
            for name in PRICES:
                outcome, _ = register_card(self.db, self.registry, name)
                self.assertFalse(outcome["strict_capability_ready"], outcome)
            unrelated, _ = register_card(self.db, self.registry, "Revitalize")
            self.assertTrue(unrelated["strict_capability_ready"], unrelated["blockers"])

    def test_genuine_withdrawals_require_shoal_price_and_fixed_hand_exile_owners(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        original = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY, HAND_EXILE):
            with self.subTest(capability=capability):
                document = deepcopy(original)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Announced X pitch authority withdrawn"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in PRICES:
                    result, _ = register_card(self.db, registry, name)
                    self.assertFalse(result["strict_capability_ready"], result)
                retained, _ = register_card(self.db, registry, "Revitalize")
                self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

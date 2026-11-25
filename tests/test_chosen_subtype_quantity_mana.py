"""The original chosen creature type supplies a live paid mana output."""

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
from quorune.compiler.public_quantity_mana import chosen_subtype_mana_template, public_quantity_mana_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_quantity_mana_abilities import PublicQuantityActivatedManaAbilitySpec
from quorune.public_quantity_mana_model import (
    CHOSEN_SUBTYPE_QUANTITY_MANA_CAPABILITY as CAPABILITY,
    PublicQuantityManaOutput,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


CITY = "Three Tree City"
GATE = "Baldur's Gate"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class ChosenSubtypeQuantityManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Commander", "Generic Bound Plains", "Unsummon"},
            "creature-subtype-addition-cards.json": {"Goblin Piker", "Boggart Shenanigans", "Island"},
            "characteristic-query-union-cards.json": {"Universal Automaton"},
            "public-quantity-mana-cards.json": {GATE},
            "additional-conditional-modifier-cards.json": {"Divination"},
        }
        paths = [FIXTURES / "chosen-subtype-quantity-mana-cards.json"]
        for name, names in support.items():
            doc = json.loads((FIXTURES / name).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert {r["name"] for r in doc["cards"]} == names
            ids = {r["oracle_id"] for r in doc["cards"]}
            doc["rulings"] = [r for r in doc.get("rulings", []) if r["oracle_id"] in ids]
            target = folder / name
            target.write_text(json.dumps(doc), encoding="utf-8")
            paths.append(target)
        database = folder / "chosen-subtype-quantity-mana.sqlite3"
        build_fixture_database(paths, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Chosen Subtype Mana", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_exact_descriptor_and_activation_price_have_independent_scope(self):
        card = self.db.lookup(CITY, fuzzy=False)
        original = card.oracle_text.splitlines()[-1].split(": ", 1)[1]
        model = chosen_subtype_mana_template(original)
        self.assertEqual(2, model.schema_version)
        self.assertEqual(model, PublicQuantityManaOutput.from_dict(model.to_dict()))
        self.assertIsNone(public_quantity_mana_template(original, source_name=CITY))
        for update in ({"schema_version": True}, {"selection": "combination"}, {"colors": ["G", "R"]},
                       {"restriction": "creature_spell_only"}, {"extra": True}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                PublicQuantityManaOutput.from_dict({**model.to_dict(), **update})
        self.assertIsNone(chosen_subtype_mana_template(original.replace("creatures you", "permanents you")))
        self.assertIsNone(chosen_subtype_mana_template(original + " You gain 1 life."))

        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        report, programs = register_card(self.db, self.registry, CITY)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        quantity, = [p for p in programs.programs_for_oracle(card.oracle_id) if CAPABILITY in p.capability_dependencies]
        ability = PublicQuantityActivatedManaAbilitySpec.from_dict(quantity.handlers[0]["ability"])
        with self.assertRaises(ValueError):
            PublicQuantityActivatedManaAbilitySpec(replace(ability.costs, tap_source=False), ability.output)
        unlabelled = replace(quantity, coverage=[])
        self.assertTrue(bind_semantic_program_runtime(
            unlabelled, capability_registry=self.registry, profile=PROFILE,
        )["strict"])
        deps = tuple(c for c in quantity.capability_dependencies if c != CAPABILITY)
        no_owner = replace(unlabelled, capability_dependencies=deps,
                           capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(no_owner, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, bound["blockers"])

    def _reject(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _act(self, game, command):
        self._reject(game, "D", command)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def _start(self, game, mana):
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def _play(self, game, city):
        self.priority(game, "A")
        self._act(game, {"action_id": "play-land:" + city.ref})
        pending = game.state.pending_decision
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        for actor in "BCD":
            self.assertIsNone(game.packet("pilot:" + actor, full=True)["decision"])
        choices = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        chosen, = (row["id"] for row in choices if row["id"].endswith(":goblin"))
        self._act(game, {"action_id": "choose", "replacement": chosen})
        self.assertEqual("battlefield", game.state.cards[city.object_id].zone)
        self.assertEqual("goblin", game.state.cards[city.object_id].annotations["chosen_creature_type"])

    def _offered(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")]

    def _quantity_offer(self, game, source):
        row, = [action for action in self._offered(game, source)
                if (action.get("choice_schema") or {}).get("mana_output")]
        self.assertTrue(row.get("mana_ability"))
        return row

    def test_paid_city_rechecks_current_matching_creatures_in_its_exact_color_choice(self):
        game = self.session(60531501)
        city = self.add(game.engine, CITY, zone="hand")
        goblin = self.add(game.engine, "Goblin Piker")
        self.add(game.engine, "Universal Automaton")
        self.add(game.engine, "Boggart Shenanigans")
        self.add(game.engine, "Goblin Piker", seat="C", ref="foreign-city-goblin")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.assert_closed(game, city)
        self._start(game, {"C": 2})
        self._play(game, city)
        self.assertEqual(2, len(self._offered(game, city)))
        row = self._quantity_offer(game, city)
        self.assertEqual(2, row["choice_schema"]["mana_output"]["total"])
        self.cast(game, "B", bounce, [goblin.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[goblin.object_id].zone)
        current = self._quantity_offer(game, city)
        self.assertEqual(1, current["choice_schema"]["mana_output"]["total"])
        self.assertEqual(set("WUBRG"), set(current["choice_schema"]["mana_output"]["allowed_colors"]))
        command = {"action_id": current["id"], "pay": "manual", "payment": {"C": 2}}
        self._reject(game, "A", {**command, "mana_output": {"U": 2}})
        self._reject(game, "A", {**command, "mana_choice": "C"})
        self.assertFalse(game.state.cards[city.object_id].tapped)
        self._act(game, {**command, "mana_choice": "U"})
        self.assertEqual({"U": 1}, {key: value for key, value in game.state.players["A"].mana_pool.items() if value})
        self.assertTrue(game.state.cards[city.object_id].tapped)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_paid_city_remains_available_with_zero_matching_creatures(self):
        game = self.session(60531502)
        city = self.add(game.engine, CITY, zone="hand")
        self.add(game.engine, "Goblin Piker", seat="B")
        self.assert_closed(game, city)
        self._start(game, {"C": 2})
        self._play(game, city)
        row = self._quantity_offer(game, city)
        self.assertEqual(0, row["choice_schema"]["mana_output"]["total"])
        self._act(game, {"action_id": row["id"], "pay": "manual", "payment": {"C": 2}, "mana_choice": "G"})
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.assertTrue(game.state.cards[city.object_id].tapped)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_real_cast_pays_with_the_exact_priced_city_mode_manually_and_automatically(self):
        from quorune import public_quantity_mana_runtime as quantities

        game = self.session(60531503)
        city = self.add(game.engine, CITY, zone="hand")
        for index in range(3):
            self.add(game.engine, "Goblin Piker", ref=f"city-payment-goblin-{index}")
        spell = self.add(game.engine, "Divination", zone="hand")
        self.assert_closed(game, city)
        self.assert_closed(game, spell)
        self._start(game, {"C": 2})
        self._play(game, city)
        auto = self.replay(game, load=True)
        self.priority(game, "A")
        action = self.action(game, "A", spell)
        source, = [row for row in game.engine.available_mana_sources("A") if row.ref == city.ref]
        mode, = [row for row in source.modes if row.bundle.get("U") == 3 and row.price is not None]
        price = {"ability_id": mode.price.ability_id, "cost_option": mode.price.option.option_id,
                 "payment": {"C": 2}}
        step = {"source": city.ref, "bundle": {"U": 3}, "input": price}
        command = {"action_id": action["id"], "pay": "manual", "payment": {"U": 3}}
        self._reject(game, "A", {**command, "mana": [{"source": city.ref, "bundle": {"U": 3}}]})
        amount = quantities.public_quantity_mana_amount

        def changed_after_tap(host, seat, source, output, **kwargs):
            return 2 if source.tapped else amount(host, seat, source, output, **kwargs)

        with patch.object(quantities, "public_quantity_mana_amount", side_effect=changed_after_tap):
            self._reject(game, "A", {**command, "mana": [step]})
        for native, submission in ((game, {**command, "mana": [step]}),
                                   (auto, {"action_id": action["id"], "pay": "auto"})):
            self.priority(native, "A")
            self._act(native, submission)
            self.assertTrue(native.state.cards[city.object_id].tapped)
            self.assertEqual("stack", native.state.cards[spell.object_id].zone)
            self.assertFalse(any(native.state.players["A"].mana_pool.values()))
            self.assertIsNone(self.resolve(native))
            self.assertEqual("graveyard", native.state.cards[spell.object_id].zone)
            self.replay(native, load=True)

    def test_genuine_selected_quantity_withdrawal_preserves_the_distinct_gate_output(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = deepcopy(json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes()))
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Scoped selected creature mana authority withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        rejected, _ = register_card(self.db, registry, CITY)
        retained, _ = register_card(self.db, registry, GATE)
        self.assertFalse(rejected["strict_capability_ready"])
        self.assertTrue(retained["strict_capability_ready"], retained["blockers"])


if __name__ == "__main__":
    unittest.main()

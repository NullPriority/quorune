from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
COST = "activation.source_zone_change.fixed"
SOURCE = "damage.source.stack_current_or_lki"
NAME = "Goblin Skycutter"
FIXTURES = ROOT / "tests" / "fixtures"


class SourceSacrificeBoundDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        supports = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "announced-x-public-damage-set-cards.json": {"Air Elemental"},
            "attached-destruction-set-cards.json": {"Grizzly Bears"},
            "temporary-keyword-removal-cards.json": {"Canopy Claws"},
        }
        parts = [FIXTURES / "source-sacrifice-bound-damage-cards.json"]
        for filename, names in supports.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            oracle_ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in oracle_ids]
            part = folder / filename
            part.write_text(json.dumps(data), encoding="utf-8")
            parts.append(part)
        database = folder / "sacrifice-source-damage.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Paid source damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_original_binds_paid_source_and_the_same_announced_damage_and_removal_target(self):
        card = self.db.lookup(NAME, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual("activated_ability", node.kind)
        self.assertTrue(node.cost["sacrifice_source"])
        self.assertTrue({COST, SOURCE, "target.revalidate_resolution", "continuous.resolution.fixed_keyword_removal"}
            <= set(node.capability_dependencies))
        ready, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(("activate", "battlefield"), (program.event, program.active_zone))
        schema = thaw_value(program.target_schema)
        self.assertEqual((["creature"], ["flying"], 1), (schema["types_all"], schema["keywords_all"], schema["count"]))
        first, removal = thaw_value(program.effects)
        self.assertEqual({"op": "damage", "source": "$source", "target": "$target.0", "amount": 2}, first)
        self.assertEqual(("$target.0", ["Flying"]), (removal["card"], removal["remove_keywords"]))

    def test_other_sacrifice_and_other_riders_stay_residual(self):
        card = self.db.lookup(NAME, fuzzy=False)
        for changed in (card.oracle_text.replace("Sacrifice this creature", "Sacrifice a creature"),
                        card.oracle_text.replace("loses flying", "loses first strike"),
                        card.oracle_text.replace("target creature with flying", "target creature with reach")):
            with self.subTest(changed=changed):
                ir = compile_oracle_card(replace(card, oracle_text=changed), capability_registry=self.registry,
                                         capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def offered(self, game, source):
        return [row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                if row["id"].startswith("activate:" + source.ref + ":")]

    def test_actual_activation_has_no_offer_without_a_legal_flying_creature(self):
        game = self.session(76155310)
        source = self.add(game.engine, NAME)
        self.add(game.engine, "Grizzly Bears", seat="B")
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.assertEqual([], self.offered(game, source))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_actual_activation_sacrifices_the_controlled_source_before_lki_damage(self):
        game = self.session(76155311)
        source = self.add(game.engine, NAME)
        source.owner = "C"
        flyer = self.add(game.engine, "Air Elemental", seat="B")
        ground = self.add(game.engine, "Grizzly Bears", seat="B")
        private = self.add(game.engine, "Canopy Claws", seat="D", zone="hand", ref="skycut-private")
        self.assert_closed(game, source)
        original = source.logical_object_id
        self.prepare(game)
        self.checkpoint(game)
        offered, = self.offered(game, source)
        command = {"action_id": offered["id"], "targets": [flyer.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertFalse(game.act("pilot:A", {**command, "targets": [ground.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        paid = game.act("pilot:A", command)
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertIn(source.object_id, game.state.players["C"].zones["graveyard"])
        self.assertNotEqual(original, game.state.cards[source.object_id].logical_object_id)
        self.assertEqual(original, game.state.stack[-1].context["source_logical_object_id"])
        self.assertEqual(0, game.state.cards[flyer.object_id].marked_damage)

        game = self.replay(game, load=True)
        observed = []
        dispatch = game.engine._dispatch_semantic_event

        def observe(event, context, **kwargs):
            if event == "damage.dealt" and context.get("target_object_id") == flyer.object_id:
                current = game.state.cards[flyer.object_id]
                observed.append((context, frozenset(game.engine._combat_keywords(current))))
            return dispatch(event, context, **kwargs)

        with patch.object(game.engine, "_dispatch_semantic_event", side_effect=observe):
            self.assertIsNone(self.resolve(game))
        (fact, keywords), = observed
        self.assertEqual((original, "A", "C", 2),
            (fact["source_logical_object_id"], fact["source_controller"], fact["source_owner"], fact["amount"]))
        self.assertIn("flying", keywords)
        current = game.state.cards[flyer.object_id]
        self.assertEqual(2, current.marked_damage)
        self.assertNotIn("flying", game.engine._combat_keywords(current))
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_paid_sacrifice_stays_committed_when_the_target_loses_flying(self):
        game = self.session(76155312)
        source = self.add(game.engine, NAME)
        flyer = self.add(game.engine, "Air Elemental", seat="B")
        remove = self.add(game.engine, "Canopy Claws", seat="B", zone="hand")
        for card in (source, remove):
            self.assert_closed(game, card)
        self.prepare(game)
        game.state.players["B"].mana_pool["G"] = 1
        self.checkpoint(game)
        action, = self.offered(game, source)
        paid = game.act("pilot:A", {"action_id": action["id"], "targets": [flyer.ref], "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.cast(game, "B", remove, [flyer.ref])
        self.resolve_top(game)
        self.assertNotIn("flying", game.engine._combat_keywords(game.state.cards[flyer.object_id]))
        effects = [effect.to_dict() for effect in game.state.continuous_effects]
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(0, game.state.cards[flyer.object_id].marked_damage)
        self.assertEqual(effects, [effect.to_dict() for effect in game.state.continuous_effects])
        self.replay(game, load=True)

    def test_leaving_the_source_pronoun_unbound_blocks_original_registration(self):
        original = self.db.lookup(NAME, fuzzy=False)
        unchanged = original.oracle_text.partition(": ")[2]
        with patch("quorune.compiler.activated_mana_nodes._activated_effect_material", return_value=unchanged):
            bound, _ = register_card(self.db, self.registry, NAME)
        self.assertFalse(bound["strict_capability_ready"], bound)

    def test_verified_withdrawals_require_the_price_and_last_known_source(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap in (COST, SOURCE):
            with self.subTest(cap=cap):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Source sacrifice or LKI authority withdrawn"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                binding, _ = register_card(self.db, registry, NAME)
                self.assertFalse(binding["strict_capability_ready"], binding["blockers"])
                control, _ = register_card(self.db, registry, "Grizzly Bears")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])

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
from quorune.continuous_conditions import FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.granted_characteristic_shapes import granted_characteristic_handlers_are_closed
from quorune.static_component_scope import GRANTED_STATIC_CHARACTERISTIC_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_granted_static_characteristics as existing


AMULET = "Giant's Amulet"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINALS = FIXTURES / "granted-untapped-hexproof-cards.json"
CONDITION = "continuous.characteristics.fixed_public_state"


class GrantedUntappedHexproofTests(InheritedNativeFixture, unittest.TestCase):
    stats = existing.GrantedStaticCharacteristicNativeTests.stats

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selected = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "untargeted-untap-cards.json": {
                "Isamaru, Hound of Konda", "Island", "Grizzly Bears", "Twiddle", "Unsummon", "Shock",
            },
            "granted-static-characteristic-cards.json": {"Inspiring Leader"},
        }
        fixtures = [ORIGINALS]
        for name, names in selected.items():
            fixture = json.loads((FIXTURES / name).read_bytes())
            fixture["cards"] = [card for card in fixture["cards"] if card["name"] in names]
            assert {card["name"] for card in fixture["cards"]} == names
            ids = {card["oracle_id"] for card in fixture["cards"]}
            fixture["rulings"] = [row for row in fixture.get("rulings", ()) if row["oracle_id"] in ids]
            output = folder / name
            output.write_text(json.dumps(fixture), encoding="utf-8")
            fixtures.append(output)
        path = folder / "granted-untapped-hexproof.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Conditional attached grant", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 28),
        ], ["Isamaru, Hound of Konda"])

    def test_original_complete_graph_retains_paid_entry_equip_and_conditional_child(self):
        card = self.db.lookup(AMULET, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        entry, host, child, equip = ir.faces[0].nodes
        self.assertEqual("permanent.enter.self", entry.event)
        self.assertIn("effect.choice.optional_fixed_mana_payment", entry.capability_dependencies)
        self.assertIn("attachment.action.fixed_source", entry.capability_dependencies)
        self.assertIn("attachment.equip.fixed_mana", equip.capability_dependencies)
        self.assertEqual("granted_static_ability", child.kind)
        self.assertEqual("characteristics.evaluate", child.event)
        self.assertEqual(1, host.handlers[0]["modifier"]["toughness"])
        self.assertEqual(0, host.handlers[0]["modifier"]["power"])
        self.assertEqual([], host.handlers[0]["modifier"]["add_abilities"])
        self.assertEqual(1, len(host.handlers[0]["modifier"]["add_ability_fragments"]))
        cap = {GRANTED_STATIC_CHARACTERISTIC_CAPABILITY, CONDITION, "target.protection.hexproof_permanent"}
        self.assertTrue(cap <= set(child.capability_dependencies))
        ready, semantics = register_card(self.db, self.registry, AMULET)
        self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id)
            if item.handlers and item.handlers[0]["handler_id"] == FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID]
        original, = program.handlers
        self.assertTrue(granted_characteristic_handlers_are_closed((original,)))
        for replacement in (
            {"modifier": {**original["modifier"], "add_abilities": ["Shroud"]}},
            {"modifier": {**original["modifier"], "power": 1}},
            {"source_condition": {**original["source_condition"], "predicate": {
                **original["source_condition"]["predicate"], "state_predicate": {
                    **original["source_condition"]["predicate"]["state_predicate"], "tapped": True,
                },
            }}},
            {"target": {**original["target"], "kind": "attached", "types_all": ["creature"]}},
        ):
            with self.subTest(replacement=replacement):
                changed = {**original, **replacement}
                self.assertFalse(granted_characteristic_handlers_are_closed((changed,)))
                result = bind_semantic_program_runtime(replace(program, handlers=[changed]),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)

        for quote in (
            "This creature has shroud as long as it's untapped.",
            "This creature has hexproof as long as it's tapped.",
            "This creature has hexproof as long as it's untapped. Draw a card.",
        ):
            altered = replace(card, oracle_text=card.oracle_text.replace(
                "This creature has hexproof as long as it's untapped.", quote))
            self.assertNotEqual("exact", compile_oracle_card(altered,
                capability_registry=self.registry, capability_profile=PROFILE).status, quote)

    def hexproof(self, game, card):
        row = game.state.cards[card.object_id]
        return "Hexproof" in game.engine._effective_card_data(row)["keywords"]

    def test_actual_paid_token_tap_and_equip_change_opponent_target_legality(self):
        game = self.session(70211043)
        equipment = self.add(game.engine, AMULET, zone="hand")
        body = self.add(game.engine, "Grizzly Bears", ref="amulet-equip-receiver")
        tap = self.add(game.engine, "Twiddle", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        hidden = self.add(game.engine, "Shock", seat="D", zone="hand", ref="amulet-private")
        self.add(game.engine, "Island", ref="amulet-manual-blue")
        self.add(game.engine, "Island", seat="B", ref="amulet-opponent-blue")
        for index in range(2):
            self.add(game.engine, "Generic Bound Plains", ref=f"amulet-equip-mana-{index}")
        self.assert_closed(game, equipment)
        self.prepare(game, mana={"C": 3, "U": 2})
        self.checkpoint(game)
        self.cast(game, "A", equipment)
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual("semantic.choice", pending.kind)
        self.assertEqual(["A"], pending.actors)
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "pay": True}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": "choose", "pay": True})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

        token, = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        base = token.annotations["token_characteristics"]
        self.assertEqual(("4", "4"), (str(base["power"]), str(base["toughness"])))
        self.assertIn("Giant Wizard", base["type_line"])
        self.assertEqual(token.object_id, game.state.cards[equipment.object_id].attached_to)
        self.assertEqual((4, 5), self.stats(game, token))
        self.assertTrue(self.hexproof(game, token), "The untapped equipped creature must gain hexproof")
        self.assertFalse(self.hexproof(game, body))

        self.cast(game, "A", tap, [token.ref])
        self.priority(game, "B")
        offered = self.action(game, "B", bounce)
        self.assertNotIn(token.ref, offered["target_schema"]["legal_refs"])
        self.assertIn(body.ref, offered["target_schema"]["legal_refs"])
        command = {"action_id": offered["id"], "targets": [token.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        self.assertEqual("semantic.choice", self.resolve(game).kind)
        chosen = game.act("pilot:A", {"action_id": "choose", "choice": "tap"})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertTrue(game.state.cards[token.object_id].tapped)
        self.assertFalse(game.state.cards[equipment.object_id].tapped)
        self.assertFalse(self.hexproof(game, token))
        self.assertEqual((4, 5), self.stats(game, token))

        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        equip, = [row for row in actions if row["id"].startswith("activate:" + equipment.ref + ":")]
        equipped = game.act("pilot:A", {"action_id": equip["id"], "targets": [body.ref], "pay": "auto"})
        self.assertTrue(equipped.ok, equipped.summary)
        self.priority(game, "B")
        self.assertEqual(token.object_id, game.state.cards[equipment.object_id].attached_to)
        newly_legal = self.action(game, "B", bounce)["target_schema"]["legal_refs"]
        self.assertIn(token.ref, newly_legal)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(body.object_id, game.state.cards[equipment.object_id].attached_to)
        self.assertEqual((2, 3), self.stats(game, body))
        self.assertEqual((4, 4), self.stats(game, token))
        self.assertTrue(self.hexproof(game, body))
        self.priority(game, "B")
        renewed = self.action(game, "B", bounce)
        self.assertIn(token.ref, renewed["target_schema"]["legal_refs"])
        self.assertNotIn(body.ref, renewed["target_schema"]["legal_refs"])
        self.cast(game, "B", bounce, [token.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("outside", game.state.cards[token.object_id].zone)
        for seat in "ABC":
            self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_removing_the_granted_static_child_breaks_the_paid_original(self):
        with patch("quorune.rules.granted_characteristic_shapes.granted_characteristic_occurrences", return_value=()):
            with self.assertRaisesRegex(AssertionError, "untapped equipped creature must gain hexproof"):
                self.test_actual_paid_token_tap_and_equip_change_opponent_target_legality()

    def test_verified_withdrawals_separate_regular_static_grants_and_the_untapped_condition(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        names = (AMULET, "Inspiring Leader", "Grizzly Bears")
        baseline = verified(deepcopy(source))
        for name in names:
            binding, _ = register_card(self.db, baseline, name)
            self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        for capability, blocked in (
            (CONDITION, {AMULET}),
            (GRANTED_STATIC_CHARACTERISTIC_CAPABILITY, {AMULET, "Inspiring Leader"}),
        ):
            with self.subTest(capability=capability):
                rows = deepcopy(source)
                next(row for row in rows["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Conditional grant dependency withdrawn"])
                registry = verified(rows)
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in names:
                    ready, _ = register_card(self.db, registry, name)
                    self.assertEqual(name not in blocked, ready["strict_capability_ready"],
                        (name, ready["blockers"]))

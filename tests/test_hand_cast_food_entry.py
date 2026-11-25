"""Native evidence for a fixed Food result on a retained own-hand cast entry."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.token_templates import fixed_token_creation_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules import token_creation_capability_shapes as token_shapes
from quorune.rules.capabilities import load_default_capability_registry
from quorune.source_entry_cast_information import (
    SOURCE_ENTRY_CAST_CAPABILITY, SOURCE_ENTRY_CAST_HAND_CAPABILITY,
    SOURCE_ENTRY_CAST_HAND_MECHANIC, SOURCE_ENTRY_CAST_MECHANIC,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


TROLL = "Feasting Troll King"
FOOD = "token.creation.fixed_definition"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class HandCastFoodEntryTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activation-restriction-material-cards.json": {"Murder", "Sol Ring"},
            "amplify-cards.json": {"Clone"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth"},
            "entry-value-consumer-cards.json": {"Elvish Piper"},
            "fuse-cards.json": {"Reckless Impulse"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "source-entry-cast-cards.json": {"Scion of Vitu-Ghazi"},
        }
        pieces = [FIXTURES / "hand-cast-food-entry-cards.json"]
        for name, names in support.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [row for row in document["cards"] if row["name"] in names]
            assert names == {row["name"] for row in document["cards"]}, name
            ids = {row["oracle_id"] for row in document["cards"]}
            document["rulings"] = [row for row in document.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(document), encoding="utf-8")
            pieces.append(part)
        path = folder / "hand-cast-food-entry.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Hand-cast Food entry", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self):
        card = self.db.lookup(TROLL, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, TROLL)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        return card, tuple(semantics.programs_for_oracle(card.oracle_id))

    def _offer(self, game, seat, prefix):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["id"].startswith(prefix)]
        return offer["id"]

    def _accept(self, game, seat, command, *, rejected="D"):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + rejected, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def _cast_paid(self, game, card, payment):
        quote = self._offer(game, "A", f"cast:{card.ref}")
        self._accept(game, "A", {"action_id": quote, "pay": "manual", "payment": payment})

    def _foods(self, game, seat="A"):
        return [
            card for card in game.state.cards.values()
            if card.zone == "battlefield" and card.controller == seat and card.is_token
            and "food" in game.engine._type_parts(game.engine._effective_card_data(card)["type_line"])[1]
        ]

    def _entry_receipt(self, game, source):
        trigger, = [item for item in game.state.stack if item.source_object_id == source.object_id]
        snapshot = trigger.context["source_entry_cast_information"]
        cast = snapshot["cast"]
        self.assertEqual(("A", "hand", "A"), (cast["caster"], cast["origin"], cast["origin_owner"]))
        return snapshot

    def test_fixed_food_leaf_and_cast_envelope_have_independent_closed_requirements(self):
        food = fixed_token_creation_effect_template("Create three Food tokens.")
        mechanics = {*food.mechanics, SOURCE_ENTRY_CAST_MECHANIC, SOURCE_ENTRY_CAST_HAND_MECHANIC,
                     "cr-603-handling-triggered-abilities", "trigger-event-normalized-zone-change"}
        validate = token_shapes.fixed_token_creation_node_capabilities
        self.assertEqual((FOOD, "life.change.effect"),
                         validate(effects=(food.effect,), target_schema=None, mechanic_ids=mechanics))
        unknown = set(mechanics) | {"unknown-token-qualifier"}
        self.assertEqual((), validate(effects=(food.effect,), target_schema=None, mechanic_ids=unknown))
        changed = deepcopy(food.effect)
        changed["characteristics"]["activated_ability_profile"] = "unsupported_profile"
        self.assertEqual((), validate(effects=(changed,), target_schema=None, mechanic_ids=mechanics))

    def test_original_registration_retains_guard_food_definition_keywords_and_graveyard_activation(self):
        record, programs = self._original()
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        entry, = [p for p in programs if p.event == "permanent.enter.self"]
        self.assertLessEqual({SOURCE_ENTRY_CAST_CAPABILITY, SOURCE_ENTRY_CAST_HAND_CAPABILITY, FOOD, "life.change.effect"},
                            set(entry.capability_dependencies))
        self.assertEqual("hand", entry.event_condition["origin"])
        effect, = entry.effects
        self.assertEqual(("create_token", "Food Token", 3), (effect["op"], effect["name"], effect["quantity"]))
        self.assertTrue(any(p.event == "activate" and p.active_zone == "graveyard" for p in programs))
        self.assertTrue(any({"combat.attack.vigilance", "combat.damage.assignment.trample"} <= set(p.capability_dependencies)
                            for p in programs))

    def test_actual_hand_cast_retains_its_food_trigger_after_departure_and_pays_the_printed_return(self):
        game = self.session(6486310)
        source = self.add(game.engine, TROLL, zone="hand")
        murder = self.add(game.engine, "Murder", seat="B", zone="hand")
        self.prepare(game, mana={"C": 2, "G": 4})
        game.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        self.checkpoint(game)
        self._cast_paid(game, source, {"C": 2, "G": 4})
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertLessEqual({"vigilance", "trample"}, set(game.engine._combat_keywords(source)))
        snapshot = self._entry_receipt(game, source)
        self.cast(game, "B", murder, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(snapshot, self._entry_receipt(game, source))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        foods = self._foods(game)
        self.assertEqual(3, len(foods))
        quote = self._offer(game, "A", f"activate:{source.ref}:")
        pay = {"action_id": quote, "pay": "manual", "payment": {},
               "cost_cards": [food.ref for food in foods]}
        before = authoritative_state_hash(game.state)
        invalid = dict(pay, cost_cards=[foods[0].ref] * 3)
        self.assertFalse(game.act("pilot:A", invalid).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self._accept(game, "A", pay)
        self.assertEqual([], self._foods(game))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual([], self._foods(game))
        self.assertEqual(0, self.trigger_count(game, source))
        self.replay(game, load=True)

    def test_actual_clone_uses_its_own_hand_cast_and_produces_ordinary_usable_food(self):
        game = self.session(6486320)
        original = self.add(game.engine, TROLL, seat="B")
        clone = self.add(game.engine, "Clone", zone="hand")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="food-private")
        self.prepare(game, mana={"C": 5, "U": 1})
        self.checkpoint(game)
        self._cast_paid(game, clone, {"C": 3, "U": 1})
        decision = self.resolve(game)
        self.assertEqual("replacement.order", decision.kind)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        copy, = [row["id"] for row in options if original.ref in row["id"] and not row["id"].startswith("decline:")]
        game = self.replay(game, load=True)
        self._accept(game, "A", {"action_id": "choose", "replacement": copy})
        self._entry_receipt(game, clone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(TROLL, game.engine._effective_card_data(game.state.cards[clone.object_id])["name"])
        foods = self._foods(game)
        self.assertEqual(3, len(foods))
        self.assertEqual([], self._foods(game, "B"))
        quote = self._offer(game, "A", f"activate:{foods[0].ref}:")
        self._accept(game, "A", {"action_id": quote, "pay": "manual", "payment": {"C": 2}})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(43, game.state.players["A"].life)
        self.assertEqual(2, len(self._foods(game)))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_actual_put_from_hand_requires_no_food_trigger_without_a_cast(self):
        game = self.session(6486330)
        source = self.add(game.engine, TROLL, zone="hand")
        piper = self.add(game.engine, "Elvish Piper")
        piper.acquired_control_turn_count = -1
        self.prepare(game, mana={"G": 1})
        self.checkpoint(game)
        quote = self._offer(game, "A", f"activate:{piper.ref}:")
        self._accept(game, "A", {"action_id": quote, "pay": "manual", "payment": {"G": 1}})
        choice = self.resolve(game)
        self.assertIsNotNone(choice)
        placement = {"action_id": "choose", "card": source.ref}
        game = self.replay(game, load=True)
        self._accept(game, "A", placement)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual([], self._foods(game))
        self.assertEqual(0, self.trigger_count(game, source))
        self.replay(game, load=True)

    def test_actual_cast_from_exile_does_not_supply_the_hand_origin(self):
        game = self.session(6486340)
        source = self.add(game.engine, TROLL, zone="library")
        impulse = self.add(game.engine, "Reckless Impulse", zone="hand")
        library = game.state.players["A"].zones["library"]
        library.remove(source.object_id)
        library.append(source.object_id)
        self.prepare(game, mana={"C": 3, "R": 1, "G": 4})
        self.checkpoint(game)
        self._cast_paid(game, impulse, {"C": 1, "R": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        self._cast_paid(game, source, {"C": 2, "G": 4})
        self.assertEqual("exile", game.state.stack[-1].context["cast_origin"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual([], self._foods(game))
        self.replay(game, load=True)

    def test_omitting_exact_auxiliary_cast_mechanics_restores_the_material_result_residual(self):
        restored = token_shapes._AUXILIARY_MECHANICS - {SOURCE_ENTRY_CAST_MECHANIC, SOURCE_ENTRY_CAST_HAND_MECHANIC}
        with patch.object(token_shapes, "_AUXILIARY_MECHANICS", restored):
            record = self.db.lookup(TROLL, fuzzy=False)
            ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
            residual, = ir.material_residuals
            self.assertIn("create three Food tokens", residual.text)
            report, _ = register_card(self.db, self.registry, TROLL)
            self.assertFalse(report["strict_capability_ready"], report)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_food_or_hand_condition_withdrawals_deny_the_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for dependency in (FOOD, SOURCE_ENTRY_CAST_HAND_CAPABILITY):
            with self.subTest(dependency=dependency):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == dependency).update(
                    status="blocked", blockers=["Scoped hand-cast Food authority withdrawal"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((dependency,), profile=PROFILE)
                self.assertIn(f"status:{dependency}:blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                report, _ = register_card(self.db, registry, TROLL)
                self.assertFalse(report["strict_capability_ready"], report)
                control, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()

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
from quorune.rules.capabilities import load_default_capability_registry
from quorune.static_component_scope import GRANTED_STATIC_CHARACTERISTIC_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SEDGE = "Sedge Sliver"
SLIVER = "Metallic Sliver"
PROFILE = "commander_review"
PUBLIC_STATE_CAPABILITY = "continuous.characteristics.fixed_public_state"
REGENERATION_CAPABILITY = "permanent.regeneration.self_activation"
FIXTURES = ROOT / "tests" / "fixtures"


class GrantedSwampCharacteristicTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selections = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "attached-destruction-set-cards.json": {"Shock", "Unsummon", "Murder", "Island", "Swamp"},
            "live-modifier-quantity-cards.json": {SLIVER},
        }
        fixtures = [FIXTURES / "granted-swamp-characteristic-cards.json"]
        for name, names in selections.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert {card["name"] for card in data["cards"]} == names
            ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            selected = folder / name
            selected.write_text(json.dumps(data), encoding="utf-8")
            fixtures.append(selected)
        path = folder / "granted-swamp-characteristic.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Granted recipient Swamp characteristic", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_separate_swamp_child_and_regeneration(self):
        card = self.db.lookup(SEDGE, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        nodes = ir.faces[0].nodes
        self.assertEqual(4, len(nodes))
        child, = [n for n in nodes if n.template_id == "continuous-fixed-public-state-characteristics-v1"]
        owner, = child.handlers
        condition = owner["source_condition"]
        self.assertEqual(("query_count_at_least", 1, "controller_zone"),
            (condition["kind"], condition["amount"], condition["quantity"]["scope"]))
        self.assertEqual((["land"], ["swamp"]),
            (condition["quantity"]["query"]["types_all"], condition["quantity"]["query"]["subtypes_all"]))
        self.assertEqual(("source", 1, 1),
            (owner["target"]["kind"], owner["modifier"]["power"], owner["modifier"]["toughness"]))
        self.assertTrue({GRANTED_STATIC_CHARACTERISTIC_CAPABILITY, PUBLIC_STATE_CAPABILITY}
            <= set(child.capability_dependencies))
        regeneration, = [n for n in nodes if n.template_id == "regenerate-this-permanent-v1"]
        self.assertIn(REGENERATION_CAPABILITY, regeneration.capability_dependencies)
        self.assertEqual(1, regeneration.cost["mana"]["B"])
        bound, _ = register_card(self.db, self.registry, SEDGE)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])

        for old, new in (
            ("a Swamp", "an Island"), ("a Swamp", "two Swamps"),
            ("+1/+1", "+2/+2"), ("+1/+1 as", "+1/+1 and has flying as"),
            ("you control a Swamp", "an opponent controls a Swamp"),
        ):
            with self.subTest(new=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                rejected = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", rejected.status)

    @staticmethod
    def pt(game, card):
        return tuple(game.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness"))

    def test_actual_swamp_bonus_is_recipient_relative_cumulative_and_live(self):
        game = self.session(54420911)
        first = self.add(game.engine, SEDGE, zone="hand")
        self.add(game.engine, SEDGE, seat="B", ref="earlier-sedge-source")
        allied = self.add(game.engine, SLIVER, ref="sliver-before-swamp")
        foreign = self.add(game.engine, SLIVER, seat="B", ref="sliver-with-shield")
        damaged = self.add(game.engine, SLIVER, seat="B", ref="sliver-with-marked-damage")
        dual = self.add(game.engine, "Badlands", seat="B")
        self.add(game.engine, "Island")
        own_swamp = self.add(game.engine, "Swamp", zone="hand")
        shock = self.add(game.engine, "Shock", zone="hand")
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        change_type = self.add(game.engine, "Sea's Claim", zone="hand")
        private = self.add(game.engine, "Murder", seat="D", zone="hand", ref="sedge-private-hand")
        for card in (first, allied, dual, shock, bounce, change_type):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "R": 2, "U": 2})
        self.checkpoint(game)
        self.assertEqual(((1, 1), (2, 2)), (self.pt(game, allied), self.pt(game, foreign)))

        self.cast(game, "A", first)
        self.assertIsNone(self.resolve(game))
        first = game.state.cards[first.object_id]
        self.assertEqual("battlefield", first.zone)
        self.assertEqual(((1, 1), (3, 3), (3, 3)),
            (self.pt(game, allied), self.pt(game, foreign), self.pt(game, damaged)))

        self.priority(game, "B")
        rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        regeneration = next(row["id"] for row in rows if row["id"].startswith("activate:" + foreign.ref + ":"))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": regeneration, "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:B", {"action_id": regeneration, "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(game.state.cards[dual.object_id].tapped)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[foreign.object_id].regeneration_shields)
        self.assertEqual((3, 3), self.pt(game, foreign))

        self.cast(game, "A", shock, [damaged.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[damaged.object_id].zone)
        self.assertEqual(2, game.state.cards[damaged.object_id].marked_damage)
        self.cast(game, "A", bounce, [first.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[first.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[damaged.object_id].zone)
        self.assertEqual((2, 2), self.pt(game, foreign))

        self.cast(game, "A", change_type, [dual.ref])
        self.assertIsNone(self.resolve(game))
        effective = game.engine._effective_card_data(game.state.cards[dual.object_id])
        self.assertEqual({"island"}, game.engine._type_parts(effective["type_line"])[1])
        self.assertEqual((1, 1), self.pt(game, foreign))

        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        land_offer, = [row for row in actions if row["id"] == "play-land:" + own_swamp.ref]
        played = game.act("pilot:A", {"action_id": land_offer["id"]})
        self.assertTrue(played.ok, played.summary)
        self.assertEqual(((2, 2), (1, 1)), (self.pt(game, allied), self.pt(game, foreign)))
        for seat in "ABC":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(private.ref, packet)
            self.assertNotIn(private.printed_name, packet)
        self.replay(game, load=True)

    def test_omitting_live_granted_occurrences_breaks_the_actual_swamp_bonus(self):
        game = self.session(54420912)
        source = self.add(game.engine, SEDGE, zone="hand")
        target = self.add(game.engine, SLIVER, seat="B")
        self.add(game.engine, "Badlands", seat="B")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2, "R": 1})
        self.checkpoint(game)
        with patch("quorune.characteristic_evaluation_host.CharacteristicEvaluationHostMixin._granted_characteristic_occurrences",
                   return_value=()):
            self.cast(game, "A", source)
            self.assertIsNone(self.resolve(game))
            with self.assertRaisesRegex(AssertionError, "The receiving Sliver checks its own controller"):
                self.assertEqual((2, 2), self.pt(game, target), "The receiving Sliver checks its own controller")

    def test_verified_withdrawal_keeps_the_independent_regeneration_child(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())

        def verify(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        baseline = verify(deepcopy(raw))
        ready, _ = register_card(self.db, baseline, SEDGE)
        self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        for cap in (GRANTED_STATIC_CHARACTERISTIC_CAPABILITY, PUBLIC_STATE_CAPABILITY):
            with self.subTest(cap=cap):
                rows = deepcopy(raw)
                next(item for item in rows["capabilities"] if item["id"] == cap).update(
                    status="blocked", blockers=["Granted Swamp modifier requirement withdrawn"])
                registry = verify(rows)
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in x for x in closure.blockers))
                unseen, _ = register_card(self.db, registry, SEDGE)
                self.assertFalse(unseen["strict_capability_ready"], unseen)
                control, _ = register_card(self.db, registry, SLIVER)
                self.assertTrue(control["strict_capability_ready"], control["blockers"])
                card = self.db.lookup(SEDGE, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=registry, capability_profile=PROFILE)
                self.assertTrue(any(REGENERATION_CAPABILITY in n.capability_dependencies for n in ir.faces[0].nodes))

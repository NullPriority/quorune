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
from quorune.compiler.activated_mana_nodes import _CAPABILITY_SHAPED_EFFECT_OPERATIONS
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.tap_state_capability_shapes import targeted_tap_state_node_capabilities
from quorune.tap_state import NEXT_UNTAP_PROHIBITION_ANNOTATION
from quorune.turn_step_owner import TURN_STEPS
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_temporary_keyword_removal as native_turn


ORIGINALS = (
    "Sleeper Dart", "House Guildmage", "Elvish Hunter", "Fifty Feet of Rope", "Barl's Cage", "Ajani Vengeant",
)
CAPABILITY = "untap.step.prohibit_next_controller"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class TargetedControllerUntapTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selections = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "announced-x-activation-cards.json": {"Burst of Energy"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Mountain", "Island", "Murder", "Unsummon"},
        }
        fixtures = [FIXTURES / "targeted-controller-untap-cards.json"]
        for name, names in selections.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert {card["name"] for card in data["cards"]} == names
            ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            selected = folder / name
            selected.write_text(json.dumps(data), encoding="utf-8")
            fixtures.append(selected)
        path = folder / "targeted-controller-untap.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Targeted next controller untap", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_six_complete_originals_use_the_typed_target_result_and_keep_printed_costs(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                c = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(c, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                nodes = ir.faces[0].nodes
                ability, = [n for n in nodes if any(e.get("op") == "prohibit_next_untap" for e in n.effects)]
                self.assertEqual(({"op": "prohibit_next_untap", "card": "$target.0"},), ability.effects)
                self.assertTrue({CAPABILITY, "target.revalidate_resolution"} <= set(ability.capability_dependencies))
                expected = {"zones": ["battlefield"], "categories": ["permanent"], "count": 1}
                if name != "Ajani Vengeant":
                    expected["types_any"] = ["creature"]
                self.assertEqual(expected, ability.target_schema)
                self.assertEqual(name in {"Sleeper Dart", "House Guildmage", "Elvish Hunter", "Fifty Feet of Rope"},
                    ability.cost["tap_source"])
                if name == "Sleeper Dart":
                    self.assertTrue(ability.cost["sacrifice_source"])
                    self.assertIn("activation.source_zone_change.fixed", ability.capability_dependencies)
                    self.assertTrue(any(e.get("op") == "draw" for n in nodes for e in n.effects))
                elif name == "Ajani Vengeant":
                    self.assertEqual(1, ability.cost["loyalty_delta"])
                    self.assertEqual({1, -2, -7}, {n.cost["loyalty_delta"] for n in nodes})
                elif name == "House Guildmage":
                    self.assertTrue(any(e.get("op") == "surveil" for n in nodes for e in n.effects))
                elif name == "Fifty Feet of Rope":
                    self.assertEqual(3, len(nodes))
                ready, _ = register_card(self.db, self.registry, name)
                self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        valid = ({"op": "prohibit_next_untap", "card": "$target.0"},)
        schema = {"zones": ["battlefield"], "categories": ["permanent"], "count": 1, "types_any": ["creature"]}
        for effects, target in (
            (({**valid[0], "turns": 2},), schema),
            (({**valid[0], "card": "$target.1"},), schema),
            (valid, {**schema, "zones": ["graveyard"]}),
        ):
            with self.subTest(effects=effects, target=target):
                self.assertEqual((), targeted_tap_state_node_capabilities(
                    effects=effects, target_schema=target, mechanic_ids=("tap-and-untap", "cr-115-targets")))

    def offer(self, game, source, *, ability_id=None):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        prefix = "activate:" + source.ref + ":"
        action, = [row for row in actions if row["id"].startswith(prefix) and row.get("target_schema")
            and (ability_id is None or row["id"] == prefix + ability_id)]
        return action

    def activate(self, game, source, target, *, ability_id=None):
        action = self.offer(game, source, ability_id=ability_id)
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        accepted = game.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)

    @staticmethod
    def restriction(game, card):
        return game.state.cards[card.object_id].annotations.get(NEXT_UNTAP_PROHIBITION_ANNOTATION)

    def test_actual_dart_and_cage_track_the_receiving_controller_and_expire_at_one_step(self):
        game = self.session(36151411)
        dart = self.add(game.engine, "Sleeper Dart", zone="hand")
        cage = self.add(game.engine, "Barl's Cage")
        gift = self.add(game.engine, "Harmless Offering", zone="hand")
        timing = self.add(game.engine, "Vedalken Orrery")
        victim = self.add(game.engine, "Grizzly Bears", ref="transferred-untap-target")
        victim.tapped = True
        idle = self.add(game.engine, "Grizzly Bears", seat="B", ref="untapped-restriction-target")
        burst = self.add(game.engine, "Burst of Energy", seat="B", zone="hand")
        private = self.add(game.engine, "Murder", seat="D", zone="hand", ref="controller-untap-private")
        self.add(game.engine, "Mountain")
        self.add(game.engine, "Generic Bound Plains", seat="B", ref="ordinary-white-source")
        control_land = self.add(game.engine, "Generic Bound Plains", seat="B", ref="ordinary-next-untap")
        control_land.tapped = True
        for card in (dart, cage, gift, timing, burst):
            self.assert_closed(game, card)
        game.engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.started = True
        game.state.active_player = game.state.last_normal_turn_player = "A"
        game.state.phase, game.state.step = ("ending", "end_step")
        game.state.phase_index = TURN_STEPS.index((game.state.phase, game.state.step))
        game.state.players["A"].mana_pool["C"] = 7
        game.engine._grant_priority("A")
        game.engine.pump()
        self.checkpoint(game)

        library_before = len(game.state.players["A"].zones["library"])
        self.cast(game, "A", dart)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(library_before - 1, len(game.state.players["A"].zones["library"]))
        dart = game.state.cards[dart.object_id]
        self.activate(game, dart, victim)
        self.assertEqual("graveyard", game.state.cards[dart.object_id].zone)
        self.assertIsNone(self.restriction(game, victim))
        self.assertIsNone(self.resolve(game))
        self.assertIs(self.restriction(game, victim), True)

        self.activate(game, cage, idle)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[cage.object_id].tapped)
        self.assertFalse(game.state.cards[idle.object_id].tapped)
        self.assertIs(self.restriction(game, idle), True)

        identity = game.state.cards[victim.object_id].logical_object_id
        self.cast(game, "A", gift, ["B", victim.ref])
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[victim.object_id]
        self.assertEqual(("B", identity), (current.controller, current.logical_object_id))
        self.assertIs(self.restriction(game, victim), True)
        native_turn.TemporaryKeywordRemovalRuntimeTests.advance(
            self, game, lambda s: s.state.active_player == "B" and s.state.step == "upkeep")
        self.assertTrue(game.state.cards[victim.object_id].tapped)
        self.assertFalse(game.state.cards[idle.object_id].tapped)
        self.assertFalse(game.state.cards[control_land.object_id].tapped)
        self.assertIsNone(self.restriction(game, victim))
        self.assertIsNone(self.restriction(game, idle))

        self.cast(game, "B", burst, [game.state.cards[victim.object_id].ref])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[victim.object_id].tapped)
        for seat in "ABC":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(private.ref, packet)
            self.assertNotIn(private.printed_name, packet)
        self.replay(game, load=True)

    def test_actual_ajani_positive_loyalty_can_target_an_untapped_land(self):
        game = self.session(36151412)
        ajani = self.add(game.engine, "Ajani Vengeant")
        ajani.counters["loyalty"] = 3
        target = self.add(game.engine, "Island", seat="B")
        self.assert_closed(game, ajani)
        self.prepare(game)
        self.checkpoint(game)
        positive, = [a for a in game.engine._activated_abilities(ajani) if a.loyalty_delta == 1]
        self.activate(game, ajani, target, ability_id=positive.ability_id)
        self.assertEqual(4, game.state.cards[ajani.object_id].counters.get("loyalty"))
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[target.object_id].tapped)
        self.assertIs(self.restriction(game, target), True)
        self.replay(game, load=True)

    def test_actual_target_departure_leaves_the_source_price_paid_and_no_restriction(self):
        game = self.session(36151413)
        dart = self.add(game.engine, "Sleeper Dart")
        victim = self.add(game.engine, "Grizzly Bears", seat="B")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.assert_closed(game, dart)
        self.prepare(game)
        self.checkpoint(game)
        self.activate(game, dart, victim)
        self.cast(game, "B", bounce, [victim.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[victim.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[dart.object_id].zone)
        self.assertIsNone(self.restriction(game, victim))
        self.replay(game, load=True)

    def test_omitting_the_activation_result_gate_blocks_real_original_registration(self):
        with patch("quorune.compiler.activated_mana_nodes._CAPABILITY_SHAPED_EFFECT_OPERATIONS",
                   _CAPABILITY_SHAPED_EFFECT_OPERATIONS - {"prohibit_next_untap"}):
            admitted, _ = register_card(self.db, self.registry, "Sleeper Dart")
        self.assertFalse(admitted["strict_capability_ready"], admitted)
        self.assertTrue(any("capability:undeclared_runtime_dependency:" + CAPABILITY in blocker
            for blocker in admitted["blockers"]), admitted["blockers"])

    def test_verified_withdrawal_blocks_each_whole_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        changed = deepcopy(source)
        next(row for row in changed["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Targeted next-controller untap withdrawn"])
        registry = CapabilityRegistry(changed)
        index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in reason for reason in closure.blockers))
        for name in ORIGINALS:
            ready, _ = register_card(self.db, registry, name)
            self.assertFalse(ready["strict_capability_ready"], (name, ready["blockers"]))
        control, _ = register_card(self.db, registry, "Grizzly Bears")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])

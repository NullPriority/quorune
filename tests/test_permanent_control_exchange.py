"""Printed targeted control exchanges share one atomic layer-two transaction."""

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
from quorune.continuous_effect_model import Layer
from quorune.control_exchange_model import (
    PERMANENT_CONTROL_EXCHANGE_CAPABILITY as CAPABILITY, PermanentControlExchange,
    decode_permanent_control_exchange,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.control_capability_shapes import permanent_control_exchange_node_capabilities
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SWITCH = "Switcheroo"
TRICK = "Political Trickery"
PLOTTER = "Vedalken Plotter"
BORDERS = "Shifting Borders"
TOTEM = "Avarice Totem"
GRIFT = "Shifting Grift"
REJECTED = "Cultural Exchange"
ORIGINALS = (SWITCH, TRICK, PLOTTER, BORDERS, TOTEM, GRIFT)
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class PermanentControlExchangeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {"Disenchant", "Generic Bound Plains", "Unsummon"},
            "attached-destruction-set-cards.json": {"Fervor", "Grizzly Bears", "Island", "Sol Ring"},
            "creature-subtype-addition-cards.json": {"Goblin Piker", "Isamaru, Hound of Konda"},
            "recipient-control-cards.json": {"Donate", "Universal Automaton"},
            "composed-spell-text-cards.json": {"Reach Through Mists"},
            "spree-mode-cost-cards.json": {GRIFT},
        }
        paths = [FIXTURES / "permanent-control-exchange-cards.json"]
        for source, names in support.items():
            doc = json.loads((FIXTURES / source).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert {row["name"] for row in doc["cards"]} == names, source
            ids = {row["oracle_id"] for row in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            target = folder / source
            target.write_text(json.dumps(doc), encoding="utf-8")
            paths.append(target)
        database = folder / "permanent-control-exchange.sqlite3"
        build_fixture_database(paths, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Fixed permanent control exchange", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_bind_to_the_closed_control_exchange(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                report, programs = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                self.assertTrue(any(CAPABILITY in p.capability_dependencies for p in programs.programs_for_oracle(card.oracle_id)))

        report, _ = register_card(self.db, self.registry, REJECTED)
        self.assertFalse(report["strict_capability_ready"])

        card = self.db.lookup(SWITCH, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        node, = ir.faces[0].nodes
        effect, = node.effects
        self.assertEqual((PermanentControlExchange.CREATURE, ("$target.0", "$target.1")),
                         decode_permanent_control_exchange(effect))
        for change in (
            {"schema_version": True}, {"left": "$target.1"}, {"scope": "same_type"},
            {"duration": "until_end_of_turn"},
        ):
            with self.subTest(change=change):
                self.assertFalse(permanent_control_exchange_node_capabilities(
                    effects=({**effect, **change},), target_schema=node.target_schema, mechanic_ids=node.mechanics,
                ))
        schema = deepcopy(node.target_schema)
        schema["distinct"] = False
        self.assertFalse(permanent_control_exchange_node_capabilities(
            effects=(effect,), target_schema=schema, mechanic_ids=node.mechanics,
        ))
        _, semantics = register_card(self.db, self.registry, SWITCH)
        program, = semantics.programs_for_oracle(card.oracle_id)
        without_owner = tuple(dep for dep in program.capability_dependencies if dep != CAPABILITY)
        unlabelled = replace(program, coverage=(), capability_dependencies=without_owner,
                             capability_closure=self.registry.closure(without_owner, profile=PROFILE).to_dict())
        bad = bind_semantic_program_runtime(unlabelled, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bad["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, bad["blockers"])

    def _start(self, game, originals, mana):
        for card in originals:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def _reject(self, game, command, *, actor="A"):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _act(self, game, command, *, actor="A"):
        result = game.act("pilot:" + actor, command)
        self.assertTrue(result.ok, result.summary)

    def _controllers(self, game, *cards):
        return tuple(game.state.cards[card.object_id].controller for card in cards)

    def test_actual_foreign_creatures_exchange_with_one_layer_timestamp(self):
        unavailable = self.session(70112009)
        single = self.add(unavailable.engine, SWITCH, zone="hand")
        one = self.add(unavailable.engine, "Grizzly Bears", seat="C")
        self._start(unavailable, (single,), {"C": 4, "U": 1})
        actions = unavailable.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(action["id"] == "cast:" + single.ref for action in actions))
        self._reject(unavailable, {"action_id": "cast:" + single.ref, "pay": "auto", "targets": [one.ref, one.ref]})

        game = self.session(70112010)
        spell = self.add(game.engine, SWITCH, zone="hand")
        left = self.add(game.engine, "Grizzly Bears", seat="B")
        right = self.add(game.engine, "Goblin Piker", seat="C")
        land = self.add(game.engine, "Island", seat="D")
        self._start(game, (spell,), {"C": 4, "U": 1})

        offered = self.action(game, "A", spell)
        command = {"action_id": offered["id"], "pay": "auto"}
        self._reject(game, {**command, "targets": [left.ref, left.ref]})
        self._reject(game, {**command, "targets": [left.ref, land.ref]})
        self._reject(game, {**command, "targets": [left.ref, right.ref]}, actor="D")
        self._act(game, {**command, "targets": [left.ref, right.ref]})
        self.assertEqual(("B", "C"), self._controllers(game, left, right))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("C", "B"), self._controllers(game, left, right))
        self.assertEqual(("B", "C"), tuple(game.state.cards[card.object_id].owner for card in (left, right)))
        grants = [row for row in game.state.continuous_effects if row.layer is Layer.CONTROL and row.timestamp > 0]
        self.assertEqual(2, len(grants))
        self.assertEqual(1, len({row.timestamp for row in grants}))
        self.assertEqual({left.logical_object_id, right.logical_object_id},
                         {item.logical_object_id for grant in grants for item in grant.locked_objects})
        self.replay(game, load=True)

    def test_same_controller_and_one_departed_target_are_legal_atomic_noops(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                game = self.session(70112011 + int(departed))
                spell = self.add(game.engine, SWITCH, zone="hand")
                left = self.add(game.engine, "Grizzly Bears", seat="B")
                right = self.add(game.engine, "Goblin Piker", seat="C" if departed else "B")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                self.add(game.engine, "Island", seat="B")
                self._start(game, (spell,), {"C": 4, "U": 1})
                self.cast(game, "A", spell, [left.ref, right.ref])
                before = tuple(game.state.continuous_effects)
                if departed:
                    self.cast(game, "B", bounce, [right.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[right.object_id].zone)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("B", self._controllers(game, left)[0])
                self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
                self.assertEqual(before, tuple(game.state.continuous_effects))
                self.replay(game, load=True)

    def test_actual_spell_and_entry_trigger_keep_their_opposing_land_target_roles(self):
        for name in (TRICK, PLOTTER):
            with self.subTest(name=name):
                game = self.session(70112014 if name == TRICK else 70112015)
                source = self.add(game.engine, name, zone="hand")
                own_land = self.add(game.engine, "Island", ref="exchange-own-island")
                opposing = self.add(game.engine, "Island", seat="C", ref="exchange-opposing-island")
                self._start(game, (source,), {"C": 2, "U": 1})
                if name == TRICK:
                    command = {"action_id": self.action(game, "A", source)["id"], "pay": "auto"}
                else:
                    self.cast(game, "A", source)
                    pending = self.resolve(game)
                    self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
                    self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
                    command = {"action_id": "choose"}
                self._reject(game, {**command, "targets": [opposing.ref, own_land.ref]})
                self._act(game, {**command, "targets": [own_land.ref, opposing.ref]})
                self.assertIsNone(self.resolve(game))
                self.assertEqual(("C", "A"), self._controllers(game, own_land, opposing))
                self.replay(game, load=True)

    def test_actual_totem_repeated_activation_uses_current_controllers_and_original_source(self):
        game = self.session(70112020)
        source = self.add(game.engine, TOTEM)
        own = self.add(game.engine, "Grizzly Bears")
        foreign = self.add(game.engine, "Sol Ring", seat="C")
        land = self.add(game.engine, "Island", seat="C")
        self._start(game, (source,), {"C": 10})
        for target in (own, foreign):
            self.priority(game, "A")
            actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            action, = (a for a in actions if a["id"].startswith("activate:" + source.ref + ":"))
            command = {"action_id": action["id"], "pay": "auto"}
            self._reject(game, {**command, "targets": [land.ref]})
            self._act(game, {**command, "targets": [target.ref]})
        self.resolve_top(game)
        self.assertEqual(("C", "A", "A"), self._controllers(game, source, own, foreign))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("A", "C", "A"), self._controllers(game, source, own, foreign))
        self.replay(game, load=True)

    def test_actual_spree_uses_each_paid_mode_and_current_controllers_in_printed_order(self):
        game = self.session(70112025)
        spell = self.add(game.engine, GRIFT, zone="hand")
        automaton = self.add(game.engine, "Universal Automaton", seat="B")
        goblin = self.add(game.engine, "Goblin Piker", seat="C")
        ring = self.add(game.engine, "Sol Ring", seat="D")
        first = self.add(game.engine, "Fervor", seat="B", ref="grift-first-enchantment")
        second = self.add(game.engine, "Fervor", seat="C", ref="grift-second-enchantment")
        self._start(game, (spell,), {"C": 4, "U": 2})
        action = self.action(game, "A", spell)
        targets = [automaton.ref, goblin.ref, automaton.ref, ring.ref, first.ref, second.ref]
        command = {"action_id": action["id"], "pay": "auto", "targets": targets,
                   "modes": ["mode_3", "mode_2", "mode_1"]}
        self._reject(game, {**command, "targets": [*targets[:4], first.ref, first.ref]})
        self._act(game, command)
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.assertEqual(["mode_1", "mode_2", "mode_3"], game.state.stack[-1].modes)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("D", "B", "C", "C", "B"), self._controllers(game, automaton, goblin, ring, first, second))
        self.replay(game, load=True)

    def test_actual_splice_keeps_the_unselected_hand_private_and_exchanges_lands(self):
        game = self.session(70112026)
        spell = self.add(game.engine, "Reach Through Mists", zone="hand")
        borders = self.add(game.engine, BORDERS, zone="hand", ref="exchange-spliced-borders")
        private = self.add(game.engine, "Unsummon", zone="hand", ref="exchange-unselected-hand")
        left = self.add(game.engine, "Island", seat="B", ref="splice-left-island")
        right = self.add(game.engine, "Island", seat="C", ref="splice-right-island")
        self._start(game, (spell, borders), {"C": 3, "U": 2})

        offer = self.action(game, "A", spell)
        self.assertIn("prepare_splice", offer["choice_schema"])
        self._act(game, {"action_id": offer["id"], "prepare_splice": True})
        self.assertEqual("selection.splice_cast", game.state.pending_decision.kind)
        self._act(game, {"action_id": "add:" + borders.ref})
        for seat in "BCD":
            packet = game.packet("pilot:" + seat, full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(borders.ref, json.dumps(packet))
        game = self.replay(game, load=True)
        self._reject(game, {"action_id": "cast", "targets": [left.ref, left.ref], "pay": "auto"})
        self._act(game, {"action_id": "cast", "targets": [left.ref, right.ref], "pay": "auto"})
        public = json.dumps(game.packet("pilot:D", full=True))
        self.assertIn(borders.ref, public)
        self.assertNotIn(private.ref, public)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("C", "B"), self._controllers(game, left, right))
        self.assertEqual("hand", game.state.cards[borders.object_id].zone)
        self.replay(game, load=True)

    def test_genuine_exchange_withdrawal_keeps_existing_control_acquisition(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        registry_doc = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in registry_doc["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Scoped two-permanent exchange withdrawal"],
        )
        registry = CapabilityRegistry(registry_doc)
        index = build_index(registry_value=registry_doc, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in ORIGINALS:
            with self.subTest(name=name):
                report, _ = register_card(self.db, registry, name)
                self.assertFalse(report["strict_capability_ready"], report)
        report, _ = register_card(self.db, registry, "Donate")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])

    def test_omitting_only_the_exchange_lowering_kills_current_original_admission(self):
        with patch("quorune.compiler.resolution_effect_templates.permanent_control_exchange_template", return_value=None):
            card = self.db.lookup(SWITCH, fuzzy=False)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertTrue(any("Exchange control" in row.text for row in ir.material_residuals))
            report, _ = register_card(self.db, self.registry, SWITCH)
            self.assertFalse(report["strict_capability_ready"])


if __name__ == "__main__":
    unittest.main()

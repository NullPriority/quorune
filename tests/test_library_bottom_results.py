"""Only the committed public bottom card can produce the printed creature result."""

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
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_bottom_result_model import (
    LIBRARY_BOTTOM_RESULT_CAPABILITY as CAPABILITY, LIBRARY_BOTTOM_SOURCE_POWER, LibraryBottomResultSpec,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.library_bottom_results import library_bottom_result_node_capabilities
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


DOOR = "Cellar Door"
GRENZO = "Grenzo, Dungeon Warden"
AUTOMATON = "Adaptive Automaton"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class LibraryBottomResultTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Unsummon"},
            "activation-public-state-cards.json": {"Giant Growth"},
            "attached-destruction-set-cards.json": {"Island"},
            "creature-subtype-addition-cards.json": {"Goblin Piker", "Isamaru, Hound of Konda"},
            "recipient-control-cards.json": {"Rumbling Baloth"},
            "entry-designation-cards.json": {AUTOMATON},
            "fixed-direct-phasing-cards.json": {"Reality Ripple"},
            "graveyard-exile-replacement-cards.json": {"Leyline of the Void"},
        }
        paths = [FIXTURES / "library-bottom-result-cards.json"]
        for source, names in support.items():
            doc = json.loads((FIXTURES / source).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert {row["name"] for row in doc["cards"]} == names, source
            ids = {row["oracle_id"] for row in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            target = folder / source
            target.write_text(json.dumps(doc), encoding="utf-8")
            paths.append(target)
        database = folder / "library-bottom-committed-results.sqlite3"
        build_fixture_database(paths, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Committed library bottom", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_intact_originals_bind_complete_roles_and_reject_changed_results(self):
        for name in (DOOR, GRENZO):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = (p for p in semantics.programs_for_oracle(card.oracle_id) if CAPABILITY in p.capability_dependencies)
                node, = (n for n in ir.faces[0].nodes if CAPABILITY in n.capability_dependencies)
                effect, = node.effects
                spec = LibraryBottomResultSpec.from_effect(effect)
                self.assertEqual(spec.target_schema(), node.target_schema)
                self.assertIs(spec.targets_player, name == DOOR)
                if name == GRENZO:
                    self.assertEqual(LIBRARY_BOTTOM_SOURCE_POWER.to_dict(), effect["result"]["source_power"])
                for changed in (dict(effect, schema_version=True), dict(effect, player="foreign-seat"),
                                dict(effect, position="top"), dict(effect, result={"kind": "draw"})):
                    self.assertFalse(library_bottom_result_node_capabilities(
                        effects=(changed,), target_schema=node.target_schema, mechanic_ids=node.mechanics,
                    ))
                altered = deepcopy(effect)
                if name == DOOR:
                    altered["result"]["token"]["quantity"] = 2
                else:
                    altered["result"]["source_power"]["coefficient"] = -1
                self.assertFalse(library_bottom_result_node_capabilities(
                    effects=(altered,), target_schema=node.target_schema, mechanic_ids=node.mechanics,
                ))
                deps = tuple(dep for dep in program.capability_dependencies if dep != CAPABILITY)
                unlabelled = replace(program, coverage=(), capability_dependencies=deps,
                                     capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                bad = bind_semantic_program_runtime(unlabelled, capability_registry=self.registry, profile=PROFILE)
                self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, bad["blockers"])

    def _bottom(self, game, name, seat, ref):
        card = self.add(game.engine, name, seat=seat, zone="library", ref=ref)
        card.known_to.clear()
        card.revealed_to.clear()
        library = game.state.players[seat].zones["library"]
        library.remove(card.object_id)
        library.insert(0, card.object_id)
        return card

    def _start(self, game, sources, mana):
        for source in sources:
            self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def _reject(self, game, command, *, actor="A"):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + actor, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _act(self, game, command):
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def _activate(self, game, source, targets=(), *, generic):
        self.priority(game, "A")
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        row, = (action for action in legal if action["id"].startswith("activate:" + source.ref + ":"))
        command = {"action_id": row["id"], "pay": "manual", "payment": {"C": generic}, "targets": list(targets)}
        self._reject(game, command, actor="D")
        self._act(game, command)

    def _cast_grenzo(self, game, source, x):
        row = self.action(game, "A", source)
        payment = {"B": 1, "R": 1}
        if x:
            payment["C"] = x
        self._act(game, {"action_id": row["id"], "x": x, "pay": "manual", "payment": payment})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(x, game.state.cards[source.object_id].counters.get("+1/+1", 0))

    def _assert_private(self, game, *refs):
        for seat in "ABCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            for ref in refs:
                self.assertNotIn(ref, packet)

    def test_actual_paid_door_uses_the_target_library_bottom_and_commits_before_token(self):
        for name, creates in (("Goblin Piker", True), ("Generic Bound Plains", False)):
            with self.subTest(name=name):
                game = self.session(70117060 + int(creates))
                door = self.add(game.engine, DOOR)
                bottom = self._bottom(game, name, "B", "door-private-library-bottom")
                top = game.state.players["B"].zones["library"][-1]
                self._start(game, (door,), {"C": 3})
                self._assert_private(game, bottom.ref)
                self._activate(game, door, ["B"], generic=3)
                self.assertTrue(game.state.cards[door.object_id].tapped)
                self._assert_private(game, bottom.ref)
                game = self.replay(game, load=True)
                cursor = len(game.state.events)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard", game.state.cards[bottom.object_id].zone)
                self.assertEqual(top, game.state.players["B"].zones["library"][-1])
                tokens = tuple(c for c in game.state.cards.values() if c.is_token and c.zone == "battlefield")
                self.assertEqual(["A"] if creates else [], [c.controller for c in tokens])
                if creates:
                    self.assertIn("Zombie", tokens[0].printed_name)
                events = game.state.events[cursor:]
                self.assertFalse(any("mill" in event.code for event in events))
                moved, = (event for event in events if event.code == "zone.move" and bottom.ref in str(event.details))
                self.assertEqual(("library", "graveyard"), (moved.details["from"], moved.details["to"]))
                if creates:
                    token_event, = (event for event in events if event.code == "token.create")
                    self.assertLess(events.index(moved), events.index(token_event))
                self.replay(game, load=True)

    def test_actual_grenzo_read_uses_current_or_immediate_departure_power(self):
        for index, response in enumerate((None, "Unsummon", "Reality Ripple")):
            with self.subTest(response=response):
                game = self.session(70117070 + index)
                source = self.add(game.engine, GRENZO, zone="hand")
                bottom = self._bottom(game, "Rumbling Baloth", "A", "grenzo-hidden-baloth")
                growth = self.add(game.engine, "Giant Growth", zone="hand")
                answer = self.add(game.engine, response, seat="B", zone="hand") if response else None
                self.add(game.engine, "Island", seat="B", ref="grenzo-first-island")
                self.add(game.engine, "Island", seat="B", ref="grenzo-second-island")
                self._start(game, (source,), {"C": 3, "B": 1, "R": 1, "G": 1})
                self._cast_grenzo(game, source, 1)
                self._activate(game, source, generic=2)
                if answer is not None:
                    self.cast(game, "A", growth, [source.ref])
                    self.resolve_top(game)
                    self.assertEqual(6, game.engine._numeric_stat(source.object_id, "power"))
                    self.cast(game, "B", answer, [source.ref])
                    self.resolve_top(game)
                    current = game.state.cards[source.object_id]
                    self.assertTrue(current.zone == "hand" if response == "Unsummon" else current.phased_out)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield" if answer is not None else "graveyard", game.state.cards[bottom.object_id].zone)
                self.replay(game, load=True)

    def test_actual_replacement_order_and_entry_choice_keep_the_same_public_card(self):
        game = self.session(70117074)
        source = self.add(game.engine, GRENZO, zone="hand")
        bottom = self._bottom(game, AUTOMATON, "A", "grenzo-private-automaton")
        next_bottom = game.state.players["A"].zones["library"][1]
        leyline = self.add(game.engine, "Leyline of the Void", seat="B", ref="grenzo-first-leyline")
        other = self.add(game.engine, "Leyline of the Void", seat="C", ref="grenzo-second-leyline")
        self._start(game, (source, bottom, leyline, other), {"C": 2, "B": 1, "R": 1})
        self._cast_grenzo(game, source, 0)
        self._activate(game, source, generic=2)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("library", game.state.cards[bottom.object_id].zone)
        self._assert_private(game, bottom.ref)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        command = {"action_id": "choose", "replacement": options[0]["id"]}
        self._reject(game, command, actor="C")
        stale = self.replay(game, load=True)
        library = stale.state.players["A"].zones["library"]
        library[0], library[1] = library[1], library[0]
        before = authoritative_state_hash(stale.state)
        rejected = stale.act("pilot:A", command)
        self.assertFalse(rejected.ok)
        self.assertIn("bottom card changed", rejected.summary)
        self.assertEqual(before, authoritative_state_hash(stale.state))
        game = self.replay(game, load=True)
        self._act(game, command)
        pending = game.state.pending_decision
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("exile", game.state.cards[bottom.object_id].zone)
        self.assertEqual(next_bottom, game.state.players["A"].zones["library"][0])
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        chosen, = (row["id"] for row in options if row["id"].endswith(":goblin"))
        second = {"action_id": "choose", "replacement": chosen}
        self._reject(game, second, actor="B")
        game = self.replay(game, load=True)
        self._act(game, second)
        current = game.state.cards[bottom.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertEqual("goblin", current.annotations["chosen_creature_type"])
        self.assertEqual(next_bottom, game.state.players["A"].zones["library"][0])
        self.replay(game, load=True)

    def test_exact_producer_omission_keeps_both_material_results_unresolved(self):
        path = "quorune.compiler.library_bottom_result_templates.library_bottom_creature_result_template"
        with patch(path, return_value=None):
            for name in (DOOR, GRENZO):
                report, _ = register_card(self.db, self.registry, name)
                self.assertFalse(report["strict_capability_ready"], report)

    def test_genuine_withdrawal_retains_the_ordinary_entry_replacement(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        registry_doc = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in registry_doc["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Scoped library-bottom result authority withdrawal"],
        )
        registry = CapabilityRegistry(registry_doc)
        index = build_index(registry_value=registry_doc, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in (DOOR, GRENZO):
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, AUTOMATON)
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()

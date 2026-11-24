"""Controller-relative chosen-type pricing and private singleton top choices."""

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
from quorune.cast_cost_modifiers import CHOSEN_TYPE_CAST_COST_CAPABILITY
from quorune.compiler.chosen_type_cast_costs import chosen_type_spell_cost_handler
from quorune.compiler import fixed_library_selection_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.designation_prevention import chosen_type_library_predicate
from quorune.model import TurnEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.library_selection_capability_shapes import (
    CHOSEN_TYPE_TOP_SELECTION_CAPABILITY as CAPABILITY,
    chosen_type_top_selection_effect,
    validate_chosen_type_top_selection,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


HORN = "Herald's Horn"
STONE = "Gathering Stone"
ICON = "Icon of Ancestry"
INCUBATOR = "Urza's Incubator"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class ChosenTypeTopCardTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {
                "Generic Bound Commander", "Generic Bound Plains", "Disenchant", "Forest", "Grizzly Bears",
            },
            "creature-subtype-addition-cards.json": {"Boggart Shenanigans", "Goblin Piker"},
            "attached-destruction-set-cards.json": {"Mountain"},
            "chosen-type-library-selection-cards.json": {ICON},
            "fixed-spell-cost-reduction-cards.json": {INCUBATOR},
        }
        inputs = [FIXTURES / "chosen-type-top-card-cards.json"]
        for name, names in support.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [row for row in document["cards"] if row["name"] in names]
            assert {row["name"] for row in document["cards"]} == names
            ids = {row["oracle_id"] for row in document["cards"]}
            document["rulings"] = [row for row in document.get("rulings", []) if row["oracle_id"] in ids]
            path = folder / name
            path.write_text(json.dumps(document), encoding="utf-8")
            inputs.append(path)
        path = folder / "chosen-type-top.sqlite3"
        build_fixture_database(inputs, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private Chosen Top", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_exact_wire_distinguishes_creature_keep_and_card_optional_graveyard(self):
        for may_bury in (False, True):
            descriptor = chosen_type_top_selection_effect(may_put_in_graveyard=may_bury)
            self.assertIsNone(validate_chosen_type_top_selection(descriptor))
            predicate = chosen_type_library_predicate("goblin", creatures_only=not may_bury)
            actor_effect = {**descriptor, "player": "A", "predicate_groups": [[predicate.to_dict()]]}
            self.assertEqual(predicate, validate_chosen_type_top_selection(actor_effect, actor="A"))
            other = chosen_type_library_predicate("goblin", creatures_only=may_bury)
            for change in ({"public_reveal": True}, {"look_count": True}, {"maximum": 2},
                           {"remainder_destination": "library_bottom"},
                           {"predicate_groups": [[other.to_dict()]]}, {"player": "B"}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    validate_chosen_type_top_selection({**actor_effect, **change}, actor="A")
        for prefix in ("Creature spells", "Spells"):
            first = f"{prefix} you cast of the chosen type cost {{1}} less to cast."
            last = f"{prefix} of the chosen type you cast cost {{1}} less to cast."
            self.assertEqual(chosen_type_spell_cost_handler(last), chosen_type_spell_cost_handler(first))
            self.assertIsNone(chosen_type_spell_cost_handler(
                f"{prefix} you cast of the chosen type you cast cost {{1}} less to cast.",
            ))

    def test_originals_have_complete_cost_entry_and_private_selection_authority(self):
        for name in (HORN, STONE):
            with self.subTest(name=name):
                source = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(source, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual({"zone.change", "cast.cost.modify", "permanent.enter" if name == STONE else "step.begin"},
                                 {node.event for node in ir.faces[0].nodes})
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                programs = semantics.programs_for_oracle(source.oracle_id)
                self.assertTrue(any(CHOSEN_TYPE_CAST_COST_CAPABILITY in p.capability_dependencies for p in programs))
                p, = [program for program in programs if CAPABILITY in program.capability_dependencies]
                unlabelled = replace(p, coverage=[])
                self.assertTrue(bind_semantic_program_runtime(
                    unlabelled, capability_registry=self.registry, profile=PROFILE,
                )["strict"])
                deps = tuple(c for c in p.capability_dependencies if c != CAPABILITY)
                changed = replace(unlabelled, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"])
                self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, bound["blockers"])

    def _start(self, game, *, actor="A", mana=None):
        game.state.config.semantic_policy = "trusted_only"
        if actor != "A":
            game.state.current_turn = TurnEntry("chosen-top-preceding-turn", actor)
            game.state.last_normal_turn_player = actor
        self.prepare(game, actor=actor, active=actor, mana=mana)
        self.checkpoint(game)

    def _reject(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _choose(self, game, command):
        self._reject(game, "D", command)
        selected = game.act("pilot:A", command)
        self.assertTrue(selected.ok, selected.summary)

    def _entry(self, game, source):
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        entries = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        option, = [row["id"] for row in entries if row["id"].endswith(":goblin")]
        self._choose(game, {"action_id": "choose", "replacement": option})
        self.assertEqual("goblin", game.state.cards[source.object_id].annotations["chosen_creature_type"])

    def _private_top(self, game, top):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        visible = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertIn(top.ref, json.dumps(visible))
        self.assertIn(top.printed_name, json.dumps(visible))
        for actor in "BCD":
            message = game.packet("pilot:" + actor, full=True)
            self.assertIsNone(message["decision"])
            self.assertNotIn(top.ref, json.dumps(message))
        action, = visible["legal_actions"]
        return action["id"], tuple(action["choice_schema"]["legal_values"])

    def _upkeep(self, game, source):
        self.until(game, lambda s: s.state.active_player == "A" and s.state.step == "upkeep"
                   and any(item.source_object_id == source.object_id for item in s.state.stack))

    def _manual_cast(self, game, source, payment, *, accept=True, seat="A"):
        self.priority(game, seat)
        offer = self.action(game, seat, source)
        command = {"action_id": offer["id"], "pay": "manual", "payment": payment}
        if not accept:
            self._reject(game, seat, command)
            return
        cast = game.act("pilot:" + seat, command)
        self.assertTrue(cast.ok, cast.summary)

    def test_actual_horn_prices_own_creature_only_and_opponent_pays_full(self):
        game = self.session(11870610)
        horn = self.add(game.engine, HORN, zone="hand")
        goblin = self.add(game.engine, "Goblin Piker", zone="hand")
        kindred = self.add(game.engine, "Boggart Shenanigans", zone="hand")
        opponent = self.add(game.engine, "Goblin Piker", seat="B", zone="hand", ref="opponent-priced-goblin")
        forest = self.add(game.engine, "Forest", seat="B")
        mountain = self.add(game.engine, "Mountain", seat="B")
        self.assert_closed(game, horn)
        self._start(game, mana={"C": 5, "R": 2})
        self._manual_cast(game, horn, {"C": 3})
        self._entry(game, horn)
        self._manual_cast(game, goblin, {"R": 1})
        self.assertIsNone(self.resolve(game))
        self._manual_cast(game, kindred, {"C": 1, "R": 1}, accept=False)
        self._manual_cast(game, kindred, {"C": 2, "R": 1})
        self.assertIsNone(self.resolve(game))

        self.until(game, lambda s: s.state.active_player == "B" and s.state.phase == "precombat_main")
        self.cast(game, "B", opponent)
        self.assertTrue(game.state.cards[forest.object_id].tapped)
        self.assertTrue(game.state.cards[mountain.object_id].tapped)
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_actual_horn_privately_inspects_and_reveals_after_the_source_leaves(self):
        game = self.session(11870611)
        horn = self.add(game.engine, HORN)
        horn.annotations["chosen_creature_type"] = "goblin"
        top = self.add(game.engine, "Goblin Piker", zone="library", ref="horn-private-goblin")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        for index in range(2):
            self.add(game.engine, "Generic Bound Plains", seat="B", ref=f"horn-removal-land-{index}")
        self.assert_closed(game, horn)
        self._start(game, actor="D")
        self._upkeep(game, horn)
        self.cast(game, "B", removal, [horn.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[horn.object_id].zone)
        action, legal = self._private_top(game, top)
        self.assertEqual(("reveal_and_take", "keep"), legal)
        game = self.replay(game, load=True)
        self._choose(game, {"action_id": action, "choice": "reveal_and_take"})
        self.assertEqual("hand", game.state.cards[top.object_id].zone)
        self.assertEqual(set("ABCD"), set(game.state.cards[top.object_id].revealed_to))
        self.replay(game, load=True)

    def test_actual_horn_can_keep_a_noncreature_or_unavailable_match_private(self):
        for selected, card in (("goblin", "Boggart Shenanigans"), (None, "Goblin Piker")):
            with self.subTest(selected=selected, card=card):
                game = self.session(11870612 + int(selected is not None))
                horn = self.add(game.engine, HORN)
                if selected is not None:
                    horn.annotations["chosen_creature_type"] = selected
                top = self.add(game.engine, card, zone="library", ref="horn-keep-private")
                self.assert_closed(game, horn)
                self._start(game, actor="D")
                self._upkeep(game, horn)
                action, choices = self._private_top(game, top)
                self.assertEqual(("keep",), choices)
                self._reject(game, "A", {"action_id": action, "choice": "reveal_and_take"})
                self._choose(game, {"action_id": action, "choice": "keep"})
                self.assertEqual("library", game.state.cards[top.object_id].zone)
                self.assertEqual(top.object_id, game.state.players["A"].zones["library"][-1])
                self.assertLessEqual(set(game.state.cards[top.object_id].revealed_to), {"A"})
                for seat in "BCD":
                    self.assertNotIn(top.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
                self.replay(game, load=True)

    def test_actual_stone_entry_uses_its_new_type_and_discounts_revealed_kindred_spell(self):
        game = self.session(11870614)
        stone = self.add(game.engine, STONE, zone="hand")
        top = self.add(game.engine, "Boggart Shenanigans", zone="library", ref="stone-private-kindred")
        self.assert_closed(game, stone)
        self._start(game, mana={"C": 5, "R": 1})
        self._manual_cast(game, stone, {"C": 4})
        self._entry(game, stone)
        action, choices = self._private_top(game, top)
        self.assertEqual(("reveal_and_take", "put_into_graveyard", "keep"), choices)
        self._choose(game, {"action_id": action, "choice": "reveal_and_take"})
        self.assertEqual("hand", game.state.cards[top.object_id].zone)
        self.assertEqual(set("ABCD"), set(game.state.cards[top.object_id].revealed_to))
        self._manual_cast(game, top, {"C": 1, "R": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[top.object_id].zone)
        self.replay(game, load=True)

    def test_actual_stone_upkeep_can_put_a_matching_card_in_graveyard_without_revealing(self):
        game = self.session(11870615)
        stone = self.add(game.engine, STONE)
        stone.annotations["chosen_creature_type"] = "goblin"
        top = self.add(game.engine, "Goblin Piker", zone="library", ref="stone-private-graveyard-option")
        self.assert_closed(game, stone)
        self._start(game, actor="D")
        self._upkeep(game, stone)
        action, choices = self._private_top(game, top)
        self.assertEqual(("reveal_and_take", "put_into_graveyard", "keep"), choices)
        game = self.replay(game, load=True)
        before = len(game.state.events)
        self._choose(game, {"action_id": action, "choice": "put_into_graveyard"})
        self.assertEqual("graveyard", game.state.cards[top.object_id].zone)
        self.assertFalse(any(event.code == "library.look" for event in game.state.events[before:]))
        self.replay(game, load=True)

    def test_omitting_the_exact_top_result_retains_each_original_entry_and_price(self):
        with patch.object(fixed_library_selection_templates.ChosenTypeTopSelectionEffectTemplate,
                          "compiled", return_value=(None, (), None, ())):
            for name in (HORN, STONE):
                source = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(source, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual(1, len(ir.material_residuals))
                self.assertIn("look at the top card", ir.material_residuals[0].text)
                deps = {c for face in ir.faces for node in face.nodes for c in node.capability_dependencies}
                self.assertLessEqual({"zone.entry.public_designation", CHOSEN_TYPE_CAST_COST_CAPABILITY}, deps)

    def test_genuine_top_authority_withdrawal_keeps_the_independent_icon_and_generic_price(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = deepcopy(json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes()))
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(r for r in raw["capabilities"] if r["id"] == CAPABILITY).update(
            status="blocked", blockers=["Scoped chosen type private singleton withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for source in (HORN, STONE):
            report, _ = register_card(self.db, registry, source)
            self.assertFalse(report["strict_capability_ready"], report)
        for source in (ICON, INCUBATOR):
            report, _ = register_card(self.db, registry, source)
            self.assertTrue(report["strict_capability_ready"], report["blockers"])


if __name__ == "__main__":
    unittest.main()

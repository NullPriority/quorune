"""Temporary creature copy sets bind one public snapshot and the current recipient set."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.temporary_self_copy import (
    TEMPORARY_CREATURE_COPY_SET_CAPABILITY as CAPABILITY,
    TEMPORARY_CREATURE_COPY_SET_VERSION as VERSION, TemporaryCreatureCopySet,
    decode_temporary_creature_copy_set, temporary_creature_copy_set_node_capabilities,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


WEAVE = "Mirrorweave"
COMMAND = "Augmenter Pugilist // Echoing Equation"
EQUATION = "Echoing Equation"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class TemporaryCreatureCopySetTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "activation-public-state-cards.json": {"Giant Growth", "Raise the Alarm"},
            "attached-destruction-set-cards.json": {"Fervor", "Grizzly Bears"},
            "creature-subtype-addition-cards.json": {"Goblin Piker", "Isamaru, Hound of Konda"},
            "activation-result-views-cards.json": {"Llanowar Elves"},
            "permanent-control-exchange-cards.json": {"Avarice Totem"},
            "qualified-attached-setting-cards.json": {"Sky Skiff"},
        }
        parts = [FIXTURES / "temporary-creature-copy-set-cards.json"]
        for source, names in support.items():
            doc = json.loads((FIXTURES / source).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert {row["name"] for row in doc["cards"]} == names, source
            ids = {row["oracle_id"] for row in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / source
            part.write_text(json.dumps(doc), encoding="utf-8")
            parts.append(part)
        database = folder / "temporary-creature-copy-set.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Temporary creature copy sets", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_intact_originals_bind_distinct_closed_scopes_and_only_the_printed_copy_face(self):
        for name, scope in ((WEAVE, TemporaryCreatureCopySet.ALL_OTHER_CREATURES),
                            (COMMAND, TemporaryCreatureCopySet.OTHER_CONTROLLED_CREATURES_NONLEGENDARY)):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                report, programs = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                face, = [f for f in ir.faces if any(CAPABILITY in n.capability_dependencies for n in f.nodes)]
                if name == COMMAND:
                    self.assertEqual(EQUATION, face.face_id)
                    self.assertEqual(2, len(ir.faces))
                    self.assertEqual(2, len(ir.faces[0].nodes))
                node, = [n for n in face.nodes if CAPABILITY in n.capability_dependencies]
                effect, = node.effects
                self.assertIs(scope, decode_temporary_creature_copy_set(effect))
                self.assertEqual(scope.target_schema(), node.target_schema)
                for fields in ({VERSION: True}, {"target": "$source.zone_object"}, {"scope": "all_permanents"},
                               {"source": "$controller"}, {"duration": "indefinite"}):
                    self.assertFalse(temporary_creature_copy_set_node_capabilities(
                        effects=({**effect, **fields},), target_schema=node.target_schema, mechanic_ids=node.mechanics,
                    ))
                invalid = deepcopy(node.target_schema)
                invalid.pop("controller_relation" if scope.controlled else "supertypes_none")
                self.assertFalse(temporary_creature_copy_set_node_capabilities(
                    effects=(effect,), target_schema=invalid, mechanic_ids=node.mechanics,
                ))
                program, = [p for p in programs.programs_for_oracle(card.oracle_id) if CAPABILITY in p.capability_dependencies]
                deps = tuple(c for c in program.capability_dependencies if c != CAPABILITY)
                removed = replace(program, coverage=(), capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(removed, capability_registry=self.registry, profile=PROFILE)
                self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, binding["blockers"])

    def _start(self, game, cards, mana):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def _reject(self, game, command, actor="A"):
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:" + actor, command)
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _act(self, game, command, actor="A"):
        action = game.act("pilot:" + actor, command)
        self.assertTrue(action.ok, action.summary)

    def _pay(self, game, spell, payment, targets=()):
        self.priority(game, "A")
        row = self.action(game, "A", spell)
        self._act(game, {"action_id": row["id"], "pay": "manual", "payment": payment, "targets": list(targets)})

    def _data(self, game, card):
        return game.engine._effective_card_data(game.state.cards[card.object_id])

    def _types(self, game, card):
        return game.engine._effective_type_parts(game.state.cards[card.object_id])

    def test_mirrorweave_snapshots_all_current_other_creatures_preserves_counters_and_expires(self):
        game = self.session(70702010)
        spell = self.add(game.engine, WEAVE, zone="hand")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        raise_alarm = self.add(game.engine, "Raise the Alarm", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        red = self.add(game.engine, "Goblin Piker", seat="C")
        elf = self.add(game.engine, "Llanowar Elves", seat="D")
        noncreature = self.add(game.engine, "Fervor")
        late = self.add(game.engine, "Goblin Piker", zone="hand", ref="weave-late-piker")
        private = self.add(game.engine, "Generic Bound Plains", seat="D", zone="hand", ref="weave-private-hand")
        target.counters["+1/+1"] = 2
        red.counters["+1/+1"] = 1
        self._start(game, (spell, growth, raise_alarm), {"C": 4, "U": 2, "W": 1, "R": 1, "G": 1})
        self._pay(game, growth, {"G": 1}, [red.ref])
        self.assertIsNone(self.resolve(game))
        self._pay(game, raise_alarm, {"C": 1, "W": 1})
        self.assertIsNone(self.resolve(game))
        tokens = [card for card in game.state.cards.values() if card.is_token and card.controller == "A"]
        self.assertEqual(2, len(tokens))

        row = self.action(game, "A", spell)
        self._reject(game, {"action_id": row["id"], "pay": "manual", "payment": {"C": 2, "U": 2},
                            "targets": [target.ref]}, "D")
        self._pay(game, spell, {"C": 2, "U": 2}, [target.ref])
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        copied = [event.details["source"] for event in game.state.events[cursor:] if event.code == "permanent.copy"]
        self.assertCountEqual([red.ref, elf.ref, *(card.ref for card in tokens)], copied)
        for recipient in (red, elf, *tokens):
            self.assertEqual("Grizzly Bears", self._data(game, recipient)["name"])
            self.assertEqual(recipient.logical_object_id, game.state.cards[recipient.object_id].logical_object_id)
            self.assertIs(recipient.is_token, game.state.cards[recipient.object_id].is_token)
        self.assertEqual("Fervor", self._data(game, noncreature)["name"])
        self.assertEqual(6, game.engine._numeric_stat(red.object_id, "power"))
        self.assertEqual(6, game.engine._numeric_stat(red.object_id, "toughness"))
        self.assertEqual(2, game.engine._numeric_stat(elf.object_id, "power"))
        self.assertEqual({"+1/+1": 2}, game.state.cards[target.object_id].counters)
        for chair in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + chair, full=True)))
        self._pay(game, late, {"C": 1, "R": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("Goblin Piker", self._data(game, late)["name"])
        turn = game.state.turn_sequence
        self.until(game, lambda g: g.state.turn_sequence > turn)
        self.assertEqual("Goblin Piker", self._data(game, red)["name"])
        self.assertEqual("Llanowar Elves", self._data(game, elf)["name"])
        self.assertEqual({"+1/+1": 1}, game.state.cards[red.object_id].counters)
        self.replay(game, load=True)

    def _animate(self, game, land, actor):
        self.priority(game, actor)
        actions = game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]
        row, = [a for a in actions if a["id"].startswith("activate:" + land.ref + ":") and not a.get("mana_ability")]
        self._act(game, {"action_id": row["id"], "pay": "manual", "payment": {"C": 1}}, actor)
        self.assertIsNone(self.resolve(game))

    def test_actual_animated_land_contributes_unanimated_values_and_its_printed_activation(self):
        game = self.session(70702012)
        spell = self.add(game.engine, WEAVE, zone="hand")
        target = self.add(game.engine, "Inkmoth Nexus", seat="B")
        changed = self.add(game.engine, "Grizzly Bears")
        self.assert_closed(game, spell)
        self.assert_closed(game, target)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 3, "U": 2})
        game.state.players["B"].mana_pool["C"] = 1
        self.checkpoint(game)
        self._animate(game, target, "B")
        self.assertEqual({"land", "artifact", "creature"}, self._types(game, target)[0])
        self._pay(game, spell, {"C": 2, "U": 2}, [target.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("Inkmoth Nexus", self._data(game, changed)["name"])
        self.assertEqual({"land"}, self._types(game, changed)[0])
        self.assertEqual({"land", "artifact", "creature"}, self._types(game, target)[0])
        self._animate(game, changed, "A")
        self.assertEqual({"land", "artifact", "creature"}, self._types(game, changed)[0])
        self.assertEqual(changed.logical_object_id, game.state.cards[changed.object_id].logical_object_id)
        self.replay(game, load=True)

    def test_actual_equation_back_face_limits_controlled_recipients_and_removes_only_legendary(self):
        game = self.session(70702014)
        spell = self.add(game.engine, COMMAND, zone="hand")
        target = self.add(game.engine, "Isamaru, Hound of Konda", ref="equation-original-legend")
        bear = self.add(game.engine, "Grizzly Bears")
        piker = self.add(game.engine, "Goblin Piker")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="equation-unaffected-bear")
        self._start(game, (spell,), {"C": 3, "U": 2})
        action_id = "cast:" + spell.ref + ":face:" + EQUATION
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [action for action in actions if action["id"] == action_id]
        command = {"action_id": offered["id"], "face": EQUATION, "pay": "manual", "payment": {"C": 3, "U": 2}}
        self._reject(game, {**command, "targets": [foreign.ref]})
        self._act(game, {**command, "targets": [target.ref]})
        self.assertEqual(EQUATION, game.state.stack[-1].chosen_face)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        for card in (bear, piker):
            self.assertEqual("Isamaru, Hound of Konda", self._data(game, card)["name"])
            self.assertNotIn("legendary", self._types(game, card)[2])
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        self.assertIn("legendary", self._types(game, target)[2])
        self.assertEqual("Grizzly Bears", self._data(game, foreign)["name"])
        self.replay(game, load=True)

    def test_exact_copy_set_omission_retains_material_original_result(self):
        path = "quorune.compiler.temporary_self_copy_templates.temporary_creature_copy_set_template"
        with patch(path, return_value=None):
            for name in (WEAVE, COMMAND):
                report, _ = register_card(self.db, self.registry, name)
                self.assertFalse(report["strict_capability_ready"], report)

    def test_genuine_copy_set_withdrawal_keeps_the_existing_single_copy(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Scoped temporary creature copy set withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in (WEAVE, COMMAND):
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, "Mirage Mirror")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


    def test_original_nanogene_binds_one_controlled_model_and_the_all_other_nonlegendary_scope(self):
        name = "Nanogene Conversion"
        original = self.db.lookup(name, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(original.oracle_id)
        scope = TemporaryCreatureCopySet.ALL_OTHER_CREATURES_NONLEGENDARY
        effect, = program.effects
        self.assertIs(scope, decode_temporary_creature_copy_set(effect))
        self.assertEqual(2, effect[VERSION])
        self.assertEqual(scope.target_schema(), program.target_schema)
        self.assertEqual("you", program.target_schema["controller_relation"])
        self.assertNotIn("supertypes_none", program.target_schema)
        for changed in (
            replace(program, effects=[{**effect, VERSION: 1}]),
            replace(program, target_schema={**program.target_schema, "controller_relation": "opponent"}),
            replace(program, effects=[{**effect, "scope": TemporaryCreatureCopySet.ALL_OTHER_CREATURES.value}]),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile=PROFILE,
            )["strict"])
        with patch("quorune.compiler.temporary_self_copy_templates.temporary_creature_copy_set_template", return_value=None):
            withheld, _ = register_card(self.db, self.registry, name)
            self.assertFalse(withheld["strict_capability_ready"], withheld)

    def test_actual_nanogene_copies_legendary_model_across_controllers_and_preserves_existing_values(self):
        game = self.session(70709101)
        spell = self.add(game.engine, "Nanogene Conversion", zone="hand")
        model = self.add(game.engine, "Isamaru, Hound of Konda", ref="nanogene-legend")
        own = self.add(game.engine, "Llanowar Elves", ref="nanogene-own")
        foreign = self.add(game.engine, "Goblin Piker", seat="B", ref="nanogene-foreign")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        alarm = self.add(game.engine, "Raise the Alarm", seat="C", zone="hand")
        private = self.add(game.engine, "Generic Bound Plains", seat="D", zone="hand", ref="nanogene-private")
        model.counters["+1/+1"] = 2
        foreign.counters["+1/+1"] = 1
        self._start(game, (spell, growth, alarm), {"C": 3, "U": 1, "G": 1})
        game.state.players["C"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast(game, "C", alarm)
        self.assertIsNone(self.resolve(game))
        self._pay(game, growth, {"G": 1}, [foreign.ref])
        self.assertIsNone(self.resolve(game))
        tokens = [card for card in game.state.cards.values() if card.is_token and card.controller == "C"]
        self.assertEqual(2, len(tokens))
        recipients = (own, foreign, *tokens)
        original_names = {card.object_id: self._data(game, card)["name"] for card in recipients}
        self._pay(game, spell, {"C": 3, "U": 1}, [model.ref])
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        copies = [event.details["source"] for event in game.state.events[cursor:] if event.code == "permanent.copy"]
        self.assertCountEqual([card.ref for card in recipients], copies)
        for card in recipients:
            values = game.engine._copyable_characteristics(game.state.cards[card.object_id])
            self.assertEqual("Isamaru, Hound of Konda", values["name"])
            self.assertNotIn("Legendary", values["type_line"])
            current = game.state.cards[card.object_id]
            self.assertEqual((card.controller, card.logical_object_id, card.is_token),
                             (current.controller, current.logical_object_id, current.is_token))
        self.assertIn("Legendary", self._data(game, model)["type_line"])
        self.assertEqual({"+1/+1": 2}, game.state.cards[model.object_id].counters)
        self.assertEqual({"+1/+1": 1}, game.state.cards[foreign.object_id].counters)
        self.assertEqual(6, game.engine._numeric_stat(foreign.object_id, "power"))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > turn)
        for card in recipients:
            self.assertEqual(original_names[card.object_id], self._data(game, card)["name"])
        self.replay(game, load=True)

    def test_actual_nanogene_copies_the_unanimated_values_of_a_crewed_vehicle(self):
        game = self.session(70709102)
        spell = self.add(game.engine, "Nanogene Conversion", zone="hand")
        vehicle = self.add(game.engine, "Sky Skiff", ref="nanogene-vehicle")
        worker = self.add(game.engine, "Grizzly Bears", ref="nanogene-worker")
        foreign = self.add(game.engine, "Goblin Piker", seat="B", ref="nanogene-foreign-vehicle")
        self._start(game, (spell, vehicle), {"C": 3, "U": 1})
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        crew, = (row for row in actions if row["id"].startswith(f"activate:{vehicle.ref}:"))
        crewed = game.act("pilot:A", {"action_id": crew["id"], "cost_cards": [worker.ref]})
        self.assertTrue(crewed.ok, crewed.summary)
        self.assertIsNone(self.resolve(game))
        self.assertIn("creature", self._types(game, vehicle)[0])
        self.assertTrue(game.state.cards[worker.object_id].tapped)
        self._pay(game, spell, {"C": 3, "U": 1}, [vehicle.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        for card in (worker, foreign):
            self.assertEqual("Sky Skiff", self._data(game, card)["name"])
            self.assertEqual({"artifact"}, set(self._types(game, card)[0]))
        self.assertIn("creature", self._types(game, vehicle)[0])
        self.assertTrue(game.state.cards[worker.object_id].tapped)
        self.replay(game, load=True)

    def test_actual_nanogene_lost_model_control_prevents_every_copy(self):
        game = self.session(70709103)
        spell = self.add(game.engine, "Nanogene Conversion", zone="hand")
        model = self.add(game.engine, "Grizzly Bears", ref="nanogene-lost-model")
        target = self.add(game.engine, "Goblin Piker", seat="C", ref="nanogene-uncopied")
        totem = self.add(game.engine, "Avarice Totem", seat="B")
        self._start(game, (spell, totem), {"C": 3, "U": 1})
        game.state.players["B"].mana_pool.update({"C": 5})
        self.checkpoint(game)
        self._pay(game, spell, {"C": 3, "U": 1}, [model.ref])
        self.priority(game, "B")
        choices = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = (row for row in choices if row["id"].startswith(f"activate:{totem.ref}:"))
        result = game.act("pilot:B", {"action_id": activation["id"], "targets": [model.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[model.object_id].controller)
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("Goblin Piker", self._data(game, target)["name"])
        self.assertFalse(any(event.code == "permanent.copy" for event in game.state.events[cursor:]))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_verified_copy_set_withdrawal_blocks_nanogene_and_keeps_ordinary_growth(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Scoped entire current creature copy set withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn(f"status:{CAPABILITY}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        rejected, _ = register_card(self.db, registry, "Nanogene Conversion")
        self.assertFalse(rejected["strict_capability_ready"], rejected)
        retained, _ = register_card(self.db, registry, "Giant Growth")
        self.assertTrue(retained["strict_capability_ready"], retained["blockers"])


if __name__ == "__main__":
    unittest.main()

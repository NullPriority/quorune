from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.admission import program_has_declared_complete_source_admission
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.stack_target_change_model import (
    MANDATORY_STACK_TARGET_CHANGE_FIELD, OPTIONAL_STACK_TARGET_CHANGE_FIELD,
    OptionalStackTargetChangeSpec,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
NAMES = ("Redirect", "Deflecting Swat", "Insidious Will")
FIXTURES = ROOT / "tests" / "fixtures"
ANNOUNCEMENT = "trigger.event.normalized_target_announcement"


class OptionalStackTargetChangeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Forest", "Island", "Unsummon"},
            "ability-source-counter-cards.json": {"Shock"},
            "activation-public-state-cards.json": {"Giant Growth"},
            "activation-result-views-cards.json": {"Mountain"},
            "additional-conditional-modifier-cards.json": {"Revitalize"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "conditional-free-cast-cards.json": {"Deflecting Swat"},
            "crime-cards.json": {"Redirect"},
            "designation-prevention-cards.json": {"Prodigal Sorcerer"},
            "empty-battlefield-condition-cards.json": {"Ashcoat Bear"},
            "fixed-stack-spell-copy-cards.json": {"Insidious Will"},
            "mandatory-single-stack-target-change-cards.json": {"Tolarian Terror", "Twin Bolt"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        pieces = []
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            pieces.append(piece)
        database = folder / "optional-stack-target-changes.sqlite3"
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Optional stack target choices", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_programs(self, name):
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding)
        return card, {p.event: p for p in semantics.programs_for_oracle(card.oracle_id)}

    def test_complete_originals_keep_conditional_price_and_all_modal_siblings(self):
        for name in NAMES:
            with self.subTest(name=name):
                card, programs = self.original_programs(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                spell = programs["resolve"]
                self.assertTrue(program_has_declared_complete_source_admission(spell))
                self.assertIn(ANNOUNCEMENT, spell.capability_dependencies)
                if name == "Insidious Will":
                    self.assertEqual(1, spell.target_schema["mode_count"])
                    modes = [row["effects"][0]["op"] for row in spell.target_schema["modes"].values()]
                    self.assertEqual(["counter_stack_target", "retarget_stack_item", "copy_stack_item"], modes)
                else:
                    spec = OptionalStackTargetChangeSpec(name == "Deflecting Swat")
                    self.assertEqual(list(spec.effects), thaw_value(spell.effects))
                    self.assertEqual(spec.target_schema, thaw_value(spell.target_schema))
                if name == "Deflecting Swat":
                    self.assertIsNone(spell.cost_schema)
                    price = programs["cast.cost"].handlers[0]["alternative_cost"]
                    self.assertEqual(("{0}", "controlled_commander"), (price["cost_text"], price["condition"]["kind"]))

    def test_malformed_policy_and_missing_modal_dependency_fail_closed(self):
        _, programs = self.original_programs("Redirect")
        spell = programs["resolve"]
        effect, = thaw_value(spell.effects)
        schema = thaw_value(spell.target_schema)
        for changes in (
            {"effects": [{**effect, OPTIONAL_STACK_TARGET_CHANGE_FIELD: True}]},
            {"effects": [{**effect, MANDATORY_STACK_TARGET_CHANGE_FIELD: 1}]},
            {"effects": [{**effect, "stack": "$source"}]},
            {"target_schema": {**schema, "source_exclusion": False}},
            {"target_schema": {**schema, "count": 2}},
        ):
            with self.subTest(changes=changes):
                runtime = bind_semantic_program_runtime(replace(spell, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(runtime["strict"], runtime)
        _, modal = self.original_programs("Insidious Will")
        spell = modal["resolve"]
        stripped = tuple(value for value in spell.capability_dependencies if value != ANNOUNCEMENT)
        closure = self.registry.closure(stripped, profile=PROFILE).to_dict()
        self.assertTrue(closure["trusted"], closure["blockers"])
        altered = replace(spell, capability_dependencies=stripped, capability_closure=closure,
                          provenance={**spell.provenance, "capability_closure_fingerprint": closure["fingerprint"]})
        result = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + ANNOUNCEMENT, result["blockers"])
        self.assertFalse(result["strict"], result)

    def _lands(self, game, seat, names):
        for index, name in enumerate(names):
            self.add(game.engine, name, seat=seat, ref=f"optional-{seat}-land-{index}")

    def _spell(self, game, card):
        item, = (item for item in game.state.stack if item.card_object_id == card.object_id)
        return item

    def _cast_mode(self, game, actor, spell, mode, targets):
        self.priority(game, actor)
        offered = self.action(game, actor, spell)
        result = game.act("pilot:" + actor, {
            "action_id": offered["id"], "modes": [mode], "targets": targets, "pay": "auto",
        })
        self.assertTrue(result.ok, result.summary)

    def _choice(self, game, actor):
        decision = self.resolve(game)
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.choice", [actor]), (decision.kind, decision.actors))
        schema = decision.payload_by_actor[actor]["legal_actions"][0]["choice_schema"]
        self.assertTrue(schema["optional"])
        self.assertTrue(schema["may_retain_each_default"])
        return schema

    def _choose(self, game, actor, fields):
        result = game.act("pilot:" + actor, {"action_id": "choose", **fields})
        self.assertTrue(result.ok, result.summary)

    def test_actual_redirect_keeps_a_lost_incarnation_or_selects_the_returned_object(self):
        for change in (False, True):
            with self.subTest(changing_target=change):
                game = self.session(77157001 + change)
                growth = self.add(game.engine, "Giant Growth", zone="hand")
                bear = self.add(game.engine, "Ashcoat Bear")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                redirect = self.add(game.engine, "Redirect", seat="C", zone="hand")
                private = self.add(game.engine, "Revitalize", seat="D", zone="hand", ref="optional-private-D")
                self._lands(game, "B", ("Island",))
                self._lands(game, "C", ("Island", "Island"))
                for card in (growth, bear, bounce, redirect):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"C": 1, "G": 2})
                self.checkpoint(game)
                old_id = bear.logical_object_id
                self.cast(game, "A", growth, [bear.ref])
                original = self._spell(game, growth)
                previous = deepcopy(original.context["target_snapshots"][bear.ref])
                self.cast(game, "B", bounce, [bear.ref])
                self.resolve_top(game)
                self.cast(game, "A", game.state.cards[bear.object_id])
                self.resolve_top(game)
                self.assertNotEqual(old_id, game.state.cards[bear.object_id].logical_object_id)
                self.cast(game, "C", redirect, [original.ref])
                schema = self._choice(game, "C")
                self.assertEqual([bear.ref], schema["default"])
                self.assertIn(bear.ref, schema["target_schema"]["legal_refs"])
                self.assertNotIn(private.ref, json.dumps(game.packet("pilot:C", full=True)))
                for observer in "ABD":
                    self.assertIsNone(game.packet("pilot:" + observer, full=True)["decision"])

                game = self.replay(game, load=True)
                self._choose(game, "C", {"targets": [{"ref": bear.ref}]} if change else {})
                retained = self._spell(game, growth)
                self.assertEqual("A", retained.controller)
                self.assertEqual(not change, previous == retained.context["target_snapshots"][bear.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(5 if change else 2, game.engine._numeric_stat(bear.object_id, "power"))
                self.replay(game, load=True)

    def test_actual_insidious_reselection_keeps_one_illegal_slot_and_original_shares(self):
        game = self.session(77157003)
        twin = self.add(game.engine, "Twin Bolt", zone="hand")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        spell = self.add(game.engine, "Insidious Will", seat="C", zone="hand")
        bear = self.add(game.engine, "Ashcoat Bear", seat="D")
        self._lands(game, "B", ("Island",))
        self._lands(game, "C", ("Island", "Island", "Forest", "Forest"))
        for card in (twin, bounce, spell, bear):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "R": 1})
        self.checkpoint(game)
        action = self.action(game, "A", twin)
        response = {"action_id": action["id"], "targets": ["B", bear.ref], "pay": "auto",
                    "damage_allocation": {"B": 1, bear.ref: 1}}
        self.assertTrue(game.act("pilot:A", response).ok)
        original = self._spell(game, twin)
        prior = deepcopy(original.context["target_snapshots"][bear.ref])
        allocated = deepcopy(original.context["announced_damage_allocation"])
        self.cast(game, "B", bounce, [bear.ref])
        self.resolve_top(game)
        self._cast_mode(game, "C", spell, "mode_2", [original.ref])
        schema = self._choice(game, "C")
        self.assertEqual(["B", bear.ref], schema["default"])
        before = authoritative_state_hash(game.state)
        bad = game.act("pilot:C", {"action_id": "choose", "targets": ["C"]})
        self.assertFalse(bad.ok, bad.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self._choose(game, "C", {"targets": ["C", {"retain": 1}]})
        current = self._spell(game, twin)
        self.assertEqual(["C", bear.ref], current.targets)
        self.assertEqual(prior, current.context["target_snapshots"][bear.ref])
        self.assertEqual(allocated, current.context["announced_damage_allocation"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((40, 39), (game.state.players["B"].life, game.state.players["C"].life))
        self.assertEqual("hand", game.state.cards[bear.object_id].zone)
        self.replay(game, load=True)

    def test_actual_redirect_can_target_a_spell_with_no_announced_targets(self):
        game = self.session(77157004)
        original = self.add(game.engine, "Revitalize", zone="hand")
        redirect = self.add(game.engine, "Redirect", seat="B", zone="hand")
        self._lands(game, "B", ("Island", "Island"))
        for card in (original, redirect):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast(game, "A", original)
        original_ref = self._spell(game, original).ref
        self.priority(game, "B")
        offered = self.action(game, "B", redirect)
        self.assertIn(original_ref, offered["target_schema"]["legal_refs"])
        self.cast(game, "B", redirect, [original_ref])
        self.resolve_top(game)
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(43, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_swat_uses_commanders_price_and_announces_ward_to_original_controller(self):
        game = self.session(77157005)
        sorcerer = self.add(game.engine, "Prodigal Sorcerer")
        swat = self.add(game.engine, "Deflecting Swat", seat="B", zone="hand")
        terror = self.add(game.engine, "Tolarian Terror", seat="C")
        commander_id, = [object_id for object_id in game.state.players["B"].zones["command"]
                        if game.state.cards[object_id].is_commander]
        game.engine.move_card(commander_id, "battlefield", controller="B", log=False)
        for card in (sorcerer, swat, terror):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        action, = (row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith("activate:" + sorcerer.ref + ":"))
        accepted = game.act("pilot:A", {"action_id": action["id"], "targets": ["D"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        ability, = (item for item in game.state.stack if item.kind == "activated_ability")

        self.priority(game, "B")
        offered = self.action(game, "B", swat)
        free, = offered["cost_options"]
        accepted = game.act("pilot:B", {"action_id": offered["id"], "targets": [ability.ref],
                                       "cost_option": free["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(free["id"], self._spell(game, swat).context["cost_option"])
        schema = self._choice(game, "B")
        self.assertEqual(["D"], schema["default"])
        self._choose(game, "B", {"targets": [terror.ref]})
        ward, = (item for item in game.state.stack if item.semantic_key == "builtin:ward")
        self.assertEqual(("C", "A", ability.ref), (ward.controller, ward.context["payer"], ward.context["target_stack"]))
        pending = self.resolve(game)
        self.assertEqual(["A"], pending.actors)
        self._choose(game, "A", {"pay": False})
        self.assertIsNone(self.resolve(game))
        self.assertEqual((0, 40), (game.state.cards[terror.object_id].marked_damage, game.state.players["D"].life))
        self.replay(game, load=True)

    def test_actual_insidious_counter_and_copy_siblings_are_still_executable(self):
        for mode in ("mode_1", "mode_3"):
            with self.subTest(mode=mode):
                game = self.session(77157006 if mode == "mode_1" else 77157007)
                shock = self.add(game.engine, "Shock", zone="hand")
                will = self.add(game.engine, "Insidious Will", seat="C", zone="hand")
                self._lands(game, "C", ("Island", "Island", "Forest", "Forest"))
                for card in (shock, will):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"R": 1})
                self.checkpoint(game)
                self.cast(game, "A", shock, ["D"])
                self._cast_mode(game, "C", will, mode, [self._spell(game, shock).ref])
                if mode == "mode_3":
                    choice = self._choice(game, "C")
                    self.assertEqual(["D"], choice["default"])
                    self._choose(game, "C", {"targets": ["B"]})
                    copy, = (item for item in game.state.stack if item.kind == "spell_copy")
                    self.assertEqual(("C", ["B"]), (copy.controller, copy.targets))
                self.assertIsNone(self.resolve(game))
                expected = 38 if mode == "mode_3" else 40
                self.assertEqual((expected, expected), (game.state.players["B"].life, game.state.players["D"].life))
                self.replay(game, load=True)

    def test_actual_legacy_price_key_cannot_authorize_an_unoffered_free_cast(self):
        game = self.session(77157008)
        original = self.add(game.engine, "Shock", zone="hand")
        swat = self.add(game.engine, "Deflecting Swat", seat="B", zone="hand")
        self._lands(game, "B", ("Mountain", "Forest", "Forest"))
        for card in (original, swat):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1})
        self.checkpoint(game)
        self.cast(game, "A", original, ["D"])
        subject = self._spell(game, original).ref
        self.priority(game, "B")
        action = self.action(game, "B", swat)
        option, = action["cost_options"]
        self.assertEqual("mana", option["kind"])
        command = {"action_id": action["id"], "targets": [subject], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        declined = game.act("pilot:B", {**command, "cost_option": "commander_free"})
        self.assertFalse(declined.ok, declined.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:B", {**command, "cost_option": option["id"]})
        self.assertTrue(accepted.ok, accepted.summary)
        self._choice(game, "B")
        self._choose(game, "B", {})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_missing_optional_lowering_keeps_every_original_unready(self):
        with patch("quorune.compiler.resolution_effect_templates._optional_stack_target_change_template", return_value=None):
            for name in NAMES:
                victim, _ = register_card(self.db, self.registry, name)
                self.assertFalse(victim["strict_capability_ready"], victim)
            control, _ = register_card(self.db, self.registry, "Revitalize")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_verified_withdrawals_require_optional_target_and_announcement_owners(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in ("target.revalidate_resolution", ANNOUNCEMENT):
            with self.subTest(capability=capability):
                candidate = deepcopy(raw)
                next(row for row in candidate["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Optional target change authority withdrawn"])
                registry = CapabilityRegistry(candidate)
                index = build_index(registry_value=candidate, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in NAMES:
                    binding, _ = register_card(self.db, registry, name)
                    self.assertFalse(binding["strict_capability_ready"], binding)
                unaffected, _ = register_card(self.db, registry, "Revitalize")
                self.assertTrue(unaffected["strict_capability_ready"], unaffected["blockers"])

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
    STACK_SPELL_CONTROL_CONTEXT, STACK_SPELL_CONTROL_FIELD, StackSpellControlHistory, StackSpellControlSpec,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
NAMES = ("Aethersnatch", "Commandeer")
FIXTURES = ROOT / "tests" / "fixtures"
CONTROL = "continuous.control.fixed_resolution"
ANNOUNCEMENT = "trigger.event.normalized_target_announcement"


class TargetedSpellControlTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Island", "Unsummon"},
            "ability-source-counter-cards.json": {"Counterspell"},
            "activation-public-state-cards.json": {"Giant Growth"},
            "activation-restriction-material-cards.json": {"Memnite"},
            "additional-conditional-modifier-cards.json": {"Revitalize"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "fixed-stack-spell-copy-cards.json": {"Twincast"},
            "mandatory-single-stack-target-change-cards.json": {"Tolarian Terror"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "targeted-spell-control-cards.json": {"Aethersnatch", "Bump in the Night", "Commandeer", "Scornful Egotist"},
            "targeted-zero-mana-counter-cards.json": {"Nix"},
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
        database = folder / "targeted-spell-control.sqlite3"
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Targeted spell control", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_programs(self, name):
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding)
        return card, {p.event: p for p in semantics.programs_for_oracle(card.oracle_id)}

    def test_two_complete_originals_bind_exact_order_and_retain_commandeer_pitch(self):
        for name in NAMES:
            with self.subTest(name=name):
                card, programs = self.original_programs(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                spell = programs["resolve"]
                spec = StackSpellControlSpec(name == "Commandeer")
                self.assertTrue(program_has_declared_complete_source_admission(spell))
                self.assertEqual(list(spec.effects), thaw_value(spell.effects))
                self.assertEqual(spec.target_schema, thaw_value(spell.target_schema))
                self.assertTrue({CONTROL, ANNOUNCEMENT}.issubset(spell.capability_dependencies))
                if name == "Commandeer":
                    self.assertIn("casting.alternative_cost.fixed_public", programs["cast.cost"].capability_dependencies)

    def test_malformed_order_scope_marker_and_custody_history_reject(self):
        _, programs = self.original_programs("Aethersnatch")
        spell = programs["resolve"]
        acquire, choose = thaw_value(spell.effects)
        schema = thaw_value(spell.target_schema)
        for changes in (
            {"effects": [choose, acquire]},
            {"effects": [acquire]},
            {"effects": [{**acquire, STACK_SPELL_CONTROL_FIELD: True}, choose]},
            {"effects": [{**acquire, "controller": "$owner"}, choose]},
            {"effects": [{**acquire, "stack": "$source"}, choose]},
            {"target_schema": {**schema, "categories": ["ability"]}},
            {"target_schema": {**schema, "count": 2}},
        ):
            with self.subTest(changes=changes):
                result = bind_semantic_program_runtime(replace(spell, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)
        history = StackSpellControlHistory("A").acquire("B").acquire("C").acquire("B")
        self.assertEqual(("C", "B"), history.acquired_controllers)
        self.assertEqual(history, StackSpellControlHistory.from_dict(history.to_dict()))
        self.assertEqual("C", history.without({"B"}).controller)
        self.assertEqual("A", history.without({"B", "C"}).controller)
        with self.assertRaises(ValueError):
            StackSpellControlHistory.from_dict({**history.to_dict(), "schema_version": True})

    @staticmethod
    def _fund(game, seat, mana):
        game.state.players[seat].mana_pool.update(mana)

    def _spell(self, game, card):
        item, = (item for item in game.state.stack if item.card_object_id == card.object_id)
        return item

    def _choose(self, game, actor, fields):
        result = game.act("pilot:" + actor, {"action_id": "choose", **fields})
        self.assertTrue(result.ok, result.summary)

    def _concede(self, game, seat):
        self.priority(game, seat)
        result = game.act("pilot:" + seat, {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)

    def test_actual_aethersnatch_controls_the_resolving_commander_and_preserves_owner(self):
        game = self.session(77112001)
        object_id, = [value for value in game.state.players["A"].zones["command"]
                     if game.state.cards[value].is_commander]
        commander = game.state.cards[object_id]
        takeover = self.add(game.engine, "Aethersnatch", seat="B", zone="hand")
        self.assert_closed(game, takeover)
        self.prepare(game, mana={"W": 1})
        self._fund(game, "B", {"C": 4, "U": 2})
        self.checkpoint(game)
        self.cast(game, "A", commander)
        subject = self._spell(game, commander)
        self.cast(game, "B", takeover, [subject.ref])
        self.resolve_top(game)
        item = self._spell(game, commander)
        physical = game.state.cards[object_id]
        self.assertEqual((subject.ref, "B", "A", "B"), (item.ref, item.controller, physical.owner, physical.controller))
        self.assertIsNone(self.resolve(game))
        physical = game.state.cards[object_id]
        self.assertEqual(("battlefield", "A", "B", True), (physical.zone, physical.owner, physical.controller, physical.is_commander))
        self.assertIn(object_id, game.state.players["B"].zones["battlefield"])
        self.replay(game, load=True)

    def test_actual_commandeer_pitch_changes_controller_before_choosing_an_opponent(self):
        for change in (False, True):
            with self.subTest(choose_new_target=change):
                game = self.session(77112002 + change)
                original = self.add(game.engine, "Bump in the Night", zone="hand")
                takeover = self.add(game.engine, "Commandeer", seat="B", zone="hand")
                first = self.add(game.engine, "Counterspell", seat="B", zone="hand")
                second = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                for card in (original, takeover, first, second):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"B": 1})
                self.checkpoint(game)
                self.cast(game, "A", original, ["B"])
                subject = self._spell(game, original)
                receipt = deepcopy(subject.to_dict()["mana_spent_counts"])
                self.priority(game, "B")
                action = self.action(game, "B", takeover)
                pitch, = [row for row in action["cost_options"] if row["choice_schema"].get("exile_cards")]
                cast = game.act("pilot:B", {"action_id": action["id"], "targets": [subject.ref], "cost_option": pitch["id"],
                                           "exile_cards": [first.ref, second.ref], "pay": "auto"})
                self.assertTrue(cast.ok, cast.summary)
                self.assertEqual(["exile", "exile"], [game.state.cards[card.object_id].zone for card in (first, second)])

                pending = self.resolve(game)
                self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
                checked = self._spell(game, original)
                self.assertEqual(("B", receipt), (checked.controller, checked.to_dict()["mana_spent_counts"]))
                schema = pending.payload_by_actor["B"]["legal_actions"][0]["choice_schema"]
                self.assertEqual(["B"], schema["default"])
                self.assertEqual({"A", "C", "D"}, set(schema["target_schema"]["legal_refs"]))
                before = authoritative_state_hash(game.state)
                invalid = game.act("pilot:B", {"action_id": "choose", "targets": [{"ref": "B"}]})
                self.assertFalse(invalid.ok, invalid.summary)
                self.assertEqual(before, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                self._choose(game, "B", {"targets": ["D"]} if change else {})
                self.assertIsNone(self.resolve(game))
                self.assertEqual(37 if change else 40, game.state.players["D"].life)
                self.assertEqual(40, game.state.players["B"].life)
                physical = game.state.cards[original.object_id]
                self.assertEqual(("A", "graveyard"), (physical.owner, physical.zone))
                self.replay(game, load=True)

    def test_actual_taken_face_down_spell_opens_identity_only_to_its_new_controller(self):
        game = self.session(77112004)
        creature = self.add(game.engine, "Scornful Egotist", zone="hand")
        takeover = self.add(game.engine, "Aethersnatch", seat="B", zone="hand")
        for card in (creature, takeover):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3})
        self._fund(game, "B", {"C": 4, "U": 2})
        self.checkpoint(game)
        action = "cast-morph:" + creature.ref
        cast = game.act("pilot:A", {"action_id": action, "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        subject = self._spell(game, creature)
        self.assertNotIn("Scornful Egotist", json.dumps(game.packet("pilot:B", full=True)))
        self.cast(game, "B", takeover, [subject.ref])
        self.resolve_top(game)
        self.assertEqual("B", self._spell(game, creature).controller)
        self.assertIn("Scornful Egotist", json.dumps(game.packet("pilot:B", full=True)))
        for observer in "CD":
            self.assertNotIn("Scornful Egotist", json.dumps(game.packet("pilot:" + observer, full=True)))
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[creature.object_id]
        self.assertEqual(("battlefield", "B", "A", True), (current.zone, current.controller, current.owner, current.face_down))
        self.replay(game, load=True)

    def test_actual_departures_unwind_stack_control_to_earlier_player_then_default(self):
        game = self.session(77112005)
        original = self.add(game.engine, "Revitalize", zone="hand")
        first = self.add(game.engine, "Aethersnatch", seat="B", zone="hand", ref="stack-grant-earlier")
        second = self.add(game.engine, "Aethersnatch", seat="C", zone="hand", ref="stack-grant-later")
        for card in (original, first, second):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 1})
        for seat in "BC":
            self._fund(game, seat, {"C": 4, "U": 2})
        self.checkpoint(game)
        self.cast(game, "A", original)
        subject = self._spell(game, original)
        self.cast(game, "B", first, [subject.ref])
        self.resolve_top(game)
        self.cast(game, "C", second, [subject.ref])
        self.resolve_top(game)
        item = self._spell(game, original)
        self.assertEqual("C", item.controller)
        history = StackSpellControlHistory.from_dict(item.context[STACK_SPELL_CONTROL_CONTEXT])
        self.assertEqual(("A", ("B", "C")), (history.default_controller, history.acquired_controllers))

        self._concede(game, "C")
        self.assertEqual("B", self._spell(game, original).controller)
        self.assertEqual("B", game.state.cards[original.object_id].controller)
        self._concede(game, "B")
        self.assertEqual("A", self._spell(game, original).controller)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(43, game.state.players["A"].life)
        self.assertEqual("graveyard", game.state.cards[original.object_id].zone)
        self.replay(game, load=True)

    def test_actual_commandeer_excludes_a_creature_and_nix_reads_its_original_receipt(self):
        game = self.session(77112006)
        original = self.add(game.engine, "Memnite", zone="hand")
        takeover = self.add(game.engine, "Aethersnatch", seat="B", zone="hand")
        restricted = self.add(game.engine, "Commandeer", seat="B", zone="hand")
        self.add(game.engine, "Counterspell", seat="B", zone="hand")
        self.add(game.engine, "Unsummon", seat="B", zone="hand")
        nix = self.add(game.engine, "Nix", seat="C", zone="hand")
        for card in (original, takeover, restricted, nix):
            self.assert_closed(game, card)
        self.prepare(game)
        self._fund(game, "B", {"C": 4, "U": 2})
        self._fund(game, "C", {"U": 1})
        self.checkpoint(game)
        self.cast(game, "A", original)
        subject = self._spell(game, original)
        paid = deepcopy(subject.to_dict()["mana_spent_counts"])
        self.priority(game, "B")
        legal = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertNotIn("cast:" + restricted.ref, {row["id"] for row in legal})
        self.cast(game, "B", takeover, [subject.ref])
        self.resolve_top(game)
        item = self._spell(game, original)
        self.assertEqual(("B", paid), (item.controller, item.to_dict()["mana_spent_counts"]))
        self.cast(game, "C", nix, [item.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[original.object_id].zone)
        self.assertIn(original.object_id, game.state.players["A"].zones["graveyard"])
        self.replay(game, load=True)

    def test_actual_taken_spell_copy_resolves_for_new_controller_with_distinct_origin(self):
        game = self.session(77112007)
        original = self.add(game.engine, "Revitalize", zone="hand")
        copy = self.add(game.engine, "Twincast", seat="B", zone="hand")
        takeover = self.add(game.engine, "Aethersnatch", seat="C", zone="hand")
        for card in (original, copy, takeover):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 1})
        self._fund(game, "B", {"U": 2})
        self._fund(game, "C", {"C": 4, "U": 2})
        self.checkpoint(game)
        self.cast(game, "A", original)
        self.cast(game, "B", copy, [self._spell(game, original).ref])
        self.resolve_top(game)
        copied, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual("B", copied.controller)
        self.cast(game, "C", takeover, [copied.ref])
        self.resolve_top(game)
        controlled, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual((copied.ref, "C"), (controlled.ref, controlled.controller))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((43, 40, 43), tuple(game.state.players[seat].life for seat in "ABC"))
        self.assertEqual(3, len(game.engine._current_turn_history("spell_cast")))
        self.replay(game, load=True)

    def test_actual_lost_stack_target_prevents_control_and_reselection(self):
        game = self.session(77112008)
        original = self.add(game.engine, "Revitalize", zone="hand")
        takeover = self.add(game.engine, "Aethersnatch", seat="B", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="C", zone="hand")
        for card in (original, takeover, counter):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 1})
        self._fund(game, "B", {"C": 4, "U": 2})
        self._fund(game, "C", {"U": 2})
        self.checkpoint(game)
        self.cast(game, "A", original)
        subject = self._spell(game, original)
        self.cast(game, "B", takeover, [subject.ref])
        self.cast(game, "C", counter, [subject.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[original.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[takeover.object_id].zone)
        self.assertEqual((40, 40), (game.state.players["A"].life, game.state.players["B"].life))
        self.replay(game, load=True)

    def test_actual_taken_spell_announces_a_new_ward_target_from_its_new_controller(self):
        game = self.session(77112009)
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        first = self.add(game.engine, "Memnite")
        terror = self.add(game.engine, "Tolarian Terror", seat="C")
        takeover = self.add(game.engine, "Aethersnatch", seat="B", zone="hand")
        for card in (growth, first, terror, takeover):
            self.assert_closed(game, card)
        self.prepare(game, mana={"G": 1})
        self._fund(game, "B", {"C": 4, "U": 2})
        self.checkpoint(game)
        self.cast(game, "A", growth, [first.ref])
        subject = self._spell(game, growth)
        self.cast(game, "B", takeover, [subject.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        before = authoritative_state_hash(game.state)
        bad = game.act("pilot:A", {"action_id": "choose", "targets": [terror.ref]})
        self.assertFalse(bad.ok, bad.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self._choose(game, "B", {"targets": [terror.ref]})
        ward, = (item for item in game.state.stack if item.semantic_key == "builtin:ward")
        self.assertEqual(("C", "B", subject.ref),
                         (ward.controller, ward.context["payer"], ward.context["target_stack"]))
        choice = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (choice.kind, choice.actors))
        self._choose(game, "B", {"pay": False})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[growth.object_id].zone)
        self.assertEqual(5, game.engine._numeric_stat(terror.object_id, "power"))
        self.replay(game, load=True)

    def test_disabling_exact_control_lowering_restores_both_original_residuals(self):
        with patch("quorune.compiler.resolution_effect_templates._stack_spell_control_template", return_value=None):
            for name in NAMES:
                outcome, _ = register_card(self.db, self.registry, name)
                self.assertFalse(outcome["strict_capability_ready"], outcome)
            retained, _ = register_card(self.db, self.registry, "Revitalize")
            self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

    def test_verified_withdrawals_require_control_and_target_announcement_owners(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (CONTROL, ANNOUNCEMENT):
            with self.subTest(capability=capability):
                candidate = deepcopy(raw)
                next(row for row in candidate["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Spell custody authority withdrawn"])
                registry = CapabilityRegistry(candidate)
                index = build_index(registry_value=candidate, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in NAMES:
                    blocked, _ = register_card(self.db, registry, name)
                    self.assertFalse(blocked["strict_capability_ready"], blocked)
                retained, _ = register_card(self.db, registry, "Revitalize")
                self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

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
    MANDATORY_STACK_TARGET_CHANGE_FIELD, SingleStackTargetChangeSpec,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
NAMES = ("Bolt Bend", "Deflection", "Misdirection", "Redirect Lightning", "Shunt", "Swerve")
FIXTURES = ROOT / "tests" / "fixtures"
TARGET_ANNOUNCEMENT = "trigger.event.normalized_target_announcement"


class MandatorySingleStackTargetChangeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Forest", "Island", "Plains", "Unsummon"},
            "ability-source-counter-cards.json": {"Counterspell", "Shock"},
            "activation-public-state-cards.json": {"Giant Growth"},
            "activation-result-views-cards.json": {"Mountain"},
            "additional-conditional-modifier-cards.json": {"Revitalize"},
            "announced-x-player-rounds-cards.json": {"Myr Sire"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Grizzly Bears", "Murder"},
            "cast-creature-targets.json": {"Common Bond"},
            "counter-replacement-cards.json": {"Ancient Brontodon"},
            "designation-prevention-cards.json": {"Prodigal Sorcerer"},
            "event-spell-copy-cards.json": {"Deflection"},
            "mandatory-single-stack-target-change-cards.json": {
                "Bolt Bend", "Misdirection", "Redirect Lightning", "Remove Soul",
                "Shunt", "Swerve", "Tolarian Terror", "Twin Bolt", "Imp's Mischief",
            },
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "spree-mode-cost-cards.json": {"Return the Favor"},
            "stack-mana-value-counter-followups-cards.json": {"Blue Sun's Zenith", "Twincast"},
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
        database = folder / "mandatory-single-stack-target-changes.sqlite3"
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Mandatory stack target choices", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_programs(self, name):
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        return card, {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)}

    def test_complete_originals_keep_each_independent_cast_cost(self):
        for name in NAMES:
            with self.subTest(name=name):
                card, programs = self.original_programs(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                spell = programs["resolve"]
                spec = SingleStackTargetChangeSpec(name in {"Bolt Bend", "Redirect Lightning"})
                self.assertTrue(program_has_declared_complete_source_admission(spell))
                self.assertEqual(list(spec.effects), thaw_value(spell.effects))
                self.assertEqual(spec.target_schema, thaw_value(spell.target_schema))
                self.assertIn(TARGET_ANNOUNCEMENT, spell.capability_dependencies)
                if name == "Misdirection":
                    self.assertIn("casting.alternative_cost.fixed_public", programs["cast.cost"].capability_dependencies)
                if name == "Bolt Bend":
                    self.assertIn("casting.cost.self_public_reduction", programs["cast.cost.modify"].capability_dependencies)
                if name == "Redirect Lightning":
                    options = spell.cost_schema["additional_costs"][0]["options"]
                    self.assertEqual(5, options[0]["amount"])
                    self.assertEqual(2, options[1]["requirements"]["GENERIC"])

    def test_invalid_scope_marker_and_missing_source_certificate_fail_closed(self):
        _, original = self.original_programs("Deflection")
        spell = original["resolve"]
        effect, = thaw_value(spell.effects)
        schema = thaw_value(spell.target_schema)
        for changes in (
            {"effects": [{**effect, MANDATORY_STACK_TARGET_CHANGE_FIELD: True}]},
            {"effects": [{**effect, "stack": "$source"}]},
            {"effects": [{**effect, "retain_targets_only": True}]},
            {"target_schema": {**schema, "count": 2}},
            {"target_schema": {**schema, "predicate": "damageable"}},
            {"active_zone": "graveyard"},
            {"provenance": {k: v for k, v in spell.provenance.items() if k != "card_program_admission"}},
        ):
            with self.subTest(changes=changes):
                result = bind_semantic_program_runtime(replace(spell, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)

    def _lands(self, game, seat, names):
        return [self.add(game.engine, name, seat=seat, ref=f"redirect-{seat}-land-{index}")
                for index, name in enumerate(names)]

    def _spell(self, game, card):
        item, = (item for item in game.state.stack if item.card_object_id == card.object_id)
        return item

    @staticmethod
    def _item(game, ref):
        return next(item for item in game.state.stack if item.ref == ref)

    def _choice(self, game, actor):
        self.resolve(game)
        decision = game.state.pending_decision
        self.assertEqual(("semantic.choice", [actor]), (decision.kind, decision.actors))
        schema = decision.payload_by_actor[actor]["legal_actions"][0]["choice_schema"]
        self.assertFalse(schema["optional"])
        self.assertEqual([], list(schema["default"]))
        return tuple(schema["target_schema"]["legal_refs"])

    def _choose(self, game, actor, target):
        result = game.act("pilot:" + actor, {"action_id": "choose", "targets": [target]})
        self.assertTrue(result.ok, result.summary)

    def _cast_favor(self, game, favor, actor, modes, targets):
        self.priority(game, actor)
        command = {"action_id": "cast:" + favor.ref, "modes": modes, "targets": targets, "pay": "auto"}
        result = game.act("pilot:" + actor, command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_favor_copies_then_changes_the_same_original_spell_and_replays(self):
        game = self.session(70710051)
        creature = self.add(game.engine, "Grizzly Bears", zone="hand")
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        favor = self.add(game.engine, "Return the Favor", seat="C", zone="hand")
        private = self.add(game.engine, "Revitalize", seat="D", zone="hand", ref="favor-private-D")
        for card in (creature, shock, favor):
            self.assert_closed(game, card)
        self.prepare(game, mana={"G": 1, "C": 1})
        game.state.players["B"].mana_pool["R"] = 1
        game.state.players["C"].mana_pool.update({"R": 2, "C": 2})
        self.checkpoint(game)
        self.cast(game, "A", creature)
        self.cast(game, "B", shock, ["D"])
        original = self._spell(game, shock).ref
        self.priority(game, "C")
        before = authoritative_state_hash(game.state)
        illegal = game.act("pilot:C", {
            "action_id": "cast:" + favor.ref, "modes": ["mode_1"],
            "targets": [self._spell(game, creature).ref], "pay": "auto",
        })
        self.assertFalse(illegal.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self._cast_favor(game, favor, "C", ["mode_2", "mode_1"], [original, original])
        item = self._spell(game, favor)
        self.assertEqual(["mode_1", "mode_2"], item.modes)
        self.assertEqual(0, sum(game.state.players["C"].mana_pool.values()))

        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["C"]), (decision.kind, decision.actors))
        schema = decision.payload_by_actor["C"]["legal_actions"][0]["choice_schema"]
        self.assertTrue(schema["optional"])
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:C", full=True)))
        for seat in "ABD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        game = self.replay(game, load=True)
        self._choose(game, "C", "A")

        self.assertIn("B", self._choice(game, "C"))
        copy, = [row for row in game.state.stack if row.kind == "spell_copy"]
        self.assertEqual(("C", ["A"]), (copy.controller, copy.targets))
        self.assertEqual(("B", ["D"]), (self._item(game, original).controller, self._item(game, original).targets))
        self._choose(game, "C", "B")
        self.assertEqual(["B"], self._item(game, original).targets)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([38, 38, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(3, len(game.engine._current_turn_history("spell_cast")))
        self.replay(game, load=True)

    def test_actual_favor_copies_other_controllers_activated_and_triggered_abilities(self):
        for triggered in (False, True):
            with self.subTest(triggered=triggered):
                game = self.session(70710052 + triggered)
                source = self.add(game.engine, "Myr Sire" if triggered else "Prodigal Sorcerer")
                favor = self.add(game.engine, "Return the Favor", seat="B", zone="hand")
                if triggered:
                    removal = self.add(game.engine, "Murder", seat="C", zone="hand")
                for card in (source, favor):
                    self.assert_closed(game, card)
                self.prepare(game)
                game.state.players["B"].mana_pool.update({"R": 2, "C": 1})
                game.state.players["C"].mana_pool.update({"B": 2, "C": 1})
                self.checkpoint(game)
                if triggered:
                    self.cast(game, "C", removal, [source.ref])
                    self.resolve_top(game)
                    self.until(game, lambda session: any(row.kind == "triggered_ability" for row in session.state.stack))
                else:
                    self.priority(game, "A")
                    actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    action, = [row for row in actions if row["id"].startswith("activate:" + source.ref + ":")]
                    result = game.act("pilot:A", {"action_id": action["id"], "targets": ["D"], "pay": "auto"})
                    self.assertTrue(result.ok, result.summary)
                original, = [row for row in game.state.stack if row.kind.endswith("_ability")]
                self.assertEqual("A", original.controller)
                self._cast_favor(game, favor, "B", ["mode_1"], [original.ref])
                decision = self.resolve(game)
                if triggered:
                    self.assertIsNone(decision)
                    controllers = [card.controller for card in game.state.cards.values()
                                   if card.is_token and card.zone == "battlefield"]
                    self.assertCountEqual(["A", "B"], controllers)
                else:
                    self.assertEqual(("semantic.choice", ["B"]), (decision.kind, decision.actors))
                    self._choose(game, "B", "C")
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual((39, 39), (game.state.players["C"].life, game.state.players["D"].life))
                    self.assertTrue(game.state.cards[source.object_id].tapped)
                self.assertEqual(0, sum(game.state.players["B"].mana_pool.values()))
                self.replay(game, load=True)

    def test_actual_favor_skips_its_departed_copy_target_and_resolves_the_other_mode(self):
        game = self.session(70710054)
        first = self.add(game.engine, "Shock", zone="hand", ref="favor-first-shock")
        second = self.add(game.engine, "Shock", seat="B", zone="hand", ref="favor-second-shock")
        favor = self.add(game.engine, "Return the Favor", seat="C", zone="hand")
        response = self.add(game.engine, "Counterspell", seat="D", zone="hand")
        self.assert_closed(game, favor)
        self.prepare(game, mana={"R": 1})
        game.state.players["B"].mana_pool["R"] = 1
        game.state.players["C"].mana_pool.update({"R": 2, "C": 2})
        game.state.players["D"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast(game, "A", first, ["D"])
        self.cast(game, "B", second, ["D"])
        first_ref, second_ref = (self._spell(game, card).ref for card in (first, second))
        self._cast_favor(game, favor, "C", ["mode_1", "mode_2"], [first_ref, second_ref])
        self.cast(game, "D", response, [first_ref])
        self.resolve_top(game)

        self.assertIn("A", self._choice(game, "C"))
        self.assertFalse(any(row.kind == "spell_copy" for row in game.state.stack))
        self._choose(game, "C", "A")
        self.assertEqual(["A"], self._item(game, second_ref).targets)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((38, 40), (game.state.players["A"].life, game.state.players["D"].life))
        self.replay(game, load=True)

    def test_imps_mischief_binds_only_the_exact_stack_target_and_life_loss(self):
        from quorune.rules.stack_target_change_shapes import STACK_TARGET_CHANGE_MANA_VALUE_LOSS_CAPABILITY
        from quorune.stack_target_change_model import stack_target_change_with_mana_value_loss_effects

        card, programs = self.original_programs("Imp's Mischief")
        spell = programs["resolve"]
        expected = stack_target_change_with_mana_value_loss_effects()
        self.assertEqual(list(expected), thaw_value(spell.effects))
        self.assertEqual(SingleStackTargetChangeSpec(False).target_schema, thaw_value(spell.target_schema))
        self.assertIn(STACK_TARGET_CHANGE_MANA_VALUE_LOSS_CAPABILITY, spell.capability_dependencies)
        with patch("quorune.compiler.resolution_effect_templates._stack_target_change_mana_value_loss_template",
                   return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                card, capability_registry=self.registry, capability_profile=PROFILE).status)
        for actor, stack, version in (("$target.0", "$target.0", 1),
                                      ("$controller", "$target.1", 1),
                                      ("$controller", "$target.0", True)):
            with self.subTest(actor=actor, stack=stack, version=version):
                loss = {**expected[1], "player": actor, "amount": {**expected[1]["amount"],
                                                                 "stack": stack, "schema_version": version}}
                altered = replace(spell, effects=[expected[0], loss])
                bound = bind_semantic_program_runtime(
                    altered, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound)

    def test_imps_mischief_redirects_a_copied_announced_x_and_replays_deferred_life_loss(self):
        game = self.session(77115011)
        original = self.add(game.engine, "Blue Sun's Zenith", seat="B", zone="hand")
        twincast = self.add(game.engine, "Twincast", zone="hand")
        redirect = self.add(game.engine, "Imp's Mischief", seat="C", zone="hand")
        private = self.add(game.engine, "Revitalize", seat="D", zone="hand", ref="mischief-private-D")
        for card in (original, twincast, redirect):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"U": 2})
        game.state.players["B"].mana_pool["U"] = 6
        game.state.players["C"].mana_pool.update({"C": 1, "B": 1})
        self.checkpoint(game)
        self.priority(game, "B")
        offer = self.action(game, "B", original)
        result = game.act("pilot:B", {"action_id": offer["id"], "targets": ["B"], "x": 3, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        original_ref = self._spell(game, original).ref
        self.cast(game, "A", twincast, [original_ref])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self._choose(game, "A", "C")
        copy = game.state.stack[-1]
        self.assertEqual(("spell_copy", 3), (copy.kind, copy.x_value))

        life = game.state.players["C"].life
        self.cast(game, "C", redirect, [copy.ref])
        self.assertIn("D", self._choice(game, "C"))
        self.assertEqual(life, game.state.players["C"].life)
        outsider = game.packet("pilot:D", full=True)
        self.assertIsNone(outsider["decision"])
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:C", full=True)))
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        for actor, target in (("A", "D"), ("C", "C")):
            invalid = game.act(f"pilot:{actor}", {"action_id": "choose", "targets": [target]})
            self.assertFalse(invalid.ok, invalid.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self._choose(game, "C", "D")
        self.assertEqual(life - 6, game.state.players["C"].life)
        self.assertEqual(("A", ["D"]), (self._item(game, copy.ref).controller, self._item(game, copy.ref).targets))
        self.assertEqual(["B"], self._item(game, original_ref).targets)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life - 6, game.state.players["C"].life)
        self.replay(game, load=True)

    def test_imps_mischief_loses_life_without_an_alternative_and_skips_an_illegal_spell_target(self):
        for remove_subject in (False, True):
            with self.subTest(remove_subject=remove_subject):
                game = self.session(77115012 + int(remove_subject))
                creature = self.add(game.engine, "Ancient Brontodon", zone="hand")
                counter = self.add(game.engine, "Remove Soul", seat="B", zone="hand")
                redirect = self.add(game.engine, "Imp's Mischief", seat="C", zone="hand")
                response = self.add(game.engine, "Counterspell", seat="D", zone="hand") if remove_subject else None
                for card in (creature, counter, redirect):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"C": 9, "G": 2})
                game.state.players["B"].mana_pool["U"] = 2
                game.state.players["C"].mana_pool.update({"C": 1, "B": 1})
                game.state.players["D"].mana_pool["U"] = 2
                self.checkpoint(game)
                self.cast(game, "A", creature)
                original = self._spell(game, creature).ref
                self.cast(game, "B", counter, [original])
                subject = self._spell(game, counter).ref
                self.cast(game, "C", redirect, [subject])
                life = game.state.players["C"].life
                if response is not None:
                    self.cast(game, "D", response, [subject])
                    self.resolve_top(game)
                self.resolve_top(game)
                self.assertEqual(life if remove_subject else life - 2, game.state.players["C"].life)
                if not remove_subject:
                    self.assertEqual([original], self._item(game, subject).targets)
                self.replay(game, load=True)

    def test_actual_deflection_changes_counter_to_the_resolving_spell_and_replays(self):
        game = self.session(77115001)
        shock = self.add(game.engine, "Shock", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        redirect = self.add(game.engine, "Deflection", seat="C", zone="hand")
        private = self.add(game.engine, "Revitalize", seat="D", zone="hand", ref="redirect-private-D")
        self._lands(game, "B", ("Island", "Island"))
        self._lands(game, "C", ("Island", "Forest", "Forest", "Forest"))
        for card in (shock, counter, redirect):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1})
        self.checkpoint(game)

        self.cast(game, "A", shock, ["D"])
        original = self._spell(game, shock).ref
        self.cast(game, "B", counter, [original])
        spell = self._spell(game, counter).ref
        self.cast(game, "C", redirect, [spell])
        resolving = self._spell(game, redirect).ref
        self.assertEqual((resolving,), self._choice(game, "C"))
        for seat in "ABD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:C", full=True)))
        before = authoritative_state_hash(game.state)
        for actor, fields in (("A", {"targets": [resolving]}), ("C", {"targets": [original]}), ("C", {})):
            rejected = game.act("pilot:" + actor, {"action_id": "choose", **fields})
            self.assertFalse(rejected.ok, rejected.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))

        game = self.replay(game, load=True)
        self._choose(game, "C", resolving)
        self.assertEqual(("B", [resolving]), (self._item(game, spell).controller, self._item(game, spell).targets))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["D"].life)
        self.assertEqual(3, len(game.engine._current_turn_history("spell_cast")))
        self.replay(game, load=True)

    def test_actual_bolt_bend_uses_reduction_and_ward_uses_original_ability_controller(self):
        game = self.session(77115002)
        sorcerer = self.add(game.engine, "Prodigal Sorcerer")
        terror = self.add(game.engine, "Tolarian Terror", seat="C")
        reduction = self.add(game.engine, "Ancient Brontodon", seat="B")
        bend = self.add(game.engine, "Bolt Bend", seat="B", zone="hand")
        mana, = self._lands(game, "B", ("Mountain",))
        for card in (sorcerer, terror, reduction, bend):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.priority(game, "A")
        action, = (row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith("activate:" + sorcerer.ref + ":"))
        result = game.act("pilot:A", {"action_id": action["id"], "targets": ["D"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        ability, = (item for item in game.state.stack if item.kind == "activated_ability")
        self.cast(game, "B", bend, [ability.ref])
        self.assertTrue(game.state.cards[mana.object_id].tapped)
        self.assertIn(terror.ref, self._choice(game, "B"))
        self._choose(game, "B", terror.ref)

        ward, = (item for item in game.state.stack if item.semantic_key == "builtin:ward")
        self.assertEqual(("C", "A", ability.ref), (ward.controller, ward.context["payer"], ward.context["target_stack"]))
        self.assertEqual(("A", [terror.ref]),
                         (self._item(game, ability.ref).controller, self._item(game, ability.ref).targets))
        self.resolve(game)
        decision = game.state.pending_decision
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        self.assertFalse(decision.payload_by_actor["A"]["payable"])
        declined = game.act("pilot:A", {"action_id": "choose", "pay": False})
        self.assertTrue(declined.ok, declined.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((0, 40), (game.state.cards[terror.object_id].marked_damage, game.state.players["D"].life))
        self.replay(game, load=True)

    def test_actual_shunt_keeps_original_target_when_there_is_no_different_legal_one(self):
        game = self.session(77115003)
        creature = self.add(game.engine, "Ancient Brontodon", zone="hand")
        counter = self.add(game.engine, "Remove Soul", seat="B", zone="hand")
        shunt = self.add(game.engine, "Shunt", seat="C", zone="hand")
        self._lands(game, "B", ("Island", "Island"))
        self._lands(game, "C", ("Mountain", "Mountain", "Forest"))
        for card in (creature, counter, shunt):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 9, "G": 2})
        self.checkpoint(game)
        self.cast(game, "A", creature)
        original = self._spell(game, creature).ref
        self.cast(game, "B", counter, [original])
        subject = self._spell(game, counter).ref
        self.cast(game, "C", shunt, [subject])
        self.resolve_top(game)
        self.assertEqual([original], self._item(game, subject).targets)
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[creature.object_id].zone)
        self.replay(game, load=True)

    def test_actual_swerve_counts_repeated_occurrences_and_accepts_one_departed_target(self):
        game = self.session(77115004)
        common = self.add(game.engine, "Common Bond", zone="hand")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        escape = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        swerve = self.add(game.engine, "Swerve", seat="C", zone="hand")
        departed = self.add(game.engine, "Ancient Brontodon", seat="B", ref="redirect-departed")
        survivor = self.add(game.engine, "Ancient Brontodon", seat="D", ref="redirect-survivor")
        self._lands(game, "B", ("Island",))
        self._lands(game, "C", ("Island", "Mountain"))
        for card in (common, growth, escape, swerve, departed, survivor):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "G": 2, "W": 1})
        self.checkpoint(game)
        action = self.action(game, "A", common)
        accepted = game.act("pilot:A", {"action_id": action["id"], "targets": [survivor.ref, survivor.ref],
                                       "pay": "manual", "payment": {"C": 1, "G": 1, "W": 1}})
        self.assertTrue(accepted.ok, accepted.summary)
        twice = self._spell(game, common).ref
        self.assertEqual([survivor.ref, survivor.ref], self._item(game, twice).targets)
        self.cast(game, "A", growth, [departed.ref])
        once = self._spell(game, growth).ref
        self.cast(game, "B", escape, [departed.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[departed.object_id].zone)

        self.priority(game, "C")
        action = self.action(game, "C", swerve)
        self.assertEqual({once}, set(action["target_schema"]["legal_refs"]))
        before = authoritative_state_hash(game.state)
        bad = game.act("pilot:C", {"action_id": action["id"], "targets": [twice], "pay": "auto"})
        self.assertFalse(bad.ok, bad.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "C", swerve, [once])
        self.assertIn(survivor.ref, self._choice(game, "C"))
        self._choose(game, "C", survivor.ref)
        self.assertIsNone(self.resolve(game))
        card = game.state.cards[survivor.object_id]
        self.assertEqual(2, card.counters["+1/+1"])
        self.assertEqual(14, game.engine._numeric_stat(survivor.object_id, "power"))
        self.replay(game, load=True)

    def test_actual_misdirection_pitches_blue_and_preserves_the_original_damage_share(self):
        game = self.session(77115005)
        twin = self.add(game.engine, "Twin Bolt", zone="hand")
        misdirection = self.add(game.engine, "Misdirection", seat="B", zone="hand")
        pitch = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        for card in (twin, misdirection, pitch):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "R": 1})
        self.checkpoint(game)
        action = self.action(game, "A", twin)
        cast = game.act("pilot:A", {"action_id": action["id"], "targets": ["C"], "pay": "auto",
                                   "damage_allocation": {"C": 2}})
        self.assertTrue(cast.ok, cast.summary)
        subject = self._spell(game, twin).ref
        allocation = dict(self._item(game, subject).context["announced_damage_allocation"])

        self.priority(game, "B")
        action = self.action(game, "B", misdirection)
        option, = (row for row in action["cost_options"] if row["choice_schema"].get("exile_cards"))
        command = {"action_id": action["id"], "targets": [subject], "cost_option": option["id"],
                   "exile_cards": [pitch.ref], "pay": "auto"}
        accepted = game.act("pilot:B", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("exile", game.state.cards[pitch.object_id].zone)
        self.assertEqual(option["id"], self._spell(game, misdirection).context["cost_option"])
        self.assertIn("D", self._choice(game, "B"))
        self._choose(game, "B", "D")
        self.assertEqual(allocation, self._item(game, subject).context["announced_damage_allocation"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((40, 38), (game.state.players["C"].life, game.state.players["D"].life))
        self.replay(game, load=True)

    def test_actual_redirect_lightning_keeps_life_payment_and_changes_the_sealed_spell(self):
        game = self.session(77115006)
        shock = self.add(game.engine, "Shock", zone="hand")
        lightning = self.add(game.engine, "Redirect Lightning", seat="B", zone="hand")
        self._lands(game, "B", ("Mountain",))
        for card in (shock, lightning):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1})
        self.checkpoint(game)
        self.cast(game, "A", shock, ["C"])
        subject = self._spell(game, shock).ref
        self.priority(game, "B")
        action = self.action(game, "B", lightning)
        option, = action["cost_options"]
        self.assertEqual("Pay 5 life", option["label"])
        accepted = game.act("pilot:B", {"action_id": action["id"], "targets": [subject],
                                       "cost_option": option["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(35, game.state.players["B"].life)
        self.assertIn("D", self._choice(game, "B"))
        self._choose(game, "B", "D")
        self.assertIsNone(self.resolve(game))
        self.assertEqual((35, 40, 38), tuple(game.state.players[seat].life for seat in "BCD"))
        self.replay(game, load=True)

    def test_disabling_the_exact_lowerer_restores_the_material_residuals(self):
        with patch("quorune.compiler.resolution_effect_templates._single_stack_target_change_template", return_value=None):
            for name in NAMES:
                binding, _ = register_card(self.db, self.registry, name)
                self.assertFalse(binding["strict_capability_ready"], binding)
            unaffected, _ = register_card(self.db, self.registry, "Revitalize")
            self.assertTrue(unaffected["strict_capability_ready"], unaffected["blockers"])

    def test_verified_withdrawals_require_original_target_and_new_announcement_owners(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in ("target.revalidate_resolution", TARGET_ANNOUNCEMENT):
            with self.subTest(capability=capability):
                from copy import deepcopy

                candidate = deepcopy(raw)
                next(row for row in candidate["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Mandatory target authority withdrawn"])
                registry = CapabilityRegistry(candidate)
                index = build_index(registry_value=candidate, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in reason for reason in closure.blockers))
                for name in NAMES:
                    binding, _ = register_card(self.db, registry, name)
                    self.assertFalse(binding["strict_capability_ready"], binding)
                unaffected, _ = register_card(self.db, registry, "Revitalize")
                self.assertTrue(unaffected["strict_capability_ready"], unaffected["blockers"])

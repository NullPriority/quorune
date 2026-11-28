from collections import Counter
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
from quorune.public_alternative_costs import FixedPublicAlternativeCostSpec
from quorune.public_zone_moves import STACK_SPELL_EXILE_FIELD, StackSpellExileScope, StackSpellExileSpec
from quorune.target_cardinality import ANY_NUMBER_TARGET_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
TARGETED = "Mindbreak Trap"
BULK = "Summary Dismissal"
ZONE_CHANGE = "zone.change.destination_replacement"
COUNTER = "stack.counter.effect"
SCOPES = {TARGETED: StackSpellExileScope.SELECTED, BULK: StackSpellExileScope.OTHER_SPELLS_AND_ABILITIES}


class StackSpellExileTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-source-counter-cards.json": {"Counterspell", "Shock", "Sol Ring", "Twincast"},
            "additional-conditional-modifier-cards.json": {"Revitalize"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "bounded-hand-cast-cards.json": {"Young Pyromancer"},
            "designation-prevention-cards.json": {"Prodigal Sorcerer"},
            "mixed-stack-counter-cards.json": {"Carnage Tyrant"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "stack-spell-exile-cards.json": set(SCOPES),
        }
        pieces = []
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names, (filename, names)
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            pieces.append(piece)
        database = folder / "stack-spell-exile.sqlite3"
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Stack spell exile", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_programs(self, name):
        card = self.db.lookup(name, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(result["strict_capability_ready"], result)
        return card, {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)}

    def test_complete_originals_bind_exact_stack_exile_scopes_and_independent_mindbreak_price(self):
        for name, mode in SCOPES.items():
            with self.subTest(name=name):
                original, programs = self.original_programs(name)
                ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                effect = programs["resolve"]
                spec = StackSpellExileSpec(mode)
                self.assertEqual([spec.effect()], thaw_value(effect.effects))
                self.assertEqual(spec.target_schema, thaw_value(effect.target_schema))
                self.assertLessEqual(set(spec.capability_dependencies), set(effect.capability_dependencies))
                if name == TARGETED:
                    self.assertEqual({"cast.cost", "resolve"}, programs.keys())
                    price = FixedPublicAlternativeCostSpec.from_dict(
                        thaw_value(programs["cast.cost"].handlers[0]["alternative_cost"]),
                    )
                    self.assertEqual((4, "opponent_cast_three_spells", "{0}"),
                                     (price.condition.schema_version, price.condition.kind.value, price.cost_text))
                else:
                    self.assertEqual({"resolve"}, programs.keys())

    def test_stack_exile_shape_rejects_wrong_scope_targets_source_and_runtime_dependencies(self):
        _, programs = self.original_programs(TARGETED)
        program = programs["resolve"]
        effect, = thaw_value(program.effects)
        schema = thaw_value(program.target_schema)
        for changes in (
            {"effects": [{**effect, "source_stack": "$source"}]},
            {"effects": [{**effect, "stacks": "$target.0"}]},
            {"effects": [{**effect, STACK_SPELL_EXILE_FIELD: StackSpellExileSpec(
                StackSpellExileScope.OTHER_SPELLS_AND_ABILITIES).to_dict()}]},
            {"target_schema": {**schema, "categories": ["spell", "ability"]}},
            {"target_schema": {**schema, "source_exclusion": False}},
        ):
            with self.subTest(changes=changes):
                bound = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound)
        retained = tuple(cap for cap in program.capability_dependencies if cap != ANY_NUMBER_TARGET_CAPABILITY)
        closure = self.registry.closure(retained, profile=PROFILE).to_dict()
        self.assertTrue(closure["trusted"], closure["blockers"])
        stripped = replace(program, capability_dependencies=retained, capability_closure=closure,
                           provenance={**program.provenance, "capability_closure_fingerprint": closure["fingerprint"]})
        result = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + ANY_NUMBER_TARGET_CAPABILITY, result["blockers"])

    def _spell(self, game, card):
        item, = [item for item in game.state.stack if item.card_object_id == card.object_id]
        return item

    def _cast_manual(self, game, seat, card, mana):
        self.priority(game, seat)
        action = self.action(game, seat, card)
        result = game.act("pilot:" + seat, {"action_id": action["id"], "pay": "manual", "payment": mana})
        self.assertTrue(result.ok, result.summary)

    def _ping(self, game, seat, source):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in actions if row["id"].startswith("activate:" + source.ref + ":")]
        result = game.act("pilot:" + seat, {"action_id": action["id"], "targets": ["A"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        item, = [item for item in game.state.stack if item.source_object_id == source.object_id
                 and item.kind == "activated_ability"]
        return item

    def _actions(self, game):
        self.priority(game, "A")
        return game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]

    def _reject(self, game, command, seat="A"):
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:" + seat, command)
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _no_zero_price(self, game, trap, valid_target):
        action = "cast:" + trap.ref
        self.assertNotIn(action, {row["id"] for row in self._actions(game)})
        self._reject(game, {"action_id": action, "cost_option": "plain-alternative-ab1",
                             "targets": [valid_target], "pay": "auto"})

    def _countered_refs(self, game):
        return {event.details["stack"] for event in game.state.events if event.code == "stack.counter"}

    def test_actual_mindbreak_uses_one_opponents_casts_and_exiles_uncounterable_and_copied_spells(self):
        game = self.session(77129001)
        trap = self.add(game.engine, TARGETED, zone="hand")
        locked = self.add(game.engine, "Carnage Tyrant", seat="B", zone="hand")
        second = self.add(game.engine, "Shock", seat="B", zone="hand", ref="mindbreak-second-cast")
        third = self.add(game.engine, "Revitalize", seat="B", zone="hand", ref="mindbreak-third-cast")
        other = self.add(game.engine, "Shock", seat="C", zone="hand", ref="mindbreak-other-cast")
        last = self.add(game.engine, "Revitalize", seat="D", zone="hand", ref="mindbreak-third-seat")
        copier = self.add(game.engine, "Twincast", seat="C", zone="hand")
        reply = self.add(game.engine, "Counterspell", seat="C", zone="hand")
        ping = self.add(game.engine, "Prodigal Sorcerer", seat="B")
        for card in (trap, locked, second, third, other, last, copier, reply, ping):
            self.assert_closed(game, card)
        ping.acquired_control_turn_count = -1
        self.prepare(game, active="B", actor="B", mana={"C": 5, "G": 2, "R": 1, "W": 1})
        game.state.players["C"].mana_pool.update({"R": 1, "U": 4})
        game.state.players["D"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        self._cast_manual(game, "B", locked, {"C": 4, "G": 2})
        subject = self._spell(game, locked)
        self.cast(game, "C", other, ["D"])
        self.cast(game, "D", last)
        self._no_zero_price(game, trap, subject.ref)

        self.cast(game, "B", second, ["D"])
        departed_ref = self._spell(game, second).ref
        ability_ref = self._ping(game, "B", ping).ref
        self.cast(game, "C", copier, [self._spell(game, last).ref])
        self.resolve_top(game)
        copied, = [item for item in game.state.stack if item.kind == "spell_copy"]
        before_third = Counter(event.actor for event in game.state.turn_history.events if event.kind == "spell_cast")
        self.assertEqual({"B": 2, "C": 2, "D": 1}, before_third)
        self.cast(game, "B", third)
        self.assertTrue(any(item.ref == copied.ref for item in game.state.stack))
        facts = Counter(event.actor for event in game.state.turn_history.events if event.kind == "spell_cast")
        self.assertEqual({"B": 3, "C": 2, "D": 1}, facts)
        action = next(row for row in self._actions(game) if row["id"] == "cast:" + trap.ref)
        option, = [row for row in action["cost_options"] if row["kind"] == "alternate"]
        self.assertFalse(any(option["requirements"].values()))
        command = {"action_id": action["id"], "cost_option": option["id"], "pay": "auto"}
        self._reject(game, {**command, "targets": [ability_ref]})
        self._reject(game, {**command, "targets": [subject.ref, subject.ref]})
        accepted = game.act("pilot:A", {**command, "targets": [subject.ref, copied.ref, departed_ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        game = self.replay(game, load=True)
        self.cast(game, "C", reply, [departed_ref])
        self.resolve_top(game)
        self.assertEqual({departed_ref}, self._countered_refs(game))
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[locked.object_id].zone)
        self.assertFalse(any(item.ref in {subject.ref, copied.ref} for item in game.state.stack))
        self.assertEqual({departed_ref}, self._countered_refs(game))
        self.assertEqual("graveyard", game.state.cards[second.object_id].zone)
        copy_object = game.state.cards.get(copied.card_object_id)
        if copy_object is not None:
            self.assertEqual("outside", copy_object.zone)
        self.assertTrue(any(item.ref == ability_ref for item in game.state.stack))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40, game.state.players["C"].life)
        self.assertEqual(39, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_summary_exiles_other_spells_and_counters_only_current_stack_abilities(self):
        game = self.session(77129002)
        source = self.add(game.engine, BULK, zone="hand")
        locked = self.add(game.engine, "Carnage Tyrant", seat="B", zone="hand")
        ping = self.add(game.engine, "Prodigal Sorcerer", seat="C")
        maker = self.add(game.engine, "Young Pyromancer", seat="D")
        life = self.add(game.engine, "Revitalize", seat="D", zone="hand")
        for card in (source, locked, ping, maker, life):
            self.assert_closed(game, card)
        ping.acquired_control_turn_count = -1
        self.prepare(game, active="B", actor="B", mana={"C": 4, "G": 2})
        game.state.players["A"].mana_pool.update({"C": 2, "U": 2})
        game.state.players["D"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        self._cast_manual(game, "B", locked, {"C": 4, "G": 2})
        self._ping(game, "C", ping)
        self.cast(game, "D", life)
        self.priority(game, "A")
        self.assertEqual({"spell": 2, "activated_ability": 1, "triggered_ability": 1},
                         Counter(item.kind for item in game.state.stack))
        ability_refs = {item.ref for item in game.state.stack if item.kind.endswith("_ability")}
        self._cast_manual(game, "A", source, {"C": 2, "U": 2})
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"exile"}, {game.state.cards[card.object_id].zone for card in (locked, life)})
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(ability_refs, self._countered_refs(game))
        self.assertEqual((40, 40), (game.state.players["A"].life, game.state.players["D"].life))
        self.assertFalse(any(card.is_token and card.zone == "battlefield" for card in game.state.cards.values()))
        self.replay(game, load=True)

    def test_actual_mindbreak_can_pay_printed_price_with_no_targets_on_empty_stack(self):
        game = self.session(77129003)
        trap = self.add(game.engine, TARGETED, zone="hand")
        self.assert_closed(game, trap)
        self.prepare(game, mana={"C": 2, "U": 2})
        self.checkpoint(game)
        self.assertFalse(game.state.stack)
        action = self.action(game, "A", trap)
        self.assertEqual(["mana"], [price["kind"] for price in action["cost_options"]])
        result = game.act("pilot:A", {"action_id": action["id"], "targets": [],
                                      "pay": "manual", "payment": {"C": 2, "U": 2}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[trap.object_id].zone)
        self.assertFalse(self._countered_refs(game))
        self.replay(game, load=True)

    def test_omission_of_stack_exile_owner_leaves_both_results_partial(self):
        with patch("quorune.compiler.resolution_effect_templates.stack_spell_exile_template", return_value=None):
            for name in SCOPES:
                outcome, _ = register_card(self.db, self.registry, name)
                self.assertFalse(outcome["strict_capability_ready"], outcome)
            unrelated, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(unrelated["strict_capability_ready"], unrelated["blockers"])

    def test_genuine_withdrawals_distinguish_stack_exile_target_count_and_ability_counter_authority(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        original = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability, retained in (
            (ZONE_CHANGE, set()), (ANY_NUMBER_TARGET_CAPABILITY, {BULK}), (COUNTER, {TARGETED}),
        ):
            with self.subTest(capability=capability):
                document = deepcopy(original)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Stack exile authority withdrawn"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in SCOPES:
                    result, _ = register_card(self.db, registry, name)
                    self.assertEqual(name in retained, result["strict_capability_ready"], result)
                unrelated, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(unrelated["strict_capability_ready"], unrelated["blockers"])


class AvenInterrupterTests(InheritedNativeFixture, unittest.TestCase):
    _spell = StackSpellExileTests._spell
    _reject = StackSpellExileTests._reject
    aven_name = "Aven Interrupter"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "aven-interrupter.sqlite3"
        build_fixture_database([FIXTURES / name for name in (
            "bound-effect-program-cards.json", "aven-interrupter-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Aven Interrupter", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_aven_external_plot_and_origin_price_are_independently_closed(self):
        from quorune.cast_lifecycles import (
            EXTERNAL_PLOT_LIFECYCLE_VERSION, FixedCastLifecycleError, FixedCastLifecycleSpec,
            externally_plotted_cast_spec,
        )
        from quorune.compiler.public_cast_cost_modifiers import public_cast_cost_modifier_v2_template

        card = self.db.lookup(self.aven_name, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, self.aven_name)
        self.assertTrue(result["strict_capability_ready"], result)
        programs = {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)}
        trigger = programs["permanent.enter.self"]
        expected = StackSpellExileSpec(StackSpellExileScope.TARGET_PLOT, schema_version=2)
        self.assertEqual([expected.effect()], thaw_value(trigger.effects))
        self.assertEqual(expected.target_schema, thaw_value(trigger.target_schema))
        self.assertEqual(1, StackSpellExileSpec(StackSpellExileScope.SELECTED).schema_version)
        wrong = replace(trigger, target_schema={**expected.target_schema, "count": 2})
        self.assertFalse(bind_semantic_program_runtime(wrong, capability_registry=self.registry,
                                                      profile=PROFILE)["strict"])
        spec = externally_plotted_cast_spec()
        self.assertEqual(spec, FixedCastLifecycleSpec.from_dict(spec.to_dict()))
        self.assertEqual({"kind": "plot", "schema_version": EXTERNAL_PLOT_LIFECYCLE_VERSION}, spec.to_dict())
        with self.assertRaises(FixedCastLifecycleError):
            FixedCastLifecycleSpec.from_dict({**spec.to_dict(), "cost_text": "{2}"})
        modifier = public_cast_cost_modifier_v2_template(card.oracle_text.splitlines()[-1])
        self.assertEqual(("graveyard", "exile"), modifier.cast_origin_zones)
        self.assertEqual(("source_opponents", 2), (modifier.affected_controller.value, modifier.generic_adjustment))
        self.assertIsNone(public_cast_cost_modifier_v2_template(
            card.oracle_text.splitlines()[-1].replace("more", "less")))

    def aven_target(self, game, aven, target):
        self.cast(game, "A", aven)
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, list(pending.actors)))
        self._reject(game, {"action_id": "choose", "targets": [target]}, seat="B")
        # Loading the pending target proves the selected stack incarnation and source spell survive restore.
        game = self.replay(game, load=True)
        answer = game.act("pilot:A", {"action_id": "choose", "targets": [target]})
        self.assertTrue(answer.ok, answer.summary)
        return game

    def advance_to_main(self, game, seat, after_turn):
        for _ in range(5):
            self.until(game, lambda current: current.state.pending_decision is not None and (
                current.state.pending_decision.kind == "cleanup.discard"
                or (current.state.turn_sequence > after_turn and current.state.active_player == seat
                    and current.state.phase == "precombat_main" and current.state.pending_decision.kind == "priority")
            ))
            if game.state.pending_decision.kind != "cleanup.discard":
                return
            principal, = game.pending_principals()
            context = game.packet(principal, full=True)["decision"]["ctx"]
            result = game.act(principal, {"action_id": "discard",
                                          "cards": [row["id"] for row in context["hand"][:context["count"]]]})
            self.assertTrue(result.ok, result.summary)
        self.fail("The later owner's main phase did not arrive")

    def test_actual_aven_plots_uncounterable_spell_and_taxes_its_later_owner_cast(self):
        from quorune.cast_lifecycles import FIXED_ZONE_CAST_DESIGNATION_FIELD, fixed_zone_cast_designation

        game = self.session(828401)
        aven = self.add(game.engine, self.aven_name, zone="hand", ref="aven-plot-source")
        spell = self.add(game.engine, "Abrupt Decay", seat="B", zone="hand", ref="aven-plot-uncounterable")
        target = self.add(game.engine, "Sol Ring", ref="aven-decay-target")
        fuel = self.add(game.engine, "Sol Ring", seat="B", ref="aven-plot-tax-fuel")
        for card in (aven, spell, target, fuel):
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"B": 1, "G": 1})
        self.prepare(game, mana={"W": 2, "C": 1})
        self.checkpoint(game)
        self.cast(game, "B", spell, [target.ref])
        old_stack_ref = self._spell(game, spell).ref
        game = self.aven_target(game, aven, old_stack_ref)
        self.assertIsNone(self.resolve(game))
        exiled = game.state.cards[spell.object_id]
        self.assertEqual(("B", "exile", False), (exiled.owner, exiled.zone, exiled.face_down))
        marker = exiled.annotations[FIXED_ZONE_CAST_DESIGNATION_FIELD]
        self.assertEqual(exiled.logical_object_id, marker["logical_object_id"])
        self.assertEqual("plot", marker["lifecycle"]["kind"])
        self.assertIsNone(fixed_zone_cast_designation(game.state, exiled, actor="B"))
        self.assertFalse(any(event.code == "stack.counter" and event.details.get("stack") == old_stack_ref
                             for event in game.state.events))
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
        self.advance_to_main(game, "B", marker["created_turn_sequence"])
        exiled = game.state.cards[spell.object_id]
        self.assertIsNone(fixed_zone_cast_designation(game.state, exiled, actor="A"))
        action = self.action(game, "B", spell)
        plot, = (option for option in action["cost_options"] if option["id"] == "plot")
        self.assertEqual(2, plot["requirements"]["GENERIC"])
        self.assertEqual(2, sum(plot["requirements"].values()))
        paid = game.act("pilot:B", {"action_id": action["id"], "cost_option": "plot",
                                     "targets": [target.ref], "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertTrue(game.state.cards[fuel.object_id].tapped)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertNotIn(FIXED_ZONE_CAST_DESIGNATION_FIELD, game.state.cards[spell.object_id].annotations)
        self.replay(game, load=True)

    def test_actual_aven_exiles_copies_and_ignores_a_new_spell_incarnation(self):
        from quorune.cast_lifecycles import FIXED_ZONE_CAST_DESIGNATION_FIELD

        for mode in ("copy", "stale"):
            with self.subTest(mode=mode):
                game = self.session(828402 if mode == "copy" else 828403)
                aven = self.add(game.engine, self.aven_name, zone="hand")
                original = self.add(game.engine, "Think Twice", seat="B", zone="hand", ref="aven-identity-original")
                interrupt = self.add(
                    game.engine, "Twincast" if mode == "copy" else "Counterspell",
                    seat="C", zone="hand", ref="aven-identity-interrupt",
                )
                for card in (aven, original, interrupt):
                    self.assert_closed(game, card)
                game.state.players["B"].mana_pool.update({"U": 2, "C": 5})
                game.state.players["C"].mana_pool["U"] = 2
                self.prepare(game, mana={"W": 2, "C": 1})
                self.checkpoint(game)
                self.cast(game, "B", original)
                original_ref = self._spell(game, original).ref
                target = original_ref
                if mode == "copy":
                    self.cast(game, "C", interrupt, [original_ref])
                    self.resolve_top(game)
                    copy, = (item for item in game.state.stack if item.kind == "spell_copy")
                    target = copy.ref
                game = self.aven_target(game, aven, target)
                if mode == "copy":
                    self.resolve_top(game)
                    self.assertTrue(any(item.ref == original_ref for item in game.state.stack))
                    self.assertFalse(any(item.ref == target for item in game.state.stack))
                    self.assertFalse(any(FIXED_ZONE_CAST_DESIGNATION_FIELD in card.annotations
                                         for card in game.state.cards.values()))
                else:
                    self.cast(game, "C", interrupt, [original_ref])
                    self.resolve_top(game)
                    self.assertEqual("graveyard", game.state.cards[original.object_id].zone)
                    self.priority(game, "B")
                    action = self.action(game, "B", original)
                    cast = game.act("pilot:B", {"action_id": action["id"], "cost_option": "flashback", "pay": "auto"})
                    self.assertTrue(cast.ok, cast.summary)
                    self.assertNotEqual(original_ref, self._spell(game, original).ref)
                self.assertIsNone(self.resolve(game))
                if mode == "stale":
                    self.assertEqual("exile", game.state.cards[original.object_id].zone)
                    self.assertNotIn(FIXED_ZONE_CAST_DESIGNATION_FIELD, game.state.cards[original.object_id].annotations)
                self.replay(game, load=True)

    def test_actual_aven_surcharges_opponent_flashback_and_preserves_other_casts(self):
        game = self.session(828404)
        aven = self.add(game.engine, self.aven_name, ref="aven-origin-price")
        own = self.add(game.engine, "Think Twice", zone="graveyard", ref="aven-own-price")
        foreign = self.add(game.engine, "Think Twice", seat="B", zone="graveyard", ref="aven-foreign-price")
        from_hand = self.add(game.engine, "Think Twice", seat="B", zone="hand", ref="aven-hand-price")
        self.assert_closed(game, aven)
        game.state.players["B"].mana_pool.update({"C": 4, "U": 1})
        self.prepare(game, mana={"C": 3, "U": 1})
        self.checkpoint(game)
        own_option, = (row for row in self.action(game, "A", own)["cost_options"] if row["id"] == "flashback")
        self.assertEqual(2, own_option["requirements"]["GENERIC"])
        self.priority(game, "B")
        hand_option, = (row for row in self.action(game, "B", from_hand)["cost_options"] if row["id"] == "normal")
        self.assertEqual(1, hand_option["requirements"]["GENERIC"])
        action = self.action(game, "B", foreign)
        flashback, = (row for row in action["cost_options"] if row["id"] == "flashback")
        self.assertEqual((4, 1), (flashback["requirements"]["GENERIC"], flashback["requirements"]["U"]))
        command = {"action_id": action["id"], "cost_option": "flashback", "pay": "manual"}
        self._reject(game, {**command, "payment": {"C": 2, "U": 1}}, seat="B")
        result = game.act("pilot:B", {**command, "payment": {"C": 4, "U": 1}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[foreign.object_id].zone)
        self.replay(game, load=True)

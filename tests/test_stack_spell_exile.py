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

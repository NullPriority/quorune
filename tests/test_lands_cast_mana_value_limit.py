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
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.static_cast_rules import StaticCastCondition
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
NAME = "Lavinia, Azorius Renegade"
CAPABILITY = "casting.prohibition.fixed_public"
FIXTURES = ROOT / "tests" / "fixtures"


class LandsCastManaValueLimitTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Island"},
            "ability-source-counter-cards.json": {"Boomerang", "Grizzly Bears"},
            "activation-result-views-cards.json": {"Ornithopter"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "cast-spell-mana-value-cards.json": {"Blaze"},
            "event-player-fixed-damage-cards.json": {"Scalding Viper // Steam Clean"},
            "expend-cards.json": {"Frogmite"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "lands-cast-mana-value-limit-cards.json"]
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            parts.append(piece)
        database = folder / "lands-cast-mana-value-limit.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Lands and spell mana value", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_programs(self):
        card = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        static, = (program for program in programs if program.event == "cast.static.rule")
        triggered, = (program for program in programs if program.event == "spell.cast")
        return card, static, triggered

    def test_complete_original_binds_exact_cast_restriction_and_zero_mana_sibling(self):
        card, static, triggered = self.original_programs()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual("battlefield", static.active_zone)
        self.assertIn(CAPABILITY, static.capability_dependencies)
        descriptor, = thaw_value(static.handlers)
        self.assertEqual(("cast_prohibition", "source_opponents", StaticCastCondition.CASTER_LAND_MANA_VALUE_LIMIT),
                         (descriptor["kind"], descriptor["player_scope"], descriptor["condition"]))
        query, = descriptor["spell_queries"]
        self.assertEqual(["creature", "land"], query["excluded_types"])
        self.assertEqual([{"op": "counter_stack_target", "stack": "$context.stack"}],
                         thaw_value(triggered.effects))
        self.assertIn("stack.counter.cast_event_spell", triggered.capability_dependencies)

    def test_changed_cast_relation_condition_and_query_fail_closed(self):
        card, program, _ = self.original_programs()
        handler, = thaw_value(program.handlers)
        query, = handler["spell_queries"]
        for changes in (
            {"player_scope": "all_players"},
            {"player_scope": "source_controller"},
            {"spell_queries": [{**query, "excluded_types": ["land"]}]},
            {"spell_queries": [{**query, "types_all": ["artifact"]}]},
            {"spell_queries": [query, query]},
            {"origin_zones": ["graveyard"]},
            {"chosen_name": True},
            {"affects_abilities": True},
            {"schema_version": True},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(SemanticNodeError):
                    replace(program, handlers=[{**handler, **changes}])
        for old, new in (("Each opponent", "Each player"), ("greater than", "at least"),
                         ("lands that player controls", "permanents that player controls")):
            ir = compile_oracle_card(replace(card, oracle_text=card.oracle_text.replace(old, new)),
                capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", ir.status, new)

    def _actions(self, game, seat):
        self.priority(game, seat)
        return {row["id"]: row for row in game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]}

    def _accept(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def _reject_unchanged(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_land_entry_and_departure_change_the_same_ordinary_cast_offer(self):
        game = self.session(76156801)
        lavinia = self.add(game.engine, NAME, seat="B")
        initial = self.add(game.engine, "Island", ref="lavinia-initial-land")
        extra = self.add(game.engine, "Island", zone="hand", ref="lavinia-extra-land")
        first = self.add(game.engine, "Boomerang", zone="hand", ref="lavinia-first-cast")
        later = self.add(game.engine, "Boomerang", zone="hand", ref="lavinia-later-cast")
        controllers_spell = self.add(game.engine, "Boomerang", seat="B", zone="hand", ref="lavinia-own-cast")
        target = self.add(game.engine, "Grizzly Bears", seat="C")
        for card in (lavinia, first, later, controllers_spell):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U": 4})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)

        first_id = "cast:" + first.ref
        first_cast = {"action_id": first_id, "targets": [target.ref], "pay": "auto"}
        offers = self._actions(game, "A")
        self.assertNotIn(first_id, offers)
        self._reject_unchanged(game, "A", first_cast)
        self.assertIn("play-land:" + extra.ref, offers)
        self._accept(game, "A", {"action_id": "play-land:" + extra.ref})
        self.assertEqual("battlefield", game.state.cards[extra.object_id].zone)
        self.assertIn(first_id, self._actions(game, "A"))
        self._accept(game, "A", first_cast)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[target.object_id].zone)

        self.assertIn("cast:" + controllers_spell.ref, self._actions(game, "B"))
        self.cast(game, "B", controllers_spell, [extra.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[extra.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[initial.object_id].zone)
        later_id = "cast:" + later.ref
        self.assertNotIn(later_id, self._actions(game, "A"))
        self._reject_unchanged(game, "A", {"action_id": later_id, "targets": [initial.ref], "pay": "auto"})
        self.replay(game, load=True)

    def test_actual_offered_x_and_submitted_x_use_the_same_current_land_limit(self):
        game = self.session(76156802)
        lavinia = self.add(game.engine, NAME, seat="B")
        spell = self.add(game.engine, "Blaze", zone="hand")
        for index in range(2):
            self.add(game.engine, "Island", ref=f"lavinia-x-land-{index}")
        self.assert_closed(game, lavinia)
        self.assert_closed(game, spell)
        self.prepare(game, mana={"R": 1, "C": 5})
        self.checkpoint(game)
        offer = self._actions(game, "A")["cast:" + spell.ref]
        option, = offer["cost_options"]
        self.assertEqual((0, 1), (option["choice_schema"]["x"]["minimum"], option["choice_schema"]["x"]["maximum"]))
        self.assertIn("D", option["target_schema"]["legal_refs"])
        command = {"action_id": offer["id"], "targets": ["D"], "pay": "auto", "x": 2}
        self._reject_unchanged(game, "A", command)
        self._accept(game, "A", {**command, "x": 1})
        item = next(item for item in game.state.stack if item.card_object_id == spell.object_id)
        self.assertEqual(1, item.x_value)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(39, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_actual_selected_creature_face_remains_available_when_adventure_is_prohibited(self):
        game = self.session(76156803)
        lavinia = self.add(game.engine, NAME, seat="B")
        land = self.add(game.engine, "Island")
        card = self.add(game.engine, "Scalding Viper // Steam Clean", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="C")
        self.assert_closed(game, lavinia)
        self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1, "U": 1, "C": 2})
        self.checkpoint(game)
        offers = self._actions(game, "A")
        own = "cast:" + card.ref
        adventure = own + ":face:Steam Clean"
        self.assertIn(own, offers)
        self.assertNotIn(adventure, offers)
        self._reject_unchanged(game, "A", {"action_id": adventure, "face": "Steam Clean",
                                          "targets": [target.ref], "pay": "auto"})
        self._accept(game, "A", {"action_id": own, "face": "Scalding Viper", "pay": "auto"})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
        self.replay(game, load=True)

    def test_actual_creature_above_land_count_casts_and_retained_zero_mana_trigger_counters(self):
        game = self.session(76156804)
        lavinia = self.add(game.engine, NAME, seat="B")
        paid = self.add(game.engine, "Frogmite", zone="hand")
        free = self.add(game.engine, "Ornithopter", zone="hand")
        for card in (lavinia, paid, free):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4})
        self.checkpoint(game)
        self.assertIn("cast:" + paid.ref, self._actions(game, "A"))
        self.cast(game, "A", paid)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[paid.object_id].zone)
        self.assertIn("cast:" + free.ref, self._actions(game, "A"))
        self.cast(game, "A", free)
        trigger, = (item for item in game.state.stack if item.kind == "triggered_ability")
        self.assertEqual((lavinia.object_id, "B", 0),
                         (trigger.source_object_id, trigger.controller, trigger.context["mana_spent_total"]))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[free.object_id].zone)
        self.replay(game, load=True)

    def test_omitting_exact_caster_land_prohibition_keeps_only_its_original_residual(self):
        from quorune.compiler import static_cast_rule_templates as owner

        original = owner._cast_prohibition_or_limit_rule

        def omit_exact(text):
            result = original(text)
            return None if result is not None and result[1]["condition"] == StaticCastCondition.CASTER_LAND_MANA_VALUE_LIMIT else result

        with patch.object(owner, "_cast_prohibition_or_limit_rule", side_effect=omit_exact):
            binding, semantics = register_card(self.db, self.registry, NAME)
            ordinary, _ = register_card(self.db, self.registry, "Boomerang")
        self.assertFalse(binding["strict_capability_ready"], binding)
        self.assertTrue(ordinary["strict_capability_ready"], ordinary["blockers"])
        card = self.db.lookup(NAME, fuzzy=False)
        self.assertTrue(any(program.event == "spell.cast" for program in semantics.programs_for_oracle(card.oracle_id)))

    def test_verified_withdrawal_requires_the_static_cast_prohibition_authority(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = deepcopy(json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes()))
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Caster land static prohibition authority withdrawn"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in reason for reason in closure.blockers))
        binding, _ = register_card(self.db, registry, NAME)
        ordinary, _ = register_card(self.db, registry, "Boomerang")
        self.assertFalse(binding["strict_capability_ready"], binding)
        self.assertTrue(ordinary["strict_capability_ready"], ordinary["blockers"])

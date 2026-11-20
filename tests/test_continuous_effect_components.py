from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from common import keep_all, load_assets, make_session
from quorune.ability_fragments import ability_fragment_from_dict
from quorune.compiler.continuous_templates import (
    fixed_query_keyword_grant_handler,
)
from quorune.continuous_effects import (
    CharacteristicState,
    ContinuousEffect,
    ContinuousOperation,
    Layer,
    evaluate_continuous_effects,
)
from quorune.model import StackItem
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.semantic_runtime import (
    ContinuousEffectSourceContext,
    FixedQueryAbilityGrantHandler,
    FixedPowerToughnessAnthemHandler,
    SemanticNodeError,
    default_continuous_effect_component_registry,
)
from quorune.semantic_runtime.continuous_components import (
    FixedQueryKeywordGrantHandler,
)
from quorune.semantics import SemanticProgram, SemanticRegistry
from quorune.trigger_participation import WardSpec


def anthem_descriptor() -> dict:
    return {
        "handler_id": "continuous.anthem.power_toughness.v1",
        "schema_version": 1,
        "event": "characteristics.evaluate",
        "condition": {
            "target_controller": "source_controller",
            "target_subtypes_all": ["thopter"],
        },
        "modifier": {"power": 1, "toughness": 1},
    }


def ability_grant_descriptor() -> dict:
    return {
        "handler_id": "continuous.ability.fixed-query-grant.v1",
        "schema_version": 1,
        "event": "characteristics.evaluate",
        "condition": {
            "target_controller": "source_controller",
            "predicate": {
                "zones": ["battlefield"],
                "owner": None,
                "controller": None,
                "types_all": ["creature"],
                "types_any": [],
                "excluded_types": [],
                "subtypes_all": [],
                "supertypes_all": [],
                "colors_all": [],
                "colors_any": [],
                "keywords_all": [],
                "token": True,
                "tapped": None,
                "include_phased_out": False,
                "known_to_actor": None,
                "exclude_ref": None,
            },
            "exclude_source": False,
        },
        "modifier": {
            "add_ability_fragments": [
                {
                    "kind": "granted_activated",
                    "value": {
                        "schema_version": 1,
                        "ability_id": "test-mana",
                        "semantic_key": "builtin:mana-fixed",
                        "cost_text": "{T}",
                        "effect_text": "Add {G}.",
                        "mana": {},
                        "tap_source": True,
                        "sorcery_speed": False,
                        "mana_ability": True,
                        "fixed_mana_outputs": [{"G": 1}],
                    },
                }
            ]
        },
    }


class ContinuousEffectComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def card(engine, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == name
        )

    @staticmethod
    def raw_thopter(engine, controller: str = "A"):
        ref = engine.create_token(
            controller,
            name="Test Thopter",
            characteristics={
                "type_line": "Token Artifact Creature — Thopter",
                "colors": [],
                "power": "1",
                "toughness": "1",
                "keywords": ["Flying"],
            },
            reason="continuous component witness",
        )[0]
        return engine._resolve_object(
            controller, ref, zones={"battlefield"}
        )

    def test_registered_anthem_component_replaces_name_dispatch(self):
        session = self.session(1250601)
        engine = session.engine
        thopter = self.raw_thopter(engine)
        automaton = self.card(engine, "Stridehangar Automaton")
        engine.move_card(automaton.object_id, "battlefield", controller="A")
        automaton.printed_name = "Renamed static source"

        self.assertEqual(2, engine._numeric_stat(thopter.object_id, "power"))
        self.assertEqual(
            2, engine._numeric_stat(thopter.object_id, "toughness")
        )
        projected = session.projector._obj(thopter, "pilot:A")
        self.assertEqual("2", projected["p"])
        self.assertEqual("2", projected["q"])
        descriptor = default_continuous_effect_component_registry().describe(
            "continuous.anthem.power_toughness.v1"
        )
        self.assertEqual(
            ["continuous.power_toughness.fixed_anthem"],
            descriptor["capability_dependencies"],
        )

    def test_anthem_component_rejects_malformed_descriptors(self):
        handler = FixedPowerToughnessAnthemHandler()
        malformed = anthem_descriptor()
        malformed["modifier"]["power"] = True
        with self.assertRaisesRegex(SemanticNodeError, "integers"):
            handler.validate(malformed)

        wrong_controller = anthem_descriptor()
        wrong_controller["condition"]["target_controller"] = "any"
        with self.assertRaisesRegex(SemanticNodeError, "source_controller"):
            handler.validate(wrong_controller)

        with self.assertRaisesRegex(ValueError, "event must match"):
            SemanticProgram(
                key="test:mismatched-continuous-event",
                label="Mismatched continuous event",
                event="resolve",
                handlers=[anthem_descriptor()],
            )

    def test_fixed_query_ability_grant_is_typed_and_canonical(self):
        handler = FixedQueryAbilityGrantHandler()
        context = ContinuousEffectSourceContext(
            source_object_id="source",
            source_ref="S1",
            source_controller="A",
            source_timestamp=4,
            component_id="test:ability-grant:0",
        )
        effect = handler.lower(ability_grant_descriptor(), context)[0]
        reconstructed = ContinuousEffect.from_dict(effect.to_dict())

        self.assertEqual(effect, reconstructed)
        self.assertEqual(Layer.ABILITY, effect.layer)
        self.assertEqual("A", effect.applies.controller)
        self.assertTrue(effect.applies.token)
        self.assertEqual(("creature",), effect.applies.types_all)
        self.assertEqual("add_ability_fragment", effect.operations[0].op)
        self.assertEqual(
            ["continuous.ability.fixed_query_grant"],
            default_continuous_effect_component_registry().describe(
                "continuous.ability.fixed-query-grant.v1"
            )["capability_dependencies"],
        )

        global_trigger = ability_grant_descriptor()
        global_trigger["condition"]["target_controller"] = "any"
        global_trigger["modifier"]["add_ability_fragments"] = [
            {
                "kind": "granted_triggered",
                "value": {
                    "schema_version": 1,
                    "ability_id": "test-trigger",
                    "semantic_key": "fixture:test-trigger",
                    "event": "combat.attack.declared",
                    "label": "Whenever this creature attacks, draw a card.",
                },
            }
        ]
        trigger_effect = handler.lower(global_trigger, context)[0]
        self.assertIsNone(trigger_effect.applies.controller)
        self.assertEqual((), trigger_effect.applies.excluded_controllers)

        declaration = ability_grant_descriptor()
        declaration["modifier"]["add_ability_fragments"] = [
            {
                "kind": "declaration_requirement",
                "value": {
                    "template_id": "intrinsic-attack-each-combat-if-able-v1",
                    "declaration": "attack",
                    "kind": "attack_each_combat",
                },
            }
        ]
        declaration_effect = handler.lower(declaration, context)[0]
        self.assertEqual(
            "declaration_requirement",
            declaration_effect.operations[0].value["kind"],
        )

        combined = ability_grant_descriptor()
        combined["modifier"] = {
            "add_abilities": ["Double Strike"],
            "add_ability_fragments": list(
                declaration["modifier"]["add_ability_fragments"]
            ),
        }
        combined_effect = handler.lower(combined, context)[0]
        self.assertEqual(
            ("add_ability", "add_ability_fragment"),
            tuple(operation.op for operation in combined_effect.operations),
        )
        self.assertEqual("Double Strike", combined_effect.operations[0].value)
        self.assertEqual(
            "declaration_requirement",
            combined_effect.operations[1].value["kind"],
        )

    def test_fixed_query_ward_grant_uses_typed_layer_six_fragment(self):
        compiled = fixed_query_keyword_grant_handler(
            "Other creatures you control have ward {2}."
        )
        self.assertIsNotNone(compiled)
        assert compiled is not None
        _template_id, descriptor, capabilities = compiled
        self.assertIn("trigger.keyword.ward.fixed_generic", capabilities)
        effect = FixedQueryKeywordGrantHandler().lower(
            descriptor,
            ContinuousEffectSourceContext(
                source_object_id="source",
                source_ref="S1",
                source_controller="A",
                source_timestamp=4,
                component_id="test:ward-grant:0",
            ),
        )[0]
        self.assertEqual(
            ["add_ability", "add_ability_fragment"],
            [operation.op for operation in effect.operations],
        )
        self.assertEqual(
            WardSpec(generic_cost=2),
            ability_fragment_from_dict(effect.operations[1].value),
        )
        self.assertIsNone(
            fixed_query_keyword_grant_handler(
                'Other creatures you control have "Ward—Pay 2 life."'
            )
        )

    def test_fixed_query_ability_grant_rejects_malformed_descriptors(self):
        handler = FixedQueryAbilityGrantHandler()
        empty = ability_grant_descriptor()
        empty["modifier"]["add_ability_fragments"] = []
        with self.assertRaisesRegex(SemanticNodeError, "nonempty"):
            handler.validate(empty)

        competing_controller = ability_grant_descriptor()
        competing_controller["condition"]["predicate"]["controller"] = "A"
        with self.assertRaisesRegex(SemanticNodeError, "reserve"):
            handler.validate(competing_controller)

        unsupported_relation = ability_grant_descriptor()
        unsupported_relation["condition"]["target_controller"] = "owner"
        with self.assertRaisesRegex(SemanticNodeError, "controller relation"):
            handler.validate(unsupported_relation)

        unsupported_fragment = ability_grant_descriptor()
        unsupported_fragment["modifier"]["add_ability_fragments"] = [
            {
                "kind": "static_component",
                "value": {
                    "schema_version": 1,
                    "semantic_key": "fixture:static",
                },
            }
        ]
        with self.assertRaisesRegex(
            SemanticNodeError,
            "versioned cost component reference",
        ):
            handler.validate(unsupported_fragment)

        unsupported_keyword = ability_grant_descriptor()
        unsupported_keyword["modifier"]["add_abilities"] = ["Banding"]
        handler.validate(unsupported_keyword)
        unsupported_keyword["modifier"]["add_abilities"] = ["Unrepresented keyword"]
        with self.assertRaisesRegex(SemanticNodeError, "supported keywords"):
            handler.validate(unsupported_keyword)

    def test_multiple_anthem_components_stack_and_respect_control(self):
        session = self.session(1250602)
        engine = session.engine
        thopter_a = self.raw_thopter(engine, "A")
        thopter_b = self.raw_thopter(engine, "B")
        automaton = self.card(engine, "Stridehangar Automaton")
        engine.move_card(automaton.object_id, "battlefield", controller="A")
        created = engine.create_token(
            "A",
            name="Stridehangar Automaton",
            copy_of=automaton.ref,
            reason="second anthem source characterization",
        )
        copied = next(
            engine._resolve_object("A", ref)
            for ref in created
            if engine._resolve_object("A", ref).oracle_id
            == automaton.oracle_id
        )
        self.assertEqual(
            3, engine._numeric_stat(thopter_a.object_id, "power")
        )

        engine.change_control(
            copied.object_id,
            "B",
            reason="control applicability characterization",
        )
        self.assertEqual(
            2, engine._numeric_stat(thopter_a.object_id, "power")
        )
        self.assertEqual(
            2, engine._numeric_stat(thopter_b.object_id, "power")
        )
        copied.phased_out = True
        self.assertEqual(
            1, engine._numeric_stat(thopter_b.object_id, "power")
        )

    def test_layer_four_subtype_change_enables_layer_seven_anthem(self):
        handler = FixedPowerToughnessAnthemHandler()
        anthem = handler.lower(
            anthem_descriptor(),
            ContinuousEffectSourceContext(
                source_object_id="source",
                source_ref="S1",
                source_controller="A",
                source_timestamp=2,
                component_id="test:anthem:0",
            ),
        )[0]
        result = evaluate_continuous_effects(
            CharacteristicState(
                name="Test Creature",
                controller="A",
                card_types={"Creature"},
                subtypes={"Goblin"},
                power=1,
                toughness=1,
            ),
            [
                anthem,
                ContinuousEffect(
                    effect_id="type-change",
                    source_id="type-source",
                    layer=Layer.TYPE,
                    sublayer="4",
                    timestamp=9,
                    operations=(
                        ContinuousOperation(
                            "add_types",
                            ["Thopter"],
                            field="subtypes",
                        ),
                    ),
                ),
            ],
        )
        self.assertEqual(
            ("type-change", anthem.effect_id), result.applied_effects
        )
        self.assertEqual(2, result.characteristics["power"])
        self.assertEqual(2, result.characteristics["toughness"])

    def test_anthem_component_replays_exactly(self):
        session = self.session(1250603)
        engine = session.engine
        automaton = self.card(engine, "Stridehangar Automaton")
        engine.move_card(automaton.object_id, "battlefield", controller="A")
        program = SemanticProgram(
            key="test:continuous-anthem-token",
            label="Create an anthem witness",
            effects=[
                {
                    "op": "create_token",
                    "controller": "A",
                    "name": "Thopter",
                    "characteristics": {
                        "type_line": "Token Creature — Thopter",
                        "power": "1",
                        "toughness": "1",
                    },
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="continuous-anthem-token",
                ref="S-continuous-anthem-token",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                visibility=["A", "B"],
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        thopter = next(
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Thopter"
        )
        self.assertEqual(2, engine._numeric_stat(thopter.object_id, "power"))

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "continuous-anthem-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])

    def test_complete_legacy_registry_uses_continuous_component(self):
        with tempfile.TemporaryDirectory() as temporary:
            semantics_path = Path(temporary) / "semantics.json"
            semantics_path.write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "include_builtin_packs": False,
                        "programs": {},
                    }
                ),
                encoding="utf-8",
            )
            registry = SemanticRegistry(semantics_path)

        session = self.session(1250604)
        engine = session.engine
        engine.semantics = registry
        engine._semantic_trust_cache.clear()
        thopter = self.raw_thopter(engine)
        automaton = self.card(engine, "Stridehangar Automaton")
        engine.move_card(automaton.object_id, "battlefield", controller="A")
        self.assertEqual(2, engine._numeric_stat(thopter.object_id, "power"))


if __name__ == "__main__":
    unittest.main()


class SharedLayerRecipientTests(unittest.TestCase):
    def effects(self):
        from quorune.continuous_effect_model import ContinuousEffectOrigin
        from quorune.object_predicate import ObjectQuerySpec

        common = dict(source_id="giver", timestamp=1, origin=ContinuousEffectOrigin.STATIC_ABILITY,
                      applies=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), controller="A",
                                              controlled_by_nonowner=True),
                      recipient_scope_anchor="start")
        return (
            ContinuousEffect(effect_id="start", layer=Layer.TYPE, sublayer="4",
                operations=(ContinuousOperation("add_types", ("Pirate",), field="subtypes"),), **common),
            ContinuousEffect(effect_id="keywords", layer=Layer.ABILITY, sublayer="6",
                operations=(ContinuousOperation("add_ability", "Menace"),), **common),
            ContinuousEffect(effect_id="stats", layer=Layer.POWER_TOUGHNESS, sublayer="7c",
                operations=(ContinuousOperation("modify_power_toughness", (2, 2)),), **common),
        )

    def test_earliest_membership_remains_when_a_dependent_type_effect_removes_creature(self):
        from quorune.object_predicate import ObjectQuerySpec

        type_change = ContinuousEffect("after-start", "change", Layer.TYPE, "4", 2,
            (ContinuousOperation("remove_types", ("Creature",), field="card_types"),),
            depends_on=("start",), applies=ObjectQuerySpec(subtypes_all=("pirate",)))
        cases = (("B", True), ("A", False))
        for owner, qualifies in cases:
            with self.subTest(owner=owner):
                base = CharacteristicState("Subject",controller="A",card_types={"Creature"},power=2,toughness=3)
                result = evaluate_continuous_effects(base, (*self.effects(), type_change),
                    context={"owner":owner,"object_id":"recipient","logical_object_id":"recipient:1"})
                self.assertEqual(("start","after-start","keywords","stats") if qualifies else (),
                                 result.applied_effects)
                self.assertEqual(4 if qualifies else 2,result.characteristics["power"])
                self.assertEqual(qualifies,"Menace" in result.characteristics["abilities"])
                self.assertEqual(not qualifies,"Creature" in result.characteristics["card_types"])

    def test_replay_codec_keeps_legacy_shapes_and_rejects_orphan_or_foreign_followers(self):
        from dataclasses import replace
        from quorune.continuous_effect_model import ContinuousEffectError

        anchor, keyword, stats = self.effects()
        legacy = replace(anchor, recipient_scope_anchor=None).to_dict()
        self.assertNotIn("recipient_scope_anchor",legacy)
        self.assertEqual(legacy,ContinuousEffect.from_dict(legacy).to_dict())
        self.assertEqual(keyword,ContinuousEffect.from_dict(keyword.to_dict()))
        with self.assertRaises(ContinuousEffectError):
            ContinuousEffect.from_dict({**legacy,"recipient_scope_anchor":None})
        with self.assertRaises(ContinuousEffectError):
            replace(anchor,recipient_scope_anchor="other")
        for effects in ((keyword,stats),(anchor,replace(keyword,source_id="other"),stats),
                        (anchor,replace(keyword,timestamp=2),stats)):
            with self.subTest(effects=[effect.effect_id for effect in effects]), self.assertRaises(ContinuousEffectError):
                evaluate_continuous_effects(CharacteristicState("Subject"),effects)

    def test_new_type_membership_waits_for_an_applicable_later_animation(self):
        from quorune.object_predicate import ObjectQuerySpec

        animation = ContinuousEffect("animate", "other", Layer.TYPE, "4", 20,
            (ContinuousOperation("add_types", ("Creature",), field="card_types"),),
            applies=ObjectQuerySpec(types_all=("artifact",)))
        base = CharacteristicState("Subject",controller="A",card_types={"Artifact"},power=0,toughness=0)
        result = evaluate_continuous_effects(base, (*self.effects(), animation),
            context={"owner":"B","object_id":"recipient","logical_object_id":"recipient:1"})
        self.assertEqual(("animate","start","keywords","stats"),result.applied_effects)
        self.assertEqual(["Artifact","Creature"],result.characteristics["card_types"])
        self.assertEqual(["Pirate"],result.characteristics["subtypes"])
        self.assertIn("Menace",result.characteristics["abilities"])

    def test_type_dependencies_are_rechecked_after_enablers_and_control_changes(self):
        from quorune.object_predicate import ObjectQuerySpec

        removal = ContinuousEffect("remove-creature", "other", Layer.TYPE, "4", 30,
            (ContinuousOperation("remove_types", ("Creature",), field="card_types"),),
            applies=ObjectQuerySpec(types_all=("creature",)))
        animation = ContinuousEffect("animate", "other", Layer.TYPE, "4", 20,
            (ContinuousOperation("add_types", ("Creature",), field="card_types"),),
            applies=ObjectQuerySpec(types_all=("artifact",)))
        custody = ContinuousEffect("custody", "other", Layer.CONTROL, "2", 10,
            (ContinuousOperation("set_controller", "A"),))
        base = CharacteristicState("Subject",controller="B",card_types={"Artifact"})
        result = evaluate_continuous_effects(base, (*self.effects(), animation, removal, custody),
            context={"owner":"B","object_id":"recipient","logical_object_id":"recipient:1"})
        # Before animation the removal applies to nothing. Once animation makes
        # it applicable, the earlier modifier has to wait for it as well.
        self.assertEqual(("custody","animate","remove-creature"),result.applied_effects)
        self.assertEqual(["Artifact"],result.characteristics["card_types"])

    def test_dependency_on_another_receiver_orders_one_plan_over_the_board(self):
        from quorune.continuous_type_dependencies import battlefield_type_effect_order
        from quorune.object_predicate import ObjectQuerySpec

        land_animation = ContinuousEffect("forest-animation","other",Layer.TYPE,"4",20,
            (ContinuousOperation("add_types",("Creature",),field="card_types"),),
            applies=ObjectQuerySpec(types_all=("land",),subtypes_all=("forest",)))
        forest_enabler = ContinuousEffect("forest-addition","other",Layer.TYPE,"4",30,
            (ContinuousOperation("add_types",("Forest",),field="subtypes"),),
            applies=ObjectQuerySpec(types_all=("land",)))
        effects=(*self.effects(),land_animation,forest_enabler)
        def states():
            return tuple((CharacteristicState(name,controller="A",card_types={"Land"},subtypes={name}),
                         {"owner":"B","object_id":name,"logical_object_id":name+":1"})
                         for name in ("Forest","Island"))
        board_order=battlefield_type_effect_order(tuple((base,context,effects) for base,context in states()))
        self.assertEqual(("forest-addition","forest-animation","start"),board_order.effect_ids)
        for base,context in states():
            result=evaluate_continuous_effects(base,effects,context=context,type_order=board_order)
            self.assertEqual(("forest-addition","forest-animation","start","keywords","stats"),result.applied_effects)
            self.assertIn("Pirate",result.characteristics["subtypes"])

    def test_generic_modifier_lowers_one_validated_group_and_owns_keyword_dependencies(self):
        from quorune.compiler.fixed_characteristic_settings import nonowner_creature_modifier_handler
        from quorune.rules.keyword_runtime_requirements import registered_component_capabilities
        from quorune.semantic_runtime.fixed_characteristic_settings import NonownerCreatureModifierHandler

        printed = "Each creature you control but don't own gets +1/+2, has menace and deathtouch, and is a Wizard in addition to its other types."
        result = nonowner_creature_modifier_handler(printed)
        self.assertIsNotNone(result)
        _template, descriptor, required = result
        handler = NonownerCreatureModifierHandler()
        self.assertEqual(required, registered_component_capabilities(descriptor,
            {"capability_dependencies":handler.capability_dependencies}))
        self.assertTrue({"combat.block.menace","damage.result.deathtouch",
                         "state_query.permanent.controlled_by_nonowner"} <= set(required))
        context = ContinuousEffectSourceContext("giver","GIVER","C",4,"ordinal",
                                               source_logical_object_id="giver:2")
        effects = handler.lower(descriptor,context)
        self.assertEqual(("4","6","7c"),tuple(effect.sublayer for effect in effects))
        self.assertEqual({effects[0].effect_id},{effect.recipient_scope_anchor for effect in effects})
        self.assertEqual({"C"},{effect.applies.controller for effect in effects})
        self.assertEqual(("Wizard",),effects[0].operations[0].value)
        self.assertEqual((1,2),effects[-1].operations[0].value)
        for text in (printed+" Draw a card.", printed.replace("but don't own","and own"),
                     printed.replace("Wizard","Forest"), printed.replace("menace","unknown ability"),
                     printed.replace("in addition to its other types","instead")):
            with self.subTest(text=text):
                self.assertIsNone(nonowner_creature_modifier_handler(text))

    def test_omitting_the_shared_layer_decision_is_detected(self):
        from unittest.mock import patch
        from quorune.continuous_effects import _matches

        with patch("quorune.continuous_effects._matches_effect_recipient",
                   side_effect=lambda effect,state,context,_memberships: _matches(effect.applies,state,context)):
            result = unittest.TestResult()
            type(self)("test_earliest_membership_remains_when_a_dependent_type_effect_removes_creature").run(result)
        self.assertEqual([],result.errors)
        self.assertEqual(1,len(result.failures))


from inherited_composition_fixture import InheritedNativeFixture


class NonownerCreatureModifierNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((ROOT/"tests/fixtures/bound-effect-program-cards.json").read_text(encoding="utf-8"))
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        path = folder/"base.json"
        path.write_text(json.dumps(base),encoding="utf-8")
        database = folder/"nonowner-modifier.sqlite3"
        build_fixture_database([path, ROOT/"tests/fixtures/nonowner-creature-modifier-cards.json"],database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Nonowner creature modifiers", [
            DeckEntry("Isamaru, Hound of Konda",1,"commander"),DeckEntry("Generic Bound Plains",30),
        ],["Isamaru, Hound of Konda"])

    def current_data(self, session, card):
        return session.engine._effective_card_data(session.state.cards[card.object_id])

    def assert_stats(self, session, card, expected):
        actual = tuple(session.engine._numeric_stat(card.object_id, key) for key in ("power","toughness"))
        self.assertEqual(expected,actual)

    def assert_modifier(self, session, card, applies):
        data = self.current_data(session,card)
        _types, subtypes, _supertypes = session.engine._type_parts(data["type_line"])
        keywords = {word.casefold() for word in data["keywords"]}
        self.assertEqual(applies, "pirate" in subtypes)
        self.assertEqual(applies, {"menace","deathtouch"} <= keywords)

    def test_paid_original_tracks_a_borrowed_creature_and_retains_its_printed_types(self):
        session = self.session(61360401)
        engine = session.engine
        source = self.add(engine,"Don Andres, the Renegade",zone="hand")
        friendly = self.add(engine,"Grizzly Bears",ref="same-owner")
        foreign = self.add(engine,"Grizzly Bears",seat="B",ref="borrowed-creature")
        other = self.add(engine,"Grizzly Bears",seat="C",ref="outside-control")
        control = self.add(engine,"Mind Control",zone="hand")
        answer = self.add(engine,"Disenchant",seat="B",zone="hand")
        hidden = self.add(engine,"Shock",seat="D",zone="hand",ref="nonowner-private")
        for card in (source,control,answer):
            self.assert_closed(session,card)
        self.prepare(session,mana={"C":4,"U":3,"B":1,"R":1})
        session.state.players["B"].mana_pool.update({"C":1,"W":1})
        self.checkpoint(session)
        self.cast_for(session,"A",source,reserve={"U":2})
        self.assertIsNone(self.resolve(session))
        for card in (friendly,foreign,other):
            self.assert_modifier(session,card,False)
        self.cast_for(session,"A",control,[foreign.ref])
        self.assertIsNone(self.resolve(session))
        actual = session.state.cards[foreign.object_id]
        self.assertEqual(("B","A"),(actual.owner,actual.controller))
        self.assert_modifier(session,foreign,True)
        self.assert_stats(session,foreign,(4,4))
        self.assertIn("bear",session.engine._type_parts(self.current_data(session,foreign)["type_line"])[1])
        self.assert_modifier(session,friendly,False)
        self.assert_modifier(session,other,False)
        for seat in ("A","B","C"):
            self.assertNotIn(hidden.ref,json.dumps(session.packet("pilot:"+seat,full=True)))
        session=self.replay(session,load=True)
        self.cast_for(session,"B",answer,[control.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("B",session.state.cards[foreign.object_id].controller)
        self.assert_modifier(session,foreign,False)
        self.assert_stats(session,foreign,(2,2))
        self.replay(session,load=True)

    def test_paid_animation_qualifies_the_borrowed_artifact_at_the_earlier_type_boundary(self):
        session = self.session(61360402)
        engine = session.engine
        source = self.add(engine,"Don Andres, the Renegade",zone="hand")
        control = self.add(engine,"Confiscate",zone="hand")
        animation = self.add(engine,"Ensoul Artifact",zone="hand")
        target = self.add(engine,"Sol Ring",seat="C")
        answer = self.add(engine,"Disenchant",seat="C",zone="hand")
        for card in (source,control,animation,target,answer):
            self.assert_closed(session,card)
        self.prepare(session,mana={"C":6,"U":4,"B":1,"R":1})
        session.state.players["C"].mana_pool.update({"C":1,"W":1})
        self.checkpoint(session)
        self.cast_for(session,"A",source,reserve={"U":3})
        self.assertIsNone(self.resolve(session))
        self.cast_for(session,"A",control,[target.ref],reserve={"U":1})
        self.assertIsNone(self.resolve(session))
        self.assertEqual(("C","A"),(target.owner,session.state.cards[target.object_id].controller))
        self.assert_modifier(session,target,False)
        self.cast_for(session,"A",animation,[target.ref])
        self.assertIsNone(self.resolve(session))
        self.assert_modifier(session,target,True)
        self.assert_stats(session,target,(7,7))
        session=self.replay(session,load=True)
        self.cast_for(session,"C",answer,[animation.ref])
        self.assertIsNone(self.resolve(session))
        self.assert_modifier(session,target,False)
        self.assertEqual("A",session.state.cards[target.object_id].controller)
        self.replay(session,load=True)

    def test_paid_borrowed_noncreature_cast_still_creates_the_two_tapped_treasures(self):
        session = self.session(61360403)
        engine = session.engine
        source = self.add(engine,"Don Andres, the Renegade",zone="hand")
        plunder = self.add(engine,"Memory Plunder",zone="hand")
        target = self.add(engine,"Shock",seat="B",zone="graveyard",ref="borrowed-spell")
        for card in (source, plunder, target):
            self.assert_closed(session,card)
        self.prepare(session,mana={"C":1,"U":5,"B":1,"R":1})
        self.checkpoint(session)
        self.cast_for(session,"A",source,reserve={"U":4})
        self.assertIsNone(self.resolve(session))
        self.priority(session,"A")
        offer = self.action(session,"A",plunder)
        command = {"action_id":offer["id"],"targets":[target.ref],"pay":"auto"}
        if offer.get("cost_options"):
            command["cost_option"]=offer["cost_options"][0]["id"]
        accepted = session.act("pilot:A",command)
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual("semantic.choice",self.resolve(session).kind)
        choices = session.packet("pilot:A",full=True)["decision"]["ctx"]["cast_options"]
        command = {"action_id":"choose","choice":"cast",
                   "cast":{"cost_option":choices[0]["id"],"targets":["C"]}}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:D",command).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        session=self.replay(session,load=True)
        choice = session.act("pilot:A",command)
        self.assertTrue(choice.ok,choice.summary)
        self.assertEqual("B",session.state.cards[target.object_id].owner)
        self.assertIsNone(self.resolve(session))
        created = [card for card in session.state.cards.values() if card.is_token and card.zone=="battlefield"]
        self.assertEqual(2,len(created))
        self.assertEqual([("Treasure Token", True, "A", "A")] * 2,
                         [(card.printed_name,card.tapped,card.controller,card.owner) for card in created])
        self.assertEqual(38,session.state.players["C"].life)
        self.replay(session,load=True)

    def test_paid_change_of_source_controller_can_include_the_original_source_as_recipient(self):
        session = self.session(61360404)
        source = self.add(session.engine,"Don Andres, the Renegade")
        control = self.add(session.engine,"Mind Control",seat="B",zone="hand")
        self.assert_closed(session,source)
        prior = tuple(session.engine._numeric_stat(source.object_id, k) for k in ("power","toughness"))
        self.prepare(session,actor="B",active="B",mana={"C":3,"U":2})
        self.checkpoint(session)
        self.cast_for(session,"B",control,[source.ref])
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[source.object_id]
        self.assertEqual(("A","B"),(current.owner,current.controller))
        self.assert_stats(session,source,tuple(value+2 for value in prior))
        self.assertTrue({"menace","deathtouch"} <= {k.casefold() for k in self.current_data(session,source)["keywords"]})
        self.assertIn("vampire",session.engine._type_parts(self.current_data(session,source)["type_line"])[1])
        self.replay(session,load=True)


    def test_paid_land_entry_reapplies_the_type_dependency_order_across_two_recipients(self):
        from quorune.control_effects import change_control

        session = self.session(61360405)
        engine = session.engine
        source = self.add(engine,"Don Andres, the Renegade")
        lands = self.add(engine,"Living Lands",seat="D")
        forest = self.add(engine,"Forest",seat="B")
        island = self.add(engine,"Island",seat="C")
        enabler = self.add(engine,"Yavimaya, Cradle of Growth",zone="hand")
        for card in (source,lands,enabler):
            self.assert_closed(session,card)
        change_control(engine,forest.object_id,"A",reason="Set up the first borrowed land")
        change_control(engine,island.object_id,"A",reason="Set up the second borrowed land")
        self.prepare(session)
        self.checkpoint(session)
        self.assert_modifier(session,forest,True)
        self.assert_modifier(session,island,False)
        self.assert_stats(session,forest,(3,3))
        offers=session.packet("pilot:A",full=True)["decision"]["ctx"]["legal"]["actions"]
        offer=next(row for row in offers if row.get("card")==enabler.ref and row["action"]=="play_land")
        accepted=session.act("pilot:A",{"action_id":offer["id"]})
        self.assertTrue(accepted.ok,accepted.summary)
        for card in (forest,island):
            self.assert_modifier(session,card,True)
            self.assert_stats(session,card,(3,3))
        self.assert_modifier(session,enabler,False)
        self.replay(session,load=True)

    def test_paid_same_layer_source_setting_prevents_the_modifier_from_starting(self):
        from quorune.control_effects import change_control

        session = self.session(61360406)
        source = self.add(session.engine,"Don Andres, the Renegade")
        target = self.add(session.engine,"Grizzly Bears",seat="C")
        aura = self.add(session.engine,"Song of the Dryads",seat="B",zone="hand")
        self.assert_closed(session,source)
        self.assert_closed(session,aura)
        change_control(session.engine,target.object_id,"A",reason="Set up an existing borrowed creature")
        self.prepare(session,actor="B",active="B",mana={"C":2,"G":1})
        self.checkpoint(session)
        self.assert_modifier(session,target,True)
        self.cast_for(session,"B",aura,[source.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual({"land"},session.engine._type_parts(self.current_data(session,source)["type_line"])[0])
        self.assert_modifier(session,target,False)
        self.assert_stats(session,target,(2,2))
        self.replay(session,load=True)


    def test_actual_control_application_fault_restores_exact_state_and_can_retry(self):
        from unittest.mock import patch

        from quorune import control_effects
        from quorune.errors import GameRuleError

        session = self.session(61360410)
        engine = session.engine
        self.add(engine,"Don Andres, the Renegade")
        aura = self.add(engine,"Mind Control",zone="hand")
        target = self.add(engine,"Grizzly Bears",seat="B")
        self.prepare(session,mana={"C":3,"U":2})
        self.checkpoint(session)
        self.cast_for(session,"A",aura,[target.ref])
        original = control_effects._commit_control_change
        committed = []
        def fault(host, object_id, controller, *, reason):
            original(host,object_id,controller,reason=reason)
            if object_id == target.object_id:
                committed.append(host.state.cards[object_id].controller)
                raise GameRuleError("Injected borrowed creature control fault")
        with patch.object(control_effects,"_commit_control_change",side_effect=fault):
            for _ in range(12):
                principal = session.pending_principals()[0]
                before = authoritative_state_hash(session.state)
                response = session.act(principal,{"action_id":"pass"})
                if not response.ok:
                    self.assertIn("Injected borrowed creature control fault",response.summary)
                    break
            else:
                self.fail("The real control transaction did not reach the fault")
        self.assertEqual(["A"],committed)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertEqual("B",session.state.cards[target.object_id].controller)
        self.assert_modifier(session,target,False)
        session=self.replay(session,load=True)
        self.assertTrue(session.act(principal,{"action_id":"pass"}).ok)
        self.assertIsNone(self.resolve(session))
        self.assert_modifier(session,target,True)
        self.replay(session,load=True)

    def test_native_owned_recipient_substitution_is_killed(self):
        from dataclasses import replace
        from unittest.mock import patch

        from quorune import continuous_effects

        original = continuous_effects._matches
        intercepted = []
        def forget_owner(query,state,context):
            if query.controlled_by_nonowner:
                intercepted.append(context.get("owner"))
                query = replace(query,controlled_by_nonowner=False,_serialization_version=2)
            return original(query,state,context)
        result = unittest.TestResult()
        with patch.object(continuous_effects,"_matches",side_effect=forget_owner):
            type(self)("test_paid_original_tracks_a_borrowed_creature_and_retains_its_printed_types").run(result)
        self.assertEqual([],result.errors)
        self.assertEqual(1,len(result.failures))
        self.assertIn("A",intercepted)
        self.assertIn("AssertionError: False != True",result.failures[0][1])

    def test_genuine_relation_and_modifier_owner_withdrawals_fail_closed(self):
        from copy import deepcopy

        from common import ROOT
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests

        raw = json.loads((ROOT/"quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        declarations = read_evidence_document(ROOT/"platform/capability-evidence-declarations.json")
        tests = set(discover_tests(ROOT).values())
        def rebuilt(value):
            owner=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=declarations,discovered_test_ids=tests)
            owner.mark_evidence_verified(validate_capability_evidence_index(index,registry=owner))
            return owner
        def ready(name,registry):
            card=self.db.lookup(name,fuzzy=False)
            program=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),
                capability_registry=registry,capability_profile="commander_review")
            return bind_card_program_runtime(program,capability_registry=registry,profile="commander_review")["strict_capability_ready"]
        card="Don Andres, the Renegade"
        self.assertTrue(ready(card,rebuilt(raw)))
        for cap in ("state_query.permanent.controlled_by_nonowner","continuous.characteristics.fixed_nonowner_creature_modifier",
                    "combat.block.menace"):
            with self.subTest(cap=cap):
                changed=deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"]==cap).update(
                    status="blocked",blockers=["Focused nonowner modifier owner withdrawal"])
                registry=rebuilt(changed)
                self.assertFalse(ready(card,registry))
                self.assertTrue(ready("Grizzly Bears",registry))

    def test_raw_modifier_infers_bound_relation_and_keyword_owners(self):
        from dataclasses import replace

        from quorune.card_programs.binding import bind_semantic_program_runtime

        session = self.session(61360411)
        source = self.add(session.engine,"Don Andres, the Renegade",zone="hand")
        self.assert_closed(session,source)
        program, = (program for program in session.engine.semantics.programs_for_oracle(source.oracle_id)
                    if any("nonowner-creature-modifier" in descriptor["handler_id"] for descriptor in program.handlers))
        for cap in ("state_query.permanent.controlled_by_nonowner","continuous.characteristics.fixed_nonowner_creature_modifier",
                    "combat.block.menace","damage.result.deathtouch"):
            with self.subTest(cap=cap):
                retained=[value for value in program.capability_dependencies if value!=cap]
                changed=replace(program,coverage=[],capability_dependencies=retained,
                    capability_closure=self.registry.closure(retained,profile="commander_review").to_dict())
                proof=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile="commander_review")
                self.assertFalse(proof["strict"])
                self.assertIn("capability:undeclared_runtime_dependency:"+cap,proof["blockers"])

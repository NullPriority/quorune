from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, advance_fixture_turn, keep_all, make_session
from quorune.ability_fragments import (
    StaticComponentSpec,
    ability_fragment_to_dict,
)
from quorune.card_programs import (
    bind_card_program_runtime,
    compile_card_program,
)
from quorune.carddb import CardDatabase
from quorune.characteristic_fragments import (
    CharacteristicQuantityScope,
    CharacteristicQuantitySpec,
)
from quorune.compiler.continuous_templates import (
    fixed_public_state_characteristics_handler,
)
from quorune.compiler.public_state_fact_queries import (
    fixed_graveyard_condition_query,
    fixed_public_fact_condition,
)
from quorune.continuous_conditions import (
    FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
    FixedPublicStateConditionError,
    FixedPublicStateConditionKind,
    FixedPublicStateFact,
    FixedPublicStateConditionSnapshot,
    FixedPublicStateConditionSpec,
)
from quorune.continuous_effects import (
    CharacteristicState,
    Layer,
    evaluate_continuous_effects,
)
from quorune.deck import DeckLoader
from quorune.engine import CommanderEngine
from quorune.model import CardInstance
from quorune.object_predicate import (
    ObjectQuerySpec,
    PermanentStatePredicateSpec,
)
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.semantics import SemanticProgram
from quorune.semantic_runtime import (
    ContinuousEffectSourceContext,
    LifeChangeIntent,
    default_continuous_effect_component_registry,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.turn_history import record_creature_attack_history
from scripts.build_test_database import build_fixture_database


CAPABILITY_ID = "continuous.characteristics.fixed_public_state"
TEMPLATE_ID = "continuous-fixed-public-state-characteristics-v1"
QUERY_HANDLER_ID = "ability.static.query-characteristic-modifier.v1"


class AdditionalConditionalModifierCompilerTests(unittest.TestCase):
    """CR 611.3a/613.4c: additional fixed bonuses add and remain live.

    The wording carries no remembered value or reference to an earlier bonus.
    Only a complete signed fixed modifier behind a represented condition is
    admitted; increasing a variable or repeating an effect remains residual.
    """

    def test_additional_and_also_fixed_bonuses_keep_the_complete_condition(self):
        from types import SimpleNamespace
        from test_qualified_zone_event_queries import query_record

        registry = load_default_capability_registry()
        cases = (
            ("As long as you have no cards in hand, this creature gets an additional +2/+0.", 2, 0),
            ("This creature gets an additional +1/-1 as long as you have no cards in hand.", 1, -1),
            ("As long as equipped creature is a Human, it gets an additional +1/+0.", 1, 0),
            ("Threshold — Enchanted creature gets an additional +2/-2 as long as there are seven or more cards in your graveyard.", 2, -2),
            ("Creatures you control also get +1/+0 and have trample as long as you control six or more creatures.", 1, 0),
        )
        for text, power, toughness in cases:
            with self.subTest(text=text):
                ir = compile_oracle_card(query_record(text), capability_registry=registry)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(text, node.text)
                self.assertEqual((0, len(text)), (node.span.start, node.span.end))
                handler = next(h for h in node.handlers if "modifier" in h)
                self.assertEqual(power, handler["modifier"]["power"])
                self.assertEqual(toughness, handler["modifier"]["toughness"])
                self.assertIn("source_condition", handler)
                self.assertTrue(bind_card_program_runtime(
                    compile_card_program(
                        SimpleNamespace(rulings=lambda _oracle_id: ()), query_record(text),
                        capability_registry=registry, capability_profile="commander_review",
                        trust_level="trusted",
                    ),
                    capability_registry=registry, profile="commander_review",
                )["strict_capability_ready"])

    def test_additional_wording_does_not_admit_unrepresented_bonus_or_condition(self):
        from test_qualified_zone_event_queries import query_record

        registry = load_default_capability_registry()
        for text in (
            "As long as you have no cards in hand, this creature gets an additional +X/+X.",
            "As long as you control a stickered permanent, this creature gets an additional +2/+0.",
            "As long as you have no cards in hand, this creature gets an additional +2/+0 for each card in your graveyard.",
            "As long as you have no cards in hand, this creature gets an additional +2/+0. Repeat this effect.",
        ):
            with self.subTest(text=text):
                self.assertNotEqual("exact", compile_oracle_card(
                    query_record(text), capability_registry=registry,
                ).status)

    def test_additional_modifier_producer_and_capability_omissions_fail_closed(self):
        from test_qualified_zone_event_queries import query_record

        text = "As long as you have no cards in hand, this creature gets an additional +2/+0."
        registry = load_default_capability_registry()
        with mock.patch(
            "quorune.compiler.continuous_templates._fixed_additive_modifier_body",
            side_effect=lambda body: body,
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                query_record(text), capability_registry=registry,
            ).status)
        blocked = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        for capability in blocked["capabilities"]:
            if capability["id"] == CAPABILITY_ID:
                capability["status"] = "blocked"
                capability["blockers"] = ["focused additive modifier omission"]
        self.assertNotEqual("exact", compile_oracle_card(
            query_record(text), capability_registry=CapabilityRegistry(blocked),
        ).status)
FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "fixed-public-state-characteristics-cards.json"
)


def focused_card_database(directory: str) -> CardDatabase:
    path = Path(directory) / "fixed-public-state-characteristics.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE,
            ROOT
            / "tests"
            / "fixtures"
            / "fixed-public-state-interaction-cards.json",
        ],
        path,
    )
    return CardDatabase(path)


def source_context(
    *,
    active_player: str | None = "A",
    hand_count: int = 3,
    graveyard_count: int = 0,
    controller_life: int = 40,
    opponent_life: tuple[int, ...] = (40, 40, 40),
    turn_sequence: int = 4,
    entered_turn_sequence: int = 1,
    counters: tuple[tuple[str, int], ...] = (),
    draw_count: int = 0,
    spell_count: int = 0,
    noncreature_spell_count: int = 0,
    instant_sorcery_count: int = 0,
    opponent_poison: tuple[int, ...] = (),
    controller_is_monarch: bool = False,
    source_query_matches: bool | None = None,
    attached_query_matches: bool | None = None,
    condition_quantity: int | None = None,
) -> ContinuousEffectSourceContext:
    return ContinuousEffectSourceContext(
        source_object_id="public-state-source",
        source_ref="PUBLIC-STATE-SOURCE",
        source_controller="A",
        source_timestamp=7,
        component_id="public-state:0",
        source_logical_object_id="public-state-source@1",
        public_state=FixedPublicStateConditionSnapshot(
            source_controller="A",
            active_player=active_player,
            controller_hand_count=hand_count,
            controller_graveyard_card_count=graveyard_count,
            controller_life=controller_life,
            opponent_life_totals=opponent_life,
            turn_sequence=turn_sequence,
            source_entered_battlefield_turn_sequence=(
                entered_turn_sequence
            ),
            source_counters=counters,
            controller_draw_count=draw_count,
            controller_spell_cast_count=spell_count,
            controller_noncreature_spell_cast_count=(
                noncreature_spell_count
            ),
            controller_instant_sorcery_cast_count=instant_sorcery_count,
            opponent_poison_counter_counts=opponent_poison,
            controller_is_monarch=controller_is_monarch,
            source_query_matches=source_query_matches,
            attached_query_matches=attached_query_matches,
            condition_quantity=condition_quantity,
        ),
    )


class FixedPublicStateCharacteristicCompilerTests(unittest.TestCase):
    def test_boolean_composition_preserves_attachment_numeric_trigger_and_status_boundaries(self):
        from quorune.compiler.public_state_queries import fixed_public_state_parts
        from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding
        from test_qualified_zone_event_queries import query_record
        registry=load_default_capability_registry()
        text='As long as you have no cards in hand and enchanted creature is a Human, it gets +1/+1.'
        ir=compile_oracle_card(query_record(text),capability_registry=registry)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertEqual('attached',ir.faces[0].nodes[0].handlers[0]['target']['kind'])
        ambiguous='As long as this Equipment is untapped and enchanted creature is a Human, it gets +1/+1.'
        self.assertIsNone(fixed_public_state_parts(ambiguous,source_name='Generic Query Observer'))
        trigger='When this creature enters, if this creature has a charge counter on it and you have no cards in hand, draw a card.'
        self.assertIsNone(fixed_counter_trigger_binding(trigger,card_name='Generic Query Observer'))
        text="As long as you're the monarch and you have no cards in hand, this creature has flying."
        ir=compile_oracle_card(query_record(text),capability_registry=registry)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertIn('variant.monarch.designate',ir.faces[0].nodes[0].capability_dependencies)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        changed=copy.deepcopy(raw)
        next(row for row in changed['capabilities'] if row['id']=='variant.monarch.designate').update(status='blocked',blockers=['compound monarch producer withdrawn'])
        self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=CapabilityRegistry(changed)).status)
        with mock.patch('quorune.compiler.public_state_queries.compose_public_conditions',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)

    def test_boolean_condition_truth_query_snapshots_and_malformed_inputs_are_distinct(self):
        from quorune.compiler.public_state_queries import fixed_public_state_condition
        from quorune.card_programs.runtime import _FixedPublicStateSnapshotResolver
        from types import SimpleNamespace
        both=fixed_public_state_condition('you have 30 or more life and you have no cards in hand',source_name='Generic Source')
        either=fixed_public_state_condition('you have 30 or more life or you have no cards in hand',source_name='Generic Source')
        for life,hand in ((20,1),(20,0),(40,1),(40,0)):
            base=source_context(controller_life=life,hand_count=hand).public_state
            snapshot=replace(base,term_snapshots=(base,base))
            self.assertEqual(life>=30 and hand==0,both.matches(snapshot))
            self.assertEqual(life>=30 or hand==0,either.matches(snapshot))
        with self.assertRaises(FixedPublicStateConditionError):both.matches(base)
        with self.assertRaises(FixedPublicStateConditionError):replace(base,term_snapshots=(base,replace(base,source_controller='B')))
        payload=both.to_dict()
        for field,value in (('schema_version',True),('terms',[]),('terms',[payload,payload]),('unknown',True)):
            with self.assertRaises(FixedPublicStateConditionError):FixedPublicStateConditionSpec.from_dict({**payload,field:value})
        for text in ('you have 30 or more life and the top card of your library is blue',
                     'you have no cards in hand and your turn or you have 30 or more life'):
            self.assertIsNone(fixed_public_state_condition(text,source_name='Generic Source'))
        queries=fixed_public_state_condition('you control three or more creatures and you control four or more artifacts',source_name='Generic Source')
        self.assertIsNotNone(queries)
        state=SimpleNamespace(players={'A':SimpleNamespace(zones={'hand':[],'graveyard':[]},life=40)},turn_order=['A'],active_player='A',cards={})
        source=SimpleNamespace(controller='A',ref='SOURCE',object_id='source',counters={})
        resolver=_FixedPublicStateSnapshotResolver(state,public_object_resolver=None,quantity_resolver=lambda source,q:3 if q.query.types_all==('creature',) else 1)
        snapshot=resolver.snapshot(source,queries)
        self.assertEqual((3,1),tuple(child.condition_quantity for child in snapshot.term_snapshots))
        self.assertFalse(queries.matches(snapshot))
        from quorune.semantic_runtime.fixed_characteristic_settings import validate_setting_condition
        with self.assertRaises(SemanticNodeError):validate_setting_condition(queries.to_dict())
        from test_qualified_zone_event_queries import query_record
        text='As long as you have 30 or more life and you control a creature with power 4 or greater, this creature gets +1/+1.'
        self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry()).status)

    def test_boolean_public_conditions_keep_independent_leaf_snapshots_and_operators(self):
        from test_qualified_zone_event_queries import query_record
        cases=(
            ('As long as you have 30 or more life and an opponent has 10 or less life, this creature gets +6/+6 and has flying.','all'),
            ('As long as you have 30 or more life or you have no cards in hand, this creature gets +1/+1.','any'),
        )
        for text,kind in cases:
            with self.subTest(text=text):
                ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0];self.assertEqual(text,node.text)
                condition=FixedPublicStateConditionSpec.from_dict(node.handlers[0]['source_condition'])
                self.assertEqual(kind,condition.kind.value)
                self.assertEqual(6,condition.schema_version)
                self.assertEqual(2,len(condition.terms))
                self.assertEqual(condition,FixedPublicStateConditionSpec.from_dict(condition.to_dict()))

    def test_conditional_typed_keyword_schema_dependencies_and_omission_fail_closed(self):
        from test_qualified_zone_event_queries import query_record
        from quorune.compiler.conditional_granted_ability_templates import conditional_typed_keyword_handler
        from quorune.semantic_runtime.conditional_continuous import ConditionalTypedKeywordHandler

        text = 'During your turn, this creature has ward {2}.'
        compiled = conditional_typed_keyword_handler(text, source_name='Generic Query Observer')
        self.assertIsNotNone(compiled)
        descriptor = compiled[1]
        handler = ConditionalTypedKeywordHandler()
        handler.validate(descriptor)
        for altered in (
            text.replace('{2}', '{X}'), text.replace('{2}', '\u2014Pay 2 life'),
            text.replace('During your turn', 'As long as the top card of your library is blue'),
            text.replace('ward {2}', 'unrepresented'), text + ' Draw a card.',
        ):
            self.assertIsNone(conditional_typed_keyword_handler(altered, source_name='Generic Query Observer'))
        for field, value in (('schema_version', True), ('unknown', True)):
            with self.assertRaises(SemanticNodeError):
                handler.validate({**descriptor, field:value})
        for field, value in (('add_abilities', ['Ward {3}']), ('add_ability_fragments', []), ('power', True)):
            altered = copy.deepcopy(descriptor)
            altered['modifier'][field] = value
            with self.assertRaises(SemanticNodeError):
                handler.validate(altered)
        legacy = {**descriptor, 'handler_id':FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID, 'schema_version':1}
        with self.assertRaises(SemanticNodeError):
            default_continuous_effect_component_registry().validate(legacy)
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in ('continuous.characteristics.fixed_public_state', 'trigger.keyword.ward.fixed_generic'):
            altered = copy.deepcopy(raw)
            next(row for row in altered['capabilities'] if row['id']==capability).update(status='blocked', blockers=['conditional keyword dependency withdrawal'])
            self.assertNotEqual('exact', compile_oracle_card(query_record(text), capability_registry=CapabilityRegistry(altered)).status)
        with mock.patch('quorune.compiler.conditional_granted_ability_templates.conditional_typed_keyword_handler', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(query_record(text), capability_registry=load_default_capability_registry()).status)

    def test_conditional_typed_keywords_keep_their_fragments_and_condition(self):
        from test_qualified_zone_event_queries import query_record
        from quorune.ability_fragments import ability_fragment_from_dict
        from quorune.trigger_participation import WardSpec
        from quorune.ability_fragments import ProtectionSpec, CombatKeywordTriggerSpec, SpellCastKeywordTriggerSpec

        registry = load_default_capability_registry()
        cases = (
            ('This creature has prowess as long as you control a black creature.', SpellCastKeywordTriggerSpec, 'query_count_at_least'),
            ('During your turn, this creature has ward {2}.', WardSpec, 'controller_turn'),
            ('As long as this creature is untapped, other legendary creatures you control have ward {1}.', WardSpec, 'source_matches_query'),
            ('As long as this creature is untapped, it has protection from artifacts and from each color.', ProtectionSpec, 'source_matches_query'),
            ('As long as equipped creature is a Human, it has bushido 2.', CombatKeywordTriggerSpec, 'attached_matches_query'),
        )
        for text, fragment_type, condition_kind in cases:
            with self.subTest(text=text):
                ir = compile_oracle_card(query_record(text), capability_registry=registry,
                    capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = next(n for n in ir.faces[0].nodes if n.kind == 'static_ability')
                self.assertEqual(text, node.text)
                self.assertEqual((0, len(text)), (node.span.start, node.span.end))
                descriptor = node.handlers[0]
                self.assertEqual('continuous.characteristics.public-state-keywords.v1', descriptor['handler_id'])
                self.assertEqual(7, descriptor['schema_version'])
                self.assertEqual(condition_kind, descriptor['source_condition']['kind'])
                fragments = descriptor['modifier']['add_ability_fragments']
                self.assertTrue(fragments)
                self.assertTrue(all(isinstance(ability_fragment_from_dict(f), fragment_type) for f in fragments))
                default_continuous_effect_component_registry().validate(descriptor)
                self.assertTrue(bind_card_program_runtime(compile_card_program(
                    type('NoRulings', (), {'rulings':lambda self, record: ()})(), query_record(text),
                    capability_registry=registry, capability_profile='commander_review',
                    trust_level='trusted'), capability_registry=registry,
                    profile='commander_review')['strict_capability_ready'])

    def test_graveyard_static_grant_keeps_explicit_active_zone_and_owner_relative_query(self):
        from test_qualified_zone_event_queries import query_record
        text = 'As long as this card is in your graveyard and you control an Island, creatures you control have flying.'
        ir = compile_oracle_card(query_record(text), capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual('graveyard', node.active_zone)
        self.assertEqual(text, node.text)
        self.assertEqual('source_controller', node.handlers[0]['target']['target_controller'])
        query = node.handlers[0]['source_condition']['quantity']['query']
        self.assertEqual(['land'], query['types_all'])
        self.assertEqual(['island'], query['subtypes_all'])
        self.assertEqual([], query['supertypes_all'])

    def test_graveyard_grant_rejects_unrepresented_zone_condition_and_malformed_descriptor(self):
        from test_qualified_zone_event_queries import query_record
        from quorune.compiler.conditional_granted_ability_templates import graveyard_static_characteristics_handler
        from quorune.semantic_runtime.conditional_continuous import GraveyardStaticCharacteristicsHandler
        text = 'As long as this card is in your graveyard and you control an Island, creatures you control have flying.'
        descriptor = graveyard_static_characteristics_handler(text, source_name='Generic Query Observer')[1]
        handler = GraveyardStaticCharacteristicsHandler(); handler.validate(descriptor)
        for field, value in (('schema_version', True), ('handler_id', 'continuous.characteristics.fixed-public-state.v1')):
            with self.assertRaises(ValueError):handler.validate({**descriptor, field: value})
        for text in (
            'As long as this card is in exile and you control an Island, creatures you control have flying.',
            'As long as this card is in your graveyard and you control an Island, creatures you control have unrepresented.',
            'As long as this card is in your graveyard and the top card of your library is blue, creatures you control have flying.',
        ):
            self.assertIsNone(graveyard_static_characteristics_handler(text, source_name='Generic Query Observer'))
        raw = json.loads((Path(__file__).parents[1] / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        text = 'As long as this card is in your graveyard and you control an Island, creatures you control have flying.'
        for cap in ('continuous.ability.graveyard_static_grant', 'combat.block.flying'):
            changed = copy.deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == cap).update(status='blocked', blockers=['graveyard grant boundary'])
            self.assertNotEqual('exact', compile_oracle_card(query_record(text), capability_registry=CapabilityRegistry(changed)).status)

    def test_graveyard_grant_compiler_omission_mutant_is_killed(self):
        with mock.patch('quorune.compiler.conditional_granted_ability_templates.graveyard_static_characteristics_handler', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_graveyard_static_grant_keeps_explicit_active_zone_and_owner_relative_query()
    def test_conditional_stats_and_declaration_result_preserve_one_live_condition(self):
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.declaration_fragments import DeclarationRequirementTemplate, DeclarationRestrictionTemplate
        from quorune.ability_fragments import ability_fragment_from_dict

        registry = load_default_capability_registry()
        cases = (
            ("As long as you have no cards in hand, this creature gets +2/+0 and can't block.", DeclarationRestrictionTemplate),
            ("As long as there are four or more card types among cards in your graveyard, this creature gets +2/+2, has flying, and attacks each combat if able.", DeclarationRequirementTemplate),
            ("This creature gets +1/+0 and can't be blocked as long as you control eight or more lands.", DeclarationRestrictionTemplate),
        )
        for text, fragment_type in cases:
            with self.subTest(text=text):
                ir = compile_oracle_card(query_record(text), capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = next(n for n in ir.faces[0].nodes if n.kind == 'static_ability')
                self.assertEqual(text, node.text)
                self.assertEqual(1, node.span.line)
                descriptor = node.handlers[0]
                self.assertEqual('continuous.declaration.public-state.v1', descriptor['handler_id'])
                self.assertEqual(6, descriptor['schema_version'])
                self.assertIsInstance(ability_fragment_from_dict(descriptor['modifier']['add_ability_fragments'][0]), fragment_type)
                self.assertIn('combat.declaration.typed_components', node.capability_dependencies)
                self.assertIn('continuous.characteristics.fixed_public_state', node.capability_dependencies)

    def test_conditional_declaration_composition_rejects_open_grammar_and_malformed_descriptors(self):
        from test_qualified_zone_event_queries import query_record
        from quorune.compiler.declaration_nodes import conditional_characteristic_declaration_grant
        from quorune.semantic_runtime.conditional_continuous import ConditionalDeclarationGrantHandler

        text = "As long as you have no cards in hand, this creature gets +2/+0 and can't block."
        compiled = conditional_characteristic_declaration_grant(text, source_name='Generic Query Observer')
        self.assertIsNotNone(compiled)
        handler = ConditionalDeclarationGrantHandler()
        descriptor = compiled[1]
        handler.validate(descriptor)
        for altered in (
            text + ' Draw a card.',
            text.replace('+2/+0', '+X/+0'),
            text.replace("can't block", "can't block unless you pay 1 life"),
            text.replace('no cards in hand', 'the chosen card in hand'),
        ):
            self.assertIsNone(conditional_characteristic_declaration_grant(altered, source_name='Generic Query Observer'))
        for field, value in (('schema_version', True), ('unknown', True)):
            with self.assertRaises(SemanticNodeError):
                handler.validate({**descriptor, field:value})
        changed = copy.deepcopy(descriptor)
        changed['modifier']['add_ability_fragments'] *= 2
        with self.assertRaises(SemanticNodeError):
            handler.validate(changed)
        changed = copy.deepcopy(descriptor)
        changed['modifier']['power'] = True
        with self.assertRaises(SemanticNodeError):
            handler.validate(changed)
        changed = copy.deepcopy(descriptor)
        changed['modifier']['add_ability_fragments'][0]['scope'] = 'all_creatures'
        with self.assertRaises((SemanticNodeError, ValueError)):
            handler.validate(changed)
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in ('combat.declaration.typed_components', 'continuous.characteristics.fixed_public_state'):
            altered = copy.deepcopy(raw)
            next(row for row in altered['capabilities'] if row['id']==capability).update(status='blocked', blockers=['independent declaration dependency withdrawal'])
            registry = CapabilityRegistry(altered)
            ir = compile_oracle_card(query_record(text), capability_registry=registry, capability_profile='commander_review')
            self.assertNotEqual('exact', ir.status)

    def test_conditional_declaration_producer_omission_mutant_is_killed(self):
        from test_qualified_zone_event_queries import query_record
        text = "As long as you have no cards in hand, this creature gets +2/+0 and can't block."
        with mock.patch('quorune.compiler.declaration_nodes.conditional_characteristic_declaration_grant', return_value=None):
            with self.assertRaises(AssertionError):
                ir = compile_oracle_card(query_record(text), capability_registry=load_default_capability_registry(), capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)

    def test_keyword_qualified_opponent_condition_remains_residual_without_raising(self):
        from quorune.compiler.public_state_queries import fixed_public_state_condition
        self.assertIsNone(fixed_public_state_condition(
            'an opponent controls a creature with flying', source_name='Groundling Pouncer',
        ))
        self.assertIsNone(fixed_public_state_condition(
            'you control a creature with flying', source_name='Boundary witness',
        ))
        from test_fixed_optional_mana_payment_triggers import payment_record
        ir = compile_oracle_card(payment_record(
            'As long as an opponent controls a creature with flying, this creature gets +1/+1 and has flying.',
        ), capability_registry=self.capabilities, capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)
        self.assertTrue(ir.material_residuals)
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_closed_public_state_grammar_compiles_capability_closed(self):
        cases = {
            "Fresh-Faced Recruit": "controller_turn",
            "Street Riot": "controller_turn",
            "Chaos Imps": "source_counter_at_least",
            "Keldon Strike Team": "source_entered_this_turn",
            "Neheb, the Worthy": "controller_hand_count_at_most",
        }
        for name, condition_kind in cases.items():
            with self.subTest(name=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                nodes = [
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.template_id == TEMPLATE_ID
                ]
                self.assertEqual(1, len(nodes))
                node = nodes[0]
                self.assertTrue(node.exact)
                self.assertEqual(
                    FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
                    node.handlers[0]["handler_id"],
                )
                self.assertEqual(
                    condition_kind,
                    node.handlers[0]["source_condition"]["kind"],
                )
                self.assertIn(CAPABILITY_ID, node.capability_dependencies)
                self.assertGreater(node.span.line, 0)

        street = compile_card_program(
            self.db,
            self.db.lookup("Street Riot"),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        self.assertEqual((), street.residuals)
        ability = next(
            ability
            for ability in street.abilities
            if ability.provenance.get("template_id") == TEMPLATE_ID
        )
        self.assertGreaterEqual(
            set(ability.capability_dependencies),
            {
                CAPABILITY_ID,
                "continuous.power_toughness.fixed_anthem",
                "continuous.ability.fixed_query_keyword_grant",
                "combat.damage.assignment.trample",
            },
        )

    def test_graveyard_threshold_migrates_to_typed_query_owner(self):
        program = compile_card_program(
            self.db,
            self.db.lookup("Krosan Beast"),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )

        self.assertEqual((), program.residuals)
        handlers = [
            descriptor.get("handler_id")
            for ability in program.abilities
            for descriptor in ability.handlers
        ]
        self.assertIn(QUERY_HANDLER_ID, handlers)
        self.assertNotIn(FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID, handlers)

    def test_public_object_state_and_query_conditions_compile_closed(self):
        cases = {
            "Public State Equipment Standard": "source_matches_query",
            "Public State Attachment Standard": "attached_matches_query",
            "Public State Metalcraft Standard": "query_count_at_least",
        }
        for name, condition_kind in cases.items():
            with self.subTest(name=name):
                program = compile_card_program(
                    self.db,
                    self.db.lookup(name),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="trusted",
                )
                self.assertEqual((), program.residuals)
                descriptor = next(
                    descriptor
                    for ability in program.abilities
                    for descriptor in ability.handlers
                    if descriptor.get("handler_id")
                    == FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID
                )
                self.assertEqual(
                    condition_kind,
                    descriptor["source_condition"]["kind"],
                )
                self.assertEqual(2, descriptor["source_condition"]["schema_version"])
                default_continuous_effect_component_registry().validate(
                    descriptor
                )

    def test_fixed_public_condition_queries_compile_across_authoritative_facts(self):
        base = self.db.lookup("Fresh-Faced Recruit")
        cases = (
            (
                "This creature has first strike as long as it's attacking.",
                "source_matches_query",
            ),
            (
                "As long as this enchantment has seven or more quest "
                "counters on it, creatures you control get +5/+5.",
                "source_counter_at_least",
            ),
            (
                "As long as you control another multicolored permanent, "
                "this creature gets +1/+1 and has flying.",
                "query_count_at_least",
            ),
            (
                "As long as you control no untapped lands, this creature "
                "gets +2/+1.",
                "query_count_at_most",
            ),
            (
                "This creature gets +3/+3 as long as there is a land card "
                "in your graveyard.",
                "query_count_at_least",
            ),
            (
                "As long as you have seven or more cards in hand, this "
                "creature has first strike.",
                "controller_hand_count_at_least",
            ),
            (
                "As long as you've drawn two or more cards this turn, this "
                "creature has lifelink.",
                "controller_draw_count_at_least",
            ),
            (
                "As long as you've cast two or more noncreature spells this "
                "turn, this creature has double strike.",
                "controller_noncreature_spell_cast_count_at_least",
            ),
            (
                "As long as you've cast two or more spells this turn, this "
                "creature gets +2/+0.",
                "controller_spell_cast_count_at_least",
            ),
            (
                "This creature has flying as long as you've cast an instant "
                "or sorcery spell this turn.",
                "controller_instant_sorcery_cast_count_at_least",
            ),
            (
                "This creature has deathtouch as long as an opponent has "
                "three or more poison counters.",
                "opponent_poison_counter_at_least",
            ),
            (
                "As long as you're the monarch, permanents you control have "
                "hexproof.",
                "controller_is_monarch",
            ),
        )
        for index, (text, expected_kind) in enumerate(cases):
            with self.subTest(text=text):
                record = replace(
                    base,
                    oracle_id=(
                        f"00000000-0000-4000-8000-{118_240_000 + index:012d}"
                    ),
                    name=f"Public Condition Query {index}",
                    type_line="Creature — Fixture",
                    oracle_text=text,
                    keywords=(),
                )
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(TEMPLATE_ID, node.template_id)
                condition = node.handlers[0]["source_condition"]
                self.assertEqual(expected_kind, condition["kind"])
                if expected_kind == "controller_is_monarch":
                    self.assertIn(
                        "variant.monarch.designate",
                        node.capability_dependencies,
                    )
                default_continuous_effect_component_registry().validate(
                    node.handlers[0]
                )

        attached_state = fixed_public_state_characteristics_handler(
            "Enchanted creature has shroud as long as it's untapped.",
            source_name="Attached Condition Query",
        )
        self.assertIsNotNone(attached_state)
        self.assertEqual(
            "attached_matches_query",
            attached_state[1]["source_condition"]["kind"],
        )
        attached_color = fixed_public_state_characteristics_handler(
            "Enchanted creature gets +1/+2 as long as it's white.",
            source_name="Attached Color Query",
        )
        self.assertIsNotNone(attached_color)
        self.assertEqual(
            ["W"],
            attached_color[1]["source_condition"]["predicate"]["colors_all"],
        )
        attached_opponent = fixed_public_state_characteristics_handler(
            "Enchanted creature gets +2/+2 as long as an opponent controls "
            "a black permanent.",
            source_name="Attached Opponent Query",
        )
        self.assertIsNotNone(attached_opponent)
        assert attached_opponent is not None
        opponent_condition = attached_opponent[1]["source_condition"]
        self.assertEqual(
            ("query_count_at_least", 1, "opponent_zones", ["B"]),
            (
                opponent_condition["kind"],
                opponent_condition["amount"],
                opponent_condition["quantity"]["scope"],
                opponent_condition["quantity"]["query"]["colors_all"],
            ),
        )
        self.assertEqual(
            ("attached", ["creature"]),
            (
                attached_opponent[1]["target"]["kind"],
                attached_opponent[1]["target"]["types_all"],
            ),
        )

    def test_public_condition_closure_compiles_typed_queries_and_facts(self):
        base = self.db.lookup("Fresh-Faced Recruit")
        cases = (
            (
                "Delirium — This creature gets +2/+2 as long as there are "
                "four or more card types among cards in your graveyard.",
                "public_fact_at_least",
                "controller_graveyard_distinct_card_types",
            ),
            (
                "Celebration — This creature gets +1/+1 and has trample as "
                "long as two or more nonland permanents entered the "
                "battlefield under your control this turn.",
                "public_fact_at_least",
                "controller_nonland_permanents_entered_this_turn",
            ),
            (
                "As long as you control a Nissa planeswalker, this creature "
                "gets +2/+0 and has vigilance.",
                "query_count_at_least",
                None,
            ),
            (
                "This creature gets +2/+2 as long as you have more cards in "
                "hand than each opponent.",
                "public_fact_at_least",
                "controller_hand_advantage",
            ),
            (
                "As long as your life total is less than or equal to half "
                "your starting life total, this creature has indestructible.",
                "public_fact_at_least",
                "controller_at_or_below_half_starting_life",
            ),
            (
                "This creature has flying as long as an artifact card is in "
                "your graveyard.",
                "query_count_at_least",
                None,
            ),
            (
                "This creature has indestructible as long as it attacked a "
                "battle this turn.",
                "public_fact_at_least",
                "source_attacked_battle_this_turn",
            ),
            (
                "This creature gets +2/+0 as long as you gained life this "
                "turn.",
                "public_fact_at_least",
                "controller_life_gained_this_turn",
            ),
        )
        for index, (text, expected_kind, expected_fact) in enumerate(cases):
            with self.subTest(text=text):
                record = replace(
                    base,
                    oracle_id=(
                        f"00000000-0000-4000-8000-{118_260_000 + index:012d}"
                    ),
                    name=f"Public Condition Closure {index}",
                    type_line="Creature — Fixture",
                    oracle_text=text,
                    keywords=(),
                )
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                if expected_fact == "controller_graveyard_distinct_card_types":
                    # The leaf public-fact contract remains valid. Integrated
                    # lowering now uses the shared collection quantity owner.
                    leaf = fixed_public_state_characteristics_handler(text, source_name=record.name)
                    self.assertEqual(expected_kind, leaf[1]["source_condition"]["kind"])
                    self.assertEqual(expected_fact, leaf[1]["source_condition"]["fact"])
                    default_continuous_effect_component_registry().validate(leaf[1])
                    node = next(node for face in ir.faces for node in face.nodes
                                if node.template_id == "continuous-self-query-characteristics-v1")
                    value = node.handlers[0]["fragment"]["value"]
                    self.assertEqual("fixed_if_threshold", value["calculation"])
                    self.assertEqual(4, value["minimum_count"])
                    self.assertEqual("distinct_card_types", value["quantity"]["reduction"])
                    self.assertFalse(value["quantity"]["query"]["token"])
                    continue
                node = next(
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.template_id == TEMPLATE_ID
                )
                condition = node.handlers[0]["source_condition"]
                self.assertEqual(expected_kind, condition["kind"])
                self.assertEqual(expected_fact, condition.get("fact"))
                default_continuous_effect_component_registry().validate(
                    node.handlers[0]
                )

    def test_public_fact_descriptors_preserve_thresholds_subtypes_and_axes(self):
        threshold_cases = (
            (
                "two or more nonland permanents entered the battlefield under "
                "your control this turn",
                "controller_nonland_permanents_entered_this_turn",
            ),
            (
                "two or more creatures died under your control this turn",
                "controller_creatures_died_this_turn",
            ),
        )
        for text, fact in threshold_cases:
            with self.subTest(text=text):
                condition = fixed_public_fact_condition(
                    text,
                    source_name="Threshold Fixture",
                )
                self.assertIsNotNone(condition)
                assert condition is not None
                self.assertEqual(4, condition.schema_version)
                self.assertEqual(
                    condition,
                    FixedPublicStateConditionSpec.from_dict(
                        condition.to_dict()
                    ),
                )
                self.assertEqual(2, condition.amount)
                self.assertEqual(fact, condition.fact.value)

        for plural, subtype in (
            ("Goblins", "goblin"),
            ("Elves", "elf"),
            ("Samurai", "samurai"),
        ):
            with self.subTest(plural=plural):
                condition = fixed_public_fact_condition(
                    f"you attacked with two or more {plural} this turn",
                    source_name="Attack Fixture",
                )
                self.assertIsNotNone(condition)
                assert condition is not None
                self.assertEqual(4, condition.schema_version)
                self.assertEqual(2, condition.amount)
                self.assertEqual(subtype, condition.fact_parameter)

        self.assertIsNone(
            fixed_public_fact_condition(
                "you attacked with two or more Shinythings this turn",
                source_name="Attack Fixture",
            )
        )
        green = fixed_graveyard_condition_query(
            "a green card is in your graveyard"
        )
        self.assertIsNotNone(green)
        assert green is not None and green.quantity is not None
        self.assertEqual(("G",), green.quantity.query.colors_all)
        self.assertFalse(green.quantity.query.subtypes_all)
        goblin = fixed_graveyard_condition_query(
            "a Goblin card is in your graveyard"
        )
        self.assertIsNotNone(goblin)
        assert goblin is not None and goblin.quantity is not None
        self.assertEqual(("goblin",), goblin.quantity.query.subtypes_all)
        self.assertIsNone(
            fixed_graveyard_condition_query(
                "a shiny card is in your graveyard"
            )
        )

    def test_fixed_public_condition_models_use_closed_current_facts(self):
        cases = (
            (
                FixedPublicStateConditionSpec(
                    FixedPublicStateConditionKind.CONTROLLER_HAND_COUNT_AT_LEAST,
                    amount=7,
                ),
                source_context(hand_count=7).public_state,
            ),
            (
                FixedPublicStateConditionSpec(
                    FixedPublicStateConditionKind.CONTROLLER_DRAW_COUNT_AT_LEAST,
                    amount=2,
                ),
                source_context(draw_count=2).public_state,
            ),
            (
                FixedPublicStateConditionSpec(
                    FixedPublicStateConditionKind
                    .CONTROLLER_NONCREATURE_SPELL_CAST_COUNT_AT_LEAST,
                    amount=2,
                ),
                source_context(noncreature_spell_count=2).public_state,
            ),
            (
                FixedPublicStateConditionSpec(
                    FixedPublicStateConditionKind.OPPONENT_POISON_COUNTER_AT_LEAST,
                    amount=3,
                ),
                source_context(opponent_poison=(0, 3, 1)).public_state,
            ),
            (
                FixedPublicStateConditionSpec(
                    FixedPublicStateConditionKind.CONTROLLER_IS_MONARCH,
                ),
                source_context(controller_is_monarch=True).public_state,
            ),
            (
                FixedPublicStateConditionSpec(
                    FixedPublicStateConditionKind.QUERY_COUNT_AT_MOST,
                    amount=0,
                    quantity=CharacteristicQuantitySpec(
                        scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
                        query=ObjectQuerySpec(zones=("battlefield",)),
                    ),
                    schema_version=2,
                ),
                source_context(condition_quantity=0).public_state,
            ),
        )
        for condition, snapshot in cases:
            with self.subTest(kind=condition.kind):
                assert snapshot is not None
                self.assertTrue(condition.matches(snapshot))
                self.assertFalse(
                    condition.matches(
                        replace(
                            snapshot,
                            controller_hand_count=0,
                            controller_draw_count=0,
                            controller_noncreature_spell_cast_count=0,
                            opponent_poison_counter_counts=(),
                            controller_is_monarch=False,
                            condition_quantity=1,
                        )
                    )
                )

        for fact, amount in (
            (
                FixedPublicStateFact.CONTROLLER_GRAVEYARD_DISTINCT_CARD_TYPES,
                4,
            ),
            (
                FixedPublicStateFact.CONTROLLER_NONLAND_PERMANENTS_ENTERED_THIS_TURN,
                2,
            ),
            (FixedPublicStateFact.CONTROLLER_HAND_ADVANTAGE, 1),
        ):
            with self.subTest(fact=fact):
                condition = FixedPublicStateConditionSpec(
                    FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
                    amount=amount,
                    fact=fact,
                    schema_version=3,
                )
                self.assertEqual(
                    condition,
                    FixedPublicStateConditionSpec.from_dict(
                        condition.to_dict()
                    ),
                )
                self.assertTrue(
                    condition.matches(
                        source_context(
                            condition_quantity=amount,
                        ).public_state
                    )
                )
                self.assertFalse(
                    condition.matches(
                        source_context(
                            condition_quantity=max(0, amount - 1),
                        ).public_state
                    )
                )

        with self.assertRaises(FixedPublicStateConditionError):
            FixedPublicStateConditionSpec(
                FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
                amount=1,
                fact=FixedPublicStateFact.CONTROLLER_HAND_ADVANTAGE,
                schema_version=2,
            )

    def test_fixed_public_condition_dependencies_fail_closed(self):
        registry_value = json.loads(
            (
                ROOT
                / "quorune"
                / "rules"
                / "capability-registry.json"
            ).read_text(encoding="utf-8")
        )
        base = self.db.lookup("Fresh-Faced Recruit")
        cases = (
            (
                "state_query.permanent.public_state_predicate",
                "As long as you control another multicolored permanent, "
                "this creature gets +1/+1.",
            ),
            (
                "state_query.permanent.public_state_predicate",
                "Delirium — This creature gets +2/+2 as long as there are "
                "four or more card types among cards in your graveyard.",
            ),
            (
                "variant.monarch.designate",
                "As long as you're the monarch, this creature gets +1/+1.",
            ),
        )
        for index, (blocked_id, text) in enumerate(cases):
            with self.subTest(blocked_id=blocked_id):
                mutated = copy.deepcopy(registry_value)
                blocked = next(
                    row
                    for row in mutated["capabilities"]
                    if row["id"] == blocked_id
                )
                blocked["status"] = "blocked"
                blocked["blockers"] = ["focused public-condition mutation"]
                record = replace(
                    base,
                    oracle_id=(
                        f"00000000-0000-4000-8000-{118_250_000 + index:012d}"
                    ),
                    name=f"Blocked Public Condition {index}",
                    type_line="Creature — Fixture",
                    oracle_text=text,
                    keywords=(),
                )
                ir = compile_oracle_card(
                    record,
                    capability_registry=CapabilityRegistry(mutated),
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)

        kindred_condition = fixed_public_state_characteristics_handler(
            "As long as enchanted permanent is a Goblin, it gets +1/+1.",
            source_name="Kindred Condition Fixture",
        )
        self.assertIsNotNone(kindred_condition)
        kindred_predicate = kindred_condition[1]["source_condition"]["predicate"]
        self.assertEqual([], kindred_predicate["types_all"])
        self.assertEqual(["goblin"], kindred_predicate["subtypes_any"])

        base = self.db.lookup("Fresh-Faced Recruit")
        conditional_indestructible = replace(
            base,
            oracle_id="00000000-0000-4000-8000-000011820005",
            name="Conditional Indestructible Standard",
            oracle_text=(
                "This creature has indestructible as long as it is tapped."
            ),
            keywords=(),
        )
        program = compile_card_program(
            self.db,
            conditional_indestructible,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        self.assertEqual((), program.residuals)
        descriptor = next(
            descriptor
            for ability in program.abilities
            for descriptor in ability.handlers
            if descriptor.get("handler_id")
            == FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID
        )
        self.assertIs(
            descriptor["source_condition"]["predicate"]["state_predicate"][
                "tapped"
            ],
            True,
        )
        self.assertEqual(
            ["Indestructible"], descriptor["modifier"]["add_abilities"]
        )
        self.assertIn(
            "permanent.indestructible.ordinary",
            program.capability_dependencies,
        )

        direct = {
            "Public State Attack Standard": (
                "continuous.characteristics.fixed-query-grant.v1",
                "attacking",
            ),
            "Public State Modification Standard": (
                "continuous.ability.fixed-query-keyword-grant.v1",
                "modified",
            ),
        }
        for name, (handler_id, state_field) in direct.items():
            with self.subTest(name=name):
                program = compile_card_program(
                    self.db,
                    self.db.lookup(name),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="trusted",
                )
                self.assertEqual((), program.residuals)
                descriptor = next(
                    descriptor
                    for ability in program.abilities
                    for descriptor in ability.handlers
                    if descriptor.get("handler_id") == handler_id
                )
                state = descriptor["condition"]["predicate"]["state_predicate"]
                self.assertIs(state[state_field], True)
                default_continuous_effect_component_registry().validate(
                    descriptor
                )

    def test_extended_state_predicates_preserve_legacy_serialization(self):
        legacy = PermanentStatePredicateSpec(
            counter_name="+1/+1",
            minimum_counter_count=1,
        )
        self.assertEqual(
            {
                "entered_this_turn",
                "tapped",
                "counter_name",
                "minimum_counter_count",
            },
            set(legacy.to_dict()),
        )
        current = ObjectQuerySpec(
            zones=("battlefield",),
            state_predicate=PermanentStatePredicateSpec(attacking=True),
        )
        restored = ObjectQuerySpec.from_dict(current.to_dict())
        self.assertEqual(current, restored)
        self.assertIs(restored.state_predicate.attacking, True)
        with self.assertRaises(FixedPublicStateConditionError):
            FixedPublicStateConditionSpec(
                FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY,
                predicate=current,
                schema_version=1,
            )

    def test_threshold_opponent_anthem_preserves_the_query_relation(self):
        base = self.db.lookup("Fresh-Faced Recruit")
        record = replace(
            base,
            oracle_id="00000000-0000-4000-8000-000011820004",
            oracle_text=(
                "Threshold — As long as there are seven or more cards in "
                "your graveyard, creatures your opponents control get -1/-0."
            ),
            keywords=(),
        )
        program = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )

        self.assertEqual((), program.residuals)
        descriptor = next(
            descriptor
            for ability in program.abilities
            for descriptor in ability.handlers
            if descriptor.get("handler_id")
            == FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID
        )
        self.assertEqual(
            "source_opponents", descriptor["target"]["target_controller"]
        )
        default_continuous_effect_component_registry().validate(descriptor)

    def test_opponent_graveyard_threshold_uses_the_shared_query_owner(self):
        base = self.db.lookup("Fresh-Faced Recruit")
        record = replace(
            base,
            oracle_id="00000000-0000-4000-8000-000011820005",
            oracle_text=(
                "As long as an opponent has eight or more cards in their "
                "graveyard, creatures you control have flying."
            ),
            keywords=(),
        )
        program = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )

        self.assertEqual((), program.residuals)
        descriptor = next(
            descriptor
            for ability in program.abilities
            for descriptor in ability.handlers
            if descriptor.get("handler_id")
            == FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID
        )
        condition = descriptor["source_condition"]
        self.assertEqual("query_count_at_least", condition["kind"])
        self.assertEqual(8, condition["amount"])
        self.assertEqual("opponent_zones", condition["quantity"]["scope"])
        self.assertEqual(
            ["graveyard"], condition["quantity"]["query"]["zones"]
        )
        default_continuous_effect_component_registry().validate(descriptor)

    def test_unrepresented_conditions_and_bodies_remain_residual(self):
        base = self.db.lookup("Fresh-Faced Recruit")
        unsupported = (
            "This creature gets +X/+X during your turn.",
            "During your turn, this creature has ward {X}.",
            "As long as this creature is untapped, other legendary creatures "
            "you control have ward {X}.",
            "During your turn, this creature has \"{T}: Draw a card.\" "
            "and has \"{T}: You gain 1 life.\"",
            "As long as this creature is equipped, it has ward {X}.",
            "Creatures you control have flying as long as you control an "
            "artifact with flying.",
            "Artifacts you control have shroud as long as you control three "
            "artifacts.",
            "Artifacts you control have shroud as long as an opponent "
            "controls two or more artifacts.",
            "As long as you control a creature with flying, this creature "
            "gets +1/+1.",
            "As long as this Equipment has four or more different kinds of "
            "counters on it, equipped creature has double strike.",
        )
        for index, text in enumerate(unsupported):
            with self.subTest(text=text):
                record = replace(
                    base,
                    oracle_id=f"00000000-0000-4000-8000-{118_200_000 + index:012d}",
                    oracle_text=text,
                    keywords=(),
                )
                program = compile_card_program(
                    self.db,
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="provisional",
                )
                self.assertTrue(program.residuals)
                self.assertFalse(
                    any(
                        descriptor.get("handler_id")
                        == FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID
                        for ability in program.abilities
                        for descriptor in ability.handlers
                    )
                )

    def test_one_quoted_activation_uses_the_conditional_grant_owner(self):
        base = self.db.lookup("Fresh-Faced Recruit")
        record = replace(base, oracle_id="00000000-0000-4000-8000-000011820099",
            oracle_text='During your turn, this creature has "{T}: Draw a card."',
            keywords=())
        program = compile_card_program(self.db, record,
            capability_registry=self.capabilities, capability_profile="commander_review",
            trust_level="trusted")
        self.assertEqual((), program.residuals)
        descriptor = next(descriptor for ability in program.abilities
            for descriptor in ability.handlers
            if descriptor.get("handler_id") == "continuous.characteristics.fixed-public-state.v2")
        self.assertEqual(2, descriptor["schema_version"])
        default_continuous_effect_component_registry().validate(descriptor)

    def test_public_state_descriptors_fail_closed_without_effects(self):
        compiled = fixed_public_state_characteristics_handler(
            "During your turn, this creature gets +2/+0 and has first strike.",
            source_name="Descriptor Fixture",
        )
        self.assertIsNotNone(compiled)
        descriptor = compiled[1]
        registry = default_continuous_effect_component_registry()
        registry.validate(descriptor)
        self.assertEqual(2, len(registry.lower(descriptor, source_context())))
        self.assertEqual(
            (),
            registry.lower(
                descriptor,
                source_context(active_player="B"),
            ),
        )

        malformed = []
        for path, value in (
            (("unknown",), True),
            (("schema_version",), True),
            (("source_condition", "kind"), "metalcraft"),
            (("source_condition", "amount"), 1),
            (("target", "kind"), "opponents"),
            (("target", "exclude_source"), 1),
            (("modifier", "add_abilities"), ["Ward {2}"]),
            (("modifier", "power"), True),
        ):
            candidate = copy.deepcopy(descriptor)
            target = candidate
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            malformed.append(candidate)
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                with self.assertRaises(SemanticNodeError):
                    registry.validate(candidate)

    def test_condition_and_target_mutants_change_card_program_fingerprint(self):
        record = self.db.lookup("Street Riot")
        expected = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="provisional",
        )
        original = fixed_public_state_characteristics_handler

        for mutation in ("condition", "target"):
            def mutated(text: str, *, source_name: str):
                compiled = original(text, source_name=source_name)
                if compiled is None:
                    return None
                template_id, descriptor, capabilities = compiled
                changed = copy.deepcopy(descriptor)
                if mutation == "condition":
                    changed["source_condition"]["kind"] = "other_turn"
                else:
                    changed["target"]["target_controller"] = "any"
                return template_id, changed, capabilities

            with self.subTest(mutation=mutation), mock.patch(
                "quorune.compiler.runtime_templates."
                "fixed_public_state_characteristics_handler",
                side_effect=mutated,
            ):
                changed = compile_card_program(
                    self.db,
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="provisional",
                )
                self.assertNotEqual(
                    expected.fingerprint,
                    changed.fingerprint,
                )


class FixedPublicStateCharacteristicRuntimeTests(unittest.TestCase):
    def test_original_channeler_condition_controls_required_attack_and_replays(self):
        from quorune.record import authoritative_state_hash
        from quorune.turn_step_owner import TURN_STEPS
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime

        session = self.session(361001)
        engine = session.engine
        record = self.db.lookup("Dragon's Rage Channeler")
        register_generated_programs(
            self.db, engine.semantics, (record,), trust_level='provisional',
            capability_registry=self.capabilities, capability_profile='commander_review',
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True,
        )
        source = CardInstance('public-state:channeler', 'delirium-channeler', record.oracle_id,
            record.name, 'A', 'A', 'battlefield', acquired_control_turn_count=-1,
            zone_timestamp=engine._next_zone_timestamp(), known_to=list(engine.seats), revealed_to=list(engine.seats))
        engine.state.cards[source.object_id] = source
        engine.state.players['A'].zones['battlefield'].append(source.object_id)
        program = compile_best_available_card_program(self.db, self.db.lookup("Dragon's Rage Channeler"), semantic_registry=engine.semantics, capability_registry=self.capabilities, capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program, capability_registry=self.capabilities, profile='commander_review')['strict_capability_ready'])
        # Four distinct printed types independently establish CR 205.2 and the
        # original Delirium condition; no descriptor or trust override is used.
        witnesses = ('Island', 'Sol Ring', 'Veil of Summer', 'Three Visits')
        cards = []
        for name in witnesses:
            record = self.db.lookup(name)
            card = CardInstance('delirium:' + name, 'delirium-' + name, record.oracle_id, name, 'A', 'A', 'graveyard', known_to=list(engine.seats), revealed_to=list(engine.seats))
            engine.state.cards[card.object_id] = card
            engine.state.players['A'].zones['graveyard'].append(card.object_id)
            cards.append(card)
        effective = engine._effective_card_data(source)
        self.assertEqual(('3', '3'), (str(effective['power']), str(effective['toughness'])))
        self.assertIn('Flying', effective['keywords'])
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.priority_player = None; engine.state.priority_passes = []
        engine.state.started = True; engine.state.active_player = 'A'
        engine.state.phase = 'combat'; engine.state.step = 'declare_attackers'
        engine.state.phase_index = TURN_STEPS.index(('combat', 'declare_attackers'))
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear(); session.decisions.clear()
        before = authoritative_state_hash(session.state)
        for principal, attackers in (('pilot:A', {}), ('pilot:C', {source.ref: 'B'})):
            denied = session.act(principal, {'action_id':'attack', 'attackers':attackers})
            self.assertFalse(denied.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id':'attack', 'attackers':{source.ref:'B'}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('B', session.state.cards[source.object_id].attacking)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        public = json.dumps(session.packet('pilot:D', full=True)['state'])
        self.assertIn(source.ref, public)
        self.assertNotIn(source.object_id, public)
        for seat in 'ABC':
            for object_id in session.state.players[seat].zones['hand']:
                self.assertNotIn(session.state.cards[object_id].ref, public)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay['ok'], replay)
        self.assertEqual(authoritative_state_hash(session.state), replay['final_state_hash'])
        # Losing one distinct type recomputes both layers; the attack declaration
        # has already happened and is not retroactively removed.
        engine.move_card(cards[-1].object_id, 'exile', log=False)
        current = engine.state.cards[source.object_id]
        effective = engine._effective_card_data(current)
        self.assertEqual(('1', '1'), (str(effective['power']), str(effective['toughness'])))
        self.assertNotIn('Flying', effective['keywords'])
        self.assertEqual('B', current.attacking)
        engine.state.combat.attackers.clear(); current.attacking = None; current.tapped = False
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.priority_player = None; engine.state.priority_passes = []
        engine.state.phase = 'combat'; engine.state.step = 'declare_attackers'
        engine.state.phase_index = TURN_STEPS.index(('combat', 'declare_attackers'))
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear(); session.decisions.clear()
        accepted = session.act('pilot:A', {'action_id':'attack', 'attackers':{}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(session.state.cards[source.object_id].attacking)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay['ok'], replay)

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        return session

    def add_source(
        self,
        session,
        *,
        name: str,
        ref: str,
        seat: str = "A",
        zone: str = "battlefield",
    ) -> CardInstance:
        engine = session.engine
        record = self.db.lookup(name)
        registration = register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertGreaterEqual(registration["runtime_handlers_promoted"], 1)
        card = CardInstance(
            object_id=f"public-state:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine._next_zone_timestamp(),
            entered_battlefield_turn_sequence=engine.state.turn_sequence,
            known_to=(
                [seat]
                if zone in {"hand", "library"}
                else list(engine.seats)
            ),
            revealed_to=(
                []
                if zone in {"hand", "library"}
                else list(engine.seats)
            ),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def add_constructed_condition_source(
        self,
        session,
        *,
        ref: str,
        texts: tuple[str, ...],
    ) -> CardInstance:
        engine = session.engine
        source = self.creature(
            engine,
            seat="A",
            name=f"Condition Source {ref}",
        )
        characteristics = (
            source.annotations.get("object_characteristics")
            or source.annotations["token_characteristics"]
        )
        fragments = characteristics.setdefault(
            "ability_fragments",
            [],
        )
        for index, text in enumerate(texts):
            compiled = fixed_public_state_characteristics_handler(
                text,
                source_name=source.printed_name,
            )
            self.assertIsNotNone(compiled)
            assert compiled is not None
            key = f"test:public-condition:{ref}:{index}"
            engine.semantics.put(
                SemanticProgram(
                    key=key,
                    label=f"Public condition {ref} {index}",
                    oracle_id=source.oracle_id,
                    ability_id=f"static:{key}",
                    active_zone="battlefield",
                    event="characteristics.evaluate",
                    handlers=[compiled[1]],
                    trust_level="provisional",
                )
            )
            fragments.append(
                ability_fragment_to_dict(StaticComponentSpec(key))
            )
        return source

    @staticmethod
    def resolve_top(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    @staticmethod
    def creature(
        engine,
        *,
        seat: str,
        name: str,
        subtype: str = "Minotaur",
        colors: tuple[str, ...] = (),
    ) -> CardInstance:
        ref = engine.create_token(
            seat,
            name=name,
            characteristics={
                "type_line": f"Token Creature — {subtype}",
                "power": "3",
                "toughness": "3",
                "keywords": [],
                "colors": list(colors),
            },
            reason="fixed public-state characteristic assurance",
        )[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

    @staticmethod
    def attach(source: CardInstance, target: CardInstance) -> None:
        source.attached_to = target.object_id
        if source.object_id not in target.attachments:
            target.attachments.append(source.object_id)

    def test_live_public_object_state_and_layer_five_queries_recompute(self):
        session = self.session(118_220_004)
        engine = session.engine
        self.add_source(
            session,
            name="Public State Attack Standard",
            ref="ATTACK-STANDARD",
        )
        self.add_source(
            session,
            name="Public State Modification Standard",
            ref="MODIFIED-STANDARD",
        )
        metalcraft = self.add_source(
            session,
            name="Public State Metalcraft Standard",
            ref="METALCRAFT-STANDARD",
        )
        target = self.creature(
            engine,
            seat="A",
            name="Public State Target",
        )

        baseline = engine._effective_card_data(target)
        self.assertEqual("3", baseline["power"])
        self.assertNotIn("Lifelink", baseline["keywords"])
        self.assertNotIn("Trample", baseline["keywords"])

        target.attacking = "B"
        attacking = engine._effective_card_data(target)
        self.assertEqual("4", attacking["power"])
        self.assertIn("Lifelink", attacking["keywords"])
        target.attacking = None

        target.counters["stun"] = 1
        self.assertIn(
            "Trample",
            engine._effective_card_data(target)["keywords"],
        )
        target.counters.clear()

        opposing_aura = self.add_source(
            session,
            name="Aboshan's Desire",
            ref="OPPOSING-AURA",
            seat="B",
        )
        self.attach(opposing_aura, target)
        aura_state = engine._public_object_query_result(target)
        self.assertTrue(aura_state.enchanted)
        self.assertFalse(aura_state.modified)
        self.assertNotIn(
            "Trample",
            engine._effective_card_data(target)["keywords"],
        )
        engine.change_control(
            opposing_aura.object_id,
            "A",
            reason="modified-state controller witness",
        )
        controlled_aura_state = engine._public_object_query_result(target)
        self.assertTrue(controlled_aura_state.enchanted)
        self.assertTrue(controlled_aura_state.modified)
        self.assertIn(
            "Trample",
            engine._effective_card_data(target)["keywords"],
        )

        conditioned = self.add_source(
            session,
            name="Public State Equipment Standard",
            ref="EQUIPPED-STANDARD",
        )
        equipment = self.add_source(
            session,
            name="Javelin of Lightning",
            ref="STATE-EQUIPMENT",
            seat="B",
        )
        self.attach(equipment, conditioned)
        equipment_state = engine._public_object_query_result(conditioned)
        self.assertTrue(equipment_state.equipped)
        self.assertTrue(equipment_state.modified)
        equipped = engine._effective_card_data(conditioned)
        self.assertEqual("3", equipped["power"])
        self.assertEqual("3", equipped["toughness"])
        self.assertIn("Flying", equipped["keywords"])
        equipment.attached_to = None
        conditioned.attachments.remove(equipment.object_id)
        unequipped = engine._effective_card_data(conditioned)
        self.assertEqual("2", unequipped["power"])
        self.assertNotIn("Flying", unequipped["keywords"])

        attachment = self.add_source(
            session,
            name="Public State Attachment Standard",
            ref="ATTACHMENT-STANDARD",
        )
        black_target = self.creature(
            engine,
            seat="A",
            name="Black Attachment Target",
            colors=("B",),
        )
        self.attach(attachment, black_target)
        black = engine._effective_card_data(black_target)
        self.assertEqual("4", black["power"])
        self.assertIn("Wither", black["keywords"])
        black_target.annotations["copy_overrides"]["colors"] = ["U"]
        blue = engine._effective_card_data(black_target)
        self.assertEqual("3", blue["power"])
        self.assertNotIn("Wither", blue["keywords"])

        artifact_refs = engine.create_token(
            "A",
            name="Public Artifact",
            quantity=2,
            characteristics={
                "type_line": "Token Artifact",
                "keywords": [],
            },
            reason="public-state count witness",
        )
        artifact = engine._resolve_object(
            "A",
            artifact_refs[0],
            zones={"battlefield"},
        )
        type_changed = self.creature(
            engine,
            seat="A",
            name="Layer Five Type Witness",
        )
        self.assertNotIn(
            "Shroud",
            engine._effective_card_data(artifact)["keywords"],
        )
        type_changed.annotations["continuous_add_types"] = ["Artifact"]
        self.assertIn(
            "Shroud",
            engine._effective_card_data(artifact)["keywords"],
        )
        type_changed.annotations["continuous_add_types"] = []
        self.assertNotIn(
            "Shroud",
            engine._effective_card_data(artifact)["keywords"],
        )

        type_changed.annotations["continuous_add_types"] = ["Artifact"]
        engine.move_card(metalcraft.object_id, "graveyard", log=False)
        self.assertNotIn(
            "Shroud",
            engine._effective_card_data(artifact)["keywords"],
        )

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.started = True
        engine._grant_priority("D")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:D",
            {
                "action_id": "concede",
                "choices": {"confirm_concede": True},
                "plan": "REPLAY_TYPED_PUBLIC_STATE_CHARACTERISTICS",
                "reason": "Verify typed public-state queries from checkpoint.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        with tempfile.TemporaryDirectory() as directory:
            record_dir = Path(directory) / "public-state-query-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_opponent_graveyard_threshold_revalidates_by_seat_relation(self):
        session = self.session(118_220_010)
        engine = session.engine
        source = self.add_constructed_condition_source(
            session,
            ref="OPPONENT-GRAVEYARD",
            texts=(
                "As long as an opponent has eight or more cards in their "
                "graveyard, creatures you control have flying.",
            ),
        )

        for seat, count in (("A", 8), ("B", 7)):
            player = engine.state.players[seat]
            while len(player.zones["graveyard"]) < count:
                engine.move_card(
                    player.zones["library"][-1],
                    "graveyard",
                    log=False,
                )

        def keywords() -> set[str]:
            with mock.patch.object(
                CommanderEngine,
                "semantic_program_is_current_trusted",
                return_value=True,
            ):
                return set(engine._effective_card_data(source)["keywords"])

        self.assertNotIn("Flying", keywords())
        opponent = engine.state.players["B"]
        engine.move_card(opponent.zones["library"][-1], "graveyard", log=False)
        self.assertIn("Flying", keywords())
        engine.move_card(opponent.zones["graveyard"][-1], "exile", log=False)
        self.assertNotIn("Flying", keywords())

    def test_public_condition_queries_follow_authoritative_history_and_control(self):
        session = self.session(118_220_007)
        engine = session.engine
        source = self.add_constructed_condition_source(
            session,
            ref="HISTORY-QUERY",
            texts=(
                "This creature gets +2/+0 as long as you've drawn two or "
                "more cards this turn.",
                "This creature gets +0/+2 as long as you've cast two or "
                "more noncreature spells this turn.",
                "As long as you control another multicolored permanent, "
                "this creature gets +1/+1 and has vigilance.",
            ),
        )
        status_source = self.add_constructed_condition_source(
            session,
            ref="PLAYER-STATUS",
            texts=(
                "This creature gets +1/+0 as long as an opponent has three "
                "or more poison counters.",
                "This creature gets +0/+1 as long as you're the monarch.",
            ),
        )
        absence_source = self.add_constructed_condition_source(
            session,
            ref="ABSENCE-QUERY",
            texts=(
                "This creature gets +1/+1 as long as you control no untapped "
                "lands.",
            ),
        )

        def stats() -> tuple[int, int, set[str]]:
            with mock.patch.object(
                CommanderEngine,
                "semantic_program_is_current_trusted",
                return_value=True,
            ):
                current = engine._effective_card_data(source)
            return (
                int(current["power"]),
                int(current["toughness"]),
                set(current["keywords"]),
            )

        self.assertEqual((3, 3, set()), stats())
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            absent = engine._effective_card_data(absence_source)
        self.assertEqual((4, 4), (int(absent["power"]), int(absent["toughness"])))
        land_ref = engine.create_token(
            "A",
            name="Condition Land",
            characteristics={"type_line": "Token Land — Forest"},
            reason="public condition tapped-state quantity",
        )[0]
        land = engine._resolve_object("A", land_ref, zones={"battlefield"})
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            present = engine._effective_card_data(absence_source)
        self.assertEqual(
            (3, 3),
            (int(present["power"]), int(present["toughness"])),
        )
        land.tapped = True
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            tapped = engine._effective_card_data(absence_source)
        self.assertEqual((4, 4), (int(tapped["power"]), int(tapped["toughness"])))
        engine.state.players["B"].poison = 3
        engine.become_monarch("A", reason="condition status fixture")
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            status = engine._effective_card_data(status_source)
        self.assertEqual((4, 4), (int(status["power"]), int(status["toughness"])))
        engine.state.players["B"].poison = 0
        engine.become_monarch("B", reason="condition status fixture")
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            inactive_status = engine._effective_card_data(status_source)
        self.assertEqual(
            (3, 3),
            (int(inactive_status["power"]), int(inactive_status["toughness"])),
        )
        multicolor = self.creature(
            engine,
            seat="A",
            name="Multicolor Condition Witness",
            colors=("R", "W"),
        )
        self.assertEqual((4, 4, {"Vigilance"}), stats())

        engine.apply_effect(
            {"op": "draw", "player": "A", "count": 2},
            actor="A",
        )
        self.assertEqual((6, 4, {"Vigilance"}), stats())
        engine._record_turn_history("spell_cast", actor="A", types=("creature",))
        self.assertEqual((6, 4, {"Vigilance"}), stats())
        engine._record_turn_history("spell_cast", actor="A", types=("instant",))
        engine._record_turn_history("spell_cast", actor="A", types=("sorcery",))
        self.assertEqual((6, 6, {"Vigilance"}), stats())

        projected = session.projector._snapshot("pilot:B")
        rendered = json.dumps(projected, sort_keys=True)
        for object_id in engine.state.players["A"].zones["hand"]:
            hidden = engine.state.cards[object_id]
            self.assertNotIn(hidden.object_id, rendered)
            self.assertNotIn(hidden.ref, rendered)

        engine.change_control(source.object_id, "B", reason="condition owner")
        self.assertEqual((3, 3, set()), stats())
        engine.change_control(source.object_id, "A", reason="condition owner")
        source.annotations["token_characteristics"]["colors"] = ["R", "W"]
        engine.move_card(multicolor.object_id, "graveyard", log=False)
        self.assertEqual((5, 5, set()), stats())

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine._grant_priority("D")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:D",
            {
                "action_id": "concede",
                "choices": {"confirm_concede": True},
                "plan": "REPLAY_PUBLIC_CONDITION_QUERIES",
                "reason": "Verify public condition facts from checkpoint.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        with tempfile.TemporaryDirectory() as directory:
            record_dir = Path(directory) / "public-condition-query-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_public_fact_thresholds_use_canonical_producers_and_consumers(self):
        session = self.session(118_220_021)
        engine = session.engine
        source = self.creature(
            engine,
            seat="A",
            name="Public Threshold Consumer",
        )
        engine.state.turn_history.events.clear()

        entry_condition = fixed_public_fact_condition(
            "two or more nonland permanents entered the battlefield under "
            "your control this turn",
            source_name=source.printed_name,
        )
        death_condition = fixed_public_fact_condition(
            "two or more creatures died under your control this turn",
            source_name=source.printed_name,
        )
        self.assertIsNotNone(entry_condition)
        self.assertIsNotNone(death_condition)
        assert entry_condition is not None and death_condition is not None

        def holds(condition: FixedPublicStateConditionSpec) -> bool:
            return engine._fixed_public_state_condition_holds(
                source,
                condition,
            )

        self.assertFalse(holds(entry_condition))
        engine.create_token(
            "A",
            name="Threshold Land",
            characteristics={"type_line": "Token Land — Gate"},
            reason="public threshold land control",
        )
        self.assertFalse(holds(entry_condition))
        first_entry_ref = engine.create_token(
            "A",
            name="Threshold Artifact",
            characteristics={"type_line": "Token Artifact"},
            reason="public threshold first nonland",
        )[0]
        self.assertFalse(holds(entry_condition))
        first_entry = engine._resolve_object(
            "A",
            first_entry_ref,
            zones={"battlefield"},
        )
        engine.move_card(first_entry.object_id, "graveyard", log=False)
        second_entries = engine.create_token(
            "A",
            name="Threshold Pair",
            quantity=2,
            characteristics={"type_line": "Token Enchantment"},
            reason="public threshold simultaneous nonlands",
        )
        self.assertTrue(holds(entry_condition))
        second_entry = engine._resolve_object(
            "A",
            second_entries[0],
            zones={"battlefield"},
        )
        engine.change_control(
            second_entry.object_id,
            "B",
            reason="public threshold later control change",
        )
        self.assertTrue(holds(entry_condition))

        advance_fixture_turn(engine)
        self.assertFalse(holds(entry_condition))
        victims = [
            self.creature(
                engine,
                seat="A",
                name=f"Death Threshold Victim {index}",
            )
            for index in range(3)
        ]
        opposing_victim = self.creature(
            engine,
            seat="A",
            name="Death Threshold Opposing Controller",
        )
        engine.state.turn_history.events.clear()
        engine.change_control(
            opposing_victim.object_id,
            "B",
            reason="death threshold previous controller",
        )
        engine.move_card(
            opposing_victim.object_id,
            "graveyard",
            semantic_events=True,
        )
        self.assertFalse(holds(death_condition))
        engine.move_card(
            victims[0].object_id,
            "graveyard",
            semantic_events=True,
        )
        self.assertFalse(holds(death_condition))
        engine.move_card(
            victims[1].object_id,
            "graveyard",
            semantic_events=True,
        )
        self.assertTrue(holds(death_condition))
        engine.move_card(
            victims[2].object_id,
            "graveyard",
            semantic_events=True,
        )
        self.assertTrue(holds(death_condition))

    def test_subtype_attack_threshold_counts_distinct_event_time_objects(self):
        session = self.session(118_220_022)
        engine = session.engine
        source = self.creature(
            engine,
            seat="A",
            name="Subtype Attack Consumer",
        )
        first = self.creature(
            engine,
            seat="A",
            name="First Goblin Attacker",
            subtype="Goblin",
        )
        second = self.creature(
            engine,
            seat="A",
            name="Second Goblin Attacker",
            subtype="Goblin",
        )
        nonmatching = self.creature(
            engine,
            seat="A",
            name="Elf Attacker",
            subtype="Elf",
        )
        engine.state.turn_history.events.clear()
        condition = FixedPublicStateConditionSpec(
            FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
            amount=2,
            fact=(
                FixedPublicStateFact
                .CONTROLLER_ATTACKED_WITH_SUBTYPE_THIS_TURN
            ),
            fact_parameter="goblin",
            schema_version=4,
        )
        historical_condition = FixedPublicStateConditionSpec(
            FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
            amount=2,
            fact=(
                FixedPublicStateFact
                .CONTROLLER_ATTACKED_WITH_SUBTYPE_THIS_TURN
            ),
            fact_parameter="goblin",
            schema_version=3,
        )
        self.assertEqual(
            historical_condition,
            FixedPublicStateConditionSpec.from_dict(
                historical_condition.to_dict()
            ),
        )

        def holds() -> bool:
            return engine._fixed_public_state_condition_holds(
                source,
                condition,
            )

        record_creature_attack_history(
            engine,
            first,
            {"target": "B", "kind": "player"},
        )
        record_creature_attack_history(
            engine,
            first,
            {"target": "C", "kind": "player"},
        )
        self.assertTrue(
            engine._fixed_public_state_condition_holds(
                source,
                historical_condition,
            )
        )
        self.assertFalse(holds())
        record_creature_attack_history(
            engine,
            nonmatching,
            {"target": "B", "kind": "player"},
        )
        self.assertFalse(holds())
        record_creature_attack_history(
            engine,
            second,
            {"target": "D", "kind": "player"},
        )
        self.assertTrue(holds())
        engine.change_control(
            first.object_id,
            "B",
            reason="attack threshold later control change",
        )
        first.annotations["token_characteristics"]["type_line"] = (
            "Token Creature — Elf"
        )
        self.assertTrue(holds())
        advance_fixture_turn(engine)
        self.assertFalse(holds())

    def test_public_fact_conditions_recompute_and_replay(self):
        session = self.session(118_220_011)
        engine = session.engine
        source = self.add_constructed_condition_source(
            session,
            ref="PUBLIC-FACTS",
            texts=(
                "Delirium — This creature gets +2/+2 as long as there are "
                "four or more card types among cards in your graveyard.",
                "This creature gets +1/+0 as long as you gained life this turn.",
                "Celebration — This creature gets +1/+1 and has trample as "
                "long as two or more nonland permanents entered the "
                "battlefield under your control this turn.",
            ),
        )
        engine.state.turn_history.events.clear()
        def characteristics(card: CardInstance):
            with mock.patch.object(
                CommanderEngine,
                "semantic_program_is_current_trusted",
                return_value=True,
            ):
                return engine._effective_card_data(card)

        baseline = characteristics(source)
        self.assertEqual((3, 3), (int(baseline["power"]), int(baseline["toughness"])))
        self.assertNotIn("Trample", baseline["keywords"])

        engine.apply_life_change_intent(
            LifeChangeIntent(
                actor="A",
                player="A",
                amount=1,
                reason="public-fact condition witness",
            )
        )
        life_active = characteristics(source)
        self.assertEqual(
            (4, 3),
            (int(life_active["power"]), int(life_active["toughness"])),
        )

        hand_source = self.add_constructed_condition_source(
            session,
            ref="HAND-ADVANTAGE",
            texts=(
                "This creature gets +0/+1 as long as you have more cards in "
                "hand than each opponent.",
            ),
        )
        self.assertEqual("3", characteristics(hand_source)["toughness"])
        opponent_max = max(
            len(engine.state.players[seat].zones["hand"])
            for seat in ("B", "C", "D")
        )
        while len(engine.state.players["A"].zones["hand"]) <= opponent_max:
            engine.move_card(
                engine.state.players["A"].zones["library"][-1],
                "hand",
                log=False,
            )
        self.assertEqual("4", characteristics(hand_source)["toughness"])

        life_source = self.add_constructed_condition_source(
            session,
            ref="STARTING-LIFE",
            texts=(
                "As long as your life total is less than or equal to half "
                "your starting life total, this creature has indestructible.",
            ),
        )
        self.assertNotIn("Indestructible", characteristics(life_source)["keywords"])
        engine.apply_life_change_intent(
            LifeChangeIntent(
                actor="A",
                player="A",
                amount=(engine.state.config.starting_life // 2)
                - engine.state.players["A"].life,
                reason="starting-life condition witness",
            )
        )
        self.assertIn("Indestructible", characteristics(life_source)["keywords"])

        planeswalker_source = self.add_constructed_condition_source(
            session,
            ref="PLANESWALKER-SUBTYPE",
            texts=(
                "As long as you control a Nissa planeswalker, this creature "
                "gets +2/+0 and has vigilance.",
            ),
        )
        self.assertEqual("3", characteristics(planeswalker_source)["power"])
        engine.create_token(
            "A",
            name="Nissa Condition Witness",
            characteristics={
                "type_line": "Token Legendary Planeswalker — Nissa",
                "loyalty": "4",
                "keywords": [],
            },
            reason="planeswalker subtype condition witness",
        )
        planeswalker_active = characteristics(planeswalker_source)
        self.assertEqual("5", planeswalker_active["power"])
        self.assertIn("Vigilance", planeswalker_active["keywords"])

        attack_source = self.add_constructed_condition_source(
            session,
            ref="ATTACKED-BATTLE",
            texts=(
                "This creature has indestructible as long as it attacked a "
                "battle this turn.",
            ),
        )
        self.creature(engine, seat="A", name="Celebration Witness")
        normal_entry = next(
            engine.state.cards[object_id]
            for object_id in engine.state.players["A"].zones["library"]
            if (
                (record := engine.card_record(engine.state.cards[object_id]))
                is not None
                and record.is_creature
            )
        )
        engine.move_card(
            normal_entry.object_id,
            "battlefield",
            log=False,
            semantic_events=True,
        )
        self.assertIn(
            normal_entry.logical_object_id,
            {
                event.object_incarnation
                for event in engine._current_turn_history("permanent_entered")
            },
        )
        celebration = characteristics(source)
        self.assertEqual(
            (5, 4),
            (int(celebration["power"]), int(celebration["toughness"])),
        )
        self.assertIn("Trample", celebration["keywords"])

        distinct_types: set[str] = set()
        for object_id in tuple(engine.state.players["A"].zones["library"]):
            card = engine.state.cards[object_id]
            row = engine._public_object_query_result(card)
            if set(row.types) <= distinct_types:
                continue
            engine.move_card(card.object_id, "graveyard", log=False)
            distinct_types.update(row.types)
            if len(distinct_types) >= 4:
                break
        self.assertGreaterEqual(len(distinct_types), 4)
        delirium = characteristics(source)
        self.assertEqual(
            (7, 6),
            (int(delirium["power"]), int(delirium["toughness"])),
        )

        self.assertNotIn("Indestructible", characteristics(attack_source)["keywords"])
        engine._record_turn_history(
            "creature_attacked",
            actor="A",
            object_incarnation=attack_source.logical_object_id,
            target="BATTLE-FACT",
            target_kind="battle",
            types=("creature", "fixture"),
        )
        self.assertIn("Indestructible", characteristics(attack_source)["keywords"])

        with mock.patch(
            "quorune.card_programs.runtime._FixedPublicStateSnapshotResolver."
            "_public_fact_quantity",
            return_value=0,
        ):
            mutated = characteristics(source)
        self.assertEqual(
            (3, 3),
            (int(mutated["power"]), int(mutated["toughness"])),
        )
        self.assertNotIn("Trample", mutated["keywords"])

        projected = json.dumps(session.packet("pilot:B", full=True))
        for object_id in engine.state.players["A"].zones["hand"]:
            hidden = engine.state.cards[object_id]
            self.assertNotIn(hidden.object_id, projected)
            self.assertNotIn(hidden.ref, projected)

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine._grant_priority("D")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:D",
            {
                "action_id": "concede",
                "choices": {"confirm_concede": True},
                "plan": "REPLAY_PUBLIC_FACT_CONDITIONS",
                "reason": "Verify sealed public facts from checkpoint.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        with tempfile.TemporaryDirectory() as directory:
            record_dir = Path(directory) / "public-fact-condition-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_unrelated_fixed_conditions_do_not_scan_turn_history(self):
        session = self.session(118_220_008)
        engine = session.engine
        source = self.add_constructed_condition_source(
            session,
            ref="NO-HISTORY-SCAN",
            texts=("During your turn, this creature gets +2/+0.",),
        )
        engine.state.active_player = "A"

        with (
            mock.patch.object(
                CommanderEngine,
                "semantic_program_is_current_trusted",
                return_value=True,
            ),
            mock.patch(
                "quorune.card_programs.runtime.drawn_this_turn",
                side_effect=AssertionError("unexpected draw-history scan"),
            ),
            mock.patch(
                "quorune.card_programs.runtime.current_turn_history_events",
                side_effect=AssertionError("unexpected cast-history scan"),
            ),
        ):
            current = engine._effective_card_data(source)

        self.assertEqual(
            (5, 3),
            (int(current["power"]), int(current["toughness"])),
        )

    def test_opponent_color_existence_uses_live_public_query(self):
        session = self.session(118_220_009)
        engine = session.engine
        source = self.add_constructed_condition_source(
            session,
            ref="OPPONENT-COLOR",
            texts=(
                "This creature gets +2/+2 as long as an opponent controls "
                "a black permanent.",
            ),
        )

        def stats() -> tuple[int, int]:
            with mock.patch.object(
                CommanderEngine,
                "semantic_program_is_current_trusted",
                return_value=True,
            ):
                current = engine._effective_card_data(source)
            return int(current["power"]), int(current["toughness"])

        self.assertEqual((3, 3), stats())
        black_permanent = self.creature(
            engine,
            seat="B",
            name="Opponent Color Witness",
            colors=("B",),
        )
        self.assertEqual((5, 5), stats())
        engine.change_control(
            black_permanent.object_id,
            "A",
            reason="opponent public-query relation witness",
        )
        self.assertEqual((3, 3), stats())

    def test_public_state_conditions_recompute_layer_six_and_seven_results(self):
        registry = default_continuous_effect_component_registry()
        descriptor = fixed_public_state_characteristics_handler(
            "During your turn, this creature gets +2/+1 and has first strike.",
            source_name="State Source",
        )[1]
        effects = registry.lower(descriptor, source_context(active_player="A"))
        self.assertEqual([Layer.ABILITY, Layer.POWER_TOUGHNESS], [
            effect.layer for effect in effects
        ])
        source = evaluate_continuous_effects(
            CharacteristicState(
                name="State Source",
                controller="A",
                card_types={"Creature"},
                power=2,
                toughness=2,
            ),
            effects,
            context={
                "object_id": "public-state-source",
                "logical_object_id": "public-state-source@1",
                "ref": "PUBLIC-STATE-SOURCE",
                "owner": "A",
                "controller": "A",
                "zone": "battlefield",
            },
        ).characteristics
        self.assertEqual(4, source["power"])
        self.assertEqual(3, source["toughness"])
        self.assertIn("First Strike", source["abilities"])

        unrelated = evaluate_continuous_effects(
            CharacteristicState(
                name="Other Creature",
                controller="A",
                card_types={"Creature"},
                power=2,
                toughness=2,
            ),
            effects,
            context={
                "object_id": "other",
                "logical_object_id": "other@1",
                "ref": "OTHER",
                "owner": "A",
                "controller": "A",
                "zone": "battlefield",
            },
        ).characteristics
        self.assertEqual(2, unrelated["power"])
        self.assertNotIn("First Strike", unrelated["abilities"])
        stale_source = evaluate_continuous_effects(
            CharacteristicState(
                name="State Source",
                controller="A",
                card_types={"Creature"},
                power=2,
                toughness=2,
            ),
            effects,
            context={
                "object_id": "public-state-source",
                "logical_object_id": "public-state-source@2",
                "ref": "PUBLIC-STATE-SOURCE",
                "owner": "A",
                "controller": "A",
                "zone": "battlefield",
            },
        ).characteristics
        self.assertEqual(2, stale_source["power"])
        self.assertNotIn("First Strike", stale_source["abilities"])
        self.assertEqual(
            (),
            registry.lower(descriptor, source_context(active_player="B")),
        )

        threshold = fixed_public_state_characteristics_handler(
            "Threshold — This creature gets +7/+7 as long as there are seven "
            "or more cards in your graveyard.",
            source_name="Threshold Source",
        )[1]
        self.assertEqual(
            (),
            registry.lower(threshold, source_context(graveyard_count=6)),
        )
        self.assertEqual(
            1,
            len(registry.lower(threshold, source_context(graveyard_count=7))),
        )

        counter = fixed_public_state_characteristics_handler(
            "This creature has trample as long as it has a +1/+1 counter on it.",
            source_name="Counter Source",
        )[1]
        self.assertEqual(
            (),
            registry.lower(counter, source_context(counters=())),
        )
        self.assertEqual(
            1,
            len(
                registry.lower(
                    counter,
                    source_context(counters=(("+1/+1", 1),)),
                )
            ),
        )

        attached_pronoun = fixed_public_state_characteristics_handler(
            "Enchanted creature has shroud as long as it's untapped.",
            source_name="Attached Pronoun Source",
        )[1]
        attached_condition = FixedPublicStateConditionSpec.from_dict(
            attached_pronoun["source_condition"]
        )
        self.assertFalse(
            attached_condition.matches(
                source_context(attached_query_matches=False).public_state
            )
        )
        self.assertTrue(
            attached_condition.matches(
                source_context(attached_query_matches=True).public_state
            )
        )

        opponent_anthem = fixed_public_state_characteristics_handler(
            "Threshold — As long as there are seven or more cards in your "
            "graveyard, creatures your opponents control get -1/-0.",
            source_name="Threshold Opponent Anthem",
        )[1]
        opponent_effects = registry.lower(
            opponent_anthem,
            source_context(graveyard_count=7),
        )

        def opponent_power(controller: str) -> int:
            return int(
                evaluate_continuous_effects(
                    CharacteristicState(
                        name=f"{controller} relation witness",
                        controller=controller,
                        card_types={"Creature"},
                        power=3,
                        toughness=3,
                    ),
                    opponent_effects,
                    context={
                        "object_id": f"{controller}-witness",
                        "logical_object_id": f"{controller}-witness@1",
                        "ref": f"{controller}-WITNESS",
                        "owner": controller,
                        "controller": controller,
                        "zone": "battlefield",
                    },
                ).characteristics["power"]
            )

        self.assertEqual(3, opponent_power("A"))
        self.assertEqual(2, opponent_power("B"))
        self.assertEqual(2, opponent_power("C"))

    def test_turn_gated_attachment_and_anthem_compose(self):
        session = self.session(118_220_001)
        engine = session.engine
        riot = self.add_source(session, name="Street Riot", ref="RIOT")
        javelin = self.add_source(
            session,
            name="Javelin of Lightning",
            ref="JAVELIN",
        )
        desire = self.add_source(
            session,
            name="Aboshan's Desire",
            ref="DESIRE",
            zone="hand",
        )
        target = self.creature(engine, seat="A", name="Conditional Target")
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        engine.state.players["A"].mana_pool["C"] = 4
        engine._activate(
            "A",
            {
                "source": javelin.ref,
                "ability": "ab4",
                "targets": [target.ref],
            },
        )
        self.assertEqual("builtin:equip", engine.state.stack[-1].semantic_key)
        self.resolve_top(engine)
        self.assertEqual(target.object_id, javelin.attached_to)

        engine.state.players["A"].mana_pool["U"] = 1
        engine._cast(
            "A",
            {
                "card": desire.ref,
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"U": 1},
            },
        )
        self.resolve_top(engine)
        self.assertEqual(target.object_id, desire.attached_to)

        active = engine._effective_card_data(target)
        self.assertEqual("6", active["power"])
        self.assertIn("First Strike", active["keywords"])
        self.assertIn("Trample", active["keywords"])
        self.assertIn("Flying", active["keywords"])

        threshold_cards = list(
            engine.state.players["A"].zones["library"][-7:]
        )
        for object_id in threshold_cards:
            engine.move_card(object_id, "graveyard", log=False)
        self.assertIn(
            "Shroud",
            engine._effective_card_data(target)["keywords"],
        )
        for object_id in threshold_cards:
            engine.move_card(object_id, "library", log=False)

        engine.state.active_player = "B"
        inactive = engine._effective_card_data(target)
        self.assertEqual("3", inactive["power"])
        self.assertNotIn("First Strike", inactive["keywords"])
        self.assertNotIn("Trample", inactive["keywords"])
        self.assertIn("Flying", inactive["keywords"])
        self.assertNotIn("Shroud", inactive["keywords"])

        engine.change_control(
            riot.object_id,
            "B",
            reason="public-state controller boundary witness",
        )
        engine.change_control(
            target.object_id,
            "B",
            reason="public-state target boundary witness",
        )
        changed = engine._effective_card_data(target)
        self.assertEqual("4", changed["power"])
        self.assertIn("Trample", changed["keywords"])
        self.assertNotIn("First Strike", changed["keywords"])

        engine.move_card(riot.object_id, "graveyard", log=False)
        departed = engine._effective_card_data(target)
        self.assertEqual("3", departed["power"])
        self.assertNotIn("Trample", departed["keywords"])

    def test_typed_aura_and_public_state_characteristics_compose(self):
        session = self.session(118_220_006)
        engine = session.engine
        aura = self.add_source(
            session,
            name="Public State Attachment Standard",
            ref="TYPED-PUBLIC-STATE-AURA",
            zone="hand",
        )
        target = self.creature(
            engine,
            seat="A",
            name="Typed Aura Public-State Target",
            colors=("B",),
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        engine.state.players["A"].mana_pool.update({"B": 1, "C": 1})

        engine._cast(
            "A",
            {
                "card": aura.ref,
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"B": 1, "C": 1},
            },
        )
        self.resolve_top(engine)

        self.assertEqual(target.object_id, aura.attached_to)
        self.assertTrue(engine._attachment_is_legal(aura, subtypes={"aura"}))
        enhanced = engine._effective_card_data(target)
        self.assertEqual("4", enhanced["power"])
        self.assertEqual("4", enhanced["toughness"])
        self.assertIn("Wither", enhanced["keywords"])

        target.annotations["copy_overrides"] = {
            "type_line": "Token Creature — Wall"
        }
        self.assertFalse(
            engine._attachment_is_legal(aura, subtypes={"aura"})
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual("graveyard", aura.zone)
        self.assertNotIn(
            "Wither",
            engine._effective_card_data(target)["keywords"],
        )

    def test_public_state_characteristics_execute_while_replacement_siblings_fail_closed(self):
        session = self.session(118_220_003)
        engine = session.engine
        record = self.db.lookup("Angel of Vitality")
        program = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="provisional",
        )
        blockers = {
            blocker
            for residual in program.residuals
            for blocker in residual["blockers"]
        }
        self.assertGreaterEqual(
            blockers,
            {
                "replacement applicability",
                "self-replacement and prevention ordering",
            },
        )
        binding = bind_card_program_runtime(
            program,
            capability_registry=self.capabilities,
            profile="commander_review",
        )
        self.assertFalse(binding["strict_capability_ready"])
        self.assertFalse(binding["compatible_ready"])

        angel = self.add_source(
            session,
            name="Angel of Vitality",
            ref="ANGEL-OF-VITALITY",
        )
        engine.state.players["A"].life = 25
        active = engine._effective_card_data(angel)
        self.assertEqual("4", active["power"])
        self.assertEqual("4", active["toughness"])
        self.assertIn("Flying", active["keywords"])
        self.assertFalse(
            any(
                "replacement" in runtime_program.event
                and engine.semantic_program_is_current_trusted(runtime_program)
                for runtime_program in engine.semantics.programs_for_oracle(
                    angel.oracle_id
                )
            )
        )

    def test_hand_count_condition_projects_and_replays_without_hidden_identity(self):
        session = self.session(118_220_002)
        engine = session.engine
        self.add_source(session, name="Neheb, the Worthy", ref="NEHEB")
        target = self.creature(engine, seat="A", name="Private Count Target")
        hand = list(engine.state.players["A"].zones["hand"])
        for object_id in hand[1:]:
            engine.move_card(object_id, "library", log=False)
        hidden = engine.state.cards[hand[0]]
        engine.state.active_player = "A"

        self.assertEqual("5", engine._effective_card_data(target)["power"])
        projected = session.projector._snapshot("pilot:B")
        rendered = json.dumps(projected, sort_keys=True)
        self.assertNotIn(hidden.object_id, rendered)
        self.assertNotIn(hidden.ref, rendered)
        self.assertNotIn(hidden.printed_name, rendered)

        engine.move_card(
            engine.state.players["A"].zones["library"][-1],
            "hand",
            log=False,
        )
        self.assertEqual("3", engine._effective_card_data(target)["power"])
        engine.move_card(
            engine.state.players["A"].zones["hand"][-1],
            "library",
            log=False,
        )
        self.assertEqual("5", engine._effective_card_data(target)["power"])

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"a": "pass"})
            self.assertTrue(result.ok, result.summary)
        with tempfile.TemporaryDirectory() as directory:
            record_dir = Path(directory) / "public-state-characteristic-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)


class AdditionalConditionalModifierRuntimeTests(unittest.TestCase):
    def test_original_vivid_land_compound_entry_counter_price_and_actual_untap_replay(self):
        session=self.session(3633201);engine=session.engine
        land=self.add(engine,'Vivid Creek',zone='hand',ref='compound-vivid-land')
        spell=self.add(engine,'Essence Harvest',zone='hand',ref='compound-vivid-spell')
        self.assert_whole_admission(land.printed_name)
        setup=self.add(engine,'Generic Boolean Life Response',zone='hand',ref='compound-vivid-setup')
        self.ready(session,setup,{'C':2})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row['id'].startswith('play-land:') and row.get('card')==land.ref)
        self.checkpoint(session);before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id']}).ok);self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id']});self.assertTrue(accepted.ok,accepted.summary)
        current=session.state.cards[land.object_id]
        self.assertTrue(current.tapped);self.assertEqual(2,current.counters.get('charge',0))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('activate:'+land.ref+':') for row in actions))
        self.replay(session,load=True)
        for _ in range(200):
            if session.state.active_player=='A' and not session.state.cards[land.object_id].tapped:break
            decision=session.state.pending_decision
            if decision.kind=='priority':command={'action_id':'pass'}
            elif decision.kind=='combat.attackers':command={'action_id':'attack','attackers':{}}
            elif decision.kind=='cleanup.discard':
                cards=[session.state.cards[oid].ref for oid in session.state.players[decision.actors[0]].zones['hand'][:decision.payload_by_actor[decision.actors[0]]['count']]]
                command={'action_id':'discard','cards':cards}
            else:self.fail('UnexpectednativeVividturn '+decision.kind)
            accepted=session.act(session.pending_principals()[0],command);self.assertTrue(accepted.ok,accepted.summary)
        else:self.fail('Vividlandnativeuntapdidnotoccur')
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        ability=next(ability for ability in engine._activated_abilities(session.state.cards[land.object_id]) if ability.source_counter_removal_cost is not None)
        offered=next(row for row in actions if row['id']=='activate:'+land.ref+':'+ability.ability_id)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'mana_output':{'B':1}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(session.state.cards[land.object_id].tapped);self.assertEqual(1,session.state.cards[land.object_id].counters.get('charge',0))
        self.assertEqual(1,session.state.players['A'].mana_pool['B'])
        self.replay(session,load=True);self.replay(session)

    def test_original_heritage_and_dryad_selected_tap_mana_atomic_costs_and_replay(self):
        session=self.session(3633101);engine=session.engine
        source=self.add(engine,'Heritage Druid',zone='hand',ref='tap-mana-heritage')
        bodies=[self.add(engine,'Generic Selected Tap Elf',ref='tap-mana-elf-'+str(i)) for i in range(2)]
        foreign=self.add(engine,'Generic Selected Tap Elf',seat='B',ref='tap-mana-foreign-elf')
        green_spell=self.add(engine,'Generic Conditional Green Qualifier',zone='hand',ref='tap-mana-pay-green')
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{'G':1})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        while session.state.priority_player!='A':self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row['id'].startswith('activate:'+source.ref+':'))
        before=session.state.to_dict()
        for refs in ([source.ref,bodies[0].ref,bodies[0].ref],[source.ref,bodies[0].ref,foreign.ref],[source.ref,bodies[0].ref]):
            self.assertFalse(session.act('pilot:A',{'action_id':offered['id'],'cost_cards':refs}).ok);self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'cost_cards':[source.ref,*[body.ref for body in bodies]]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertFalse(session.state.stack)
        self.assertEqual(3,session.state.players['A'].mana_pool['G'])
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in (source,*bodies)))
        self.assertFalse(session.state.cards[foreign.object_id].tapped)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==green_spell.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session);self.replay(session)
        session=self.session(3633102);engine=session.engine
        source=self.add(engine,'Loam Dryad',ref='tap-mana-dryad')
        body=self.add(engine,'Generic Selected Tap Elf',ref='tap-mana-fresh-selected')
        fresh_source=self.add(engine,'Loam Dryad',ref='tap-mana-fresh-source')
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{})
        fresh_source.acquired_control_turn_count=session.state.players['A'].turns_begun
        engine.permissions.invalidate_current();session.state.pending_decision=None;engine._grant_priority('A');engine.pump()
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('activate:'+fresh_source.ref+':') for row in actions))
        offered=next(row for row in actions if row['id'].startswith('activate:'+source.ref+':'))
        self.checkpoint(session);before=session.state.to_dict()
        self.assertFalse(session.act('pilot:A',{'action_id':offered['id'],'cost_cards':[source.ref],'mana_output':{'G':1}}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'cost_cards':[body.ref],'mana_output':{'G':1}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped);self.assertTrue(session.state.cards[body.object_id].tapped)
        self.assertEqual(1,session.state.players['A'].mana_pool['G']);self.assertFalse(session.state.stack)
        self.replay(session,load=True)
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])

    def test_original_phyrexian_altar_selected_sacrifice_mana_pays_real_spell_and_replays(self):
        session=self.session(3633001);engine=session.engine
        source=self.add(engine,'Phyrexian Altar',zone='hand',ref='selected-mana-altar')
        fodder=self.add(engine,'Generic Bound Body',ref='selected-mana-own-body')
        foreign=self.add(engine,'Generic Bound Body',seat='B',ref='selected-mana-foreign-body')
        spell=self.add(engine,'Essence Harvest',zone='hand',ref='selected-mana-life-spell')
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{'C':5})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        while session.state.priority_player!='A':self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row['id'].startswith('activate:'+source.ref+':'))
        before=session.state.to_dict()
        for principal,cost in (('pilot:C',fodder.ref),('pilot:A',foreign.ref),('pilot:A',source.ref)):
            denied=session.act(principal,{'action_id':offered['id'],'cost_cards':[cost],'mana_output':{'B':1}})
            self.assertFalse(denied.ok);self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'cost_cards':[fodder.ref],'mana_output':{'B':1}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',session.state.cards[fodder.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[foreign.object_id].zone)
        self.assertEqual(1,session.state.players['A'].mana_pool['B']);self.assertFalse(session.state.stack)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==spell.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True);self.resolve(session)
        self.assertEqual(0,session.state.players['A'].mana_pool.get('B',0))
        self.assertEqual('graveyard',session.state.cards[spell.object_id].zone)
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_fangren_and_fumes_current_attacking_counter_sets_replay(self):
        from quorune.model import CombatState
        session=self.session(3632901);engine=session.engine
        source=self.add(engine,'Fangren Firstborn',ref='combat-counter-fangren')
        body=self.add(engine,'Generic Bound Body',ref='combat-counter-vigilant')
        spectator=self.add(engine,'Generic Bound Body',ref='combat-counter-spectator');spectator.tapped=True
        fumes=self.add(engine,'Choking Fumes',zone='hand',ref='combat-counter-fumes')
        self.assert_whole_admission(source.printed_name);self.assert_whole_admission(fumes.printed_name)
        self.ready(session,fumes,{'C':2,'W':1})
        session.state.pending_decision=None;session.state.priority_player=None;session.state.priority_passes=[]
        session.state.phase='combat';session.state.step='declare_attackers';session.state.phase_index=5;session.state.combat=CombatState()
        engine.permissions.invalidate_current();engine.pump();self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B',body.ref:'D'}})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True);self.resolve(session)
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual(1,session.state.cards[body.object_id].counters.get('+1/+1',0))
        self.assertEqual(0,session.state.cards[spectator.object_id].counters.get('+1/+1',0))
        while session.state.priority_player!='A':self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==fumes.ref)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':cast['id'],'pay':'auto'}).ok);self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session)
        # CR704.5q removes matching +1/+1 and -1/-1 counters from attackers.
        for card in (source,body):
            current=session.state.cards[card.object_id]
            self.assertEqual(0,current.counters.get('+1/+1',0));self.assertEqual(0,current.counters.get('-1/-1',0))
        self.assertEqual(0,session.state.cards[spectator.object_id].counters.get('-1/-1',0))
        self.assertFalse(session.state.cards[body.object_id].tapped)
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_source_damage_counter_survival_lethal_and_reentered_incarnation_replay(self):
        cases=((3632801,'Fungusaur',False,False),(3632802,'Siegehorn Ceratops',False,False),
            (3632803,'Siegehorn Ceratops',True,False),(3632804,'Siegehorn Ceratops',False,True))
        for seed,name,lethal,reenter in cases:
            with self.subTest(name=name,lethal=lethal,reenter=reenter):
                session=self.session(seed);engine=session.engine
                source=self.add(engine,name,ref='source-damage-counter-recipient')
                damage=self.add(engine,'Lightning Bolt' if lethal else 'Generic Source Counter One Damage',zone='hand',ref='source-damage-counter-damage')
                bounce=self.add(engine,'Unsummon',zone='hand',ref='source-damage-counter-bounce')
                self.assert_whole_admission(source.printed_name)
                offered=self.ready(session,damage,{'R':1,'U':1,'G':1,'W':1})
                self.checkpoint(session);identity=source.logical_object_id
                before=session.state.to_dict()
                self.assertFalse(session.act('pilot:D',{'action_id':offered['id'],'targets':[source.ref],'pay':'auto'}).ok)
                self.assertEqual(before,session.state.to_dict())
                accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':[source.ref],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary)
                for _ in range(8):
                    if any(item.kind=='triggered_ability' for item in session.state.stack):break
                    self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                self.assertTrue(any(item.kind=='triggered_ability' for item in session.state.stack))
                self.replay(session,load=True)
                if reenter:
                    while session.state.priority_player!='A':self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                    actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    offered=next(row for row in actions if row.get('card')==bounce.ref)
                    self.assertTrue(session.act('pilot:A',{'action_id':offered['id'],'targets':[source.ref],'pay':'auto'}).ok)
                    self.resolve(session)
                    while session.state.priority_player!='A':self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                    actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    offered=next(row for row in actions if row.get('card')==source.ref)
                    self.assertTrue(session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'}).ok)
                self.resolve(session)
                current=session.state.cards[source.object_id]
                self.assertEqual('graveyard' if lethal else 'battlefield',current.zone)
                self.assertEqual(0 if lethal or reenter else 1 if name=='Fungusaur' else 2,current.counters.get('+1/+1',0))
                if reenter:self.assertNotEqual(identity,current.logical_object_id)
                for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
                self.replay(session)

    def test_original_gleam_attacker_and_rakish_damage_source_counter_recipients_replay(self):
        from quorune.model import CombatState
        for seed,name in ((3632701,'Gleam of Battle'),(3632702,'Rakish Heir'),(3632703,'Necropolis Regent')):
            with self.subTest(name=name):
                session=self.session(seed);engine=session.engine
                observer=self.add(engine,name,ref='action-counter-observer')
                attacker=self.add(engine,'Generic Event Counter Vampire',ref='action-counter-attacker')
                control=self.add(engine,'Generic Bound Body',ref='action-counter-control')
                self.assert_whole_admission(observer.printed_name)
                engine.permissions.invalidate_current();session.state.pending_decision=None;session.state.started=True
                session.state.active_player='A';session.state.phase='combat';session.state.step='declare_attackers'
                session.state.phase_index=5;session.state.priority_player=None;session.state.priority_passes=[];session.state.combat=CombatState()
                engine.pump();self.checkpoint(session)
                before=session.state.to_dict()
                self.assertFalse(session.act('pilot:C',{'action_id':'attack','attackers':{attacker.ref:'B',control.ref:'D'}}).ok)
                self.assertEqual(before,session.state.to_dict())
                accepted=session.act('pilot:A',{'action_id':'attack','attackers':{attacker.ref:'B',control.ref:'D'}})
                self.assertTrue(accepted.ok,accepted.summary)
                checked=False
                for _ in range(70):
                    if session.state.phase=='postcombat_main' and not session.state.stack:break
                    decision=session.state.pending_decision
                    if decision.kind=='trigger.order':
                        pending=[item for batch in session.state.pending_trigger_batches for item in batch.items]
                        self.replay(session,load=True);checked=True
                        command={'action_id':'order','triggers':[item.ref for item in pending]}
                    elif decision.kind=='priority':
                        if any(item.kind=='triggered_ability' for item in session.state.stack) and not checked:
                            self.replay(session,load=True);checked=True
                        command={'action_id':'pass'}
                    elif decision.kind=='combat.blockers':command={'action_id':'block','blocks':{}}
                    else:self.fail('Unexpected eventcounter decision '+decision.kind)
                    accepted=session.act(session.pending_principals()[0],command);self.assertTrue(accepted.ok,accepted.summary)
                else:self.fail('Actioneventcountercombatdidnotfinish')
                self.assertTrue(checked)
                self.assertEqual(2 if name=='Necropolis Regent' else 1,session.state.cards[attacker.object_id].counters.get('+1/+1',0))
                self.assertEqual(2 if name=='Necropolis Regent' else 1 if name=='Gleam of Battle' else 0,session.state.cards[control.object_id].counters.get('+1/+1',0))
                self.assertEqual(0,session.state.cards[observer.object_id].counters.get('+1/+1',0))
                for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
                self.replay(session)

    def test_original_unicorn_counter_entry_recipient_and_pending_bounce_reentry_replay(self):
        for seed,reenter in ((3632601,False),(3632602,True)):
            with self.subTest(reenter=reenter):
                session=self.session(seed);engine=session.engine
                observer=self.add(engine,'Good-Fortune Unicorn',ref='entrycounter-unicorn')
                body=self.add(engine,'Generic Ordinary Grant Flash Sliver',zone='hand',ref='entrycounter-recipient')
                bounce=self.add(engine,'Unsummon',zone='hand',ref='entrycounter-bounce')
                self.assert_whole_admission(observer.printed_name)
                offered=self.ready(session,body,{'U':1})
                self.checkpoint(session)
                before=session.state.to_dict()
                self.assertFalse(session.act('pilot:B',{'action_id':offered['id'],'pay':'auto'}).ok);self.assertEqual(before,session.state.to_dict())
                accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
                for _ in range(8):
                    if any(item.kind=='triggered_ability' for item in session.state.stack):break
                    self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                self.assertTrue(any(item.kind=='triggered_ability' for item in session.state.stack))
                identity=session.state.cards[body.object_id].logical_object_id
                self.replay(session,load=True)
                if reenter:
                    while session.state.priority_player!='A':
                        self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                    actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    cast=next(row for row in actions if row.get('card')==bounce.ref)
                    accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[body.ref],'pay':'auto'})
                    self.assertTrue(accepted.ok,accepted.summary)
                    for _ in range(8):
                        if session.state.cards[body.object_id].zone=='hand':break
                        self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                    while session.state.priority_player!='A':
                        self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                    actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    cast=next(row for row in actions if row.get('card')==body.ref)
                    accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
                self.resolve(session)
                current=session.state.cards[body.object_id]
                self.assertEqual(1,current.counters.get('+1/+1',0));self.assertEqual(0,session.state.cards[observer.object_id].counters.get('+1/+1',0))
                if reenter:self.assertNotEqual(identity,current.logical_object_id)
                for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
                self.replay(session)

    def test_original_polluted_bonds_land_controller_and_campaign_split_defenders_replay(self):
        session=self.session(3632501);engine=session.engine
        observer=self.add(engine,'Polluted Bonds',seat='B',ref='event-controller-bonds')
        self.assert_whole_admission(observer.printed_name)
        setup=self.add(engine,'Generic Boolean Life Response',zone='hand',ref='event-controller-setup')
        self.ready(session,setup,{})
        land=next(session.state.cards[oid] for oid in session.state.players['A'].zones['hand'] if 'Land' in engine.card_record(session.state.cards[oid]).type_line)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row['id'].startswith('play-land:') and row.get('card')==land.ref)
        life={seat:session.state.players[seat].life for seat in 'ABCD'}
        self.checkpoint(session);before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id']}).ok);self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id']});self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session)
        self.assertEqual(life['A']-2,session.state.players['A'].life);self.assertEqual(life['B']+2,session.state.players['B'].life)
        for seat in 'CD':self.assertEqual(life[seat],session.state.players[seat].life)
        self.replay(session)
        from quorune.model import CombatState
        session=self.session(3632502);engine=session.engine
        self.add(engine,'Campaign of Vengeance',ref='event-controller-campaign')
        attackers=[self.add(engine,'Generic Bound Body',ref='event-controller-attacker-'+str(i)) for i in range(2)]
        self.assert_whole_admission('Campaign of Vengeance')
        engine.permissions.invalidate_current();session.state.pending_decision=None;session.state.started=True
        session.state.active_player='A';session.state.phase='combat';session.state.step='declare_attackers'
        session.state.phase_index=5;session.state.priority_player=None;session.state.priority_passes=[];session.state.combat=CombatState()
        engine.pump();self.checkpoint(session)
        life={seat:session.state.players[seat].life for seat in 'ABCD'}
        accepted=session.act('pilot:A',{'action_id':'attack','attackers':{attackers[0].ref:'B',attackers[1].ref:'C'}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('trigger.order',session.state.pending_decision.kind)
        pending=[item for batch in session.state.pending_trigger_batches for item in batch.items]
        self.assertEqual(2,len(pending))
        self.replay(session,load=True)
        accepted=session.act('pilot:A',{'action_id':'order','triggers':[item.ref for item in reversed(pending)]})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(life['A']+2,session.state.players['A'].life)
        self.assertEqual(life['B']-1,session.state.players['B'].life);self.assertEqual(life['C']-1,session.state.players['C'].life)
        self.assertEqual(life['D'],session.state.players['D'].life)
        self.replay(session)

    def test_original_colored_sphere_prevention_actual_damage_contrasts_and_departure_replay(self):
        session=self.session(3632401);engine=session.engine
        sphere=self.add(engine,'Sphere of Law',zone='hand',ref='colored-prevention-sphere')
        red=[self.add(engine,'Lightning Bolt',zone='hand',ref='colored-prevention-red-'+str(i)) for i in range(3)]
        blue=self.add(engine,'Generic Colored Prevention Blue Damage',zone='hand',ref='colored-prevention-blue')
        remove=self.add(engine,'Disenchant',zone='hand',ref='colored-prevention-remove')
        self.assert_whole_admission(sphere.printed_name)
        offered=self.ready(session,sphere,{'C':6,'W':2,'R':6})
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:D',{'action_id':offered['id'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        life={seat:session.state.players[seat].life for seat in 'ABCD'}
        def cast(card,target):
            while session.state.priority_player!='A':
                self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
            actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            self.assertTrue(any(row.get('card')==card.ref for row in actions),
                {'card':card.printed_name,'pool':dict(session.state.players['A'].mana_pool),'zone':session.state.cards[card.object_id].zone})
            offered=next(row for row in actions if row.get('card')==card.ref)
            accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':[target],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        cast(red[0],'A');self.assertEqual(life['A']-1,session.state.players['A'].life)
        cast(blue,'A');self.assertEqual(life['A']-4,session.state.players['A'].life)
        cast(red[1],'B');self.assertEqual(life['B']-3,session.state.players['B'].life)
        cast(remove,sphere.ref);self.assertEqual('graveyard',session.state.cards[sphere.object_id].zone)
        self.replay(session,load=True)
        cast(red[2],'A');self.assertEqual(life['A']-7,session.state.players['A'].life)
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_event_player_two_result_kambal_and_assassin_native_actions_replay(self):
        session=self.session(3632301);engine=session.engine
        observer=self.add(engine,'Kambal, Consul of Allocation',seat='B',ref='sequence-event-kambal')
        spell=self.add(engine,'Generic Boolean Life Response',zone='hand',ref='sequence-event-noncreature')
        creature=self.add(engine,'Generic Bound Body',zone='hand',ref='sequence-event-creature')
        self.assert_whole_admission(observer.printed_name)
        offered=self.ready(session,spell,{'W':1})
        life={seat:session.state.players[seat].life for seat in 'ABCD'}
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':['A'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        trigger=next(item for item in session.state.stack if item.kind=='triggered_ability')
        self.assertEqual('B',trigger.controller)
        self.replay(session,load=True);self.resolve(session)
        self.assertEqual(life['A'],session.state.players['A'].life)
        self.assertEqual(life['B']+2,session.state.players['B'].life)
        for seat in 'CD':self.assertEqual(life[seat],session.state.players[seat].life)
        while session.state.priority_player!='A':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==creature.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertFalse(any(item.kind=='triggered_ability' for item in session.state.stack))
        self.resolve(session)
        self.assertEqual(life['B']+2,session.state.players['B'].life)
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)
        from quorune.model import CombatState
        session=self.session(3632302);engine=session.engine
        source=self.add(engine,'Agate-Blade Assassin',ref='sequence-event-assassin')
        self.assert_whole_admission(source.printed_name)
        engine.permissions.invalidate_current();session.state.pending_decision=None;session.state.started=True
        session.state.active_player='A';session.state.phase='combat';session.state.step='declare_attackers'
        session.state.phase_index=5;session.state.priority_player=None;session.state.priority_passes=[];session.state.combat=CombatState()
        engine.pump();self.checkpoint(session)
        life={seat:session.state.players[seat].life for seat in 'ABCD'}
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':'attack','attackers':{source.ref:'B'}}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True);self.resolve(session)
        self.assertEqual(life['A']+1,session.state.players['A'].life)
        self.assertEqual(life['B']-1,session.state.players['B'].life)
        for seat in 'CD':self.assertEqual(life[seat],session.state.players[seat].life)
        self.replay(session)

    def test_original_forced_fruition_caster_draw_and_caress_discarder_life_loss_replay(self):
        session=self.session(3632201);engine=session.engine
        observer=self.add(engine,'Forced Fruition',seat='B',ref='event-result-fruition')
        spell=self.add(engine,'Generic Boolean Life Response',zone='hand',ref='event-result-fruition-cast')
        # Refill a larger known initial library so this witness exercises seven draws.
        for index in range(8):self.add(engine,'Generic Bound Plains',zone='library',ref='event-result-extra-library-'+str(index))
        self.assert_whole_admission(observer.printed_name)
        offered=self.ready(session,spell,{})
        hand=len(session.state.players['A'].zones['hand'])
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id'],'targets':['A'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':['A'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True);self.resolve(session)
        self.assertEqual(hand+6,len(session.state.players['A'].zones['hand']))
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)
        session=self.session(3632202);engine=session.engine
        observer=self.add(engine,"Liliana's Caress",ref='event-result-caress')
        discard=self.add(engine,'Mind Rot',zone='hand',ref='event-result-caress-discard')
        self.assert_whole_admission(observer.printed_name)
        offered=self.ready(session,discard,{'C':2,'B':1})
        life=session.state.players['B'].life
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);decision=self.resolve(session)
        self.assertIsNotNone(decision)
        cards=[session.state.cards[oid].ref for oid in session.state.players['B'].zones['hand'][:2]]
        accepted=session.act('pilot:B',{'action_id':'choose','cards':cards})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True)
        for _ in range(12):
            if session.state.pending_decision is not None and session.state.pending_decision.kind=='trigger.order':
                pending=[item for batch in session.state.pending_trigger_batches for item in batch.items]
                accepted=session.act('pilot:A',{'action_id':'order','triggers':[item.ref for item in pending]})
                self.assertTrue(accepted.ok,accepted.summary)
            decision=self.resolve(session)
            if not session.state.stack and not session.state.pending_trigger_batches:break
        self.assertEqual(life-4,session.state.players['B'].life,
            {'decision':session.state.pending_decision.kind if session.state.pending_decision else None,
             'stack':[(item.kind,item.semantic_key) for item in session.state.stack],
             'discarded':[(ref,next(c.zone for c in session.state.cards.values() if c.ref==ref)) for ref in cards]})
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_event_bound_mill_uses_damaged_player_defender_and_caster_with_replay(self):
        from quorune.model import CombatState
        for index,name in enumerate(('Shriekgeist','Crosstown Courier','Nemesis of Reason')):
            with self.subTest(name=name):
                session=self.session(3632101+index);engine=session.engine
                source=self.add(engine,name,ref='event-bound-mill-source')
                self.assert_whole_admission(source.printed_name)
                engine.permissions.invalidate_current();session.state.pending_decision=None;session.state.started=True
                session.state.active_player='A';session.state.priority_player=None;session.state.priority_passes=[]
                session.state.phase='combat';session.state.step='declare_attackers';session.state.phase_index=5;session.state.combat=CombatState()
                engine.pump();self.checkpoint(session)
                before={s:len(session.state.players[s].zones['graveyard']) for s in 'ABCD'}
                available=len(session.state.players['B'].zones['library'])
                expected=10 if name=='Nemesis of Reason' else 2
                denied=session.act('pilot:D',{'action_id':'attack','attackers':{source.ref:'B'}})
                self.assertFalse(denied.ok)
                accepted=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
                self.assertTrue(accepted.ok,accepted.summary)
                self.replay(session,load=True)
                for _ in range(60):
                    if session.state.phase=='postcombat_main' and not session.state.stack:break
                    decision=session.state.pending_decision
                    if decision.kind=='priority':command={'action_id':'pass'}
                    elif decision.kind=='combat.blockers':command={'action_id':'block','blocks':{}}
                    else:self.fail('Unexpected boundmill decision '+decision.kind)
                    accepted=session.act(session.pending_principals()[0],command);self.assertTrue(accepted.ok,accepted.summary)
                else:self.fail('Boundmillcombatdidnotfinish')
                self.assertEqual(before['B']+min(expected,available),len(session.state.players['B'].zones['graveyard']))
                for seat in 'ACD':self.assertEqual(before[seat],len(session.state.players[seat].zones['graveyard']))
                self.replay(session)
        session=self.session(3632104);engine=session.engine
        observer=self.add(engine,'Memory Erosion',seat='B',ref='event-mill-caster-observer')
        spell=self.add(engine,'Generic Boolean Life Response',zone='hand',ref='event-mill-cast-spell')
        self.assert_whole_admission(observer.printed_name)
        offered=self.ready(session,spell,{})
        before={s:len(session.state.players[s].zones['graveyard']) for s in 'ABCD'}
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':['A'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True);self.resolve(session)
        self.assertEqual(before['A']+3,len(session.state.players['A'].zones['graveyard']))
        for seat in 'BCD':self.assertEqual(before[seat],len(session.state.players[seat].zones['graveyard']))
        self.replay(session)

    def test_original_psychic_corrosion_draw_mills_current_opponents_as_atomic_batches_and_replays(self):
        from unittest.mock import patch
        from quorune.zone_transitions import ZoneTransitionOwner
        session=self.session(3632001);engine=session.engine
        observer=self.add(engine,'Psychic Corrosion',zone='hand',ref='opponent-mill-corrosion')
        draw=self.add(engine,'Divination',zone='hand',ref='opponent-mill-draw')
        self.assert_whole_admission(observer.printed_name)
        offered=self.ready(session,observer,{'C':4,'U':2})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        while session.state.priority_player!='A':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==draw.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        while session.state.priority_player!='C':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        accepted=session.act('pilot:C',{'action_id':'concede','choices':{'confirm_concede':True}})
        self.assertTrue(accepted.ok,accepted.summary)
        before={s:len(session.state.players[s].zones['graveyard']) for s in 'ABD'}
        available={s:len(session.state.players[s].zones['library']) for s in 'BD'}
        before_a_library=len(session.state.players['A'].zones['library'])
        self.replay(session,load=True)
        original=ZoneTransitionOwner.move_cards_simultaneously;groups=[]
        def capture(owner,moves,*args,**kwargs):
            moves=tuple(moves)
            if moves and all(destination=='graveyard' for _,destination in moves):
                groups.append(tuple(owner.host.state.cards[object_id].owner for object_id,_ in moves))
            return original(owner,moves,*args,**kwargs)
        with patch.object(ZoneTransitionOwner,'move_cards_simultaneously',new=capture):
            self.resolve(session)
            if session.state.pending_decision is not None and session.state.pending_decision.kind=='trigger.order':
                pending=[item for batch in session.state.pending_trigger_batches for item in batch.items]
                accepted=session.act('pilot:A',{'action_id':'order','triggers':[item.ref for item in pending]})
                self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(before['B']+min(4,available['B']),len(session.state.players['B'].zones['graveyard']))
        self.assertEqual(before['D']+min(4,available['D']),len(session.state.players['D'].zones['graveyard']))
        self.assertEqual(before['A']+1,len(session.state.players['A'].zones['graveyard']))
        self.assertEqual(before_a_library-2,len(session.state.players['A'].zones['library']))
        self.assertEqual(2,len(groups))
        for index,group in enumerate(groups):
            self.assertEqual(min(2,max(0,available['B']-2*index)),group.count('B'))
            self.assertEqual(min(2,max(0,available['D']-2*index)),group.count('D'))
            self.assertTrue(set(group)<={'B','D'})
        for seat in 'BD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_damage_amount_subscription_diagnostic_excludes_noncombat_incoming_and_foreign_sources(self):
        """Matcher diagnostic; actual combat supplies the command witness."""
        from quorune.trigger_discovery import semantic_event_matches
        session=self.session(3631904);engine=session.engine
        wall=self.add(engine,'Wall of Essence',ref='amount-diagnostic-wall')
        thrull=self.add(engine,'Mourning Thrull',ref='amount-diagnostic-thrull')
        other=self.add(engine,'Generic Bound Body',seat='B',ref='amount-diagnostic-other')
        wall_program=next(p for p in engine.semantics.programs_for_oracle(wall.oracle_id) if p.event=='damage.dealt')
        thrull_program=next(p for p in engine.semantics.programs_for_oracle(thrull.oracle_id) if p.event=='damage.dealt.self')
        for combat,target,expected in ((True,wall.ref,True),(False,wall.ref,False),(True,other.ref,False)):
            context={'card':other.ref,'source':other.ref,'target':target,'combat':combat,'amount':2,'target_kind':'permanent'}
            self.assertEqual(expected,semantic_event_matches(engine,wall_program,wall,'damage.dealt',context))
        for card,expected in ((thrull.ref,True),(other.ref,False)):
            context={'card':card,'source':card,'target':wall.ref,'combat':False,'amount':1,'target_kind':'permanent'}
            self.assertEqual(expected,semantic_event_matches(engine,thrull_program,thrull,'damage.dealt',context))

    def test_original_committed_combat_damage_amount_draw_gain_and_receive_replay(self):
        from quorune.model import CombatState
        cases=((3631901,'Fear of Failed Tests',False),(3631902,'Mourning Thrull',False),(3631903,'Wall of Essence',True))
        for seed,name,blocking in cases:
            with self.subTest(name=name):
                session=self.session(seed);engine=session.engine
                source=self.add(engine,name,seat='B' if blocking else 'A',ref='committed-damage-amount-source')
                attacker=self.add(engine,'Generic Bound Body',ref='committed-damage-amount-attacker') if blocking else source
                self.assert_whole_admission(source.printed_name)
                self.ready(session,self.add(engine,'Generic Boolean Life Response',zone='hand',ref='committed-amount-setup'),{})
                session.state.pending_decision=None;session.state.priority_player=None;session.state.priority_passes=[]
                session.state.phase='combat';session.state.step='declare_attackers';session.state.phase_index=5;session.state.combat=CombatState()
                engine.permissions.invalidate_current();engine.pump()
                before_hand=len(session.state.players['A'].zones['hand'])
                life={seat:session.state.players[seat].life for seat in 'ABCD'}
                self.checkpoint(session)
                before=session.state.to_dict()
                self.assertFalse(session.act('pilot:C',{'action_id':'attack','attackers':{attacker.ref:'B'}}).ok)
                self.assertEqual(before,session.state.to_dict())
                accepted=session.act('pilot:A',{'action_id':'attack','attackers':{attacker.ref:'B'}})
                self.assertTrue(accepted.ok,accepted.summary)
                pending_checked=False
                for _ in range(70):
                    if any(item.kind=='triggered_ability' for item in session.state.stack) and not pending_checked:
                        self.replay(session,load=True);pending_checked=True
                    if session.state.phase=='postcombat_main' and not session.state.stack:break
                    decision=session.state.pending_decision
                    if decision.kind=='combat.blockers':
                        command={'action_id':'block','blocks':{source.ref:attacker.ref} if blocking else {}}
                    elif decision.kind=='priority':command={'action_id':'pass'}
                    else:self.fail('Unexpected combat decision '+decision.kind)
                    accepted=session.act(session.pending_principals()[0],command)
                    self.assertTrue(accepted.ok,accepted.summary)
                else:self.fail('Committed damage combat did not reach main phase')
                self.assertTrue(pending_checked)
                if name=='Fear of Failed Tests':
                    self.assertEqual(before_hand+2,len(session.state.players['A'].zones['hand']))
                    self.assertEqual(life['B']-2,session.state.players['B'].life)
                elif name=='Mourning Thrull':
                    self.assertEqual(life['A']+1,session.state.players['A'].life)
                    self.assertEqual(life['B']-1,session.state.players['B'].life)
                else:
                    self.assertEqual(life['B']+2,session.state.players['B'].life)
                    self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
                for observer in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+observer,full=True)['state']['players']['A'])
                self.replay(session)

    def test_original_source_attack_damage_and_granted_sliver_attack_target_revalidation_replay(self):
        from quorune.model import CombatState
        cases=((3631801,'Raging Regisaur',False),(3631802,'Thorncaster Sliver',False),
            (3631803,'Raging Regisaur',True),(3631804,'Thorncaster Sliver',True))
        for seed,name,revalidate in cases:
            with self.subTest(name=name,revalidate=revalidate):
                session=self.session(seed);engine=session.engine
                source=self.add(engine,name,ref='source-action-damage-observer')
                attacker=source if name=='Raging Regisaur' else self.add(engine,'Generic Ordinary Grant Sliver',ref='source-action-granted-attacker')
                victim=self.add(engine,'Generic Bound Body',seat='B',ref='source-action-damage-victim')
                bounce=self.add(engine,'Unsummon',zone='hand',ref='source-action-target-bounce')
                self.assert_whole_admission(source.printed_name)
                self.ready(session,bounce,{'U':1})
                session.state.pending_decision=None;session.state.priority_player=None
                session.state.priority_passes=[];session.state.phase='combat'
                session.state.step='declare_attackers';session.state.phase_index=5;session.state.combat=CombatState()
                engine.permissions.invalidate_current();engine.pump();self.checkpoint(session)
                before=session.state.to_dict()
                self.assertFalse(session.act('pilot:C',{'action_id':'attack','attackers':{attacker.ref:'B'}}).ok)
                self.assertEqual(before,session.state.to_dict())
                accepted=session.act('pilot:A',{'action_id':'attack','attackers':{attacker.ref:'B'}})
                self.assertTrue(accepted.ok,accepted.summary)
                decision=session.state.pending_decision
                self.assertEqual('semantic.target',decision.kind)
                accepted=session.act('pilot:A',{'action_id':'choose','targets':[victim.ref]})
                self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual(attacker.object_id,session.state.stack[-1].source_object_id)
                self.replay(session,load=True)
                if revalidate:
                    actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    cast=next(row for row in actions if row.get('card')==bounce.ref)
                    accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[victim.ref],'pay':'auto'})
                    self.assertTrue(accepted.ok,accepted.summary)
                self.resolve(session)
                self.assertEqual('hand' if revalidate else 'battlefield',session.state.cards[victim.object_id].zone)
                self.assertEqual(0 if revalidate else 1,session.state.cards[victim.object_id].marked_damage)
                self.assertFalse(session.state.stack)
                for observer in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+observer,full=True)['state']['players']['A'])
                self.replay(session)

    def test_original_dormant_sliver_entry_and_foreign_recipient_draws_replay(self):
        session=self.session(3631701);engine=session.engine
        source=self.add(engine,'Dormant Sliver',zone='hand',ref='ordinary-dormant-source')
        recipient=self.add(engine,'Generic Ordinary Grant Flash Sliver',seat='B',zone='hand',ref='ordinary-dormant-recipient')
        session.state.players['B'].mana_pool['C']=1
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{'G':1,'U':1,'C':2})
        before_a=len(session.state.players['A'].zones['hand'])
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True);self.resolve(session)
        self.assertEqual(before_a,len(session.state.players['A'].zones['hand']))
        while session.state.priority_player!='B':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==recipient.ref)
        before_b=len(session.state.players['B'].zones['hand'])
        accepted=session.act('pilot:B',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(before_b,len(session.state.players['B'].zones['hand']))
        self.assertIn('Defender',engine._effective_card_data(session.state.cards[recipient.object_id])['keywords'])
        for observer in 'ACD':self.assertNotIn('hand',session.packet('pilot:'+observer,full=True)['state']['players']['B'])
        self.replay(session)

    def test_original_clot_granted_regeneration_protects_recipient_after_actual_destroy_and_replays(self):
        session=self.session(3631702);engine=session.engine
        source=self.add(engine,'Clot Sliver',zone='hand',ref='ordinary-clot-source')
        recipient=self.add(engine,'Generic Ordinary Grant Sliver',seat='B',ref='ordinary-clot-recipient')
        non_sliver=self.add(engine,'Generic Bound Body',seat='B',ref='ordinary-clot-nonrecipient')
        destroy=self.add(engine,'Murder',zone='hand',ref='ordinary-clot-destroy')
        session.state.players['B'].mana_pool['C']=2
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{'C':3,'B':3})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertFalse(any('Regenerate' in ability.effect_text for ability in engine._activated_abilities(non_sliver)))
        while session.state.priority_player!='B':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        ability=next(row for row in actions if row['id'].startswith('activate:'+recipient.ref+':'))
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':ability['id'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:B',{'action_id':ability['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True);self.resolve(session)
        self.assertEqual(1,session.state.cards[recipient.object_id].regeneration_shields)
        self.assertEqual(0,session.state.cards[source.object_id].regeneration_shields)
        while session.state.priority_player!='A':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertTrue(any(row.get('card')==destroy.ref for row in actions),
            {'pool':dict(session.state.players['A'].mana_pool),'zone':session.state.cards[destroy.object_id].zone,
             'phase':session.state.phase,'step':session.state.step,'active':session.state.active_player,
             'actions':[(row['id'],row.get('card')) for row in actions]})
        cast=next(row for row in actions if row.get('card')==destroy.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[recipient.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        current=session.state.cards[recipient.object_id]
        self.assertEqual('battlefield',current.zone);self.assertTrue(current.tapped)
        self.assertEqual(0,current.regeneration_shields)
        self.replay(session)

    def test_original_infernal_scarring_granted_death_draw_uses_recipient_controller_and_replays(self):
        session=self.session(3631703);engine=session.engine
        aura=self.add(engine,'Infernal Scarring',zone='hand',ref='ordinary-infernal-aura')
        recipient=self.add(engine,'Generic Bound Body',seat='B',ref='ordinary-infernal-recipient')
        destroy=self.add(engine,'Murder',zone='hand',ref='ordinary-infernal-destroy')
        self.assert_whole_admission(aura.printed_name)
        offered=self.ready(session,aura,{'C':2,'B':3})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':[recipient.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(4,engine._numeric_stat(recipient.object_id,'power'))
        before_b=len(session.state.players['B'].zones['hand'])
        while session.state.priority_player!='A':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==destroy.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[recipient.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session,load=True);self.resolve(session)
        self.assertEqual('graveyard',session.state.cards[recipient.object_id].zone)
        self.assertEqual(before_b+1,len(session.state.players['B'].zones['hand']))
        for observer in 'ACD':self.assertNotIn('hand',session.packet('pilot:'+observer,full=True)['state']['players']['B'])
        self.replay(session)

    def test_original_ghalta_cost_uses_current_aggregate_after_actual_bounce_and_replays(self):
        session=self.session(3631602);engine=session.engine
        spell=self.add(engine,'Ghalta, Primal Hunger',zone='hand',ref='aggregate-ghalta')
        body=self.add(engine,'Generic Aggregate Life Body',ref='aggregate-cost-body')
        self.add(engine,'Generic Aggregate Foreign Body',seat='B',ref='aggregate-cost-foreign')
        bounce=self.add(engine,'Unsummon',zone='hand',ref='aggregate-cost-bounce')
        mana=self.add(engine,'Generic Aggregate Mana Source',ref='aggregate-cost-mana')
        self.assert_whole_admission(spell.printed_name)
        self.assert_whole_admission(mana.printed_name)
        self.ready(session,bounce,{'U':1,'G':2})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        stale=next(row for row in actions if row.get('card')==spell.ref)
        self.checkpoint(session)
        action=next(row for row in actions if row.get('card')==bounce.ref)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        while session.state.priority_player!='A':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row.get('card')==spell.ref for row in actions))
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:A',{'action_id':stale['id'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        cast=next(row for row in actions if row.get('card')==body.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        while session.state.priority_player!='A':
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==spell.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session)
        self.assertEqual('battlefield',session.state.cards[spell.object_id].zone)
        self.assertEqual(12,engine._numeric_stat(spell.object_id,'power'))
        self.assertEqual(0,session.state.players['A'].mana_pool.get('G',0))
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_constructed_aggregate_mana_pays_real_spell_with_current_stats_and_replays(self):
        """Complete constructed carrier; no claim about Selvala's other ability."""
        session=self.session(3631603);engine=session.engine
        mana=self.add(engine,'Generic Aggregate Mana Source',ref='aggregate-mana-source')
        body=self.add(engine,'Generic Aggregate Life Body',ref='aggregate-mana-body')
        self.add(engine,'Generic Aggregate Foreign Body',seat='C',ref='aggregate-mana-foreign')
        spell=self.add(engine,'Essence Harvest',zone='hand',ref='aggregate-mana-essence')
        self.assert_whole_admission(mana.printed_name)
        offered=self.ready(session,mana,{})
        self.assertEqual(10,offered['choice_schema']['mana_output']['total'])
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:B',{'action_id':offered['id'],'mana_output':{'B':1,'G':9}}).ok)
        self.assertEqual(before,session.state.to_dict())
        self.assertFalse(session.act('pilot:A',{'action_id':offered['id'],'mana_output':{'G':20}}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'mana_output':{'B':1,'G':9}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(session.state.cards[mana.object_id].tapped)
        self.assertEqual(9,session.state.players['A'].mana_pool['G'])
        self.assertEqual(1,session.state.players['A'].mana_pool['B'])
        self.assertFalse(session.state.stack)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==spell.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session)
        self.assertEqual(30,session.state.players['B'].life)
        self.assertEqual(50,session.state.players['A'].life)
        self.assertEqual(10,engine._numeric_stat(body.object_id,'power'))
        for seat in 'BCD':self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_essence_harvest_aggregate_resolves_once_across_life_changes_and_replays(self):
        session=self.session(3631601);engine=session.engine
        source=self.add(engine,'Essence Harvest',zone='hand',ref='aggregate-essence-harvest')
        creature=self.add(engine,'Generic Aggregate Life Body',ref='aggregate-life-sensitive-body')
        self.add(engine,'Generic Aggregate Foreign Body',seat='B',ref='aggregate-foreign-body')
        session.state.players['A'].life=35
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{'C':2,'B':1})
        self.assertEqual(10,engine._numeric_stat(creature.object_id,'power'))
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id'],'targets':['A'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':['A'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session)
        self.assertEqual(35,session.state.players['A'].life)
        self.assertEqual(10,engine._numeric_stat(creature.object_id,'power'))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        for seat in 'BCD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_boolean_intervening_condition_rechecks_after_actual_attack_response_and_replays(self):
        from quorune.model import CombatState
        session=self.session(3631502);engine=session.engine
        source=self.add(engine,'Generic Boolean Attack Observer',ref='boolean-attack-source')
        response=self.add(engine,'Generic Boolean Life Response',zone='hand',ref='boolean-attack-response')
        session.state.players['B'].life=10
        self.assert_whole_admission(source.printed_name)
        engine.permissions.invalidate_current();session.state.pending_decision=None
        session.state.started=True;session.state.active_player='A';session.state.phase='combat'
        session.state.step='declare_attackers';session.state.phase_index=5;session.state.combat=CombatState()
        engine.pump();self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(any(item.kind=='triggered_ability' for item in session.state.stack))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==response.ref)
        before=len(session.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session)
        self.assertEqual(12,session.state.players['B'].life)
        self.assertEqual(before-1,len(session.state.players['A'].zones['hand']))
        for seat in 'BCD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_blood_baron_boolean_life_condition_ends_after_actual_life_gain_and_replays(self):
        session=self.session(3631501);engine=session.engine
        source=self.add(engine,'Blood Baron of Vizkopa',zone='hand',ref='boolean-blood-baron')
        life_spell=self.add(engine,'Stream of Life',zone='hand',ref='boolean-life-gain-spell')
        green=self.add(engine,'Generic Boolean Green Source',ref='boolean-life-gain-land')
        session.state.players['B'].life=10
        self.assert_whole_admission(source.printed_name)
        self.assert_whole_admission(life_spell.printed_name)
        offered=self.ready(session,source,{'C':5,'W':1,'B':1})
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:D',{'action_id':offered['id'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(10,engine._numeric_stat(source.object_id,'power'))
        self.assertIn('Flying',engine._effective_card_data(session.state.cards[source.object_id])['keywords'])
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(row for row in actions if row['id'].startswith('activate:'+green.ref+':'))
        self.assertTrue(session.act('pilot:A',{'action_id':mana['id']}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==life_spell.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':['B'],'x':2,'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True);self.resolve(session)
        self.assertEqual(12,session.state.players['B'].life)
        self.assertEqual(4,engine._numeric_stat(source.object_id,'power'))
        keywords=engine._effective_card_data(session.state.cards[source.object_id])['keywords']
        self.assertNotIn('Flying',keywords)
        self.assertIn('Lifelink',keywords)
        for seat in 'BCD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_tyranid_opponent_quantity_resolves_after_actual_concession_and_replays(self):
        session=self.session(3631401);engine=session.engine
        spell=self.add(engine,'Tyranid Invasion',zone='hand',ref='public-opponent-tokens')
        self.assert_whole_admission(spell.printed_name)
        action=self.ready(session,spell,{'C':3,'G':1})
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:D',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        while session.state.priority_player!='C':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.replay(session,load=True)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':'concede'}).ok)
        self.assertEqual(before,session.state.to_dict())
        conceded=session.act('pilot:C',{'action_id':'concede','choices':{'confirm_concede':True}})
        self.assertTrue(conceded.ok,conceded.summary)
        self.assertFalse(session.state.players['C'].in_game)
        self.resolve(session)
        tokens=[card for card in session.state.cards.values() if card.controller=='A' and card.zone=='battlefield' and card.object_kind=='token']
        self.assertEqual(2,len(tokens))
        for token in tokens:
            self.assertEqual(3,engine._numeric_stat(token.object_id,'power'))
            self.assertEqual(3,engine._numeric_stat(token.object_id,'toughness'))
            self.assertIn('Trample',engine._effective_card_data(token)['keywords'])
        for seat in 'BD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_token_doubler_owner_diagnostic_respects_controller_and_current_ability_removal(self):
        """Engine-owner contrast; the Master witness supplies native command evidence."""
        from quorune.continuous_effects import ContinuousEffect,ContinuousEffectOrigin,ContinuousEffectDuration,ContinuousObjectIdentity,ContinuousOperation
        from quorune.continuous_effect_state import commit_continuous_effect
        session=self.session(3631302);engine=session.engine
        source=self.add(engine,'Generic Counted Token Doubler',ref='token-doubler-removal-source')
        self.assert_whole_admission(source.printed_name)
        definition={'type_line':'Token Creature — Wolf','power':'1','toughness':'1','colors':[],'keywords':[]}
        self.assertEqual(1,len(engine.create_token('B',name='Foreign Owner Diagnostic',characteristics=definition)))
        self.assertEqual(2,len(engine.create_token('A',name='Own Owner Diagnostic',characteristics=definition)))
        commit_continuous_effect(engine.state,ContinuousEffect(effect_id='fixture:remove-token-doubler',source_id='fixture:ability-removal',
            layer=Layer.ABILITY,sublayer='6',timestamp=engine._next_zone_timestamp(),
            operations=(ContinuousOperation('remove_all_abilities'),),origin=ContinuousEffectOrigin.RESOLUTION,
            duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,applies=ObjectQuerySpec(zones=('battlefield',)),
            locked_objects=(ContinuousObjectIdentity(source.object_id,source.logical_object_id),)))
        self.assertEqual(1,len(engine.create_token('A',name='Removed Owner Diagnostic',characteristics=definition)))

    def test_original_master_waves_value_tokens_double_enter_with_anthem_and_die_after_bounce_replays(self):
        session=self.session(3631301);engine=session.engine
        master=self.add(engine,'Master of Waves',zone='hand',ref='counted-master-waves')
        self.add(engine,'Generic Devotion Amplifier',ref='counted-token-devotion-modifier')
        self.add(engine,'Generic Devotion Hybrid',ref='counted-token-devotion-hybrid')
        doubler=self.add(engine,'Parallel Lives',zone='hand',ref='counted-token-doubler')
        island=self.add(engine,'Generic Conditional Keyword Island',ref='counted-token-island')
        colorless=self.add(engine,'Generic Counted Token Three Mana',ref='counted-token-colorless')
        bounce=self.add(engine,'Generic Devotion Creature Bounce',zone='hand',ref='counted-master-bounce')
        self.assert_whole_admission(master.printed_name)
        self.assert_whole_admission('Parallel Lives')
        offered=self.ready(session,doubler,{'C':3,'G':1})
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        for land in (island,colorless):
            actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            mana=next(row for row in actions if row['id'].startswith('activate:'+land.ref+':'))
            self.assertTrue(session.act('pilot:A',{'action_id':mana['id']}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==master.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True)
        self.resolve(session)
        tokens=[card for card in session.state.cards.values() if card.zone=='battlefield' and card.controller=='A' and card.object_kind=='token']
        self.assertEqual(6,len(tokens))
        for token in tokens:
            self.assertEqual(2,engine._numeric_stat(token.object_id,'power'))
            self.assertEqual(1,engine._numeric_stat(token.object_id,'toughness'))
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==bounce.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[master.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('hand',session.state.cards[master.object_id].zone)
        self.assertFalse(any(token.object_id in session.state.players['A'].zones['battlefield'] for token in tokens))
        for seat in 'BCD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_karametra_acolyte_mana_uses_devotion_and_current_modifier_with_replay(self):
        session=self.session(3631205);engine=session.engine
        source=self.add(engine,"Karametra's Acolyte",ref='devotion-mana-acolyte')
        self.add(engine,'Generic Devotion Amplifier',ref='devotion-mana-amplifier')
        self.add(engine,'Generic Devotion Green Hybrid',ref='devotion-mana-hybrid')
        self.add(engine,'Generic Devotion Green Hybrid',seat='B',ref='devotion-mana-foreign')
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{})
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id']}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':offered['id']})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(3,session.state.players['A'].mana_pool['G'])
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.replay(session,load=True)

    def test_devotion_cost_modifier_changes_offer_and_accepted_cast_then_private_draw_replays(self):
        session=self.session(3631204);engine=session.engine
        source=self.add(engine,'Generic Devotion Cost Spell',zone='hand',ref='devotion-cost-spell')
        amplifier=self.add(engine,'Generic Devotion Amplifier',ref='devotion-cost-amplifier')
        self.add(engine,'Generic Devotion Hybrid',ref='devotion-cost-hybrid')
        bounce=self.add(engine,'Generic Devotion Creature Bounce',zone='hand',ref='devotion-cost-modifier-bounce')
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{'C':3,'U':1})
        self.checkpoint(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==bounce.ref)
        self.assertTrue(session.act('pilot:A',{'action_id':cast['id'],'targets':[amplifier.ref],'pay':'auto'}).ok)
        self.resolve(session)
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row.get('card')==source.ref for row in actions))
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        recast=next(row for row in actions if row.get('card')==amplifier.ref)
        self.assertTrue(session.act('pilot:A',{'action_id':recast['id'],'pay':'auto'}).ok)
        self.resolve(session)
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==source.ref)
        before=len(session.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual(before,len(session.state.players['A'].zones['hand']))
        for seat in 'BCD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session,load=True)

    def test_original_daxos_devotion_cast_uses_live_modifier_ability_and_departure_with_replay(self):
        from quorune.devotion import current_devotion
        for remove_ability in (False,True):
            with self.subTest(remove_ability=remove_ability):
                session=self.session(3631201+int(remove_ability));engine=session.engine
                daxos=self.add(engine,'Daxos, Blessed by the Sun',zone='hand',ref='devotion-daxos')
                amplifier=self.add(engine,'Generic Devotion Amplifier',ref='devotion-amplifier')
                hybrid=self.add(engine,'Generic Devotion Hybrid',ref='devotion-hybrid')
                self.add(engine,'Generic Devotion Hybrid',seat='B',ref='devotion-foreign-hybrid')
                removal=self.add(engine,'Snakeform' if remove_ability else 'Generic Devotion Creature Bounce',zone='hand',ref='devotion-modifier-ending')
                island=self.add(engine,'Generic Conditional Keyword Island',ref='devotion-removal-island')
                bounce=self.add(engine,'Generic Devotion Artifact Bounce',zone='hand',ref='devotion-hybrid-bounce')
                self.assert_whole_admission(daxos.printed_name)
                offered=self.ready(session,daxos,{'W':4})
                self.checkpoint(session)
                before=session.state.to_dict()
                self.assertFalse(session.act('pilot:D',{'action_id':offered['id'],'pay':'auto'}).ok)
                self.assertEqual(before,session.state.to_dict())
                accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
                self.assertEqual(4,current_devotion(engine,'A',('W',)))
                self.assertEqual(4,engine._numeric_stat(daxos.object_id,'toughness'))
                self.assertEqual(2,engine._numeric_stat(daxos.object_id,'power'))
                for spell,target,expected in ((removal,amplifier,3),(bounce,hybrid,2)):
                    while session.state.priority_player!='A':
                        result=session.act(session.pending_principals()[0],{'action_id':'pass'})
                        self.assertTrue(result.ok,result.summary)
                    actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    if remove_ability and spell is removal:
                        mana=next(row for row in actions if row['id'].startswith('activate:'+island.ref+':'))
                        self.assertTrue(session.act('pilot:A',{'action_id':mana['id']}).ok)
                        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    cast=next(row for row in actions if row.get('card')==spell.ref)
                    accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[target.ref],'pay':'auto'})
                    self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
                    self.assertEqual(expected,current_devotion(engine,'A',('W',)))
                    self.assertEqual(expected,engine._numeric_stat(daxos.object_id,'toughness'))
                for seat in 'BCD':
                    self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
                self.replay(session,load=remove_ability)

    def test_original_nylea_disciple_devotion_life_gain_resolves_after_actual_cast_and_replays(self):
        session=self.session(3631203);engine=session.engine
        source=self.add(engine,"Nylea's Disciple",zone='hand',ref='devotion-disciple')
        self.add(engine,'Generic Devotion Amplifier',ref='devotion-life-amplifier')
        self.add(engine,'Generic Devotion Green Hybrid',ref='devotion-green-hybrid')
        self.add(engine,'Generic Devotion Green Hybrid',seat='C',ref='devotion-foreign-green')
        self.assert_whole_admission(source.printed_name)
        offered=self.ready(session,source,{'G':2,'C':2})
        life=session.state.players['A'].life
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(life+4,session.state.players['A'].life)
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.replay(session,load=True)

    def test_original_howlpack_singular_union_cast_grant_and_source_bounce_replays(self):
        session=self.session(3631101)
        engine=session.engine
        source=self.add(engine,'Howlpack Resurgence',zone='hand',ref='singular-howlpack-source')
        wolf=self.add(engine,'Generic Singular Wolf',ref='singular-wolf')
        werewolf=self.add(engine,'Generic Singular Werewolf',ref='singular-werewolf')
        dual=self.add(engine,'Generic Singular Wolf Werewolf',ref='singular-dual')
        outsider=self.add(engine,'Generic Bound Body',ref='singular-human')
        foreign=self.add(engine,'Generic Singular Wolf',seat='B',ref='singular-foreign-wolf')
        bounce=self.add(engine,'Generic Singular Enchantment Bounce',zone='hand',ref='singular-source-bounce')
        island=self.add(engine,'Generic Conditional Keyword Island',ref='singular-bounce-island')
        self.assert_whole_admission('Howlpack Resurgence')
        offered=self.ready(session,source,{'G':1,'C':2})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        for recipient in (wolf,werewolf,dual):
            self.assertEqual(3,engine._numeric_stat(recipient.object_id,'power'))
            self.assertIn('trample',{word.casefold() for word in engine._effective_card_data(recipient)['keywords']})
        for recipient in (outsider,foreign):
            self.assertEqual(2,engine._numeric_stat(recipient.object_id,'power'))
            self.assertNotIn('trample',{word.casefold() for word in engine._effective_card_data(recipient)['keywords']})
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(row for row in actions if row['id'].startswith('activate:'+island.ref+':'))
        self.assertTrue(session.act('pilot:A',{'action_id':mana['id']}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==bounce.ref)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:D',{'action_id':cast['id'],'targets':[source.ref],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.replay(session,load=True)
        self.resolve(session)
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        for recipient in (wolf,werewolf,dual):
            current=session.state.cards[recipient.object_id]
            self.assertEqual(2,engine._numeric_stat(current.object_id,'power'))
            self.assertNotIn('trample',{word.casefold() for word in engine._effective_card_data(current)['keywords']})
        for seat in 'BCD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_oathsworn_graveyard_cast_uses_current_controller_life_history_and_replay(self):
        session=self.session(3631002)
        engine=session.engine
        vampire=self.add(engine,'Oathsworn Vampire',zone='graveyard',ref='conditional-zone-vampire')
        self.assert_whole_admission('Oathsworn Vampire')
        tapper=self.add(engine,'Generic Conditional Keyword Tap Helper',ref='conditional-zone-lifegain')
        body=self.add(engine,'Generic Bound Body',ref='conditional-zone-lifegain-body')
        self.ready(session,tapper,{'B':1,'C':1})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row.get('card')==vampire.ref for row in actions))
        activation=next(row for row in actions if row['id'].startswith('activate:'+tapper.ref+':'))
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':activation['id'],'cost_cards':[body.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==vampire.ref)
        self.replay(session,load=True)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual('battlefield',session.state.cards[vampire.object_id].zone)
        self.assertTrue(session.state.cards[vampire.object_id].tapped)
        self.replay(session)

    def test_original_prowler_graveyard_permission_tracks_owner_condition_and_actual_cast_with_replay(self):
        session=self.session(3631001)
        engine=session.engine
        prowler=self.add(engine,'Marang River Prowler',zone='graveyard',ref='conditional-zone-prowler')
        prowler.controller='B'
        self.assert_whole_admission('Marang River Prowler')
        qualifier=self.add(engine,'Generic Conditional Green Qualifier',ref='conditional-zone-qualifier')
        foreign=self.add(engine,'Generic Conditional Green Qualifier',seat='C',ref='conditional-zone-foreign')
        bounce=self.add(engine,'Generic Recipient Count Artifact Bounce',zone='hand',ref='conditional-zone-bounce')
        island=self.add(engine,'Generic Conditional Keyword Island',ref='conditional-zone-island')
        self.ready(session,bounce,{'U':2,'C':2})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row.get('card')==prowler.ref)
        from quorune.semantic_runtime.casting_activation_metadata import compiled_self_zone_cast_permission
        self.assertTrue(compiled_self_zone_cast_permission(engine,'A',prowler))
        self.assertFalse(compiled_self_zone_cast_permission(engine,'B',prowler))
        self.checkpoint(session)
        before=session.state.to_dict()
        self.assertFalse(session.act('pilot:D',{'action_id':offered['id'],'pay':'auto'}).ok)
        self.assertEqual(before,session.state.to_dict())
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==bounce.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[qualifier.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertFalse(compiled_self_zone_cast_permission(engine,'A',session.state.cards[prowler.object_id]))
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row.get('card')==prowler.ref for row in actions))
        qualifier_cast=next(row for row in actions if row.get('card')==qualifier.ref)
        self.assertTrue(session.act('pilot:A',{'action_id':qualifier_cast['id'],'pay':'auto'}).ok)
        self.resolve(session)
        while session.state.priority_player!='A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(row for row in actions if row['id'].startswith('activate:'+island.ref+':'))
        self.assertTrue(session.act('pilot:A',{'action_id':mana['id']}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==prowler.ref)
        self.replay(session,load=True)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual('battlefield',session.state.cards[prowler.object_id].zone)
        self.assertEqual('A',session.state.cards[prowler.object_id].controller)
        for seat in 'BCD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_recipient_count_equipment_cast_equip_reattach_and_bounce_replays(self):
        from quorune.attachments import attach_objects
        session = self.session(3630901)
        engine = session.engine
        gauntlets = self.add(engine, 'Golem-Skin Gauntlets', zone='hand', ref='recipient-gauntlets')
        suit = self.add(engine, 'Thran Power Suit', zone='hand', ref='recipient-suit')
        target = self.add(engine, 'Generic Bound Body', ref='recipient-body')
        other = self.add(engine, 'Generic Bound Body', ref='recipient-other-body')
        foreign = self.add(engine, 'Generic Recipient Count Equipment', seat='C', ref='recipient-foreign-equipment')
        attach_objects(engine.state.cards, foreign, target, source_timestamp=engine._next_zone_timestamp())
        bounce = self.add(engine, 'Generic Recipient Count Artifact Bounce', zone='hand', ref='recipient-attachment-bounce')
        island = self.add(engine, 'Generic Conditional Keyword Island', ref='recipient-bounce-island')
        self.assert_whole_admission('Golem-Skin Gauntlets')
        self.assert_whole_admission('Thran Power Suit')
        static = next(p for p in engine.semantics.programs_for_oracle(gauntlets.oracle_id) if p.handlers and p.handlers[0]['handler_id']=='continuous.attached.fixed-characteristics.v1')
        self.assertTrue(engine.semantic_program_is_current_trusted(static))
        self.ready(session,session.state.cards[island.object_id],{'C':9})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row.get('card')==gauntlets.ref)
        self.checkpoint(session)
        accepted = session.act('pilot:A',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        for equipment, recipient in ((gauntlets,target),(suit,target),(gauntlets,other)):
            while session.state.priority_player != 'A':
                result=session.act(session.pending_principals()[0],{'action_id':'pass'})
                self.assertTrue(result.ok,result.summary)
            actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if equipment is suit:
                cast=next(row for row in actions if row.get('card')==suit.ref)
                self.assertTrue(session.act('pilot:A',{'action_id':cast['id'],'pay':'auto'}).ok)
                self.resolve(session)
                while session.state.priority_player != 'A':
                    result=session.act(session.pending_principals()[0],{'action_id':'pass'})
                    self.assertTrue(result.ok,result.summary)
                actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            equip=next(row for row in actions if row['id'].startswith('activate:'+equipment.ref+':'))
            before=session.state.to_dict()
            self.assertFalse(session.act('pilot:D',{'action_id':equip['id'],'targets':[recipient.ref],'pay':'auto'}).ok)
            self.assertEqual(before,session.state.to_dict())
            accepted=session.act('pilot:A',{'action_id':equip['id'],'targets':[recipient.ref],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
            self.resolve(session)
            if equipment is gauntlets and recipient is target:
                self.assertEqual(4,engine._numeric_stat(target.object_id,'power'))
                self.assertEqual(6,engine._numeric_stat(target.object_id,'toughness'))
            elif equipment is suit:
                self.assertEqual(8,engine._numeric_stat(target.object_id,'power'))
                self.assertEqual(9,engine._numeric_stat(target.object_id,'toughness'))
                self.replay(session,load=True)
            else:
                self.assertEqual(4,engine._numeric_stat(target.object_id,'power'))
                self.assertEqual(3,engine._numeric_stat(other.object_id,'power'))
        while session.state.priority_player != 'A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        mana=next(row for row in actions if row['id'].startswith('activate:'+island.ref+':'))
        self.assertTrue(session.act('pilot:A',{'action_id':mana['id']}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==bounce.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[foreign.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual('hand',session.state.cards[foreign.object_id].zone)
        self.assertEqual(3,engine._numeric_stat(target.object_id,'power'))
        for seat in 'BCD':
            self.assertNotIn('hand',session.packet('pilot:'+seat,full=True)['state']['players']['A'])
        self.replay(session)

    def test_original_omenreader_live_reduction_tracks_actual_tap_cast_and_bounce_with_replay(self):
        session = self.session(3630801)
        engine = session.engine
        source = self.add(engine, 'Centaur Omenreader', ref='conditional-cost-source')
        self.assert_whole_admission('Centaur Omenreader')
        tapper = self.add(engine, 'Generic Conditional Keyword Tap Helper', ref='conditional-cost-tapper')
        spell = self.add(engine, 'Generic Conditional Cost Creature', zone='hand', ref='conditional-cost-creature')
        bounce = self.add(engine, 'Unsummon', zone='hand', ref='conditional-cost-bounce')
        foreign = self.add(engine, 'Generic Conditional Cost Creature', seat='B', zone='hand', ref='conditional-cost-other-player')
        self.ready(session, tapper, {'U':1})
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row.get('card') == spell.ref for row in actions))
        activation = next(row for row in actions if row['id'].startswith('activate:'+tapper.ref+':'))
        self.checkpoint(session)
        before = session.state.to_dict()
        self.assertFalse(session.act('pilot:B', {'action_id':activation['id'], 'cost_cards':[source.ref]}).ok)
        self.assertEqual(before, session.state.to_dict())
        accepted = session.act('pilot:A', {'action_id':activation['id'], 'cost_cards':[source.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        while session.state.priority_player != 'A':
            result = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        cast = next(row for row in actions if row.get('card') == spell.ref)
        from quorune.semantic_runtime.cast_costs import active_fixed_spell_cost_reductions
        self.assertTrue(active_fixed_spell_cost_reductions(engine, 'A', session.state.cards[spell.object_id]))
        self.assertEqual((), active_fixed_spell_cost_reductions(engine, 'B', session.state.cards[foreign.object_id]))
        self.assertEqual((), active_fixed_spell_cost_reductions(engine, 'A', session.state.cards[bounce.object_id]))
        self.replay(session, load=True)
        accepted = session.act('pilot:A', {'action_id':cast['id'], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual('battlefield', session.state.cards[spell.object_id].zone)
        while session.state.priority_player != 'A':
            result = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        cast = next(row for row in actions if row.get('card') == bounce.ref)
        accepted = session.act('pilot:A', {'action_id':cast['id'], 'targets':[source.ref], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[source.object_id].zone)
        from quorune.semantic_runtime.cast_costs import active_fixed_spell_cost_reductions
        self.assertEqual((), active_fixed_spell_cost_reductions(engine, 'A', session.state.cards[spell.object_id]))
        for seat in 'BCD':
            public = session.packet('pilot:'+seat, full=True)['state']['players']['A']
            self.assertNotIn('hand', public)
        self.replay(session)

    def test_original_pristine_angel_cast_and_live_protection_follow_actual_tap_untap_and_replay(self):
        from quorune.ability_fragments import ProtectionSpec
        session = self.session(3630702)
        engine = session.engine
        angel = self.add(engine, 'Pristine Angel', zone='hand', ref='conditional-pristine')
        tapper = self.add(engine, 'Generic Conditional Keyword Tap Helper', ref='conditional-protection-tapper')
        self.add(engine, 'Generic Bound Body', ref='conditional-protection-other')
        island = self.add(engine, 'Generic Conditional Keyword Island', ref='conditional-protection-island')
        bounce = self.add(engine, 'Unsummon', zone='hand', ref='conditional-protection-bounce')
        offered = self.ready(session, angel, {'W':2, 'C':4})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id':offered['id'], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertTrue(any(isinstance(f, ProtectionSpec) for f in engine._effective_ability_fragments(angel)))
        while session.state.priority_player != 'A':
            result = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        mana_action = next(row for row in actions if row['id'].startswith('activate:'+island.ref+':'))
        accepted = session.act('pilot:A', {'action_id':mana_action['id']})
        self.assertTrue(accepted.ok, accepted.summary)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        protected_cast = next((row for row in actions if row.get('card') == bounce.ref), None)
        self.assertIsNotNone(protected_cast)
        before = session.state.to_dict()
        rejected = session.act('pilot:A', {'action_id':protected_cast['id'], 'targets':[angel.ref], 'pay':'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, session.state.to_dict())
        angel = session.state.cards[angel.object_id]
        activation = next(row for row in actions if row['id'].startswith('activate:'+tapper.ref+':'))
        accepted = session.act('pilot:A', {'action_id':activation['id'], 'cost_cards':[angel.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(angel.tapped)
        self.assertFalse(any(isinstance(f, ProtectionSpec) for f in engine._effective_ability_fragments(angel)))
        self.resolve(session)
        while session.state.priority_player != 'A':
            result = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        cast = next(row for row in actions if row.get('card') == bounce.ref)
        accepted = session.act('pilot:A', {'action_id':cast['id'], 'targets':[angel.ref], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        choice = self.resolve(session)
        self.assertEqual('semantic.choice', choice.kind)
        self.replay(session, load=True)
        selected = session.act('pilot:A', {'action_id':'choose', 'choice':'apply', 'reason':'Untap the original Angel.'})
        self.assertTrue(selected.ok, selected.summary)
        self.resolve(session)
        self.assertFalse(angel.tapped)
        self.assertEqual('battlefield', session.state.cards[angel.object_id].zone)
        self.assertTrue(any(isinstance(f, ProtectionSpec) for f in engine._effective_ability_fragments(angel)))
        for seat in 'BCD':
            public = session.packet('pilot:'+seat, full=True)['state']['players']['A']
            self.assertNotIn('hand', public)
        self.replay(session)

    def test_original_k9_conditional_ward_survives_creation_then_ends_after_real_tap_and_replays(self):
        from quorune.trigger_participation import WardSpec
        session = self.session(3630701)
        engine = session.engine
        source = self.add(engine, 'K-9, Mark I', ref='conditional-k9')
        self.assert_whole_admission('K-9, Mark I')
        target = session.state.cards[session.state.players['A'].zones['command'][0]]
        engine.move_card(target.object_id, 'battlefield', controller='A', log=False)
        outsider = self.add(engine, 'Generic Bound Body', seat='C', ref='conditional-ward-outsider')
        first = self.add(engine, 'Unsummon', seat='B', zone='hand', ref='conditional-ward-first')
        second = self.add(engine, 'Unsummon', seat='B', zone='hand', ref='conditional-ward-second')
        engine.state.players['B'].mana_pool['U'] = 2
        offered = self.ready(session, source, {'U':1, 'C':1})
        self.checkpoint(session)
        before = session.state.to_dict()
        self.assertFalse(session.act('pilot:C', {'action_id':offered['id'], 'pay':'auto'}).ok)
        self.assertEqual(before, session.state.to_dict())
        self.assertTrue(any(isinstance(f, WardSpec) for f in engine._effective_ability_fragments(target)))
        self.assertFalse(any(isinstance(f, WardSpec) for f in engine._effective_ability_fragments(source)))
        self.assertFalse(any(isinstance(f, WardSpec) for f in engine._effective_ability_fragments(outsider)))
        while session.state.priority_player != 'B':
            result = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        actions = session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        cast = next(row for row in actions if row.get('card') == first.ref)
        accepted = session.act('pilot:B', {'action_id':cast['id'], 'targets':[target.ref], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        ward = self.resolve(session)
        self.assertEqual('semantic.choice', ward.kind)
        self.replay(session, load=True)
        before = session.state.to_dict()
        self.assertFalse(session.act('pilot:C', {'action_id':'choose', 'pay':False, 'plan':'DECLINE_WARD'}).ok)
        self.assertEqual(before, session.state.to_dict())
        declined = session.act('pilot:B', {'action_id':'choose', 'pay':False, 'plan':'DECLINE_WARD', 'reason':'Preserve mana.'})
        self.assertTrue(declined.ok, declined.summary)
        self.resolve(session)
        self.assertEqual('battlefield', session.state.cards[target.object_id].zone)
        while session.state.priority_player != 'A':
            result = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        activation = next(row for row in actions if row['id'].startswith('activate:'+source.ref+':'))
        accepted = session.act('pilot:A', {'action_id':activation['id'], 'targets':[target.ref], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(source.tapped)
        self.assertFalse(any(isinstance(f, WardSpec) for f in engine._effective_ability_fragments(target)))
        self.resolve(session)
        while session.state.priority_player != 'B':
            result = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        actions = session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        cast = next(row for row in actions if row.get('card') == second.ref)
        accepted = session.act('pilot:B', {'action_id':cast['id'], 'targets':[target.ref], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        replacement = self.resolve(session)
        self.assertEqual('replacement.order', replacement.kind)
        declined_replacement = next(row['id'] for row in replacement.payload_by_actor['A']['options'] if row['decline'])
        accepted = session.act('pilot:A', {'action_id':'choose', 'replacement':declined_replacement})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual('hand', session.state.cards[target.object_id].zone)
        for seat in 'ACD':
            public = session.packet('pilot:'+seat, full=True)['state']['players']['B']
            self.assertNotIn('hand', public)
        self.replay(session)

    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "additional-modifiers.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/additional-conditional-modifier-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Additional modifiers", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 10),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    import test_bound_effect_programs as _bound
    session = _bound.BoundEffectProgramRuntimeTests.session
    add = _bound.BoundEffectProgramRuntimeTests.add
    ready = _bound.BoundEffectProgramRuntimeTests.ready
    checkpoint = _bound.BoundEffectProgramRuntimeTests.checkpoint
    resolve = _bound.BoundEffectProgramRuntimeTests.resolve
    replay = _bound.BoundEffectProgramRuntimeTests.replay

    def clear_initial_hand(self, session):
        for object_id in tuple(session.state.players["A"].zones["hand"]):
            session.engine.move_card(object_id, "library", log=False)

    def assert_whole_admission(self, name):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry

        program = compile_best_available_card_program(
            self.db, self.db.lookup(name), semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile="commander_review",
        )
        self.assertTrue(bind_card_program_runtime(
            program, capability_registry=self.registry, profile="commander_review",
        )["strict_capability_ready"])

    def test_original_taste_adds_live_bonus_to_foreign_creature_and_private_draw_removes_it(self):
        """Actual hand-empty timing adds +2/+0 to the existing +2/+0."""
        session = self.session(341001)
        engine = session.engine
        self.clear_initial_hand(session)
        target = self.add(engine, "Generic Bound Body", seat="B", ref="taste-target")
        other = self.add(engine, "Generic Bound Body", seat="C", ref="taste-other")
        aura = self.add(engine, "Taste for Mayhem", zone="hand")
        draw = self.add(engine, "Divination", zone="hand")
        self.assert_whole_admission(aura.printed_name)
        action = self.ready(session, aura, {"R": 1, "U": 3})
        self.checkpoint(session)
        before = session.state.to_dict()
        denied = session.act("pilot:B", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertFalse(denied.ok)
        self.assertEqual(before, session.state.to_dict())
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual(4, engine._numeric_stat(target.object_id, "power"))
        self.assertEqual(1, len(session.state.players["A"].zones["hand"]))
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row.get("card") == draw.ref)
        accepted = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(0, len(session.state.players["A"].zones["hand"]))
        self.assertEqual(6, engine._numeric_stat(target.object_id, "power"))
        self.assertEqual(2, engine._numeric_stat(other.object_id, "power"))
        self.resolve(session)
        self.assertEqual(2, len(session.state.players["A"].zones["hand"]))
        self.assertEqual(4, engine._numeric_stat(target.object_id, "power"))
        self.assertEqual("battlefield", session.state.cards[aura.object_id].zone)
        self.assertEqual(target.object_id, session.state.cards[aura.object_id].attached_to)
        for seat in "BCD":
            packet = session.packet("pilot:" + seat, full=True)
            self.assertIsNone(packet["decision"])
            public = packet["state"]["players"]["A"]
            self.assertEqual(2, public["hand_n"])
            self.assertNotIn("hand", public)
        self.replay(session, load=True)

    def test_original_jetmir_additional_thresholds_stack_and_recompute_after_real_bounce(self):
        for count, before_power, after_power, removed in (
            (6, 4, 3, "trample"),
            (9, 5, 4, "double strike"),
        ):
            with self.subTest(count=count):
                session = self.session(341010 + count)
                engine = session.engine
                self.clear_initial_hand(session)
                bodies = [self.add(engine, "Generic Bound Body", ref=f"jetmir-body-{i}") for i in range(count - 1)]
                foreign = self.add(engine, "Generic Bound Body", seat="B", ref="jetmir-foreign")
                source = self.add(engine, "Jetmir, Nexus of Revels", zone="hand")
                bounce = self.add(engine, "Unsummon", zone="hand")
                self.assert_whole_admission(source.printed_name)
                action = self.ready(session, source, {"R": 1, "G": 1, "W": 1, "U": 2})
                self.checkpoint(session)
                accepted = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
                self.assertTrue(accepted.ok, accepted.summary)
                self.resolve(session)
                self.assertEqual(before_power, engine._numeric_stat(bodies[0].object_id, "power"))
                self.assertIn(removed, engine._combat_keywords(session.state.cards[bodies[0].object_id]))
                self.assertEqual(2, engine._numeric_stat(foreign.object_id, "power"))
                self.assertNotIn(removed, engine._combat_keywords(foreign))
                actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                action = next(row for row in actions if row.get("card") == bounce.ref)
                accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [bodies[-1].ref], "pay": "auto"})
                self.assertTrue(accepted.ok, accepted.summary)
                self.resolve(session)
                self.assertEqual("hand", session.state.cards[bodies[-1].object_id].zone)
                self.assertEqual(after_power, engine._numeric_stat(bodies[0].object_id, "power"))
                self.assertNotIn(removed, engine._combat_keywords(session.state.cards[bodies[0].object_id]))
                self.assertIn("vigilance", engine._combat_keywords(session.state.cards[bodies[0].object_id]))
                self.replay(session, load=True)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from common import ROOT
from quorune.card_programs import bind_card_program_runtime, compile_card_program
from quorune.characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
from quorune.compiler.continuous_templates import fixed_public_state_characteristics_handler
from quorune.compiler.public_state_fact_queries import fixed_graveyard_condition_query, fixed_public_fact_condition
from quorune.continuous_conditions import FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID, FixedPublicStateConditionError, FixedPublicStateConditionKind, FixedPublicStateFact, FixedPublicStateConditionSpec
from quorune.object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime import default_continuous_effect_component_registry
from quorune.semantic_runtime.context import SemanticNodeError
from public_state_condition_support import CAPABILITY_ID, TEMPLATE_ID, QUERY_HANDLER_ID, FIXTURE, focused_card_database, source_context


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
                descriptor, bound = node.handlers
                self.assertEqual('continuous.characteristics.fixed-public-state.v1', descriptor['handler_id'])
                self.assertEqual(1, descriptor['schema_version'])
                self.assertEqual(descriptor['source_condition'], bound['fragment']['value']['source_condition'])
                fragment = ability_fragment_from_dict(bound['fragment'])
                self.assertEqual('source', fragment.scope)
                self.assertIsInstance(fragment.rules[0], fragment_type)
                self.assertIn('combat.declaration.typed_components', node.capability_dependencies)
                self.assertIn('continuous.characteristics.fixed_public_state', node.capability_dependencies)

    def test_conditional_declaration_composition_rejects_open_grammar_and_malformed_descriptors(self):
        from test_qualified_zone_event_queries import query_record
        from quorune.compiler.declaration_nodes import conditional_characteristic_declaration_grant
        text = "As long as you have no cards in hand, this creature gets +2/+0 and can't block."
        compiled = conditional_characteristic_declaration_grant(text, source_name='Generic Query Observer')
        self.assertIsNotNone(compiled)
        from quorune.semantic_runtime.ability_fragments import default_ability_fragment_registry
        continuous = default_continuous_effect_component_registry()
        fragments = default_ability_fragment_registry()
        def validate(descriptor):
            owner = fragments if fragments.describe(descriptor['handler_id']) is not None else continuous
            owner.validate(descriptor)
        characteristic, bound = compiled[1]
        for descriptor in compiled[1]:
            validate(descriptor)
            for field, value in (('schema_version', True), ('unknown', True)):
                with self.assertRaises((SemanticNodeError, ValueError)):
                    validate({**descriptor, field:value})
        for altered in (text + ' Draw a card.', text.replace('+2/+0', '+X/+0'),
                text.replace("can't block", "can't block unless you pay 1 life"),
                text.replace('no cards in hand', 'the chosen card in hand')):
            self.assertIsNone(conditional_characteristic_declaration_grant(altered, source_name='Generic Query Observer'))
        changed = copy.deepcopy(bound)
        changed['fragment']['value']['rules'] = []
        with self.assertRaises((SemanticNodeError, ValueError)):
            validate(changed)
        changed = copy.deepcopy(characteristic)
        changed['modifier']['power'] = True
        with self.assertRaises((SemanticNodeError, ValueError)):
            validate(changed)
        changed = copy.deepcopy(bound)
        changed['fragment']['value']['scope'] = 'all_creatures'
        with self.assertRaises((SemanticNodeError, ValueError)):
            validate(changed)
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in ('combat.declaration.typed_components', 'continuous.characteristics.fixed_public_state'):
            altered = copy.deepcopy(raw)
            next(row for row in altered['capabilities'] if row['id']==capability).update(status='blocked', blockers=['independent declaration dependency withdrawal'])
            ir = compile_oracle_card(query_record(text), capability_registry=CapabilityRegistry(altered), capability_profile='commander_review')
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
                "query_characteristic_threshold",
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
                if expected_kind == 'query_characteristic_threshold':
                    from quorune.ability_fragments import ability_fragment_from_dict
                    self.assertEqual('continuous-self-query-characteristics-v1', node.template_id)
                    fragment = ability_fragment_from_dict(node.handlers[0]['fragment'])
                    self.assertEqual(1, fragment.minimum_count)
                    self.assertTrue(fragment.quantity.exclude_source)
                    self.assertEqual(2, fragment.quantity.query.minimum_color_count)
                    self.assertEqual((1,1), (fragment.power,fragment.toughness))
                    self.assertEqual(('Flying',), fragment.add_abilities)
                    from quorune.semantic_runtime.ability_fragments import default_ability_fragment_registry
                    default_ability_fragment_registry().validate(node.handlers[0])
                    continue
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
        promoted = replace(base, oracle_id='00000000-0000-4000-8000-000118200999',
            oracle_text='As long as you control a creature with flying, this creature gets +1/+1.', keywords=())
        current = compile_card_program(self.db, promoted, capability_registry=self.capabilities,
            capability_profile='commander_review', trust_level='trusted')
        self.assertFalse(current.residuals)
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
            "gets +1/+1 and has flying.",
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
                "composed_public_state_characteristics_handler",
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

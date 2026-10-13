from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
import tempfile
from dataclasses import replace
from unittest.mock import patch

from quorune.compiler.resolution_condition_templates import resolution_condition_template
from quorune.oracle_ir import _reviewed_effect_template
from quorune.resolution_conditions import RESOLUTION_CONDITION_CAPABILITY
from quorune.rules.capabilities import capability_dependencies_for_node
from quorune.rules.capabilities import load_default_capability_registry
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
from common import ROOT


class ResolutionPublicConditionCompilerTests(unittest.TestCase):
    def test_dual_departure_usage_limit_remains_outside_supported_nondeparture_contract(self):
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card
        registry=load_default_capability_registry()
        for text in (
            'When this creature enters and when this creature dies, draw a card. This ability triggers only once each turn.',
            'When this artifact enters and when you sacrifice it, draw a card. This ability triggers only once each turn.',
        ):
            ir=compile_oracle_card(record(text,'Creature — Human'),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,text)
        from quorune.rules.trigger_limits import validate_program_trigger_limit,TRIGGER_ONCE_PER_TURN_CAPABILITY
        from types import SimpleNamespace
        for event in ('creature.dies','creature.dies.self','permanent.sacrificed','permanent.sacrificed.self'):
            program=SimpleNamespace(event='permanent.enter',active_zone='battlefield',trigger_limit={'schema_version':1,'kind':'once_per_turn'},
                capability_dependencies=(TRIGGER_ONCE_PER_TURN_CAPABILITY,),event_condition={'event_subscriptions':[
                    {'event':'permanent.enter','condition':None},{'event':event,'condition':None}]})
            with self.assertRaisesRegex(ValueError,'nondeparture'):validate_program_trigger_limit(program)

    def test_independent_children_preserve_standalone_source_global_and_departure_meaning(self):
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding,_public_state_source_self_trigger_binding
        from quorune.trigger_discovery import semantic_event_matches
        from quorune.model import CardInstance
        from types import SimpleNamespace
        source=CardInstance(object_id='observer',ref='observer',oracle_id='fixture:source',printed_name='Fixture',
            owner='A',controller='A',zone='battlefield')
        host=SimpleNamespace(active_seats=tuple('ABCD'),state=SimpleNamespace(cards={},active_player='C'))
        registry=load_default_capability_registry()
        profiles=(
            ('Whenever this creature deals combat damage to a player','damage.dealt',{'target_kind':'player','combat':True}),
            ('Whenever this creature deals damage to an opponent','damage.dealt',{'target_kind':'player','target':'B','player':'B','combat':False}),
            ('Whenever this creature attacks','creature.attacks',{}),
            ('Whenever this creature blocks','creature.blocks',{}),
            ('Whenever this creature becomes blocked','creature.becomes_blocked',{}),
            ('When this creature dies','creature.dies',{'previous_controller':'A','types':['creature']}),
            ('When you sacrifice this creature','permanent.sacrificed',{'previous_controller':'A','types':['creature']}),
            ('When this creature is turned face up','permanent.turned_face_up',{}),
        )
        for prefix,event,facts in profiles:
            with self.subTest(prefix=prefix):
                child=fixed_counter_trigger_binding(prefix+', draw a card.',card_name='Fixture') or _public_state_source_self_trigger_binding(prefix+', draw a card.',card_name='Fixture')
                ir=compile_oracle_card(record('When this creature enters and '+prefix.casefold()+', draw a card.','Creature — Human'),
                    capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.to_dict())
                node=next(n for n in ir.faces[0].nodes if n.kind=='triggered_ability')
                child_program=SimpleNamespace(event=child.event.value,event_condition=child.event_condition,active_zone=child.active_zone)
                composed=SimpleNamespace(event=node.event,event_condition=node.event_condition,active_zone=node.active_zone)
                for ref,expected in (('observer',True),('friendly',False),('opponent',False),('same-name',False)):
                    context={**facts,'card':ref,'controller':'B' if ref=='opponent' else 'A'}
                    standalone=semantic_event_matches(host,child_program,source,event,context)
                    combined=semantic_event_matches(host,composed,source,event,context)
                    self.assertEqual(expected,standalone,context)
                    self.assertEqual(standalone,combined,context)
        for prefix in ('Whenever a player casts a spell','Whenever you cast a Spirit spell'):
            child=fixed_counter_trigger_binding(prefix+', draw a card.',card_name='Fixture')
            ir=compile_oracle_card(record('When this creature enters and '+prefix.casefold()+', draw a card.','Creature — Human'),capability_registry=registry)
            self.assertEqual('exact',ir.status)
            node=next(n for n in ir.faces[0].nodes if n.kind=='triggered_ability')
            for controller in 'AB':
                context={'card':'unrelated-spell','controller':controller,'subtypes':['spirit'],'types':['creature'],'mana_value':2}
                expected=prefix.startswith('Whenever a player') or controller=='A'
                self.assertEqual(expected,semantic_event_matches(host,SimpleNamespace(event=child.event.value,
                    event_condition=child.event_condition,active_zone=child.active_zone),source,'spell.cast',context))
                self.assertEqual(expected,semantic_event_matches(host,SimpleNamespace(event=node.event,
                    event_condition=node.event_condition,active_zone=node.active_zone),source,'spell.cast',context))
        prefix='Whenever this creature is dealt damage'
        child=fixed_counter_trigger_binding(prefix+', draw a card.',card_name='Fixture')
        ir=compile_oracle_card(record('When this creature enters and '+prefix.casefold()+', draw a card.','Creature — Human'),capability_registry=registry)
        self.assertEqual('exact',ir.status)
        node=next(n for n in ir.faces[0].nodes if n.kind=='triggered_ability')
        for target in ('observer','friendly','opponent'):
            context={'card':'unrelated-damage-source','target':target,'combat':False,'target_kind':'permanent'}
            expected=target=='observer'
            for event,condition,zone in ((child.event.value,child.event_condition,child.active_zone),(node.event,node.event_condition,node.active_zone)):
                self.assertEqual(expected,semantic_event_matches(host,SimpleNamespace(event=event,event_condition=condition,active_zone=zone),
                    source,'damage.dealt',context))

    def test_independent_damage_child_keeps_source_identity_in_event_and_predicate(self):
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding
        from quorune.rules.event_subscriptions import FixedEventSubscriptionSet
        from quorune.trigger_discovery import semantic_event_matches
        from types import SimpleNamespace
        registry=load_default_capability_registry()
        body='draw a card.'
        child=fixed_counter_trigger_binding('Whenever this creature deals combat damage to a player, '+body,card_name='Fixture')
        self.assertEqual('damage.dealt.self',child.event.value)
        combined=compile_oracle_card(record('When this creature enters and whenever this creature deals combat damage to a player, '+body,
            'Creature — Human'),capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',combined.status,combined.to_dict())
        node=next(n for n in combined.faces[0].nodes if n.kind=='triggered_ability')
        subscriptions=FixedEventSubscriptionSet.from_condition(node.event_condition)
        self.assertEqual(2,len(subscriptions.subscriptions))
        host=SimpleNamespace(active_seats=tuple('ABCD'),state=SimpleNamespace(cards={},active_player='A'))
        from quorune.model import CardInstance
        source=CardInstance(object_id='source',ref='actual-source',oracle_id='fixture:dual-source',
            printed_name='Fixture',owner='A',controller='A',zone='battlefield')
        standalone=SimpleNamespace(event=child.event.value,event_condition=child.event_condition,active_zone=child.active_zone)
        program=SimpleNamespace(event=node.event,event_condition=node.event_condition,active_zone=node.active_zone)
        cases=(('actual-source',True,'player',True),('friendly-other',True,'player',False),
               ('opponent-other',True,'player',False),('same-name-other',True,'player',False),
               ('actual-source',False,'player',False),('actual-source',True,'permanent',False))
        for card,combat,target_kind,expected in cases:
            context={'card':card,'combat':combat,'target_kind':target_kind,'source_controller':'A' if card!='opponent-other' else 'B'}
            self.assertEqual(expected,semantic_event_matches(host,standalone,source,'damage.dealt',context),context)
            self.assertEqual(expected,semantic_event_matches(host,program,source,'damage.dealt',context),context)

    def test_fixed_numeric_existence_condition_has_explicit_timing_and_closed_value(self):
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.compiler.public_state_queries import fixed_public_state_condition
        from quorune.continuous_conditions import FixedPublicStateConditionSpec
        registry = load_default_capability_registry()
        text = 'When this creature enters, if you control a creature with power 4 or greater, draw a card.'
        ir = compile_oracle_card(record(text, 'Creature — Human'), capability_registry=registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        condition = fixed_public_state_condition('you control a creature with power 4 or greater', source_name='Fixture')
        self.assertEqual(5, condition.schema_version)
        self.assertEqual(condition, FixedPublicStateConditionSpec.from_dict(condition.to_dict()))
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row = next(c for c in raw['capabilities'] if c['id'] == 'condition.creature.fixed_numeric_exists')
        row.update(status='blocked', blockers=['Independent numeric-condition dependency mutation'])
        from quorune.rules.capabilities import CapabilityRegistry
        altered = CapabilityRegistry(raw)
        altered.mark_evidence_verified(registry.evidence_fingerprint)
        self.assertNotEqual('exact', compile_oracle_card(record(text, 'Creature — Human'), capability_registry=altered).status)
        for value in ('X', '-1', 'four and this creature gets +1/+1'):
            self.assertIsNone(fixed_public_state_condition('you control a creature with power ' + value + ' or greater', source_name='Fixture'))
        # No static characteristic condition may read itself through final P/T.
        static = 'As long as you control a creature with power 4 or greater, this creature gets +1/+1.'
        self.assertNotEqual('exact', compile_oracle_card(record(static, 'Creature — Human'), capability_registry=registry).status)

    def test_numeric_existence_schema_rejects_malformed_values_and_kills_comparison_mutant(self):
        from quorune.compiler.public_state_queries import fixed_public_state_condition
        from quorune.continuous_conditions import FixedPublicStateConditionSpec, FixedPublicStateConditionError
        condition = fixed_public_state_condition('you control a creature with power 4 or greater', source_name='Fixture')
        for change in ({'schema_version': 2}, {'amount': True}, {'quantity': {}}, {'predicate': {**condition.predicate.to_dict(), 'controller': '$controller'}}):
            with self.assertRaises(FixedPublicStateConditionError):
                FixedPublicStateConditionSpec.from_dict({**condition.to_dict(), **change})
        from quorune.target_numeric import TargetNumericCharacteristicSpec
        with patch.object(TargetNumericCharacteristicSpec, 'permits', return_value=True):
            with self.assertRaises(AssertionError):
                self.assertFalse(condition.predicate.numeric_characteristic.permits(power=3, toughness=3))

    def test_numeric_existence_unavailable_stats_fail_closed_and_phased_objects_do_not_count(self):
        from types import SimpleNamespace
        from quorune.compiler.public_state_queries import fixed_public_state_condition
        from quorune.continuous_conditions import FixedPublicStateConditionError
        from quorune.card_programs.runtime import fixed_public_state_condition_matches
        from quorune.resolution_conditions import ResolutionConditionBinding
        from quorune.object_query import ObjectQueryResult
        condition = fixed_public_state_condition('you control a creature with power 4 or greater', source_name='Fixture')
        candidate = SimpleNamespace(object_id='one')
        state = SimpleNamespace(players={'A': SimpleNamespace(zones={'battlefield':['one'],'hand':[],'graveyard':[]},life=40,in_game=True)}, cards={'one':candidate},turn_order=['A'],turn_sequence=1,active_player='A')
        row = ObjectQueryResult(object_id='one',ref='one',printed_name='Fixture',owner='A',controller='A',zone='battlefield',types=('creature',),effective_power=None,effective_toughness=2)
        with self.assertRaises(FixedPublicStateConditionError):
            fixed_public_state_condition_matches(state, ResolutionConditionBinding(controller='A'), condition, public_object_resolver=lambda _: row, quantity_resolver=None, require_available=True)
        self.assertFalse(fixed_public_state_condition_matches(state, ResolutionConditionBinding(controller='A'), condition, public_object_resolver=lambda _: replace(row,effective_power=8,phased_out=True), quantity_resolver=None, require_available=True))

    def test_independent_dual_event_subscription_keeps_each_condition(self):
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.event_subscriptions import FixedEventSubscriptionSet
        text = 'When this enchantment enters and whenever you cast a spell with mana value 5 or greater, draw a card.'
        ir = compile_oracle_card(record(text, 'Enchantment'), capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = next(n for n in ir.faces[0].nodes if n.kind == 'triggered_ability')
        subscriptions = FixedEventSubscriptionSet.from_condition(node.event_condition)
        self.assertEqual({'permanent.enter','spell.cast'}, {r.event.removesuffix('.self') for r in subscriptions.subscriptions})
        self.assertEqual(2, len(subscriptions.subscriptions))
        self.assertIn('fixed_multi_event_subscription', node.runtime_coverage)
        self.assertIn('trigger.subscription.fixed_multi_event', node.capability_dependencies)

    def test_independent_dual_event_rejects_nested_or_unrepresented_prefixes_and_mutant(self):
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.compiler import fixed_public_multi_event_bindings as owner
        registry = load_default_capability_registry()
        for text in (
            'When this enchantment enters and whenever a player rewrites history, draw a card.',
            'When this enchantment enters and whenever you cast a spell and whenever you gain life, draw a card.',
            'When this creature enters and whenever another creature enters, draw a card.',
            'When you cycle this card and when this creature dies, draw a card.',
        ):
            self.assertNotEqual('exact', compile_oracle_card(record(text, 'Creature — Human'), capability_registry=registry).status)
        with patch.object(owner, 'fixed_public_multi_event_binding_spec', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_independent_dual_event_subscription_keeps_each_condition()

    def test_qualified_zone_event_pair_preserves_each_occurrence_predicate(self):
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.event_subscriptions import FixedEventSubscriptionSet
        text = 'Whenever another creature you control enters or dies, draw a card.'
        ir = compile_oracle_card(record(text, 'Enchantment'), capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = next(n for n in ir.faces[0].nodes if n.kind == 'triggered_ability')
        subscriptions = FixedEventSubscriptionSet.from_condition(node.event_condition)
        self.assertEqual({'permanent.enter','creature.dies'}, {r.event for r in subscriptions.subscriptions})
        for row in subscriptions.subscriptions:
            condition = str(row.condition)
            self.assertIn('$source.controller', condition)
            self.assertIn('$source.ref', condition)
            self.assertIn('creature', condition)

    def test_qualified_event_pair_unknown_predicate_and_parser_omission_fail_closed(self):
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.compiler import fixed_public_multi_event_bindings as owner
        registry = load_default_capability_registry()
        for text in (
            'Whenever another creature you control with an unknown quality enters or dies, draw a card.',
            'Whenever one or more creatures you control enter or die, draw a card.',
            'Whenever another creature you control enters, dies, or attacks, draw a card.',
        ):
            self.assertNotEqual('exact', compile_oracle_card(record(text, 'Enchantment'), capability_registry=registry).status)
        with patch.object(owner, 'fixed_public_multi_event_binding_spec', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_qualified_zone_event_pair_preserves_each_occurrence_predicate()

    def test_bare_subtype_condition_retains_permanent_not_creature_domain(self):
        # CR 109.2: a bare subtype denotes battlefield permanents, including Kindred.
        from quorune.compiler.public_state_queries import fixed_public_state_condition
        bare = fixed_public_state_condition("you control a Faerie", source_name="Generic Condition")
        creature = fixed_public_state_condition("you control a Faerie creature", source_name="Generic Condition")
        self.assertEqual(("faerie",), bare.quantity.query.subtypes_all)
        self.assertEqual((), bare.quantity.query.types_all)
        self.assertEqual(("creature",), creature.quantity.query.types_all)

    def test_derived_conditional_results_remain_residual_without_timing_proof(self):
        examples = (
            "If you control an artifact, you gain life equal to this creature's power.",
            "If you control an artifact, draw a card for each creature you control.",
            "If you control an artifact, this creature gets +1/+1 until end of turn.",
            "If you control an artifact, draw a card at the beginning of the next turn's upkeep.",
            "Draw a card at the beginning of the next turn's upkeep. If you control an artifact, draw a card.",
        )
        for text in examples:
            with self.subTest(text=text):
                compiled = _reviewed_effect_template(text, card_name="Generic Condition", source_is_permanent=True)
                self.assertIsNone(compiled[0], "Derived conditional results are outside the fixed-result boundary")
        # The existing unconditional quantity owners remain available.
        for body in ("You gain life equal to this creature's power.", "Draw a card for each creature you control."):
            self.assertIsNotNone(_reviewed_effect_template(body, card_name="Generic Condition", source_is_permanent=True)[0])

    def test_condition_uses_real_spell_trigger_and_activation_shells(self):
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card

        registry = load_default_capability_registry()
        for text, type_line, expected_kind in (
            ("Draw a card. If you control an artifact, you gain 3 life.", "Instant", "spell_ability"),
            ("When this creature enters, draw a card. If you control an artifact, you gain 3 life.", "Creature — Wizard", "triggered_ability"),
            ("{T}: Draw a card. If you control an artifact, you gain 3 life.", "Creature — Wizard", "activated_ability"),
            ("{1}, Sacrifice this artifact: Draw a card. If you control an artifact, you gain 3 life.", "Artifact", "activated_ability"),
        ):
            with self.subTest(text=text):
                compiled = compile_oracle_card(record(text, type_line), capability_registry=registry, capability_profile="commander_review")
                self.assertEqual("exact", compiled.status, compiled.material_residuals)
                node = next(node for face in compiled.faces for node in face.nodes if node.kind == expected_kind)
                self.assertIn(RESOLUTION_CONDITION_CAPABILITY, node.capability_dependencies)

    def test_condition_keeps_printed_order_and_child_capability_shapes(self):
        examples = (
            ("Destroy target creature. If a creature died this turn, draw a card.", "destroy", "draw"),
            ("Draw a card. If you have 5 or less life, you gain 3 life.", "draw", "life"),
            ("Create a Treasure token. If you control an artifact, draw a card.", "create_token", "draw"),
            ("Draw a card. If you control a blue creature, draw a card, then discard a card.", "draw", "draw"),
        )
        for text, prefix_op, child_op in examples:
            with self.subTest(text=text):
                template, effects, schema, mechanics = _reviewed_effect_template(text, card_name="Generic Condition")
                self.assertEqual(template, "fixed-resolution-public-condition-v1")
                self.assertEqual(effects[0]["op"], prefix_op)
                self.assertEqual(effects[-1]["op"], "apply_if_public_condition")
                self.assertEqual(effects[-1]["effects"][0]["op"], child_op)
                dependencies = capability_dependencies_for_node(effects=effects, target_schema=schema, mechanic_ids=mechanics)
                self.assertIn(RESOLUTION_CONDITION_CAPABILITY, dependencies)
                malformed = copy.deepcopy(effects)
                malformed[-1]["condition"]["amount"] = True
                self.assertEqual(capability_dependencies_for_node(effects=malformed, target_schema=schema, mechanic_ids=mechanics), ())
                wrong = copy.deepcopy(effects)
                wrong[-1]["effects"][0]["op"] = "unrepresented_operation"
                self.assertEqual(capability_dependencies_for_node(effects=wrong, target_schema=schema, mechanic_ids=mechanics), ())

    def test_conditional_exclusions_are_fully_consumed(self):
        for text in (
            "Draw a card. If you control an artifact, draw two cards instead.",
            "Draw a card. If you control an artifact, destroy target creature.",
            "Draw a card. If this creature has flying, draw a card.",
            "Draw a card. If you control another creature, draw a card.",
            "Draw a card. If you control an artifact, draw a card. Otherwise, gain 3 life.",
            "Draw a card. If you control an artifact, if you control a creature, draw a card.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(resolution_condition_template(
                    text, source_name="Generic Condition",
                    compile_component=lambda body: _reviewed_effect_template(body, card_name="Generic Condition"),
                ))

    def test_handler_binds_controller_and_rejects_unavailable_facts(self):
        from quorune.semantic_choices.resolution_condition import PublicResolutionConditionHandler
        from quorune.semantic_choices.context import SemanticChoiceContext, SnapshotSemanticChoiceQuery
        from quorune.util import stable_json
        from quorune.semantic_choices.model import SemanticChoiceError

        effect = _reviewed_effect_template("If you have 5 or less life, draw a card.", card_name="Generic Condition")[1][0]
        effect = {**effect, "player": "A"}
        effect["effects"][0]["player"] = "A"
        context = SemanticChoiceContext(
            actor="A", stack_ref="stack:condition", stack_controller="A", stack_label="Condition",
            source_ref="old-source", card_ref=None, semantic_program_id="generic-condition", semantic_program_version=1,
            query=SnapshotSemanticChoiceQuery(seat_order=("A", "B", "C", "D"), active_order=("A", "B", "C", "D")),
        )
        handler = PublicResolutionConditionHandler()
        with self.assertRaisesRegex(ValueError, "not materialized"):
            handler.prepare(effect, context)
        for matches in (False, True):
            query = replace(context.query, resolution_condition_facts={stable_json(effect["condition"]): matches})
            preparation = handler.prepare(effect, replace(context, query=query))
            self.assertIsNone(preparation.request)
            self.assertEqual(int(matches), len(preparation.auto_continue.prepend_effects))
        with self.assertRaisesRegex(SemanticChoiceError, "resolving controller"):
            handler.prepare({**effect, "player": "B"}, context)

    def test_canonical_query_distinguishes_unavailable_history_from_false(self):
        from types import SimpleNamespace
        from quorune.card_programs.runtime import fixed_public_state_condition_matches
        from quorune.compiler.public_state_queries import fixed_public_state_condition
        from quorune.resolution_conditions import ResolutionConditionBinding
        from quorune.continuous_conditions import FixedPublicStateConditionError

        state = SimpleNamespace(players={"A": SimpleNamespace(life=40, in_game=True, zones={"hand": [], "graveyard": []})},
            cards={}, active_player="A", turn_sequence=1, turn_order=("A",), turn_history=None)
        condition = fixed_public_state_condition("a creature died this turn", source_name="Generic Condition")
        binding = ResolutionConditionBinding(controller="A")
        self.assertFalse(fixed_public_state_condition_matches(state, binding, condition,
            public_object_resolver=None, quantity_resolver=None))
        with self.assertRaisesRegex(FixedPublicStateConditionError, "unavailable"):
            fixed_public_state_condition_matches(state, binding, condition,
                public_object_resolver=None, quantity_resolver=None, require_available=True)


class IndependentDualSourceEventRuntimeTests(unittest.TestCase):
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _Actions
    add=_Actions.add
    ready=_Actions.ready
    checkpoint=_Actions.checkpoint
    resolve=_Actions.resolve
    replay=_Actions.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'dual-source.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
                                ROOT/'tests/fixtures/independent-dual-source-events.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Generic Dual Source Deck',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def session(self,seed):
        from quorune.semantics import SemanticRegistry
        session=self._Actions.session(self,seed);engine=session.engine
        engine.semantics=SemanticRegistry(include_builtin_packs=False)
        from quorune.oracle_ir import register_generated_programs
        register_generated_programs(self.db,engine.semantics,tuple(self.db.iter_cards()),trust_level='provisional',
            capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        return session

    def cast_observer(self,session):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        engine=session.engine;source=self.add(engine,'Generic Dual Source Observer',zone='hand',ref='first-source')
        action=self.ready(session,source,{'U':1,'R':1})
        program=compile_best_available_card_program(self.db,self.db.lookup(source.printed_name),semantic_registry=engine.semantics,
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'])
        before_hand=len(engine.state.players['A'].zones['hand']);self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(before_hand,len(engine.state.players['A'].zones['hand']))
        self.assertEqual(1,sum(event.code=='stack.trigger' and event.details.get('source')==source.ref for event in engine.state.events))
        self.replay(session)
        return engine.state.cards[source.object_id]

    def finish_combat(self,session,*,pending_reload=False):
        reloaded=False
        for _ in range(80):
            engine=session.engine;decision=engine.state.pending_decision
            if not engine.state.stack and (engine.state.phase=='postcombat_main' or engine.state.step=='end_combat'):return session
            if pending_reload and not reloaded and any(item.context.get('event')=='damage.dealt' for item in engine.state.stack):
                session=self.replay(session,load=True);reloaded=True;continue
            self.assertIsNotNone(decision)
            if decision.kind=='priority':response={'action_id':'pass'}
            elif decision.kind=='combat.blockers':response={'action_id':'block','blocks':{}}
            elif decision.kind=='trigger.order':
                batch=engine.state.pending_trigger_batches[0]
                response={'action_id':'order','triggers':[item.ref for item in batch.items]}
            else:self.fail('Unexpected dual-source combat decision '+decision.kind)
            result=session.act(session.pending_principals()[0],response)
            self.assertTrue(result.ok,result.summary)
        self.fail('Dual-source combat did not finish')

    def test_trusted_dual_event_draws_only_for_each_actual_source_in_real_combat(self):
        from quorune.model import CombatState
        for index,mode in enumerate(('source','friendly','opponent','same_name')):
            with self.subTest(mode=mode):
                session=self.session(390001+index);engine=session.engine
                source=self.cast_observer(session)
                other=self.add(engine,'Generic Dual Source Observer' if mode=='same_name' else 'Generic Bound Body',
                    seat='B' if mode=='opponent' else 'A',ref='second-object')
                other.acquired_control_turn_count=-1
                attacker=source if mode=='source' else other
                active=attacker.controller;defender='C'
                engine.permissions.invalidate_current();engine.state.pending_decision=None
                engine.state.priority_player=None;engine.state.priority_passes=[]
                engine.state.active_player=active;engine.state.phase='combat';engine.state.step='declare_attackers'
                engine.state.phase_index=5;engine.state.combat=CombatState();engine._issue_attackers()
                self.checkpoint(session);hands={seat:len(engine.state.players[seat].zones['hand']) for seat in 'ABCD'}
                before=authoritative_state_hash(session.state)
                rejected=session.act('pilot:D',{'action_id':'attack','attackers':{attacker.ref:defender}})
                self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
                accepted=session.act('pilot:'+active,{'action_id':'attack','attackers':{attacker.ref:defender}})
                self.assertTrue(accepted.ok,accepted.summary)
                session=self.finish_combat(session,pending_reload=mode in {'source','same_name'});engine=session.engine
                expected=1 if mode in {'source','same_name'} else 0
                self.assertEqual(hands['A']+expected,len(engine.state.players['A'].zones['hand']),mode)
                triggers=[event for event in engine.state.events if event.code=='stack.trigger' and event.details.get('source')==source.ref]
                self.assertEqual(1+int(mode=='source'),len(triggers),mode)
                for seat in 'BCD':self.assertEqual(hands[seat],len(engine.state.players[seat].zones['hand']))
                self.replay(session,load=True)

    def test_dual_event_noncombat_damage_is_excluded_through_real_activation(self):
        session=self.session(390010);engine=session.engine
        source=self.cast_observer(session)
        action=self.ready(session,source,{'R':1})
        self.checkpoint(session);hand_before=len(engine.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(hand_before,len(engine.state.players['A'].zones['hand']))
        self.replay(session)

    def test_multiple_source_wound_observers_draw_once_each_after_actual_combat_in_apnap_order(self):
        from quorune.model import CombatState
        session=self.session(390011);engine=session.engine
        first=self.add(engine,'Generic Dual Wound Observer',ref='wound-A')
        second=self.add(engine,'Generic Dual Wound Observer',seat='B',ref='wound-B')
        third=self.add(engine,'Generic Dual Wound Observer',seat='C',ref='wound-C')
        first.acquired_control_turn_count=-1
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.active_player='A';engine.state.started=True;engine.state.phase='combat'
        engine.state.step='declare_attackers';engine.state.phase_index=5;engine.state.combat=CombatState()
        engine._issue_attackers();self.checkpoint(session)
        before={seat:len(engine.state.players[seat].zones['hand']) for seat in 'ABCD'}
        accepted=session.act('pilot:A',{'action_id':'attack','attackers':{first.ref:'B'}})
        self.assertTrue(accepted.ok,accepted.summary)
        for _ in range(40):
            decision=engine.state.pending_decision
            if decision.kind=='combat.blockers':
                accepted=session.act('pilot:B',{'action_id':'block','blocks':{second.ref:first.ref}})
            else:accepted=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(accepted.ok,accepted.summary)
            if len(engine.state.stack)==2 and all(item.context.get('event')=='damage.dealt' for item in engine.state.stack):break
        self.assertEqual(['A','B'],[item.controller for item in engine.state.stack])
        self.assertEqual([first.object_id,second.object_id],[item.source_object_id for item in engine.state.stack])
        resumed=self.replay(session,load=True);resumed=self.finish_combat(resumed);engine=resumed.engine
        for seat in 'ABCD':self.assertEqual(before[seat]+int(seat in 'AB'),len(engine.state.players[seat].zones['hand']))
        private_draws=[event for event in engine.state.events if event.code=='card.draw.private']
        for event in private_draws:
            owner=event.actor
            for seat in set('ABCD')-{owner}:
                packet=json.dumps(resumed.packet('pilot:'+seat,full=True))
                for ref in event.details['objects']:self.assertNotIn(ref,packet)
        self.replay(resumed)

    def test_created_dual_source_trigger_survives_real_ability_removal_response_and_replays(self):
        from quorune.model import CombatState
        session=self.session(390012);engine=session.engine;source=self.cast_observer(session)
        removal=self.add(engine,'Snakeform',zone='hand',ref='source-ability-removal')
        self.add(engine,'Island',ref='removal-island')
        self.add(engine,'Generic Bound Plains',ref='removal-plains-one')
        self.add(engine,'Generic Bound Plains',ref='removal-plains-two')
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.active_player='A';engine.state.phase='combat';engine.state.step='declare_attackers'
        engine.state.phase_index=5;engine.state.combat=CombatState();engine._issue_attackers();self.checkpoint(session)
        before=len(engine.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertTrue(accepted.ok,accepted.summary)
        for _ in range(40):
            if any(item.context.get('event')=='damage.dealt' for item in engine.state.stack):break
            accepted=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(1,len(engine.state.stack))
        action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row.get('card')==removal.ref)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        resumed=self.replay(session,load=True);resumed=self.finish_combat(resumed)
        self.assertEqual(before+1,len(resumed.state.players['A'].zones['hand']))
        self.assertEqual(1,resumed.engine._numeric_stat(source.object_id,'power'))
        self.assertFalse(resumed.engine._effective_ability_fragments(resumed.state.cards[source.object_id]))
        self.replay(resumed)


class ResolutionPublicConditionRuntimeTests(unittest.TestCase):
    # Reuse the real trusted registration, command, checkpoint and replay harness.
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _ActionHarness
    session = _ActionHarness.session
    add = _ActionHarness.add
    ready = _ActionHarness.ready
    checkpoint = _ActionHarness.checkpoint
    resolve = _ActionHarness.resolve
    replay = _ActionHarness.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "resolution-conditions.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/resolution-public-condition-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Generic Condition Deck", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 99),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_original_numeric_entry_condition_uses_current_power_at_both_trigger_boundaries(self):
        # CR 603.4 checks at occurrence and again at resolution. Counters and
        # resolution-created buffs contribute to the current effective power.
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        for mode in ('counter_true', 'foreign_false', 'response_removed'):
            session = self.session(357201 + ('counter_true', 'foreign_false', 'response_removed').index(mode))
            engine = session.engine
            source = self.add(engine, 'Beastbond Outcaster', zone='hand')
            body = self.add(engine, 'Hill Giant', seat='B' if mode == 'foreign_false' else 'A', ref='numeric-condition-body')
            if mode == 'counter_true':
                body.counters['+1/+1'] = 1
            buff = self.add(engine, 'Giant Growth', zone='hand') if mode == 'response_removed' else None
            removal = self.add(engine, 'Murder', zone='hand') if mode == 'response_removed' else None
            program = compile_best_available_card_program(self.db, self.db.lookup('Beastbond Outcaster'), semantic_registry=engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
            self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')['strict_capability_ready'])
            action = self.ready(session, buff or source, {'G': 3, 'B': 2, 'C': 3})
            self.checkpoint(session)
            if buff is not None:
                accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [body.ref], 'pay': 'auto'})
                self.assertTrue(accepted.ok, accepted.summary)
                self.resolve(session)
                self.assertEqual(6, engine._numeric_stat(body.object_id, 'power'))
                while session.pending_principals()[0] != 'pilot:A':
                    self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
                action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == source.ref)
            draws = len(session.state.players['A'].zones['hand'])
            accepted = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
            self.assertTrue(accepted.ok, accepted.summary)
            if removal is not None:
                for _ in range(8):
                    if any(item.kind == 'triggered_ability' for item in session.state.stack):
                        break
                    self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
                self.assertTrue(any(item.kind == 'triggered_ability' for item in session.state.stack))
                while session.pending_principals()[0] != 'pilot:A':
                    self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
                response = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == removal.ref)
                accepted = session.act('pilot:A', {'action_id': response['id'], 'targets': [body.ref], 'pay': 'auto'})
                self.assertTrue(accepted.ok, accepted.summary)
            self.resolve(session)
            expected = draws if mode == 'counter_true' else draws - 2 if removal is not None else draws - 1
            self.assertEqual(expected, len(session.state.players['A'].zones['hand']))
            projected = json.dumps(session.packet('pilot:D', full=True)['state'])
            for oid in session.state.players['A'].zones['hand']:
                if oid not in {source.object_id, buff.object_id if buff else '', removal.object_id if removal else ''}:
                    self.assertNotIn(session.state.cards[oid].ref, projected)
            self.replay(session, load=True)

    def test_numeric_resolution_uses_current_stats_and_condition_omission_mutant_is_killed(self):
        session = self.session(357204)
        engine = session.engine
        body = self.add(engine, 'Hill Giant', ref='resolution-number-body')
        body.counters['+1/+1'] = 1
        source = self.add(engine, 'Generic Numeric Resolution Draw', zone='hand')
        action = self.ready(session, source, {'G': 1})
        self.checkpoint(session)
        before = len(session.state.players['A'].zones['hand'])
        accepted = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(before, len(session.state.players['A'].zones['hand']))
        self.replay(session, load=True)
        source = self.add(engine, 'Generic Numeric Resolution Draw', zone='hand', ref='mutated-numeric-condition')
        action = self.ready(session, source, {'G': 1})
        before = len(session.state.players['A'].zones['hand'])
        accepted = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        with patch.object(engine, '_fixed_public_state_condition_holds', return_value=False):
            self.resolve(session)
        with self.assertRaises(AssertionError):
            self.assertEqual(before, len(session.state.players['A'].zones['hand']))

    def test_original_beanstalk_entry_and_own_large_spell_keep_distinct_subscriptions_and_replay(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        session = self.session(357301)
        engine = session.engine
        source = self.add(engine, 'Up the Beanstalk', zone='hand')
        small = self.add(engine, 'Hill Giant', zone='hand', ref='small-spell')
        large = self.add(engine, 'Craw Wurm', zone='hand', ref='large-spell')
        foreign = self.add(engine, "Jace's Ingenuity", seat='B', zone='hand', ref='foreign-large-spell')
        program = compile_best_available_card_program(self.db, self.db.lookup('Up the Beanstalk'), semantic_registry=engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')['strict_capability_ready'])
        action = self.ready(session, source, {'G': 5, 'R': 1, 'C': 12})
        session.state.players['B'].mana_pool.update({'U': 2, 'C': 3})
        self.checkpoint(session)
        before = len(session.state.players['A'].zones['hand'])
        accepted = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(before, len(session.state.players['A'].zones['hand']))
        for card, expected_draw in ((small, 0), (large, 1)):
            while session.pending_principals()[0] != 'pilot:A':
                self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
            action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == card.ref)
            before = len(session.state.players['A'].zones['hand'])
            accepted = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
            self.assertTrue(accepted.ok, accepted.summary)
            self.resolve(session)
            self.assertEqual(before - 1 + expected_draw, len(session.state.players['A'].zones['hand']))
        while session.pending_principals()[0] != 'pilot:B':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == foreign.ref)
        before = len(session.state.players['A'].zones['hand'])
        accepted = session.act('pilot:B', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(before, len(session.state.players['A'].zones['hand']))
        projected = json.dumps(session.packet('pilot:D', full=True)['state'])
        for oid in session.state.players['A'].zones['hand']:
            self.assertNotIn(session.state.cards[oid].ref, projected)
        self.replay(session, load=True)

    def test_qualified_pair_actual_entry_and_death_draw_only_for_source_controller(self):
        session = self.session(357302)
        engine = session.engine
        source = self.add(engine, 'Generic Qualified Entry Death Draw', zone='hand')
        own = self.add(engine, 'Hill Giant', zone='hand', ref='pair-own')
        foreign = self.add(engine, 'Generic Bound Body', seat='B', ref='pair-foreign')
        removal = self.add(engine, 'Murder', zone='hand')
        action = self.ready(session, source, {'G': 1, 'R': 1, 'B': 2, 'C': 4})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        while session.pending_principals()[0] != 'pilot:A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == own.ref)
        before = len(session.state.players['A'].zones['hand'])
        accepted = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(before, len(session.state.players['A'].zones['hand']))
        while session.pending_principals()[0] != 'pilot:A':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        action = next(a for a in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a.get('card') == removal.ref)
        before = len(session.state.players['A'].zones['hand'])
        accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [own.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(before, len(session.state.players['A'].zones['hand']))
        self.replay(session, load=True)
        before = len(session.state.players['A'].zones['hand'])
        engine.apply_effect({'op': 'destroy', 'card': foreign.ref}, actor='A')
        self.resolve(session)
        self.assertEqual(before, len(session.state.players['A'].zones['hand']))

    def test_trusted_prefix_changes_condition_before_private_result_and_replay(self):
        session = self.session(4821001)
        engine = session.engine
        source = self.add(engine, "Generic Condition Treasure Draw", zone="hand")
        action = self.ready(session, source, {"G": 1})
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:B", {"action_id": action["id"], "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.checkpoint(session)
        draws = sum(event.code == "card.draw" for event in session.state.events)
        hand_before = set(engine.state.players["A"].zones["hand"])
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        tokens = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(1, len(tokens))
        self.assertEqual(1, sum(event.code == "card.draw" for event in session.state.events) - draws)
        drawn_ids = set(engine.state.players["A"].zones["hand"]) - hand_before
        self.assertEqual(1, len(drawn_ids))
        drawn_ref = engine.state.cards[next(iter(drawn_ids))].ref
        self.assertIn(drawn_ref, json.dumps(session.packet("pilot:A", full=True)))
        for seat in "BCD":
            self.assertNotIn(drawn_ref, json.dumps(session.packet(f"pilot:{seat}", full=True)))
        self.replay(session, load=True)
        # A condition cached before the prefix would miss the draw.
        source = self.add(engine, "Generic Condition Treasure Draw", zone="hand", ref="mutant-condition")
        action = self.ready(session, source, {"G": 1})
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        draws = sum(event.code == "card.draw" for event in session.state.events)
        with patch.object(engine, "_fixed_public_state_condition_holds", return_value=False):
            self.resolve(session)
        self.assertEqual(0, sum(event.code == "card.draw" for event in session.state.events) - draws)

    def test_trusted_death_prefix_updates_history_before_condition_and_replays(self):
        session = self.session(4821002)
        engine = session.engine
        source = self.add(engine, "Generic Condition Death Draw", zone="hand")
        target = self.add(engine, "Generic Bound Body", seat="B", ref="death-recipient")
        action = self.ready(session, source, {"B": 1})
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        self.checkpoint(session)
        draws = sum(event.code == "card.draw" for event in session.state.events)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", engine.state.cards[target.object_id].zone)
        self.assertEqual(1, sum(event.code == "card.draw" for event in session.state.events) - draws)
        self.assertEqual(1, sum(event.kind == "creature_died" for event in engine.state.turn_history.events))
        self.replay(session, load=True)

    def test_private_conditional_scry_resumes_without_repeating_prefix(self):
        session = self.session(4821003)
        engine = session.engine
        self.add(engine, "Generic Bound Mentor", ref="artifact-condition")
        source = self.add(engine, "Generic Condition Draw Scry", zone="hand")
        action = self.ready(session, source, {"U": 1})
        self.checkpoint(session)
        draws = sum(event.code == "card.draw" for event in session.state.events)
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        decision = self.resolve(session)
        self.assertEqual("semantic.choice", decision.kind)
        self.assertEqual(1, sum(event.code == "card.draw" for event in engine.state.events) - draws)
        refs = tuple(engine.state.cards[object_id].ref for object_id in reversed(engine.state.players["A"].zones["library"][-2:]))
        for seat in "BCD":
            self.assertTrue(all(ref not in json.dumps(session.packet(f"pilot:{seat}", full=True)) for ref in refs))
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": "choose", "cards": {"top": [refs[0]], "bottom": []}})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": "choose", "cards": {"top": list(refs), "bottom": []}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, sum(event.code == "card.draw" for event in session.state.events) - draws)
        self.replay(session, load=True)

    def test_trusted_activation_checks_resolution_controller_and_false_result(self):
        session = self.session(4821004)
        engine = session.engine
        source = self.add(engine, "Generic Condition Activated Draw")
        action = self.ready(session, source, {})
        self.checkpoint(session)
        life_before = engine.state.players["A"].life
        draws = sum(event.code == "card.draw" for event in session.state.events)
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertTrue(engine.state.cards[source.object_id].tapped)
        self.assertEqual(life_before, engine.state.players["A"].life)
        self.assertEqual(1, sum(event.code == "card.draw" for event in session.state.events) - draws)
        self.replay(session, load=True)

    def test_trusted_sacrificed_source_uses_ability_controller_not_graveyard_owner(self):
        session = self.session(4821005)
        engine = session.engine
        source = self.add(engine, "Generic Condition Sacrificed Draw")
        source.owner = "B"
        self.add(engine, "Generic Bound Mentor", ref="resolver-artifact")
        action = self.ready(session, source, {"C": 1})
        self.checkpoint(session)
        life_before = engine.state.players["A"].life
        draws = sum(event.code == "card.draw" for event in session.state.events)
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        departed = engine.state.cards[source.object_id]
        self.assertEqual("graveyard", departed.zone)
        self.assertEqual("B", departed.controller)
        self.assertEqual("A", engine.state.stack[-1].controller)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(life_before + 3, engine.state.players["A"].life)
        self.assertEqual(1, sum(event.code == "card.draw" for event in session.state.events) - draws)
        self.replay(session, load=True)

    def test_existing_reanimation_leaf_composes_with_token_result_and_replays(self):
        session = self.session(4821006)
        engine = session.engine
        source = self.add(engine, "Generic Composed Return Food", zone="hand")
        target = self.add(engine, "Generic Bound Body", zone="graveyard", ref="return-recipient")
        incarnation = target.logical_object_id
        action = self.ready(session, source, {"W": 1})
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        returned = engine.state.cards[target.object_id]
        self.assertEqual("battlefield", returned.zone)
        self.assertNotEqual(incarnation, returned.logical_object_id)
        self.assertEqual("A", returned.controller)
        foods = [card for card in engine.state.cards.values() if card.is_token and card.zone == "battlefield"
                 and "food" in engine._type_parts(engine._effective_card_data(card)["type_line"])[1]]
        self.assertEqual(1, len(foods))
        self.replay(session, load=True)

    def test_trusted_subtype_condition_counts_noncreature_kindred_and_replays(self):
        session = self.session(4821007)
        engine = session.engine
        self.add(engine, "Generic Condition Kindred Faerie", ref="kindred-faerie")
        source = self.add(engine, "Generic Condition Faerie Draw", zone="hand")
        target = self.add(engine, "Generic Bound Body", ref="faerie-test-target")
        action = self.ready(session, source, {"B": 1})
        self.checkpoint(session)
        draws = sum(event.code == "card.draw" for event in session.state.events)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, engine._numeric_stat(target.object_id, "power"))
        self.assertEqual(1, sum(event.code == "card.draw" for event in session.state.events) - draws)
        self.replay(session, load=True)


if __name__ == "__main__":
    unittest.main()

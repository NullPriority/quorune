from __future__ import annotations

"""CR601.2c/f: announced targets determine one source-face generic reduction."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import FrozenMap
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.targeted_spell_costs import (
    TargetedSpellCostSpec,TargetedCostSubject,SELF_TARGET_REDUCTION_CAPABILITY,validate_targeted_spell_cost_fact,
)
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_compound_damage_recipients as controls


class SelfTargetReductionCompilerTests(unittest.TestCase):
    def test_announced_count_recognizer_safely_rejects_an_unrelated_composition_view(self):
        from types import SimpleNamespace
        from quorune.rules.fixed_homogeneous_target_set_capability_shapes import is_closed_announced_target_set_program
        view=SimpleNamespace(capability_dependencies=(),cost_schema=None,coverage=(),effects=(),event_condition=None,
            provenance={'template_id':'fixed-draw-v1'},target_schema=None)
        self.assertFalse(is_closed_announced_target_set_program(view))
        self.assertFalse(is_closed_announced_target_set_program(SimpleNamespace()))

    def test_component_rejects_malformed_public_predicate_leaf(self):
        from quorune.compiler.targeted_spell_costs import self_target_reduction_template
        from quorune.semantic_runtime.targeted_spell_costs import TargetedSpellCostHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        descriptor=self_target_reduction_template('This spell costs {3} less to cast if it targets a tapped creature.')[1]
        modifier=descriptor['modifier'];schema=modifier['target_schema']
        bad={**descriptor,'modifier':{**modifier,'target_schema':{**schema,
            'state_predicate':{**schema['state_predicate'],'tapped':'yes'}}}}
        with self.assertRaises(SemanticNodeError):TargetedSpellCostHandler().validate(bad)

    def test_version_two_reduction_and_captured_fact_are_closed_without_reinterpreting_old_taxes(self):
        old={'schema_version':1,'subject':'source','generic':2}
        self.assertEqual(old,TargetedSpellCostSpec.from_dict(old).to_dict())
        # Use the existing canonical direct-target predicate rather than inventing its wire fields.
        from quorune.compiler.direct_target import direct_permanent_target_spec
        schema=direct_permanent_target_spec('target tapped creature').to_target_schema()
        spec=TargetedSpellCostSpec(TargetedCostSubject.SPELL_TARGET,-3,2,FrozenMap(schema))
        self.assertEqual(spec,TargetedSpellCostSpec.from_dict(spec.to_dict()))
        for changed in ({'generic':True},{'generic':0},{'schema_version':1},
                {'target_schema':{**schema,'unknown':True}},{'target_schema':{**schema,'count':2}},
                {'target_schema':{**schema,'zones':['hand']}}):
            with self.assertRaises(ValueError):TargetedSpellCostSpec.from_dict({**spec.to_dict(),**changed})
        source={'component_id':'ONE','source_ref':'CARD','source_identity':'INCARNATION','controller':'A','generic':-3}
        fact={'schema_version':2,'generic':0,'generic_reduction':3,'sources':[source]}
        self.assertEqual(0,validate_targeted_spell_cost_fact(fact))
        for changed in ({'generic_reduction':2},{'generic_reduction':True},{'sources':[source,source]},
                {'schema_version':1},{'generic':-3}):
            with self.assertRaises(ValueError):validate_targeted_spell_cost_fact({**fact,**changed})

    def test_compiler_requires_new_authority_and_keeps_unsupported_costs_residual(self):
        caps=load_default_capability_registry()
        text='This spell costs {3} less to cast if it targets a tapped creature.\nExile target creature.'
        ir=compile_oracle_card(record(text,'Instant'),capability_registry=caps,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=next(node for node in ir.faces[0].nodes if SELF_TARGET_REDUCTION_CAPABILITY in node.capability_dependencies)
        self.assertEqual('all',node.active_zone)
        self.assertEqual(-3,node.handlers[0]['modifier']['generic'])
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(row for row in raw['capabilities'] if row['id']==SELF_TARGET_REDUCTION_CAPABILITY).update(status='blocked',blockers=['withdrawn'])
        self.assertNotEqual('exact',compile_oracle_card(record(text,'Instant'),capability_registry=CapabilityRegistry(raw),capability_profile='commander_review').status)
        for clause in ('This spell costs {1}{U} less to cast if it targets a tapped creature.',
                'This spell costs {3} less to cast if it targets a creature whose controller has eight or more cards in their graveyard.',
                'This spell costs {3} less to cast if it targets a card in a private hand.'):
            self.assertNotEqual('exact',compile_oracle_card(record(clause+'\nExile target creature.','Instant'),
                capability_registry=caps,capability_profile='commander_review').status,clause)

    def test_public_card_and_spell_predicates_keep_their_own_domains(self):
        from quorune.compiler.targeted_spell_costs import _self_reduction_target_schema
        grave=_self_reduction_target_schema('creature card with mana value 3 or less')
        self.assertEqual(['graveyard'],grave['zones']);self.assertEqual(3,grave['mana_value_max'])
        self.assertEqual(grave,_self_reduction_target_schema('CREATURE CARD WITH MANA VALUE 3 OR LESS'))
        creature=_self_reduction_target_schema('creature spell')
        self.assertEqual(['stack'],creature['zones']);self.assertEqual(['creature'],creature['types_any'])
        blue=_self_reduction_target_schema('blue spell')
        self.assertEqual(['stack'],blue['zones']);self.assertEqual(['U'],blue['colors_any'])
        for unsupported in ('card in a private hand','spell or ability that targets a creature',
                'creature card with mana value X or less','spell with the chosen color'):
            self.assertIsNone(_self_reduction_target_schema(unsupported),unsupported)


class SelfTargetReductionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.CompoundDamageRuntimeTests.session
    add=controls.CompoundDamageRuntimeTests.add
    prepare=controls.CompoundDamageRuntimeTests.prepare
    checkpoint=controls.CompoundDamageRuntimeTests.checkpoint
    cast=controls.CompoundDamageRuntimeTests.cast
    resolve=controls.CompoundDamageRuntimeTests.resolve
    replay=controls.CompoundDamageRuntimeTests.replay
    binding=controls.CompoundDamageRuntimeTests.binding
    actions=controls.CompoundDamageRuntimeTests.actions
    action=controls.CompoundDamageRuntimeTests.action
    private_replay=controls.CompoundDamageRuntimeTests.private_replay

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'self-target-reduction.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/self-target-reduction-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Self target reduction',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_original_quicksand_offers_cheaper_tapped_target_and_rejects_cheap_untapped_payment_atomically(self):
        session=self.session(730401);spell=self.add(session.engine,'Quicksand Whirlpool',zone='hand')
        target=self.add(session.engine,'Generic Bound Body',seat='B');target.tapped=True
        other=self.add(session.engine,'Generic Bound Body',seat='C',ref='untapped-discount-counterexample')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'W':1,'C':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,spell)['strict_capability_ready'])
        self.assertTrue(any(row['id'].startswith('cast:'+spell.ref) for row in self.actions(session)))
        action=self.action(session,spell);before=authoritative_state_hash(session.state)
        for principal,ref in (('pilot:B',target.ref),('pilot:A',other.ref)):
            rejected=session.act(principal,{'action_id':action['id'],'targets':[ref],'pay':'auto'})
            self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'A',spell,[target.ref]);item=next(item for item in session.state.stack if item.kind=='spell')
        self.assertEqual(3,item.context['mana_spent_total'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('exile',session.state.cards[target.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[other.object_id].zone)
        self.private_replay(session,private)

    def test_fresh_original_quicksand_kills_target_reduction_omission(self):
        method='test_original_quicksand_offers_cheaper_tapped_target_and_rejects_cheap_untapped_payment_atomically'
        with patch('quorune.semantic_runtime.targeted_spell_costs._self_targeted_reduction_sources',return_value=[]):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_original_savage_stomp_binds_controller_and_subtype_across_both_target_roles(self):
        session=self.session(730402);spell=self.add(session.engine,'Savage Stomp',zone='hand')
        dinosaur=self.add(session.engine,'Colossal Dreadmaw')
        other=self.add(session.engine,'Generic Bound Body',ref='own-nondinosaur')
        enemy=self.add(session.engine,'Colossal Dreadmaw',seat='B',ref='enemy-dinosaur')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'G':1});self.checkpoint(session)
        for card in (spell,dinosaur,enemy):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        action=self.action(session,spell);before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':action['id'],'targets':[other.ref,enemy.ref],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,'A',spell,[dinosaur.ref,enemy.ref]);item=next(item for item in session.state.stack if item.kind=='spell')
        self.assertEqual(1,item.context['mana_spent_total'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.cards[dinosaur.object_id].counters.get('+1/+1',0))
        self.assertEqual(6,session.state.cards[dinosaur.object_id].marked_damage)
        self.assertEqual('battlefield',session.state.cards[dinosaur.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[enemy.object_id].zone)
        self.private_replay(session,private)

    def test_original_town_optional_targets_reduce_once_for_one_or_two_qualifiers(self):
        for count in (1,2):
            session=self.session(730402+count);spell=self.add(session.engine,"This Town Ain't Big Enough",zone='hand')
            own=[self.add(session.engine,'Generic Bound Body',ref='own-town-'+str(i)) for i in range(2)]
            enemy=self.add(session.engine,'Generic Bound Body',seat='B',ref='foreign-town')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'U':1,'C':1});self.checkpoint(session)
            self.assertTrue(self.binding(session,spell)['strict_capability_ready'])
            action=self.action(session,spell);before=authoritative_state_hash(session.state)
            for refs in ([],[enemy.ref]):
                bad=session.act('pilot:A',{'action_id':action['id'],'targets':refs,'pay':'auto'})
                self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
            self.cast(session,'A',spell,[card.ref for card in own[:count]])
            item=next(item for item in session.state.stack if item.kind=='spell');self.assertEqual(2,item.context['mana_spent_total'])
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual(['hand']*count,[session.state.cards[card.object_id].zone for card in own[:count]])
            self.assertEqual('battlefield',session.state.cards[enemy.object_id].zone)
            self.private_replay(session,private)

    def test_original_torch_reduction_changes_payment_without_changing_x_or_counter_prohibition(self):
        session=self.session(730405);spell=self.add(session.engine,'Torch Breath',zone='hand')
        target=self.add(session.engine,'Wind Drake',seat='B')
        other=self.add(session.engine,'Generic Bound Body',seat='C',ref='nonblue-x-target')
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':2})
        self.prepare(session,mana={'R':1,'C':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,spell)['strict_capability_ready'])
        action=self.action(session,spell)
        self.assertEqual(3,max(option['choice_schema']['x']['maximum'] for option in action['cost_options']))
        prices=next(option['target_costs']['prices'] for option in action['cost_options'] if option['choice_schema']['x']['maximum']==3)
        self.assertEqual({1,3},{price['maximum_x'] for price in prices})
        self.assertTrue(any(price['label']=='Choose X up to 3' for price in prices))
        before=authoritative_state_hash(session.state)
        bad=session.act('pilot:A',{'action_id':action['id'],'targets':[other.ref],'x':3,'pay':'auto'})
        self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'x':3,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        item=next(item for item in session.state.stack if item.kind=='spell');self.assertEqual(2,item.context['mana_spent_total'])
        self.cast(session,'B',counter,[item.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[spell.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[counter.object_id].zone)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session,private)

    def test_original_quicksand_cost_stays_locked_after_actual_refocus_untaps_target(self):
        session=self.session(730406);spell=self.add(session.engine,'Quicksand Whirlpool',zone='hand')
        target=self.add(session.engine,'Generic Bound Body',seat='B');target.tapped=True
        response=self.add(session.engine,'Refocus',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':1,'C':1})
        self.prepare(session,mana={'W':1,'C':2});self.checkpoint(session)
        self.assertTrue(self.binding(session,response)['strict_capability_ready'])
        self.cast(session,'A',spell,[target.ref]);self.cast(session,'B',response,[target.ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('exile',session.state.cards[target.object_id].zone)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session,private)

    def test_original_swampsnare_aura_uses_current_flying_and_canonical_attachment_layers(self):
        session=self.session(730407);spell=self.add(session.engine,'Swampsnare Trap',zone='hand')
        target=self.add(session.engine,'Generic Bound Body',seat='B');target.counters['flying']=1
        removal=self.add(session.engine,'Naturalize',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'G':1,'C':1})
        self.prepare(session,mana={'B':1,'C':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,spell)['strict_capability_ready'])
        self.cast(session,'A',spell,[target.ref]);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[spell.object_id].zone)
        self.assertEqual(-3,session.engine._numeric_stat(target.object_id,'power'))
        self.assertEqual(3,session.engine._numeric_stat(target.object_id,'toughness'))
        self.cast(session,'B',removal,[spell.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[spell.object_id].zone)
        self.assertEqual(2,session.engine._numeric_stat(target.object_id,'power'))
        self.assertEqual(6,session.engine._numeric_stat(target.object_id,'toughness'))
        self.private_replay(session,private)

    def test_original_price_of_fame_keeps_surveil_choice_private_after_discounted_destruction(self):
        session=self.session(730408);spell=self.add(session.engine,'Price of Fame',zone='hand')
        target=self.add(session.engine,'Isamaru, Hound of Konda',seat='B')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'B':1,'C':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,spell)['strict_capability_ready'])
        self.cast(session,'A',spell,[target.ref]);pending=self.resolve(session)
        self.assertEqual('semantic.choice',pending.kind)
        self.assertEqual('graveyard',session.state.cards[target.object_id].zone)
        refs=[session.state.cards[oid].ref for oid in reversed(session.state.players['A'].zones['library'][-2:])]
        for seat in 'BCD':
            packet=json.dumps(session.packet('pilot:'+seat,full=True));self.assertTrue(all(ref not in packet for ref in refs))
        session=self.replay(session,load=True);before=authoritative_state_hash(session.state)
        response={'action_id':'choose','cards':{'top':refs[:1],'graveyard':refs[1:]}}
        rejected=session.act('pilot:B',response);self.assertFalse(rejected.ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',response);self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve(session));self.private_replay(session,private)

    def test_typed_free_base_seam_reduces_real_sphere_increase_before_payment(self):
        from quorune.impulse_access import grant_temporary_cast_permission
        from quorune.impulse_access_model import TemporaryCastPermissionGrant,ImpulseAccessDuration
        session=self.session(730409);spell=self.add(session.engine,'Quicksand Whirlpool',zone='hand')
        sphere=self.add(session.engine,'Sphere of Resistance',seat='B')
        target=self.add(session.engine,'Generic Bound Body',seat='B');target.tapped=True
        other=self.add(session.engine,'Generic Bound Body',seat='C',ref='untapped-free-counterexample')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        grant_temporary_cast_permission(session.engine,card=spell,
            grant=TemporaryCastPermissionGrant(player='A',duration=ImpulseAccessDuration.END_OF_TURN,
                not_before_turn_sequence=None,without_mana_cost=True,source='existing-free-base-seam'),
            reason='Actual cost-order contrast; not a new free-cast producer claim')
        self.prepare(session);self.checkpoint(session)
        self.assertTrue(self.binding(session,sphere)['strict_capability_ready'])
        action=self.action(session,spell);option=next(row for row in action['cost_options'] if not any(row['requirements'].values()))
        command={'action_id':action['id'],'cost_option':option['id'],'targets':[other.ref],'pay':'auto'}
        before=authoritative_state_hash(session.state);bad=session.act('pilot:A',command)
        self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{**command,'targets':[target.ref]});self.assertTrue(accepted.ok,accepted.summary)
        item=next(item for item in session.state.stack if item.kind=='spell');self.assertEqual(0,item.context['mana_spent_total'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('exile',session.state.cards[target.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[sphere.object_id].zone)
        self.private_replay(session,private)

    def test_original_torch_at_x_zero_still_requires_its_red_mana(self):
        session=self.session(730410);spell=self.add(session.engine,'Torch Breath',zone='hand')
        target=self.add(session.engine,'Wind Drake',seat='B')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'R':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':self.action(session,spell)['id'],'targets':[target.ref],'x':0,'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        item=next(item for item in session.state.stack if item.kind=='spell');self.assertEqual(1,item.context['mana_spent_total'])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[target.object_id].zone)
        self.assertEqual(0,session.state.cards[target.object_id].marked_damage)
        self.private_replay(session,private)

    def test_all_twenty_nine_originals_have_complete_preflight_and_runtime_reduction_authority(self):
        from quorune.preflight import card_semantic_status
        from quorune.card_programs.binding import bind_semantic_program_runtime
        names=('Fate of the Sun-Cryst','Whack',"Ephara's Dispersal","This Town Ain't Big Enough",'Run Over',
            'Savage Stomp','Depower','Quicksand Whirlpool','Price of Fame','Grow Extra Arms',"Hawkeye's Shot",'Knockout Blow',
            'Fantastic Bounce','Seized from Slumber','Out of the Way','Grounded for Life','Mascot Interception','Torch Breath',
            "Ajani's Response",'Bury in Books','Luminous Rebuke','Dark Endurance','Banish from Edoras','Titanic Brawl','Swampsnare Trap',
            'No One Left Behind','Revoke Demise','Out of Air','Mystical Dispute')
        session=self.session(730411)
        for name in names:
            source=self.add(session.engine,name,zone='hand')
            self.assertTrue(self.binding(session,source)['strict_capability_ready'],name)
            status=card_semantic_status(self.db.lookup(name),session.engine.semantics,db=self.db,
                capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('fully_playable',status['status'],(name,status['unresolved']))
        source=self.db.lookup('Quicksand Whirlpool')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if SELF_TARGET_REDUCTION_CAPABILITY in p.capability_dependencies)
        for cap in (SELF_TARGET_REDUCTION_CAPABILITY,'casting.cost.targeted_fixed_generic'):
            deps=tuple(x for x in program.capability_dependencies if x!=cap)
            missing=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
        for changed in ({'active_zone':'battlefield'},{'coverage':tuple(x for x in program.coverage if x!='self_cast_cost_reduction')}):
            self.assertFalse(bind_semantic_program_runtime(replace(program,**changed),capability_registry=self.registry,profile='commander_review')['strict'])

    def test_fresh_original_quicksand_kills_non_target_qualifier_substitution(self):
        from quorune.targeted_spell_costs import TargetedSpellCostSource
        original=TargetedSpellCostSource.applies
        def all_qualifiers(source,targets):
            return bool(source.eligible_target_refs) if source.spec.generic<0 else original(source,targets)
        method='test_original_quicksand_offers_cheaper_tapped_target_and_rejects_cheap_untapped_payment_atomically'
        with patch.object(TargetedSpellCostSource,'applies',all_qualifiers):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_original_graveyard_reductions_use_selected_card_value_and_keep_return_ownership(self):
        for name,paid,life in (('No One Left Behind',2,40),('Revoke Demise',3,42)):
            session=self.session(730412+paid);spell=self.add(session.engine,name,zone='hand')
            target=self.add(session.engine,'Wind Drake',zone='graveyard')
            expensive=self.add(session.engine,'Colossal Dreadmaw',zone='graveyard')
            foreign=self.add(session.engine,'Wind Drake',seat='B',zone='graveyard',ref='foreign-return-card')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'B':1,'C':paid-1});self.checkpoint(session)
            self.assertTrue(self.binding(session,spell)['strict_capability_ready'])
            action=self.action(session,spell);before=authoritative_state_hash(session.state)
            for ref in (expensive.ref,foreign.ref):
                rejected=session.act('pilot:A',{'action_id':action['id'],'targets':[ref],'pay':'auto'})
                self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
            self.cast(session,'A',spell,[target.ref]);item=next(item for item in session.state.stack if item.kind=='spell')
            self.assertEqual(paid,item.context['mana_spent_total'])
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual('battlefield',session.state.cards[target.object_id].zone)
            self.assertEqual('A',session.state.cards[target.object_id].controller)
            self.assertEqual('graveyard',session.state.cards[foreign.object_id].zone)
            self.assertEqual('graveyard',session.state.cards[expensive.object_id].zone)
            self.assertEqual(life,session.state.players['A'].life)
            self.private_replay(session,private)

    def test_original_stack_reductions_use_creature_or_blue_spell_characteristics(self):
        for name,victim,paid in (('Out of Air','Wind Drake',2),('Mystical Dispute','Refocus',1)):
            session=self.session(730416+paid);spell=self.add(session.engine,name,zone='hand')
            target=self.add(session.engine,victim,seat='B',zone='hand')
            body=self.add(session.engine,'Generic Bound Body',seat='B')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            session.state.players['A'].mana_pool.update({'U':paid})
            self.prepare(session,actor='B',active='B',mana={'U':1,'C':2 if victim=='Wind Drake' else 1});self.checkpoint(session)
            self.assertTrue(self.binding(session,spell)['strict_capability_ready'])
            self.cast(session,'B',target,[body.ref] if victim=='Refocus' else [])
            target_item=next(item for item in session.state.stack if item.kind=='spell')
            self.cast(session,'A',spell,[target_item.ref]);counter=next(item for item in session.state.stack if item.card_object_id==spell.object_id)
            self.assertEqual(paid,counter.context['mana_spent_total'])
            session=self.replay(session,load=True);pending=self.resolve(session)
            if pending is not None:
                self.assertEqual('semantic.choice',pending.kind)
                session=self.replay(session,load=True)
                accepted=session.act('pilot:B',{'action_id':'choose','pay':False})
                self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(session))
            self.assertEqual('graveyard',session.state.cards[target.object_id].zone)
            self.assertEqual('graveyard',session.state.cards[spell.object_id].zone)
            self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
            self.private_replay(session,private)

    def test_actual_copy_retargets_to_untapped_creature_without_another_cast_or_reduction_payment(self):
        session=self.session(730419);spell=self.add(session.engine,'Quicksand Whirlpool',zone='hand')
        target=self.add(session.engine,'Generic Bound Body',seat='B');target.tapped=True
        copied_target=self.add(session.engine,'Generic Bound Body',seat='C',ref='copy-untapped-target')
        copy=self.add(session.engine,'Reverberate',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'R':2})
        self.prepare(session,mana={'W':1,'C':2});self.checkpoint(session)
        self.cast(session,'A',spell,[target.ref]);original=next(item for item in session.state.stack if item.kind=='spell')
        self.cast(session,'B',copy,[original.ref]);pending=self.resolve(session)
        self.assertEqual('semantic.choice',pending.kind)
        session=self.replay(session,load=True);before=authoritative_state_hash(session.state)
        command={'action_id':'choose','targets':[copied_target.ref]}
        bad=session.act('pilot:A',command);self.assertFalse(bad.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:B',command);self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual('exile',session.state.cards[target.object_id].zone)
        self.assertEqual('exile',session.state.cards[copied_target.object_id].zone)
        self.assertEqual(2,len(session.engine._current_turn_history('spell_cast')))
        self.private_replay(session,private)

    def test_all_illegal_target_resolution_does_not_reprice_or_refund_the_paid_reduction(self):
        session=self.session(730420);spell=self.add(session.engine,'Quicksand Whirlpool',zone='hand')
        target=self.add(session.engine,'Generic Bound Body',seat='B');target.tapped=True
        response=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['B'].mana_pool.update({'U':1})
        self.prepare(session,mana={'W':1,'C':2});self.checkpoint(session)
        self.cast(session,'A',spell,[target.ref]);self.cast(session,'B',response,[target.ref])
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[target.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[spell.object_id].zone)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        self.private_replay(session,private)

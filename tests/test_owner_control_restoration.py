"""CR110.2/111.2/608.2f: fixed simultaneous control for each physical owner."""
from dataclasses import FrozenInstanceError,replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import test_fixed_creature_power_damage as records
import test_recipient_control as recipients
from inherited_composition_fixture import InheritedNativeFixture
from quorune.oracle_ir import compile_oracle_card
from quorune.owner_control_model import OwnerControlSpec,OWNER_CONTROL_CAPABILITY as CAP
from quorune.rules.capabilities import load_default_capability_registry,capability_dependencies_for_node
from quorune.rules.owner_control_shapes import owner_control_runtime_dependencies
from quorune.semantic_runtime.control_handlers import OwnerControlHandler
from quorune.record import authoritative_state_hash


class OwnerControlModelCompilerTests(unittest.TestCase):
    def compile(self,text):
        return compile_oracle_card(records.card_record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')

    def test_closed_scopes_lower_to_one_immutable_source_backed_intent(self):
        context=recipients.RecipientControlModelTests().context()
        for scope in ('creatures','nontoken_permanents'):
            spec=OwnerControlSpec(scope)
            with self.assertRaises(FrozenInstanceError):spec.scope='all'
            effect={'op':'restore_owner_control','schema_version':1,'scope':scope,'source':'source-ref'}
            intent,=OwnerControlHandler().lower(effect,context).intents
            self.assertEqual(('A',spec),(intent.actor,intent.spec))
            for change in ({'schema_version':True},{'scope':'permanents'},{'scope':True},{'controller':'A'},{'source':None},{'duration':'until_end_of_turn'}):
                with self.subTest(change=change),self.assertRaises(ValueError):OwnerControlHandler().lower({**effect,**change},context)

    def test_only_exact_all_player_owner_scopes_have_capability_closure(self):
        for subject in ('creatures','nontoken permanents'):
            ir=self.compile('Each player gains control of all '+subject+' they own.')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node,=ir.faces[0].nodes
            self.assertIsNone(node.target_schema);self.assertIn(CAP,node.capability_dependencies)
            for change in ({'schema_version':True},{'source':'$target.0'},{'scope':'permanents'},{'scope':'nontoken_creatures'},{'unexpected':1}):
                with self.subTest(change=change):
                    effects=({**node.effects[0],**change},)
                    self.assertEqual((),capability_dependencies_for_node(effects=effects,target_schema=None,mechanic_ids=node.mechanics))
                    with self.assertRaises(ValueError):owner_control_runtime_dependencies(SimpleNamespace(effects=effects))
        for text in ('Each opponent gains control of all creatures they own.',
                'Each player gains control of all creatures they own until end of turn.',
                'Each player gains control of a creature they own.',
                'Each player gains control of all permanents they own.',
                'Each player gains control of all creatures they control.'):
            with self.subTest(text=text):self.assertNotEqual('exact',self.compile(text).status)


class OwnerControlDependencyTests(unittest.TestCase):
    def world(self,*,aura_recipient='A',earlier=False,stale=False):
        from quorune.continuous_effect_model import (ContinuousEffect,ContinuousEffectOrigin as Origin,
            ContinuousEffectDuration as Duration,ContinuousEffectRelation as Relation,ContinuousObjectIdentity as Identity,ContinuousOperation,Layer)
        cards={name:SimpleNamespace(object_id=name,logical_object_id=name+'@1',zone='battlefield',controller='A') for name in ('aura','body')}
        def resolution(label,source,time,target,controller,*,logical=None):
            return ContinuousEffect(effect_id=label,source_id=source,layer=Layer.CONTROL,sublayer='2',timestamp=time,
                operations=(ContinuousOperation('set_controller',controller),),origin=Origin.RESOLUTION,duration=Duration.ZONE_OBJECT,
                locked_objects=(Identity(target,logical or target+'@1'),))
        origins=tuple(resolution('control-origin:'+name+'@1',name,0,name,'A') for name in cards)
        journal=(*origins,resolution('owner-aura','owner-restoration',20,'aura',aura_recipient),
            resolution('owner-body','owner-restoration',20,'body','D'))
        if earlier:journal=(*journal,resolution('earlier','earlier-source',1,'aura','B'))
        if stale:journal=(*journal,resolution('stale','departed-source',25,'aura','C',logical='aura@0'))
        aura=ContinuousEffect(effect_id='aura-static',source_id='aura',layer=Layer.CONTROL,sublayer='2',timestamp=5,
            operations=(ContinuousOperation('set_controller','A'),),origin=Origin.STATIC_ABILITY,
            relation=Relation.SOURCE_ATTACHED_TO_OBJECT,related_object=Identity('body','body@1'))
        return SimpleNamespace(state=SimpleNamespace(cards=cards)),journal,(aura,)

    def test_source_dependencies_recheck_real_changes_and_keep_one_restoration_atomic(self):
        from quorune.attached_control import layer_two_controller_map
        cases=(({},'A','D'),({'aura_recipient':'C'},'C','C'),({'earlier':True},'A','A'),({'stale':True},'A','D'))
        for options,aura_controller,body_controller in cases:
            with self.subTest(options=options):
                host,journal,static=self.world(**options)
                expected={'aura':aura_controller,'body':body_controller}
                self.assertEqual(expected,layer_two_controller_map(host,journal,static))
                self.assertEqual(expected,layer_two_controller_map(host,tuple(reversed(journal)),static))

    def test_fresh_dependency_witness_kills_unchanged_source_controller_dependencies(self):
        from quorune.continuous_effect_model import ContinuousEffectOrigin
        def wrong(effects,**_context):
            result=[]
            for effect in effects:
                if effect.origin is not ContinuousEffectOrigin.STATIC_ABILITY:
                    result.append(effect);continue
                required=set(effect.depends_on)
                for other in effects:
                    identities=(*other.locked_objects,*((other.related_object,) if other.related_object else ()))
                    if other.effect_id!=effect.effect_id and any(i.object_id==effect.source_id for i in identities):required.add(other.effect_id)
                result.append(replace(effect,depends_on=tuple(sorted(required))))
            return tuple(result)
        method='test_source_dependencies_recheck_real_changes_and_keep_one_restoration_atomic'
        getattr(type(self)(method),method)()
        with patch('quorune.attached_control.control_source_dependencies',wrong):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_duplicate_custody_origins_still_fail_closed_before_ordering(self):
        from quorune.attached_control import layer_two_controller_map
        host,journal,static=self.world()
        with self.assertRaises(ValueError):layer_two_controller_map(host,(*journal,journal[0]),static)


class OwnerControlNativeTests(InheritedNativeFixture,unittest.TestCase):
    setUpClass=classmethod(recipients.RecipientControlNativeTests.setUpClass.__func__)
    scene=recipients.RecipientControlNativeTests.scene
    activate=recipients.RecipientControlNativeTests.activate
    reject=recipients.RecipientControlNativeTests.reject
    finish=recipients.RecipientControlNativeTests.finish
    finish_turn=recipients.RecipientControlNativeTests.finish_turn

    def board(self,name,*,aura=False):
        extra={}
        def before(s,source,gift):
            extra['peer']=self.add(s.engine,'Grizzly Bears',seat='C',ref='restoration-peer');extra['peer'].owner='B'
            extra['land']=self.add(s.engine,'Forest',ref='restoration-land');extra['land'].owner='D'
            extra['phased']=self.add(s.engine,'Grizzly Bears',seat='C',ref='restoration-phased');extra['phased'].owner='D';extra['phased'].phased_out=True
            for name in ('Raise the Alarm','Donate','Unsummon'):
                extra[name]=self.add(s.engine,name,zone='hand',ref='restoration-'+name.replace(' ','-'))
            extra['later']=self.add(s.engine,'Donate',zone='hand',ref='restoration-later-gift')
            self.add(s.engine,'Island')
            if aura:extra['aura']=self.add(s.engine,'Control Magic',zone='hand')
        s,source,gift,private=self.scene(name,zone='battlefield' if name=='Homeward Path' else 'hand',before=before)
        if name!='Homeward Path':self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.cast(s,'A',extra['Raise the Alarm']);self.assertIsNone(self.resolve(s))
        tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield']
        self.assertEqual(4 if name=='Trostani Discordant' else 2,len(tokens))
        token=tokens[0];self.assertEqual(('A','A'),(token.owner,token.controller))
        self.cast(s,'A',extra['Donate'],['C',token.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(('A','C'),(s.state.cards[token.object_id].owner,s.state.cards[token.object_id].controller))
        if aura:
            self.cast(s,'A',extra['aura'],[gift.ref]);self.assertIsNone(self.resolve(s))
            self.assertEqual(gift.object_id,s.state.cards[extra['aura'].object_id].attached_to)
        return s,source,gift,private,extra,token

    def restored(self,s,gift,extra,token,*,permanents):
        self.assertEqual('D',s.state.cards[gift.object_id].controller)
        self.assertEqual('D',s.engine._effective_card_data(s.state.cards[gift.object_id]).get('controller',s.state.cards[gift.object_id].controller))
        self.assertEqual('B',s.state.cards[extra['peer'].object_id].controller)
        self.assertEqual('C' if permanents else 'A',s.state.cards[token.object_id].controller)
        self.assertEqual('A',s.state.cards[token.object_id].owner)
        self.assertEqual('D' if permanents else 'A',s.state.cards[extra['land'].object_id].controller)
        self.assertEqual('C',s.state.cards[extra['phased'].object_id].controller)

    def homeward(self):
        s,source,gift,private,extra,token=self.board('Homeward Path')
        self.activate(s,source,[]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.restored(s,gift,extra,token,permanents=False)
        effects=[e for e in s.state.continuous_effects if e.source_id==source.object_id and e.effect_id.startswith('control-effect:')]
        self.assertEqual(3,len(effects));self.assertEqual(1,len({e.timestamp for e in effects}))
        self.assertEqual({'A','B','D'},{e.operations[0].value for e in effects})
        self.assertTrue(s.state.cards[source.object_id].tapped)
        self.cast(s,'A',extra['later'],['C',token.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('C',s.state.cards[token.object_id].controller)
        self.finish(s,private)

    def test_homeward_path_restores_multiple_owners_and_tokens_with_one_timestamp(self):
        self.homeward()

    def test_brooding_restores_only_nontokens_after_source_departure_without_detaching_aura(self):
        s,source,gift,private,extra,token=self.board('Brooding Saurian',aura=True)
        self.until(s,lambda s:self.trigger_count(s,source)>0)
        self.cast(s,'A',extra['Unsummon'],[source.ref]);self.resolve_top(s)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.restored(s,gift,extra,token,permanents=True)
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        aura=s.state.cards[extra['aura'].object_id]
        self.assertEqual(('A',gift.object_id),(aura.controller,aura.attached_to))
        self.finish(s,private)

    def test_trostani_and_alicia_keep_their_other_abilities_and_restore_at_actual_end_step(self):
        for name in ('Trostani Discordant','Alicia Masters, Skilled Sculptor'):
            with self.subTest(name=name):
                s,source,gift,private,extra,token=self.board(name)
                if name=='Trostani Discordant':
                    self.assertEqual(3,s.engine._numeric_stat(gift.object_id,'power'))
                    self.assertEqual(2,len([c for c in s.state.cards.values() if c.is_token and 'lifelink' in s.engine._combat_keywords(c)]))
                self.finish_turn(s)
                self.restored(s,gift,extra,token,permanents=False)
                if name=='Alicia Masters, Skilled Sculptor':
                    treasure=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield' and 'Treasure' in s.engine._effective_card_data(c)['type_line']]
                    self.assertEqual(1,len(treasure));self.assertEqual(('A','A'),(treasure[0].owner,treasure[0].controller))
                self.finish(s,private)

    def test_assignment_batch_rejects_an_invalid_later_recipient_before_any_mutation(self):
        from quorune.control_effects import gain_control_of_assignments,ControlEffectError
        from quorune.continuous_effect_state import ResolutionEffectSource
        from quorune.continuous_effect_model import ContinuousEffectDuration
        s,source,gift,private,extra,token=self.board('Homeward Path')
        before=authoritative_state_hash(s.state)
        for assignments in (((gift.ref,'D'),(extra['peer'].ref,'Z')),((gift.ref,'D'),(gift.ref,'B')),((gift.ref,'D'),('invalid',False))):
            with self.subTest(assignments=assignments),self.assertRaises(ControlEffectError):
                gain_control_of_assignments(s.engine,actor='A',assignments=assignments,duration=ContinuousEffectDuration.ZONE_OBJECT,
                    source=ResolutionEffectSource(stack_ref='invalid-owner-plan'),reason='invalid owner batch')
            self.assertEqual(before,authoritative_state_hash(s.state))

    def test_owner_control_payload_cannot_lose_its_capability_when_coverage_is_removed(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s,source,gift,private=self.scene('Homeward Path',zone='battlefield')
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if any(e.get('op')=='restore_owner_control' for e in p.effects))
        deps=tuple(c for c in program.capability_dependencies if c!=CAP)
        stripped=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,bound['blockers'])

    def test_fresh_homeward_witness_kills_restoring_all_permanents_to_the_resolving_actor(self):
        from quorune import control_effects
        original=control_effects.gain_control_of_assignments
        def wrong(host,*,actor,assignments,**kwargs):
            changed=(tuple((ref,actor) for ref,_owner in assignments) if len({owner for _ref,owner in assignments})>1 else assignments)
            return original(host,actor=actor,assignments=changed,**kwargs)
        method='test_homeward_path_restores_multiple_owners_and_tokens_with_one_timestamp'
        type(self)(method).homeward()
        with patch.object(control_effects,'gain_control_of_assignments',wrong):
            with self.assertRaises(AssertionError):type(self)(method).homeward()

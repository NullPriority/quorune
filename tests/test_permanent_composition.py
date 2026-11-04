"""CR 730 topology and read-only capture; these values do not enable Mutate."""
from dataclasses import FrozenInstanceError
import unittest

from quorune.continuous_effect_model import ContinuousObjectIdentity as Identity
from quorune.permanent_composition import PermanentComponent, PermanentComposition, MergePosition


class PermanentCompositionTests(unittest.TestCase):
    def component(self,name,*,owner='A',token=False,commander=None):
        return PermanentComponent(Identity(name,name+'@1'),owner,token,commander)

    def test_over_and_under_keep_one_object_and_order_all_physical_parts(self):
        base=self.component('BASE');first=self.component('FIRST');second=self.component('SECOND')
        original=PermanentComposition.single(base)
        over=original.merge(first,position=MergePosition.TOP,expected_object=base.identity)
        under=over.merge(second,position=MergePosition.BOTTOM,expected_object=base.identity)
        self.assertEqual(base.identity,under.object_identity)
        self.assertEqual(('FIRST','BASE','SECOND'),under.physical_object_ids)
        self.assertEqual(first,under.top)
        self.assertEqual(('BASE',),original.physical_object_ids)
        self.assertEqual((1,2),(over.mutation_count,under.mutation_count))
        self.assertEqual('A',under.owner)

    def test_owner_and_current_incarnation_are_required_for_each_merge(self):
        base=self.component('BASE');original=PermanentComposition.single(base)
        for incoming,position,current in (
                (self.component('OTHER',owner='B'),MergePosition.TOP,base.identity),
                (self.component('OTHER'),MergePosition.TOP,Identity('BASE','BASE@3')),
                (PermanentComponent(Identity('BASE','BASE@3'),'A'),MergePosition.BOTTOM,base.identity),
                (self.component('OTHER'),'top',base.identity)):
            with self.assertRaises(ValueError):original.merge(incoming,position=position,expected_object=current)
        with self.assertRaises(ValueError):PermanentComposition(Identity('BASE','BASE@3'),(base,),0)

    def test_retained_object_identity_can_be_separate_from_its_physical_parts(self):
        retained=Identity('PERMANENT','PERMANENT@7')
        first=self.component('FIRST_PART');second=self.component('SECOND_PART')
        composition=PermanentComposition(retained,(first,second),0)
        merged=composition.merge(self.component('INCOMING'),position=MergePosition.TOP,expected_object=retained)
        self.assertEqual(retained,merged.object_identity)
        self.assertEqual(('INCOMING','FIRST_PART','SECOND_PART'),merged.physical_object_ids)
        self.assertNotIn(retained.object_id,merged.physical_object_ids)

    def test_top_token_status_and_buried_commander_identity_are_independent(self):
        token=self.component('TOKEN',token=True)
        commander=self.component('COMMANDER',commander='commander:A:one')
        beneath=PermanentComposition.single(token).merge(commander,position=MergePosition.BOTTOM,expected_object=token.identity)
        self.assertTrue(beneath.is_token);self.assertTrue(beneath.is_commander)
        self.assertEqual(('commander:A:one',),beneath.commander_designation_ids)
        above=beneath.merge(self.component('CARD'),position=MergePosition.TOP,expected_object=token.identity)
        self.assertFalse(above.is_token);self.assertTrue(above.is_commander)
        # Copyable characteristics are a separate reader. A fresh physical copy
        # does not acquire the source's component pile or commander designation.
        copied=PermanentComposition.single(self.component('COPY',token=True))
        self.assertFalse(copied.is_commander);self.assertEqual(0,copied.mutation_count)
        self.assertEqual(('COPY',),copied.physical_object_ids)
        with self.assertRaises(ValueError):self.component('TOKEN_COMMANDER',token=True,commander='commander:A:one')

    def test_mutation_count_is_explicit_history_instead_of_part_count(self):
        base=self.component('BASE');other=self.component('EXISTING_PART')
        prior_composition=PermanentComposition(base.identity,(base,other),0)
        merged=prior_composition.merge(self.component('INCOMING'),position=MergePosition.TOP,expected_object=base.identity)
        self.assertEqual(3,len(merged.components));self.assertEqual(1,merged.mutation_count)
        for invalid in (True,-1,1.5,3):
            with self.assertRaises(ValueError):PermanentComposition(base.identity,(base,other),invalid)

    def test_closed_wire_round_trips_without_aliasing_mutable_inputs(self):
        base=self.component('BASE');other=self.component('OTHER')
        original=PermanentComposition(base.identity,[other,base],1)
        wire=original.to_dict();restored=PermanentComposition.from_dict(wire)
        self.assertEqual(original,restored)
        wire['components'][0]['owner']='B'
        self.assertEqual('A',restored.owner)
        exported=restored.to_dict();exported['components'].clear()
        self.assertEqual(2,len(restored.components))
        malformed=[{**original.to_dict(),'unexpected':True},
            {**original.to_dict(),'schema_version':True},
            {**original.to_dict(),'object':{'object_id':'BASE','logical_object_id':'BASE@-1'}},
            {**original.to_dict(),'components':[]},
            {**original.to_dict(),'components':[other.to_dict(),{**base.to_dict(),'owner':'B'}]},
            {**original.to_dict(),'components':[base.to_dict(),{**other.to_dict(),'controller':'A'}]},
            {**original.to_dict(),'components':[base.to_dict(),{**base.to_dict(),
                'identity':{'object_id':'BASE','logical_object_id':'BASE@3'}}]}]
        for changed in malformed:
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):PermanentComposition.from_dict(changed)
        duplicate_commanders=(self.component('BASE',commander='same'),self.component('OTHER',commander='same'))
        with self.assertRaises(ValueError):PermanentComposition(base.identity,duplicate_commanders,1)

    def test_departure_query_is_scoped_and_cannot_mutate_the_parts(self):
        base=self.component('BASE');other=self.component('OTHER')
        composition=PermanentComposition(base.identity,(other,base),1)
        parts=composition.departure_components(expected_object=base.identity)
        self.assertEqual((other,base),parts)
        with self.assertRaises(ValueError):composition.departure_components(expected_object=Identity('BASE','BASE@2'))
        with self.assertRaises(FrozenInstanceError):composition.components=()
        with self.assertRaises(FrozenInstanceError):parts[0].owner='B'


class CompositionSnapshotTests(unittest.TestCase):
    def card(self,**changes):
        from quorune.model import CardInstance
        values={'object_id':'PART','ref':'PUBLIC_PART','oracle_id':'physical-oracle','printed_name':'Physical Part',
            'owner':'A','controller':'B','zone':'battlefield','zone_change_counter':2,
            'face_down':True,'active_face':'back','counters':{'charge':3},'marked_damage':1,
            'known_to':['A'],'annotations':{'nested':{'values':[1,2]}}}
        return CardInstance(**{**values,**changes})

    def test_physical_state_and_copiable_values_are_separate_immutable_snapshots(self):
        from quorune.composition_snapshot import CompositionObjectSnapshot
        card=self.card();values={'name':'','power':'2','toughness':'2','type_line':'Creature'}
        snapshot=CompositionObjectSnapshot.capture(card,copiable_values=values,controller='B')
        self.assertEqual(card.to_dict(),snapshot.detached_card().to_dict())
        self.assertEqual(Identity('PART','PART@2'),snapshot.identity)
        self.assertEqual('physical-oracle',snapshot.card_state['oracle_id'])
        self.assertEqual('',snapshot.copiable_values['name'])
        values['name']='changed';card.annotations['nested']['values'].append(3)
        self.assertEqual('',snapshot.copiable_values['name'])
        self.assertEqual((1,2),snapshot.card_state['annotations']['nested']['values'])
        detached=snapshot.detached_card();detached.counters['charge']=99
        self.assertEqual(3,snapshot.card_state['counters']['charge'])
        self.assertEqual(3,card.counters['charge'])
        wire=snapshot.to_dict();restored=CompositionObjectSnapshot.from_dict(wire)
        self.assertEqual(snapshot,restored)
        wire['card_state']['annotations']['nested']['values'].append(4)
        self.assertEqual((1,2),restored.card_state['annotations']['nested']['values'])
        with self.assertRaises(FrozenInstanceError):snapshot.controller='A'

    def test_current_copy_kind_and_legacy_commander_facts_are_preserved_without_normalization(self):
        from quorune.composition_snapshot import CompositionObjectSnapshot
        for kind in ('spell_copy','card_copy'):
            card=self.card(zone='stack',object_kind=kind,face_down=False)
            snapshot=CompositionObjectSnapshot.capture(card,copiable_values={},controller='C',stack_ref='SPELL',stack_kind='spell_copy')
            self.assertEqual(kind,snapshot.detached_card().object_kind)
            self.assertFalse(snapshot.card_state['is_token'])
            self.assertEqual(('A','B','C'),(snapshot.card_state['owner'],snapshot.card_state['controller'],snapshot.controller))
        legacy=self.card(is_commander=True,commander_designation_id=None)
        snapshot=CompositionObjectSnapshot.capture(legacy,copiable_values={},controller='B')
        self.assertNotIn('commander_designation_id',snapshot.card_state)
        self.assertTrue(snapshot.detached_card().is_commander)
        self.assertEqual(legacy.to_dict(),snapshot.detached_card().to_dict())

    def test_snapshot_wire_rejects_unknown_or_noncanonical_physical_and_scope_data(self):
        from quorune.composition_snapshot import CompositionObjectSnapshot
        snapshot=CompositionObjectSnapshot.capture(self.card(),copiable_values={},controller='B')
        wire=snapshot.to_dict()
        malformed=[{**wire,'unknown':True},{**wire,'schema_version':True},
            {**wire,'controller':'A'},{**wire,'stack_ref':'SPELL','stack_kind':'spell'},
            {**wire,'card_state':{**wire['card_state'],'unknown':True}},
            {**wire,'card_state':{**wire['card_state'],'zone_change_counter':True}},
            {**wire,'card_state':{**wire['card_state'],'zone':'hand'}},
            {**wire,'card_state':{**wire['card_state'],'is_token':False,'object_kind':'token'}},
            {**wire,'copiable_values':{'bad':float('nan')}}]
        for value in malformed:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):CompositionObjectSnapshot.from_dict(value)


class CompositionCaptureTests(unittest.TestCase):
    def host(self,card):
        from types import SimpleNamespace
        from quorune.entry_copy import COPIABLE_ORACLE_FIELD
        players={seat:SimpleNamespace(zones={'battlefield':[]}) for seat in 'ABC'}
        stack=[]
        if card.zone=='battlefield':players[card.controller].zones['battlefield'].append(card.object_id)
        if card.zone=='stack':stack=[SimpleNamespace(ref='STACK',kind='spell_copy',controller='C',card_object_id=card.object_id)]
        return SimpleNamespace(state=SimpleNamespace(cards={card.object_id:card},players=players,stack=stack),
            _copyable_characteristics=lambda source:{'name':'Copied Part','type_line':'Creature','power':'2','toughness':'6',
                COPIABLE_ORACLE_FIELD:'copiable-oracle'},card_record=lambda source:None)

    def test_capture_uses_current_stack_controller_without_inferring_it_from_owner_or_holder(self):
        from quorune.rules.composition_capture import capture_composition_object
        from quorune.entry_copy import COPIABLE_ORACLE_FIELD
        card=CompositionSnapshotTests().card(zone='stack',object_kind='spell_copy',face_down=False)
        host=self.host(card);before=card.to_dict()
        snapshot=capture_composition_object(host,Identity(card.object_id,card.logical_object_id),
            expected_zone='stack',expected_stack_ref='STACK')
        self.assertEqual(('A','B','C'),(snapshot.card_state['owner'],snapshot.card_state['controller'],snapshot.controller))
        self.assertEqual(('STACK','spell_copy'),(snapshot.stack_ref,snapshot.stack_kind))
        self.assertEqual('physical-oracle',snapshot.card_state['oracle_id'])
        self.assertEqual('copiable-oracle',snapshot.copiable_values[COPIABLE_ORACLE_FIELD])
        self.assertEqual(before,card.to_dict())

    def test_face_down_copy_values_use_the_existing_copy_layer_view(self):
        from quorune.rules.composition_capture import capture_composition_object
        from quorune.continuous_effect_model import Layer
        from quorune.entry_copy import COPIABLE_ORACLE_FIELD
        card=CompositionSnapshotTests().card();host=self.host(card);reads=[]
        def masked(source,*,maximum_layer):
            reads.append(maximum_layer)
            return {'name':'','power':'2','toughness':'2','type_line':'Creature','activated_abilities':[]}
        host._effective_card_data=masked
        host._copyable_characteristics=lambda source:self.fail('Printed face must not supply face-down copiable values')
        snapshot=capture_composition_object(host,Identity(card.object_id,card.logical_object_id),expected_zone='battlefield')
        self.assertEqual([Layer.COPY],reads)
        self.assertEqual('custom-copy:face-down',snapshot.copiable_values[COPIABLE_ORACLE_FIELD])
        self.assertEqual('',snapshot.copiable_values['name'])
        self.assertEqual('Physical Part',snapshot.card_state['printed_name'])

    def test_missing_stale_or_ambiguous_current_membership_rejects_without_mutation(self):
        from quorune.rules.composition_capture import capture_composition_object
        card=CompositionSnapshotTests().card(face_down=False)
        current=Identity(card.object_id,card.logical_object_id)
        host=self.host(card);before=card.to_dict()
        with self.assertRaises(ValueError):capture_composition_object(host,Identity(card.object_id,'PART@3'),expected_zone='battlefield')
        host.state.players['A'].zones['battlefield'].append(card.object_id)
        with self.assertRaises(ValueError):capture_composition_object(host,current,expected_zone='battlefield')
        host.state.players['A'].zones['battlefield'].clear();host.state.players['B'].zones['battlefield'].clear()
        with self.assertRaises(ValueError):capture_composition_object(host,current,expected_zone='battlefield')
        self.assertEqual(before,card.to_dict())
        card=CompositionSnapshotTests().card(zone='stack',face_down=False)
        host=self.host(card);current=Identity(card.object_id,card.logical_object_id)
        with self.assertRaises(ValueError):capture_composition_object(host,current,expected_zone='stack')
        with self.assertRaises(ValueError):capture_composition_object(host,current,expected_zone='stack',expected_stack_ref='OLD_STACK')
        host.state.stack.append(host.state.stack[0])
        with self.assertRaises(ValueError):capture_composition_object(host,current,expected_zone='stack',expected_stack_ref='STACK')
        host.state.stack.clear()
        with self.assertRaises(ValueError):capture_composition_object(host,current,expected_zone='stack',expected_stack_ref='STACK')


class CompositionCaptureNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'composition-capture.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/plural-reanimation-cards.json',
            ROOT/'tests/fixtures/typed-grant-carrier-cards.json',
            ROOT/'tests/fixtures/banding-cards.json',
            ROOT/'tests/fixtures/fixed-public-state-characteristics-cards.json',
            ROOT/'tests/fixtures/fixed-basic-type-setting-cards.json',
            ROOT/'tests/fixtures/activated-self-transform-cards.json',
            ROOT/'tests/fixtures/morph-rules-cards.json',
            ROOT/'tests/fixtures/face-down-method-cards.json',
            ROOT/'tests/fixtures/nonmana-morph-cards.json',
            ROOT/'tests/fixtures/composed-manifest-cards.json',
            ROOT/'tests/fixtures/face-up-effect-cards.json',
            ROOT/'tests/fixtures/double-faced-spell-copy-cards.json',
            ROOT/'tests/fixtures/spell-history-transformations-cards.json',
            ROOT/'tests/fixtures/conditional-copy-value-cards.json',
            ROOT/'tests/fixtures/state-based-destination-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Composition capture',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def physical_face_fixture(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.zone_transitions import ZoneTransitionOwner
        session=H.session(self,959230);engine=session.engine
        root=H.add(self,engine,'Ornithopter',ref='FACE_ROOT');root.acquired_control_turn_count=-1
        root.counters={'charge':3};root.marked_damage=1
        gargoyle=None
        for name,ref,mana in (('Thraben Gargoyle // Stonewing Antagonizer','FACE_GARGOYLE',{'C':1}),
                ('Ulvenwald Captive // Ulvenwald Abomination','FACE_CAPTIVE',{'G':2})):
            incoming=H.add(self,engine,name,zone='hand',ref=ref)
            if gargoyle is None:gargoyle=incoming
            action=H.ready(self,session,incoming,mana)
            self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
            root=engine.state.cards[root.object_id]
            reference=next(item.ref for item in engine.state.stack if item.card_object_id==incoming.object_id)
            ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
                target=Identity(root.object_id,root.logical_object_id),source=Identity(incoming.object_id,incoming.logical_object_id),
                stack_ref=reference,position=MergePosition.BOTTOM))
        return session,engine.state.cards[root.object_id],gargoyle

    def assert_normal_complete_program_closed(self,session,card):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),
            semantic_registry=session.engine.semantics,capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
        self.assertEqual('capability_closed',binding['trust_basis'])

    def test_face_down_spell_copy_keeps_anonymous_copiable_values_and_public_identity_through_replay(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=H.session(self,959250);engine=session.engine
        observer=H.add(self,engine,'Generic Complete Creature Copy Observer',zone='hand',ref='FD_COPY_OBSERVER')
        source=H.add(self,engine,'Lumithread Field',zone='hand',ref='FD_COPY_ORIGINAL')
        secret=H.add(self,engine,'Generic Bound Growth',seat='B',zone='hand',ref='FD_COPY_PRIVATE_HAND')
        for card in (observer,source):self.assert_normal_complete_program_closed(session,card)
        offer=H.ready(self,session,observer,{'C':5,'U':1,'W':1});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto','reserve':{'C':4,'W':1}}).ok)
        self.assertIsNone(H.resolve(self,session))
        self.assertTrue(session.act('pilot:A',{'action_id':'cast-morph:'+source.ref,'pay':'auto','reserve':{'C':1,'W':1}}).ok)
        self.assertTrue(engine.state.cards[source.object_id].face_down)
        self.assertNotIn('Lumithread Field',str(session.packet('pilot:B',full=True)['state']))
        session=H.replay(self,session,load=True);engine=session.engine
        for _ in range(12):
            if any(item.kind=='spell_copy' for item in engine.state.stack):break
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        copied=next(item for item in engine.state.stack if item.kind=='spell_copy')
        copy_id=copied.card_object_id
        public=str(session.packet('pilot:B',full=True)['state'])
        self.assertFalse('Lumithread Field' in public,'Copy projection must not expose the hidden original name')
        self.assertFalse(source.oracle_id[:8] in public,'Copy projection must not expose the hidden original Oracle/image identity')
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertIsNone(H.resolve(self,session))
        token=engine.state.cards[copy_id];current=engine._effective_card_data(token)
        self.assertEqual(('battlefield','token',False),(token.zone,token.object_kind,token.face_down))
        self.assertEqual(('',0,'Creature',[],[],''),(current['name'],current['mana_value'],current['type_line'],
            current['colors'],current['keywords'],current.get('executable_oracle_text','')))
        self.assertEqual((2,2),(engine._numeric_stat(token.object_id,'power'),engine._numeric_stat(token.object_id,'toughness')))
        self.assertFalse(engine._activated_abilities(token))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('turn-face-up:'+token.ref) for row in actions))
        original_offer=next(row for row in actions if row['id']=='turn-face-up:'+source.ref)
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:B',{'action_id':original_offer['id'],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertFalse(session.act('pilot:A',{'action_id':'turn-face-up:'+token.ref,'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertTrue(session.act('pilot:A',{'action_id':original_offer['id'],'pay':'auto'}).ok)
        self.assertFalse(engine.state.cards[source.object_id].face_down)
        self.assertEqual('Enchantment',engine._effective_card_data(engine.state.cards[source.object_id])['type_line'])
        self.assertEqual('Creature',engine._effective_card_data(engine.state.cards[token.object_id])['type_line'])
        self.assertNotIn(secret.ref,str(session.packet('pilot:A',full=True)))
        H.replay(self,session,load=True)

    def test_face_down_copy_reader_preserves_only_status_values_and_rejects_missing_representation(self):
        from dataclasses import replace
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.entry_copy import COPIABLE_ORACLE_FIELD
        from quorune.errors import GameRuleError
        from quorune.morph import MORPH_FACE_DOWN_ANNOTATION
        from quorune.record import authoritative_state_hash
        from quorune.token_creation import token_copy_snapshot
        from quorune.stack_resolution import trusted_generic_empty_resolution
        for index,(name,method,keywords) in enumerate((('Zoetic Cavern','morph',[]),
                ('Defenestrated Phantom','disguise',['Ward {2}']))):
            with self.subTest(method=method):
                session=H.session(self,959251+index);engine=session.engine
                source=H.add(self,engine,name,zone='hand',ref='FD_VALUES_SOURCE')
                H.ready(self,session,source,{'C':3})
                self.assertTrue(session.act('pilot:A',{'action_id':'cast-'+method+':'+source.ref,'pay':'auto'}).ok)
                item=next(row for row in engine.state.stack if row.card_object_id==source.object_id)
                self.assertIsNone(trusted_generic_empty_resolution(engine,replace(item,semantic_key='builtin:unknown-face-down'),None))
                self.assertIsNone(H.resolve(self,session))
                source=engine.state.cards[source.object_id];source.counters['+1/+1']=2
                before=authoritative_state_hash(engine.state)
                values=engine._copyable_characteristics(source);snapshot=token_copy_snapshot(engine,source)
                self.assertEqual(('',0,'Creature',keywords,[],[]),(values['name'],values['mana_value'],
                    values['type_line'],values['keywords'],values['produced_mana'],values['activated_abilities']))
                self.assertEqual((2,2),(int(values['power']),int(values['toughness'])))
                self.assertEqual('custom-copy:face-down',values[COPIABLE_ORACLE_FIELD])
                self.assertEqual(('', 'custom-copy:face-down'),(snapshot['printed_name'],snapshot['oracle_id']))
                self.assertEqual(before,authoritative_state_hash(engine.state))
                malformed=replace(source,annotations={key:value for key,value in source.annotations.items()
                    if key!=MORPH_FACE_DOWN_ANNOTATION})
                with self.assertRaises(GameRuleError):engine._copyable_characteristics(malformed)
                self.assertEqual(before,authoritative_state_hash(engine.state))

    def test_actual_morph_land_resolves_as_a_creature_then_turns_up_as_the_same_land(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.session(self,959255);engine=session.engine
        source=H.add(self,engine,'Zoetic Cavern',zone='hand',ref='MORPH_LAND_RESOLUTION')
        self.assert_normal_complete_program_closed(session,source)
        H.ready(self,session,source,{'C':5});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':'cast-morph:'+source.ref,'pay':'auto','reserve':{'C':2}}).ok)
        identity=engine.state.cards[source.object_id].logical_object_id
        self.assertIsNone(H.resolve(self,session),'A validated Morph spell resolves as its current face-down creature')
        self.assertTrue(engine.state.cards[source.object_id].face_down)
        self.assertEqual('Creature',engine._effective_card_data(engine.state.cards[source.object_id])['type_line'])
        self.assertTrue(session.act('pilot:A',{'action_id':'turn-face-up:'+source.ref,'pay':'auto'}).ok)
        current=engine.state.cards[source.object_id]
        self.assertEqual(identity,current.logical_object_id)
        self.assertEqual('Land',engine._effective_card_data(current)['type_line'])
        action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+source.ref+':') and 'Add {C}' in row['label'])
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual(1,engine.state.players['A'].mana_pool['C'])
        H.replay(self,session,load=True)

    def test_face_down_empty_resolution_requires_current_complete_method_and_preserves_absence(self):
        from copy import deepcopy
        from dataclasses import replace
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.compiled_morph import current_face_down_spell_is_trusted
        from quorune.record import authoritative_state_hash
        from quorune.stack_resolution import trusted_generic_empty_resolution
        session=H.session(self,959256);engine=session.engine
        card=H.add(self,engine,'Zoetic Cavern',zone='hand',ref='FD_RESOLUTION_CONTRACT')
        H.ready(self,session,card,{'C':3})
        self.assertTrue(session.act('pilot:A',{'action_id':'cast-morph:'+card.ref,'pay':'auto'}).ok)
        item=next(row for row in engine.state.stack if row.card_object_id==card.object_id)
        self.assertTrue(current_face_down_spell_is_trusted(engine,item,card))
        before=authoritative_state_hash(engine.state)
        for field,value in (('semantic_key','builtin:unknown-face-down'),('default_destination','graveyard')):
            with self.subTest(field=field):
                changed=replace(item,**{field:value})
                self.assertFalse(current_face_down_spell_is_trusted(engine,changed,card))
                self.assertIsNone(trusted_generic_empty_resolution(engine,changed,None))
        with self.assertRaises(ValueError):replace(item,kind='ability')
        for field,value in (('schema_version',True),('kind','unknown'),('spec_fingerprint','stale')):
            annotations=deepcopy(card.annotations);annotations['face_down_method'][field]=value
            self.assertFalse(current_face_down_spell_is_trusted(engine,item,replace(card,annotations=annotations)))
        self.assertEqual(before,authoritative_state_hash(engine.state))
        engine.state.face_down_characteristics_version=None
        self.assertIsNone(trusted_generic_empty_resolution(engine,item,None),'Historical absence preserves the former printed-type resolution boundary')

    def test_historical_absent_face_down_spell_copy_keeps_original_representation_and_replay(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.session(self,959257);engine=session.engine
        engine.state.token_copy_value_version=None;engine.state.face_down_characteristics_version=None
        observer=H.add(self,engine,'Generic Complete Creature Copy Observer',zone='hand',ref='OLD_FD_COPY_OBSERVER')
        source=H.add(self,engine,'Lumithread Field',zone='hand',ref='OLD_FD_COPY_SOURCE')
        offer=H.ready(self,session,observer,{'C':4,'U':1});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto','reserve':{'C':3}}).ok)
        self.assertIsNone(H.resolve(self,session))
        self.assertTrue(session.act('pilot:A',{'action_id':'cast-morph:'+source.ref,'pay':'auto'}).ok)
        self.assertIsNone(H.resolve(self,session))
        token=next(card for card in engine.state.cards.values() if card.is_token and card.zone=='battlefield')
        self.assertEqual(source.oracle_id,token.oracle_id)
        self.assertEqual(('Lumithread Field','Enchantment'),
            (engine._effective_card_data(token)['name'],engine._effective_card_data(token)['type_line']))
        self.assertNotIn('token_copy_value_version',engine.state.to_dict())
        H.replay(self,session,load=True)

    def test_face_down_permanent_copy_retains_disguise_ward_and_anonymous_identity_with_real_payment(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=H.session(self,959253);engine=session.engine
        source=H.add(self,engine,'Defenestrated Phantom',zone='hand',ref='FD_WARD_ORIGINAL')
        copy_spell=H.add(self,engine,'Cackling Counterpart',zone='hand',ref='FD_WARD_COPY')
        frog=H.add(self,engine,'Turn to Frog',seat='B',zone='hand',ref='FD_WARD_TARGETING_SPELL')
        for card in (source,copy_spell,frog):self.assert_normal_complete_program_closed(session,card)
        H.ready(self,session,source,{'C':4,'U':2})
        engine.state.players['B'].mana_pool.update({'C':3,'U':1});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':'cast-disguise:'+source.ref,'pay':'auto','reserve':{'C':1,'U':2}}).ok)
        self.assertIsNone(H.resolve(self,session))
        self.assertTrue(session.act('pilot:A',{'action_id':'cast:'+copy_spell.ref,'targets':[source.ref],'pay':'auto'}).ok)
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertIsNone(H.resolve(self,session))
        token=next(card for card in engine.state.cards.values() if card.is_token and card.zone=='battlefield')
        values=engine._effective_card_data(token)
        self.assertEqual(('', 'Creature',['Ward {2}']),(values['name'],values['type_line'],values['keywords']))
        self.assertFalse(token.face_down)
        public=str(session.packet('pilot:B',full=True)['state'])
        self.assertFalse('Defenestrated Phantom' in public)
        self.assertFalse(source.oracle_id[:8] in public)
        self.assertTrue(session.act('pilot:A',{'action_id':'pass'}).ok)
        accepted=session.act('pilot:B',{'action_id':'cast:'+frog.ref,'targets':[token.ref],
            'pay':'auto','reserve':{'C':2}})
        self.assertTrue(accepted.ok,accepted.summary)
        decision=H.resolve(self,session)
        self.assertEqual('semantic.choice',decision.kind);self.assertEqual(['B'],decision.actors)
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:C',{'action_id':'choose','pay':True}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertTrue(session.act('pilot:B',{'action_id':'choose','pay':True}).ok)
        self.assertIsNone(H.resolve(self,session))
        self.assertEqual(0,sum(engine.state.players['B'].mana_pool.values()))
        self.assertEqual((1,1),(engine._numeric_stat(token.object_id,'power'),engine._numeric_stat(token.object_id,'toughness')))
        self.assertEqual([],engine._effective_card_data(engine.state.cards[token.object_id])['keywords'])
        self.assertTrue(any(event.code=='ward.paid' for event in engine.state.events))
        H.replay(self,session,load=True)

    def test_anonymous_spell_copy_keeps_builtin_provenance_without_a_physical_card_record(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record_trust import semantic_execution_provenance_row
        from quorune.stack_resolution import physical_stack_record
        session=H.session(self,959254);engine=session.engine
        source=H.add(self,engine,'Lumithread Field',zone='hand',ref='FD_EXECUTION_SOURCE')
        H.ready(self,session,source,{'C':3})
        self.assertTrue(session.act('pilot:A',{'action_id':'cast-morph:'+source.ref,'pay':'auto'}).ok)
        original=next(item for item in engine.state.stack if item.card_object_id==source.object_id)
        copied=engine._copy_stack_item(controller='A',target=original,targets=[],target_groups={},reason='private execution-origin witness')
        card=engine.state.cards[copied.card_object_id]
        self.assertTrue(card.oracle_id.startswith('custom-copy:'));self.assertNotEqual(source.oracle_id,card.oracle_id)
        self.assertIsNone(physical_stack_record(engine,copied))
        row=semantic_execution_provenance_row(engine,copied.semantic_key,capability_registry=self.registry,
            profile='commander_review',stack_item=copied)
        self.assertTrue(row['builtin']);self.assertEqual('builtin:morph-face-down',row['key'])
        self.assertIsNone(row['oracle_id'])
        card.oracle_id=source.oracle_id
        previous_row=semantic_execution_provenance_row(engine,copied.semantic_key,capability_registry=self.registry,
            profile='commander_review',stack_item=copied)
        self.assertEqual(row,previous_row,'The builtin execution provenance stays identical to the former physical-ID representation')

    def test_double_faced_spell_copy_uses_departure_faces_and_becomes_the_same_transformable_token(self):
        self._double_faced_spell_copy_journey(copy_value_mode=1)

    def test_historical_double_faced_spell_copy_keeps_absent_face_transport_and_exact_replay(self):
        self._double_faced_spell_copy_journey(copy_value_mode=None)

    def _double_faced_spell_copy_journey(self,*,copy_value_mode):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.defined_token_faces import DEFINED_TOKEN_IDENTITY_FIELD
        from quorune.record import authoritative_state_hash
        session=H.session(self,959248);engine=session.engine
        engine.state.token_copy_value_version=copy_value_mode
        observer=H.add(self,engine,'Generic Complete Creature Copy Observer',zone='hand',ref='DFC_COPY_OBSERVER')
        source=H.add(self,engine,'Ulvenwald Captive // Ulvenwald Abomination',zone='hand',ref='DFC_COPIED_SPELL')
        counter=H.add(self,engine,'Counterspell',seat='B',zone='hand',ref='DFC_ORIGINAL_COUNTER')
        secret=H.add(self,engine,'Generic Bound Growth',seat='B',zone='hand',ref='DFC_COPY_PRIVATE_HAND')
        for card in (observer,source,counter):self.assert_normal_complete_program_closed(session,card)
        offer=H.ready(self,session,observer,{'C':7,'U':1,'G':3})
        engine.state.players['B'].mana_pool['U']=2;H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto','reserve':{'C':6,'G':3}}).ok)
        self.assertIsNone(H.resolve(self,session))
        self.assertTrue(session.act('pilot:A',{'action_id':'cast:'+source.ref,'pay':'auto','reserve':{'C':5,'G':2}}).ok)
        original=next(item for item in engine.state.stack if item.card_object_id==source.object_id)
        self.assertTrue(any(item.source_object_id==observer.object_id for item in engine.state.stack),
            [(item.kind,item.label,item.source_object_id) for item in engine.state.stack])
        for _ in range(4):
            if session.pending_principals()==['pilot:B']:break
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:C',{'action_id':'cast:'+counter.ref,'targets':[original.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertTrue(session.act('pilot:B',{'action_id':'cast:'+counter.ref,'targets':[original.ref],'pay':'auto'}).ok)
        for _ in range(12):
            if engine.state.cards[source.object_id].zone=='graveyard':break
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        self.assertEqual('graveyard',engine.state.cards[source.object_id].zone)
        session=H.replay(self,session,load=True);engine=session.engine
        for _ in range(12):
            if any(item.kind=='spell_copy' for item in engine.state.stack):break
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        copied=next(item for item in engine.state.stack if item.kind=='spell_copy')
        copied_id=copied.card_object_id;identity=engine.state.cards[copied_id].logical_object_id
        self.assertEqual('spell_copy',engine.state.cards[copied_id].object_kind)
        self.assertFalse(engine.state.cards[copied_id].is_token)
        if copy_value_mode==1:self.assertEqual(2,engine.state.cards[copied_id].annotations[DEFINED_TOKEN_IDENTITY_FIELD]['schema_version'])
        else:self.assertNotIn(DEFINED_TOKEN_IDENTITY_FIELD,engine.state.cards[copied_id].annotations)
        session=H.replay(self,session,load=True);engine=session.engine
        created=sum(event.code=='token.create' for event in engine.state.events)
        self.assertIsNone(H.resolve(self,session))
        token=engine.state.cards[copied_id]
        self.assertEqual(('battlefield','token',identity),(token.zone,token.object_kind,token.logical_object_id))
        self.assertEqual(created,sum(event.code=='token.create' for event in engine.state.events))
        timestamp=token.zone_timestamp
        action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+token.ref+':') and 'Transform' in row['label'])
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertIsNone(H.resolve(self,session))
        current=engine.state.cards[copied_id]
        self.assertEqual('Ulvenwald Abomination' if copy_value_mode==1 else 'Ulvenwald Captive',engine._effective_card_data(current)['name'])
        self.assertEqual(1 if copy_value_mode==1 else 0,current.transform_count)
        if copy_value_mode==1:self.assertGreater(current.zone_timestamp,timestamp)
        else:self.assertEqual(timestamp,current.zone_timestamp)
        self.assertEqual('graveyard',engine.state.cards[source.object_id].zone)
        self.assertNotIn(secret.ref,str(session.packet('pilot:A',full=True)))
        H.replay(self,session,load=True)

    def test_direct_and_repeated_spell_copy_faces_are_read_only_and_invalid_creation_rolls_back(self):
        from copy import deepcopy
        from dataclasses import replace
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.defined_token_faces import DEFINED_TOKEN_FACES_FIELD,DEFINED_TOKEN_IDENTITY_FIELD
        from quorune.record import authoritative_state_hash
        from quorune.token_copy_faces import spell_copy_characteristics
        session=H.session(self,959249);engine=session.engine
        source=H.add(self,engine,'Ulvenwald Captive // Ulvenwald Abomination',zone='hand',ref='DIRECT_DFC_SPELL')
        offer=H.ready(self,session,source,{'C':1,'G':1})
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
        original=next(item for item in engine.state.stack if item.card_object_id==source.object_id)
        first=engine._copy_stack_item(controller='A',target=original,targets=[],target_groups={},reason='direct copy owner witness')
        card=engine.state.cards[first.card_object_id]
        self.assertEqual('spell_copy',card.object_kind);self.assertFalse(card.is_token)
        before=authoritative_state_hash(engine.state)
        values=spell_copy_characteristics(engine,card)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertEqual(2,values[DEFINED_TOKEN_FACES_FIELD]['schema_version'])
        malformed_source=replace(card,annotations={key:value for key,value in card.annotations.items()
            if key!=DEFINED_TOKEN_IDENTITY_FIELD})
        with self.assertRaises(ValueError):spell_copy_characteristics(engine,malformed_source)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        second=engine._copy_stack_item(controller='A',target=first,targets=[],target_groups={},reason='repeat copy owner witness')
        repeated=engine.state.cards[second.card_object_id]
        self.assertNotEqual(card.object_id,repeated.object_id)
        self.assertEqual(card.annotations[DEFINED_TOKEN_IDENTITY_FIELD],repeated.annotations[DEFINED_TOKEN_IDENTITY_FIELD])
        malformed=deepcopy(values);malformed[DEFINED_TOKEN_FACES_FIELD]['schema_version']=True
        before=authoritative_state_hash(engine.state)
        with self.assertRaises(ValueError):
            with engine.transaction():
                engine._copy_stack_item(controller='A',target=original,targets=[],target_groups={},
                    reason='malformed physical copy',copiable_characteristics=malformed)
        self.assertEqual(before,authoritative_state_hash(engine.state))

    def test_actual_token_copy_of_oracle_double_face_retains_its_transform_and_back_mana_ability(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=H.session(self,959244);engine=session.engine
        source=H.add(self,engine,'Ulvenwald Captive // Ulvenwald Abomination',ref='COPIED_PHYSICAL_DFC')
        source.active_face='Ulvenwald Captive'
        source.acquired_control_turn_count=-1;source.counters['+1/+1']=2;source.marked_damage=1
        spell=H.add(self,engine,'Cackling Counterpart',zone='hand',ref='COPY_PHYSICAL_DFC_SPELL')
        offer=H.ready(self,session,spell,{'C':6,'U':2,'G':2});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'targets':[source.ref],
            'pay':'auto','reserve':{'C':5,'G':2}}).ok)
        self.assertIsNone(H.resolve(self,session))
        token=next(card for card in engine.state.cards.values() if card.is_token and card.zone=='battlefield')
        self.assertEqual({},token.counters);self.assertEqual(0,token.marked_damage)
        self.assertEqual(0,token.transform_count)
        timestamp=token.zone_timestamp
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        activation=next(row for row in actions if row['id'].startswith('activate:'+token.ref+':') and 'Transform' in row['label'])
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:B',{'action_id':activation['id'],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertTrue(session.act('pilot:A',{'action_id':activation['id'],'pay':'auto'}).ok)
        self.assertIsNone(H.resolve(self,session))
        current=engine.state.cards[token.object_id]
        self.assertEqual('Ulvenwald Abomination',engine._effective_card_data(current)['name'])
        self.assertEqual(1,current.transform_count)
        self.assertGreater(current.zone_timestamp,timestamp)
        self.assertEqual('Ulvenwald Captive',engine._effective_card_data(engine.state.cards[source.object_id])['name'])
        back=next(ability for ability in engine._activated_abilities(current) if 'Add {C}{C}' in ability.effect_text)
        self.assertFalse(any('Transform' in ability.effect_text for ability in engine._activated_abilities(current)))
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'activate:'+current.ref+':'+back.ability_id,'pay':'auto'}).ok,
            'The newly created creature token still cannot pay a tap cost this turn')
        self.assertEqual(before,authoritative_state_hash(engine.state))
        H.replay(self,session,load=True)

    def test_actual_back_face_copy_has_both_physical_faces_after_pending_load_and_read_only_capture(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.defined_token_faces import defined_token_definition,DEFINED_TOKEN_FACES_FIELD
        from quorune.record import authoritative_state_hash
        from quorune.token_creation import token_copy_snapshot
        session=H.session(self,959245);engine=session.engine
        source=H.add(self,engine,'Ulvenwald Captive // Ulvenwald Abomination',ref='BACK_COPY_ORIGINAL')
        source.active_face='Ulvenwald Abomination'
        spell=H.add(self,engine,'Cackling Counterpart',zone='hand',ref='BACK_COPY_SPELL')
        secret=H.add(self,engine,'Generic Bound Growth',zone='hand',ref='BACK_COPY_PRIVATE_HAND')
        offer=H.ready(self,session,spell,{'C':1,'U':2});H.checkpoint(self,session)
        before=authoritative_state_hash(engine.state);public=session.packet('pilot:B',full=True)['state']
        snapshot=token_copy_snapshot(engine,source)
        self.assertEqual(2,snapshot['characteristics'][DEFINED_TOKEN_FACES_FIELD]['schema_version'])
        snapshot['characteristics'][DEFINED_TOKEN_FACES_FIELD]['front']['characteristics']['name']='tampered detached result'
        self.assertEqual(before,authoritative_state_hash(engine.state));self.assertEqual(public,session.packet('pilot:B',full=True)['state'])
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'targets':[source.ref],'pay':'auto'}).ok)
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertIsNone(H.resolve(self,session))
        token=next(card for card in engine.state.cards.values() if card.is_token and card.zone=='battlefield')
        physical=defined_token_definition(token)
        self.assertEqual(('Ulvenwald Captive','Ulvenwald Abomination'),(physical.front.name,physical.back.name))
        self.assertEqual('Ulvenwald Abomination',token.active_face)
        self.assertEqual('Ulvenwald Abomination',engine._effective_card_data(token)['name'])
        self.assertTrue(any('Add {C}{C}' in ability.effect_text for ability in engine._activated_abilities(token)))
        self.assertNotIn(secret.ref,str(session.packet('pilot:B',full=True)))
        H.replay(self,session,load=True)

    def test_actual_copy_of_a_merged_physical_double_face_stays_single_faced_after_paid_transform(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.defined_token_faces import defined_token_definition
        session=H.session(self,959246);engine=session.engine
        root=H.add(self,engine,'Ulvenwald Captive // Ulvenwald Abomination',ref='MERGED_DFC_ROOT')
        root.active_face='Ulvenwald Captive'
        part=H.add(self,engine,'Ornithopter',zone='hand',ref='MERGED_DFC_PART')
        offer=H.ready(self,session,part,{'C':0})
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
        stack_ref=next(item.ref for item in engine.state.stack if item.card_object_id==part.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
            target=Identity(root.object_id,root.logical_object_id),source=Identity(part.object_id,part.logical_object_id),
            stack_ref=stack_ref,position=MergePosition.BOTTOM))
        spell=H.add(self,engine,'Cackling Counterpart',zone='hand',ref='MERGED_DFC_COPY_SPELL')
        offer=H.ready(self,session,spell,{'C':6,'G':2,'U':2});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'targets':[root.ref],
            'pay':'auto','reserve':{'C':5,'G':2}}).ok)
        self.assertIsNone(H.resolve(self,session))
        token=next(card for card in engine.state.cards.values() if card.is_token and card.zone=='battlefield')
        self.assertIsNone(defined_token_definition(token));self.assertIsNone(token.permanent_composition)
        timestamp=token.zone_timestamp
        activation=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+token.ref+':') and 'Transform' in row['label'])
        self.assertTrue(session.act('pilot:A',{'action_id':activation['id'],'pay':'auto'}).ok)
        self.assertIsNone(H.resolve(self,session))
        current=engine.state.cards[token.object_id]
        self.assertEqual((0,timestamp),(current.transform_count,current.zone_timestamp))
        self.assertEqual('Ulvenwald Captive',engine._effective_card_data(current)['name'])
        self.assertEqual(2,len(engine.state.cards[root.object_id].permanent_composition.parts))
        H.replay(self,session,load=True)

    def test_historical_absent_copy_value_mode_keeps_the_original_single_face_snapshot_and_replay(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.defined_token_faces import defined_token_definition,DEFINED_TOKEN_FACES_FIELD
        session=H.session(self,959247);engine=session.engine;engine.state.token_copy_value_version=None
        source=H.add(self,engine,'Thraben Gargoyle // Stonewing Antagonizer',ref='LEGACY_DFC_COPY_SOURCE')
        source.active_face='Thraben Gargoyle'
        spell=H.add(self,engine,'Cackling Counterpart',zone='hand',ref='LEGACY_DFC_COPY_SPELL')
        offer=H.ready(self,session,spell,{'C':7,'U':2});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'targets':[source.ref],
            'pay':'auto','reserve':{'C':6}}).ok)
        self.assertIsNone(H.resolve(self,session))
        token=next(card for card in engine.state.cards.values() if card.is_token and card.zone=='battlefield')
        self.assertIsNone(defined_token_definition(token))
        self.assertNotIn(DEFINED_TOKEN_FACES_FIELD,token.annotations['copy_overrides'])
        action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+token.ref+':') and 'Transform' in row['label'])
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertIsNone(H.resolve(self,session))
        self.assertEqual(0,engine.state.cards[token.object_id].transform_count)
        H.replay(self,session,load=True)

    def test_prepared_physical_transform_is_read_only_and_changes_only_eligible_component_faces(self):
        from dataclasses import replace
        from quorune.rules.composition_faces import prepare_composition_transform
        from quorune.record import authoritative_state_hash
        session,root,_gargoyle=self.physical_face_fixture();engine=session.engine
        identity=Identity(root.object_id,root.logical_object_id);before=authoritative_state_hash(engine.state)
        plan=prepare_composition_transform(engine,identity,expected_transform_count=0)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertEqual(('FACE_GARGOYLE','FACE_CAPTIVE'),plan.changed_refs)
        values={part.card_state['ref']:part for part in plan.after.parts}
        self.assertEqual('Stonewing Antagonizer',values['FACE_GARGOYLE'].card_state['active_face'])
        self.assertEqual('Ulvenwald Abomination',values['FACE_CAPTIVE'].card_state['active_face'])
        from quorune.entry_copy import COPIABLE_FACE_FIELD
        self.assertEqual('Ulvenwald Abomination',values['FACE_CAPTIVE'].copiable_values.get(COPIABLE_FACE_FIELD))
        self.assertNotIn('Defender',values['FACE_GARGOYLE'].copiable_values['keywords'])
        self.assertEqual(plan.before.parts[0],plan.after.parts[0])
        self.assertEqual(plan.before.topology,plan.after.topology);self.assertEqual(plan.before.merges,plan.after.merges)
        with self.assertRaises(ValueError):replace(plan,changed_refs=('FACE_ROOT',))
        from quorune.replacement.immutable import FrozenMap,thaw_value
        altered=thaw_value(plan.after.parts[1].card_state);altered['counters']={'unexpected':1}
        invalid_part=replace(plan.after.parts[1],card_state=FrozenMap(altered))
        invalid_after=replace(plan.after,parts=(plan.after.parts[0],invalid_part,*plan.after.parts[2:]))
        with self.assertRaises(ValueError):replace(plan,after=invalid_after)
        with self.assertRaises(ValueError):prepare_composition_transform(engine,identity,expected_transform_count=True)
        self.assertIsNone(prepare_composition_transform(engine,identity,expected_transform_count=1))
        self.assertEqual(before,authoritative_state_hash(engine.state))

    def test_buried_transform_activation_turns_every_physical_double_face_once(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session,root,gargoyle=self.physical_face_fixture();engine=session.engine
        root=engine.state.cards[root.object_id];identity=root.logical_object_id;timestamp=root.zone_timestamp
        H.ready(self,session,root,{'C':12})
        ability=next(value for value in engine._activated_abilities(root)
            if value.builtin_semantic_key and value.builtin_semantic_key.startswith(gargoyle.oracle_id+':ability:'))
        action_id='activate:'+root.ref+':'+ability.ability_id
        H.checkpoint(self,session)
        from quorune.record import authoritative_state_hash
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:B',{'action_id':action_id,'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertTrue(session.act('pilot:A',{'action_id':action_id,'pay':'auto'}).ok)
        self.assertEqual(0,engine.state.stack[-1].context.get('source_transform_count'))
        second=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id']==action_id)
        self.assertTrue(session.act('pilot:A',{'action_id':second['id'],'pay':'auto'}).ok)
        self.assertEqual([0,0],[item.context.get('source_transform_count') for item in engine.state.stack])
        self.assertEqual(0,engine.state.players['A'].mana_pool.get('C',0))
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertIsNone(H.resolve(self,session))
        root=engine.state.cards[root.object_id]
        self.assertEqual(1,root.transform_count)
        faces={part.card_state['ref']:part.card_state.get('active_face') for part in root.permanent_composition.parts}
        self.assertEqual('Stonewing Antagonizer',faces['FACE_GARGOYLE'])
        self.assertEqual('Ulvenwald Abomination',faces['FACE_CAPTIVE'])
        self.assertIsNone(root.active_face)
        self.assertEqual((identity,timestamp,{'charge':3},1),
            (root.logical_object_id,root.zone_timestamp,root.counters,root.marked_damage))
        self.assertEqual(1,sum(event.code=='permanent.transform' for event in engine.state.events))
        self.assertNotIn('defender',{word.casefold() for word in engine._effective_card_data(root)['keywords']})
        action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:'+root.ref+':') and 'Add {C}{C}' in row['label'])
        mana=engine.state.players['A'].mana_pool.get('C',0)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual(mana+2,engine.state.players['A'].mana_pool['C'])
        H.replay(self,session)

    def test_shared_transform_preparation_is_read_only_for_a_composed_parent(self):
        from quorune.permanent_transform import _prepared_transform_faces
        from quorune.record import authoritative_state_hash
        session,root,_gargoyle=self.physical_face_fixture();engine=session.engine
        before=authoritative_state_hash(engine.state);clock=engine.state.timestamp_sequence
        result=_prepared_transform_faces(engine,root,expected_transform_count=0,day_night_instruction=False)
        self.assertIsNotNone(result)
        self.assertEqual(before,authoritative_state_hash(engine.state),'Batch preparation must not commit physical components')
        self.assertEqual(clock,engine.state.timestamp_sequence)
        self.assertEqual(0,root.transform_count)

    def test_mixed_transform_owner_orders_only_timestamped_subjects_and_commits_composed_parts_once(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.permanent_transform import commit_transform_batch
        from quorune.transform_order_model import TransformTimestampOrderRequired,TransformTimestampContinuation,TransformTimestampModelError
        from quorune.transform_timestamp_order import _selected_order
        from quorune.replacement.immutable import FrozenMap
        from quorune.record import authoritative_state_hash
        session,root,_gargoyle=self.physical_face_fixture();engine=session.engine
        first=H.add(self,engine,'Thraben Gargoyle // Stonewing Antagonizer',ref='MIXED_TIME_FIRST')
        second=H.add(self,engine,'Thraben Gargoyle // Stonewing Antagonizer',ref='MIXED_TIME_SECOND')
        cards=(root,first,second);counts={card.object_id:0 for card in cards}
        before=authoritative_state_hash(engine.state);clock=engine.state.timestamp_sequence;root_timestamp=root.zone_timestamp
        with self.assertRaises(TransformTimestampOrderRequired) as required:
            commit_transform_batch(engine,cards,reason='Mixed physical owner fixture',day_night_instruction=False,expected_transform_counts=counts)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        request=required.exception.request
        self.assertEqual({first.object_id,second.object_id},{row['object_id'] for row in request.subjects})
        self.assertEqual((('A',(first.ref,second.ref)),),request.choices)
        context=FrozenMap({name:getattr(engine.state,name) for name in ('active_player','turn_sequence','phase_index','phase','step')})
        frame=TransformTimestampContinuation(request,context,'stabilize',engine.state.priority_player,())
        with self.assertRaises(TransformTimestampModelError):frame.with_order((root.ref,first.ref))
        frame=frame.with_order((second.ref,first.ref))
        frame=TransformTimestampContinuation.from_dict(frame.to_dict())
        with _selected_order(frame):
            results=commit_transform_batch(engine,cards,reason='Mixed physical owner fixture',
                day_night_instruction=False,expected_transform_counts=counts)
        self.assertEqual(3,len(results))
        self.assertEqual((clock+1,clock+2),(second.zone_timestamp,first.zone_timestamp))
        self.assertEqual(root_timestamp,root.zone_timestamp)
        self.assertEqual(clock+2,engine.state.timestamp_sequence)
        self.assertEqual((1,1,1),tuple(card.transform_count for card in cards))
        self.assertEqual(3,sum(event.code=='permanent.transform' for event in engine.state.events))

    def test_composition_transform_commit_rejects_stale_and_forged_plans_and_rolls_back_partial_write(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune.rules.composition_faces import prepare_composition_transform
        from quorune.composition_storage import PermanentCompositionStorage
        from quorune.replacement.immutable import FrozenMap
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.errors import GameRuleError,StateInvariantError
        from quorune.record import authoritative_state_hash
        session,root,_gargoyle=self.physical_face_fixture();engine=session.engine
        identity=Identity(root.object_id,root.logical_object_id)
        plan=prepare_composition_transform(engine,identity,expected_transform_count=0)
        owner=ZoneTransitionOwner(engine)
        changed=replace(plan.after.parts[1],copiable_values=FrozenMap({'name':'Forged physical result'}))
        forged=replace(plan,after=replace(plan.after,parts=(plan.after.parts[0],changed,*plan.after.parts[2:])))
        before=authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):owner.commit_composition_transform(forged)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        engine.state.cards[root.object_id].counters['charge']+=1
        stale=authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):owner.commit_composition_transform(plan)
        self.assertEqual(stale,authoritative_state_hash(engine.state))
        engine.state.cards[root.object_id].counters['charge']-=1
        self.assertEqual(before,authoritative_state_hash(engine.state))
        original=PermanentCompositionStorage.validate_carrier
        def fail_after_write(storage,card):
            original(storage,card)
            if card.transform_count==1:raise StateInvariantError('Injected post-write carrier failure')
        with patch.object(PermanentCompositionStorage,'validate_carrier',new=fail_after_write):
            with self.assertRaises(StateInvariantError):owner.commit_composition_transform(plan)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        clock=engine.state.timestamp_sequence;timestamp=engine.state.cards[root.object_id].zone_timestamp
        self.assertEqual(plan.after,owner.commit_composition_transform(plan))
        current=engine.state.cards[root.object_id]
        self.assertEqual(1,current.transform_count)
        self.assertEqual((clock,timestamp),(engine.state.timestamp_sequence,current.zone_timestamp))
        self.assertEqual(plan.before.topology,current.permanent_composition.topology)

    def morphed_composition_fixture(self,*,other_name='Zoetic Cavern',other_method='morph'):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.zone_transitions import ZoneTransitionOwner
        session=H.session(self,959231);engine=session.engine;root=None
        for name,ref,method in (('Lumithread Field','MORPH_ROOT','morph'),(other_name,'MORPH_BURIED',other_method)):
            card=H.add(self,engine,name,zone='hand',ref=ref)
            H.ready(self,session,card,{'C':3})
            actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            offer=next(row for row in actions if row['id']=='cast-'+method+':'+ref)
            self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
            if root is None:
                self.assertIsNone(H.resolve(self,session));root=engine.state.cards[card.object_id]
            else:
                stack_ref=next(item.ref for item in engine.state.stack if item.card_object_id==card.object_id)
                ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
                    target=Identity(root.object_id,root.logical_object_id),source=Identity(card.object_id,card.logical_object_id),
                    stack_ref=stack_ref,position=MergePosition.BOTTOM))
        return session,engine.state.cards[root.object_id]

    def manifested_composition_fixture(self,*,manifested_name='Viashino Firstblade'):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.zone_transitions import ZoneTransitionOwner
        session=H.session(self,959241);engine=session.engine
        spell=H.add(self,engine,'Soul Summons',zone='hand',ref='COMPOSED_MANIFEST_SOURCE')
        root=H.add(self,engine,manifested_name,zone='library',ref='COMPOSED_MANIFESTED')
        library=engine.state.players['A'].zones['library'];library.remove(root.object_id);library.append(root.object_id)
        offer=H.ready(self,session,spell,{'C':1,'W':1});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
        self.assertIsNone(H.resolve(self,session));root=engine.state.cards[root.object_id]
        other=H.add(self,engine,'Ornithopter',zone='hand',ref='COMPOSED_MANIFEST_BURIED')
        offer=H.ready(self,session,other,{'C':0})
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
        stack_ref=next(item.ref for item in engine.state.stack if item.card_object_id==other.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
            target=Identity(root.object_id,root.logical_object_id),source=Identity(other.object_id,other.logical_object_id),
            stack_ref=stack_ref,position=MergePosition.BOTTOM))
        return session,engine.state.cards[root.object_id]

    def test_actual_merged_manifest_offers_printed_creature_cost_and_turns_up_without_entering_again(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session,root=self.manifested_composition_fixture();engine=session.engine
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.players['A'].mana_pool.update({'C':1,'R':1,'W':1})
        engine._grant_priority('A');engine.pump();H.checkpoint(self,session)
        identity=root.logical_object_id;clock=engine.state.timestamp_sequence
        bottom=root.permanent_composition.parts[1]
        offers=[row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('turn-face-up:'+root.ref)]
        self.assertEqual(1,len(offers),'A merged manifested creature retains its printed-mana special action')
        offer=offers[0];self.assertEqual('manifest',offer['method'])
        self.assertEqual((1,1,1),(offer['requirements']['GENERIC'],offer['requirements']['R'],offer['requirements']['W']))
        self.assertNotIn('Viashino Firstblade',str(session.packet('pilot:B',full=True)))
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:B',{'action_id':offer['id'],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
        current=engine.state.cards[root.object_id]
        self.assertFalse(current.face_down);self.assertEqual(identity,current.logical_object_id)
        self.assertEqual(bottom,current.permanent_composition.parts[1])
        self.assertEqual((clock+1,clock+1),(engine.state.timestamp_sequence,current.zone_timestamp))
        self.assertEqual(2,engine._numeric_stat(current.object_id,'power'))
        self.assertEqual([],engine.state.stack)
        self.assertEqual(1,sum(event.code=='permanent.turn_face_up' for event in engine.state.events))
        H.replay(self,session,load=True)

    def test_actual_merged_manifest_keeps_printed_and_megamorph_prices_distinct(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        for method in ('manifest','megamorph'):
            with self.subTest(method=method):
                session,root=self.manifested_composition_fixture(manifested_name='Aerie Bowmasters');engine=session.engine
                engine.permissions.invalidate_current();engine.state.pending_decision=None
                engine.state.players['A'].mana_pool.update({'C':5,'G':2})
                engine._grant_priority('A');engine.pump();H.checkpoint(self,session)
                offers=[row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    if row['id'].startswith('turn-face-up:'+root.ref)]
                self.assertEqual({'manifest','megamorph'},{row['method'] for row in offers})
                offer=next(row for row in offers if row['method']==method)
                self.assertEqual((2,2) if method=='manifest' else (5,1),
                    (offer['requirements']['GENERIC'],offer['requirements']['G']))
                self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
                current=engine.state.cards[root.object_id]
                self.assertFalse(current.face_down)
                self.assertEqual(0 if method=='manifest' else 1,current.counters.get('+1/+1',0))
                self.assertEqual(3 if method=='manifest' else 1,sum(engine.state.players['A'].mana_pool.values()))
                H.replay(self,session,load=True)

    def test_merged_manifest_physical_card_authority_ignores_animation_and_copied_costs(self):
        from dataclasses import replace
        from quorune.continuous_effect_model import Layer
        from quorune.continuous_effect_state import ResolutionEffectSource,create_resolution_continuous_effect
        from quorune.continuous_effects import ContinuousOperation
        from quorune.rules.composition_face_up import composed_turn_up_options,composition_face_up_read_view
        from quorune.manifest import physical_manifest_turn_up_spec
        from quorune.record import authoritative_state_hash
        session,root=self.manifested_composition_fixture(manifested_name='Lumithread Field');engine=session.engine
        create_resolution_continuous_effect(engine,source=ResolutionEffectSource(stack_ref='fixture:manifest-animation'),
            targets=(root,),layer=Layer.TYPE,sublayer='4',
            operations=(ContinuousOperation('add_types',['Creature'],field='card_types'),))
        before=authoritative_state_hash(engine.state)
        _reader,_preview,data=composition_face_up_read_view(engine,root)
        self.assertIn('creature',engine._type_parts(data['type_line'])[0])
        choices=composed_turn_up_options(engine,'A',root)
        self.assertTrue(any(choice.spec.method=='morph' for choice in choices))
        self.assertFalse(any(choice.spec.method=='manifest' for choice in choices))
        physical=root.permanent_composition.parts[0].detached_card()
        self.assertIsNone(physical_manifest_turn_up_spec(engine,physical))
        self.assertIsNone(physical_manifest_turn_up_spec(engine,replace(physical,is_token=True,object_kind='token')))
        self.assertEqual(before,authoritative_state_hash(engine.state))
        session,root=self.manifested_composition_fixture();engine=session.engine
        physical=root.permanent_composition.parts[0].detached_card()
        physical.annotations['copy_overrides']={'type_line':'Creature','mana_cost':'{9}',
            '_copiable_oracle_id':self.db.lookup('Ornithopter').oracle_id}
        spec=physical_manifest_turn_up_spec(engine,physical)
        self.assertEqual((1,1,1),(spec.requirements_dict['GENERIC'],spec.requirements_dict['R'],spec.requirements_dict['W']))

    def test_actual_merged_manifest_double_faced_card_uses_its_physical_front(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session,root=self.manifested_composition_fixture(manifested_name='Thraben Gargoyle // Stonewing Antagonizer');engine=session.engine
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.players['A'].mana_pool.update({'C':1})
        engine._grant_priority('A');engine.pump();H.checkpoint(self,session)
        offer=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id']=='turn-face-up:'+root.ref+':manifest')
        self.assertEqual(1,offer['requirements']['GENERIC'])
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
        current=engine.state.cards[root.object_id]
        self.assertFalse(current.face_down)
        self.assertEqual('Thraben Gargoyle',current.permanent_composition.parts[0].card_state['active_face'])
        self.assertEqual('Thraben Gargoyle',engine._effective_card_data(current)['name'])
        self.assertEqual(0,current.transform_count)
        H.replay(self,session,load=True)

    def test_face_up_owner_reveals_a_refused_physical_spell_composition_without_status_or_clock_change(self):
        from quorune.zone_transitions import ZoneTransitionOwner
        session,root=self.manifested_composition_fixture(manifested_name='Divination');engine=session.engine
        clock=engine.state.timestamp_sequence;timestamp=root.zone_timestamp;count=root.transform_count
        storage=root.permanent_composition
        changed=ZoneTransitionOwner(engine).turn_face_up(root,viewers=tuple(engine.seats))
        self.assertFalse(changed)
        self.assertTrue(root.face_down)
        self.assertEqual((clock,timestamp,count),(engine.state.timestamp_sequence,root.zone_timestamp,root.transform_count))
        self.assertEqual(storage.topology,root.permanent_composition.topology)
        for before,after in zip(storage.parts,root.permanent_composition.parts):
            self.assertEqual(before.card_state['face_down'],after.card_state['face_down'])
            self.assertEqual(before.copiable_values,after.copiable_values)
            if before.card_state['face_down']:self.assertEqual(set(engine.seats),set(after.card_state['known_to']))
        self.assertEqual(0,sum(event.code=='permanent.turn_face_up' for event in engine.state.events))
        self.assertEqual(1,sum(event.code=='card.reveal' for event in engine.state.events))

    def assert_face_up_effect_original_closed(self,session,card):
        from dataclasses import replace
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.compiler.program_generation import _is_closed_effect_program
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),
            semantic_registry=session.engine.semantics,capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
        self.assertEqual('capability_closed',binding['trust_basis'])
        semantic=next(row for row in session.engine.semantics.programs_for_oracle(card.oracle_id)
            if 'fixed-target-face-up' in row.coverage)
        self.assertIs(True,semantic.provenance['requires_complete_card_program'])
        self.assertTrue(_is_closed_effect_program(semantic))
        for certificate in (None,{'schema_version':1,'oracle_ir_status':'partial','material_residual_count':1},
                {'schema_version':True,'oracle_ir_status':'exact','material_residual_count':0}):
            changed=replace(semantic,provenance={**semantic.provenance,'card_program_admission':certificate})
            self.assertFalse(_is_closed_effect_program(changed))
        self.assertFalse(_is_closed_effect_program(replace(semantic,provenance={
            key:value for key,value in semantic.provenance.items() if key!='requires_complete_card_program'})))

    def test_actual_break_open_turns_up_opponent_composition_and_its_controller_draws(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        from quorune.zone_object_state import mark_card_manifested
        session,root=self.morphed_composition_fixture(other_name='Fathom Seer');engine=session.engine
        spell=H.add(self,engine,'Break Open',seat='B',zone='hand',ref='ACTUAL_BREAK_OPEN')
        faceup=H.add(self,engine,'Ornithopter',ref='BREAK_OPEN_FACEUP_CONTROL')
        own=H.add(self,engine,'Grizzly Bears',seat='B',ref='BREAK_OPEN_OWN_FACEDOWN')
        mark_card_manifested(own,controller='B')
        self.assert_face_up_effect_original_closed(session,spell)
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.players['B'].mana_pool.update({'C':1,'R':1})
        engine._grant_priority('B');engine.pump();H.checkpoint(self,session)
        offer=next(row for row in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
            if row['id']=='cast:'+spell.ref)
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:A',{'action_id':offer['id'],'targets':[root.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        for ref in (faceup.ref,own.ref):
            self.assertFalse(session.act('pilot:B',{'action_id':offer['id'],'targets':[ref],'pay':'auto'}).ok)
            self.assertEqual(before,authoritative_state_hash(engine.state))
        hand=len(engine.state.players['A'].zones['hand']);timestamp=root.zone_timestamp
        self.assertTrue(session.act('pilot:B',{'action_id':offer['id'],'targets':[root.ref],'pay':'auto'}).ok)
        self.assertIsNone(H.resolve(self,session))
        current=engine.state.cards[root.object_id]
        self.assertFalse(current.face_down,[(event.code,event.details) for event in engine.state.events[-8:]])
        self.assertTrue(all(not part.card_state['face_down'] for part in current.permanent_composition.parts))
        self.assertGreater(current.zone_timestamp,timestamp)
        self.assertEqual(hand+2,len(engine.state.players['A'].zones['hand']))
        self.assertEqual(0,sum(engine.state.players['B'].mana_pool.values()))
        self.assertEqual(1,sum(event.code=='permanent.turn_face_up' for event in engine.state.events))
        H.replay(self,session,load=True)

    def test_refused_face_up_commit_rejects_stale_preparations_and_rolls_back_a_reveal_fault(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        from quorune.rules.composition_faces import prepare_composition_face_up
        from quorune.zone_transitions import ZoneTransitionOwner
        session,root=self.manifested_composition_fixture(manifested_name='Divination');engine=session.engine
        owner=ZoneTransitionOwner(engine)
        plan=prepare_composition_face_up(engine,Identity(root.object_id,root.logical_object_id))
        self.assertFalse(plan.can_turn_face_up)
        before=authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):owner.commit_composition_face_up(replace(plan,viewers=('A',)))
        self.assertEqual(before,authoritative_state_hash(engine.state))
        with patch.object(engine,'_log',side_effect=GameRuleError('injected refusal publication failure')):
            with self.assertRaises(GameRuleError):owner.commit_composition_face_up(plan)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        current=engine.state.cards[root.object_id]
        current.marked_damage+=1
        before=authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):owner.commit_composition_face_up(plan)
        self.assertEqual(before,authoritative_state_hash(engine.state))

    def test_actual_break_open_revalidates_a_target_turned_up_by_its_own_special_action(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session,root=self.morphed_composition_fixture(other_name='Fathom Seer');engine=session.engine
        spell=H.add(self,engine,'Break Open',seat='B',zone='hand',ref='BREAK_OPEN_REVALIDATED')
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.players['A'].mana_pool.update({'C':1,'W':1})
        engine.state.players['B'].mana_pool.update({'C':1,'R':1})
        engine._grant_priority('B');engine.pump();H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:B',{'action_id':'cast:'+spell.ref,'targets':[root.ref],'pay':'auto'}).ok)
        for _ in range(4):
            if session.pending_principals()==['pilot:A']:break
            self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
        self.assertEqual(['pilot:A'],session.pending_principals())
        hand=len(engine.state.players['A'].zones['hand'])
        self.assertTrue(session.act('pilot:A',{'action_id':'turn-face-up:'+root.ref,'pay':'auto'}).ok)
        timestamp=engine.state.cards[root.object_id].zone_timestamp
        self.assertIsNone(H.resolve(self,session))
        self.assertFalse(engine.state.cards[root.object_id].face_down)
        self.assertEqual(timestamp,engine.state.cards[root.object_id].zone_timestamp)
        self.assertEqual(hand+2,len(engine.state.players['A'].zones['hand']))
        self.assertEqual('graveyard',engine.state.cards[spell.object_id].zone)
        self.assertTrue(any(event.code=='target.illegal' for event in engine.state.events))
        self.assertEqual(1,sum(event.code=='permanent.turn_face_up' for event in engine.state.events))
        H.replay(self,session,load=True)

    def test_plain_manifested_spell_refusal_reveals_without_status_clock_or_face_up_trigger(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.zone_object_state import mark_card_manifested
        from quorune.zone_transitions import ZoneTransitionOwner
        session=H.session(self,959243);engine=session.engine
        card=H.add(self,engine,'Divination',ref='PLAIN_REFUSED_MANIFEST')
        mark_card_manifested(card,controller='A')
        clock=engine.state.timestamp_sequence;timestamp=card.zone_timestamp
        revealed=list(card.revealed_to)
        self.assertNotIn('Divination',str(session.packet('pilot:B',full=True)))
        self.assertFalse(ZoneTransitionOwner(engine).turn_face_up(card,viewers=tuple(engine.seats)))
        self.assertTrue(card.face_down)
        self.assertEqual((clock,timestamp),(engine.state.timestamp_sequence,card.zone_timestamp))
        self.assertEqual(revealed,card.revealed_to)
        self.assertEqual(set(engine.seats),set(card.known_to))
        self.assertEqual(1,sum(event.code=='card.reveal' for event in engine.state.events))
        self.assertEqual(0,sum(event.code=='permanent.turn_face_up' for event in engine.state.events))
        self.assertIn('Divination',str(session.packet('pilot:B',full=True)))

    def test_actual_break_open_refusal_reveals_only_the_merged_object_and_has_no_face_up_event(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session,root=self.manifested_composition_fixture(manifested_name='Divination');engine=session.engine
        spell=H.add(self,engine,'Break Open',seat='B',zone='hand',ref='REFUSED_BREAK_OPEN')
        secret=H.add(self,engine,'Generic Bound Growth',zone='hand',ref='REFUSAL_PRIVATE_HAND')
        self.assert_face_up_effect_original_closed(session,spell)
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.players['B'].mana_pool.update({'C':1,'R':1})
        engine._grant_priority('B');engine.pump();H.checkpoint(self,session)
        self.assertNotIn('Divination',str(session.packet('pilot:B',full=True)))
        timestamp=root.zone_timestamp;storage=root.permanent_composition
        offer=next(row for row in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
            if row['id']=='cast:'+spell.ref)
        self.assertTrue(session.act('pilot:B',{'action_id':offer['id'],'targets':[root.ref],'pay':'auto'}).ok)
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertIsNone(H.resolve(self,session))
        current=engine.state.cards[root.object_id]
        self.assertTrue(current.face_down);self.assertEqual(timestamp,current.zone_timestamp)
        self.assertEqual(storage.topology,current.permanent_composition.topology)
        for before,after in zip(storage.parts,current.permanent_composition.parts):
            self.assertEqual(before.card_state['face_down'],after.card_state['face_down'])
            self.assertEqual(before.copiable_values,after.copiable_values)
        self.assertEqual(2,engine._numeric_stat(current.object_id,'power'))
        self.assertEqual(0,sum(event.code=='permanent.turn_face_up' for event in engine.state.events))
        self.assertEqual(1,sum(event.code=='card.reveal' for event in engine.state.events))
        self.assertIn('Divination',str(session.packet('pilot:B',full=True)))
        self.assertNotIn(secret.ref,str(session.packet('pilot:B',full=True)))
        H.replay(self,session,load=True)

    def test_prepared_face_up_restores_all_parts_without_live_reveal_or_state_change(self):
        from dataclasses import replace
        from quorune.rules.composition_faces import prepare_composition_face_up
        from quorune.composition_characteristics import composition_copiable_values
        from quorune.record import authoritative_state_hash
        from quorune.replacement.immutable import FrozenMap,thaw_value
        session,root=self.morphed_composition_fixture();engine=session.engine
        before=authoritative_state_hash(engine.state);public=session.packet('pilot:B',full=True)['state']
        plan=prepare_composition_face_up(engine,Identity(root.object_id,root.logical_object_id))
        self.assertTrue(plan.can_turn_face_up)
        self.assertTrue(all(not part.card_state['face_down'] for part in plan.after.parts))
        self.assertTrue(all('face_down_method' not in part.card_state['annotations'] for part in plan.after.parts))
        values=composition_copiable_values(plan.after)
        self.assertEqual('Enchantment',values['type_line']);self.assertIn('Morph',values['keywords'])
        self.assertTrue(any('Add {C}' in ability['effect_text'] for ability in values['activated_abilities']))
        self.assertEqual(before,authoritative_state_hash(engine.state));self.assertEqual(public,session.packet('pilot:B',full=True)['state'])
        self.assertEqual(plan.before.topology,plan.after.topology);self.assertEqual(plan.before.merges,plan.after.merges)
        raw=thaw_value(plan.after.parts[0].card_state);raw['counters']={'unexpected':1}
        changed=replace(plan.after.parts[0],card_state=FrozenMap(raw))
        with self.assertRaises(ValueError):replace(plan,after=replace(plan.after,parts=(changed,*plan.after.parts[1:])))
        with self.assertRaises(ValueError):replace(plan,blocked_refs=('unknown-part',))
        with self.assertRaises(ValueError):replace(plan,blocked_refs=(plan.before.parts[0].card_state['ref'],))

    def test_turning_up_a_morphed_composition_turns_up_each_physical_component(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session,root=self.morphed_composition_fixture();engine=session.engine
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.players['A'].mana_pool.update({'C':1,'W':1})
        engine._grant_priority('A');engine.pump();H.checkpoint(self,session)
        self.assertTrue(root.face_down)
        self.assertTrue(all(part.card_state['face_down'] for part in root.permanent_composition.parts))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offer=next((row for row in actions if row['id']=='turn-face-up:'+root.ref),None)
        self.assertIsNotNone(offer,'A merged face-up view retains its top printed Morph special action')
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
        root=engine.state.cards[root.object_id]
        self.assertFalse(root.face_down)
        self.assertTrue(all(not part.card_state['face_down'] for part in root.permanent_composition.parts))
        self.assertEqual('Enchantment',engine._effective_card_data(root)['type_line'])
        self.assertTrue(any('Add {C}' in ability.effect_text for ability in engine._activated_abilities(root)))
        H.replay(self,session,load=True)

    def test_actual_composed_morph_offers_each_inherited_cost_and_commits_the_selected_price(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        for selected_name in ('Lumithread Field','Zoetic Cavern'):
            with self.subTest(selected_source=selected_name):
                session,root=self.morphed_composition_fixture();engine=session.engine
                secret=H.add(self,engine,'Generic Bound Growth',zone='hand',ref='COMPOSED_MORPH_PRIVATE_HAND')
                engine.permissions.invalidate_current();engine.state.pending_decision=None
                engine.state.players['A'].mana_pool.update({'C':2,'W':1})
                engine._grant_priority('A');engine.pump();H.checkpoint(self,session)
                identity=root.logical_object_id;topology=root.permanent_composition.topology
                offers=[row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    if row['id'].startswith('turn-face-up:'+root.ref)]
                self.assertEqual(2,len(offers),'Each currently inherited Morph cost must be offered')
                self.assertEqual({(1,1),(2,0)},
                    {(row['requirements']['GENERIC'],row['requirements']['W']) for row in offers})
                self.assertEqual(2,len({row['id'] for row in offers}))
                selected_oracle=self.db.lookup(selected_name).oracle_id
                offer=next(row for row in offers if row['face_up_ability'].startswith(selected_oracle+':'))
                before=authoritative_state_hash(engine.state)
                self.assertNotIn(secret.ref,str(session.packet('pilot:B',full=True)))
                self.assertNotIn('Lumithread Field',str(session.packet('pilot:B',full=True)))
                self.assertNotIn('Zoetic Cavern',str(session.packet('pilot:B',full=True)))
                self.assertFalse(session.act('pilot:B',{'action_id':offer['id'],'pay':'auto'}).ok)
                self.assertEqual(before,authoritative_state_hash(engine.state))
                self.assertFalse(session.act('pilot:A',{'action_id':offer['id'],'face_up_ability':'unknown-source','pay':'auto'}).ok)
                self.assertEqual(before,authoritative_state_hash(engine.state))
                payment={'action_id':offer['id'],'pay':'auto'}
                if selected_name=='Zoetic Cavern':payment['reserve']={'W':1}
                self.assertTrue(session.act('pilot:A',payment).ok)
                current=engine.state.cards[root.object_id]
                self.assertFalse(current.face_down)
                self.assertTrue(all(not part.card_state['face_down'] for part in current.permanent_composition.parts))
                self.assertTrue(all(set(part.card_state['known_to'])==set(engine.seats) for part in current.permanent_composition.parts))
                self.assertEqual((identity,topology),(current.logical_object_id,current.permanent_composition.topology))
                expected=(1,0) if selected_name=='Lumithread Field' else (0,1)
                self.assertEqual(expected,(engine.state.players['A'].mana_pool.get('C',0),engine.state.players['A'].mana_pool.get('W',0)))
                self.assertEqual(1,sum(event.code=='permanent.turn_face_up' for event in engine.state.events))
                self.assertNotIn(secret.ref,str(session.packet('pilot:B',full=True)))
                mana=engine.state.players['A'].mana_pool.get('C',0)
                action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    if row['id'].startswith('activate:'+root.ref+':') and 'Add {C}' in row['label'])
                self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
                self.assertEqual(mana+1,engine.state.players['A'].mana_pool['C'])
                H.replay(self,session,load=True)

    def test_composed_turn_up_advances_only_the_parent_timestamp_in_modern_mode_and_preserves_absent_mode(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        for version in (1,None):
            with self.subTest(version=version):
                session,root=self.morphed_composition_fixture();engine=session.engine
                engine.state.face_status_timestamp_version=version
                engine.permissions.invalidate_current();engine.state.pending_decision=None
                engine.state.players['A'].mana_pool.update({'C':1,'W':1})
                engine._grant_priority('A');engine.pump();H.checkpoint(self,session)
                clock=engine.state.timestamp_sequence;timestamp=root.zone_timestamp
                from quorune.rules.composition_face_up import composition_face_up_read_view
                reader,preview,_data=composition_face_up_read_view(engine,root)
                self.assertEqual(clock,reader.state.timestamp_sequence)
                self.assertEqual(clock+1 if version==1 else timestamp,preview.zone_timestamp)
                self.assertEqual(clock,engine.state.timestamp_sequence)
                self.assertTrue(root.face_down)
                physical=tuple(part.card_state['zone_timestamp'] for part in root.permanent_composition.parts)
                offer=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    if row['id']=='turn-face-up:'+root.ref)
                self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
                current=engine.state.cards[root.object_id]
                self.assertEqual(clock+1 if version==1 else clock,engine.state.timestamp_sequence)
                self.assertEqual(clock+1 if version==1 else timestamp,current.zone_timestamp)
                self.assertEqual(physical,tuple(part.card_state['zone_timestamp'] for part in current.permanent_composition.parts))
                self.assertFalse(current.face_down)
                H.replay(self,session,load=True)

    def test_actual_composed_megamorph_and_disguise_use_the_chosen_method_not_the_root_marker(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        for name,method,color,generic in (('Aerie Bowmasters','megamorph','G',5),
                                           ('Defenestrated Phantom','disguise','W',4)):
            with self.subTest(method=method):
                session,root=self.morphed_composition_fixture(other_name=name,other_method=method);engine=session.engine
                engine.permissions.invalidate_current();engine.state.pending_decision=None
                engine.state.players['A'].mana_pool.update({'C':generic,color:1})
                engine._grant_priority('A');engine.pump();H.checkpoint(self,session)
                oracle=self.db.lookup(name).oracle_id
                offer=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    if row['id'].startswith('turn-face-up:'+root.ref) and row.get('face_up_ability','').startswith(oracle+':'))
                self.assertEqual(method,offer['method'])
                self.assertEqual(generic,offer['requirements']['GENERIC']);self.assertEqual(1,offer['requirements'][color])
                before=authoritative_state_hash(engine.state)
                self.assertFalse(session.act('pilot:A',{'action_id':offer['id'],'method':'morph','pay':'auto'}).ok)
                self.assertEqual(before,authoritative_state_hash(engine.state))
                self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
                current=engine.state.cards[root.object_id]
                self.assertFalse(current.face_down)
                self.assertEqual(1 if method=='megamorph' else 0,current.counters.get('+1/+1',0))
                self.assertTrue(all(not part.card_state['face_down'] for part in current.permanent_composition.parts))
                self.assertEqual(1,sum(event.code=='permanent.turn_face_up' for event in engine.state.events))
                H.replay(self,session,load=True)

    def test_composed_face_up_view_is_private_and_current_ability_loss_suppresses_all_prices(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_face_up import composed_turn_up_options
        from quorune.record import authoritative_state_hash
        session,root=self.morphed_composition_fixture();engine=session.engine
        before=authoritative_state_hash(engine.state);public=session.packet('pilot:B',full=True)['state']
        choices=composed_turn_up_options(engine,'A',root)
        self.assertEqual(2,len(choices))
        self.assertFalse(composed_turn_up_options(engine,'B',root))
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertEqual(public,session.packet('pilot:B',full=True)['state'])
        frog=H.add(self,engine,'Turn to Frog',zone='hand',ref='COMPOSED_FACE_UP_FROG')
        offer=H.ready(self,session,frog,{'C':1,'U':1});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'targets':[root.ref],'pay':'auto'}).ok)
        self.assertIsNone(H.resolve(self,session))
        self.assertFalse(composed_turn_up_options(engine,'A',engine.state.cards[root.object_id]))
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:A',{'action':'turn_face_up','card':root.ref,
            'face_up_ability':choices[0].source_key,'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertTrue(engine.state.cards[root.object_id].face_down)
        H.replay(self,session,load=True)

    def test_actual_composed_nonmana_morph_returns_two_islands_and_retains_the_buried_face_up_trigger(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session,root=self.morphed_composition_fixture(other_name='Fathom Seer');engine=session.engine
        islands=[H.add(self,engine,'Island',ref='COMPOSED_PRICE_ISLAND_'+str(index)) for index in range(2)]
        foreign=H.add(self,engine,'Island',seat='B',ref='COMPOSED_FOREIGN_ISLAND')
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine._grant_priority('A');engine.pump();H.checkpoint(self,session)
        oracle=self.db.lookup('Fathom Seer').oracle_id
        offer=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('turn-face-up:'+root.ref) and row.get('face_up_ability','').startswith(oracle+':'))
        refs=set(offer['choice_schema']['return_cards']['legal_refs'])
        self.assertTrue({card.ref for card in islands}.issubset(refs));self.assertNotIn(foreign.ref,refs)
        before=authoritative_state_hash(engine.state)
        for chosen in ([islands[0].ref,islands[0].ref],[islands[0].ref,foreign.ref]):
            self.assertFalse(session.act('pilot:A',{'action_id':offer['id'],'return_cards':chosen,'pay':'auto'}).ok)
            self.assertEqual(before,authoritative_state_hash(engine.state))
        hand=set(engine.state.players['A'].zones['hand'])
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],
            'return_cards':[card.ref for card in islands],'pay':'auto'}).ok)
        self.assertIsNone(H.resolve(self,session))
        self.assertTrue(all(engine.state.cards[card.object_id].zone=='hand' for card in islands))
        self.assertEqual('battlefield',engine.state.cards[foreign.object_id].zone)
        self.assertEqual(len(hand)+4,len(engine.state.players['A'].zones['hand']))
        draws=set(engine.state.players['A'].zones['hand'])-hand-{card.object_id for card in islands}
        self.assertEqual(2,len(draws))
        for object_id in draws:self.assertNotIn(engine.state.cards[object_id].ref,str(session.packet('pilot:B',full=True)))
        self.assertEqual(1,sum(event.code=='permanent.turn_face_up' for event in engine.state.events))
        self.assertFalse(engine.state.cards[root.object_id].face_down)
        H.replay(self,session,load=True)

    def test_composition_face_up_commit_rejects_stale_preparation_and_rolls_back_post_write_failure(self):
        from unittest.mock import patch
        from quorune.rules.composition_faces import prepare_composition_face_up
        from quorune.composition_storage import PermanentCompositionStorage
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.errors import GameRuleError,StateInvariantError
        from quorune.record import authoritative_state_hash
        session,root=self.morphed_composition_fixture();engine=session.engine
        plan=prepare_composition_face_up(engine,Identity(root.object_id,root.logical_object_id));owner=ZoneTransitionOwner(engine)
        engine.state.cards[root.object_id].counters['charge']=1
        stale=authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):owner.commit_composition_face_up(plan)
        self.assertEqual(stale,authoritative_state_hash(engine.state))
        engine.state.cards[root.object_id].counters.pop('charge')
        before=authoritative_state_hash(engine.state);original=PermanentCompositionStorage.validate_carrier
        def fail_after_write(storage,card):
            original(storage,card)
            if not card.face_down:raise StateInvariantError('Injected face-up post-write failure')
        with patch.object(PermanentCompositionStorage,'validate_carrier',new=fail_after_write):
            with self.assertRaises(StateInvariantError):owner.commit_composition_face_up(plan)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertTrue(engine.state.cards[root.object_id].face_down)
        self.assertEqual(plan.after,owner.commit_composition_face_up(plan))
        self.assertFalse(engine.state.cards[root.object_id].face_down)

    def test_composed_face_up_choices_reject_malformed_prices_and_withdrawn_source_admission(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune.rules.composition_face_up import composed_turn_up_options,selected_composed_turn_up_option
        from quorune.record import authoritative_state_hash
        session,root=self.morphed_composition_fixture();engine=session.engine
        before=authoritative_state_hash(engine.state)
        choices=composed_turn_up_options(engine,'A',root);self.assertEqual(2,len(choices))
        for field,value in (('source_key',True),('source_key',''),('source_oracle_id',' '),('spec',object())):
            with self.assertRaises(ValueError):replace(choices[0],**{field:value})
        self.assertIsNone(selected_composed_turn_up_option(engine,'A',root,source_key='unknown-source'))
        self.assertIsNone(selected_composed_turn_up_option(engine,'A',root,method='unknown-method'))
        original=engine.semantic_program_is_current_trusted
        withdrawn=choices[1].source_key.rsplit(':',1)[0]
        with patch.object(engine,'semantic_program_is_current_trusted',
                side_effect=lambda program:program.key!=withdrawn and original(program)):
            self.assertFalse(composed_turn_up_options(engine,'A',root))
        self.assertEqual(2,len(composed_turn_up_options(engine,'A',root)))
        self.assertEqual(before,authoritative_state_hash(engine.state))

    def test_face_up_plan_keeps_an_ordinary_face_up_token_buried_below_a_morphed_card(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.rules.composition_faces import prepare_composition_face_up
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.record import authoritative_state_hash
        session=H.session(self,959232);engine=session.engine
        token_ref=engine.create_token('A',name='Physical Beast',
            characteristics={'type_line':'Token Creature — Beast','power':'2','toughness':'2'},
            reason='Physical face fixture')[0]
        root=engine._resolve_object('A',token_ref)
        source=H.add(self,engine,'Lumithread Field',zone='hand',ref='MORPH_ABOVE_TOKEN')
        H.ready(self,session,source,{'C':3})
        offer=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id']=='cast-morph:'+source.ref)
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
        stack_ref=next(item.ref for item in engine.state.stack if item.card_object_id==source.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
            target=Identity(root.object_id,root.logical_object_id),source=Identity(source.object_id,source.logical_object_id),
            stack_ref=stack_ref,position=MergePosition.TOP))
        root=engine.state.cards[root.object_id]
        self.assertFalse(root.is_token);self.assertTrue(root.face_down)
        before=authoritative_state_hash(engine.state)
        plan=prepare_composition_face_up(engine,Identity(root.object_id,root.logical_object_id))
        self.assertTrue(plan.can_turn_face_up)
        self.assertFalse(plan.after.parts[0].card_state['face_down'])
        self.assertTrue(plan.before.parts[1].card_state['is_token'])
        self.assertFalse(plan.before.parts[1].card_state['face_down'])
        self.assertEqual(plan.before.parts[1],plan.after.parts[1])
        self.assertEqual(before,authoritative_state_hash(engine.state))

    def daybound_composition_fixture(self,*,root_bound=False,designation='day'):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.zone_transitions import ZoneTransitionOwner
        session=H.session(self,959233);engine=session.engine;engine.state.day_night=designation
        names=('Thraben Gargoyle // Stonewing Antagonizer','Sunlit Adept // Moonlit Adept')
        root=H.add(self,engine,names[int(root_bound)],zone='hand',ref='BOUND_ROOT')
        offer=H.ready(self,session,root,{'G':1,'C':1})
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
        self.assertIsNone(H.resolve(self,session));root=engine.state.cards[root.object_id]
        if root_bound:
            from quorune.continuous_effect_model import Layer
            from quorune.continuous_effect_state import ResolutionEffectSource,create_resolution_continuous_effect
            from quorune.continuous_effects import ContinuousOperation
            create_resolution_continuous_effect(engine,source=ResolutionEffectSource(stack_ref='fixture:bound-nonhuman'),
                targets=(root,),layer=Layer.TYPE,sublayer='4',
                operations=(ContinuousOperation('remove_types',['Human'],field='subtypes'),))
            self.assertNotIn('human',engine._type_parts(engine._effective_card_data(root)['type_line'])[1])
        bound=H.add(self,engine,names[1-int(root_bound)],zone='hand',ref='BOUND_COMPONENT')
        offer=H.ready(self,session,bound,{'G':1,'C':1})
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
        stack_ref=next(item.ref for item in engine.state.stack if item.card_object_id==bound.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
            target=Identity(root.object_id,root.logical_object_id),source=Identity(bound.object_id,bound.logical_object_id),
            stack_ref=stack_ref,position=MergePosition.BOTTOM))
        root=engine.state.cards[root.object_id]
        self.assertIn('daybound',{word.casefold() for word in engine._effective_card_data(root)['keywords']})
        return session,root

    def test_inherited_daybound_establishes_day_without_treating_the_merged_parent_as_double_faced(self):
        from quorune.day_night import synchronize_day_night
        session,root=self.daybound_composition_fixture(designation=None);engine=session.engine
        faces=root.permanent_composition;clock=engine.state.timestamp_sequence
        designation,changed=synchronize_day_night(engine,reason='merged bound presence fixture')
        self.assertEqual('day',designation)
        self.assertEqual((),changed)
        self.assertEqual(('day',0,None,clock,faces),(engine.state.day_night,root.transform_count,
            root.active_face,engine.state.timestamp_sequence,root.permanent_composition))

    def test_a_merged_carrier_with_original_daybound_face_has_no_automatic_front_back_transform(self):
        from quorune.day_night import synchronize_day_night
        session,root=self.daybound_composition_fixture(root_bound=True);engine=session.engine
        faces=root.permanent_composition;clock=engine.state.timestamp_sequence
        engine.state.day_night='night'  # Valid fixture for the owner after a designation change.
        designation,changed=synchronize_day_night(engine,reason='merged physical-face eligibility fixture')
        self.assertEqual('night',designation)
        self.assertEqual((),changed)
        self.assertEqual((0,None,clock,faces),(root.transform_count,root.active_face,
            engine.state.timestamp_sequence,root.permanent_composition))

    def test_ordinary_unmerged_daybound_control_still_transforms_in_both_directions(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.day_night import synchronize_day_night
        session=H.session(self,959234);engine=session.engine;engine.state.day_night='night'
        card=H.add(self,engine,'Sunlit Adept // Moonlit Adept',ref='ORDINARY_BOUND')
        designation,changed=synchronize_day_night(engine,reason='ordinary bound control')
        self.assertEqual(('night',(card.ref,),1,'Moonlit Adept'),
            (designation,changed,card.transform_count,card.active_face))
        engine.state.day_night='day'
        designation,changed=synchronize_day_night(engine,reason='ordinary reverse bound control')
        self.assertEqual(('day',(card.ref,),2,'Sunlit Adept'),
            (designation,changed,card.transform_count,card.active_face))

    def test_current_inherited_bound_query_reads_all_sources_and_observes_actual_ability_loss(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.composition_characteristics import composition_day_night_bound_modes
        from quorune.day_night_model import DayNightBoundMode
        from quorune.record import authoritative_state_hash
        session,root=self.daybound_composition_fixture();engine=session.engine
        before=authoritative_state_hash(engine.state)
        self.assertEqual(frozenset({DayNightBoundMode.DAYBOUND}),composition_day_night_bound_modes(engine,root))
        self.assertEqual(before,authoritative_state_hash(engine.state))
        frog=H.add(self,engine,'Turn to Frog',zone='hand',ref='BOUND_FROG')
        offer=H.ready(self,session,frog,{'U':1,'C':1});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'targets':[root.ref],'pay':'auto'}).ok)
        self.assertIsNone(H.resolve(self,session));root=engine.state.cards[root.object_id]
        before=authoritative_state_hash(engine.state)
        self.assertEqual(frozenset(),composition_day_night_bound_modes(engine,root))
        self.assertEqual(before,authoritative_state_hash(engine.state))
        H.replay(self,session,load=True)

    def test_buried_daybound_prohibits_an_ordinary_transform_instruction_on_the_retained_root(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session,root=self.daybound_composition_fixture();engine=session.engine
        H.ready(self,session,root,{'C':6});H.checkpoint(self,session)
        ability=next(row for row in engine._activated_abilities(root)
            if row.builtin_semantic_key and row.builtin_semantic_key.startswith(root.oracle_id+':ability:'))
        action='activate:'+root.ref+':'+ability.ability_id
        offers=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertIn(action,[row['id'] for row in offers])
        mana_before=sum(engine.state.players['A'].mana_pool.values())
        retained=(root.logical_object_id,root.zone_timestamp,root.permanent_composition)
        from quorune.record import authoritative_state_hash
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:B',{'action_id':action,'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertTrue(session.act('pilot:A',{'action_id':action,'pay':'auto'}).ok)
        self.assertEqual(mana_before-6,sum(engine.state.players['A'].mana_pool.values()))
        self.assertIsNone(H.resolve(self,session));root=engine.state.cards[root.object_id]
        self.assertEqual(0,root.transform_count,'A current inherited Daybound restriction applies to the permanent')
        self.assertIsNone(root.active_face)
        self.assertEqual(retained,(root.logical_object_id,root.zone_timestamp,root.permanent_composition))
        self.assertEqual('A',engine.state.priority_player)
        self.assertFalse(any(event.code=='permanent.transform' for event in engine.state.events))
        H.replay(self,session,load=True)

    def test_top_characteristics_keep_buried_abilities_and_existing_resolution_effects(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.continuous_effect_model import Layer
        from quorune.model import GameState
        session=H.session(self,959200);engine=session.engine
        root=H.add(self,engine,'Ornithopter',ref='CHAR_ROOT')
        growth=H.add(self,engine,'Generic Bound Growth',zone='hand',ref='CHAR_GROWTH')
        incoming=H.add(self,engine,'Generic Bound Body',zone='hand',ref='CHAR_TOP')
        action=H.ready(self,session,growth,{'G':1,'W':1});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'targets':[root.ref],'pay':'auto'}).ok)
        self.assertIsNone(H.resolve(self,session))
        action=H.ready(self,session,incoming,{'W':2})
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        engine.state.players['A'].zones['battlefield'].remove(root.object_id)
        engine.state.players['B'].zones['battlefield'].append(root.object_id);root.controller='B'
        root.counters={'charge':3};root.marked_damage=1
        target=Identity(root.object_id,root.logical_object_id)
        source=Identity(incoming.object_id,incoming.logical_object_id)
        reference=next(item.ref for item in engine.state.stack if item.card_object_id==incoming.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,target=target,
            source=source,stack_ref=reference,position=MergePosition.TOP))
        root=engine.state.cards[root.object_id]
        current=engine._effective_card_data(root)
        self.assertEqual(('Generic Bound Body',4,8),
            (current['name'],int(current['power']),int(current['toughness'])))
        self.assertEqual({'flying','vigilance','trample'},{word.casefold() for word in current['keywords']})
        copied=engine._copyable_characteristics(root)
        layer_one=engine._effective_card_data(root,maximum_layer=Layer.COPY)
        self.assertEqual(('Generic Bound Body',2,6),(copied['name'],int(copied['power']),int(copied['toughness'])))
        self.assertEqual({'flying','vigilance'},{word.casefold() for word in copied['keywords']})
        self.assertEqual(copied['name'],layer_one['name'])
        self.assertEqual(target,Identity(root.object_id,root.logical_object_id))
        self.assertEqual(('B',{'charge':3},1),(root.controller,root.counters,root.marked_damage))
        self.assertEqual([root.object_id],engine.state.players['B'].zones['battlefield'])
        self.assertNotIn(incoming.object_id,engine.state.cards)
        wire=engine.state.to_dict();self.assertEqual(wire,GameState.from_dict(wire).to_dict())

    def test_buried_activation_is_offered_and_resolves_from_the_retained_source(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.record import authoritative_state_hash
        session=H.session(self,959201);engine=session.engine
        root=H.add(self,engine,'Ornithopter',ref='ACT_ROOT')
        target=H.add(self,engine,'Generic Bound Body',ref='ACT_TARGET');target.tapped=True
        incoming=H.add(self,engine,'Generic Bound Mentor',zone='hand',ref='ACT_BURIED')
        action=H.ready(self,session,incoming,{'W':3})
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        reference=next(item.ref for item in engine.state.stack if item.card_object_id==incoming.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
            target=Identity(root.object_id,root.logical_object_id),source=Identity(incoming.object_id,incoming.logical_object_id),
            stack_ref=reference,position=MergePosition.BOTTOM))
        root=engine.state.cards[root.object_id];root.acquired_control_turn_count=-1
        action=H.ready(self,session,root,{'W':1});H.checkpoint(self,session)
        self.assertTrue(action['id'].startswith('activate:'+root.ref+':'))
        self.assertIn(target.ref,action['target_schema']['legal_refs'])
        self.assertNotIn(root.ref,action['target_schema']['legal_refs'])
        import json
        self.assertNotIn(action['id'],json.dumps(session.packet('pilot:B',full=True)))
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:B',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertFalse(session.act('pilot:A',{'action_id':action['id'],'targets':[root.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'}).ok)
        item=engine.state.stack[-1]
        self.assertEqual(root.object_id,item.source_object_id)
        self.assertEqual('A',item.controller)
        self.assertTrue(item.semantic_key.startswith(incoming.oracle_id+':ability:'))
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertIsNone(H.resolve(self,session))
        self.assertTrue(engine.state.cards[root.object_id].tapped)
        self.assertFalse(engine.state.cards[target.object_id].tapped)
        self.assertIn('flying',{word.casefold() for word in engine._effective_card_data(target.object_id)['keywords']})
        self.assertEqual('Ornithopter',engine._effective_card_data(root.object_id)['name'])
        H.replay(self,session)

    def test_buried_early_static_survives_later_ability_loss_on_the_retained_source(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.record import authoritative_state_hash
        session=H.session(self,959220);engine=session.engine
        root=H.add(self,engine,'Ornithopter',ref='EARLY_ROOT')
        land=H.add(self,engine,'Darksteel Citadel',seat='B',ref='EARLY_NONBASIC')
        basic=H.add(self,engine,'Snow-Covered Island',seat='B',ref='EARLY_BASIC')
        incoming=H.add(self,engine,'Magus of the Moon',zone='hand',ref='EARLY_BURIED')
        frog=H.add(self,engine,'Turn to Frog',zone='hand',ref='EARLY_REMOVE')
        copy_spell=H.add(self,engine,'Cackling Counterpart',zone='hand',ref='EARLY_COPY')
        action=H.ready(self,session,incoming,{'R':3})
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        reference=next(item.ref for item in engine.state.stack if item.card_object_id==incoming.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
            target=Identity(root.object_id,root.logical_object_id),source=Identity(incoming.object_id,incoming.logical_object_id),
            stack_ref=reference,position=MergePosition.BOTTOM))
        def subtypes(card):return engine._type_parts(engine._effective_card_data(card.object_id)['type_line'])[1]
        self.assertEqual({'mountain'},subtypes(land));self.assertEqual({'island'},subtypes(basic))
        action=H.ready(self,session,frog,{'U':2});H.checkpoint(self,session)
        before=authoritative_state_hash(engine.state)
        import json
        self.assertNotIn(action['id'],json.dumps(session.packet('pilot:B',full=True)))
        self.assertNotIn('permanent_composition',json.dumps(session.packet('pilot:B',full=True)))
        self.assertFalse(session.act('pilot:B',{'action_id':action['id'],'targets':[root.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'targets':[root.ref],'pay':'auto'}).ok)
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertIsNone(H.resolve(self,session))
        self.assertEqual({'mountain'},subtypes(land));self.assertEqual({'island'},subtypes(basic))
        current=engine._effective_card_data(root.object_id)
        self.assertEqual((1,1),(int(current['power']),int(current['toughness'])))
        self.assertEqual([],current['ability_fragments'])
        self.assertNotIn('indestructible',{word.casefold() for word in engine._effective_card_data(land.object_id)['keywords']})
        self.assertEqual(2,len(engine.state.cards[root.object_id].permanent_composition.parts))
        H.replay(self,session)
        action=H.ready(self,session,copy_spell,{'U':3});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'targets':[root.ref],'pay':'auto'}).ok)
        self.assertIsNone(H.resolve(self,session))
        copied=next(card for card in engine.state.cards.values() if card.zone=='battlefield' and card.is_token)
        self.assertIsNone(copied.permanent_composition)
        values=engine._effective_card_data(copied)
        self.assertEqual(('Ornithopter',0,2),(values['name'],int(values['power']),int(values['toughness'])))
        self.assertTrue(values['ability_fragments'])
        self.assertEqual({'mountain'},subtypes(land));self.assertEqual({'island'},subtypes(basic))
        H.replay(self,session,load=True)

    def test_repeated_buried_anthems_exclude_the_retained_source_and_preserve_instances(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.zone_transitions import ZoneTransitionOwner
        session=H.session(self,959210);engine=session.engine
        root=H.add(self,engine,'Ornithopter',ref='ANTHEM_ROOT')
        other=H.add(self,engine,'Ornithopter',ref='ANTHEM_RECIPIENT')
        enemy=H.add(self,engine,'Ornithopter',seat='B',ref='ANTHEM_ENEMY')
        copy_spell=H.add(self,engine,'Cackling Counterpart',zone='hand',ref='ANTHEM_COPY')
        frog=H.add(self,engine,'Turn to Frog',zone='hand',ref='ANTHEM_REMOVE')
        for index in range(2):
            incoming=H.add(self,engine,'Benalish Marshal',zone='hand',ref=f'ANTHEM_PART_{index}')
            action=H.ready(self,session,incoming,{'W':3})
            self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
            root=engine.state.cards[root.object_id]
            reference=next(item.ref for item in engine.state.stack if item.card_object_id==incoming.object_id)
            ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
                target=Identity(root.object_id,root.logical_object_id),
                source=Identity(incoming.object_id,incoming.logical_object_id),stack_ref=reference,position=MergePosition.BOTTOM))
        root=engine.state.cards[root.object_id]
        self.assertEqual(2,root.permanent_composition.topology.mutation_count)
        def stats(card):
            values=engine._effective_card_data(card.object_id)
            return int(values['power']),int(values['toughness'])
        self.assertEqual((2,4),stats(other))
        self.assertEqual((0,2),stats(root))
        self.assertEqual((0,2),stats(enemy))
        action=H.ready(self,session,copy_spell,{'U':3});H.checkpoint(self,session)
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertEqual((2,4),stats(other))
        self.assertEqual(3,len(engine.state.cards[root.object_id].permanent_composition.parts))
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'targets':[root.ref],'pay':'auto'}).ok)
        self.assertIsNone(H.resolve(self,session))
        copies=[card for card in engine.state.cards.values() if card.zone=='battlefield' and card.is_token]
        self.assertEqual(1,len(copies));copied=copies[0]
        self.assertIsNone(copied.permanent_composition)
        self.assertEqual((2,4),stats(root));self.assertEqual((2,4),stats(copied))
        self.assertEqual((4,6),stats(other))
        from quorune.composition_characteristics import copied_static_component_instances
        fragments=engine._effective_card_data(copied)['ability_fragments']
        from quorune.ability_fragments import static_component_keys
        instances=copied_static_component_instances(fragments,static_component_keys(fragments))
        self.assertEqual(2,len(instances))
        self.assertTrue(all(timestamp<=copied.zone_timestamp for _key,timestamp in instances))
        from quorune.card_programs.runtime import collect_card_program_continuous_effects
        fragment_map=engine._effective_static_component_fragment_map()
        key_map=engine._static_keys_from_fragment_map(fragment_map)
        effects=collect_card_program_continuous_effects(engine.state,engine.semantics,
            engine.semantic_program_is_current_trusted,
            static_component_resolver=lambda card:key_map.get(card.object_id,()),
            copied_characteristic_resolver=lambda card:engine._copied_characteristic_occurrences(card,fragment_map,key_map),
            public_object_resolver=engine._public_object_query_result)
        for card,timestamp in ((root,engine.state.cards[root.object_id].permanent_composition.merges[-1].timestamp),
                (copied,copied.zone_timestamp)):
            own=[effect for effect in effects if effect.source_id==card.object_id]
            self.assertEqual(2,len(own));self.assertEqual(2,len({effect.effect_id for effect in own}))
            self.assertEqual({timestamp},{effect.timestamp for effect in own})
        H.replay(self,session,load=True)
        action=H.ready(self,session,frog,{'U':2});H.checkpoint(self,session)
        from quorune.record import authoritative_state_hash
        before=authoritative_state_hash(engine.state)
        import json
        self.assertNotIn(action['id'],json.dumps(session.packet('pilot:B',full=True)))
        self.assertNotIn('permanent_composition',json.dumps(session.packet('pilot:B',full=True)))
        self.assertFalse(session.act('pilot:B',{'action_id':action['id'],'targets':[root.ref],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'targets':[root.ref],'pay':'auto'}).ok)
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertIsNone(H.resolve(self,session))
        self.assertEqual((3,3),stats(root));self.assertEqual((0,2),stats(copied))
        self.assertEqual((2,4),stats(other));self.assertEqual((0,2),stats(enemy))
        self.assertEqual('Ornithopter',engine._copyable_characteristics(engine.state.cards[root.object_id])['name'])
        H.replay(self,session)

    def test_buried_trigger_uses_retained_source_after_another_actual_creature_cast(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.zone_transitions import ZoneTransitionOwner
        session=H.session(self,959202);engine=session.engine
        root=H.add(self,engine,'Ornithopter',ref='TRIG_ROOT')
        incoming=H.add(self,engine,'Soul Warden',zone='hand',ref='TRIG_BURIED')
        second=H.add(self,engine,'Soul Warden',zone='hand',ref='TRIG_BURIED_SECOND')
        visitor=H.add(self,engine,'Ornithopter',zone='hand',ref='TRIG_VISITOR')
        action=H.ready(self,session,incoming,{'W':1})
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        life=engine.state.players['A'].life
        reference=next(item.ref for item in engine.state.stack if item.card_object_id==incoming.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
            target=Identity(root.object_id,root.logical_object_id),source=Identity(incoming.object_id,incoming.logical_object_id),
            stack_ref=reference,position=MergePosition.BOTTOM))
        self.assertEqual(life,engine.state.players['A'].life)
        self.assertFalse(engine.state.stack)
        action=H.ready(self,session,second,{'W':1})
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        root=engine.state.cards[root.object_id]
        reference=next(item.ref for item in engine.state.stack if item.card_object_id==second.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
            target=Identity(root.object_id,root.logical_object_id),source=Identity(second.object_id,second.logical_object_id),
            stack_ref=reference,position=MergePosition.BOTTOM))
        self.assertEqual(2,engine.state.cards[root.object_id].permanent_composition.topology.mutation_count)
        action=H.ready(self,session,visitor,{});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        decision=H.resolve(self,session);self.assertEqual('trigger.order',decision.kind)
        batch=engine.state.pending_trigger_batches[0];self.assertEqual(2,len(batch.items))
        self.assertEqual({root.object_id},{item.source_object_id for item in batch.items})
        self.assertEqual(1,len({item.source_ability_id for item in batch.items}))
        session=H.replay(self,session,load=True);engine=session.engine
        batch=engine.state.pending_trigger_batches[0]
        self.assertTrue(session.act('pilot:A',{'action_id':'order','triggers':[item.ref for item in batch.items]}).ok)
        self.assertIsNone(H.resolve(self,session))
        self.assertEqual(life+2,session.state.players['A'].life)
        self.assertEqual('Ornithopter',session.engine._effective_card_data(root.object_id)['name'])
        H.replay(self,session,load=True)

    def test_actual_casts_capture_physical_and_copiable_planes_without_mutation_or_replay_changes(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.composition_snapshot import CompositionObjectSnapshot
        from quorune.rules.composition_capture import capture_composition_object
        from quorune.record import authoritative_state_hash
        session=H.session(self,959150);engine=session.engine
        root=H.add(self,engine,'Generic Bound Body',ref='CAPTURE_ROOT');root.tapped=True
        growth=H.add(self,engine,'Generic Bound Growth',zone='hand',ref='CAPTURE_GROWTH')
        incoming=H.add(self,engine,'Generic Bound Body',zone='hand',ref='CAPTURE_INCOMING')
        secret=H.add(self,engine,'Generic Bound Growth',zone='library',ref='CAPTURE_LIBRARY_SECRET')
        action=H.ready(self,session,growth,{'G':1,'W':1});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'targets':[root.ref],'pay':'auto'}).ok)
        self.assertIsNone(H.resolve(self,session))
        current=engine._effective_card_data(root.object_id)
        self.assertEqual((4,8),(int(current['power']),int(current['toughness'])))
        self.assertFalse(engine.state.cards[root.object_id].tapped)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in actions if row.get('card')==incoming.ref)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        session=H.replay(self,session,load=True);engine=session.engine
        root_identity=Identity(root.object_id,engine.state.cards[root.object_id].logical_object_id)
        incoming_identity=Identity(incoming.object_id,engine.state.cards[incoming.object_id].logical_object_id)
        packet=session.packet('pilot:B',full=True);before=authoritative_state_hash(engine.state);commands=len(session.commands)
        stack_ref=next(item.ref for item in engine.state.stack if item.card_object_id==incoming.object_id)
        root_snapshot=capture_composition_object(engine,root_identity,expected_zone='battlefield')
        spell_snapshot=capture_composition_object(engine,incoming_identity,expected_zone='stack',expected_stack_ref=stack_ref)
        self.assertEqual((2,6),(int(root_snapshot.copiable_values['power']),int(root_snapshot.copiable_values['toughness'])))
        self.assertNotIn('trample',{str(value).casefold() for value in root_snapshot.copiable_values['keywords']})
        self.assertEqual(('stack','spell','A'),(spell_snapshot.card_state['zone'],spell_snapshot.stack_kind,spell_snapshot.controller))
        self.assertEqual(incoming_identity,spell_snapshot.identity)
        self.assertEqual(root_snapshot,CompositionObjectSnapshot.from_dict(root_snapshot.to_dict()))
        detached=root_snapshot.detached_card();detached.owner='C';detached.counters['charge']=7
        self.assertEqual('A',engine.state.cards[root.object_id].owner)
        self.assertNotIn('charge',engine.state.cards[root.object_id].counters)
        self.assertEqual(before,authoritative_state_hash(engine.state));self.assertEqual(commands,len(session.commands))
        after_packet=session.packet('pilot:B',full=True)
        for field in ('state','view','decision','view_revision'):
            self.assertEqual(packet[field],after_packet[field])
        self.assertNotIn(secret.ref,str(packet));self.assertNotIn(secret.ref,str(after_packet))
        self.assertIsNone(H.resolve(self,session))
        self.assertEqual('battlefield',engine.state.cards[incoming.object_id].zone)
        before=authoritative_state_hash(engine.state)
        with self.assertRaises(ValueError):capture_composition_object(engine,incoming_identity,
            expected_zone='stack',expected_stack_ref=stack_ref)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        H.replay(self,session,load=True)

    def test_physical_merge_kernel_keeps_one_object_and_rolls_back_partial_commit(self):
        from unittest.mock import patch
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_merge import prepare_composition_merge,PreparedCompositionMerge
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        from quorune.model import GameState
        session=H.session(self,959160);engine=session.engine
        root=H.add(self,engine,'Ornithopter',ref='MERGED_ROOT')
        engine.state.players['A'].zones['battlefield'].remove(root.object_id)
        engine.state.players['B'].zones['battlefield'].append(root.object_id);root.controller='B'
        root.tapped=True;root.counters={'charge':3};root.marked_damage=1
        incoming=H.add(self,engine,'Ornithopter',zone='hand',ref='MERGED_INCOMING')
        action=H.ready(self,session,incoming,{});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        session=H.replay(self,session,load=True);engine=session.engine
        target=Identity(root.object_id,engine.state.cards[root.object_id].logical_object_id)
        source=Identity(incoming.object_id,engine.state.cards[incoming.object_id].logical_object_id)
        reference=next(item.ref for item in engine.state.stack if item.card_object_id==incoming.object_id)
        def prepare():return prepare_composition_merge(engine,target=target,source=source,
            stack_ref=reference,position=MergePosition.BOTTOM)
        prepared=prepare();self.assertEqual('A',prepared.chooser)
        self.assertEqual(prepared,PreparedCompositionMerge.from_dict(prepared.to_dict()))
        engine.state.cards[root.object_id].marked_damage=0
        before=authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):ZoneTransitionOwner(engine).commit_composition_merge(prepared)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        prepared=prepare();before=authoritative_state_hash(engine.state)
        def fail_after_storage(*args,**kwargs):
            self.assertIsNotNone(engine.state.cards[root.object_id].permanent_composition)
            self.assertNotIn(incoming.object_id,engine.state.cards)
            raise RuntimeError('Injected physical-merge commitment failure')
        with patch.object(engine,'_log',side_effect=fail_after_storage):
            with self.assertRaises(RuntimeError):ZoneTransitionOwner(engine).commit_composition_merge(prepared)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        stored=ZoneTransitionOwner(engine).commit_composition_merge(prepare())
        retained=engine.state.cards[root.object_id]
        self.assertEqual(target,Identity(retained.object_id,retained.logical_object_id))
        self.assertEqual(('B',True,{'charge':3},0),(retained.controller,retained.tapped,retained.counters,retained.marked_damage))
        self.assertEqual([root.object_id],engine.state.players['B'].zones['battlefield'])
        self.assertNotIn(incoming.object_id,engine.state.cards)
        self.assertFalse(any(item.card_object_id==incoming.object_id for item in engine.state.stack))
        self.assertEqual(2,len(stored.parts));self.assertEqual(1,stored.topology.mutation_count)
        self.assertFalse(any(event.code=='token.create' for event in engine.state.events))
        wire=engine.state.to_dict();self.assertEqual(wire,GameState.from_dict(wire).to_dict())
        before=authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):ZoneTransitionOwner(engine).move_card(root.object_id,'graveyard')
        self.assertEqual(before,authoritative_state_hash(engine.state))
        with self.assertRaises(GameRuleError):ZoneTransitionOwner(engine).move_cards_simultaneously(
            [(root.object_id,'graveyard')],reason='composition departure guard')
        self.assertEqual(before,authoritative_state_hash(engine.state))
        with self.assertRaises(GameRuleError):ZoneTransitionOwner(engine).reset_zone_change(retained,'graveyard')
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertEqual(2,len(engine.state.cards[root.object_id].permanent_composition.parts))

    def test_split_kernel_restores_all_parts_in_owner_order_with_one_departure(self):
        from unittest.mock import patch
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        session=H.session(self,959170);engine=session.engine
        root=H.add(self,engine,'Ornithopter',ref='SPLIT_ROOT')
        engine.state.players['A'].zones['battlefield'].remove(root.object_id)
        engine.state.players['B'].zones['battlefield'].append(root.object_id);root.controller='B'
        root.counters={'charge':3};root.tapped=True
        incoming=H.add(self,engine,'Ornithopter',zone='hand',ref='SPLIT_PART')
        action=H.ready(self,session,incoming,{});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        target=Identity(root.object_id,engine.state.cards[root.object_id].logical_object_id)
        source=Identity(incoming.object_id,engine.state.cards[incoming.object_id].logical_object_id)
        reference=next(item.ref for item in engine.state.stack if item.card_object_id==incoming.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,target=target,
            source=source,stack_ref=reference,position=MergePosition.BOTTOM))
        history=engine.state.turn_history
        history.arrival_history_version=1;history.graveyard_type_history_version=1;history.departure_history_version=1
        order=(incoming.object_id,root.object_id)
        def depart(chooser='A',ordered=order):
            return ZoneTransitionOwner(engine).depart_composition(root.object_id,'graveyard',
                physical_order=ordered,chooser=chooser,actor='A',reason='physical split kernel fixture')
        before=authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):depart(chooser='B')
        with self.assertRaises(GameRuleError):depart(ordered=(root.object_id,root.object_id))
        self.assertEqual(before,authoritative_state_hash(engine.state))
        original=ZoneTransitionOwner._enter_owner_zone;placements=[]
        def fail_second(owner,plan,**kwargs):
            placements.append(plan.card.object_id)
            if len(placements)==2:raise RuntimeError('Injected second physical arrival failure')
            return original(owner,plan,**kwargs)
        with patch.object(ZoneTransitionOwner,'_enter_owner_zone',autospec=True,side_effect=fail_second):
            with self.assertRaises(RuntimeError):depart()
        self.assertEqual(2,len(placements));self.assertEqual(before,authoritative_state_hash(engine.state))
        moved=depart()
        self.assertEqual(order,tuple(card.object_id for card in moved))
        self.assertEqual(list(order),engine.state.players['A'].zones['graveyard'][-2:])
        self.assertNotIn(root.object_id,engine.state.players['B'].zones['battlefield'])
        for card in moved:
            self.assertEqual('graveyard',card.zone);self.assertIsNone(card.permanent_composition)
            self.assertEqual({},card.counters);self.assertFalse(card.tapped)
        events=engine.state.turn_history.events
        kinds=[event.kind for event in events]
        self.assertEqual(1,kinds.count('creature_died'));self.assertEqual(1,kinds.count('permanent_left'))
        self.assertEqual(2,kinds.count('card_arrived_graveyard'))

    def test_split_library_bottom_preserves_private_owner_order(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.zone_transitions import ZoneTransitionOwner
        session=H.session(self,959180);engine=session.engine
        root=H.add(self,engine,'Ornithopter',ref='LIBRARY_SPLIT_ROOT')
        incoming=H.add(self,engine,'Ornithopter',zone='hand',ref='LIBRARY_SPLIT_PART')
        action=H.ready(self,session,incoming,{})
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        target=Identity(root.object_id,engine.state.cards[root.object_id].logical_object_id)
        source=Identity(incoming.object_id,engine.state.cards[incoming.object_id].logical_object_id)
        reference=next(item.ref for item in engine.state.stack if item.card_object_id==incoming.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,target=target,
            source=source,stack_ref=reference,position=MergePosition.BOTTOM))
        before=list(engine.state.players['A'].zones['library']);order=(incoming.object_id,root.object_id)
        ZoneTransitionOwner(engine).depart_composition(root.object_id,'library',physical_order=order,
            chooser='A',actor='A',position='bottom',reason='private ordered physical split fixture')
        self.assertEqual([*order,*before],engine.state.players['A'].zones['library'])
        packet=session.packet('pilot:B',full=True)
        self.assertNotIn(root.ref,str(packet['state']));self.assertNotIn(incoming.ref,str(packet['state']))
        event=next(event for event in reversed(engine.state.events) if event.code=='composition.departure')
        self.assertNotIn('objects',event.details);self.assertNotIn('order',event.details)

    def test_actual_murder_asks_physical_owner_and_resumes_after_pending_load(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.record import authoritative_state_hash
        session=H.session(self,959190);engine=session.engine
        root=H.add(self,engine,'Ornithopter',ref='ORDER_ROOT')
        engine.state.players['A'].zones['battlefield'].remove(root.object_id)
        engine.state.players['B'].zones['battlefield'].append(root.object_id);root.controller='B'
        incoming=H.add(self,engine,'Ornithopter',zone='hand',ref='ORDER_PART')
        action=H.ready(self,session,incoming,{})
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        target=Identity(root.object_id,engine.state.cards[root.object_id].logical_object_id)
        source=Identity(incoming.object_id,engine.state.cards[incoming.object_id].logical_object_id)
        reference=next(item.ref for item in engine.state.stack if item.card_object_id==incoming.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,target=target,
            source=source,stack_ref=reference,position=MergePosition.BOTTOM))
        murder=H.add(self,engine,'Murder',seat='C',zone='hand',ref='ORDER_MURDER')
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.active_player='C';engine.state.started=True
        engine.state.phase='precombat_main';engine.state.step='main'
        engine.state.players['C'].mana_pool.update({'B':2,'C':1})
        engine.state.turn_history.arrival_history_version=1
        engine.state.turn_history.graveyard_type_history_version=1
        engine.state.turn_history.departure_history_version=1
        engine._grant_priority('C');engine.pump();H.checkpoint(self,session)
        actions=session.packet('pilot:C',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in actions if row.get('card')==murder.ref)
        self.assertTrue(session.act('pilot:C',{'action_id':action['id'],'targets':[root.ref],'pay':'auto'}).ok)
        self.assertIsNotNone(H.resolve(self,session))
        self.assertEqual(['pilot:A'],list(session.pending_principals()))
        self.assertEqual('battlefield',engine.state.cards[root.object_id].zone)
        self.assertIsNotNone(engine.state.cards[root.object_id].permanent_composition)
        self.assertNotIn(incoming.object_id,engine.state.cards)
        packet=session.packet('pilot:A',full=True)
        legal=packet['decision']['ctx']['legal_actions'][0]['choice_schema']['legal_refs']
        self.assertEqual({root.ref,incoming.ref},set(legal))
        for seat in 'BCD':
            private=session.packet('pilot:'+seat,full=True)
            self.assertNotIn(incoming.ref,str(private.get('decision')))
            self.assertNotIn('permanent_composition',str(private))
        session=H.replay(self,session,load=True);engine=session.engine
        before=authoritative_state_hash(engine.state)
        for principal,order in (('pilot:B',[incoming.ref,root.ref]),('pilot:C',[incoming.ref,root.ref]),
                                ('pilot:A',[root.ref,root.ref])):
            self.assertFalse(session.act(principal,{'action_id':'choose','cards':order}).ok)
            self.assertEqual(before,authoritative_state_hash(engine.state))
        accepted=session.act('pilot:A',{'action_id':'choose','cards':[incoming.ref,root.ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(H.resolve(self,session))
        self.assertEqual([incoming.object_id,root.object_id],engine.state.players['A'].zones['graveyard'][-2:])
        self.assertEqual('graveyard',engine.state.cards[murder.object_id].zone)
        self.assertEqual(1,sum(event.kind=='creature_died' for event in engine.state.turn_history.events))
        H.replay(self,session,load=True)

    def test_actual_murder_couples_reviewed_replacement_and_exiler_order_for_all_parts(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.record import authoritative_state_hash
        session=H.session(self,959200);engine=session.engine
        root=H.add(self,engine,'Ornithopter',ref='COUPLED_ROOT')
        engine.state.players['A'].zones['battlefield'].remove(root.object_id)
        engine.state.players['B'].zones['battlefield'].append(root.object_id);root.controller='B'
        incoming=H.add(self,engine,'Ornithopter',zone='hand',ref='COUPLED_PART')
        action=H.ready(self,session,incoming,{})
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        target=Identity(root.object_id,engine.state.cards[root.object_id].logical_object_id)
        source=Identity(incoming.object_id,engine.state.cards[incoming.object_id].logical_object_id)
        reference=next(item.ref for item in engine.state.stack if item.card_object_id==incoming.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,target=target,
            source=source,stack_ref=reference,position=MergePosition.BOTTOM))
        # Existing reviewed replacement controls; complete Dauthi card support
        # is not asserted or promoted by this witness.
        for index in range(2):H.add(self,engine,'Dauthi Voidwalker',seat='D',ref='COUPLED_REPLACER_'+str(index))
        murder=H.add(self,engine,'Murder',seat='C',zone='hand',ref='COUPLED_MURDER')
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.active_player='C';engine.state.started=True;engine.state.phase='precombat_main';engine.state.step='main'
        engine.state.players['C'].mana_pool.update({'B':2,'C':1});engine._grant_priority('C');engine.pump()
        H.checkpoint(self,session)
        action=next(row for row in session.packet('pilot:C',full=True)['decision']['ctx']['legal']['actions']
            if row.get('card')==murder.ref)
        self.assertTrue(session.act('pilot:C',{'action_id':action['id'],'targets':[root.ref],'pay':'auto'}).ok)
        self.assertEqual('replacement.order',H.resolve(self,session).kind)
        self.assertEqual(['pilot:B'],list(session.pending_principals()))
        session=H.replay(self,session,load=True);engine=session.engine
        option=session.packet('pilot:B',full=True)['decision']['ctx']['options'][0]['id']
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'choose','replacement':option}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertTrue(session.act('pilot:B',{'action_id':'choose','replacement':option}).ok)
        self.assertEqual(['pilot:D'],list(session.pending_principals()))
        self.assertEqual('battlefield',engine.state.cards[root.object_id].zone)
        self.assertNotIn(incoming.object_id,engine.state.cards)
        session=H.replay(self,session,load=True);engine=session.engine
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'choose','cards':[root.ref,incoming.ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        accepted=session.act('pilot:D',{'action_id':'choose','cards':[incoming.ref,root.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        for card in (root,incoming):
            current=engine.state.cards[card.object_id]
            self.assertEqual('exile',current.zone);self.assertEqual(1,current.counters.get('void',0))
        # C still chooses the ordinary destination replacement on its resolving
        # Murder card. That separate object must not reuse the group order.
        self.assertEqual('replacement.order',H.resolve(self,session).kind)
        self.assertEqual(['pilot:C'],list(session.pending_principals()))
        option=session.packet('pilot:C',full=True)['decision']['ctx']['options'][0]['id']
        self.assertTrue(session.act('pilot:C',{'action_id':'choose','replacement':option}).ok)
        self.assertIsNone(H.resolve(self,session))
        self.assertEqual('exile',engine.state.cards[murder.object_id].zone)
        self.assertEqual(2,sum(event.code=='replacement.apply' for event in engine.state.events))
        H.replay(self,session,load=True)


class CompositionStorageTests(unittest.TestCase):
    def test_object_map_port_preserves_aliases_and_rejects_the_whole_invalid_edit(self):
        from types import SimpleNamespace
        from quorune.game_object_store import GameObjectStore
        first=CompositionSnapshotTests().card();second=CompositionSnapshotTests().card(object_id='SECOND',ref='SECOND')
        state=SimpleNamespace(cards={first.object_id:first});alias=state.cards
        store=GameObjectStore(SimpleNamespace(state=state))
        with self.assertRaises(ValueError):store.commit(upserts=(second,),removals=('MISSING',))
        self.assertEqual({first.object_id:first},state.cards)
        store.commit(upserts=(second,));self.assertIs(alias,state.cards)
        self.assertEqual((first,),store.commit(removals=(first.object_id,)))
        self.assertEqual({second.object_id:second},alias)
        with self.assertRaises(ValueError):store.commit(upserts=(first,first))
        self.assertEqual({second.object_id:second},alias)

    def snapshots(self):
        from quorune.composition_snapshot import CompositionObjectSnapshot
        root=CompositionSnapshotTests().card(face_down=False,active_face=None)
        source=CompositionSnapshotTests().card(object_id='INCOMING',ref='INCOMING_REF',zone='stack',
            zone_change_counter=1,controller='A',face_down=False,active_face=None)
        values={'name':'Part','type_line':'Creature — Beast','power':'2','toughness':'6'}
        return (root,CompositionObjectSnapshot.capture(root,copiable_values=values,controller='B'),
            CompositionObjectSnapshot.capture(source,copiable_values=values,controller='C',stack_ref='SPELL',stack_kind='spell'))

    def test_storage_couples_ordered_physical_snapshots_and_merge_history(self):
        from quorune.composition_storage import PermanentCompositionStorage
        root,base,incoming=self.snapshots()
        initial=PermanentCompositionStorage.single(base)
        stored=initial.merge(incoming,position=MergePosition.TOP,timestamp=7,expected_object=base.identity)
        self.assertEqual(('INCOMING','PART'),stored.topology.physical_object_ids)
        self.assertEqual((incoming,base),stored.parts)
        self.assertEqual(1,stored.topology.mutation_count)
        self.assertEqual((7,),tuple(step.timestamp for step in stored.merges))
        self.assertEqual(stored,PermanentCompositionStorage.from_dict(stored.to_dict()))
        self.assertEqual(('PART',),initial.topology.physical_object_ids)

    def test_copied_spells_explicitly_become_token_components_without_changing_captured_kind(self):
        from dataclasses import replace
        from quorune.composition_storage import PermanentCompositionStorage
        root,base,incoming=self.snapshots()
        for kind in ('spell_copy','card_copy'):
            raw={**dict(incoming.card_state),'object_kind':kind}
            captured=replace(incoming,card_state=raw,stack_kind='spell_copy' if kind=='spell_copy' else 'spell')
            stored=PermanentCompositionStorage.single(base).merge(captured,position=MergePosition.TOP,
                timestamp=3,expected_object=base.identity)
            self.assertTrue(stored.topology.is_token)
            self.assertEqual(kind,stored.parts[0].card_state['object_kind'])
            self.assertFalse(stored.parts[0].card_state['is_token'])

    def test_storage_rejects_mismatched_parts_steps_and_noncanonical_commander_facts(self):
        from quorune.composition_storage import PermanentCompositionStorage
        root,base,incoming=self.snapshots()
        stored=PermanentCompositionStorage.single(base).merge(incoming,position=MergePosition.BOTTOM,
            timestamp=4,expected_object=base.identity)
        wire=stored.to_dict()
        malformed=[{**wire,'extra':True},{**wire,'schema_version':True},{**wire,'parts':[base.to_dict()]},
            {**wire,'parts':list(reversed(wire['parts']))},{**wire,'merges':[]},
            {**wire,'merges':[{**wire['merges'][0],'timestamp':True}]},
            {**wire,'merges':[{**wire['merges'][0],'position':'middle'}]}]
        for value in malformed:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):PermanentCompositionStorage.from_dict(value)
        from quorune.composition_snapshot import CompositionObjectSnapshot
        legacy=CompositionSnapshotTests().card(is_commander=True)
        unknown=CompositionObjectSnapshot.capture(legacy,copiable_values={},controller='B')
        with self.assertRaises(ValueError):PermanentCompositionStorage.single(unknown)

    def test_carrier_codec_omits_absence_and_validates_the_retained_object(self):
        from quorune.composition_storage import PermanentCompositionStorage
        from quorune.model import CardInstance
        root,base,incoming=self.snapshots();plain=root.to_dict()
        self.assertNotIn('permanent_composition',plain)
        stored=PermanentCompositionStorage.single(base).merge(incoming,position=MergePosition.TOP,
            timestamp=5,expected_object=base.identity)
        root.permanent_composition=stored
        wire=root.to_dict();loaded=CardInstance.from_dict(wire)
        self.assertEqual(wire,loaded.to_dict());self.assertEqual(stored,loaded.permanent_composition)
        with self.assertRaises(ValueError):CardInstance.from_dict({**wire,'zone_change_counter':3})
        with self.assertRaises(ValueError):CardInstance.from_dict({**wire,'zone':'graveyard'})
        with self.assertRaises(ValueError):CardInstance.from_dict({**plain,'permanent_composition':None})
        self.assertEqual(plain,CardInstance.from_dict(plain).to_dict())


class CompositionCharacteristicTests(unittest.TestCase):
    def test_inherited_bound_query_keeps_multiple_modes_and_rejects_active_untrusted_sources(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from quorune.composition_characteristics import composition_day_night_bound_modes
        from quorune.composition_storage import PermanentCompositionStorage
        from quorune.day_night_model import DAYBOUND_TEMPLATE_ID,NIGHTBOUND_TEMPLATE_ID,DayNightBoundMode
        root,base,incoming=CompositionStorageTests().snapshots()
        root.permanent_composition=PermanentCompositionStorage.single(base).merge(incoming,
            position=MergePosition.BOTTOM,timestamp=7,expected_object=base.identity)
        programs=[SimpleNamespace(key=key,provenance={'template_id':template}) for key,template in
            (('day1',DAYBOUND_TEMPLATE_ID),('day2',DAYBOUND_TEMPLATE_ID),('night',NIGHTBOUND_TEMPLATE_ID),('other','unrelated-template'))]
        active={'day1','day2','night','other'};untrusted=set()
        host=SimpleNamespace(_effective_card_data=lambda _card:{'ability_fragments':[]},
            semantic_program_is_current_trusted=lambda program:program.key not in untrusted)
        with patch('quorune.composition_characteristics.copiable_source_trigger_programs',return_value=programs),patch(
                'quorune.semantic_runtime.current_ability_components.program_has_current_ability_fragments',
                side_effect=lambda program,_values:program.key in active):
            self.assertEqual(frozenset({DayNightBoundMode.DAYBOUND,DayNightBoundMode.NIGHTBOUND}),
                composition_day_night_bound_modes(host,root))
            active.discard('night')
            self.assertEqual(frozenset({DayNightBoundMode.DAYBOUND}),composition_day_night_bound_modes(host,root))
            untrusted.add('day2')
            with self.assertRaises(ValueError):composition_day_night_bound_modes(host,root)
            active.difference_update({'day1','day2'})
            self.assertEqual(frozenset(),composition_day_night_bound_modes(host,root))
            active.add('night')
            self.assertEqual(frozenset({DayNightBoundMode.NIGHTBOUND}),composition_day_night_bound_modes(host,root))
            root.phased_out=True
            self.assertEqual(frozenset(),composition_day_night_bound_modes(host,root))

    def test_prepared_physical_faces_reject_a_carrier_status_that_differs_from_its_top(self):
        from quorune.rules.composition_faces import _retained_root
        from quorune.composition_storage import PermanentCompositionStorage
        from quorune.replacement.immutable import FrozenMap
        root,base,incoming=CompositionStorageTests().snapshots()
        storage=PermanentCompositionStorage.single(base).merge(incoming,position=MergePosition.BOTTOM,
            timestamp=7,expected_object=base.identity)
        root.permanent_composition=storage;root.face_down=True
        with self.assertRaises(ValueError):_retained_root(base.identity,FrozenMap(root.to_dict()),storage,storage)

    def test_physical_face_preparation_rejects_malformed_identities_before_reading_the_host(self):
        from quorune.rules.composition_faces import prepare_composition_face_up,prepare_composition_transform
        for identity in (None,True,object(),Identity('PART','OTHER@1')):
            with self.subTest(identity=identity):
                with self.assertRaises(ValueError):prepare_composition_face_up(None,identity)
                with self.assertRaises(ValueError):prepare_composition_transform(None,identity,expected_transform_count=0)

    def test_physical_face_up_uses_double_faced_front_and_keeps_token_creation_identity(self):
        from types import SimpleNamespace
        from quorune.rules.composition_faces import _physical_turn_up_face
        from quorune.defined_token_faces import DEFINED_TOKEN_IDENTITY_FIELD,incubator_definition
        card=CompositionSnapshotTests().card(active_face='Physical back')
        card.annotations['copy_overrides']={'type_line':'Creature','_copiable_oracle_id':'copied-other'}
        record=SimpleNamespace(layout='transform',faces=({'name':'Physical front','type_line':'Sorcery'},
            {'name':'Physical back','type_line':'Creature'}))
        seen=[]
        def lookup(oracle):seen.append(oracle);return record
        host=SimpleNamespace(card_db=SimpleNamespace(by_oracle_id=lookup))
        self.assertEqual(('Physical front','Sorcery'),_physical_turn_up_face(host,card,is_token=False))
        self.assertEqual([card.oracle_id],seen)
        record.faces=({'name':'','type_line':'Creature'},record.faces[1])
        with self.assertRaises(ValueError):_physical_turn_up_face(host,card,is_token=False)
        card.is_token=True;card.object_kind='token';card.active_face='Phyrexian Token'
        card.annotations[DEFINED_TOKEN_IDENTITY_FIELD]=incubator_definition().to_dict()
        self.assertEqual(('Incubator','Token Artifact — Incubator'),_physical_turn_up_face(host,card,is_token=True))
        card.face_down=False
        self.assertEqual(('Phyrexian Token',''),_physical_turn_up_face(host,card,is_token=True))
        card.annotations.pop(DEFINED_TOKEN_IDENTITY_FIELD)
        self.assertEqual(('Phyrexian Token',''),_physical_turn_up_face(host,card,is_token=True))
        card.face_down=True
        self.assertEqual(('Phyrexian Token',''),_physical_turn_up_face(host,card,is_token=True))
        card.is_token=False;record.layout='flip'
        with self.assertRaises(ValueError):_physical_turn_up_face(host,card,is_token=False)

    def test_face_up_plan_restores_an_ordinary_face_down_token_without_physical_double_faces(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from quorune.rules.composition_faces import prepare_composition_face_up,_physical_turn_up_face
        from quorune.composition_snapshot import CompositionObjectSnapshot
        from quorune.composition_storage import PermanentCompositionStorage
        from quorune.characteristic_evaluation import type_parts
        from quorune.replacement.immutable import FrozenMap
        creation={'name':'Physical Beast','type_line':'Token Creature — Beast','power':'2','toughness':'2'}
        token=CompositionSnapshotTests().card(is_token=True,object_kind='token',face_down=True,active_face=None,
            annotations={'token_characteristics':creation})
        source=CompositionSnapshotTests().card(object_id='CARD',ref='CARD_REF',zone='stack',
            oracle_id='physical-card',controller='A',face_down=True,active_face=None)
        base=CompositionObjectSnapshot.capture(token,copiable_values={'type_line':'Creature'},controller='B')
        incoming=CompositionObjectSnapshot.capture(source,copiable_values={'type_line':'Creature'},controller='A',
            stack_ref='STACK:physical-card',stack_kind='spell')
        storage=PermanentCompositionStorage.single(base).merge(incoming,position=MergePosition.TOP,
            timestamp=7,expected_object=base.identity)
        root=replace(token,is_token=False,object_kind='card',permanent_composition=storage)
        before=root.to_dict();seen=[]
        def physical_record(oracle):
            seen.append(oracle)
            self.assertEqual('physical-card',oracle,'Ordinary token creation is not an Oracle card lookup')
            return SimpleNamespace(layout='normal',type_line='Enchantment')
        def values(card):
            self.assertFalse(card.face_down)
            return creation if card.is_token else {'name':'Physical card','type_line':'Enchantment'}
        host=SimpleNamespace(state=SimpleNamespace(cards={root.object_id:root}),seats=tuple('ABC'),
            card_db=SimpleNamespace(by_oracle_id=physical_record),_type_parts=type_parts,_copyable_characteristics=values)
        plan=prepare_composition_face_up(host,Identity(root.object_id,root.logical_object_id))
        self.assertTrue(plan.can_turn_face_up)
        self.assertTrue(all(not part.card_state['face_down'] for part in plan.after.parts))
        self.assertEqual(FrozenMap(creation),plan.after.parts[1].copiable_values)
        self.assertTrue(plan.after.parts[1].card_state['is_token'])
        self.assertEqual(storage.topology,plan.after.topology)
        self.assertEqual(before,root.to_dict());self.assertEqual(['physical-card'],seen)
        unresolved=replace(token,is_token=False,object_kind='spell_copy')
        with self.assertRaises(ValueError):_physical_turn_up_face(host,unresolved,is_token=True)

    def test_face_up_preparation_refuses_any_physical_spell_part_without_partial_status_change(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from quorune.rules.composition_faces import prepare_composition_face_up
        from quorune.composition_snapshot import CompositionObjectSnapshot
        from quorune.composition_storage import PermanentCompositionStorage
        from quorune.characteristic_evaluation import type_parts
        from quorune.entry_copy import COPIABLE_ORACLE_FIELD
        for face_down in (True,False):
            with self.subTest(spell_face_down=face_down):
                root,base,incoming=CompositionStorageTests().snapshots();root.face_down=True
                base=replace(base,card_state=root.to_dict())
                spell=incoming.detached_card();spell.face_down=face_down;spell.oracle_id='physical-spell'
                spell.annotations['copy_overrides']={COPIABLE_ORACLE_FIELD:'copied-creature'}
                incoming=replace(incoming,card_state=spell.to_dict())
                storage=PermanentCompositionStorage.single(base).merge(incoming,position=MergePosition.BOTTOM,
                    timestamp=7,expected_object=base.identity)
                root.permanent_composition=storage;before=root.to_dict()
                records={root.oracle_id:SimpleNamespace(layout='normal',type_line='Creature'),
                    'physical-spell':SimpleNamespace(layout='normal',type_line='Instant')}
                host=SimpleNamespace(state=SimpleNamespace(cards={root.object_id:root}),seats=tuple('ABC'),
                    card_db=SimpleNamespace(by_oracle_id=records.get),_type_parts=type_parts,
                    _copyable_characteristics=lambda _card:{'name':'Creature copy','type_line':'Creature'})
                plan=prepare_composition_face_up(host,Identity(root.object_id,root.logical_object_id))
                self.assertFalse(plan.can_turn_face_up);self.assertEqual((spell.ref,),plan.blocked_refs)
                self.assertEqual(storage,plan.after);self.assertEqual(before,root.to_dict())
                with self.assertRaises(ValueError):replace(plan,viewers=('A','A'))

    def test_physical_transform_tokens_keep_creation_identity_and_refuse_unrepresented_double_faces(self):
        from types import SimpleNamespace
        from quorune.rules.composition_faces import _next_physical_face
        from quorune.characteristic_evaluation import type_parts
        from quorune.defined_token_faces import DEFINED_TOKEN_IDENTITY_FIELD,incubator_definition
        token=CompositionSnapshotTests().card(is_token=True,object_kind='token',face_down=False,active_face=None)
        token.annotations[DEFINED_TOKEN_IDENTITY_FIELD]=incubator_definition().to_dict()
        def no_lookup(*_args):self.fail('Defined token physical faces must use their immutable creation identity')
        host=SimpleNamespace(card_db=SimpleNamespace(by_oracle_id=no_lookup),_type_parts=type_parts)
        self.assertEqual('Phyrexian Token',_next_physical_face(host,token,is_token=True))
        token.active_face='Phyrexian Token'
        self.assertEqual('Incubator',_next_physical_face(host,token,is_token=True))
        token.annotations.pop(DEFINED_TOKEN_IDENTITY_FIELD)
        self.assertIsNone(_next_physical_face(host,token,is_token=True))
        token.is_token=False;token.object_kind='spell_copy'
        record=SimpleNamespace(faces=({'name':'Physical front'},{'name':'Physical back'}))
        host.card_db.by_oracle_id=lambda _oracle:record
        with self.assertRaises(ValueError):_next_physical_face(host,token,is_token=True)
        record.faces=({'name':'Only face'},)
        self.assertIsNone(_next_physical_face(host,token,is_token=True))

    def test_captured_spell_copy_transforms_explicit_token_faces_without_rewriting_capture(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from quorune.rules.composition_faces import prepare_composition_transform,prepare_composition_face_up,_physical_turn_up_face
        from quorune.composition_storage import PermanentCompositionStorage
        from quorune.defined_token_faces import (DEFINED_TOKEN_IDENTITY_FIELD,DEFINED_TOKEN_FACES_FIELD,
            DEFINED_TOKEN_ACTIVE_FACE_FIELD,incubator_definition,defined_token_current_characteristics)
        from quorune.characteristic_evaluation import type_parts
        from quorune.replacement.immutable import FrozenMap
        root,base,incoming=CompositionStorageTests().snapshots()
        definition=incubator_definition().to_dict()
        raw=dict(incoming.card_state)
        raw.update(object_kind='spell_copy',is_token=False,active_face='Incubator',annotations={
            DEFINED_TOKEN_IDENTITY_FIELD:definition,'token_characteristics':{
                DEFINED_TOKEN_FACES_FIELD:definition,DEFINED_TOKEN_ACTIVE_FACE_FIELD:'Incubator'}})
        captured=replace(incoming,card_state=raw,stack_kind='spell_copy')
        storage=PermanentCompositionStorage.single(base).merge(captured,position=MergePosition.BOTTOM,
            timestamp=7,expected_object=base.identity)
        root.permanent_composition=storage;before=root.to_dict();views=[]
        def values(card):
            views.append((card.object_kind,card.is_token,card.active_face))
            return defined_token_current_characteristics(card) if card.is_token else dict(base.copiable_values)
        host=SimpleNamespace(state=SimpleNamespace(cards={root.object_id:root}),seats=tuple('ABC'),
            card_db=SimpleNamespace(by_oracle_id=lambda _oracle:SimpleNamespace(layout='normal',faces=(),type_line='Creature — Beast')),
            _type_parts=type_parts,_copyable_characteristics=values)
        plan=prepare_composition_transform(host,Identity(root.object_id,root.logical_object_id),expected_transform_count=0)
        self.assertIsNotNone(plan,'Explicit copied-spell token faces remain eligible after physical merge')
        changed=plan.after.parts[1]
        self.assertEqual(('INCOMING_REF',),plan.changed_refs)
        self.assertEqual('Phyrexian Token',changed.card_state['active_face'])
        self.assertEqual('Phyrexian Token',changed.copiable_values['name'])
        self.assertEqual([('token',True,'Phyrexian Token')],views)
        self.assertEqual('spell_copy',changed.card_state['object_kind'])
        self.assertFalse(changed.card_state['is_token'])
        self.assertEqual('spell_copy',changed.stack_kind)
        self.assertEqual(captured.identity,changed.identity)
        self.assertEqual(storage.topology,plan.after.topology)
        self.assertEqual(before,root.to_dict())
        self.assertEqual(FrozenMap(raw),plan.before.parts[1].card_state)
        facedown=replace(captured.detached_card(),face_down=True,active_face='Phyrexian Token')
        self.assertEqual(('Incubator','Token Artifact — Incubator'),_physical_turn_up_face(host,facedown,is_token=True))
        malformed=replace(facedown,annotations={DEFINED_TOKEN_IDENTITY_FIELD:{}})
        with self.assertRaises(ValueError):_physical_turn_up_face(host,malformed,is_token=True)
        self.assertEqual('spell_copy',facedown.object_kind);self.assertFalse(facedown.is_token)
        down_base=replace(base,card_state=replace(base.detached_card(),face_down=True).to_dict())
        down_copy=replace(captured,card_state=facedown.to_dict())
        down_storage=PermanentCompositionStorage.single(down_base).merge(down_copy,position=MergePosition.BOTTOM,
            timestamp=7,expected_object=base.identity)
        down_root=replace(root,face_down=True,permanent_composition=down_storage)
        host.state.cards={root.object_id:down_root};down_before=down_root.to_dict();views.clear()
        up=prepare_composition_face_up(host,Identity(root.object_id,root.logical_object_id))
        self.assertTrue(up.can_turn_face_up)
        self.assertTrue(all(not part.card_state['face_down'] for part in up.after.parts))
        self.assertEqual('Incubator',up.after.parts[1].card_state['active_face'])
        self.assertEqual('Incubator',up.after.parts[1].copiable_values['name'])
        self.assertEqual('spell_copy',up.after.parts[1].card_state['object_kind'])
        self.assertFalse(up.after.parts[1].card_state['is_token'])
        self.assertEqual([('card',False,None),('token',True,'Incubator')],views)
        self.assertEqual(down_before,down_root.to_dict())

    def test_transform_preparation_skips_a_face_down_double_faced_component_without_reading_it(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from quorune.rules.composition_faces import prepare_composition_transform,_next_physical_face
        from quorune.composition_storage import PermanentCompositionStorage
        from quorune.characteristic_evaluation import type_parts
        from quorune.replacement.immutable import FrozenMap
        root,base,incoming=CompositionStorageTests().snapshots()
        hidden=replace(incoming.detached_card(),face_down=True,oracle_id='hidden-double-faced')
        incoming=replace(incoming,card_state=hidden.to_dict(),copiable_values=FrozenMap(
            {'name':'','type_line':'Creature','power':'2','toughness':'2'}))
        storage=PermanentCompositionStorage.single(base).merge(incoming,position=MergePosition.BOTTOM,
            timestamp=7,expected_object=base.identity)
        root.permanent_composition=storage;before=root.to_dict();seen=[]
        record=SimpleNamespace(layout='transform',faces=({'name':'Visible front','type_line':'Creature — Beast'},
            {'name':'Visible back','type_line':'Creature — Beast'}))
        def lookup(oracle):
            self.assertNotEqual('hidden-double-faced',oracle,'Ineligible face-down parts need no physical face read')
            seen.append(oracle);return record
        def values(card):
            self.assertNotEqual('hidden-double-faced',card.oracle_id)
            return {'name':'Visible back','type_line':'Creature — Beast'}
        host=SimpleNamespace(state=SimpleNamespace(cards={root.object_id:root}),
            card_db=SimpleNamespace(by_oracle_id=lookup),_type_parts=type_parts,_copyable_characteristics=values)
        self.assertIsNone(_next_physical_face(host,hidden,is_token=False))
        plan=prepare_composition_transform(host,Identity(root.object_id,root.logical_object_id),expected_transform_count=0)
        self.assertIsNotNone(plan)
        self.assertEqual((base.card_state['ref'],),plan.changed_refs)
        self.assertEqual(incoming,plan.after.parts[1])
        self.assertEqual('Visible back',plan.after.parts[0].card_state['active_face'])
        self.assertEqual([root.oracle_id],seen)
        self.assertEqual(before,root.to_dict())
        host.card_db.by_oracle_id=lambda _oracle:SimpleNamespace(layout='normal',faces=())
        self.assertIsNone(prepare_composition_transform(host,Identity(root.object_id,root.logical_object_id),expected_transform_count=0))
        self.assertIsNone(_next_physical_face(None,replace(hidden,is_token=True,object_kind='token'),is_token=True))
        self.assertEqual(before,root.to_dict())

    def test_physical_transform_eligibility_uses_original_card_faces_and_rejects_spell_faces(self):
        from types import SimpleNamespace
        from quorune.rules.composition_faces import _next_physical_face
        from quorune.characteristic_evaluation import type_parts
        from quorune.entry_copy import COPIABLE_ORACLE_FIELD,COPIABLE_FACE_FIELD
        card=CompositionSnapshotTests().card(face_down=False,active_face='Physical front')
        card.annotations['copy_overrides']={COPIABLE_ORACLE_FIELD:'different-copied-oracle',COPIABLE_FACE_FIELD:'Different copied face'}
        record=SimpleNamespace(layout='transform',faces=({'name':'Physical front','type_line':'Creature'},
            {'name':'Physical back','type_line':'Artifact Creature'}))
        seen=[]
        def physical(oracle):seen.append(oracle);return record
        def copied_forbidden(*_args):self.fail('Copied characteristic identity must not select physical faces')
        host=SimpleNamespace(card_db=SimpleNamespace(by_oracle_id=physical),card_record=copied_forbidden,_type_parts=type_parts)
        self.assertEqual('Physical back',_next_physical_face(host,card,is_token=False))
        self.assertEqual([card.oracle_id],seen)
        record.layout='modal_dfc';record.faces=({'name':'Physical front','type_line':'Creature'},
            {'name':'Physical back','type_line':'Sorcery'})
        self.assertIsNone(_next_physical_face(host,card,is_token=False))
        record.layout='meld';self.assertIsNone(_next_physical_face(host,card,is_token=False))
        record.layout='normal';self.assertIsNone(_next_physical_face(host,card,is_token=False))
        record.layout='transform';card.active_face='not-a-physical-face'
        with self.assertRaises(ValueError):_next_physical_face(host,card,is_token=False)
        card.active_face='Physical front';card.face_down=True
        self.assertIsNone(_next_physical_face(host,card,is_token=False))

    def test_replaced_copy_program_instances_override_obsolete_oracle_sources_and_keep_multiplicity(self):
        from types import SimpleNamespace
        from quorune.ability_fragments import StaticComponentSpec
        from quorune.copiable_program_source import CopiableProgramSourceSpec
        from quorune.copiable_static_instance import CopiableStaticInstanceSpec
        from quorune.composition_characteristics import copiable_source_trigger_programs
        current=SimpleNamespace(key='new-source:trigger',active_zone='battlefield',provenance={})
        def obsolete(*_args,**_kwargs):self.fail('Explicit copied program instances must not restore an obsolete Oracle source')
        host=SimpleNamespace(semantics=SimpleNamespace(get=lambda key:current if key==current.key else None,
            programs_for_oracle=obsolete),card_db=SimpleNamespace(by_oracle_id=obsolete))
        instance=CopiableStaticInstanceSpec(current.key,31)
        values={'ability_fragments':(StaticComponentSpec(current.key),instance,instance,
            CopiableProgramSourceSpec('obsolete-source','front'))}
        self.assertEqual([current,current],copiable_source_trigger_programs(host,values,'battlefield'))
        self.assertEqual([],copiable_source_trigger_programs(host,values,'graveyard'))
        with self.assertRaises(ValueError):copiable_source_trigger_programs(host,
            {'ability_fragments':(CopiableStaticInstanceSpec('unknown-program',31),)},'battlefield')

    def test_later_ordinary_copy_rebases_static_creation_after_replacing_the_composed_catalog(self):
        from dataclasses import replace
        from quorune.ability_fragments import StaticComponentSpec,ability_fragment_to_dict
        from quorune.composition_characteristics import composition_copiable_values,copied_static_component_instances
        from quorune.characteristic_evaluation import _copy_effect,evaluate_card_characteristics
        from quorune.continuous_effect_model import Layer
        root,stored=self.stored();base=composition_copiable_values(stored)
        empty={**base,'name':'Empty copy','keywords':[],'ability_fragments':[],'activated_abilities':[]}
        ordinary={**empty,'name':'Ordinary copied source','ability_fragments':[
            ability_fragment_to_dict(StaticComponentSpec('ordinary-source:static:anthem'))]}
        first=replace(_copy_effect(root,empty),effect_id='earlier-empty-copy',timestamp=30)
        second=replace(_copy_effect(root,ordinary),effect_id='later-ordinary-copy',timestamp=31)
        current=evaluate_card_characteristics(root,base,runtime_effects=(first,second),maximum_layer=Layer.COPY)
        self.assertEqual('Ordinary copied source',current['name'])
        self.assertEqual((('ordinary-source:static:anthem',31),),copied_static_component_instances(
            current['ability_fragments'],('ordinary-source:static:anthem',)))

    def test_copied_early_static_selection_uses_its_source_layer_instead_of_late_keys(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from quorune.ability_fragments import StaticComponentSpec
        from quorune.continuous_effect_model import Layer
        from quorune.card_programs.runtime import _continuous_programs_for_source
        source=SimpleNamespace(zone='battlefield',annotations={},oracle_id='retained-oracle')
        program=SimpleNamespace(key='copied:static:early',handlers=({'handler_id':'fixture:early'},),
            active_zone='battlefield',event='characteristics.evaluate',ability_id='static:early',provenance={})
        semantics=SimpleNamespace(get=lambda key:program if key==program.key else None,
            runtime_handler_programs_for_oracle=lambda *_args,**_kwargs:())
        registry=SimpleNamespace(source_component_layer=lambda _descriptor:Layer.TYPE)
        fragments=(StaticComponentSpec(program.key),)
        calls=[]
        def early(_source,layer):calls.append(layer);return (program.key,)
        with patch('quorune.card_programs.runtime.default_continuous_effect_component_registry',return_value=registry):
            self.assertEqual((program,),_continuous_programs_for_source(semantics,source,lambda _source:(),
                early_static_component_resolver=early,copied_source_fragments=fragments))
            self.assertEqual([Layer.TYPE],calls)
            self.assertEqual((),_continuous_programs_for_source(semantics,source,lambda _source:(program.key,),
                early_static_component_resolver=lambda *_args:(),copied_source_fragments=fragments))
            self.assertEqual((),_continuous_programs_for_source(semantics,source,lambda _source:(),
                early_static_component_resolver=early,maximum_layer=Layer.COPY,copied_source_fragments=fragments))
            self.assertEqual((),_continuous_programs_for_source(semantics,source,lambda _source:(),
                early_static_component_resolver=early,copied_source_fragments=(StaticComponentSpec('unknown:key'),)))

    def test_copied_source_snapshot_stops_at_copy_layer_without_static_collection(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from quorune.composition_characteristics import copied_static_source_fragments,composition_copiable_values
        from quorune.copiable_program_source import CopiableProgramSourceSpec
        from quorune.characteristic_evaluation import evaluate_card_characteristics
        from quorune.continuous_effect_model import ContinuousEffect,ContinuousOperation,Layer
        root,stored=self.stored()
        late=ContinuousEffect('later-loss',root.object_id,Layer.ABILITY,'6',50,
            (ContinuousOperation('remove_all_abilities'),))
        def collect_forbidden(*_args,**_kwargs):self.fail('Copy-layer source capture must not collect static effects')
        host=SimpleNamespace(state=None,card_record=lambda _card:None,
            _compiled_base_characteristics=lambda *_args,**_kwargs:composition_copiable_values(stored),
            _effective_card_data=collect_forbidden,
            _apply_layered_characteristic_annotations=lambda card,base,**kwargs:evaluate_card_characteristics(card,base,
                runtime_effects=kwargs['runtime_effects'],maximum_layer=kwargs['maximum_layer']))
        with patch('quorune.continuous_effect_state.active_resolution_effects',return_value=(late,)):
            snapshot=copied_static_source_fragments(host,root)
            self.assertTrue(any(isinstance(fragment,CopiableProgramSourceSpec) for fragment in snapshot))
            plain=CompositionSnapshotTests().card(face_down=False,active_face=None)
            host._compiled_base_characteristics=collect_forbidden
            self.assertIsNone(copied_static_source_fragments(host,plain))

    def test_copied_and_granted_static_occurrence_ties_keep_legacy_integer_order(self):
        from quorune.card_programs.runtime import _granted_characteristic_timestamp_order
        from quorune.continuous_effect_model import ContinuousEffect,ContinuousOperation,Layer
        def effect(key):return ContinuousEffect(key,'SOURCE',Layer.ABILITY,'6',30,
            (ContinuousOperation('add_ability','flying'),))
        copied,first,second=effect('copied'),effect('grant-first'),effect('grant-second')
        orders={'copied':(30,'copy:0','same-program'),'grant-first':(30,2,'same-program'),
            'grant-second':(30,10,'same-program')}
        rows=_granted_characteristic_timestamp_order((copied,second,first),orders)
        by_id={value.effect_id:value for value in rows}
        self.assertEqual((),by_id['grant-first'].depends_on)
        self.assertEqual(('grant-first',),by_id['grant-second'].depends_on)
        self.assertEqual(('grant-second',),by_id['copied'].depends_on)

    def test_copied_static_instance_codec_rebases_multiplicity_and_preserves_legacy_absence(self):
        from quorune.ability_fragments import StaticComponentSpec,canonical_ability_fragments
        from quorune.copiable_program_source import CopiableProgramSourceSpec
        from quorune.copiable_static_instance import CopiableStaticInstanceSpec
        from quorune.composition_characteristics import copied_static_fragments,copied_static_component_instances
        component=StaticComponentSpec('source:anthem')
        self.assertEqual({'schema_version':1,'semantic_key':'source:anthem'},component.to_dict())
        self.assertEqual((component,),copied_static_fragments((component,),None))
        values=(component,component,CopiableProgramSourceSpec('source-oracle','front'))
        first=copied_static_fragments(values,10);second=copied_static_fragments(first,20)
        self.assertEqual((('source:anthem',10),)*2,copied_static_component_instances(first,('source:anthem',)))
        self.assertEqual((('source:anthem',20),)*2,copied_static_component_instances(second,('source:anthem',)))
        self.assertEqual((),copied_static_component_instances(second,()))
        instance=CopiableStaticInstanceSpec('source:anthem',20)
        self.assertEqual(instance,CopiableStaticInstanceSpec.from_dict(instance.to_dict()))
        for bad in ({**instance.to_dict(),'extra':True},{**instance.to_dict(),'schema_version':True},
                {**instance.to_dict(),'creation_timestamp':True},{**instance.to_dict(),'creation_timestamp':-1}):
            with self.assertRaises(ValueError):CopiableStaticInstanceSpec.from_dict(bad)
        with self.assertRaises(ValueError):copied_static_fragments(values,None)
        with self.assertRaises(ValueError):copied_static_component_instances((component,instance,instance),('source:anthem',))
        self.assertEqual(canonical_ability_fragments(values),canonical_ability_fragments(
            value for value in first if not isinstance(value,CopiableStaticInstanceSpec)))

    def test_copied_static_program_lowering_keeps_distinct_instances_and_current_membership(self):
        from types import SimpleNamespace
        from quorune.card_programs.runtime import _continuous_program_instances
        source=SimpleNamespace(zone='battlefield')
        program=SimpleNamespace(key='source:anthem',provenance={},ability_id='static:anthem',event='characteristics.evaluate')
        refs=lambda _source:(('source:anthem',30),('source:anthem',30))
        rows=_continuous_program_instances(None,source,(program,),None,refs)
        self.assertEqual((program,program),tuple(row[0] for row in rows))
        self.assertEqual((30,30),tuple(row[1] for row in rows))
        self.assertEqual(2,len({row[2] for row in rows}))
        self.assertEqual((),_continuous_program_instances(None,source,(),None,refs))
        self.assertEqual(((program,None,None),),_continuous_program_instances(None,source,(program,),None))

    def test_program_source_codec_and_collection_preserve_repeated_faces(self):
        from types import SimpleNamespace
        from quorune.copiable_program_source import CopiableProgramSourceSpec
        from quorune.ability_fragments import ability_fragment_to_dict,ability_fragment_from_dict
        from quorune.composition_characteristics import copiable_source_trigger_programs
        source=CopiableProgramSourceSpec('source-oracle','Back')
        wire=ability_fragment_to_dict(source)
        self.assertEqual(source,ability_fragment_from_dict(wire))
        for bad in ({**source.to_dict(),'extra':True},{**source.to_dict(),'schema_version':True},
                {**source.to_dict(),'face_id':''},{**source.to_dict(),'oracle_id':' source-oracle'}):
            with self.assertRaises(ValueError):CopiableProgramSourceSpec.from_dict(bad)
        record=SimpleNamespace(faces=({'name':'Front'},{'name':'Back'}))
        front=SimpleNamespace(key='front',provenance={'face_id':'front'})
        back=SimpleNamespace(key='back',provenance={'face_id':'back'})
        host=SimpleNamespace(card_db=SimpleNamespace(by_oracle_id=lambda _oracle:record),
            semantics=SimpleNamespace(programs_for_oracle=lambda *_args,**_kwargs:(front,back)))
        values={'ability_fragments':[wire,wire]}
        self.assertEqual([back,back],copiable_source_trigger_programs(host,values,'battlefield'))
        self.assertIsNone(copiable_source_trigger_programs(host,{},'battlefield'))
        host.card_db.by_oracle_id=lambda _oracle:None
        with self.assertRaises(ValueError):copiable_source_trigger_programs(host,values,'battlefield')

    def stored(self,position=MergePosition.TOP):
        from dataclasses import replace
        from quorune.composition_storage import PermanentCompositionStorage
        from quorune.ability_fragments import ToxicSpec,ability_fragment_to_dict
        root,base,incoming=CompositionStorageTests().snapshots()
        toxic=ability_fragment_to_dict(ToxicSpec(2))
        base=replace(base,copiable_values={'name':'Base','type_line':'Creature — Beast',
            'mana_cost':'{G}','mana_value':1,'power':'2','toughness':'6','colors':['G'],
            'keywords':['Vigilance'],'ability_fragments':[toxic],'activated_abilities':[]})
        incoming=replace(incoming,copiable_values={'name':'Top','type_line':'Artifact Creature — Construct',
            'mana_cost':'{3}','mana_value':3,'power':'5','toughness':'7','colors':[],
            'keywords':['Flying'],'ability_fragments':[toxic],'activated_abilities':[]})
        stored=PermanentCompositionStorage.single(base).merge(incoming,position=position,
            timestamp=20,expected_object=base.identity)
        root.permanent_composition=stored
        return root,stored

    def test_top_values_combine_all_ability_instances_without_copying_physical_state(self):
        from quorune.composition_characteristics import composition_copiable_values
        from quorune.ability_fragments import toxic_specs,canonical_ability_fragments
        for position,name,power in ((MergePosition.TOP,'Top','5'),(MergePosition.BOTTOM,'Base','2')):
            with self.subTest(position=position):
                root,stored=self.stored(position);before=stored.to_dict()
                values=composition_copiable_values(stored)
                self.assertEqual((name,power),(values['name'],values['power']))
                self.assertEqual({'Flying','Vigilance'},set(values['keywords']))
                self.assertEqual((2,2),tuple(value.value for value in toxic_specs(
                    canonical_ability_fragments(values['ability_fragments']))))
                self.assertNotIn('permanent_composition',values)
                self.assertNotIn('counters',values)
                values['ability_fragments'].clear()
                self.assertEqual(before,stored.to_dict())

    def test_merge_timestamp_orders_before_later_copy_and_after_older_copy(self):
        from dataclasses import replace
        from quorune.composition_characteristics import composition_copiable_values
        from quorune.characteristic_evaluation import _copy_effect,evaluate_card_characteristics
        from quorune.continuous_effect_model import Layer
        root,stored=self.stored();base=composition_copiable_values(stored)
        copied={**base,'name':'External copy','power':'9','toughness':'11',
            'keywords':['Haste'],'ability_fragments':[],'activated_abilities':[]}
        effect=_copy_effect(root,copied)
        earlier=evaluate_card_characteristics(root,base,runtime_effects=(replace(effect,timestamp=19),),maximum_layer=Layer.COPY)
        later=evaluate_card_characteristics(root,base,runtime_effects=(replace(effect,timestamp=21),),maximum_layer=Layer.COPY)
        self.assertEqual(('Top','5',{'Flying','Vigilance'}),
            (earlier['name'],earlier['power'],set(earlier['keywords'])))
        self.assertEqual(('External copy','9',['Haste'],[]),
            (later['name'],later['power'],later['keywords'],later['ability_fragments']))


class CompositionOrderChoiceTests(unittest.TestCase):
    def request(self,destination='graveyard'):
        from quorune.composition_storage import PermanentCompositionStorage
        from quorune.composition_order_model import CompositionOrderRequest
        root,base,incoming=CompositionStorageTests().snapshots()
        root.permanent_composition=PermanentCompositionStorage.single(base).merge(incoming,
            position=MergePosition.BOTTOM,timestamp=4,expected_object=base.identity)
        return root,CompositionOrderRequest(root.to_dict(),destination,'C','A','order fixture')

    def test_order_request_binds_owner_scope_and_complete_physical_permutation(self):
        from types import SimpleNamespace
        from dataclasses import replace
        from quorune.composition_order_model import CompositionOrderRequest
        root,request=self.request()
        self.assertEqual(request,CompositionOrderRequest.from_dict(request.to_dict()))
        self.assertEqual(('PART','INCOMING'),request.ordered_ids(request.refs))
        with self.assertRaises(ValueError):replace(request,chooser='B')
        with self.assertRaises(ValueError):request.ordered_ids((request.refs[0],request.refs[0]))
        with self.assertRaises(ValueError):CompositionOrderRequest.from_dict({**request.to_dict(),'unknown':True})
        state=SimpleNamespace(cards={root.object_id:root});request.validate_current(state)
        root.tapped=not root.tapped
        with self.assertRaises(ValueError):request.validate_current(state)

    def test_saved_parent_effect_cannot_substitute_another_trusted_instruction(self):
        from types import SimpleNamespace
        from quorune.composition_ordering import validate_original_composition_effect
        from quorune.errors import GameRuleError
        original={'op':'destroy','card':'ROOT'}
        host=SimpleNamespace(semantics=SimpleNamespace(get=lambda key:SimpleNamespace(effects=(original,))),
            semantic_program_is_current_trusted=lambda program:True,_semantic_value=lambda effect,item:dict(effect))
        item=SimpleNamespace(semantic_key='parent')
        validate_original_composition_effect(host,item,original,0)
        validate_original_composition_effect(host,item,{**original,'_replacement_selections':[]},0)
        with self.assertRaises(GameRuleError):validate_original_composition_effect(host,item,
            {'op':'gain_life','player':'C','amount':100},0)
        with self.assertRaises(GameRuleError):validate_original_composition_effect(host,item,original,1)
        host.semantic_program_is_current_trusted=lambda program:False
        with self.assertRaises(GameRuleError):validate_original_composition_effect(host,item,original,0)

    def test_resolved_order_request_keeps_original_actor_and_exiler_distinct(self):
        from dataclasses import replace
        from quorune.composition_order_model import CompositionOrderRequest
        root,plain=self.request()
        self.assertNotIn('resolved_destination',plain.to_dict())
        resolved=replace(plain,schema_version=2,resolved_destination='exile',ordering_actor='D',chooser='D')
        self.assertEqual(('C','D'),(resolved.actor,resolved.chooser))
        self.assertEqual(resolved,CompositionOrderRequest.from_dict(resolved.to_dict()))
        with self.assertRaises(ValueError):replace(resolved,chooser='C')
        with self.assertRaises(ValueError):replace(plain,resolved_destination='exile')


    def test_order_handler_masks_unknown_library_parts_and_rejects_extra_response_fields(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from quorune.composition_storage import PermanentCompositionStorage
        from quorune.composition_order_model import CompositionOrderRequest
        from quorune.semantic_choices.composition_order import CompositionOrderHandler
        from quorune.semantic_choices.model import SemanticChoiceError
        root,base,incoming=CompositionStorageTests().snapshots()
        hidden=replace(base,card_state={**dict(base.card_state),'face_down':True,'known_to':['B'],'revealed_to':[]})
        root.permanent_composition=PermanentCompositionStorage.single(hidden).merge(incoming,
            position=MergePosition.BOTTOM,timestamp=4,expected_object=base.identity)
        request=CompositionOrderRequest(root.to_dict(),'library','C','A','private order fixture')
        effect={'op':'composition_departure_order','player':'A','request':request.to_dict(),
            'original_effect':{'op':'destroy','card':root.ref}}
        context=SimpleNamespace(actor='A',stack_controller='C',stack_ref='SPELL',
            query=SimpleNamespace(object=lambda ref,**kwargs:root))
        preparation=CompositionOrderHandler().prepare(effect,context)
        rows=preparation.request.payload()['objects']
        self.assertEqual('Face-down component',next(row['name'] for row in rows if row['id']==root.ref))
        continuation=SimpleNamespace(effect=effect)
        for response in ({'cards':request.refs,'actor':'C'},
                         {'cards':request.refs,'choice_schema':{'legal_refs':['forged']}}):
            with self.assertRaises(SemanticChoiceError):CompositionOrderHandler().complete(continuation,response,context.query)


class CompositionReplacementProjectionTests(unittest.TestCase):
    def prepared(self):
        from dataclasses import dataclass
        from quorune.replacement import ReplaceableEvent,AffectedObject,ReplacementEffect,ReplacementClass
        from quorune.replacement.operations import CreateAffectedObjectCounter
        effect=ReplacementEffect('replace','REPLACER','zone.change',ReplacementClass.OTHER,
            operations=({'op':'set','field':'destination','value':'exile'},
                CreateAffectedObjectCounter('void',1,'D','REPLACER')))
        child=ReplaceableEvent('counter','counter.place',None,affected_object=AffectedObject('ROOT','A',None),
            payload={'target_kind':'card','target_zone':'exile','target_types':['creature'],
                'target_controller':None,'placing_player':'D','counter_name':'void','amount':1,
                'requested_amount':1,'source':'REPLACER','follows_zone_destination':True})
        event=ReplaceableEvent('zone','zone.change',None,affected_object=AffectedObject('ROOT','A','B'),
            payload={'destination':'exile'},applied_effects=('replace',),children=(child,))
        @dataclass
        class PreparedFixture:
            object_id: str
            destination: str
            event: object
            effects: tuple
            counter_events: tuple
            keyword_grants: tuple=()
            devour_sacrifices: tuple=()
        return PreparedFixture('ROOT','exile',event,(effect,),(child,))

    def test_counter_projection_uses_each_new_recipient_and_the_selected_source_actor(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from quorune.composition_replacements import project_composition_counters,composition_ordering_principal
        prepared=self.prepared()
        first=CompositionSnapshotTests().card(object_id='ONE',ref='ONE',zone='exile')
        second=CompositionSnapshotTests().card(object_id='TWO',ref='TWO',zone='exile')
        host=SimpleNamespace(state=SimpleNamespace(cards={'replacer':SimpleNamespace(ref='REPLACER',controller='D')}),
            active_seats=tuple('ABCD'),_effective_card_data=lambda card:{'type_line':card.ref},
            _type_parts=lambda text:({'creature'} if text=='ONE' else {'artifact'},set(),set()))
        self.assertEqual('D',composition_ordering_principal(host,SimpleNamespace(owner='A'),prepared,'C'))
        projected=project_composition_counters(host,prepared,(first,second))
        self.assertEqual(('ONE','TWO'),tuple(event.affected_object.object_id for event in projected.counter_events))
        self.assertEqual((('creature',),('artifact',)),tuple(tuple(event.payload['target_types']) for event in projected.counter_events))
        self.assertTrue(all(event.payload['placing_player']=='D' and event.payload['amount']==1 for event in projected.counter_events))
        self.assertEqual(2,len({event.event_id for event in projected.counter_events}))
        self.assertEqual(('ROOT',),tuple(event.affected_object.object_id for event in prepared.counter_events))

    def test_nested_counter_modifiers_and_changed_amounts_reject_before_projection(self):
        from dataclasses import replace
        from quorune.composition_replacements import validate_composition_replacements
        from quorune.errors import GameRuleError
        for changes in ({'amount':2},{'amount':True},{'_counter_prohibited':True}):
            prepared=self.prepared();child=replace(prepared.counter_events[0],
                payload={**dict(prepared.counter_events[0].payload),**changes})
            prepared.counter_events=(child,);prepared.event=replace(prepared.event,children=(child,))
            with self.assertRaises(GameRuleError):validate_composition_replacements(prepared)
        prepared=self.prepared();child=replace(prepared.counter_events[0],applied_effects=('double',))
        prepared.counter_events=(child,);prepared.event=replace(prepared.event,children=(child,))
        with self.assertRaises(GameRuleError):validate_composition_replacements(prepared)

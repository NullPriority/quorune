from __future__ import annotations

from dataclasses import replace
import json,unittest
from pathlib import Path
import tempfile
from common import ROOT
from quorune.devour import DevourSpec,DevourQuality,DevourObject,DevourProposal
from quorune.replacement.model import AffectedObject,EntryReplacementScope,ReplaceableEvent,ReplacementEffect,ReplacementClass,ReplacementEventBatch,ReplacementSelection
from quorune.replacement.operations import DevourEntryOperation,operation_from_dict
from quorune.replacement.ordering import next_batch_replacement_choice,apply_batch_replacement,resolve_replacement_batch,replacement_choice_payload


class DevourModelTests(unittest.TestCase):
    def test_fixed_zero_and_variable_devour_counter_results_follow_the_selected_count(self):
        for quality in DevourQuality:
            for multiplier in (0,1,2,3):
                spec=DevourSpec(quality,multiplier)
                self.assertEqual([0,multiplier,2*multiplier,3*multiplier],[spec.counter_amount(n) for n in range(4)])
                self.assertEqual(spec,DevourSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        square=DevourSpec(DevourQuality.CREATURE,'devoured_count')
        self.assertEqual([0,1,4,9,16],[square.counter_amount(n) for n in range(5)])

    def test_devour_proposals_keep_distinct_incarnations_and_closed_typed_codecs(self):
        objects=tuple(DevourObject('id'+str(i),'incarnation'+str(i),'REF'+str(i)) for i in range(3))
        proposal=DevourProposal('A','devour-component',DevourSpec(DevourQuality.CREATURE,'devoured_count'),objects)
        self.assertEqual(9,proposal.counter_amount)
        self.assertEqual(proposal,DevourProposal.from_dict(json.loads(json.dumps(proposal.to_dict()))))
        self.assertEqual(0,replace(proposal,objects=()).counter_amount)
        for key in ('object_id','logical_object_id','ref'):
            repeated=(objects[0],replace(objects[1],**{key:getattr(objects[0],key)}))
            with self.subTest(key=key),self.assertRaises(ValueError):replace(proposal,objects=repeated)
        raw=proposal.to_dict()
        for change in ({'schema_version':True},{'unknown':1},{'objects':'REF0'},{'controller':''}):
            with self.subTest(change=tuple(change)),self.assertRaises(ValueError):DevourProposal.from_dict({**raw,**change})
        for change in ({'multiplier':True},{'multiplier':-1},{'multiplier':'X'},{'quality':'food','multiplier':'devoured_count'}):
            with self.subTest(change=change),self.assertRaises(ValueError):DevourSpec.from_dict({**proposal.spec.to_dict(),**change})


class DevourCompilerTests(unittest.TestCase):
    def test_fixed_quality_and_selected_count_devour_have_source_pinned_components(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from test_granted_skulk import record
        registry=load_default_capability_registry()
        for text in ('Devour 0','Devour 2','Devour artifact 1','Devour Food 3',
                'Devour X, where X is the number of creatures devoured this way','Devour 1, devour 2'):
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,'Creature'),capability_registry=registry)
                self.assertEqual('exact',ir.status,ir.material_residuals)
                for node in ir.faces[0].nodes:
                    self.assertEqual(node.text,text[node.span.start:node.span.end])
                    self.assertIn('zone.entry.devour',node.capability_dependencies)

    def test_unknown_devour_variants_and_open_descriptors_remain_closed(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.semantic_runtime.devour import DevourEntryHandler
        from test_granted_skulk import record
        registry=load_default_capability_registry()
        for text in ('Devour X','Devour enchantment 2','Devour 2 if you control a Forest','Devour Food X','Devour -1'):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature'),capability_registry=registry).status)
        descriptor={'handler_id':'replacement.zone.devour-entry.v1','schema_version':1,'event':'zone.change','devour':DevourSpec(DevourQuality.CREATURE,2).to_dict()}
        for change in ({'schema_version':True},{'extra':1},{'event':'step.begin'}):
            with self.subTest(change=change),self.assertRaises(ValueError):DevourEntryHandler().validate({**descriptor,**change})


class DevourReplacementModelTests(unittest.TestCase):
    def boundary(self,count=1,multiplier=2):
        scope=EntryReplacementScope(tuple('new'+str(i) for i in range(count)))
        candidates=tuple(DevourObject('old'+str(i),'old-incarnation'+str(i),'OLD'+str(i)) for i in range(3))
        events=[];effects=[]
        for i in range(count):
            event=ReplaceableEvent(event_id='event'+str(i),kind='zone.change',affected_player=None,affected_object=AffectedObject('new'+str(i),'A','A'),
                payload={'object_ref':'NEW'+str(i),'logical_object_id':'new-incarnation'+str(i),'origin':'stack',
                    'destination':'battlefield','destination_controller':'A','object_types':['creature']},entry_scope=scope)
            operation=DevourEntryOperation('A','component'+str(i),DevourSpec(DevourQuality.CREATURE,multiplier),candidates)
            effects.append(ReplacementEffect(effect_id='devour'+str(i),source_id='NEW'+str(i),event_kind='zone.change',replacement_class=ReplacementClass.OTHER,
                conditions={'object_ref':{'eq':'NEW'+str(i)},'destination':{'eq':'battlefield'}},operations=(operation,),optional=True))
            events.append(event)
        return ReplacementEventBatch('devour-batch',tuple(events),tuple('ABCD')),tuple(effects)

    def choose(self,batch,effects,refs):
        pending=next_batch_replacement_choice(batch,effects)
        selection={'effect_id':pending.choice.options[0],'objects':refs,
            **({'event_id':pending.event_id} if pending.event_order_options else {})}
        return apply_batch_replacement(batch,effects,pending,selection)

    def test_selected_devour_is_a_pure_counter_proposal_with_stable_journal_replay(self):
        for multiplier,expected in ((2,4),('devoured_count',4),(0,0)):
            with self.subTest(multiplier=multiplier):
                batch,effects=self.boundary(multiplier=multiplier);original=batch.to_dict()
                changed=self.choose(batch,effects,['OLD0','OLD1'])
                self.assertEqual(original,batch.to_dict())
                event,=changed.events;proposal,=event.payload['devour_proposals']
                self.assertEqual(expected,DevourProposal.from_dict(proposal).counter_amount)
                self.assertEqual(expected,sum(c.payload.get('amount',0) for c in event.children if c.kind=='counter.place'))
                self.assertEqual(('OLD0','OLD1'),changed.journal[0].objects)
                self.assertEqual(changed.to_dict(),ReplacementEventBatch.from_dict(json.loads(json.dumps(changed.to_dict()))).to_dict())
                self.assertEqual(changed.to_dict(),resolve_replacement_batch(batch,effects,selections=changed.journal).to_dict())

    def test_simultaneous_entry_reservations_prevent_reusing_one_devoured_object(self):
        batch,effects=self.boundary(2)
        first=self.choose(batch,effects,['OLD0'])
        self.assertTrue(all(e.entry_scope.reserved_zone_changes==('old0',) for e in first.events))
        with self.assertRaises(ValueError):self.choose(first,effects,['OLD0'])
        complete=self.choose(first,effects,['OLD1'])
        self.assertTrue(all(set(e.entry_scope.reserved_zone_changes)=={'old0','old1'} for e in complete.events))
        self.assertEqual(complete.to_dict(),resolve_replacement_batch(batch,effects,selections=complete.journal).to_dict())

    def test_zero_decline_legacy_shapes_and_unknown_selections_remain_distinct(self):
        scope={'entering_objects':['new0'],'entering_from_library':[],'reserved_zone_changes':[]}
        self.assertEqual(scope,EntryReplacementScope.from_dict(scope).to_dict())
        legacy={'event_id':'event0','path':[],'chooser':'A','effect_id':'some-effect'}
        self.assertEqual(legacy,ReplacementSelection.from_dict(legacy).to_dict())
        batch,effects=self.boundary()
        zero=self.choose(batch,effects,[])
        self.assertEqual((),zero.journal[0].objects);self.assertFalse(zero.events[0].children)
        pending=next_batch_replacement_choice(batch,effects)
        decline=apply_batch_replacement(batch,effects,pending,'decline:'+pending.choice.options[0])
        self.assertNotIn('devour_proposals',decline.events[0].payload)
        self.assertNotIn('objects',decline.journal[0].to_dict())
        for refs in (['OLD0','OLD0'],['NEW0'],['foreign'],[True]):
            with self.subTest(refs=refs),self.assertRaises(ValueError):self.choose(batch,effects,refs)
        operation=effects[0].operations[0]
        self.assertEqual(operation,operation_from_dict(json.loads(json.dumps(operation.to_dict()))))

    def test_new_object_choice_fields_reject_null_boolean_and_cross_kind_payloads(self):
        raw={'event_id':'event0','path':[],'chooser':'A','effect_id':'effect'}
        for objects in (None,False,'OLD0',['OLD0','OLD0']):
            with self.subTest(objects=objects),self.assertRaises(ValueError):ReplacementSelection.from_dict({**raw,'objects':objects})
        with self.assertRaises(ValueError):ReplacementSelection.from_dict({**raw,'objects':[],'allocation':{'damage':1}})

    def test_object_choice_payload_hides_already_reserved_objects_and_accepts_typed_response(self):
        from quorune.replacement_decisions import _replacement_selection
        from quorune.replacement.replay import _decode_combat_selections
        from quorune.replacement.immutable import thaw_value
        batch,effects=self.boundary(2)
        first=self.choose(batch,effects,['OLD0']);pending=next_batch_replacement_choice(first,effects)
        payload=replacement_choice_payload(pending,effects)
        schema=payload['legal_actions'][0]['choice_schema']['replacement_objects']
        self.assertEqual(['OLD1','OLD2'],schema['legal_refs']);self.assertEqual(0,schema['minimum'])
        selected=_replacement_selection({'replacement':pending.choice.options[0],'replacement_objects':['OLD1']},pending,error_type=ValueError)
        decoded,=_decode_combat_selections({'replacement_selections':[selected]})
        final=apply_batch_replacement(first,effects,pending,thaw_value(decoded))
        self.assertEqual(('OLD1',),final.journal[-1].objects)
        for value in (True,'OLD1',['OLD1','OLD1']):
            with self.assertRaises(ValueError):_replacement_selection({'replacement':pending.choice.options[0],'replacement_objects':value},pending,error_type=ValueError)

    def test_objects_cannot_be_added_to_an_unrelated_replacement_selection(self):
        from quorune.replacement.operations import SetField
        batch,effects=self.boundary()
        old=replace(effects[0],effect_id='ordinary-entry',operations=(SetField('tapped',True),),optional=False)
        pending=next_batch_replacement_choice(batch,(old,))
        with self.assertRaises(ValueError):apply_batch_replacement(batch,(old,),pending,{'effect_id':old.effect_id,'objects':['OLD0']})
        result=apply_batch_replacement(batch,(old,),pending,old.effect_id)
        self.assertTrue(result.events[0].payload['tapped']);self.assertNotIn('objects',result.journal[0].to_dict())

    def test_multiple_devour_instances_share_one_counter_occurrence_with_one_additional_counter(self):
        from quorune.replacement.operations import AddAmount
        from quorune.replacement import advance_replacement_batch
        batch,effects=self.boundary()
        second=replace(effects[0],effect_id='second-devour',operations=(replace(effects[0].operations[0],
            component_id='second-devour',spec=DevourSpec(DevourQuality.CREATURE,1)),))
        scales=ReplacementEffect(effect_id='scales',source_id='scales',event_kind='counter.place',replacement_class=ReplacementClass.OTHER,
            conditions={'counter_name':{'eq':'+1/+1'}},operations=(AddAmount('amount',1),))
        final=advance_replacement_batch(batch,(*effects,second,scales),selections=(
            {'effect_id':effects[0].effect_id,'objects':['OLD0']},{'effect_id':second.effect_id,'objects':['OLD1']}))
        self.assertIsNone(final.pending);self.assertEqual(1,len(final.batch.events[0].children));counter,=final.batch.events[0].children
        self.assertEqual(4,counter.payload['amount']);self.assertEqual(('scales',),counter.applied_effects)

    def test_controller_can_choose_either_simultaneous_devour_event_first(self):
        from quorune.replacement_decisions import _replacement_selection
        from quorune.replacement import advance_replacement_batch
        batch,effects=self.boundary(2);pending=next_batch_replacement_choice(batch,effects)
        self.assertEqual(('event0','event1'),pending.event_order_options)
        payload=replacement_choice_payload(pending,effects)
        self.assertEqual(['devour1','decline:devour1'],payload['event_effect_options']['event1'])
        selection=_replacement_selection({'replacement':'devour1','replacement_event':'event1','replacement_objects':['OLD0']},pending,error_type=ValueError)
        changed=apply_batch_replacement(batch,effects,pending,selection)
        self.assertEqual('event1',changed.journal[0].event_id)
        final=advance_replacement_batch(changed,effects,selections=({'effect_id':'devour0','objects':['OLD1']},))
        self.assertIsNone(final.pending)
        self.assertEqual(['old1','old0'],[event.payload['devour_proposals'][0]['objects'][0]['object_id'] for event in final.batch.events])
        with self.assertRaises(ValueError):_replacement_selection({'replacement':'devour0','replacement_event':'event1'},pending,error_type=ValueError)


class DevourPreparationTests(unittest.TestCase):
    def snapshot(self,parents=1,redirect=False):
        from quorune.semantic_runtime.zone_replacement_model import ZoneChangeSubjectSnapshot,ZoneChangeReplacementSnapshot
        from quorune.zone_trigger_events import ZoneTransitionKind
        from quorune.replacement.operations import MultiplyAmount,SetField
        batch,effects=DevourReplacementModelTests().boundary(parents)
        subjects=tuple(ZoneChangeSubjectSnapshot(object_id='new'+str(i),object_ref='NEW'+str(i),logical_object_id='new-incarnation'+str(i),
            owner='A',controller='A',origin='stack',destination='battlefield',destination_controller='A',entry_face_id='front',object_types=('creature',),is_card_object=True) for i in range(parents))
        candidates=tuple(ZoneChangeSubjectSnapshot(object_id='old'+str(i),object_ref='OLD'+str(i),logical_object_id='old-incarnation'+str(i),
            owner='B' if i==0 else 'A',controller='A',origin='battlefield',destination='graveyard',destination_controller='B' if i==0 else 'A',
            entry_face_id='front',object_types=('creature',),is_card_object=True,transition_kind=ZoneTransitionKind.SACRIFICE) for i in range(3))
        effects=(*effects,ReplacementEffect(effect_id='counter-double',source_id='OLD0',event_kind='counter.place',replacement_class=ReplacementClass.OTHER,
            conditions={'counter_name':{'eq':'+1/+1'}},operations=(MultiplyAmount('amount',2),)))
        if redirect:
            effects=(*effects,*(ReplacementEffect(effect_id='sac-'+destination,source_id='redirect-'+destination,event_kind='zone.change',replacement_class=ReplacementClass.OTHER,
                conditions={'object_ref':{'eq':'OLD0'},'destination':{'eq':'graveyard'}},operations=(SetField('destination',destination),)) for destination in ('exile','library')))
        return ZoneChangeReplacementSnapshot(0,0,tuple('ABCD'),tuple('NEW'+str(i) for i in range(parents))+('OLD0',),subjects,effects,devour_candidates=candidates)

    def test_devoured_counter_source_still_modifies_the_proposed_entry_before_the_combined_event(self):
        from quorune.semantic_runtime.zone_replacements import prepare_zone_change_replacement_snapshot
        from quorune.entry_copy import serialize_prepared_copy_entry,restore_prepared_copy_entry
        snapshot=self.snapshot()
        prepared=prepare_zone_change_replacement_snapshot(snapshot,selections=({'effect_id':'devour0','objects':['OLD0']},))['new0']
        self.assertEqual(4,prepared.counter_events[0].payload['amount'])
        sacrifice,=prepared.devour_sacrifices
        self.assertEqual(('old0','battlefield','graveyard'),(sacrifice.object_id,sacrifice.origin,sacrifice.destination))
        encoded=serialize_prepared_copy_entry(prepared)
        restored=restore_prepared_copy_entry(json.loads(json.dumps(encoded)),revision=0)
        self.assertEqual(encoded,serialize_prepared_copy_entry(restored))
        with self.assertRaises(ValueError):replace(prepared,devour_sacrifices=())
        changed=replace(sacrifice,logical_object_id='not-the-selected-incarnation')
        with self.assertRaises(ValueError):replace(prepared,devour_sacrifices=(changed,))

    def test_sacrifice_destination_choice_preserves_the_already_proposed_devour_count_and_counter_result(self):
        from quorune.semantic_runtime.zone_replacements import prepare_zone_change_replacement_snapshot
        from quorune.replacement import ReplacementChoiceRequired
        snapshot=self.snapshot(redirect=True);first={'effect_id':'devour0','objects':['OLD0']}
        with self.assertRaises(ReplacementChoiceRequired) as raised:prepare_zone_change_replacement_snapshot(snapshot,selections=(first,))
        self.assertEqual('OLD0',raised.exception.pending.choice.event.payload['object_ref'])
        prepared=prepare_zone_change_replacement_snapshot(snapshot,selections=(first,'sac-exile'))['new0']
        self.assertEqual(4,prepared.counter_events[0].payload['amount']);self.assertEqual('exile',prepared.devour_sacrifices[0].destination)

    def test_two_simultaneous_devourers_cannot_share_one_sacrifice_and_use_one_before_event_source_view(self):
        from quorune.semantic_runtime.zone_replacements import prepare_zone_change_replacement_snapshot
        snapshot=self.snapshot(2)
        first_event='zone.change:0:1:NEW0'
        with self.assertRaises(ValueError):prepare_zone_change_replacement_snapshot(snapshot,selections=(
            {'effect_id':'devour0','event_id':first_event,'objects':['OLD0']},{'effect_id':'devour1','objects':['OLD0']}))
        result=prepare_zone_change_replacement_snapshot(snapshot,selections=(
            {'effect_id':'devour0','event_id':first_event,'objects':['OLD0']},{'effect_id':'devour1','objects':['OLD1']}))
        self.assertEqual({'new0','new1'},set(result))
        self.assertEqual([4,4],[result[key].counter_events[0].payload['amount'] for key in ('new0','new1')])
        self.assertEqual(['old0','old1'],[result[key].devour_sacrifices[0].object_id for key in ('new0','new1')])

    def test_ordinary_prepared_entry_serialization_omits_the_new_optional_sacrifice_field(self):
        from quorune.semantic_runtime.zone_replacements import prepare_zone_change_replacement_snapshot
        from quorune.entry_copy import serialize_prepared_copy_entry,restore_prepared_copy_entry
        snapshot=self.snapshot();result=prepare_zone_change_replacement_snapshot(snapshot,selections=('decline:devour0',))['new0']
        encoded=serialize_prepared_copy_entry(result)
        self.assertNotIn('devour_sacrifices',encoded)
        self.assertEqual(encoded,serialize_prepared_copy_entry(restore_prepared_copy_entry(json.loads(json.dumps(encoded)),revision=0)))


import test_fixed_reveal_additional_costs as reveal
import test_fading_rules as fading


class DevourRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=reveal.FixedRevealCostRuntimeTests.session
    add=reveal.FixedRevealCostRuntimeTests.add
    fund=reveal.FixedRevealCostRuntimeTests.fund
    checkpoint=reveal.FixedRevealCostRuntimeTests.checkpoint
    resolve=reveal.FixedRevealCostRuntimeTests.resolve
    replay=reveal.FixedRevealCostRuntimeTests.replay
    reject=reveal.FixedRevealCostRuntimeTests.reject
    upkeep=fading.FadingRuntimeTests.upkeep

    def cast_to_devour(self,session,source):
        self.fund(session,{'W':10,'U':10,'B':10,'R':10,'G':10,'C':20})
        result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        self.assertIsNotNone(self.resolve(session))
        self.assertEqual('replacement.order',session.state.pending_decision.kind)

    def devour_legal_refs(self,session):
        ctx=session.packet('pilot:A',full=True)['decision']['ctx']
        option=next(row for row in ctx['options'] if 'replacement.zone.devour-entry.v1:' in row['id'] and not row['decline'])
        return ctx['object_choices'][option['id']]['legal_refs']

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temp=tempfile.TemporaryDirectory();p=Path(cls.temp.name)/'devour.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/devour-cards.json'],p)
        cls.db=CardDatabase(p);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Devour witness',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def choose_devour(self,session,refs,actor='A'):
        decision=session.state.pending_decision;self.assertIsNotNone(decision);self.assertEqual('replacement.order',decision.kind)
        ctx=session.packet('pilot:'+actor,full=True)['decision']['ctx']
        option=next(row for row in ctx['options'] if 'replacement.zone.devour-entry.v1:' in row['id'] and not row['decline'])
        result=session.act('pilot:'+actor,{'action_id':'choose','replacement':option['id'],'replacement_objects':refs})
        self.assertTrue(result.ok,result.summary)
        return result

    def test_actual_slime_chooses_public_sacrifices_before_one_entry_with_private_loaded_replay(self):
        session=self.session(702820);source=self.add(session.engine,'Gluttonous Slime',zone='hand',ref='actual-slime')
        body=self.add(session.engine,'Generic Bound Body',ref='actual-devourable')
        private=self.add(session.engine,'Generic Bound Growth',zone='hand',ref='unselected-private-hand')
        self.fund(session,{'G':1,'C':2})
        for viewer in 'BCD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'pay':'auto'});self.assertTrue(result.ok,result.summary)
        decision=self.resolve(session);self.assertIsNotNone(decision);self.assertEqual('replacement.order',decision.kind)
        self.assertEqual(('stack','battlefield'),(session.state.cards[source.object_id].zone,session.state.cards[body.object_id].zone))
        for viewer in 'BCD':self.assertIsNone(session.packet('pilot:'+viewer,full=True)['decision'])
        session=self.replay(session,load=True)
        option=session.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
        self.reject(session,'B',action_id='choose',replacement=option,replacement_objects=[body.ref])
        self.choose_devour(session,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(('battlefield','graveyard'),(session.state.cards[source.object_id].zone,session.state.cards[body.object_id].zone))
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.replay(session,load=True)

    def test_all_original_devour_cards_have_complete_capability_bindings(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        names=('Caprichrome','Feaster of Fools','Gluttonous Slime','Thorn-Thrash Viashino','Caldera Hellion',
            'Thunder-Thrash Elder','Feasting Hobbit','Mycoloth','Predator Dragon','Gorger Wurm','Thromok the Insatiable')
        for name in names:
            with self.subTest(name=name):
                card=self.db.lookup(name)
                program=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry,capability_profile='commander_review')
                binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertTrue(binding['strict_capability_ready'],binding)

    def test_original_fixed_and_square_devour_counts_keep_intrinsic_bodies(self):
        for index,(name,amount,keywords) in enumerate((('Gluttonous Slime',2,set()),('Thunder-Thrash Elder',6,set()),
                ('Gorger Wurm',2,set()),('Predator Dragon',4,{'flying','haste'}),('Thromok the Insatiable',4,set()))):
            with self.subTest(name=name):
                session=self.session(702830+index);source=self.add(session.engine,name,zone='hand',ref='count-source')
                bodies=[self.add(session.engine,'Generic Bound Body',ref='count-body-'+str(i)) for i in range(2)]
                self.cast_to_devour(session,source);self.choose_devour(session,[b.ref for b in bodies])
                self.assertIsNone(self.resolve(session));card=session.state.cards[source.object_id]
                self.assertEqual('battlefield',card.zone);self.assertEqual(amount,card.counters.get('+1/+1',0))
                printed=self.db.lookup(name)
                self.assertEqual((int(printed.power)+amount,int(printed.toughness)+amount),
                    tuple(session.engine._numeric_stat(card.object_id,k) for k in ('power','toughness')))
                self.assertTrue(keywords.issubset(session.engine._combat_keywords(card)))
                self.assertTrue(all(session.state.cards[b.object_id].zone=='graveyard' for b in bodies))
                self.replay(session)

    def test_artifact_and_food_variants_select_only_controlled_quality_permanents(self):
        for index,(name,amount,expected) in enumerate((('Caprichrome',2,{'food','food-creature','artifact'}),
                ('Feasting Hobbit',6,{'food','food-creature'}))):
            with self.subTest(name=name):
                session=self.session(702840+index);source=self.add(session.engine,name,zone='hand',ref='quality-source')
                food=self.add(session.engine,'Generic Devour Food',ref='food')
                creature=self.add(session.engine,'Gingerbrute',ref='food-creature')
                artifact=self.add(session.engine,'Caprichrome',ref='artifact')
                body=self.add(session.engine,'Generic Bound Body',ref='not-artifact')
                enemy=self.add(session.engine,'Generic Devour Food',seat='B',ref='enemy-food')
                # Ownership is independent from the controller who may sacrifice.
                creature.owner='B'
                self.cast_to_devour(session,source)
                offered=self.devour_legal_refs(session)
                self.assertEqual(expected,set(offered))
                for refs in ([enemy.ref],[body.ref],[source.ref],[food.ref,food.ref],['missing']):
                    self.reject(session,action_id='choose',replacement=next(o['id'] for o in session.packet('pilot:A',full=True)['decision']['ctx']['options'] if not o['decline']),replacement_objects=refs)
                self.choose_devour(session,[food.ref,creature.ref]);self.assertIsNone(self.resolve(session))
                self.assertEqual(amount,session.state.cards[source.object_id].counters.get('+1/+1',0))
                self.assertEqual('graveyard',session.state.cards[creature.object_id].zone)
                self.assertIn(creature.object_id,session.state.players['B'].zones['graveyard'])
                self.assertEqual('battlefield',session.state.cards[artifact.object_id].zone)
                self.replay(session,load=True)

    def test_zero_devour_is_legal_and_zero_toughness_stabilizes_after_entry(self):
        for index,name in enumerate(('Gluttonous Slime','Thromok the Insatiable')):
            with self.subTest(name=name):
                session=self.session(702850+index);source=self.add(session.engine,name,zone='hand',ref='zero-source')
                body=self.add(session.engine,'Generic Bound Body',ref='zero-not-chosen')
                self.cast_to_devour(session,source);self.choose_devour(session,[]);self.assertIsNone(self.resolve(session))
                self.assertEqual('graveyard' if index else 'battlefield',session.state.cards[source.object_id].zone)
                self.assertEqual('battlefield',session.state.cards[body.object_id].zone)
                self.assertFalse(session.state.cards[source.object_id].counters);self.replay(session)

    def test_consumed_counter_doubler_applies_before_the_combined_entry_and_sacrifice(self):
        session=self.session(702860);source=self.add(session.engine,'Gorger Wurm',zone='hand',ref='doubled-devourer')
        menace=self.add(session.engine,'Corpsejack Menace',ref='consumed-doubler')
        body=self.add(session.engine,'Generic Bound Body',ref='second-consumed-body')
        self.cast_to_devour(session,source);self.choose_devour(session,[menace.ref,body.ref])
        self.assertIsNone(self.resolve(session));self.assertEqual(4,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual('graveyard',session.state.cards[menace.object_id].zone)
        self.replay(session,load=True)

    def test_existing_ability_removal_suppresses_prospective_devour(self):
        session=self.session(702861);self.add(session.engine,'Humility',ref='devour-humility')
        source=self.add(session.engine,'Gluttonous Slime',zone='hand',ref='humble-devourer')
        body=self.add(session.engine,'Generic Bound Body',ref='humble-body')
        self.fund(session,{'G':1,'C':2})
        result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertFalse(session.state.cards[source.object_id].counters)
        self.assertEqual('battlefield',session.state.cards[body.object_id].zone);self.replay(session)

    def test_multiple_and_intrinsic_entry_counter_sources_receive_one_scales_increment(self):
        for index,name in enumerate(('Generic Multiple Devour','Generic Devour Counter Entry')):
            with self.subTest(name=name):
                session=self.session(702862+index);source=self.add(session.engine,name,zone='hand',ref='multi-devourer')
                bodies=[self.add(session.engine,'Generic Bound Body',ref='multi-body-'+str(i)) for i in range(2)]
                self.add(session.engine,'Hardened Scales',ref='one-entry-scales')
                self.cast_to_devour(session,source);self.choose_devour(session,[bodies[0].ref])
                if index==0:
                    self.assertIsNotNone(self.resolve(session));self.choose_devour(session,[bodies[1].ref])
                self.assertIsNone(self.resolve(session));self.assertEqual(4,session.state.cards[source.object_id].counters.get('+1/+1',0))
                self.replay(session,load=True)

    def test_simultaneous_devourers_reserve_disjoint_existing_objects_with_saved_replay(self):
        from quorune.replacement import ReplacementChoiceRequired,replacement_choice_payload
        from quorune.semantic_runtime.zone_replacements import prepare_zone_change_replacement_batch
        from quorune.zone_transitions import ZoneTransitionOwner
        session=self.session(702870)
        first=self.add(session.engine,'Gluttonous Slime',zone='graveyard',ref='return-slime')
        second=self.add(session.engine,'Gorger Wurm',zone='graveyard',ref='return-wurm')
        bodies=[self.add(session.engine,'Generic Bound Body',ref='return-food-'+str(i)) for i in range(2)]
        changes=((first.object_id,'battlefield'),(second.object_id,'battlefield'))
        with self.assertRaises(ReplacementChoiceRequired) as raised:prepare_zone_change_replacement_batch(session.engine,changes)
        initial=raised.exception;payload=replacement_choice_payload(initial.pending,initial.effects)
        option=next(o['id'] for o in payload['options'] if not o['decline'])
        self.assertEqual({b.ref for b in bodies},set(payload['object_choices'][option]['legal_refs']))
        selections=[{'effect_id':option,'event_id':initial.pending.event_id,'objects':[bodies[0].ref]}]
        with self.assertRaises(ReplacementChoiceRequired) as raised:prepare_zone_change_replacement_batch(session.engine,changes,selections=selections)
        pending=raised.exception;payload=replacement_choice_payload(pending.pending,pending.effects)
        option=next(o['id'] for o in payload['options'] if not o['decline'])
        self.assertEqual([bodies[1].ref],payload['object_choices'][option]['legal_refs'])
        with self.assertRaises(ValueError):prepare_zone_change_replacement_batch(session.engine,changes,selections=(*selections,{'effect_id':option,'objects':[bodies[0].ref]}))
        selections.append({'effect_id':option,'objects':[bodies[1].ref]})
        # The containing batch journals selections with optional objects intact.
        restored=ReplacementEventBatch.from_dict(json.loads(json.dumps(pending.batch.to_dict())))
        self.assertEqual(pending.batch.to_dict(),restored.to_dict())
        with session.engine.transaction():ZoneTransitionOwner(session.engine).move_cards_simultaneously(changes,replacement_selections=selections,reason="Combined Devour witness")
        for source in (first,second):
            self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
            self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertTrue(all(session.state.cards[b.object_id].zone=='graveyard' for b in bodies))

    def test_copied_token_entry_offers_devour_before_allocating_any_token_or_sacrifice(self):
        session=self.session(702876);spell=self.add(session.engine,'Cackling Counterpart',zone='hand',ref='copy-devour-token')
        original=self.add(session.engine,'Gorger Wurm',ref='token-devour-original')
        body=self.add(session.engine,'Generic Bound Body',ref='token-devours-body')
        self.fund(session,{'U':2,'C':1})
        accepted=session.act('pilot:A',{'action_id':'cast:'+spell.ref,'targets':[original.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        decision=self.resolve(session);self.assertIsNotNone(decision);self.assertEqual('replacement.order',decision.kind)
        self.assertEqual('battlefield',session.state.cards[body.object_id].zone)
        self.assertFalse(any(c.is_token for c in session.state.cards.values()))
        session=self.replay(session,load=True);self.choose_devour(session,[body.ref]);self.assertIsNone(self.resolve(session))
        token,=[c for c in session.state.cards.values() if c.is_token]
        self.assertEqual(1,token.counters.get('+1/+1',0));self.assertEqual('graveyard',session.state.cards[body.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[original.object_id].zone);self.replay(session,load=True)

    def test_copied_entry_devour_uses_the_copiers_controller_and_current_sacrifices(self):
        session=self.session(702871);source=self.add(session.engine,'Clone',zone='hand',ref='devour-clone')
        original=self.add(session.engine,'Gorger Wurm',seat='B',ref='copied-devour-original')
        original.counters['+1/+1']=7
        body=self.add(session.engine,'Generic Bound Body',ref='clone-devours')
        self.cast_to_devour(session,source)
        option=next(o['id'] for o in session.packet('pilot:A',full=True)['decision']['ctx']['options'] if original.ref in o['id'] and not o['decline'])
        accepted=session.act('pilot:A',{'action_id':'choose','replacement':option});self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNotNone(self.resolve(session));self.assertEqual([body.ref],self.devour_legal_refs(session))
        session=self.replay(session,load=True);self.choose_devour(session,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual(6,session.engine._numeric_stat(source.object_id,'power'))
        self.assertEqual('battlefield',session.state.cards[original.object_id].zone);self.replay(session,load=True)

    def test_redirected_sacrifices_keep_devour_count_and_do_not_trigger_death(self):
        session=self.session(702872);source=self.add(session.engine,'Gorger Wurm',zone='hand',ref='redirect-devourer')
        self.add(session.engine,'Generic Cleanup Destination Replacement',seat='B',ref='devour-redirect')
        observer=self.add(session.engine,'Generic Devour Death Observer',ref='redirect-death-observer')
        body=self.add(session.engine,'Generic Bound Body',ref='redirect-sacrifice')
        self.cast_to_devour(session,source);hand=len(session.state.players['A'].zones['hand'])
        self.choose_devour(session,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('exile',session.state.cards[body.object_id].zone)
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual(hand,len(session.state.players['A'].zones['hand']))
        self.assertEqual('battlefield',session.state.cards[observer.object_id].zone);self.replay(session,load=True)

    def test_feaster_can_devour_a_creature_already_tapped_to_convoke_it(self):
        session=self.session(702873);source=self.add(session.engine,'Feaster of Fools',zone='hand',ref='convoked-devourer')
        body=self.add(session.engine,'Generic Bound Body',ref='convoke-then-devour')
        self.fund(session,{'B':2,'C':3})
        accepted=session.act('pilot:A',{'action_id':'cast:'+source.ref,'pay':'auto','convoke_cards':[body.ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertTrue(session.state.cards[body.object_id].tapped)
        self.assertIsNotNone(self.resolve(session));self.choose_devour(session,[body.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[body.object_id].zone)
        self.assertEqual(2,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertIn('flying',session.engine._combat_keywords(session.state.cards[source.object_id]));self.replay(session,load=True)

    def test_caldera_damage_and_viashino_paid_trample_execute_after_devour(self):
        for index,name in enumerate(('Caldera Hellion','Thorn-Thrash Viashino')):
            with self.subTest(name=name):
                session=self.session(702874+index);source=self.add(session.engine,name,zone='hand',ref='body-devourer')
                body=self.add(session.engine,'Generic Bound Body',ref='body-consumed')
                enemy=self.add(session.engine,'Generic Bound Body',seat='B',ref='caldera-victim')
                self.cast_to_devour(session,source);self.choose_devour(session,[body.ref]);self.assertIsNone(self.resolve(session))
                card=session.state.cards[source.object_id];self.assertEqual(1 if index==0 else 2,card.counters.get('+1/+1',0))
                if index==0:
                    self.assertEqual('battlefield',card.zone);self.assertEqual(3,card.marked_damage)
                    self.assertEqual(3,session.state.cards[enemy.object_id].marked_damage)
                else:
                    self.fund(session,{'G':1})
                    action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+source.ref+':'))
                    accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
                    self.assertIsNone(self.resolve(session));self.assertIn('trample',session.engine._combat_keywords(session.state.cards[source.object_id]))
                self.replay(session,load=True)

    def test_mycoloth_upkeep_counts_current_counters_instead_of_prior_devour(self):
        from quorune.counter_placement import CounterPlacementRequest,place_counters
        for index,additional in enumerate((0,3)):
            with self.subTest(additional=additional):
                session=self.session(702880+index);source=self.add(session.engine,'Mycoloth',zone='hand',ref='mycoloth-source')
                body=self.add(session.engine,'Generic Bound Body',ref='mycoloth-consumed')
                self.cast_to_devour(session,source);self.choose_devour(session,[body.ref]);self.assertIsNone(self.resolve(session))
                self.assertEqual(2,session.state.cards[source.object_id].counters.get('+1/+1',0))
                if additional:
                    place_counters(session.engine,(CounterPlacementRequest(subject_kind='permanent',subject_id=source.object_id,
                        counter_name='+1/+1',amount=additional,placing_player='A'),),reason='Current counter count boundary')
                self.upkeep(session,'B');self.assertFalse(session.state.stack)
                self.upkeep(session,'A');self.assertEqual(1,len(session.state.stack));self.checkpoint(session)
                session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield']
                self.assertEqual(2+additional,len(tokens))
                for token in tokens:
                    data=session.engine._effective_card_data(token)
                    self.assertIn('Saproling',data['type_line']);self.assertEqual('A',token.controller)
                    self.assertEqual((1,1),tuple(session.engine._numeric_stat(token.object_id,k) for k in ('power','toughness')))
                self.replay(session,load=True)

    def test_hobbit_blocking_uses_current_power_after_devour_and_later_counter_changes(self):
        from quorune.model import CombatState
        for index,with_counters in enumerate((True,False)):
            with self.subTest(with_counters=with_counters):
                session=self.session(702882+index);source=self.add(session.engine,'Feasting Hobbit',zone='hand',ref='hobbit-attacker')
                food=self.add(session.engine,'Generic Devour Food',ref='hobbit-food')
                blocker=self.add(session.engine,'Generic Bound Body',seat='B',ref='hobbit-blocker')
                self.add(session.engine,'Gorger Wurm',seat='B',ref='equal-power-hobbit-blocker')
                self.cast_to_devour(session,source);self.choose_devour(session,[food.ref]);self.assertIsNone(self.resolve(session))
                current=session.state.cards[source.object_id];self.assertEqual(5,session.engine._numeric_stat(current.object_id,'power'))
                if not with_counters:current.counters.clear()
                current.acquired_control_turn_count=-1
                engine=session.engine;engine.permissions.invalidate_current();engine.state.pending_decision=None
                engine.state.started=True;engine.state.active_player='A';engine.state.phase='combat';engine.state.step='declare_attackers'
                engine.state.phase_index=5;engine.state.combat=CombatState();engine._issue_attackers();self.checkpoint(session)
                accepted=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}});self.assertTrue(accepted.ok,accepted.summary)
                for _ in range(24):
                    if session.state.pending_decision.kind=='combat.blockers':break
                    self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                else:self.fail('Hobbit blocker declaration did not arrive')
                response={'action_id':'block','blocks':{blocker.ref:source.ref}}
                if with_counters:
                    self.reject(session,'B',**response);response={'action_id':'block','blocks':{}}
                accepted=session.act('pilot:B',response);self.assertTrue(accepted.ok,accepted.summary)
                self.replay(session,load=True)

    def test_consumed_death_observer_sees_simultaneous_sacrifices_and_entry_observer_sees_counters(self):
        session=self.session(702884);source=self.add(session.engine,'Gorger Wurm',zone='hand',ref='observed-devourer')
        observer=self.add(session.engine,'Generic Devour Death Observer',ref='consumed-death-observer')
        body=self.add(session.engine,'Generic Bound Body',ref='observed-sacrifice')
        self.add(session.engine,'Generic Devour Entry Observer',ref='entry-counter-observer')
        private=self.add(session.engine,'Generic Bound Growth',zone='hand',ref='observer-private-hand')
        self.cast_to_devour(session,source);hand=len(session.state.players['A'].zones['hand'])
        self.choose_devour(session,[observer.ref,body.ref]);decision=self.resolve(session)
        self.assertEqual('trigger.order',decision.kind)
        ctx=session.packet('pilot:A',full=True)['decision']['ctx']
        accepted=session.act('pilot:A',{'action_id':'order','order':[row['id'] for row in ctx['triggers']]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual(hand+2,len(session.state.players['A'].zones['hand']))
        self.assertEqual(2,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertEqual('graveyard',session.state.cards[observer.object_id].zone)
        for viewer in 'BCD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.replay(session,load=True)

    def test_devour_dependency_and_actual_runtime_omission_mutants_are_killed(self):
        from unittest.mock import patch
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from test_granted_skulk import record
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for dependency in ('zone.entry.devour','counter.placement.quantity_replacement','zone.change.destination_replacement','trigger.event.normalized_zone_change','trigger.placement.apnap'):
            changed=json.loads(json.dumps(raw));next(r for r in changed['capabilities'] if r['id']==dependency).update(status='blocked',blockers=['Devour dependency omitted'])
            self.assertNotEqual('exact',compile_oracle_card(record('Devour 2','Creature'),capability_registry=CapabilityRegistry(changed)).status)
        with patch('quorune.compiler.devour_nodes.devour_keyword_nodes',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record('Devour 2','Creature'),capability_registry=self.registry).status)
        method='test_actual_slime_chooses_public_sacrifices_before_one_entry_with_private_loaded_replay'
        with patch('quorune.semantic_runtime.zone_replacements.devour_replacement_effects',return_value=()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_multiple_devour_instances_share_one_counter_occurrence_with_one_additional_counter'
        with patch('quorune.replacement.devour.combine_devour_entry_counters',return_value=None):
            with self.assertRaises(AssertionError):getattr(DevourReplacementModelTests(method),method)()
        method='test_copied_token_entry_offers_devour_before_allocating_any_token_or_sacrifice'
        with patch('quorune.zone_devour.commit_token_devour_sacrifices',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_token_devour_keeps_before_event_death_sources_and_consumed_quantity_sources(self):
        session=self.session(702890);spell=self.add(session.engine,'Cackling Counterpart',zone='hand',ref='timed-token-copy')
        original=self.add(session.engine,'Generic Devour Death Creature',ref='copied-death-devourer')
        observer=self.add(session.engine,'Generic Devour Death Observer',ref='old-death-observer')
        doubler=self.add(session.engine,'Corpsejack Menace',ref='token-consumed-doubler')
        self.fund(session,{'U':2,'C':1})
        accepted=session.act('pilot:A',{'action_id':'cast:'+spell.ref,'targets':[original.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(self.resolve(session))
        hand=len(session.state.players['A'].zones['hand']);session=self.replay(session,load=True)
        self.choose_devour(session,[doubler.ref]);decision=self.resolve(session)
        self.assertEqual('trigger.order',decision.kind)
        ctx=session.packet('pilot:A',full=True)['decision']['ctx']
        self.assertEqual(2,len(ctx['triggers']),'The new copied observer must not see a sacrifice in its entry event')
        accepted=session.act('pilot:A',{'action_id':'order','order':[row['id'] for row in ctx['triggers']]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(session))
        token,=[c for c in session.state.cards.values() if c.is_token]
        self.assertEqual(2,token.counters.get('+1/+1',0));self.assertEqual(hand+2,len(session.state.players['A'].zones['hand']))
        self.assertEqual('battlefield',session.state.cards[observer.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[doubler.object_id].zone);self.replay(session,load=True)

    def test_changed_incarnation_cannot_be_substituted_for_a_pending_devour_selection(self):
        session=self.session(702891);source=self.add(session.engine,'Gluttonous Slime',zone='hand',ref='stale-devourer')
        body=self.add(session.engine,'Generic Bound Body',ref='stale-devour-body')
        self.cast_to_devour(session,source)
        option=next(o['id'] for o in session.packet('pilot:A',full=True)['decision']['ctx']['options'] if not o['decline'])
        # Adversarial stale-authority boundary: this state cannot arise via priority during entry.
        session.engine.move_card(body.object_id,'exile',log=False)
        session.engine.move_card(body.object_id,'battlefield',controller='A',log=False)
        self.reject(session,action_id='choose',replacement=option,replacement_objects=[body.ref])

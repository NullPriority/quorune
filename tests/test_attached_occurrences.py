from __future__ import annotations

"""Pinned attached-object occurrences preserve their source and incarnation."""

from pathlib import Path
import tempfile
import unittest
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_external_entry_counters as controls
import test_classic_flash_casts as stack_controls
from quorune.rules.evidence_storage import read_evidence_document


class AttachedOccurrenceTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ExternalEntryCounterTests.session
    add=controls.ExternalEntryCounterTests.add
    prepare=controls.ExternalEntryCounterTests.prepare
    checkpoint=controls.ExternalEntryCounterTests.checkpoint
    resolve=controls.ExternalEntryCounterTests.resolve
    replay=controls.ExternalEntryCounterTests.replay
    binding=controls.ExternalEntryCounterTests.binding
    priority=controls.ExternalEntryCounterTests.priority
    cast=controls.ExternalEntryCounterTests.cast
    offered_cast=controls.ExternalEntryCounterTests.offered_cast
    private_replay=controls.ExternalEntryCounterTests.private_replay
    advance=controls.ExternalEntryCounterTests.advance
    offered=controls.ExternalEntryCounterTests.offered

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'attached-occurrences.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/attached-occurrence-cards.json',ROOT/'tests/fixtures/entry-copy-cards.json',
            ROOT/'tests/fixtures/classic-flash-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Attached occurrences',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_full_blight_and_brink_register_complete_originals_before_native_actions(self):
        session=self.session(751800)
        for name in ('Blight','Brink of Disaster'):
            source=self.add(session.engine,name,zone='hand')
            self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original '+name+' must admit')

    def test_full_harvest_shackle_putrescence_and_pooling_register_complete_originals(self):
        session=self.session(751801)
        for name in ('Viridian Harvest','Spirit Shackle','Relic Putrescence','Pooling Venom'):
            source=self.add(session.engine,name,zone='hand')
            self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original '+name+' must admit')

    def test_tap_counter_result_seals_the_occurrence_incarnation_and_rejects_missing_identity(self):
        from types import SimpleNamespace
        from quorune.event_counter_reference import event_counter_recipient_context,resolve_event_counter_recipient
        program=SimpleNamespace(effects=({'op':'place_counters','card':'$event.counter_object'},))
        context={'card':'tapped-body','card_object_identity':'body@4'}
        sealed=event_counter_recipient_context(program,'permanent.tap',context)
        self.assertEqual({'event_counter_recipient':{'reference':'tapped-body','logical_object_id':'body@4'}},sealed)
        card=SimpleNamespace(ref='tapped-body',logical_object_id='body@4',zone='battlefield',phased_out=False)
        host=SimpleNamespace(state=SimpleNamespace(cards={'physical':card}));item=SimpleNamespace(context=sealed)
        self.assertEqual(card.ref,resolve_event_counter_recipient(host,item))
        card.logical_object_id='body@6';self.assertIsNone(resolve_event_counter_recipient(host,item))
        with self.assertRaises(ValueError):event_counter_recipient_context(program,'permanent.tap',{'card':'tapped-body'})

    def test_signed_power_toughness_counters_use_both_dimensions_including_zero(self):
        from types import SimpleNamespace
        from quorune.object_query import exact_numeric_characteristic
        card=SimpleNamespace(annotations={},counters={'-0/-2':1,'+1/+1':2,'+2/+0':1,'oil':9})
        self.assertEqual(6,exact_numeric_characteristic(card,{'power':'2','toughness':'2'},'power'))
        self.assertEqual(2,exact_numeric_characteristic(card,{'power':'2','toughness':'2'},'toughness'))
        card.counters={'-0/-2':1}
        self.assertEqual(0,exact_numeric_characteristic(card,{'power':'2','toughness':'2'},'toughness'))

    def test_actual_spirit_shackle_tap_trigger_adds_counter_then_state_actions_kill_creature(self):
        session=self.session(751802);source=self.add(session.engine,'Spirit Shackle',zone='hand')
        body=self.add(session.engine,'Grizzly Bears',seat='B');tap=self.add(session.engine,'Generic Bound Next Untap',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Spirit Shackle must admit')
        self.prepare(session,mana={'B':2,'U':1});self.checkpoint(session)
        self.offered_cast(session,source,{'B':2},[body.ref]);self.assertIsNone(self.resolve(session))
        self.offered_cast(session,tap,{'U':1},[body.ref])
        for _ in range(16):
            if session.state.cards[tap.object_id].zone!='stack':break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',session.state.cards[tap.object_id].zone)
        self.assertTrue(session.state.cards[body.object_id].tapped);self.assertFalse(session.state.cards[body.object_id].counters)
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone);self.assertEqual(1,len(session.state.stack))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[body.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.private_replay(session,private)

    def test_actual_harvest_uses_artifact_departure_and_locked_aura_controller(self):
        session=self.session(751803);source=self.add(session.engine,'Viridian Harvest',zone='hand')
        artifact=self.add(session.engine,'Sol Ring',seat='B');other=self.add(session.engine,'Grizzly Bears',seat='B')
        destroy=self.add(session.engine,'Naturalize',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original Harvest must admit')
        self.prepare(session,mana={'G':2,'C':1});self.checkpoint(session)
        self.offered_cast(session,source,{'G':1},[artifact.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(40,session.state.players['A'].life)
        self.offered_cast(session,destroy,{'G':1,'C':1},[artifact.ref])
        for _ in range(16):
            if session.state.cards[destroy.object_id].zone!='stack':break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',session.state.cards[artifact.object_id].zone)
        self.assertEqual(1,len(session.state.stack));self.assertEqual('A',session.state.stack[-1].controller)
        self.assertEqual(40,session.state.players['A'].life)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(46,session.state.players['A'].life);self.assertEqual(40,session.state.players['B'].life)
        self.private_replay(session,private)

    def test_actual_blight_land_mana_action_creates_untargeted_delayed_destruction(self):
        session=self.session(751804);source=self.add(session.engine,'Blight',zone='hand')
        land=self.add(session.engine,'Generic Bound Plains',seat='B',ref='blighted-land')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready']);self.prepare(session,mana={'B':2});self.checkpoint(session)
        self.offered_cast(session,source,{'B':2},[land.ref]);self.assertIsNone(self.resolve(session))
        self.priority(session,'B');actions=[a for a in self.offered(session,'B') if a.get('card')==land.ref or land.ref in a['id']]
        self.assertEqual(1,len(actions),actions);result=session.act('pilot:B',{'action_id':actions[0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertTrue(session.state.cards[land.object_id].tapped);self.assertEqual(1,session.state.players['B'].mana_pool.get('W',0))
        self.assertEqual('battlefield',session.state.cards[land.object_id].zone);self.assertEqual(1,len(session.state.stack))
        self.assertEqual([],session.state.stack[-1].targets)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[land.object_id].zone);self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_actual_brink_creature_tap_destroys_the_triggering_object(self):
        session=self.session(751805);source=self.add(session.engine,'Brink of Disaster',zone='hand')
        body=self.add(session.engine,'Grizzly Bears',seat='B');tap=self.add(session.engine,'Generic Bound Next Untap',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready']);self.prepare(session,mana={'B':2,'C':2,'U':1});self.checkpoint(session)
        self.offered_cast(session,source,{'B':2,'C':2},[body.ref]);self.assertIsNone(self.resolve(session))
        self.offered_cast(session,tap,{'U':1},[body.ref])
        for _ in range(16):
            if session.state.cards[tap.object_id].zone!='stack':break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,len(session.state.stack));self.assertEqual([],session.state.stack[-1].targets)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[body.object_id].zone);self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.private_replay(session,private)

    def test_occurrence_destruction_shape_excludes_targets_other_events_and_siblings(self):
        from types import SimpleNamespace
        from quorune.rules.occurrence_object_results import occurrence_object_runtime_dependencies
        program=SimpleNamespace(event='permanent.tap',target_schema=None,effects=({'op':'destroy','card':'$event.permanent_object'},))
        self.assertEqual({'permanent.destroy.effect','resolution.event_object.fixed_result'},set(occurrence_object_runtime_dependencies(program)))
        for changes in ({'event':'card.drawn'},{'target_schema':{'count':1}},
                {'effects':({'op':'destroy','card':'$event.permanent_object'},{'op':'life','delta':1})}):
            with self.assertRaises(ValueError):occurrence_object_runtime_dependencies(SimpleNamespace(**{**vars(program),**changes}))

    def test_native_occurrence_compiler_and_result_omissions_are_killed(self):
        from unittest.mock import patch
        method='test_actual_blight_land_mana_action_creates_untargeted_delayed_destruction'
        for target,replacement in (('quorune.compiler.occurrence_object_results.occurrence_object_result',lambda *a,**k:None),
                ('quorune.event_counter_reference.resolve_event_counter_recipient',lambda *a:None)):
            with patch(target,replacement):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_actual_pooling_venom_keeps_tap_life_loss_and_paid_attached_destruction(self):
        from quorune.record import authoritative_state_hash
        session=self.session(751806);source=self.add(session.engine,'Pooling Venom',zone='hand')
        land=self.add(session.engine,'Generic Bound Plains',seat='B',ref='venom-land')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Whole Pooling Venom must admit')
        self.prepare(session,mana={'B':2,'C':4});self.checkpoint(session);self.offered_cast(session,source,{'B':1,'C':1},[land.ref])
        self.assertIsNone(self.resolve(session));self.priority(session,'B')
        mana=[a for a in self.offered(session,'B') if a.get('card')==land.ref or land.ref in a['id']];self.assertEqual(1,len(mana))
        result=session.act('pilot:B',{'action_id':mana[0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertEqual(40,session.state.players['B'].life);self.assertIsNone(self.resolve(session));self.assertEqual(38,session.state.players['B'].life)
        self.priority(session,'A');actions=[a for a in self.offered(session,'A') if a['id'].startswith('activate:'+source.ref+':')]
        self.assertEqual(1,len(actions));command={'action_id':actions[0]['id'],'pay':'manual','payment':{'B':1,'C':3}}
        before=authoritative_state_hash(session.state);self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[land.object_id].zone);self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual(40,session.state.players['A'].life);self.private_replay(session,private)

    def test_occurrence_controller_reads_current_information_then_exact_departure_lki(self):
        from types import SimpleNamespace
        from quorune.target_controller_information import resolve_occurrence_controller_information,pin_occurrence_controller_departures
        value={'kind':'occurrence_controller_information','schema_version':1}
        card=SimpleNamespace(object_id='physical',logical_object_id='physical@2',zone_change_counter=2,controller='C',zone='battlefield',phased_out=False)
        item=SimpleNamespace(context={'card':'ref','card_object_id':'physical','card_object_identity':'physical@2'},semantic_key='ability')
        program=SimpleNamespace(effects=({'op':'place_player_counters','target':value},))
        class Host:
            state=SimpleNamespace(cards={'physical':card},stack=[item])
            semantics=SimpleNamespace(get=lambda key:program)
            def _effective_card_data(self,obj):return {'controller':obj.controller}
        host=Host();self.assertEqual('C',resolve_occurrence_controller_information(host,value,item))
        pin_occurrence_controller_departures(host,(card,))
        self.assertIn('occurrence_controller_information_lki',item.context)
        card.zone='hand';card.zone_change_counter=3;card.logical_object_id='physical@3';card.controller='B'
        self.assertEqual('C',resolve_occurrence_controller_information(host,value,item))
        with self.assertRaises(ValueError):resolve_occurrence_controller_information(host,{**value,'schema_version':True},item)
        item.context['occurrence_controller_information_lki']['information']['zone_change_counter']=99
        with self.assertRaises(ValueError):resolve_occurrence_controller_information(host,value,item)

    def test_actual_putrescence_reads_current_controller_and_departure_lki_after_control_change(self):
        for index,depart in enumerate((False,True)):
            session=self.session(751807+index);source=self.add(session.engine,'Relic Putrescence',zone='hand')
            artifact=self.add(session.engine,'Sol Ring',seat='B')
            destroy=self.add(session.engine,'Naturalize',zone='hand');private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Whole Relic Putrescence must admit')
            self.prepare(session,mana={'B':1,'C':3,'G':1});self.checkpoint(session)
            self.offered_cast(session,source,{'B':1,'C':2},[artifact.ref]);self.assertIsNone(self.resolve(session))
            self.priority(session,'B');mana=[a for a in self.offered(session,'B') if a.get('card')==artifact.ref or artifact.ref in a['id']]
            self.assertEqual(1,len(mana));result=session.act('pilot:B',{'action_id':mana[0]['id']});self.assertTrue(result.ok,result.summary)
            self.assertEqual(1,len(session.state.stack));self.assertEqual('A',session.state.stack[-1].controller)
            self.assertEqual([],session.state.stack[-1].targets)
            session.engine.change_control(artifact.object_id,'D')
            self.checkpoint(session)
            self.assertEqual('D',session.state.cards[artifact.object_id].controller)
            if depart:
                self.offered_cast(session,destroy,{'G':1,'C':1},[artifact.ref])
                self.assertIsNone(stack_controls.ClassicFlashCastTests.resolve_to_depth(self,session,1))
                self.assertEqual('graveyard',session.state.cards[artifact.object_id].zone)
            self.assertEqual([0,0,0,0],[session.state.players[s].poison for s in 'ABCD'])
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual([0,0,0,1],[session.state.players[s].poison for s in 'ABCD']);self.private_replay(session,private)

    def test_queued_putrescence_occurrence_retains_departure_controller_before_stack_placement(self):
        from quorune.tap_state import set_permanent_tapped
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.target_controller_information import resolve_occurrence_controller_information
        from types import SimpleNamespace
        session=self.session(751809);source=self.add(session.engine,'Relic Putrescence',zone='hand')
        artifact=self.add(session.engine,'Sol Ring',seat='B');self.prepare(session,mana={'B':1,'C':2});self.checkpoint(session)
        self.offered_cast(session,source,{'B':1,'C':2},[artifact.ref]);self.assertIsNone(self.resolve(session))
        with session.engine.transaction():
            set_permanent_tapped(session.engine,artifact.ref,actor='B',tapped=True,reason='Queued tap occurrence witness')
            self.assertEqual(1,len(session.state.pending_trigger_batches))
            session.engine.change_control(artifact.object_id,'D')
            ZoneTransitionOwner(session.engine).move_cards_simultaneously(((artifact.object_id,'graveyard'),),reason='Departure before trigger placement witness')
        items=session.state.pending_trigger_batches[0].items;self.assertEqual(1,len(items))
        item=SimpleNamespace(context=items[0].to_dict()['context'])
        self.assertEqual('D',resolve_occurrence_controller_information(session.engine,{'kind':'occurrence_controller_information','schema_version':1},item))
        self.checkpoint(session);self.replay(session,load=True)

    def test_brink_trigger_survives_aura_departure_but_does_not_destroy_blinked_incarnation(self):
        for index,blink in enumerate((False,True)):
            session=self.session(751810+index);source=self.add(session.engine,'Brink of Disaster',zone='hand')
            body=self.add(session.engine,'Grizzly Bears');tap=self.add(session.engine,'Generic Bound Next Untap',zone='hand')
            response=self.add(session.engine,'Cloudshift' if blink else 'Naturalize',zone='hand')
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session,mana={'B':2,'C':3,'U':1,'W':1,'G':1});self.checkpoint(session)
            self.offered_cast(session,source,{'B':2,'C':2},[body.ref]);self.assertIsNone(self.resolve(session))
            self.offered_cast(session,tap,{'U':1},[body.ref])
            for _ in range(16):
                if session.state.cards[tap.object_id].zone!='stack':break
                result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
            self.assertEqual(1,len(session.state.stack));old=session.state.cards[body.object_id].logical_object_id
            self.offered_cast(session,response,{'W':1} if blink else {'G':1,'C':1},[body.ref if blink else source.ref])
            self.assertIsNone(stack_controls.ClassicFlashCastTests.resolve_to_depth(self,session,1))
            self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
            if blink:self.assertNotEqual(old,session.state.cards[body.object_id].logical_object_id)
            session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
            self.assertEqual('battlefield' if blink else 'graveyard',session.state.cards[body.object_id].zone);self.private_replay(session,private)

    def test_harvest_simultaneous_aura_and_artifact_departure_uses_before_event_attachment(self):
        from quorune.zone_transitions import ZoneTransitionOwner
        session=self.session(751812);source=self.add(session.engine,'Viridian Harvest',zone='hand')
        artifact=self.add(session.engine,'Sol Ring',seat='B');other=self.add(session.engine,'Grizzly Bears',seat='B')
        self.prepare(session,mana={'G':1});self.checkpoint(session);self.offered_cast(session,source,{'G':1},[artifact.ref]);self.assertIsNone(self.resolve(session))
        with session.engine.transaction():
            ZoneTransitionOwner(session.engine).move_cards_simultaneously(((artifact.object_id,'graveyard'),(source.object_id,'graveyard'),
                (other.object_id,'graveyard')),reason='Simultaneous original Harvest witness')
        batches=session.state.pending_trigger_batches;self.assertEqual(1,len(batches));self.assertEqual(1,len(batches[0].items))
        item=batches[0].items[0].to_dict();self.assertEqual('A',item['controller']);self.assertEqual(artifact.ref,item['context']['card'])
        self.assertIn(source.ref,item['context']['attachments']);self.checkpoint(session)
        session=self.replay(session,load=True);session.engine.permissions.invalidate_current();session.state.pending_decision=None;session.engine.pump()
        for _ in range(12):
            if not session.state.pending_trigger_batches:break
            decision=session.state.pending_decision;self.assertIsNotNone(decision)
            if decision.kind=='trigger.order':
                ctx=session.packet(session.pending_principals()[0],full=True)['decision']['ctx']
                command={'action_id':'order','order':[row['id'] for row in ctx['triggers']]}
            else:
                self.assertEqual('priority',decision.kind);command={'action_id':'pass'}
            result=session.act(session.pending_principals()[0],command);self.assertTrue(result.ok,result.summary)
        self.assertFalse(session.state.pending_trigger_batches)
        self.assertIsNone(self.resolve(session));self.assertEqual(46,session.state.players['A'].life)

    def test_occurrence_and_attached_result_runtime_minima_fail_closed_independently(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for name,operation,requirements in (('Blight','destroy',('resolution.event_object.fixed_result','permanent.destroy.effect')),
                ('Relic Putrescence','place_player_counters',('resolution.event_object.fixed_result','resolution.target_controller_information','counter.producer.fixed_player_effect')),
                ('Pooling Venom','destroy',('permanent.destroy.effect','attachment.reference.current_or_lki'))):
            session=self.session(751813);source=self.add(session.engine,name,zone='hand')
            programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if any(e.get('op')==operation for e in p.effects)]
            self.assertEqual(1,len(programs));program=programs[0]
            self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
            for cap in requirements:
                self.assertIn(cap,program.capability_dependencies);deps=tuple(c for c in program.capability_dependencies if c!=cap)
                stripped=replace(program,capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
                binding=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(binding['strict'],(name,cap));self.assertIn('capability:undeclared_runtime_dependency:'+cap,binding['blockers'])

    def test_verified_occurrence_and_adjacent_owner_withdrawals_keep_originals_residual(self):
        import json
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        for cap,names in (('resolution.event_object.fixed_result',('Blight','Brink of Disaster','Relic Putrescence')),
                ('permanent.destroy.effect',('Blight','Brink of Disaster','Pooling Venom')),
                ('attachment.reference.current_or_lki',('Viridian Harvest','Pooling Venom')),
                ('counter.producer.fixed_event_object',('Spirit Shackle',)),
                ('resolution.target_controller_information',('Relic Putrescence',))):
            changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn attached occurrence owner'])
            registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=decl,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in names:
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status,name);blockers=[b for r in ir.material_residuals for b in r.blockers]
                self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),(name,blockers))
                self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_closed_attachment_event_grammar_and_controller_value_reject_near_misses(self):
        from quorune.compiler.attached_destruction import attached_destruction_template
        from quorune.attachment_references import AttachmentReferenceKind
        from quorune.compiler.fixed_public_event_trigger_bindings import _attachment_death_spec
        for text in ('Destroy enchanted land and draw a card.','Destroy enchanted nonbasic land.','Destroy equipped land.'):
            self.assertIsNone(attached_destruction_template(text,source_attachment_relation=AttachmentReferenceKind.ENCHANTED))
        for text in ('When enchanted artifact is put into a graveyard from anywhere, you gain 6 life.',
                'When any enchanted artifact is put into a graveyard, you gain 6 life.'):
            self.assertIsNone(_attachment_death_spec(text))
        self.assertIsNotNone(_attachment_death_spec('When enchanted artifact is put into a graveyard, you gain 6 life.'))
        from types import SimpleNamespace
        from quorune.rules.occurrence_object_results import occurrence_object_runtime_dependencies
        effect={'op':'place_player_counters','subjects':'target','counter':'poison','amount':1,'source':'$source',
            'target':{'kind':'occurrence_controller_information','schema_version':1}}
        for changed in ({**effect,'amount':True},{**effect,'counter':'energy'},
                {**effect,'target':{'kind':'occurrence_controller_information','schema_version':True}}):
            with self.assertRaises(ValueError):occurrence_object_runtime_dependencies(SimpleNamespace(effects=(changed,),event='permanent.tap',target_schema=None))

    def test_signed_counter_lki_and_attached_destruction_omissions_are_killed(self):
        from unittest.mock import patch
        cases=(('quorune.counter_names.power_toughness_counter_modifier',lambda *a:0,
                'test_signed_power_toughness_counters_use_both_dimensions_including_zero'),
            ('quorune.target_controller_information.pin_occurrence_controller_departures',lambda *a:None,
                'test_occurrence_controller_reads_current_information_then_exact_departure_lki'),
            ('quorune.compiler.attached_destruction.attached_destruction_template',lambda *a,**k:None,
                'test_actual_pooling_venom_keeps_tap_life_loss_and_paid_attached_destruction'))
        for target,replacement,method in cases:
            with patch(target,replacement):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

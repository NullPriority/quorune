from __future__ import annotations

"""CR608.2b/c/n: a printed self-exile instruction happens only on resolution."""

from pathlib import Path
import json
from copy import deepcopy
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_gain_and_get_sets as controls
from quorune.rules.evidence_storage import read_evidence_document


class SelfExileReturnSpellTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.GainAndGetSetTests.session
    add=controls.GainAndGetSetTests.add
    prepare=controls.GainAndGetSetTests.prepare
    checkpoint=controls.GainAndGetSetTests.checkpoint
    resolve=controls.GainAndGetSetTests.resolve
    replay=controls.GainAndGetSetTests.replay
    binding=controls.GainAndGetSetTests.binding
    priority=controls.GainAndGetSetTests.priority
    cast=controls.GainAndGetSetTests.cast
    actions=controls.GainAndGetSetTests.actions
    private_replay=controls.GainAndGetSetTests.private_replay
    top=controls.GainAndGetSetTests.top
    advance=controls.GainAndGetSetTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'self-exile-return.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/attached-protection-cards.json',
            ROOT/'tests/fixtures/self-exile-return-cards.json',ROOT/'tests/fixtures/conditional-color-grant-cards.json',
            ROOT/'tests/fixtures/conditional-exile-cards.json',ROOT/'tests/fixtures/fixed-stack-spell-copy-cards.json',
            ROOT/'tests/fixtures/closed-effect-program-cards.json',ROOT/'tests/fixtures/announced-target-cardinality-cards.json',
            ROOT/'tests/fixtures/undaunted-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Self-exile return',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def original(self,name,seed,count,payment,*,target_kinds=None):
        session=self.session(seed);source=self.add(session.engine,name,zone='hand')
        targets=[self.add(session.engine,kind,zone='graveyard',ref='return-target-'+str(i))
            for i,kind in enumerate(target_kinds if target_kinds is not None else ('Grizzly Bears','Generic Bound Growth')[:count])]
        foreign=self.add(session.engine,'Grizzly Bears',seat='B',zone='graveyard',ref='foreign-return-card')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand',ref='unrevealed-return-control')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'],'Complete original '+name+' must admit')
        self.prepare(session,mana=payment)
        return session,source,targets,foreign,private

    def assert_known_returns(self,session,targets,owner='A'):
        for card in targets:
            self.assertEqual('hand',session.state.cards[card.object_id].zone)
            for seat in (s for s in 'ABCD' if s!=owner):
                player=session.packet('pilot:'+seat,full=True)['state']['players'][owner]
                self.assertNotIn('hand',player)
                self.assertIn(card.ref,{c['id'] for c in player.get('known_hand',())})

    def cast_original(self,name,seed,count,payment,*,x=None,discard=False,target_kinds=None):
        session,source,targets,foreign,private=self.original(name,seed,count,payment,target_kinds=target_kinds)
        prices=[self.add(session.engine,kind,zone='hand',ref='self-exile-price-'+str(i))
            for i,kind in enumerate(('Grizzly Bears','Generic Bound Growth')[:count])] if discard else []
        self.prepare(session,mana=payment);self.checkpoint(session)
        command={'action_id':'cast:'+source.ref,'targets':[c.ref for c in targets],'pay':'manual','payment':payment}
        if x is not None:command['x']=x
        if discard:command['discard_cards']=[c.ref for c in prices]
        self.assertTrue(any(r['id']==command['action_id'] for r in self.actions(session)))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertFalse(session.act('pilot:A',{**command,'targets':[foreign.ref,*[c.ref for c in targets[1:]]]}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        if prices:
            self.assertFalse(session.act('pilot:A',{**command,'targets':[prices[0].ref,*[c.ref for c in targets[1:]]]}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('stack',session.state.cards[source.object_id].zone)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in prices))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in targets))
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        if x is not None:self.assertEqual(x,session.state.stack[-1].x_value)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assert_known_returns(session,targets)
        self.assertEqual('exile',session.state.cards[source.object_id].zone)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in prices))
        self.assertEqual('graveyard',session.state.cards[foreign.object_id].zone)
        self.private_replay(session,private)

    def test_actual_treasured_find_returns_its_card_then_exiles_itself(self):
        self.cast_original('Treasured Find',751100,1,{'B':1,'G':1})

    def test_actual_restock_returns_two_distinct_card_types_then_exiles_itself(self):
        self.cast_original('Restock',751101,2,{'G':2,'C':3})

    def test_actual_wildest_dreams_pays_double_x_for_two_returns_then_self_exile(self):
        self.cast_original('Wildest Dreams',751102,2,{'G':1,'C':4},x=2)

    def test_actual_nostalgic_dreams_pays_hand_price_after_selecting_old_graveyard_targets(self):
        self.cast_original('Nostalgic Dreams',751103,2,{'G':2},x=2,discard=True)

    def test_actual_flood_recollection_returns_an_instant_with_its_printed_restriction(self):
        self.cast_original('Flood of Recollection',751105,1,{'U':2},target_kinds=('Generic Bound Growth',))

    def test_actual_seeds_renewal_uses_existing_undaunted_price_and_returns_two_cards(self):
        self.cast_original('Seeds of Renewal',751106,2,{'G':1,'C':3})

    def test_actual_vivid_revival_returns_three_distinct_multicolored_cards(self):
        self.cast_original('Vivid Revival',751107,3,{'G':1,'C':4},target_kinds=('Lightning Helix',)*3)

    def test_actual_temporal_trespass_retains_delve_and_creates_the_next_extra_turn(self):
        session=self.session(751108);source=self.add(session.engine,'Temporal Trespass',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.prepare(session,mana={'U':3,'C':8});self.checkpoint(session)
        self.cast(session,'A',source,payment={'U':3,'C':8});session=self.replay(session,load=True)
        self.assertIsNone(self.resolve(session));self.assertEqual('exile',session.state.cards[source.object_id].zone)
        self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
        turn=session.state.turn_sequence
        self.advance(session,lambda s:s.state.turn_sequence>turn and s.state.phase=='precombat_main')
        self.assertEqual('A',session.state.active_player);self.private_replay(session,private)

    def pending_original(self,name,seed,count,payment,*,x=None,discard=False):
        session,source,targets,foreign,private=self.original(name,seed,count,payment)
        prices=[self.add(session.engine,kind,zone='hand',ref='pending-price-'+str(i))
            for i,kind in enumerate(('Grizzly Bears','Generic Bound Growth')[:count])] if discard else []
        command={'action_id':'cast:'+source.ref,'targets':[c.ref for c in targets],'pay':'manual','payment':payment}
        if x is not None:command['x']=x
        if discard:command['discard_cards']=[c.ref for c in prices]
        return session,source,targets,prices,foreign,private,command

    def test_countering_prevents_self_exile_and_never_refunds_the_original_price(self):
        for index,(name,count,payment,x,discard) in enumerate((('Treasured Find',1,{'B':1,'G':1},None,False),
                ('Restock',2,{'G':2,'C':3},None,False),('Wildest Dreams',2,{'G':1,'C':4},2,False),('Nostalgic Dreams',2,{'G':2},2,True))):
            with self.subTest(name=name):
                session,source,targets,prices,foreign,private,command=self.pending_original(name,751120+index,count,payment,x=x,discard=discard)
                counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
                session.state.players['B'].mana_pool.update({'U':2});self.prepare(session,mana=payment);self.checkpoint(session)
                result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                self.cast(session,'B',counter,[session.state.stack[-1].ref],{'U':2});session=self.replay(session,load=True)
                self.assertIsNone(self.resolve(session));self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
                self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in (*targets,*prices)))
                self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()));self.private_replay(session,private)

    def test_partial_targets_still_self_exile_but_all_illegal_targets_go_to_graveyard(self):
        for index,(name,count,payment,x,discard,removed) in enumerate((('Restock',2,{'G':2,'C':3},None,False,1),
                ('Wildest Dreams',2,{'G':1,'C':4},2,False,2),('Nostalgic Dreams',2,{'G':2},2,True,2),
                ('Treasured Find',1,{'B':1,'G':1},None,False,1))):
            with self.subTest(name=name):
                session,source,targets,prices,foreign,private,command=self.pending_original(name,751130+index,count,payment,x=x,discard=discard)
                removals=[self.add(session.engine,'Cremate',seat='B',zone='hand',ref='remove-grave-target-'+str(i)) for i in range(removed)]
                for card in removals:self.assertTrue(self.binding(session,card)['strict_capability_ready'])
                session.state.players['B'].mana_pool.update({'B':removed});self.prepare(session,mana=payment);self.checkpoint(session)
                result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                for removal,target in zip(removals,targets):
                    self.cast(session,'B',removal,[target.ref],{'B':1});self.top(session)
                self.assertEqual(1,len(session.state.stack));session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
                self.assertTrue(all(session.state.cards[c.object_id].zone=='exile' for c in targets[:removed]))
                self.assert_known_returns(session,targets[removed:])
                self.assertEqual('graveyard' if removed==count else 'exile',session.state.cards[source.object_id].zone)
                self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in prices));self.private_replay(session,private)

    def test_zero_x_and_zero_optional_targets_resolve_and_self_exile(self):
        for i,(name,payment,x,discard) in enumerate((('Wildest Dreams',{'G':1},0,False),('Nostalgic Dreams',{'G':2},0,True),('Seeds of Renewal',{'G':1,'C':3},None,False))):
            with self.subTest(name=name):self.cast_original(name,751140+i,0,payment,x=x,discard=discard)

    def test_returned_target_incarnation_is_illegal_after_real_exile_and_graveyard_return(self):
        session,source,targets,prices,foreign,private,command=self.pending_original('Treasured Find',751150,1,{'B':1,'G':1})
        exile=self.add(session.engine,'Cremate',seat='B',zone='hand');restore=self.add(session.engine,'Pull from Eternity',seat='B',zone='hand')
        for card in (exile,restore):self.assertTrue(self.binding(session,card)['strict_capability_ready'])
        incarnation=targets[0].logical_object_id
        session.state.players['B'].mana_pool.update({'B':1,'W':1});self.prepare(session,mana=command['payment']);self.checkpoint(session)
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.cast(session,'B',exile,[targets[0].ref],{'B':1});self.top(session)
        self.cast(session,'B',restore,[targets[0].ref],{'W':1});self.top(session)
        self.assertEqual('graveyard',session.state.cards[targets[0].object_id].zone)
        self.assertNotEqual(incarnation,session.state.cards[targets[0].object_id].logical_object_id)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[targets[0].object_id].zone);self.private_replay(session,private)

    def test_copied_nostalgic_dreams_retargets_for_its_controller_without_exiling_original_or_repaying(self):
        session,source,targets,prices,foreign,private,command=self.pending_original('Nostalgic Dreams',751160,2,{'G':2},x=2,discard=True)
        copying=self.add(session.engine,'Twincast',seat='B',zone='hand')
        own=[self.add(session.engine,kind,seat='B',zone='graveyard',ref='copied-own-return-'+str(i))
            for i,kind in enumerate(('Grizzly Bears','Generic Bound Growth'))]
        session.state.players['B'].mana_pool.update({'U':2});self.prepare(session,mana=command['payment']);self.checkpoint(session)
        b_hand=len(session.state.players['B'].zones['hand'])
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.cast(session,'B',copying,[session.state.stack[-1].ref],{'U':2})
        pending=self.resolve(session);self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind)
        session=self.replay(session,load=True)
        chosen=session.act('pilot:B',{'action_id':'choose','targets':[c.ref for c in own]});self.assertTrue(chosen.ok,chosen.summary)
        copies=[i for i in session.state.stack if i.kind=='spell_copy'];self.assertEqual(1,len(copies))
        self.assertEqual(('B',2),(copies[0].controller,copies[0].x_value))
        copy_id=copies[0].card_object_id
        self.assertNotEqual(source.object_id,copy_id)
        self.assertTrue(session.state.cards[copy_id].is_spell_copy)
        self.top(session)
        self.assertEqual('stack',session.state.cards[source.object_id].zone)
        self.assertFalse(any(copy_id in zone for player in session.state.players.values() for zone in player.zones.values()))
        self.assert_known_returns(session,own,owner='B');self.assertEqual(b_hand+1,len(session.state.players['B'].zones['hand']))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in (*targets,*prices)))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assert_known_returns(session,targets)
        self.assertEqual('exile',session.state.cards[source.object_id].zone)
        self.assertTrue(all(session.state.cards[c.object_id].zone=='graveyard' for c in prices));self.private_replay(session,private)

    def test_commander_return_replacement_load_resumes_before_the_terminal_self_exile(self):
        session,source,targets,foreign,private=self.original('Restock',751170,2,{'G':2,'C':3})
        commander_id=session.state.players['A'].zones['command'][0]
        commander=session.state.cards[commander_id]
        session.engine.move_card(commander_id,'graveyard',reason='Fixture commander return boundary')
        session.engine.permissions.invalidate_current();session.state.pending_decision=None
        self.assertTrue(session.engine._stabilize());self.assertEqual('state.commander_zone',session.state.pending_decision.kind)
        kept=session.act('pilot:A',{'a':'choose','choice':'remain'});self.assertTrue(kept.ok,kept.summary)
        self.prepare(session,mana={'G':2,'C':3});self.checkpoint(session)
        self.cast(session,'A',source,[targets[0].ref,commander.ref],{'G':2,'C':3})
        pending=self.resolve(session);self.assertIsNotNone(pending);self.assertEqual('replacement.order',pending.kind)
        self.assertEqual('stack',session.state.cards[source.object_id].zone)
        self.assertEqual(['pilot:A'],session.pending_principals())
        session=self.replay(session,load=True)
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        offered=[o for o in options if not o.get('decline')];self.assertEqual(1,len(offered))
        before=authoritative_state_hash(session.state)
        for actor,option in (('B',offered[0]['id']),('A','unoffered-self-exile-replacement')):
            self.assertFalse(session.act('pilot:'+actor,{'action_id':'choose','replacement':option}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
        chosen=session.act('pilot:A',{'action_id':'choose','replacement':offered[0]['id']});self.assertTrue(chosen.ok,chosen.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('command',session.state.cards[commander_id].zone)
        self.assert_known_returns(session,[targets[0]])
        self.assertEqual('graveyard',session.state.cards[targets[1].object_id].zone)
        self.assertEqual('exile',session.state.cards[source.object_id].zone);self.private_replay(session,private)

    def test_qualified_return_filters_reject_owned_wrong_types_and_monocolored_cards(self):
        for index,(name,payment,legal) in enumerate((('Flood of Recollection',{'U':2},'Generic Bound Growth'),
                ('Vivid Revival',{'G':1,'C':4},'Lightning Helix'))):
            with self.subTest(name=name):
                session,source,targets,foreign,private=self.original(name,751180+index,1,payment,target_kinds=(legal,))
                wrong=self.add(session.engine,'Grizzly Bears',zone='graveyard',ref='wrong-qualified-return')
                self.prepare(session,mana=payment);self.checkpoint(session)
                before=authoritative_state_hash(session.state)
                result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'targets':[wrong.ref],'pay':'manual','payment':payment})
                self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))
                self.cast(session,'A',source,[targets[0].ref],payment);self.assertIsNone(self.resolve(session))
                self.assert_known_returns(session,targets);self.assertEqual('graveyard',session.state.cards[wrong.object_id].zone)
                self.assertEqual('exile',session.state.cards[source.object_id].zone);self.private_replay(session,private)

    def test_terminal_self_exile_keeps_source_spans_and_rejects_unowned_source_clauses(self):
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card
        text='Return target card from your graveyard to your hand. Exile Generic Bound Compiler Fixture.'
        ir=compile_oracle_card(record(text,'Sorcery'),capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        nodes=[n for f in ir.faces for n in f.nodes if n.effects];self.assertEqual(1,len(nodes));node=nodes[0]
        self.assertEqual(text,node.text);self.assertEqual(text,text[node.span.start:node.span.end])
        self.assertIn('zone.exile.resolving_spell',node.capability_dependencies)
        self.assertEqual('return_graveyard_card_to_owner_hand',node.effects[0]['op'])
        for invalid in (text.replace('Exile Generic Bound Compiler Fixture.','Exile Some Other Spell.'),
                text+' Draw a card.',text+'\nDraw a card.',
                'Perform an unrepresented instruction. '+text,
                text.replace('Exile Generic Bound Compiler Fixture.','If an unrepresented condition holds, exile Generic Bound Compiler Fixture.'),
                text.replace('Return target card from your graveyard to your hand.','Perform an unrepresented instruction.')):
            self.assertNotEqual('exact',compile_oracle_card(record(invalid,'Sorcery'),capability_registry=self.registry,capability_profile='commander_review').status,invalid)

    def test_verified_exit_withdrawal_and_stored_destination_owner_stripping_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        from scripts.update_capability_evidence import build_index,discover_tests
        cap='zone.exile.resolving_spell'
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        changed=deepcopy(raw);next(r for r in changed['capabilities'] if r['id']==cap).update(status='blocked',blockers=['withdrawn terminal spell exit owner'])
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        registry=CapabilityRegistry(changed);index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        closure=registry.closure((cap,),profile='commander_review');self.assertFalse(closure.trusted)
        self.assertTrue(any('status:'+cap+':blocked' in b for b in closure.blockers))
        for name in ('Treasured Find','Restock','Wildest Dreams','Nostalgic Dreams'):
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,name)
            self.assertFalse(any('evidence_index:unverified' in b for r in ir.material_residuals for b in r.blockers))
        session=self.session(751104);source=self.add(session.engine,'Treasured Find',zone='hand')
        programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies]
        self.assertEqual(1,len(programs));program=programs[0]
        self.assertEqual('exile',program.destination)
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict'])
        deps=tuple(c for c in program.capability_dependencies if c!=cap)
        stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
        for malformed in (replace(program,destination='graveyard'),
                replace(program,coverage=[m for m in program.coverage if m!='spell-resolution-self-exile']),
                replace(program,provenance={**program.provenance,'template_id':'unknown-exit'})):
            self.assertFalse(bind_semantic_program_runtime(malformed,capability_registry=self.registry,profile='commander_review')['strict'])

    def test_fresh_originals_kill_self_exit_producer_destination_and_binding_omissions(self):
        from quorune.stack_resolution import complete_stack_resolution
        def without_exit(host,*,item,destination,prepared_replacement):
            return complete_stack_resolution(host,item=item,destination='graveyard' if destination=='exile' else destination,prepared_replacement=prepared_replacement)
        cases=(('quorune.compiler.spell_self_exile.terminal_self_exile_body',lambda *a,**k:None,
                'test_actual_treasured_find_returns_its_card_then_exiles_itself'),
            ('quorune.engine.complete_stack_resolution',without_exit,
                'test_actual_treasured_find_returns_its_card_then_exiles_itself'),
            ('quorune.card_programs.runtime_requirements.spell_self_exile_runtime_dependencies',lambda *a:(),
                'test_verified_exit_withdrawal_and_stored_destination_owner_stripping_reject'))
        for owner,mutation,method in cases:
            with patch(owner,mutation):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

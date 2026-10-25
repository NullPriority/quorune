from __future__ import annotations

from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.model import CombatState
from quorune.engine import TURN_STEPS
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.damage_modifier_state import DamagePreventionScope
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class ScopedPreventionCompilerTests(unittest.TestCase):
    def test_target_modifier_and_fixed_prevention_share_one_bound_target_program(self):
        from test_bound_effect_programs import record
        text='Target creature gains flying until end of turn. Prevent all damage that would be dealt to that creature this turn.'
        ir=compile_oracle_card(record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0]
        self.assertEqual('$target.0',node.effects[1]['subject'])
        self.assertNotIn('scope',node.effects[1])
        self.assertIn('resolution.effect_program.bound_references',node.capability_dependencies)
        self.assertIn('damage.prevention.persistent_amount',node.capability_dependencies)

    def test_prevention_component_rejects_derived_amounts_open_scopes_and_missing_owner(self):
        from test_bound_effect_programs import record
        from quorune.rules.prevention_component_shapes import fixed_prevention_component_capabilities
        from quorune.rules.capabilities import CapabilityRegistry
        text='Target creature gains flying until end of turn. Prevent all damage that would be dealt to that creature this turn.'
        ir=compile_oracle_card(record(text),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        node=ir.faces[0].nodes[0];effect=dict(node.effects[1])
        self.assertIn('damage.prevention.persistent_amount',fixed_prevention_component_capabilities(
            effects=(effect,),target_schema=node.target_schema,mechanic_ids=node.mechanics))
        for wrong in ({**effect,'amount':'$x'},{**effect,'scope':{'private':True}},
                      {**effect,'mode':'amount'},{**effect,'subject':'$target.1'}):
            self.assertFalse(fixed_prevention_component_capabilities(effects=(wrong,),target_schema=node.target_schema,mechanic_ids=node.mechanics))
        registry_value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in registry_value['capabilities'] if c['id']=='damage.prevention.persistent_amount').update(status='blocked',blockers=['missing durable shield'])
        registry=CapabilityRegistry(registry_value);registry.mark_evidence_verified('0'*64)
        self.assertNotEqual('exact',compile_oracle_card(record(text),capability_registry=registry).status)

    def test_role_token_attachment_source_color_and_pair_scopes_are_closed(self):
        registry=load_default_capability_registry()
        for text in ('Prevent all combat damage that would be dealt to attacking creatures you control.',
                     'Prevent all damage that would be dealt to creature tokens you control.',
                     'Prevent all combat damage that would be dealt to this creature by creatures blocking it.',
                     "Prevent all damage that would be dealt to this creature by creatures it's blocking.",
                     'Prevent all damage that would be dealt to a creature by another creature if they share a color.',
                     'Prevent all damage that this creature would deal to red creatures.',
                     'Prevent all damage that black sources and red sources would deal this turn.',
                     'Prevent all combat damage that would be dealt this turn by creatures with no +1/+1 counters on them.'):
            record=query_record(text)
            if 'this turn' in text:record=replace(record,type_line='Instant')
            self.assertEqual('exact',compile_oracle_card(record,capability_registry=registry).status,text)

    def test_scope_codec_old_form_is_preserved_and_malformed_extensions_reject(self):
        old=DamagePreventionScope().to_dict()
        self.assertNotIn('combat_pair',old);self.assertNotIn('shared_color',old);self.assertNotIn('target_colors_any',old)
        self.assertEqual(old,DamagePreventionScope.from_dict(old).to_dict())
        for patch_values in ({'combat_pair':1},{'shared_color':'yes'},{'target_colors_any':['secret']},{'invented':True}):
            with self.assertRaises(ValueError):DamagePreventionScope.from_dict({**old,**patch_values})
        registry=load_default_capability_registry()
        for text in ('Prevent all damage that would be dealt to creatures with unknown counters.',
                     'Prevent half the damage that would be dealt to attacking creatures you control.',
                     'Prevent all damage that would be dealt to creatures with the most power.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status)

    def test_damage_source_and_redirection_codecs_preserve_empty_legacy_forms(self):
        from quorune.damage_source import DamageSourceSnapshot
        from quorune.replacement.operations import RedirectDamage,operation_from_dict
        source=DamageSourceSnapshot('source','id','id@0','A','A')
        old=source.to_dict();self.assertNotIn('scope_facts',old);self.assertNotIn('combat_partners',old)
        self.assertEqual(old,DamageSourceSnapshot.from_dict(old).to_dict())
        for change in ({'scope_facts':['private']},{'scope_facts':'token'},{'combat_partners':True}):
            with self.assertRaises(ValueError):DamageSourceSnapshot.from_dict({**old,**change})
        redirect=RedirectDamage('B','player','B');old_redirect=redirect.to_dict()
        self.assertNotIn('target_scope_facts',old_redirect)
        self.assertEqual(old_redirect,operation_from_dict(old_redirect).to_dict())
        for change in ({'target_scope_facts':['unknown']},{'target_scope_facts':'token'},{'target_colors':True}):
            with self.assertRaises(ValueError):operation_from_dict({**old_redirect,**change})


class ScopedPreventionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'scoped-prevention.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/scoped-damage-prevention-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Scoped prevention',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve

    def cast(self,s,card,targets):
        for _ in range(8):
            if s.packet('pilot:A',full=True)['decision'] is not None:break
            self.assertEqual('priority',s.state.pending_decision.kind,s.state.pending_decision.to_dict())
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==card.ref)
        result=s.act('pilot:A',{'action_id':action['id'],'targets':list(targets),'pay':'auto'})
        self.assertTrue(result.ok,result.summary);return self.resolve(s)

    def test_actual_emmara_prevents_only_own_creature_token_shock_and_replays(self):
        s=self.session(325001);e=s.engine
        self.add(e,'Emmara Tandris');source=self.add(e,'Cackling Counterpart',zone='hand');body=self.add(e,'Generic Bound Body')
        first=self.add(e,'Shock',zone='hand',ref='token-shock');second=self.add(e,'Shock',zone='hand',ref='normal-shock')
        self.ready(s,source,{'U':3,'R':4,'C':3});self.checkpoint(s)
        existing=set(e.state.cards);self.cast(s,source,[body.ref])
        token=next(c for oid,c in e.state.cards.items() if oid not in existing and c.is_token and c.zone=='battlefield')
        self.cast(s,first,[token.ref]);self.assertEqual(0,e.state.cards[token.object_id].marked_damage)
        self.cast(s,second,[body.ref]);self.assertEqual(2,e.state.cards[body.object_id].marked_damage)
        self.replay(s,load=True)

    def test_actual_dolmen_attackers_take_no_combat_damage_but_unblocked_attacker_hits_player(self):
        s=self.session(325002);e=s.engine
        self.add(e,'Dolmen Gate');one=self.add(e,'Generic Bound Body',ref='protected-attacker');two=self.add(e,'Generic Bound Body',ref='unblocked-attacker');blocker=self.add(e,'Generic Bound Body',seat='B')
        e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_player=None;e.state.priority_passes=[]
        e.state.started=True;e.state.active_player='A';e.state.phase='combat';e.state.step='declare_blockers';e.state.phase_index=TURN_STEPS.index(('combat','declare_blockers'))
        one.attacking='B';two.attacking='B';e.state.combat=CombatState(attackers_declared=True,attackers={one.object_id:'B',two.object_id:'B'},defending_players=['B'])
        e._begin_blocker_decisions();self.checkpoint(s)
        result=s.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:one.ref}});self.assertTrue(result.ok,result.summary)
        for _ in range(16):
            if e.state.players['B'].life==38:break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(38,e.state.players['B'].life);self.assertEqual(0,e.state.cards[one.object_id].marked_damage)
        self.assertEqual(2,e.state.cards[blocker.object_id].marked_damage);self.replay(s,load=True)

    def test_omitting_token_fact_mutant_is_killed_by_actual_copy_and_damage(self):
        from quorune.damage_scope_facts import current_damage_scope_facts
        def without_token(*args):return tuple(f for f in current_damage_scope_facts(*args) if f!='token')
        with patch('quorune.rules.damage_snapshots.current_damage_scope_facts',side_effect=without_token):
            with self.assertRaises(AssertionError):self.test_actual_emmara_prevents_only_own_creature_token_shock_and_replays()

    def test_block_order_canonicalization_mutant_is_killed_by_saved_actual_command(self):
        from quorune.rules.block_declaration import validate_block_assignments
        def reverse_pairs(*args):
            chosen,canonical=validate_block_assignments(*args)
            # Reintroduce the original client mapping order at this boundary.
            ordering={ref:index for index,ref in enumerate(args[2])}
            chosen.sort(key=lambda pair:ordering[pair[0].ref])
            return chosen,canonical
        with patch('quorune.rules.block_declaration.validate_block_assignments',side_effect=reverse_pairs):
            with self.assertRaises(ValueError):self.test_actual_wall_prevents_only_blocked_creature_damage_across_multiple_pairings()

    def test_actual_wall_prevents_only_blocked_creature_damage_across_multiple_pairings(self):
        s=self.session(325003);e=s.engine
        wall=self.add(e,'Wall of Vapor',seat='B');second=self.add(e,'Generic Bound Body',seat='B',ref='second-defender')
        attacker=self.add(e,'Trueheart Duelist');attacker.attacking='B'
        e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_player=None;e.state.priority_passes=[]
        e.state.started=True;e.state.active_player='A';e.state.phase='combat';e.state.step='declare_blockers';e.state.phase_index=TURN_STEPS.index(('combat','declare_blockers'))
        e.state.combat=CombatState(attackers_declared=True,attackers={attacker.object_id:'B'},defending_players=['B'])
        e._begin_blocker_decisions();self.checkpoint(s)
        result=s.act('pilot:B',{'action_id':'block','blocks':{wall.ref:attacker.ref,second.ref:attacker.ref}});self.assertTrue(result.ok,result.summary)
        for _ in range(16):
            if e.state.pending_decision and e.state.pending_decision.kind=='combat.damage':break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('combat.damage',e.state.pending_decision.kind)
        result=s.act('pilot:A',{'action_id':'assign_damage','assignments':[
            {'source':attacker.ref,'target':wall.ref,'amount':1},
            {'source':attacker.ref,'target':second.ref,'amount':1}]})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,e.state.cards[wall.object_id].marked_damage)
        self.assertEqual(1,e.state.cards[second.object_id].marked_damage)
        self.replay(s,load=True)

    def test_actual_shared_color_plans_prevents_creature_damage_only_with_matching_color(self):
        s=self.session(325004);e=s.engine
        self.add(e,'Well-Laid Plans');white=self.add(e,'Generic Bound Body');other=self.add(e,'Generic Bound Body',seat='B',ref='white-color-recipient')
        white.temporary_keywords.append('Haste')
        # This record starts at an independently constructed combat boundary.
        e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_player=None;e.state.priority_passes=[]
        e.state.started=True;e.state.active_player='A';e.state.phase='combat';e.state.step='declare_blockers';e.state.phase_index=TURN_STEPS.index(('combat','declare_blockers'))
        white.attacking='B';e.state.combat=CombatState(attackers_declared=True,attackers={white.object_id:'B'},defending_players=['B'])
        e._begin_blocker_decisions();self.checkpoint(s)
        result=s.act('pilot:B',{'action_id':'block','blocks':{other.ref:white.ref}});self.assertTrue(result.ok,result.summary)
        for _ in range(16):
            if e.state.pending_decision and e.state.pending_decision.kind!='priority':break
            if e.state.combat.damage_step_initialized:break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,e.state.cards[white.object_id].marked_damage);self.assertEqual(0,e.state.cards[other.object_id].marked_damage)
        self.replay(s,load=True)

    def test_actual_shared_color_negative_keeps_unmatched_combat_damage(self):
        s=self.session(325009);e=s.engine
        self.add(e,'Well-Laid Plans');red=self.add(e,'Blitz Automaton',zone='hand');white=self.add(e,'Generic Bound Body',seat='B')
        self.ready(s,red,{'R':2,'C':8})
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='cast-prototype:'+red.ref)
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_player=None;e.state.priority_passes=[]
        e.state.phase='combat';e.state.step='declare_blockers';e.state.phase_index=TURN_STEPS.index(('combat','declare_blockers'));red=e.state.cards[red.object_id];red.attacking='B'
        e.state.combat=CombatState(attackers_declared=True,attackers={red.object_id:'B'},defending_players=['B']);e._begin_blocker_decisions();self.checkpoint(s)
        result=s.act('pilot:B',{'action_id':'block','blocks':{white.ref:red.ref}});self.assertTrue(result.ok,result.summary)
        for _ in range(16):
            if e.state.combat.damage_step_initialized:break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(3,e.state.cards[white.object_id].marked_damage)
        self.assertEqual('graveyard',e.state.cards[red.object_id].zone)
        self.replay(s,load=True)

    def test_actual_emmara_source_bounce_removes_prevention_and_replays(self):
        for spell_name in ('Unsummon',):
            s=self.session(325005);e=s.engine
            guardian=self.add(e,'Emmara Tandris');copy=self.add(e,'Cackling Counterpart',zone='hand');body=self.add(e,'Generic Bound Body')
            change=self.add(e,spell_name,zone='hand');damage=self.add(e,'Shock',zone='hand')
            self.ready(s,copy,{'U':5,'R':5,'C':4});self.checkpoint(s)
            existing=set(e.state.cards);self.cast(s,copy,[body.ref]);token=next(c for oid,c in e.state.cards.items() if oid not in existing and c.is_token and c.zone=='battlefield')
            self.cast(s,change,[guardian.ref])
            self.cast(s,damage,[token.ref]);self.assertEqual(2,e.state.cards[token.object_id].marked_damage);self.replay(s,load=True)

    def test_actual_prevention_quantity_replacement_pending_reload_preserves_same_damage(self):
        s=self.session(325007);e=s.engine
        self.add(e,'Emmara Tandris');self.add(e,'Furnace of Rath');copy=self.add(e,'Cackling Counterpart',zone='hand');body=self.add(e,'Generic Bound Body');damage=self.add(e,'Shock',zone='hand')
        self.ready(s,copy,{'U':3,'R':3,'C':3});self.checkpoint(s)
        existing=set(e.state.cards);self.cast(s,copy,[body.ref]);token=next(c for oid,c in e.state.cards.items() if oid not in existing and c.is_token and c.zone=='battlefield')
        pending=self.cast(s,damage,[token.ref]);self.assertEqual('replacement.order',pending.kind)
        self.assertEqual(0,e.state.cards[token.object_id].marked_damage)
        resumed=self.replay(s,load=True);before=authoritative_state_hash(resumed.state)
        packet=resumed.packet('pilot:A',full=True)['decision'];option=packet['ctx']['options'][0]['id']
        self.assertFalse(resumed.act('pilot:B',{'action_id':'choose','replacement':option}).ok)
        self.assertEqual(before,authoritative_state_hash(resumed.state))
        while resumed.state.pending_decision and resumed.state.pending_decision.kind=='replacement.order':
            packet=resumed.packet('pilot:A',full=True)['decision'];result=resumed.act('pilot:A',{'action_id':'choose','replacement':packet['ctx']['options'][0]['id']});self.assertTrue(result.ok,result.summary)
        self.resolve(resumed);self.assertEqual(0,resumed.state.cards[token.object_id].marked_damage);self.replay(resumed,load=True)

    def test_model_redirection_replaces_token_scope_facts_and_color_pair_result(self):
        from quorune.damage import damage_proposal,prepare_damage_batch,commit_prepared_damage_batch
        from quorune.damage_modifier_state import DamageRedirectionEffect,DamageSubject,DamageModifierDuration
        from quorune.replacement_effects import ReplacementChoiceRequired
        s=self.session(325008);e=s.engine;self.add(e,'Emmara Tandris');source=self.add(e,'Generic Bound Body',seat='B',ref='redirect-damage-source');target=self.add(e,'Generic Bound Body',ref='redirect-nontoken')
        token_ref=e.create_token('A',name='Canonical token recipient',characteristics={'type_line':'Token Creature','power':'2','toughness':'6','colors':['W']})[0]
        token=e._resolve_object('A',token_ref,zones={'battlefield'})
        effect=DamageRedirectionEffect('model-redirect','effect:model-redirect','A',
            DamageSubject(token.ref,'permanent','A',token.object_id,token.logical_object_id,token.owner),
            DamageSubject(target.ref,'permanent','A',target.object_id,target.logical_object_id,target.owner),
            DamageModifierDuration.UNTIL_END_OF_TURN,e.state.turn_sequence)
        e.state.damage_redirections.append(effect)
        proposal=damage_proposal(e,proposal_id='redirect-scope-proof',actor='B',source_ref=source.ref,target=token.ref,amount=2,combat=False,reason='canonical redirection scope diagnostic')
        selections=[]
        for _ in range(6):
            try:prepared=prepare_damage_batch(e,(proposal,),selections=tuple(selections));break
            except ReplacementChoiceRequired as required:
                choice=next((option for option in required.pending.choice.options if 'model-redirect' in option),required.pending.choice.options[0]);selections.append(choice)
        else:self.fail('Canonical redirection did not resolve')
        event=prepared.events[0]
        self.assertEqual(target.ref,event.payload['target']);self.assertNotIn('token',event.payload['target_characteristics'])
        self.assertTrue(event.payload['source_and_target_share_color'])
        commit_prepared_damage_batch(e,prepared)
        self.assertEqual(2,e.state.cards[target.object_id].marked_damage);self.assertEqual(0,e.state.cards[token.object_id].marked_damage)

    def test_actual_hindervines_preserves_only_countered_source_combat_damage(self):
        s=self.session(325010);e=s.engine
        one=self.add(e,'Generic Bound Body',ref='countered-hindervines-attacker');one.counters['+1/+1']=1
        two=self.add(e,'Generic Bound Body',ref='plain-hindervines-attacker');spell=self.add(e,'Hindervines',zone='hand')
        self.ready(s,spell,{'G':3,'C':4});self.checkpoint(s);self.cast(s,spell,[])
        # The cast is separately replayed; the next checkpoint fixes a combat boundary.
        self.replay(s,load=True)
        e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_player=None;e.state.priority_passes=[]
        e.state.phase='combat';e.state.step='declare_blockers';e.state.phase_index=TURN_STEPS.index(('combat','declare_blockers'))
        one=e.state.cards[one.object_id];two=e.state.cards[two.object_id];one.attacking='B';two.attacking='B'
        e.state.combat=CombatState(attackers_declared=True,attackers={one.object_id:'B',two.object_id:'B'},defending_players=['B']);e._begin_blocker_decisions();e.pump();self.checkpoint(s)
        # No defending creature: the ordinary priority step advances to damage.
        for _ in range(16):
            if e.state.players['B'].life==37:break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(37,e.state.players['B'].life);self.replay(s,load=True)

    def test_actual_luminesce_prevents_red_shock_but_not_blue_damage(self):
        s=self.session(325011);e=s.engine
        spell=self.add(e,'Luminesce',zone='hand');red=self.add(e,'Shock',zone='hand');blue=self.add(e,'Prodigal Sorcerer');blue.acquired_control_turn_count=-1
        self.ready(s,spell,{'W':2,'U':2,'R':2,'C':4});self.checkpoint(s)
        self.cast(s,spell,[]);self.cast(s,red,['B']);self.assertEqual(40,e.state.players['B'].life)
        action=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('activate:'+blue.ref+':'))
        result=s.act('pilot:A',{'action_id':action['id'],'targets':['B'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual(39,e.state.players['B'].life);self.replay(s,load=True)

    def test_model_unpreventable_damage_ignores_scoped_prevention_without_hiding_effect(self):
        from quorune.damage import damage_proposal,prepare_damage_batch,commit_prepared_damage_batch
        from quorune.replacement_effects import ReplacementChoiceRequired
        s=self.session(325012);e=s.engine;self.add(e,'Emmara Tandris');source=self.add(e,'Generic Bound Body',seat='B',ref='unpreventable-source')
        ref=e.create_token('A',name='Canonical unpreventable token',characteristics={'type_line':'Token Creature','power':'2','toughness':'6'})[0]
        target=e._resolve_object('A',ref,zones={'battlefield'})
        proposal=damage_proposal(e,proposal_id='unpreventable-scope-proof',actor='B',source_ref=source.ref,target=target.ref,amount=2,combat=False,reason='canonical unpreventable diagnostic',unpreventable=True)
        selections=[]
        for _ in range(6):
            try:prepared=prepare_damage_batch(e,(proposal,),selections=tuple(selections));break
            except ReplacementChoiceRequired as required:selections.append(required.pending.choice.options[0])
        else:self.fail('Unpreventable diagnostic did not finish')
        self.assertEqual(2,prepared.events[0].payload['amount']);self.assertEqual(0,prepared.events[0].payload['prevented'])
        commit_prepared_damage_batch(e,prepared);self.assertEqual(2,e.state.cards[target.object_id].marked_damage)

    def test_actual_enchanted_source_prevention_follows_aura_damage_scope(self):
        s=self.session(325013);e=s.engine
        attacker=self.add(e,'Generic Bound Body');aura=self.add(e,"Iona's Blessing",zone='hand');blocker=self.add(e,'Enchanted Being',seat='B')
        self.ready(s,aura,{'W':2,'C':4});self.checkpoint(s);self.cast(s,aura,[attacker.ref]);self.replay(s,load=True)
        e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_player=None;e.state.priority_passes=[]
        e.state.phase='combat';e.state.step='declare_blockers';e.state.phase_index=TURN_STEPS.index(('combat','declare_blockers'));attacker=e.state.cards[attacker.object_id];attacker.attacking='B'
        e.state.combat=CombatState(attackers_declared=True,attackers={attacker.object_id:'B'},defending_players=['B']);e._begin_blocker_decisions();self.checkpoint(s)
        result=s.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:attacker.ref}});self.assertTrue(result.ok,result.summary)
        for _ in range(16):
            if e.state.combat.damage_step_initialized:break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,e.state.cards[blocker.object_id].marked_damage);self.assertEqual(2,e.state.cards[attacker.object_id].marked_damage);self.replay(s,load=True)

    def test_actual_oaf_prevents_only_its_damage_to_red_creature(self):
        s=self.session(325014);e=s.engine
        attacker=self.add(e,'Indentured Oaf');red=self.add(e,'Blitz Automaton',seat='B',zone='hand');probe=self.add(e,'Shock',zone='hand')
        self.ready(s,probe,{'R':2,'C':2});e.state.players['B'].mana_pool.update({'R':2,'C':3})
        # Cast the defending red Prototype through its controller, before fixing combat.
        e.permissions.invalidate_current();e.state.pending_decision=None;e.state.active_player='B';e._grant_priority('B');e.pump()
        action=next(a for a in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a['id']=='cast-prototype:'+red.ref)
        result=s.act('pilot:B',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_player=None;e.state.priority_passes=[]
        e.state.active_player='A';e.state.phase='combat';e.state.step='declare_blockers';e.state.phase_index=TURN_STEPS.index(('combat','declare_blockers'));attacker.attacking='B'
        e.state.combat=CombatState(attackers_declared=True,attackers={attacker.object_id:'B'},defending_players=['B']);e._begin_blocker_decisions();self.checkpoint(s)
        result=s.act('pilot:B',{'action_id':'block','blocks':{red.ref:attacker.ref}});self.assertTrue(result.ok,result.summary)
        for _ in range(16):
            if e.state.cards[attacker.object_id].zone=='graveyard':break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(0,e.state.cards[red.object_id].marked_damage);self.assertEqual('graveyard',e.state.cards[attacker.object_id].zone);self.replay(s,load=True)


class PreventionCompositionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'prevention-composition.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/prevention-composition-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Prevention composition',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve

    def ready(self,s,source,mana):
        e=s.engine;e.permissions.invalidate_current();e.state.pending_decision=None;e.state.started=True
        e.state.active_player='A';e.state.phase='precombat_main';e.state.step='main';e.state.priority_passes=[]
        e.state.players['A'].mana_pool.update(mana);e._grant_priority('A');e.pump()
        return next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row.get('card')==source.ref)

    def cast(self,s,card,target):
        for _ in range(4):
            if s.packet('pilot:A',full=True)['decision'] is not None:break
            accepted=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
        action=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row.get('card')==card.ref)
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(s)

    def test_original_leap_of_faith_modifier_and_prevention_resolve_then_damage_boundary_replays(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        s=self.session(364001);e=s.engine;spell=self.add(e,'Leap of Faith',zone='hand')
        target=self.add(e,'Generic Bound Body',seat='B');other=self.add(e,'Generic Bound Body',seat='C',ref='other-body')
        first=self.add(e,'Shock',zone='hand',ref='prevented-shock');second=self.add(e,'Shock',zone='hand',ref='other-shock')
        p=compile_best_available_card_program(self.db,self.db.lookup('Leap of Faith'),semantic_registry=e.semantics,capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])
        self.ready(s,spell,{'W':1,'C':4,'R':4});self.checkpoint(s);self.cast(s,spell,target)
        self.assertIn('flying',e._combat_keywords(e.state.cards[target.object_id]));self.assertEqual(1,len(e.state.damage_prevention_shields))
        self.cast(s,first,target);self.assertEqual(0,e.state.cards[target.object_id].marked_damage)
        self.cast(s,second,other);self.assertEqual(2,e.state.cards[other.object_id].marked_damage)
        projection=json.dumps(s.projector._snapshot('pilot:D'),sort_keys=True)
        for seat in 'ABC':
            for oid in e.state.players[seat].zones['hand']:self.assertNotIn(e.state.cards[oid].ref,projection)
        self.replay(s,load=True)

    def test_original_djeru_untap_and_prevention_expire_and_preserve_target_authority(self):
        from quorune.damage_prevention import expire_end_of_turn_damage_modifiers
        from quorune.damage import damage_proposal,resolve_damage_batch
        s=self.session(364002);e=s.engine;spell=self.add(e,"Djeru's Resolve",zone='hand')
        target=self.add(e,'Generic Bound Body',seat='B');target.tapped=True
        action=self.ready(s,spell,{'W':1});self.checkpoint(s);before=authoritative_state_hash(e.state)
        denied=s.act('pilot:C',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        accepted=s.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(s);self.assertFalse(e.state.cards[target.object_id].tapped);self.assertEqual(1,len(e.state.damage_prevention_shields))
        self.replay(s,load=True);expire_end_of_turn_damage_modifiers(e.state)
        result=resolve_damage_batch(e,(damage_proposal(e,proposal_id='fixture:expired-prevention',actor='A',source_ref=spell.ref,target=target.ref,amount=1,combat=False,reason='expired prevention distinction'),))
        self.assertEqual(1,result.dealt_amount)

    def test_prevention_component_omission_mutant_is_killed(self):
        import quorune.rules.fixed_effect_clause_shapes as owner
        resolvers=tuple(value for value in owner._COMPONENT_RESOLVERS if value.__name__!='fixed_prevention_component_capabilities')
        with patch.object(owner,'_COMPONENT_RESOLVERS',resolvers):
            with self.assertRaises(AssertionError):
                self.test_original_leap_of_faith_modifier_and_prevention_resolve_then_damage_boundary_replays()

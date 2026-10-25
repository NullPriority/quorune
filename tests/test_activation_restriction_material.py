from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.abilities import parse_activated_abilities
from quorune.carddb import CardDatabase
from quorune.compiler.activated_mana_nodes import _activated_effect_material
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.player_speed_model import player_speed
from scripts.build_test_database import build_fixture_database
import test_public_condition_self_replacements as controls


NAMES=('Bagel and Schmear','Hour of Victory','Icatian Moneychanger',"Inventors' Fair",'Krosan Avenger','Llanowar Loamspeaker','Nantuko Monastery')


class ActivationRestrictionMaterialTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.PublicConditionSelfReplacementTests.session
    start=controls.PublicConditionSelfReplacementTests.start
    add=controls.PublicConditionSelfReplacementTests.add
    prepare=controls.PublicConditionSelfReplacementTests.prepare
    ready=controls.PublicConditionSelfReplacementTests.ready
    checkpoint=controls.PublicConditionSelfReplacementTests.checkpoint
    resolve=controls.PublicConditionSelfReplacementTests.resolve
    replay=controls.PublicConditionSelfReplacementTests.replay
    binding=controls.PublicConditionSelfReplacementTests.binding
    priority=controls.PublicConditionSelfReplacementTests.priority
    private_replay=controls.PublicConditionSelfReplacementTests.private_replay
    private=controls.PublicConditionSelfReplacementTests.private
    cast=controls.PublicConditionSelfReplacementTests.cast
    reject=controls.PublicConditionSelfReplacementTests.reject
    graveyard=controls.PublicConditionSelfReplacementTests.graveyard
    play_land=controls.PublicConditionSelfReplacementTests.play_land
    search=controls.PublicConditionSelfReplacementTests.search
    advance=controls.PublicConditionSelfReplacementTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'activation-restriction-material.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/activation-restriction-material-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Represented activation material',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def actions(self,s,seat='A'):
        return s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']

    def action(self,s,c,ability_id):
        return next((a for a in self.actions(s) if a['id']=='activate:'+c.ref+':'+ability_id),None)

    def activate(self,s,c,ability_id,targets=(),**extra):
        self.priority(s,'A');row=self.action(s,c,ability_id);self.assertIsNotNone(row,(c.printed_name,ability_id))
        command={'action_id':row['id'],'targets':list(targets),'pay':'auto',**extra}
        self.reject(s,command,'D');result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def next_upkeep_trigger(self,s):
        for _ in range(500):
            s.engine.pump()
            if s.state.active_player=='A' and s.state.step=='upkeep' and s.state.stack:return
            decision=s.state.pending_decision;self.assertIsNotNone(decision)
            command=({'action_id':'pass'} if decision.kind=='priority' else
                {'action_id':'attack','attacks':{}} if decision.kind=='combat.attackers' else
                {'action_id':'block','blocks':{}} if decision.kind=='combat.blockers' else None)
            self.assertIsNotNone(command,decision.kind)
            result=s.act(s.pending_principals()[0],command);self.assertTrue(result.ok,result.summary)
        self.fail('A complete four-player turn cycle did not reach the upkeep trigger')

    def test_recognized_effect_is_preserved_exactly_and_unknown_or_mismatched_restrictions_stay(self):
        for name in NAMES:
            card=self.db.lookup(name)
            for a in parse_activated_abilities(card_name=card.name,oracle_text=card.oracle_text,keywords=card.keywords):
                if 'Activate only' not in a.effect_text:continue
                expected=a.effect_text.split('Activate only',1)[0].strip()
                self.assertEqual(expected,_activated_effect_material(a),name)
                changed=replace(a,sorcery_speed=not a.sorcery_speed)
                self.assertEqual(changed.effect_text.strip(),_activated_effect_material(changed))
                changed=replace(a,effect_text=a.effect_text.replace('Activate only','Activate only after an unrepresented event and',1))
                self.assertEqual(changed.effect_text.strip(),_activated_effect_material(changed))

    def test_all_originals_have_complete_normal_registration(self):
        s=self.start(788000)
        for name in NAMES:
            c=self.add(s.engine,name,zone='hand')
            self.assertTrue(self.binding(s,c)['strict_capability_ready'],name)
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))

    def test_bagel_actual_share_optional_target_and_nosh_keep_all_consequences(self):
        for mode in ('share','zero','nosh'):
            s=self.start(788010+('share','zero','nosh').index(mode))
            source=self.add(s.engine,'Bagel and Schmear',zone='hand');target=self.add(s.engine,'Grizzly Bears',seat='B')
            private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
            count=len(s.state.players['A'].zones['hand']);life=s.state.players['A'].life
            self.activate(s,source,'ab2' if mode=='nosh' else 'ab1',[] if mode!='share' else [target.ref])
            self.assertEqual('graveyard',s.state.cards[source.object_id].zone);s=self.replay(s,load=True)
            self.assertIsNone(self.resolve(s));self.assertEqual(count+1,len(s.state.players['A'].zones['hand']))
            self.assertEqual(life+(3 if mode=='nosh' else 0),s.state.players['A'].life)
            self.assertEqual(1 if mode=='share' else 0,s.state.cards[target.object_id].counters.get('+1/+1',0))
            self.private_replay(s,private)

    def test_krosan_threshold_is_activation_only_and_regeneration_is_one_use(self):
        for amount in (6,7):
            s=self.start(788020+amount);source=self.add(s.engine,'Krosan Avenger',zone='hand')
            graves=self.graveyard(s,['Generic Bound Plains']*amount)
            purify=self.add(s.engine,'Purify the Grave',seat='B',zone='hand');s.state.players['B'].mana_pool['W']=1
            first=self.add(s.engine,'Murder',zone='hand',ref='restriction-first-murder')
            second=self.add(s.engine,'Murder',zone='hand',ref='restriction-second-murder')
            private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
            if amount==6:
                self.assertIsNone(self.action(s,source,'ab2'))
                self.reject(s,{'action_id':'activate:'+source.ref+':ab2','pay':'auto'},'A')
                self.private_replay(s,private);continue
            self.activate(s,source,'ab2');self.cast(s,purify,[graves[0].ref],seat='B')
            self.assertIsNone(self.resolve(s));self.assertEqual(6,len(s.state.players['A'].zones['graveyard']))
            self.cast(s,first,[source.ref]);self.assertIsNone(self.resolve(s))
            current=s.state.cards[source.object_id];self.assertEqual('battlefield',current.zone);self.assertTrue(current.tapped);self.assertEqual(0,current.marked_damage)
            self.cast(s,second,[source.ref]);self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
            self.private_replay(s,private)

    def test_monastery_land_play_mana_animation_and_cleanup_share_existing_owners(self):
        for amount in (6,7):
            s=self.start(788040+amount);source=self.add(s.engine,'Nantuko Monastery',zone='hand')
            self.graveyard(s,['Generic Bound Plains']*amount);private=self.private(s);self.ready(s)
            self.play_land(s,source);self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
            before=s.state.players['A'].mana_pool['C'];self.activate(s,source,'ab1')
            self.assertEqual(before+1,s.state.players['A'].mana_pool['C'])
            if amount==6:self.assertIsNone(self.action(s,source,'ab2'));self.private_replay(s,private);continue
            self.activate(s,source,'ab2');self.assertIsNone(self.resolve(s))
            data=s.engine._effective_card_data(s.state.cards[source.object_id]);types,subtypes,_=s.engine._type_parts(data['type_line'])
            self.assertTrue({'land','creature'}<=types);self.assertTrue({'insect','monk'}<=subtypes)
            self.assertEqual({'G','W'},set(data['colors']));self.assertIn('first strike',s.engine._combat_keywords(s.state.cards[source.object_id]))
            self.assertEqual((4,4),tuple(s.engine._numeric_stat(source.object_id,k) for k in ['power','toughness']))
            self.advance(s,lambda x:x.state.active_player=='B' and x.state.phase=='precombat_main')
            self.assertNotIn('creature',s.engine._type_parts(s.engine._effective_card_data(s.state.cards[source.object_id])['type_line'])[0])
            self.private_replay(s,private)

    def test_loamspeaker_cast_haste_animation_and_mana_modes_retain_sorcery_timing(self):
        s=self.start(788050);source=self.add(s.engine,'Llanowar Loamspeaker',zone='hand')
        haste=self.add(s.engine,'Expedite',zone='hand');land=self.add(s.engine,'Forest',zone='hand')
        enemy=self.add(s.engine,'Forest',seat='B',ref='restriction-enemy-forest');private=self.private(s);self.ready(s)
        self.cast(s,source);self.assertIsNone(self.resolve(s));self.assertIsNone(self.action(s,source,'ab2'))
        self.cast(s,haste,[source.ref]);self.assertIsNone(self.resolve(s));self.play_land(s,land)
        row=self.action(s,source,'ab2');self.assertIsNotNone(row)
        self.reject(s,{'action_id':row['id'],'targets':[enemy.ref],'pay':'auto'},'A')
        self.activate(s,source,'ab2',[land.ref]);self.assertIsNone(self.resolve(s))
        current=s.state.cards[land.object_id];data=s.engine._effective_card_data(current)
        self.assertIn('land',s.engine._type_parts(data['type_line'])[0]);self.assertIn('elemental',s.engine._type_parts(data['type_line'])[1])
        self.assertIn('haste',s.engine._combat_keywords(current));self.assertEqual((3,3),tuple(s.engine._numeric_stat(current.object_id,k) for k in ['power','toughness']))
        self.private_replay(s,private)

    def test_hour_cast_starts_speed_but_scoped_search_is_present_only_at_max(self):
        for speed in (0,4):
            s=self.start(788060+speed);source=self.add(s.engine,'Hour of Victory',zone='hand')
            if speed:s.state.players['A'].stats['speed']=speed
            else:s.state.players['A'].stats.pop('speed',None)
            private=self.private(s);self.ready(s)
            self.cast(s,source);self.assertIsNone(self.resolve(s))
            self.assertEqual(1 if speed==0 else 4,player_speed(s.state.players['A']))
            tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield' and c.controller=='A'];self.assertEqual(1,len(tokens))
            self.assertEqual((2,2),tuple(s.engine._numeric_stat(tokens[0].object_id,k) for k in ['power','toughness']))
            if speed==0:
                self.assertIsNone(self.action(s,source,'ab3'));self.reject(s,{'action_id':'activate:'+source.ref+':ab3','pay':'auto'},'A')
            else:
                self.activate(s,source,'ab3');self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                s,refs=self.search(s,1);self.assertTrue(refs)
            self.private_replay(s,private)

    def test_moneychanger_cast_upkeep_counter_and_sacrifice_use_last_known_quantity(self):
        for returned in (False,True):
            s=self.start(788070+returned);source=self.add(s.engine,'Icatian Moneychanger',zone='hand')
            return_spell=self.add(s.engine,'Generic Restriction Return',zone='hand') if returned else None
            if returned:self.add(s.engine,'Swamp',ref='restriction-return-mana')
            private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
            self.assertEqual(37,s.state.players['A'].life);self.assertEqual(3,s.state.cards[source.object_id].counters['credit'])
            self.assertIsNone(self.action(s,source,'ab4'))
            self.next_upkeep_trigger(s)
            self.assertIsNone(self.resolve(s));self.assertEqual(4,s.state.cards[source.object_id].counters['credit'])
            self.activate(s,source,'ab4');self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
            if returned:
                # Available lands pay the instant after the turn boundary drained mana.
                self.cast(s,return_spell,[source.ref])
            self.assertIsNone(self.resolve(s));self.assertEqual(38 if returned else 41,s.state.players['A'].life)
            if returned:self.assertEqual(3,s.state.cards[source.object_id].counters['credit'])
            self.private_replay(s,private)

    def test_punctuation_loss_omission_is_killed_by_a_fresh_complete_source(self):
        method='test_all_originals_have_complete_normal_registration'
        with patch('quorune.compiler.activated_mana_nodes._ACTIVATION_RESTRICTION_PREFIX',re.compile(r'^(?P<effect>.+?)\.?\s+activate only ',re.I)):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_fair_native_gate_real_cost_search_filter_and_unconditional_mana(self):
        for amount in (2,3):
            s=self.start(788080+amount);source=self.add(s.engine,"Inventors' Fair",zone='hand')
            for i in range(amount):self.add(s.engine,'Memnite',ref='restriction-fair-artifact-'+str(i))
            found=self.add(s.engine,'Sol Ring',zone='library',ref='restriction-fair-found')
            private=self.private(s);self.ready(s);self.play_land(s,source)
            if amount==2:
                self.assertIsNone(self.action(s,source,'ab3'))
                self.reject(s,{'action_id':'activate:'+source.ref+':ab3','pay':'auto'},'A')
                before=s.state.players['A'].mana_pool['C'];self.activate(s,source,'ab2')
                self.assertEqual(before+1,s.state.players['A'].mana_pool['C'])
            else:
                before=s.state.players['A'].mana_pool['C'];self.activate(s,source,'ab3',pay='manual',payment={'C':4})
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                self.assertEqual(before-4,s.state.players['A'].mana_pool['C'])
                decision=self.resolve(s);self.assertIsNotNone(decision)
                ctx=s.packet('pilot:A',full=True)['decision']['ctx'];options=ctx['search_cards']
                self.assertEqual([found.ref],[r['id'] for r in options])
                s,refs=self.search(s,1,ref=found.ref)
                self.assertEqual('hand',s.state.cards[found.object_id].zone)
            self.private_replay(s,private)

    def test_fair_upkeep_is_once_and_rechecks_the_original_condition(self):
        for response in (False,True):
            s=self.start(788090+response);source=self.add(s.engine,"Inventors' Fair",zone='hand')
            artifacts=[self.add(s.engine,'Memnite',ref='restriction-fair-upkeep-artifact-'+str(i)) for i in range(3)]
            bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand') if response else None
            if response:self.add(s.engine,'Island',seat='B',ref='restriction-fair-island')
            private=self.private(s);self.ready(s);self.play_land(s,source)
            self.next_upkeep_trigger(s)
            self.assertEqual(1,len(s.state.stack))
            if response:self.cast(s,bounce,[artifacts[0].ref],seat='B')
            self.assertIsNone(self.resolve(s));self.assertEqual(40 if response else 41,s.state.players['A'].life)
            self.private_replay(s,private)

    def test_reviewed_views_require_matching_source_cost_condition_and_effect_proofs(self):
        from copy import deepcopy
        from quorune.compiler.program_generation import _generated_programs_from_ir
        from quorune.compiler.activated_ability_catalog import with_activated_ability_catalog
        from quorune.card_programs.current_view_equivalence import shadowed_reviewed_fixed_search_keys,shadowed_reviewed_upkeep_type_count_keys
        from quorune.semantics import SemanticRegistry
        card=self.db.lookup("Inventors' Fair");ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
        generated=_generated_programs_from_ir(self.db,card,ir,trust_level='trusted',capability_registry=self.registry,capability_profile='commander_review',has_rules_derived_trust_carrier=False)
        reviewed=tuple(with_activated_ability_catalog(card,SemanticRegistry().programs_for_oracle(card.oracle_id),reference_programs=generated))
        for helper,event in [(shadowed_reviewed_fixed_search_keys,'activate'),(shadowed_reviewed_upkeep_type_count_keys,'step.begin')]:
            old=next(p for p in reviewed if p.event==event);current=next(p for p in generated if p.event==event)
            self.assertEqual({old.key},helper(card,generated,reviewed))
            for key in ('source_oracle_hash','source_rulings_hash'):
                provenance=dict(old.provenance);provenance[key]='mismatch'
                self.assertFalse(helper(card,generated,(replace(old,provenance=provenance),)))
            changed=deepcopy(old.effects);changed[0]['op']='unowned'
            with self.assertRaisesRegex(ValueError,'Unsupported semantic effect operation'):
                replace(old,effects=changed)
            if event=='activate':
                cost=dict(current.cost_schema);cost['text']='{0}'
                self.assertFalse(helper(card,(replace(current,cost_schema=cost),),reviewed))
                self.assertFalse(helper(card,(replace(current,cost_schema={}),),reviewed))
                cost=dict(current.cost_schema);cost.pop('sacrifice_source')
                self.assertFalse(helper(card,(replace(current,cost_schema=cost),),reviewed))
                handlers=deepcopy(old.handlers);handlers[0]['ability']['activation_conditions']=[]
                self.assertFalse(helper(card,generated,(replace(old,handlers=handlers),)))
            else:
                condition=deepcopy(old.event_condition);condition['all'][-1]['value']=2
                self.assertFalse(helper(card,generated,(replace(old,event_condition=condition),)))
                changed=deepcopy(old.effects);changed[0]['delta']=2
                self.assertFalse(helper(card,generated,(replace(old,effects=changed),)))

    def test_sorcery_effects_are_hidden_with_stack_and_off_turn_but_mana_and_nosh_remain(self):
        s=self.start(788100);bagel=self.add(s.engine,'Bagel and Schmear',zone='hand')
        speaker=self.add(s.engine,'Llanowar Loamspeaker');speaker.acquired_control_turn_count=-1
        land=self.add(s.engine,'Forest');target=self.add(s.engine,'Grizzly Bears',seat='B')
        bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        for i in range(2):self.add(s.engine,'Plains',ref='restriction-offturn-mana-'+str(i))
        private=self.private(s);self.ready(s);self.cast(s,bagel);self.assertIsNone(self.resolve(s))
        share=self.action(s,bagel,'ab1');animation=self.action(s,speaker,'ab2')
        self.assertIsNotNone(share);self.assertIsNotNone(animation)
        self.cast(s,bolt,[target.ref])
        self.assertIsNone(self.action(s,bagel,'ab1'));self.assertIsNone(self.action(s,speaker,'ab2'))
        self.reject(s,{'action_id':share['id'],'targets':[],'pay':'auto'},'A')
        self.reject(s,{'action_id':animation['id'],'targets':[land.ref],'pay':'auto'},'A')
        self.assertEqual('battlefield',s.state.cards[bagel.object_id].zone);self.assertFalse(s.state.cards[bagel.object_id].tapped)
        before=s.state.players['A'].mana_pool['G'];self.activate(s,speaker,'ab1',mana_output={'G':1})
        self.assertEqual(before+1,s.state.players['A'].mana_pool['G']);self.assertIsNone(self.resolve(s))
        self.advance(s,lambda x:x.state.active_player=='B' and x.state.phase=='precombat_main')
        self.priority(s,'A');self.assertIsNone(self.action(s,bagel,'ab1'))
        self.activate(s,bagel,'ab2');self.assertIsNone(self.resolve(s));self.assertEqual(43,s.state.players['A'].life)
        self.private_replay(s,private)

    def test_self_and_selected_land_incarnations_do_not_follow_a_returned_land(self):
        for self_animation in (False,True):
            s=self.start(788110+self_animation)
            source=self.add(s.engine,'Nantuko Monastery' if self_animation else 'Llanowar Loamspeaker')
            source.acquired_control_turn_count=-1
            land=source if self_animation else self.add(s.engine,'Forest')
            self.graveyard(s,['Generic Bound Plains']*7)
            bounce=self.add(s.engine,'Boomerang',seat='B',zone='hand');s.state.players['B'].mana_pool['U']=2
            private=self.private(s);self.ready(s);logical=land.logical_object_id
            self.activate(s,source,'ab2',[] if self_animation else [land.ref])
            self.cast(s,bounce,[land.ref],seat='B');s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual('hand',s.state.cards[land.object_id].zone)
            self.play_land(s,s.state.cards[land.object_id]);current=s.state.cards[land.object_id]
            self.assertNotEqual(logical,current.logical_object_id)
            self.assertNotIn('creature',s.engine._type_parts(s.engine._effective_card_data(current)['type_line'])[0])
            self.private_replay(s,private)

    def test_regeneration_shield_does_not_replace_a_sacrifice(self):
        s=self.start(788120);source=self.add(s.engine,'Krosan Avenger',zone='hand')
        self.graveyard(s,['Generic Bound Plains']*7)
        edict=self.add(s.engine,'Diabolic Edict',seat='B',zone='hand');s.state.players['B'].mana_pool.update({'C':1,'B':1})
        private=self.private(s);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        self.activate(s,source,'ab2');self.assertIsNone(self.resolve(s))
        self.cast(s,edict,['A'],seat='B');decision=self.resolve(s);self.assertIsNotNone(decision)
        command={'action_id':'choose','cards':[source.ref]};self.reject(s,command,'B');s=self.replay(s,load=True)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.private_replay(s,private)

    def test_catalog_scope_and_equivalence_omissions_are_killed_by_fresh_witnesses(self):
        method='test_hour_cast_starts_speed_but_scoped_search_is_present_only_at_max'
        with patch('quorune.compiled_activated_abilities.scoped_program_is_applicable',return_value=True):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_all_originals_have_complete_normal_registration'
        for hook in ['shadowed_reviewed_fixed_search_keys','shadowed_reviewed_upkeep_type_count_keys']:
            with patch('quorune.card_programs.current_view_equivalence.'+hook,return_value=set()):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

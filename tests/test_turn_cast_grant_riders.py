from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.turn_cast_grant_model import (
    TURN_CAST_GRANT_HANDLER,TURN_CAST_EXILE_HANDLER,TURN_CAST_TAPPED_HANDLER,
    TURN_CAST_EXILE_CAPABILITY as EXILE,TURN_CAST_TAPPED_CAPABILITY as TAPPED,TurnCastGrantSpec,
)
from quorune.rules.cast_grant_riders import CAST_GRANT_RIDER_FIELD as FIELD,CastGrantRiderBinding
from scripts.build_test_database import build_fixture_database
import test_turn_cast_grants as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Kess, Dissident Mage','Edgar, Master Machinist')


class TurnCastRiderTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.TurnCastGrantTests.session
    add=controls.TurnCastGrantTests.add
    prepare=controls.TurnCastGrantTests.prepare
    checkpoint=controls.TurnCastGrantTests.checkpoint
    resolve=controls.TurnCastGrantTests.resolve
    replay=controls.TurnCastGrantTests.replay
    binding=controls.TurnCastGrantTests.binding
    actions=controls.TurnCastGrantTests.actions
    cast=controls.TurnCastGrantTests.cast
    reject=controls.TurnCastGrantTests.reject
    private_replay=controls.TurnCastGrantTests.private_replay
    priority=controls.TurnCastGrantTests.priority
    advance=controls.TurnCastGrantTests.advance
    top=controls.TurnCastGrantTests.top
    options=controls.TurnCastGrantTests.options
    grants=controls.TurnCastGrantTests.grants
    use=controls.TurnCastGrantTests.use

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'turn-riders.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/turn-cast-rider-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Bound granted-cast followups',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def one_grant(self,s,spell):
        options=self.grants(s,spell);self.assertEqual(1,len(options),self.options(s,spell));return options[0]

    def test_originals_are_complete_and_each_result_has_an_independent_registered_minimum(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(802000)
        for name,handler,capability in ((NAMES[0],TURN_CAST_EXILE_HANDLER,EXILE),(NAMES[1],TURN_CAST_TAPPED_HANDLER,TAPPED)):
            source=self.add(s.engine,name);bound=self.binding(s,source);self.assertTrue(bound['strict_capability_ready'],bound['blockers'])
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            program=next((p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if any(d.get('handler_id')==handler for d in p.handlers)),None)
            self.assertIsNotNone(program);deps=tuple(d for d in program.capability_dependencies if d!=capability)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+capability,result['blockers'])

    def test_version_one_wire_is_preserved_and_result_descriptors_fail_closed(self):
        from quorune.compiler.turn_cast_grants import turn_cast_grant_handler
        from quorune.semantic_runtime.turn_cast_grants import TurnCastGrantHandler,turn_cast_rider_handlers
        plain='Once during each of your turns, you may cast a creature spell from your graveyard.'
        original=turn_cast_grant_handler(plain)[1];value=original['grant']
        self.assertEqual(1,value['schema_version']);self.assertNotIn('cast_rider',value)
        self.assertEqual(value,TurnCastGrantSpec.from_dict(value).to_dict())
        line='Once during each of your turns, you may cast an artifact spell from your graveyard. If you cast a spell this way, that artifact enters tapped.'
        descriptor=turn_cast_grant_handler(line)[1];self.assertEqual(2,descriptor['grant']['schema_version'])
        self.assertEqual('tapped_entry',turn_cast_rider_handlers()[1].validate(descriptor).cast_rider)
        poisoned={**descriptor,'handler_id':TURN_CAST_GRANT_HANDLER}
        with self.assertRaises(ValueError):TurnCastGrantHandler().validate(poisoned)
        for key,bad in (('cast_rider','none'),('cast_rider','unknown'),('schema_version',True),('without_mana_cost',True),('origins',['hand'])):
            changed=deepcopy(descriptor['grant']);changed[key]=bad
            with self.assertRaises((ValueError,TypeError)):TurnCastGrantSpec.from_dict(changed)
        for bad in (line+' Draw a card.',line.replace('artifact spell','creature spell'),line.replace('enters tapped','enters untapped')):
            self.assertIsNone(turn_cast_grant_handler(bad))
        for name in ('Maestros Ascendancy','Serra Paragon'):
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)
        binding=CastGrantRiderBinding('graveyard_exile','spell:1','A','source:1')
        self.assertEqual(binding,CastGrantRiderBinding.from_dict(binding.to_dict()))
        for key,bad in (('schema_version',True),('rider','unknown'),('caster',''),('logical_object_id','')):
            changed=binding.to_dict();changed[key]=bad
            with self.assertRaises((ValueError,TypeError)):CastGrantRiderBinding.from_dict(changed)

    def test_actual_kess_graveyard_cast_exiles_original_and_spends_its_quota(self):
        s=self.session(802010);source=self.add(s.engine,NAMES[0])
        spell=self.add(s.engine,'Lightning Bolt',zone='graveyard',ref='kess-first');other=self.add(s.engine,'Lightning Bolt',zone='graveyard',ref='kess-other')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'R':2});self.checkpoint(s);self.use(s,spell,self.one_grant(s,spell),{'R':1},['B'])
        self.assertIn(FIELD,s.state.cards[spell.object_id].annotations)
        self.assertEqual('graveyard_exile',s.state.cards[spell.object_id].annotations[FIELD]['rider'])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('exile',s.state.cards[spell.object_id].zone);self.assertNotIn(FIELD,s.state.cards[spell.object_id].annotations)
        self.assertEqual(37,s.state.players['B'].life);self.assertEqual([],self.options(s,other))
        self.assertIn('flying',s.engine._combat_keywords(s.state.cards[source.object_id]));self.private_replay(s,private)

    def test_actual_normal_hand_cast_has_no_replacement_and_preserves_kess_use(self):
        s=self.session(802020);self.add(s.engine,NAMES[0]);hand=self.add(s.engine,'Lightning Bolt',zone='hand',ref='kess-hand')
        grave=self.add(s.engine,'Lightning Bolt',zone='graveyard',ref='kess-grave');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'R':2});self.checkpoint(s);normal=next(o for o in self.options(s,hand) if o['id']=='normal')
        self.use(s,hand,normal,{'R':1},['B']);self.assertNotIn(FIELD,s.state.cards[hand.object_id].annotations)
        self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[hand.object_id].zone)
        self.use(s,grave,self.one_grant(s,grave),{'R':1},['B']);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('exile',s.state.cards[grave.object_id].zone);self.assertEqual(34,s.state.players['B'].life);self.private_replay(s,private)

    def test_counter_and_source_departure_keep_kess_bound_to_the_original_spell(self):
        for removed in (False,True):
            s=self.session(802030+removed);source=self.add(s.engine,NAMES[0]);spell=self.add(s.engine,'Lightning Bolt',zone='graveyard')
            response=self.add(s.engine,'Murder' if removed else 'Counterspell',seat='B',zone='hand')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            s.state.players['B'].mana_pool.update({'C':1,'B':2} if removed else {'U':2})
            self.prepare(s,mana={'R':1});self.checkpoint(s);self.use(s,spell,self.one_grant(s,spell),{'R':1},['B'])
            self.cast(s,'B',response,[source.ref] if removed else [s.state.stack[0].ref])
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[spell.object_id].zone)
            self.assertEqual(37 if removed else 40,s.state.players['B'].life)
            if removed:self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
            self.private_replay(s,private)

    def test_flashback_is_an_independent_permission_and_preserves_kess_allowance(self):
        s=self.session(802040);self.add(s.engine,NAMES[0]);spell=self.add(s.engine,'Think Twice',zone='graveyard')
        other=self.add(s.engine,'Lightning Bolt',zone='graveyard');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':2,'U':1,'R':1});self.checkpoint(s)
        all_options=self.options(s,spell);flashback=next((o for o in all_options if o['id']=='flashback'),None)
        self.assertIsNotNone(flashback,all_options);self.assertNotIn('permission_source',flashback)
        self.assertTrue(all(o['requirements']['GENERIC']==1 for o in all_options if o.get('permission_source')))
        self.use(s,spell,flashback,{'C':2,'U':1});self.assertNotIn(FIELD,s.state.cards[spell.object_id].annotations)
        self.assertIsNone(self.resolve(s));self.assertEqual('exile',s.state.cards[spell.object_id].zone)
        self.use(s,other,self.one_grant(s,other),{'R':1},['B']);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(37,s.state.players['B'].life);self.private_replay(s,private)

    def test_buyback_and_later_discard_do_not_inherit_the_previous_spell_replacement(self):
        s=self.session(802050);self.add(s.engine,NAMES[0]);spell=self.add(s.engine,'Capsize',zone='graveyard')
        target=self.add(s.engine,'Generic Bound Body',seat='B');discard=self.add(s.engine,'Thrill of Possibility',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':5,'U':2,'R':1});self.checkpoint(s)
        options=[o for o in self.grants(s,spell) if 'Buyback' in o.get('label','')];self.assertEqual(1,len(options),self.options(s,spell))
        self.use(s,spell,options[0],{'C':4,'U':2},[target.ref]);self.assertIsNone(self.resolve(s))
        current=s.state.cards[spell.object_id];self.assertEqual('hand',current.zone);self.assertNotIn(FIELD,current.annotations)
        normal=next(o for o in self.options(s,discard) if o['id']=='normal')
        self.use(s,discard,normal,{'C':1,'R':1},discard_cards=[current.ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone);self.private_replay(s,private)

    def test_spell_copy_has_no_created_replacement_or_additional_source_use(self):
        s=self.session(802060);self.add(s.engine,NAMES[0]);spell=self.add(s.engine,'Lightning Bolt',zone='graveyard')
        copy=self.add(s.engine,'Reverberate',seat='B',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool['R']=2;self.prepare(s,mana={'R':1});self.checkpoint(s)
        self.use(s,spell,self.one_grant(s,spell),{'R':1},['B']);original=s.state.stack[0].ref
        self.cast(s,'B',copy,[original]);pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind)
        s=self.replay(s,load=True);result=s.act('pilot:B',{'action_id':'choose','targets':['A']});self.assertTrue(result.ok,result.summary)
        copies=[i for i in s.state.stack if i.ref!=original];self.assertEqual(1,len(copies))
        if copies[0].card_object_id:self.assertNotIn(FIELD,s.state.cards[copies[0].card_object_id].annotations)
        self.assertIsNone(self.resolve(s));self.assertEqual(37,s.state.players['A'].life);self.assertEqual(37,s.state.players['B'].life)
        self.assertEqual('exile',s.state.cards[spell.object_id].zone);self.private_replay(s,private)

    def test_edgar_normal_hand_and_granted_casts_have_distinct_entry_states(self):
        s=self.session(802070);self.add(s.engine,NAMES[1]);hand=self.add(s.engine,'Sol Ring',zone='hand',ref='edgar-hand')
        grave=self.add(s.engine,'Sol Ring',zone='graveyard',ref='edgar-grave');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':2});self.checkpoint(s);normal=next(o for o in self.options(s,hand) if o['id']=='normal')
        self.use(s,hand,normal,{'C':1});self.assertIsNone(self.resolve(s));self.assertFalse(s.state.cards[hand.object_id].tapped)
        self.use(s,grave,self.one_grant(s,grave),{'C':1});s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        current=s.state.cards[grave.object_id];self.assertEqual('battlefield',current.zone);self.assertTrue(current.tapped)
        self.assertNotIn(FIELD,current.annotations);self.private_replay(s,private)

    def test_edgar_departure_does_not_erase_the_artifacts_bound_tapped_entry(self):
        s=self.session(802080);source=self.add(s.engine,NAMES[1]);spell=self.add(s.engine,'Sol Ring',zone='graveyard')
        murder=self.add(s.engine,'Murder',seat='B',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool.update({'C':1,'B':2});self.prepare(s,mana={'C':1});self.checkpoint(s)
        self.use(s,spell,self.one_grant(s,spell),{'C':1});self.cast(s,'B',murder,[source.ref])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[spell.object_id].zone);self.assertTrue(s.state.cards[spell.object_id].tapped)
        self.private_replay(s,private)

    def test_edgar_copy_entry_choice_preserves_the_created_tapped_replacement(self):
        s=self.session(802085);self.add(s.engine,NAMES[1]);target=self.add(s.engine,'Sol Ring')
        spell=self.add(s.engine,'Sculpting Steel',zone='graveyard');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3});self.checkpoint(s);self.use(s,spell,self.one_grant(s,spell),{'C':3})
        pending=self.resolve(s);self.assertIsNotNone(pending);self.assertEqual('replacement.order',pending.kind)
        options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
        selected=next((o for o in options if target.ref in o['id'] and not o['id'].startswith('decline:')),None)
        self.assertIsNotNone(selected,options);command={'action_id':'choose','replacement':selected['id']}
        self.reject(s,command,'pilot:B');s=self.replay(s,load=True)
        accepted=s.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(self.resolve(s))
        current=s.state.cards[spell.object_id];self.assertTrue(current.tapped);self.assertEqual('Sol Ring',s.engine.display_name(current.object_id))
        self.assertNotIn(FIELD,current.annotations);self.private_replay(s,private)

    def test_edgar_attack_uses_current_own_artifact_maximum_once_at_resolution(self):
        for remove in (False,True):
            s=self.session(802090+remove);source=self.add(s.engine,NAMES[1]);lotus=self.add(s.engine,'Gilded Lotus')
            self.add(s.engine,'Darksteel Ingot',seat='B');spell=self.add(s.engine,'Sol Ring',zone='graveyard')
            response=self.add(s.engine,'Disenchant',seat='B',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            for i in range(2):self.add(s.engine,'Generic Bound Plains',seat='B',ref='edgar-response-plains-'+str(i))
            self.prepare(s,mana={'C':1});self.checkpoint(s);self.use(s,spell,self.one_grant(s,spell),{'C':1});self.assertIsNone(self.resolve(s))
            self.advance(s,lambda q:q.state.pending_decision is not None and q.state.pending_decision.kind=='combat.attackers')
            result=s.act('pilot:A',{'action_id':'attack','attackers':{s.state.cards[source.object_id].ref:'B'}});self.assertTrue(result.ok,result.summary)
            if remove:self.cast(s,'B',response,[lotus.ref])
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            expected=int(self.db.lookup(NAMES[1]).power)+(1 if remove else 5)
            self.assertEqual(expected,int(s.engine._effective_card_data(s.state.cards[source.object_id])['power']))
            self.private_replay(s,private)

    def test_created_replacements_use_ordinary_order_and_sealed_stack_identity(self):
        from quorune.semantic_runtime.zone_replacements import capture_zone_change_replacement_snapshot,prepare_zone_change_replacement_snapshot
        from quorune.replacement import ReplacementEffect,ReplacementClass,SetField
        for tapped in (False,True):
            s=self.session(802100+tapped);self.add(s.engine,NAMES[1] if tapped else NAMES[0])
            spell=self.add(s.engine,'Sol Ring' if tapped else 'Lightning Bolt',zone='graveyard')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'C':1} if tapped else {'R':1});self.checkpoint(s)
            self.use(s,spell,self.one_grant(s,spell),{'C':1} if tapped else {'R':1},[] if tapped else ['B'])
            destination='battlefield' if tapped else 'graveyard'
            snapshot=capture_zone_change_replacement_snapshot(s.engine,((spell.object_id,destination),))
            created=[e for e in snapshot.effects if e.effect_id.startswith('cast-grant:')];self.assertEqual(1,len(created))
            self.assertEqual(ReplacementClass.OTHER,created[0].replacement_class)
            current=s.state.cards[spell.object_id]
            other=ReplacementEffect(effect_id='unit-competing-entry-or-zone',source_id='unit-source',event_kind='zone.change',
                replacement_class=ReplacementClass.OTHER,conditions={'origin':{'eq':'stack'},'destination':{'eq':destination},
                    'object_ref':{'eq':current.ref},'logical_object_id':{'eq':current.logical_object_id}},
                operations=(SetField('tapped',False) if tapped else SetField('destination','hand'),),label='competing ordinary result')
            competing=replace(snapshot,effects=(*snapshot.effects,other))
            first=prepare_zone_change_replacement_snapshot(competing,selections=[created[0].effect_id])[spell.object_id]
            second=prepare_zone_change_replacement_snapshot(competing,selections=[other.effect_id])[spell.object_id]
            if tapped:self.assertFalse(first.entry_tapped);self.assertTrue(second.entry_tapped)
            else:self.assertEqual('exile',first.destination);self.assertEqual('hand',second.destination)
            self.private_replay(s,private)

    def test_verified_rider_withdrawal_and_fresh_marking_omissions_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        baseline=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        from quorune import zone_object_state as owner
        for name,capability,method in ((NAMES[0],EXILE,'test_actual_kess_graveyard_cast_exiles_original_and_spends_its_quota'),
                (NAMES[1],TAPPED,'test_edgar_normal_hand_and_granted_casts_have_distinct_entry_states')):
            raw=deepcopy(baseline);next(c for c in raw['capabilities'] if c['id']==capability).update(status='blocked',
                blockers=['withdrawn conditional casting consequence'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+capability+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
            with patch.object(owner,'mark_cast_grant_rider',return_value=None):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_fresh_replacement_factory_omission_is_killed_by_the_final_zone(self):
        from quorune.rules import cast_grant_riders as owner
        method='test_actual_kess_graveyard_cast_exiles_original_and_spends_its_quota'
        with patch.object(owner,'cast_grant_rider_replacements',return_value=()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

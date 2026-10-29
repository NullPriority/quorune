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
from quorune.resolution_mana_model import ResolutionManaChoiceSpec,RESOLUTION_MANA_CHOICE_CAPABILITY as CAP
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_turn_cast_grants as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Quirion Sentinel','Manamorphose','Lotus Cobra','Crumbling Vestige','Traitorous Greed','Outcaster Trailblazer')


class ResolutionManaChoiceTests(unittest.TestCase):
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
    options=controls.TurnCastGrantTests.options
    use=controls.TurnCastGrantTests.use
    top=controls.TurnCastGrantTests.top

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'resolution-mana.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/resolution-mana-choice-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Resolution mana choices',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def mana_choice(self,s):
        for _ in range(12):
            pending=self.resolve(s);self.assertIsNotNone(pending)
            if pending.kind=='trigger.order':
                triggers=s.packet('pilot:A',full=True)['decision']['ctx']['triggers']
                command={'action_id':'order','triggers':[r['id'] for r in triggers]}
                result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);continue
            self.assertEqual('semantic.choice',pending.kind)
            context=s.packet('pilot:A',full=True)['decision']['ctx'];self.assertEqual('choose_mana',context['operation'])
            return context
        self.fail('Resolution mana choice did not arrive')

    def choose(self,s,**response):
        command={'action_id':'choose',**response};self.reject(s,command,'pilot:D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)

    def test_originals_have_normal_complete_admission_and_independent_runtime_minimum(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(805000)
        for name in NAMES:
            card=self.add(s.engine,name);binding=self.binding(s,card)
            self.assertTrue(binding['strict_capability_ready'],(name,binding['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            program=next((p for p in s.engine.semantics.programs_for_oracle(card.oracle_id) if CAP in p.capability_dependencies),None)
            self.assertIsNotNone(program,name);deps=tuple(c for c in program.capability_dependencies if c!=CAP)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            runtime=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(runtime['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,runtime['blockers'])

    def test_closed_payloads_colors_and_existing_fixed_symbol_bundles_are_distinct(self):
        from quorune.compiler.fixed_resolution_mana import fixed_resolution_mana_effect_template as compile_mana
        for text,mode,amount in (('Add one mana of any color.','one_color',1),('Add two mana of any one color.','one_color',2),
                ('Add two mana in any combination of colors.','combination',2)):
            compiled=compile_mana(text);self.assertIsNotNone(compiled);effect=compiled[1][0]
            self.assertEqual(ResolutionManaChoiceSpec(mode,amount),ResolutionManaChoiceSpec.from_effect(effect))
            self.assertEqual(effect,ResolutionManaChoiceSpec.from_effect(json.loads(json.dumps(effect))).effect())
        for bad in ('Add zero mana of any color.','Add eleven mana of any color.','Add X mana of any color.',
                'Add one mana of any color an opponent could produce.','Add one mana of any type.',
                'Add two mana of any color. Spend this mana only to cast creature spells.'):
            self.assertIsNone(compile_mana(bad),bad)
        self.assertEqual(({'op':'mana','player':'$controller','color':'B','amount':3,'source':'$source'},),compile_mana('Add {B}{B}{B}.')[1])
        for change in ({'amount':True},{'amount':0},{'amount':11},{'schema_version':1},{'selection':['combination']},{'extra':True}):
            with self.assertRaises((TypeError,ValueError)):ResolutionManaChoiceSpec.from_effect({**ResolutionManaChoiceSpec('combination',2).effect(),**change})
        from quorune.resolution_mana_model import resolution_mana_choice_dependencies
        stripped={k:v for k,v in ResolutionManaChoiceSpec('combination',2).effect().items() if k not in {'schema_version','selection'}}
        with self.assertRaises(ValueError):resolution_mana_choice_dependencies(stripped)

    def test_independent_siblings_and_genuine_activation_rules_remain_held(self):
        for name in ('Berta, Wise Extrapolator', 'Realm-Scorcher Hellkite', 'Mox Opal'):
            promoted=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',promoted.status,(name,promoted.material_residuals))
        held=('Apex of Power','Boommobile','Cosmic Crucible','Branch of Vitu-Ghazi','Nissa, Resurgent Animist')
        for name in held:
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,name);self.assertTrue(ir.material_residuals,name)

    def test_actual_sentinel_entry_chooses_each_color_and_can_spend_the_result(self):
        for i,color in enumerate('WUBRG'):
            s=self.session(805010+i);source=self.add(s.engine,'Quirion Sentinel',zone='hand')
            body=self.add(s.engine,'Generic Bound Body',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'C':1,'G':1});self.checkpoint(s);self.cast(s,'A',source);context=self.mana_choice(s)
            self.assertEqual('one_color',context['selection']);self.assertEqual(1,context['amount']);self.assertTrue(s.state.stack)
            self.reject(s,{'action_id':'choose','choice':'C'})
            s=self.replay(s,load=True);self.choose(s,choice=color);self.assertIsNone(self.resolve(s))
            self.assertEqual(1,s.state.players['A'].mana_pool[color]);self.assertEqual(1,sum(s.state.players['A'].mana_pool.values()))
            if color=='W':
                option=next((o for o in self.options(s,body) if o['id']=='normal'),None);self.assertIsNotNone(option)
                self.use(s,body,option,{'W':1});self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[body.object_id].zone)
            self.private_replay(s,private)

    def test_actual_manamorphose_allocates_then_draws_exactly_once_and_spends_mana(self):
        s=self.session(805020);spell=self.add(s.engine,'Manamorphose',zone='hand');body=self.add(s.engine,'Generic Bound Body',zone='hand')
        drawn=self.top(s,'Sol Ring','morphose-top');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s);self.cast(s,'A',spell);context=self.mana_choice(s)
        self.assertEqual(('combination',2),(context['selection'],context['amount']));self.assertEqual('library',s.state.cards[drawn.object_id].zone)
        schema=context['legal_actions'][0]['choice_schema'];self.assertEqual('mana_bundle',schema['type']);self.assertEqual(list('WUBRG'),schema['allowed_colors'])
        from quorune.choice_forms import build_action_form,delegated_choice_fields
        action=context['legal_actions'][0];form=build_action_form(action,decision_kind='semantic.choice',context=context)
        field=next((f for f in form['fields'] if f['name']=='mana'),None);self.assertIsNotNone(field)
        self.assertEqual('mana_modes',field['control']);self.assertEqual(2,field['total']);self.assertFalse(field['single_color'])
        self.assertIn('mana',delegated_choice_fields(action,decision_kind='semantic.choice',context=context))
        s=self.replay(s,load=True);self.choose(s,mana={'W':1,'U':1});self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[drawn.object_id].zone);self.assertEqual('graveyard',s.state.cards[spell.object_id].zone)
        self.assertEqual({'W':1,'U':1},{k:v for k,v in s.state.players['A'].mana_pool.items() if v})
        option=next((o for o in self.options(s,body) if o['id']=='normal'),None);self.assertIsNotNone(option)
        self.use(s,body,option,{'W':1});self.assertIsNone(self.resolve(s));self.assertEqual(1,s.state.players['A'].mana_pool['U']);self.private_replay(s,private)

    def test_invalid_partial_unknown_and_colorless_allocations_roll_back(self):
        s=self.session(805030);spell=self.add(s.engine,'Manamorphose',zone='hand');drawn=self.top(s,'Sol Ring','rejected-top')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s);self.cast(s,'A',spell);self.mana_choice(s)
        for allocation in ({},{'R':1},{'R':3},{'C':2},{'R':True,'G':1},{'R':-1,'G':3},{'R':1.5,'G':0.5},{'R':2,'unowned':0}):
            self.reject(s,{'action_id':'choose','mana':allocation});self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
            self.assertEqual('library',s.state.cards[drawn.object_id].zone)
        self.reject(s,{'action_id':'choose','mana':{'R':2},'player':'B'})
        s=self.replay(s,load=True);self.choose(s,mana={'R':2});self.assertIsNone(self.resolve(s));self.private_replay(s,private)

    def test_actual_landfall_uses_stack_and_locked_controller_after_source_returns_to_hand(self):
        s=self.session(805040);source=self.add(s.engine,'Lotus Cobra');land=self.add(s.engine,'Generic Bound Plains',zone='hand',ref='cobra-land')
        island=self.add(s.engine,'Island');bounce=self.add(s.engine,'Unsummon',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s);self.checkpoint(s)
        offer=next((a for a in self.actions(s) if a.get('kind')=='play_land' and a.get('card')==land.ref),None);self.assertIsNotNone(offer)
        result=s.act('pilot:A',{'action_id':offer['id']});self.assertTrue(result.ok,result.summary);self.assertTrue(s.state.stack)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.cast(s,'A',bounce,[source.ref])
        self.mana_choice(s);self.assertEqual('hand',s.state.cards[source.object_id].zone);self.assertTrue(s.state.cards[island.object_id].tapped)
        s=self.replay(s,load=True);self.choose(s,choice='G');self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.players['A'].mana_pool['G']);self.assertEqual(0,sum(s.state.players['B'].mana_pool.values()));self.private_replay(s,private)

    def test_actual_vestige_enters_tapped_and_retains_normal_colorless_ability(self):
        s=self.session(805050);land=self.add(s.engine,'Crumbling Vestige',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s);self.checkpoint(s);offer=next((a for a in self.actions(s) if a.get('kind')=='play_land' and a.get('card')==land.ref),None);self.assertIsNotNone(offer)
        result=s.act('pilot:A',{'action_id':offer['id']});self.assertTrue(result.ok,result.summary);self.mana_choice(s)
        self.assertTrue(s.state.cards[land.object_id].tapped);self.assertTrue(s.state.stack)
        s=self.replay(s,load=True);self.choose(s,choice='B');self.assertIsNone(self.resolve(s));self.assertEqual(1,s.state.players['A'].mana_pool['B'])
        ir=compile_oracle_card(self.db.lookup('Crumbling Vestige'),capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(any(n.kind=='mana_ability' for face in ir.faces for n in face.nodes));self.private_replay(s,private)

    def test_current_land_entry_views_require_exact_source_and_preserve_legacy_payloads(self):
        from quorune.card_programs.resolution_mana_overlay import shadowed_reviewed_resolution_mana_keys
        from quorune.semantics import SemanticRegistry
        s=self.session(805055);card=self.add(s.engine,'Lotus Cobra');record=self.db.lookup('Lotus Cobra')
        current=next((p for p in s.engine.semantics.programs_for_oracle(card.oracle_id) if CAP in p.capability_dependencies),None)
        self.assertIsNotNone(current);legacy=SemanticRegistry().programs_for_oracle(card.oracle_id)
        keys=shadowed_reviewed_resolution_mana_keys(record,[current],legacy)
        self.assertEqual(2,len(keys));self.assertEqual({p.key for p in legacy},keys)
        for changes in ({'event_condition':None},{'trust_level':'provisional'},
                {'provenance':{**current.provenance,'source_oracle_hash':'changed'}},
                {'effects':[ResolutionManaChoiceSpec('combination',1).effect()]}):
            self.assertEqual(set(),shadowed_reviewed_resolution_mana_keys(record,[replace(current,**changes)],legacy))
        trigger=next((p for p in legacy if p.event=='land.enter'),None);self.assertIsNotNone(trigger)
        changed=replace(trigger,effects=[{'op':'choose_mana','player':'$controller','amount':2}])
        self.assertEqual(set(),shadowed_reviewed_resolution_mana_keys(record,[current],[changed,*[p for p in legacy if p.key!=trigger.key]]))
        self.assertEqual([{'op':'choose_mana','player':'$controller','amount':1}],trigger.effects)
        empty=next((p for p in legacy if p.event=='resolve'),None);self.assertIsNotNone(empty)
        changed_empty=replace(empty,destination='exile')
        selected=shadowed_reviewed_resolution_mana_keys(record,[current],[changed_empty,trigger])
        self.assertNotIn(empty.key,selected);self.assertIn(trigger.key,selected)
        self.assertTrue(self.binding(s,card)['strict_capability_ready'])

    def test_foreign_land_does_not_trigger_and_real_stifle_counters_own_land_trigger(self):
        for foreign in (True,False):
            s=self.session(805057+foreign);source=self.add(s.engine,'Lotus Cobra')
            land=self.add(s.engine,'Generic Bound Plains',seat='B' if foreign else 'A',zone='hand',ref='observed-land')
            self.add(s.engine,'Island',seat='B');counter=self.add(s.engine,'Stifle',seat='B',zone='hand')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            seat='B' if foreign else 'A';self.prepare(s,actor=seat,active=seat);self.checkpoint(s)
            actions=s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
            offer=next((a for a in actions if a.get('kind')=='play_land' and a.get('card')==land.ref),None);self.assertIsNotNone(offer)
            result=s.act('pilot:'+seat,{'action_id':offer['id']});self.assertTrue(result.ok,result.summary)
            if foreign:self.assertFalse(s.state.stack)
            else:
                self.assertTrue(s.state.stack);self.cast(s,'B',counter,[s.state.stack[-1].ref]);self.assertIsNone(self.resolve(s))
            self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.assertTrue(self.binding(s,source)['strict_capability_ready']);self.private_replay(s,private)

    def test_actual_greed_preserves_control_untap_haste_then_adds_one_chosen_color(self):
        s=self.session(805060);spell=self.add(s.engine,'Traitorous Greed',zone='hand');body=self.add(s.engine,'Generic Bound Body',seat='B')
        body.tapped=True;private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'R':1});self.checkpoint(s);self.cast(s,'A',spell,[body.ref]);context=self.mana_choice(s)
        self.assertEqual(2,context['amount']);current=s.state.cards[body.object_id]
        self.assertEqual('A',current.controller);self.assertFalse(current.tapped);self.assertIn('haste',s.engine._combat_keywords(current))
        s=self.replay(s,load=True);self.choose(s,choice='U');self.assertIsNone(self.resolve(s));self.assertEqual(2,s.state.players['A'].mana_pool['U'])
        self.advance(s,lambda q:q.state.active_player!='A');self.assertEqual('B',s.state.cards[body.object_id].controller)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.private_replay(s,private)

    def test_actual_trailblazer_retains_power_trigger_and_mana_entry_result(self):
        s=self.session(805070);source=self.add(s.engine,'Outcaster Trailblazer',zone='hand');drawn=self.top(s,'Sol Ring','trailblazer-top')
        second=self.add(s.engine,'Outcaster Trailblazer',zone='hand',ref='trailblazer-second');self.add(s.engine,'Forest');self.add(s.engine,'Mountain')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':2,'G':1});self.checkpoint(s);self.cast(s,'A',source);self.mana_choice(s)
        s=self.replay(s,load=True);self.choose(s,choice='R');self.assertIsNone(self.resolve(s));self.assertEqual(1,s.state.players['A'].mana_pool['R'])
        self.assertEqual('library',s.state.cards[drawn.object_id].zone)
        self.cast(s,'A',second);self.mana_choice(s);self.choose(s,choice='G');self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[drawn.object_id].zone);self.assertEqual(4,int(s.engine._effective_card_data(s.state.cards[source.object_id])['power']));self.private_replay(s,private)

    def test_verified_withdrawal_and_fresh_production_omissions_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(c for c in raw['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['withdrawn fixed color result'],dependency_fail_closed_status='not_run')
        registry=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in ('Quirion Sentinel','Manamorphose'):
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+CAP+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune.semantic_choices import resolution_mana as owner
        from quorune.semantic_choices.model import SemanticChoiceCompletion
        method='test_actual_manamorphose_allocates_then_draws_exactly_once_and_spends_mana'
        with patch.object(owner,'complete_resolution_mana',return_value=SemanticChoiceCompletion()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

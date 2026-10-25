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
from quorune.nonmana_kicker import FixedNonmanaKickerSpec,NONMANA_KICKER_CAPABILITY as CAP,nonmana_kicker_dependencies
from quorune.compiler.nonmana_kicker import compile_nonmana_kicker
from scripts.build_test_database import build_fixture_database
import test_recipient_modifiers as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Phyrexian Scuta','Arctic Merfolk','Vicious Offering','Final Flourish','Stomped by the Foot',"Vayne's Treachery")


class NonmanaKickerTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.RecipientModifierTests.session
    start=controls.RecipientModifierTests.start
    add=controls.RecipientModifierTests.add
    ready=controls.RecipientModifierTests.ready
    prepare=controls.RecipientModifierTests.prepare
    checkpoint=controls.RecipientModifierTests.checkpoint
    resolve=controls.RecipientModifierTests.resolve
    replay=controls.RecipientModifierTests.replay
    binding=controls.RecipientModifierTests.binding
    priority=controls.RecipientModifierTests.priority
    private_replay=controls.RecipientModifierTests.private_replay
    private=controls.RecipientModifierTests.private
    reject=controls.RecipientModifierTests.reject
    stats=controls.RecipientModifierTests.stats
    until=controls.RecipientModifierTests.until

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'nonmana-kicker.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/nonmana-kicker-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Optional nonmana prices',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def cast(self,s,card,targets=(),*,kicked=False,seat='A',**extra):
        self.priority(s,seat);rows=s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
        row=next((a for a in rows if a['id']=='cast:'+card.ref),None);self.assertIsNotNone(row,card.printed_name)
        command={'action_id':row['id'],'targets':list(targets),'pay':'auto',**({'cost_option':'kicked'} if kicked else {}),**extra}
        self.reject(s,command,'D');result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def test_cost_models_preserve_optional_leaves_mana_and_closed_legacy_payloads(self):
        from quorune.kicker import compile_fixed_mana_kicker
        from quorune.rules.tap_additional_cost import FixedTapAdditionalCost
        for line,kind in [('Kicker—Pay 3 life.','fixed_life_payment'),('Kicker—Sacrifice two lands.','zone_change'),
                ('Kicker—Return a creature you control to its owner\'s hand.','zone_change'),
                ('Kicker—{2}{B}, Discard a creature card.','zone_change'),('Kicker—Tap an untapped Vampire you control.','fixed_tap')]:
            spec=compile_nonmana_kicker(line,oracle_line=line,line_index=0);self.assertIsNotNone(spec,line)
            self.assertEqual(kind,spec.to_dict()['additional_costs'][0]['kind']);self.assertEqual(spec,FixedNonmanaKickerSpec.from_dict(spec.to_dict()))
            option=spec.cast_cost_option();self.assertEqual('kicker',option['kind']);self.assertEqual('kicked',option['id'])
            self.assertIn(CAP,nonmana_kicker_dependencies(spec))
            for update in ({'schema_version':True},{'additional_costs':[]},{'mana_cost':{'GENERIC':0}},{'unknown':1}):
                with self.assertRaises(ValueError):FixedNonmanaKickerSpec.from_dict({**spec.to_dict(),**update})
        for line in ['Kicker—Pay X life.','Kicker—Sacrifice a hungry moon.','Kicker—{X}, Sacrifice a creature.','Kicker {R} and/or {G}']:
            self.assertIsNone(compile_nonmana_kicker(line,oracle_line=line,line_index=0),line)
        old=compile_fixed_mana_kicker(material_line='Kicker {2}{G}',oracle_line='Kicker {2}{G}',line_index=0)
        self.assertEqual({'id':'kicked','kind':'kicker','label':'Kicker {2}{G}','requirements':dict(old.mana_cost),'kicker_fingerprint':old.fingerprint},old.cast_cost_option())
        v1={'schema_version':1,'kind':'fixed_tap','count':1,'permanent_types':['creature']}
        self.assertEqual(v1,FixedTapAdditionalCost.from_descriptor(v1).to_descriptor())
        for value in ({**v1,'schema_version':True},{**v1,'schema_version':2,'creature_subtypes':['Vampire']},
                {**v1,'schema_version':2,'creature_subtypes':['vampire','vampire']}):
            with self.assertRaises(ValueError):FixedTapAdditionalCost.from_descriptor(value)

    def test_six_originals_have_complete_registration_and_independent_payment_minima(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.start(791000)
        for name in NAMES:
            c=self.add(s.engine,name,zone='hand');bound=self.binding(s,c)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            program=next(p for p in s.engine.semantics.programs_for_oracle(c.oracle_id) if CAP in p.capability_dependencies)
            for cap in program.capability_dependencies:
                if cap!=CAP and not cap.startswith('casting.additional_cost.'):continue
                deps=tuple(d for d in program.capability_dependencies if d!=cap)
                changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,result['blockers'])

    def test_scuta_optional_life_is_paid_before_entry_counters_and_insufficient_life_rolls_back(self):
        for kicked,life in [(False,40),(True,40),(True,2)]:
            s=self.start(791010+kicked+life);source=self.add(s.engine,'Phyrexian Scuta',zone='hand')
            s.state.players['A'].life=life;private=self.private(s);self.ready(s)
            if life==2:
                self.reject(s,{'action_id':'cast:'+source.ref,'cost_option':'kicked','pay':'auto'},'A')
                self.assertEqual('hand',s.state.cards[source.object_id].zone);self.assertEqual(2,s.state.players['A'].life)
            else:
                self.cast(s,source,kicked=kicked);self.assertEqual(life-(3 if kicked else 0),s.state.players['A'].life)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(2 if kicked else 0,s.state.cards[source.object_id].counters.get('+1/+1',0))
                self.assertEqual((5,5) if kicked else (3,3),self.stats(s,source))
            self.private_replay(s,private)

    def test_arctic_return_cost_uses_current_control_owner_destination_and_fresh_entry(self):
        for kicked in (False,True):
            s=self.start(791020+kicked);source=self.add(s.engine,'Arctic Merfolk',zone='hand')
            payer=self.add(s.engine,'Generic Bound Body');enemy=self.add(s.engine,'Grizzly Bears',seat='B')
            private=self.private(s);self.ready(s)
            if kicked:
                self.reject(s,{'action_id':'cast:'+source.ref,'cost_option':'kicked','return_cards':[enemy.ref],'pay':'auto'},'A')
            self.cast(s,source,kicked=kicked,**({'return_cards':[payer.ref]} if kicked else {}))
            self.assertEqual('hand' if kicked else 'battlefield',s.state.cards[payer.object_id].zone)
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual(1 if kicked else 0,s.state.cards[source.object_id].counters.get('+1/+1',0));self.private_replay(s,private)

    def test_four_sacrifice_originals_pay_real_creature_or_artifact_cost_and_replace_the_result(self):
        for index,name in enumerate(('Vicious Offering','Final Flourish','Stomped by the Foot',"Vayne's Treachery")):
            for kicked in (False,True):
                s=self.start(791030+index*2+kicked);source=self.add(s.engine,name,zone='hand')
                payer=self.add(s.engine,'Grizzly Bears' if name=='Vicious Offering' else 'Sol Ring')
                target=self.add(s.engine,'Generic Bound Body',seat='B');private=self.private(s);self.ready(s)
                self.cast(s,source,[target.ref],kicked=kicked,**({'sacrifice_cards':[payer.ref]} if kicked else {}))
                self.assertEqual('graveyard' if kicked else 'battlefield',s.state.cards[payer.object_id].zone)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                penalty=(6 if name in {'Final Flourish',"Vayne's Treachery"} else 5) if kicked else 2
                if penalty==6:self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
                else:self.assertEqual((2-penalty,6-penalty),self.stats(s,target))
                self.private_replay(s,private)

    def test_subtype_tap_cost_uses_current_untapped_controlled_vampire_without_a_tap_symbol(self):
        s=self.start(791060);source=self.add(s.engine,'Generic Kicker Vampire Price',zone='hand')
        vampire=self.add(s.engine,'Vampire Interloper',zone='hand')
        wrong=self.add(s.engine,'Grizzly Bears');foreign=self.add(s.engine,'Vampire Interloper',seat='B',ref='kicker-foreign-vampire')
        private=self.private(s);self.ready(s);self.cast(s,vampire);self.assertIsNone(self.resolve(s))
        for ref in (wrong.ref,foreign.ref):
            self.reject(s,{'action_id':'cast:'+source.ref,'cost_option':'kicked','tap_cost_cards':[ref],'pay':'auto'},'A')
        self.cast(s,source,kicked=True,tap_cost_cards=[vampire.ref]);self.assertTrue(s.state.cards[vampire.object_id].tapped)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual((3,3),self.stats(s,source));self.private_replay(s,private)

    def test_fixed_mana_discard_and_two_land_prices_pay_all_parts_and_keep_the_source_out_of_hand_payment(self):
        for name,field in [('Generic Kicker Discard Price','discard_cards'),('Generic Kicker Two Land Price','sacrifice_cards')]:
            s=self.start(791070+(field=='sacrifice_cards'));source=self.add(s.engine,name,zone='hand')
            payers=([self.add(s.engine,'Grizzly Bears',zone='hand')] if field=='discard_cards' else
                [self.add(s.engine,'Forest',ref='kicker-land-one'),self.add(s.engine,'Forest',ref='kicker-land-two')])
            private=self.private(s);self.ready(s)
            self.reject(s,{'action_id':'cast:'+source.ref,'cost_option':'kicked',field:[source.ref],'pay':'auto'},'A')
            self.cast(s,source,kicked=True,**{field:[c.ref for c in payers]})
            self.assertTrue(all(s.state.cards[c.object_id].zone=='graveyard' for c in payers))
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual((3,3),self.stats(s,source));self.private_replay(s,private)

    def test_paid_life_survives_countering_and_bounce_recast_does_not_remember_kicker(self):
        for countered in (False,True):
            s=self.start(791080+countered);source=self.add(s.engine,'Phyrexian Scuta',zone='hand')
            response=self.add(s.engine,'Counterspell' if countered else 'Boomerang',seat='B',zone='hand')
            s.state.players['B'].mana_pool['U']=2;private=self.private(s);self.ready(s)
            self.cast(s,source,kicked=True);self.assertEqual(37,s.state.players['A'].life)
            if countered:
                self.cast(s,response,[s.state.stack[-1].ref],seat='B');self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
            else:
                self.assertIsNone(self.resolve(s));self.assertEqual((5,5),self.stats(s,source))
                controls.RecipientModifierTests.cast(self,s,response,[source.ref],seat='B');self.assertIsNone(self.resolve(s))
                self.cast(s,s.state.cards[source.object_id]);self.assertIsNone(self.resolve(s));self.assertEqual((3,3),self.stats(s,source))
            self.assertEqual(37,s.state.players['A'].life);self.private_replay(s,private)

    def test_sacrificed_target_does_not_refund_kicker_when_the_spell_has_no_legal_target(self):
        s=self.start(791090);source=self.add(s.engine,'Vicious Offering',zone='hand');payer=self.add(s.engine,'Grizzly Bears')
        private=self.private(s);self.ready(s)
        self.cast(s,source,[payer.ref],kicked=True,sacrifice_cards=[payer.ref]);self.assertEqual('graveyard',s.state.cards[payer.object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.private_replay(s,private)

    def test_mandatory_discard_composes_with_kicker_and_replacement_does_not_unpay_sacrifice(self):
        for combined in (False,True):
            s=self.start(791100+combined);source=self.add(s.engine,'Generic Kicker Combined Price' if combined else 'Vicious Offering',zone='hand')
            payer=self.add(s.engine,'Grizzly Bears');target=self.add(s.engine,'Generic Bound Body',seat='B')
            discarded=self.add(s.engine,'Grizzly Bears',zone='hand',ref='kicker-mandatory-discard') if combined else None
            if not combined:
                self.add(s.engine,'Dauthi Voidwalker',seat='B',ref='kicker-void-one')
                self.add(s.engine,'Dauthi Voidwalker',seat='B',ref='kicker-void-two')
            private=self.private(s);self.ready(s)
            if combined:
                self.reject(s,{'action_id':'cast:'+source.ref,'cost_option':'kicked','sacrifice_cards':[payer.ref],'pay':'auto'},'A')
                self.assertEqual('battlefield',s.state.cards[payer.object_id].zone)
            self.cast(s,source,[target.ref] if not combined else [],kicked=True,sacrifice_cards=[payer.ref],
                **({'discard_cards':[discarded.ref]} if combined else {}))
            if not combined:
                decision=s.state.pending_decision;self.assertIsNotNone(decision);self.assertEqual('replacement.order',decision.kind)
                self.assertEqual('battlefield',s.state.cards[payer.object_id].zone)
                ctx=s.packet('pilot:A',full=True)['decision']['ctx'];selection=ctx['options'][0]['id']
                self.reject(s,{'action_id':'choose','replacement':selection},'D');s=self.replay(s,load=True)
                result=s.act('pilot:A',{'action_id':'choose','replacement':selection});self.assertTrue(result.ok,result.summary)
            self.assertEqual('graveyard' if combined else 'exile',s.state.cards[payer.object_id].zone)
            if combined:self.assertEqual('graveyard',s.state.cards[discarded.object_id].zone)
            s=self.replay(s,load=True);decision=self.resolve(s)
            for _ in range(4):
                if decision is None:break
                self.assertEqual('replacement.order',decision.kind)
                actor=s.pending_principals()[0];selection=s.packet(actor,full=True)['decision']['ctx']['options'][0]['id']
                result=s.act(actor,{'action_id':'choose','replacement':selection});self.assertTrue(result.ok,result.summary)
                decision=self.resolve(s)
            self.assertIsNone(decision)
            self.assertEqual((3,3) if combined else (-3,1),self.stats(s,source if combined else target));self.private_replay(s,private)

    def test_free_mana_base_retains_optional_nonmana_price_and_commit_rejects_seal_drift(self):
        from quorune.rules.casting.costs import build_cast_cost_options
        from quorune.compiled_kicker import validate_kicker_commit
        s=self.start(791110);source=self.add(s.engine,'Phyrexian Scuta',zone='hand');self.ready(s)
        from quorune.rules.casting.proposal import _spell_semantic_key
        record=self.db.lookup('Phyrexian Scuta');program=s.engine.semantics.get(_spell_semantic_key(record,None))
        options=build_cast_cost_options(s.engine,'A',source,program,hint=True,force_without_mana_cost=True)
        option=next((o.to_dict() for o in options if o.option_id=='kicked'),None);self.assertIsNotNone(option)
        self.assertFalse(any(option['requirements'].values()));self.assertEqual('fixed_life_payment',option['additional_costs'][0]['kind'])
        validate_kicker_commit(s.engine,source,option)
        for update in ({'kicker_fingerprint':'wrong'},{'additional_costs':[]},
                {'additional_costs':[{'schema_version':1,'kind':'fixed_life_payment','amount':1}]}):
            with self.assertRaises(ValueError):validate_kicker_commit(s.engine,source,{**option,**update})

    def test_return_cost_returns_a_stolen_creature_to_its_owner_without_targeting_it(self):
        s=self.start(791120);source=self.add(s.engine,'Arctic Merfolk',zone='hand')
        payer=self.add(s.engine,'Grizzly Bears',seat='B');control=self.add(s.engine,'Control Magic',zone='hand')
        private=self.private(s);self.ready(s);self.cast(s,control,[payer.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('A',s.state.cards[payer.object_id].controller)
        self.cast(s,source,kicked=True,return_cards=[payer.ref]);self.assertEqual('hand',s.state.cards[payer.object_id].zone)
        self.assertIn(payer.object_id,s.state.players['B'].zones['hand']);self.assertNotIn(payer.object_id,s.state.players['A'].zones['hand'])
        self.assertIsNone(self.resolve(s));self.assertEqual(1,s.state.cards[source.object_id].counters['+1/+1']);self.private_replay(s,private)

    def test_copy_keeps_kicked_result_without_a_second_sacrifice_payment(self):
        s=self.start(791130);source=self.add(s.engine,'Vicious Offering',zone='hand');copy=self.add(s.engine,'Reverberate',zone='hand')
        payer=self.add(s.engine,'Grizzly Bears');first=self.add(s.engine,'Generic Bound Body',seat='B',ref='kicker-copy-first')
        second=self.add(s.engine,'Generic Bound Body',seat='B',ref='kicker-copy-second')
        private=self.private(s);self.ready(s);self.cast(s,source,[first.ref],kicked=True,sacrifice_cards=[payer.ref])
        stack=s.state.stack[-1].ref;self.cast(s,copy,[stack]);decision=self.resolve(s)
        self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        self.reject(s,{'action_id':'choose','targets':[second.ref]},'B');s=self.replay(s,load=True)
        result=s.act('pilot:A',{'action_id':'choose','targets':[second.ref]});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual((-3,1),self.stats(s,first));self.assertEqual((-3,1),self.stats(s,second))
        self.assertEqual('graveyard',s.state.cards[payer.object_id].zone);self.private_replay(s,private)

    def test_verified_withdrawals_and_fresh_compiler_payment_omissions_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==CAP).update(status='blocked',blockers=['withdrawn nonmana Kicker'],dependency_fail_closed_status='not_run')
        registry=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        ir=compile_oracle_card(self.db.lookup('Phyrexian Scuta'),capability_registry=registry,capability_profile='commander_review')
        blocks=[b for r in ir.material_residuals for b in r.blockers];self.assertTrue(any('status:'+CAP+':blocked' in b for b in blocks),blocks)
        self.assertFalse(any('evidence_index:unverified' in b for b in blocks))
        method='test_six_originals_have_complete_registration_and_independent_payment_minima'
        with patch('quorune.compiler.nonmana_kicker.compile_nonmana_kicker',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_scuta_optional_life_is_paid_before_entry_counters_and_insufficient_life_rolls_back'
        with patch('quorune.rules.casting.commit._pay_fixed_life_additional_cost',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_subtype_tap_cost_uses_current_untapped_controlled_vampire_without_a_tap_symbol'
        from quorune.rules.casting.additional_tap_costs import _candidates
        def without_subtype(host,seat,cost):return _candidates(host,seat,replace(cost,schema_version=1,creature_subtypes=()))
        with patch('quorune.rules.casting.additional_tap_costs._candidates',side_effect=without_subtype):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

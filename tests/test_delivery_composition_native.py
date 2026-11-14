"""Real commands certify additional closures discovered at the frozen head."""
import json,tempfile,unittest
from pathlib import Path

from common import ROOT
import test_recipient_control as recipients
import optional_result_followup_fixture as options
import test_preparation as preparation_tests
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.preparation_model import PREPARED_FIELD
from scripts.build_test_database import build_fixture_database,compact_ci_fixture_paths

NAMES=('Plague Wind',"Burning Sun's Avatar",'Roadside Reliquary','Linvala, the Preserver','Githzerai Monk',
    'Reckless Rage','Bloomvine Regent // Claim Territory','Emeritus of Truce // Swords to Plowshares',
    'Joined Researchers // Secret Rendezvous','Rakdos Firewheeler','Voracious Hydra')


class DeliveryCompositionNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.RecipientControlNativeTests.reject
    finish=recipients.RecipientControlNativeTests.finish
    cast_face=options.OptionalResultFollowupFixture.cast_face
    prepared_copy=preparation_tests.PreparationRuntimeTests.prepare_copy

    @classmethod
    def setUpClass(cls):
        names={*NAMES,'Sliver Hivelord','Generic Bound Plains','Counterspell','Sol Ring','Fervor','Disenchant',
            'Grizzly Bears','Darksteel Myr','Unsummon','Drudge Skeletons','Hardened Scales','Forest','Plains','Vedalken Orrery'}
        docs=[json.loads(p.read_text(encoding='utf-8')) for p in (*compact_ci_fixture_paths(root=ROOT),ROOT/'tests/fixtures/bound-effect-program-cards.json')]
        cards={r['name']:r for d in docs for r in d['cards'] if r['name'] in names};assert set(cards)==names,names-set(cards)
        ids={r['oracle_id'] for r in cards.values()};rulings={tuple(sorted(r.items())):r for d in docs for r in d.get('rulings',[]) if r['oracle_id'] in ids}
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        doc=json.loads((ROOT/'tests/fixtures/recipient-control-cards.json').read_text(encoding='utf-8'));doc.update(cards=list(cards.values()),rulings=list(rulings.values()))
        fixture=folder/'composition.json';fixture.write_text(json.dumps(doc),encoding='utf-8');path=folder/'composition.sqlite3';build_fixture_database([fixture],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Additional composed originals',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def scene(self,name,before=None,*,zone='hand'):
        s=self.session(2021039+len(name));source=self.add(s.engine,name,zone=zone)
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        extra=before(s,source) if before else {}
        self.assert_closed(s,source);s.state.config.semantic_policy='trusted_only'
        self.prepare(s,mana={'C':30,**{c:10 for c in 'WUBRG'}});self.checkpoint(s)
        return s,source,private,extra

    def choose(self,s,command,*,actor='A'):
        foreign='D' if actor!='D' else 'C';self.reject(s,foreign,command)
        result=s.act('pilot:'+actor,command);self.assertTrue(result.ok,result.summary)

    def activate(self,s,source,*,actor='A'):
        self.priority(s,actor)
        row=next(r for r in s.packet('pilot:'+actor,full=True)['decision']['ctx']['legal']['actions'] if r['id'].startswith('activate:'+source.ref+':') and not r.get('mana_ability'))
        self.choose(s,{'action_id':row['id'],'pay':'auto'},actor=actor)

    def cast_prepared(self,s,copy,targets):
        self.priority(s,'A')
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r.get('card')==copy.ref)
        self.choose(s,{'action_id':row['id'],'targets':targets,'pay':'auto'})

    def settle_order(self,s):
        for _ in range(12):
            d=self.resolve(s)
            if d is None:return
            self.assertEqual('trigger.order',d.kind)
            actor=d.actors[0];refs=[item.ref for batch in s.state.pending_trigger_batches for item in batch.items if item.controller==actor]
            self.choose(s,{'action_id':'order','triggers':refs},actor=actor)
        self.fail('Waiting trigger batch did not settle')

    def test_all_additional_originals_bind_normally_without_material_residuals(self):
        for name in NAMES:
            with self.subTest(name=name):
                c=self.db.lookup(name,fuzzy=False);ir=compile_oracle_card(c,capability_registry=self.registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                p=_compile_best_available_card_program_from_ir(self.db,c,ir,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
                b=bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review');self.assertTrue(b['strict_capability_ready'],(name,b['blockers']));self.assertEqual('capability_closed',b['trust_basis'])

    def test_actual_uncontrolled_mass_destruction_preserves_own_and_indestructible_creatures(self):
        def before(s,source):
            s.state.players['B'].mana_pool['B']=1
            return {'own':self.add(s.engine,'Grizzly Bears'),'shielded':self.add(s.engine,'Drudge Skeletons',seat='B'),
                'durable':self.add(s.engine,'Darksteel Myr',seat='D')}
        s,source,private,extra=self.scene('Plague Wind',before)
        self.cast(s,'A',source);self.activate(s,extra['shielded'],actor='B');self.resolve_top(s)
        self.assertEqual(1,s.state.cards[extra['shielded'].object_id].regeneration_shields)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(['battlefield','graveyard','battlefield'],[s.state.cards[extra[k].object_id].zone for k in ('own','shielded','durable')]);self.finish(s,private)

    def test_actual_uncontrolled_entry_tap_uses_current_controller_not_owner(self):
        def before(s,source):
            own=self.add(s.engine,'Grizzly Bears');own.owner='D'
            return {'own':own,'foreign':self.add(s.engine,'Grizzly Bears',seat='B',ref='foreign-grizzly-bears'),'other':self.add(s.engine,'Darksteel Myr',seat='C')}
        s,source,private,extra=self.scene('Githzerai Monk',before);self.cast(s,'A',source);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertFalse(s.state.cards[source.object_id].tapped);self.assertFalse(s.state.cards[extra['own'].object_id].tapped)
        self.assertTrue(all(s.state.cards[extra[k].object_id].tapped for k in ('foreign','other')));self.finish(s,private)

    def test_actual_shooters_announce_each_printed_role_and_optional_amount(self):
        for name,amount in (("Burning Sun's Avatar",3),('Rakdos Firewheeler',2)):
            with self.subTest(name=name):
                def before(s,source):return {'body':self.add(s.engine,'Darksteel Myr',seat='B')}
                s,source,private,extra=self.scene(name,before);self.cast(s,'A',source)
                self.assertEqual('semantic.target',self.resolve(s).kind)
                self.reject(s,'A',{'action_id':'choose','targets':{'damage_role_0':['A'],'damage_role_1':[]}})
                self.choose(s,{'action_id':'choose','targets':{'damage_role_0':['B'],'damage_role_1':[extra['body'].ref]}})
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(40-amount,s.state.players['B'].life)
                self.assertEqual(amount,s.state.cards[extra['body'].object_id].marked_damage);self.finish(s,private)

    def test_actual_reckless_rage_keeps_opposing_controller_predicates_and_fixed_shares(self):
        def before(s,source):return {'own':self.add(s.engine,'Darksteel Myr'),'opposing':self.add(s.engine,'Darksteel Myr',seat='B',ref='rage-opposing')}
        s,source,private,extra=self.scene('Reckless Rage',before);row=self.action(s,'A',source)
        self.reject(s,'A',{'action_id':row['id'],'targets':[extra['own'].ref,extra['opposing'].ref],'pay':'auto'})
        self.cast(s,'A',source,[extra['opposing'].ref,extra['own'].ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual([4,2],[s.state.cards[extra[k].object_id].marked_damage for k in ('opposing','own')]);self.finish(s,private)

    def test_actual_reliquary_allows_no_result_and_reads_each_condition_after_responses(self):
        for both,remove in ((False,False),(True,False),(True,True)):
            with self.subTest(both=both,remove=remove):
                def before(s,source):
                    s.state.players['B'].mana_pool.update({'W':1,'C':1})
                    extra={'remove':self.add(s.engine,'Disenchant',seat='B',zone='hand')}
                    if both:extra.update(artifact=self.add(s.engine,'Sol Ring'),enchantment=self.add(s.engine,'Fervor'))
                    else:
                        self.add(s.engine,'Sol Ring',seat='B');self.add(s.engine,'Fervor',seat='B')
                    return extra
                s,source,private,extra=self.scene('Roadside Reliquary',before,zone='battlefield');hand=len(s.state.players['A'].zones['hand'])
                self.activate(s,source);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                if remove:self.cast(s,'B',extra['remove'],[extra['artifact'].ref]);self.resolve_top(s)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(hand+(0 if not both else 1 if remove else 2),len(s.state.players['A'].zones['hand']));self.finish(s,private)

    def test_actual_linvala_intervening_conditions_recheck_different_opponents(self):
        for reduce_creatures in (False,True):
            with self.subTest(reduce_creatures=reduce_creatures):
                def before(s,source):
                    s.state.players['C'].life=41;s.state.players['B'].mana_pool['U']=1
                    return {'one':self.add(s.engine,'Grizzly Bears',seat='B'),'two':self.add(s.engine,'Darksteel Myr',seat='B'),
                        'bounce':self.add(s.engine,'Unsummon',seat='B',zone='hand')}
                s,source,private,extra=self.scene('Linvala, the Preserver',before);self.cast(s,'A',source)
                while (d:=self.resolve(s)) is not None and d.kind=='trigger.order':
                    refs=[i.ref for b in s.state.pending_trigger_batches for i in b.items if i.controller=='A'];self.choose(s,{'action_id':'order','triggers':refs})
                    break
                if reduce_creatures:self.cast(s,'B',extra['bounce'],[extra['one'].ref]);self.resolve_top(s)
                s=self.replay(s,load=True);self.settle_order(s);self.assertEqual(45,s.state.players['A'].life)
                tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield'];self.assertEqual(0 if reduce_creatures else 1,len(tokens))
                if tokens:self.assertEqual((3,3),tuple(s.engine._numeric_stat(tokens[0].object_id,k) for k in ('power','toughness')))
                self.finish(s,private)

    def test_actual_hydra_replaces_entry_and_doubling_counters_and_fights_without_trample_damage(self):
        for mode in ('mode_1','mode_2'):
            with self.subTest(mode=mode):
                def before(s,source):
                    if mode=='mode_1':self.add(s.engine,'Hardened Scales')
                    return {'body':self.add(s.engine,'Grizzly Bears',seat='B')}
                s,source,private,extra=self.scene('Voracious Hydra',before);row=self.action(s,'A',source)
                self.choose(s,{'action_id':row['id'],'x':3,'pay':'auto'});self.assertEqual('semantic.target',self.resolve(s).kind)
                s=self.replay(s,load=True);self.choose(s,{'action_id':'choose','modes':[mode],'targets':[] if mode=='mode_1' else [extra['body'].ref]})
                self.assertIsNone(self.resolve(s));hydra=s.state.cards[source.object_id]
                if mode=='mode_1':self.assertEqual(9,hydra.counters['+1/+1'])
                else:
                    self.assertEqual(2,hydra.marked_damage);self.assertEqual('graveyard',s.state.cards[extra['body'].object_id].zone)
                self.assertEqual(40,s.state.players['B'].life);self.finish(s,private)

    def test_actual_omen_allocation_reveals_ordered_forests_and_keeps_both_shuffles(self):
        def before(s,source):return {'one':self.add(s.engine,'Forest',zone='library',ref='allocated-first-forest'),
            'two':self.add(s.engine,'Forest',zone='library',ref='allocated-second-forest'),'wrong':self.add(s.engine,'Grizzly Bears',zone='library')}
        s,source,private,extra=self.scene('Bloomvine Regent // Claim Territory',before)
        count=s.state.players['A'].stats.get('shuffle_count',0);self.cast_face(s,source,'Claim Territory')
        self.assertEqual('semantic.search',self.resolve(s).kind)
        self.reject(s,'A',{'action_id':'choose','search_cards':[extra['wrong'].ref]})
        s=self.replay(s,load=True);self.choose(s,{'action_id':'choose','search_cards':[extra['one'].ref,extra['two'].ref]})
        self.assertIsNone(self.resolve(s));self.assertEqual(('battlefield',True,'hand'),(s.state.cards[extra['one'].object_id].zone,s.state.cards[extra['one'].object_id].tapped,s.state.cards[extra['two'].object_id].zone))
        self.assertEqual('library',s.state.cards[source.object_id].zone);self.assertEqual(count+2,s.state.players['A'].stats.get('shuffle_count',0))
        for seat in 'BCD':self.assertIn(extra['two'].ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.finish(s,private)

    def test_actual_emeritus_checks_after_token_creation_then_casts_its_owned_inset(self):
        for initially_more in (False,True):
            with self.subTest(initially_more=initially_more):
                def before(s,source):
                    return {'body':self.add(s.engine,'Grizzly Bears',seat='B')} if initially_more else {}
                s,source,private,extra=self.scene('Emeritus of Truce // Swords to Plowshares',before);self.cast(s,'A',source)
                self.assertEqual('semantic.target',self.resolve(s).kind);self.choose(s,{'action_id':'choose','targets':['B']})
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                tokens=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield'];self.assertEqual(1,len(tokens));self.assertEqual('B',tokens[0].controller)
                self.assertEqual(initially_more,PREPARED_FIELD in s.state.cards[source.object_id].annotations)
                if initially_more:
                    copy=self.prepared_copy(s,source);self.cast_prepared(s,copy,[extra['body'].ref]);self.assertIsNone(self.resolve(s))
                    self.assertEqual('exile',s.state.cards[extra['body'].object_id].zone);self.assertEqual(42,s.state.players['B'].life)
                    self.assertNotIn(PREPARED_FIELD,s.state.cards[source.object_id].annotations)
                self.finish(s,private)

    def test_actual_researchers_prepare_at_end_step_and_draw_only_for_the_selected_pair(self):
        def before(s,source):
            self.add(s.engine,'Vedalken Orrery')
            for i in range(3):self.add(s.engine,'Plains',ref='prepared-rendezvous-land-'+str(i))
            return {}
        s,source,private,extra=self.scene('Joined Researchers // Secret Rendezvous',before);self.cast(s,'A',source);self.assertIsNone(self.resolve(s))
        self.until(s,lambda state:PREPARED_FIELD in state.state.cards[source.object_id].annotations and state.state.pending_decision is not None and state.state.pending_decision.kind=='priority')
        self.assertEqual('end_step',s.state.step);copy=self.prepared_copy(s,source)
        old={seat:set(s.state.players[seat].zones['hand']) for seat in 'ABCD'};self.priority(s,'A')
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if r.get('card')==copy.ref)
        self.reject(s,'A',{'action_id':row['id'],'targets':['A'],'pay':'auto'})
        self.choose(s,{'action_id':row['id'],'targets':['B'],'pay':'auto'});s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        for seat in 'ABCD':self.assertEqual(3 if seat in 'AB' else 0,len(set(s.state.players[seat].zones['hand'])-old[seat]))
        for oid in set(s.state.players['B'].zones['hand'])-old['B']:
            for viewer in 'ACD':self.assertNotIn(s.state.cards[oid].ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.assertNotIn(PREPARED_FIELD,s.state.cards[source.object_id].annotations);self.finish(s,private)


if __name__=='__main__':unittest.main()

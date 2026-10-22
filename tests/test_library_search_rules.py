from __future__ import annotations

"""CR101.2/701.23: library search restrictions preserve choices, shuffles and privacy."""

import tempfile,unittest,json
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch
from pathlib import Path
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_provoke_rules as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Mindlock Orb','Shadow of Doubt','Aven Mindcensor')
STATIC_CAP='library.search.restriction.static'
TEMP_CAP='library.search.prohibition.resolution_until_end_of_turn'


class LibrarySearchRuleTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ProvokeTests.session
    add=controls.ProvokeTests.add
    prepare=controls.ProvokeTests.prepare
    checkpoint=controls.ProvokeTests.checkpoint
    resolve=controls.ProvokeTests.resolve
    replay=controls.ProvokeTests.replay
    binding=controls.ProvokeTests.binding
    private_replay=controls.ProvokeTests.private_replay
    priority=controls.ProvokeTests.priority
    actions=controls.ProvokeTests.actions
    advance=controls.ProvokeTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'library-search-rules.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/library-search-rules-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Library search rules',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_complete_library_search_rule_originals_have_normal_admission(self):
        for i,name in enumerate(NAMES):
            session=self.session(761010+i);source=self.add(session.engine,name,zone='hand')
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            bound=self.binding(session,source);self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))

    def cast(self,session,card,payment,targets=(),*,seat='A',**choices):
        self.priority(session,seat)
        packet=session.packet('pilot:'+seat,full=True)
        rows=[r for r in packet['decision']['ctx']['legal']['actions'] if r['id']=='cast:'+card.ref]
        self.assertEqual(1,len(rows),card.printed_name)
        command={'action_id':rows[0]['id'],'pay':'manual','payment':payment,'targets':list(targets),**choices}
        self.reject(session,command,principal='pilot:D')
        result=session.act('pilot:'+seat,command)
        self.assertTrue(result.ok,result.summary)

    def reject(self,session,command,*,principal='pilot:A'):
        before=authoritative_state_hash(session.state)
        result=session.act(principal,command);self.assertFalse(result.ok,result.summary)
        self.assertEqual(before,authoritative_state_hash(session.state))

    def search_options(self,session,seat):
        decision=session.packet('pilot:'+seat,full=True).get('decision')
        self.assertIsNotNone(decision);self.assertEqual('semantic.search',decision['kind'])
        return decision['ctx']['search_cards']

    def select_search(self,session,seat,refs):
        result=session.act('pilot:'+seat,{'action_id':'choose','search_cards':list(refs)})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))

    def activate(self,session,card,seat='A'):
        self.priority(session,seat)
        actions=session.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
        rows=[r for r in actions if r['id'].startswith('activate:'+card.ref+':')]
        self.assertEqual(1,len(rows),rows)
        result=session.act('pilot:'+seat,{'action_id':rows[0]['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)

    def test_actual_mindlock_prevents_tutor_choice_but_keeps_mandatory_shuffle(self):
        session=self.session(761030)
        orb=self.add(session.engine,'Mindlock Orb',zone='hand')
        tutor=self.add(session.engine,'Demonic Tutor',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'B':1,'C':4,'U':1});self.checkpoint(session)
        self.cast(session,orb,{'C':3,'U':1});self.assertIsNone(self.resolve(session))
        before=tuple(session.state.players['A'].zones['library'])
        self.cast(session,tutor,{'B':1,'C':1})
        self.assertIsNone(self.resolve(session))
        self.assertEqual(set(before),set(session.state.players['A'].zones['library']))
        self.assertNotEqual(before,tuple(session.state.players['A'].zones['library']))
        self.assertEqual('graveyard',tutor.zone)
        self.private_replay(session,private)

    def test_actual_shadow_draws_privately_blocks_search_and_expires_at_cleanup(self):
        from quorune.rules.library_search_rules import library_search_constraint
        session=self.session(761031)
        shadow=self.add(session.engine,'Shadow of Doubt',zone='hand')
        tutor=self.add(session.engine,'Demonic Tutor',seat='B',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        session.state.players['A'].mana_pool['U']=2
        self.prepare(session,actor='B',active='B',mana={'C':1,'B':1});self.checkpoint(session)
        before=len(session.state.players['A'].zones['library'])
        self.cast(session,tutor,{'C':1,'B':1},seat='B')
        self.cast(session,shadow,{'U':2})
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(before-1,len(session.state.players['A'].zones['library']))
        self.assertEqual('graveyard',session.state.cards[shadow.object_id].zone)
        self.assertTrue(all(library_search_constraint(session.engine,seat).prohibited for seat in 'ABCD'))
        session=self.replay(session,load=True)
        turn=session.state.turn_sequence
        self.advance(session,lambda s:s.state.turn_sequence>turn)
        self.assertFalse(any(library_search_constraint(session.engine,seat).prohibited for seat in 'ABCD'))
        self.private_replay(session,private)

    def test_actual_mindcensor_flash_restricts_opponent_to_four_private_cards(self):
        session=self.session(761032)
        source=self.add(session.engine,'Aven Mindcensor',zone='hand')
        tutor=self.add(session.engine,'Demonic Tutor',seat='B',zone='hand')
        fifth=self.add(session.engine,'Lightning Bolt',seat='B',zone='library',ref='censored-fifth')
        visible=[self.add(session.engine,name,seat='B',zone='library',ref='censored-top-'+str(i))
            for i,name in enumerate(('Generic Bound Body','Generic Bound Plains','Forest','Plains'))]
        session.state.players['A'].mana_pool.update({'C':2,'W':1})
        self.prepare(session,actor='B',active='B',mana={'C':1,'B':1});self.checkpoint(session)
        self.cast(session,tutor,{'C':1,'B':1},seat='B');self.cast(session,source,{'C':2,'W':1})
        self.assertIsNotNone(self.resolve(session))
        source=session.state.cards[source.object_id]
        self.assertEqual((2,1),(session.engine._numeric_stat(source.object_id,'power'),session.engine._numeric_stat(source.object_id,'toughness')))
        self.assertIn('flying',session.engine._combat_keywords(source))
        options=self.search_options(session,'B');refs={r['id'] for r in options}
        self.assertEqual({c.ref for c in visible},refs)
        packet=session.packet('pilot:B',full=True)['decision']['ctx']
        self.assertEqual({c.ref for c in visible},{r['id'] for r in packet['search_window']})
        for viewer in 'ACD':
            text=json.dumps(session.packet('pilot:'+viewer,full=True))
            self.assertTrue(all(c.ref not in text for c in [fifth,*visible]))
        self.reject(session,{'action_id':'choose','search_cards':[fifth.ref]},principal='pilot:B')
        self.reject(session,{'action_id':'choose','search_cards':[visible[0].ref]},principal='pilot:C')
        session=self.replay(session,load=True)
        self.select_search(session,'B',[visible[0].ref])
        self.assertEqual('hand',session.state.cards[visible[0].object_id].zone)
        self.assertEqual('library',session.state.cards[fifth.object_id].zone)
        self.replay(session)

    def test_mindcensor_controller_searches_the_whole_library(self):
        session=self.session(761033);self.add(session.engine,'Aven Mindcensor')
        tutor=self.add(session.engine,'Demonic Tutor',zone='hand')
        deep=self.add(session.engine,'Lightning Bolt',zone='library',ref='own-deep-card')
        for i in range(5):self.add(session.engine,'Plains',zone='library',ref='own-top-'+str(i))
        self.prepare(session,mana={'B':1,'C':1});self.checkpoint(session)
        self.cast(session,tutor,{'B':1,'C':1});self.assertIsNotNone(self.resolve(session))
        self.assertIn(deep.ref,{r['id'] for r in self.search_options(session,'A')})
        self.select_search(session,'A',[deep.ref]);self.assertEqual('hand',deep.zone)
        self.replay(session,load=True)

    def test_actual_optional_search_cannot_be_chosen_and_does_not_shuffle(self):
        session=self.session(761034);self.add(session.engine,'Mindlock Orb')
        ranger=self.add(session.engine,'Borderland Ranger',zone='hand')
        self.prepare(session,mana={'C':2,'G':1});self.checkpoint(session)
        before=tuple(session.state.players['A'].zones['library'])
        self.cast(session,ranger,{'C':2,'G':1});self.assertIsNone(self.resolve(session))
        self.assertEqual(before,tuple(session.state.players['A'].zones['library']))
        self.assertEqual('battlefield',ranger.zone)
        self.replay(session,load=True)

    def test_bound_multi_card_search_keeps_one_window_and_shuffles_entire_library(self):
        session=self.session(761035);self.add(session.engine,'Aven Mindcensor',seat='A')
        spell=self.add(session.engine,'Ignite the Beacon',seat='B',zone='hand')
        fifth=self.add(session.engine,'Jace Beleren',seat='B',zone='library',ref='multi-forbidden-fifth')
        self.add(session.engine,'Generic Bound Body',seat='B',zone='library',ref='multi-top-body')
        first=self.add(session.engine,'Chandra Nalaar',seat='B',zone='library',ref='multi-top-first')
        self.add(session.engine,'Plains',seat='B',zone='library',ref='multi-top-plains')
        second=self.add(session.engine,'Jace Beleren',seat='B',zone='library',ref='multi-top-second')
        self.prepare(session,actor='B',active='B',mana={'C':4,'W':1});self.checkpoint(session)
        before=set(session.state.players['B'].zones['library'])
        self.cast(session,spell,{'C':4,'W':1},seat='B');self.assertIsNotNone(self.resolve(session))
        self.assertEqual({first.ref,second.ref},{r['id'] for r in self.search_options(session,'B')})
        self.reject(session,{'action_id':'choose','search_cards':[first.ref,fifth.ref]},principal='pilot:B')
        session=self.replay(session,load=True);self.select_search(session,'B',[first.ref,second.ref])
        self.assertEqual(before-{first.object_id,second.object_id},set(session.state.players['B'].zones['library']))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='hand' for c in (first,second)))
        self.assertEqual('library',session.state.cards[fifth.object_id].zone)
        self.replay(session)

    def test_current_ability_loss_restores_searching_and_prohibition_overrides_bounds(self):
        session=self.session(761036);orb=self.add(session.engine,'Mindlock Orb')
        self.add(session.engine,'Aven Mindcensor',seat='B')
        aura=self.add(session.engine,'Song of the Dryads',zone='hand')
        tutor=self.add(session.engine,'Demonic Tutor',zone='hand')
        self.prepare(session,mana={'C':3,'G':1,'B':1});self.checkpoint(session)
        self.cast(session,aura,{'C':2,'G':1},[orb.ref]);self.assertIsNone(self.resolve(session))
        self.cast(session,tutor,{'C':1,'B':1});self.assertIsNotNone(self.resolve(session))
        self.assertEqual(4,len(self.search_options(session,'A')))
        ref=self.search_options(session,'A')[0]['id'];self.select_search(session,'A',[ref])
        self.replay(session,load=True)

    def test_actual_scry_and_private_draw_remain_available_under_prohibition(self):
        session=self.session(761037);self.add(session.engine,'Mindlock Orb')
        opt=self.add(session.engine,'Opt',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':1});self.checkpoint(session)
        top=session.state.cards[session.state.players['A'].zones['library'][-1]]
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,opt,{'U':1});self.assertIsNotNone(self.resolve(session))
        result=session.act('pilot:A',{'action_id':'choose','cards':{'top':[top.ref],'bottom':[]}})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',top.zone)
        self.assertEqual(before,len(session.state.players['A'].zones['hand']))
        self.private_replay(session,private)

    def test_actual_search_activation_cost_is_paid_even_when_search_is_prohibited(self):
        session=self.session(761038);self.add(session.engine,'Mindlock Orb')
        land=self.add(session.engine,'Evolving Wilds')
        self.prepare(session);self.checkpoint(session)
        before=tuple(session.state.players['A'].zones['library'])
        self.activate(session,land);self.assertEqual('graveyard',land.zone)
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(set(before),set(session.state.players['A'].zones['library']))
        self.assertNotEqual(before,tuple(session.state.players['A'].zones['library']))
        self.replay(session)

    def test_prohibition_does_not_filter_nonlibrary_search_queries(self):
        session=self.session(761039);self.add(session.engine,'Mindlock Orb')
        card=self.add(session.engine,'Generic Bound Body',zone='graveyard')
        self.prepare(session)
        options=session.engine._semantic_search_options('A',{'zone':'graveyard','selector':{'types':['creature']}})
        self.assertIn(card.ref,{r['id'] for r in options})

    def test_actual_builtin_fetch_pays_life_and_sacrifice_when_prohibited(self):
        session=self.session(761040);self.add(session.engine,'Mindlock Orb')
        land=self.add(session.engine,'Flooded Strand')
        self.prepare(session);self.checkpoint(session)
        before=set(session.state.players['A'].zones['library'])
        self.activate(session,land)
        self.assertEqual('fetch_land',session.state.stack[-1].context.get('builtin'))
        self.assertEqual(('graveyard',39),(land.zone,session.state.players['A'].life))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(before,set(session.state.players['A'].zones['library']))
        self.replay(session)

    def test_actual_builtin_fetch_uses_the_same_private_top_window(self):
        session=self.session(761041);self.add(session.engine,'Aven Mindcensor')
        land=self.add(session.engine,'Flooded Strand',seat='B')
        fifth=self.add(session.engine,'Tropical Island',seat='B',zone='library',ref='fetch-forbidden-fifth')
        visible=[self.add(session.engine,name,seat='B',zone='library',ref='fetch-top-'+str(i))
            for i,name in enumerate(('Generic Bound Body','Plains','Forest','Island'))]
        self.prepare(session,actor='B',active='B');self.checkpoint(session)
        self.activate(session,land,'B')
        self.assertEqual('fetch_land',session.state.stack[-1].context.get('builtin'))
        self.assertIsNotNone(self.resolve(session))
        packet=session.packet('pilot:B',full=True)['decision']
        self.assertEqual('search.fetch',packet['kind'])
        self.assertEqual({visible[1].ref,visible[3].ref},{r['id'] for r in packet['ctx']['search_cards']})
        self.assertEqual({c.ref for c in visible},{r['id'] for r in packet['ctx']['search_window']})
        self.reject(session,{'action_id':'choose','search_card':fifth.ref},principal='pilot:B')
        session=self.replay(session,load=True)
        result=session.act('pilot:B',{'action_id':'choose','search_card':visible[1].ref})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[visible[1].object_id].zone)
        self.assertEqual('library',session.state.cards[fifth.object_id].zone)
        self.assertEqual(39,session.state.players['B'].life)
        self.replay(session)

    def test_closed_rule_and_duration_models_reject_malformed_data(self):
        from quorune.library_search_model import LibrarySearchRuleSpec,ResolvedLibrarySearchProhibition
        spec=LibrarySearchRuleSpec('top','opponents',4)
        self.assertEqual(spec,LibrarySearchRuleSpec.from_dict(spec.to_dict()))
        for change in ({'mode':'unknown'},{'top_count':True},{'top_count':0},{'player_scope':'target'},{'schema_version':2}):
            with self.assertRaises((TypeError,ValueError)):LibrarySearchRuleSpec.from_dict({**spec.to_dict(),**change})
        resolved=ResolvedLibrarySearchProhibition('LS1','S1',1,'A')
        self.assertEqual(resolved,ResolvedLibrarySearchProhibition.from_dict(resolved.to_dict()))
        with self.assertRaises(ValueError):ResolvedLibrarySearchProhibition.from_dict({**resolved.to_dict(),'extra':1})

    def test_independent_runtime_owners_and_verified_withdrawal_fail_closed(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        for name,cap in (('Mindlock Orb',STATIC_CAP),('Shadow of Doubt',TEMP_CAP)):
            session=self.session(761050);source=self.add(session.engine,name,zone='hand')
            programs=[p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if cap in p.capability_dependencies]
            self.assertTrue(programs)
            program=programs[0];deps=tuple(c for c in program.capability_dependencies if c!=cap)
            stripped=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==STATIC_CAP).update(status='blocked',blockers=['withdrawn library search permission owner'])
        registry=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in NAMES:
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+STATIC_CAP+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers))

    def test_fresh_source_permission_window_and_optional_omissions_are_killed(self):
        from quorune.rules.library_search_rules import LibrarySearchConstraint
        import quorune.rules.library_search_rules as owner
        original=owner.library_search_object_ids
        def unbounded(host,player,zone):return tuple(host.state.players[player].zones[zone])
        for path,value,method in (
            ('quorune.compiler.runtime_templates.fixed_library_search_rule_handler',lambda text:None,
             'test_complete_library_search_rule_originals_have_normal_admission'),
            ('quorune.semantic_runtime.action_permissions.controller_action_permissions',lambda *args,**kwargs:(),
             'test_actual_mindlock_prevents_tutor_choice_but_keeps_mandatory_shuffle'),
            ('quorune.selection.searching.library_search_object_ids',unbounded,
             'test_actual_mindcensor_flash_restricts_opponent_to_four_private_cards'),
            ('quorune.semantic_choices.optional_effect._prohibited_optional_library_search',lambda *args:False,
             'test_actual_optional_search_cannot_be_chosen_and_does_not_shuffle')):
            with patch(path,value):
                with self.assertRaises(AssertionError,msg=path):getattr(type(self)(method),method)()


if __name__=='__main__':unittest.main()

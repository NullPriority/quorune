"""CR702.116b: each printed Myriad instance survives keyword composition."""
from dataclasses import replace
import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_qualified_zone_event_queries import query_record


class MyriadKeywordCompositionCompilerTests(unittest.TestCase):
    def test_single_keyword_programs_keep_their_existing_complete_ir_payload(self):
        from unittest.mock import patch
        from contextlib import ExitStack
        from quorune.compiler import keyword_nodes as owner
        registry=load_default_capability_registry()
        for text in ('Myriad','Flying','Flying, haste','Cascade'):
            record=replace(query_record(text),type_line='Creature — Squirrel',power='2',toughness='2',keywords=('Flying','Haste','Myriad','Cascade'))
            with ExitStack() as context:
                for name in ('_GROUPED_SPLIT_MECHANICS','_INSTANCE_PART_MECHANICS'):
                    context.enter_context(patch.object(owner,name,tuple(m for m in getattr(owner,name) if m!='myriad')))
                context.enter_context(patch.object(owner,'_SPLIT_MECHANICS',owner._SPLIT_MECHANICS-{'myriad'}))
                before=compile_oracle_card(record,capability_registry=registry).to_dict()
            self.assertEqual(before,compile_oracle_card(record,capability_registry=registry).to_dict(),text)

    def test_each_myriad_instance_has_its_own_node_span_and_sibling_keywords(self):
        registry=load_default_capability_registry()
        for text, count, flying in (('Myriad, myriad',2,False),('Flying, myriad',1,True),
                ('Flying, myriad, myriad',2,True)):
            with self.subTest(text=text):
                record=replace(query_record(text),type_line='Creature — Squirrel',power='2',toughness='2',keywords=('Flying','Myriad'))
                ir=compile_oracle_card(record,capability_registry=registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                nodes=[n for n in ir.faces[0].nodes if n.template_id=='fixed-myriad-trigger-v1']
                self.assertEqual(count,len(nodes))
                self.assertEqual(count,len({n.node_id for n in nodes}))
                from quorune.compiler.generated_program_identity import generated_ability_id
                identities=[generated_ability_id(kind=node.kind,face_id='front',line=node.span.line,
                    static_declaration=False,node_id=node.node_id) for node in nodes]
                self.assertEqual(count,len(set(identities)))
                self.assertEqual(count,len({(n.span.start,n.span.end) for n in nodes}))
                for node in nodes:
                    self.assertEqual('myriad',text[node.span.start:node.span.end].casefold())
                    self.assertEqual('choose_myriad_token_destinations',node.effects[0]['op'])
                if flying:
                    self.assertTrue(any(n not in nodes for n in ir.faces[0].nodes))

    def test_unknown_siblings_parameters_and_owner_withdrawal_do_not_admit_myriad(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        registry=load_default_capability_registry()
        for text in ('Flying, myriad 2','Myriad, draw a card','Myriad, whenever this creature attacks, draw a card'):
            with self.subTest(text=text):
                record=replace(query_record(text),type_line='Creature — Squirrel',keywords=('Flying','Myriad'))
                self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=registry).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        next(c for c in raw['capabilities'] if c['id']=='combat.entry.lifecycle.fixed_public').update(
            status='blocked',blockers=['Myriad lifecycle owner withdrawn'])
        blocked=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        blocked.mark_evidence_verified(validate_capability_evidence_index(index,registry=blocked))
        for text,expected in (('Flying',True),('Flying, myriad',False),('Myriad, myriad',False)):
            record=replace(query_record(text),type_line='Creature — Squirrel',keywords=('Flying','Myriad'))
            self.assertEqual(expected,compile_oracle_card(record,capability_registry=blocked).status=='exact')

    def test_discarding_independent_myriad_instances_is_killed(self):
        from unittest.mock import patch
        from quorune.compiler import keyword_nodes as owner
        case=type(self)('test_each_myriad_instance_has_its_own_node_span_and_sibling_keywords')
        baseline=unittest.TestResult();case.run(baseline);self.assertTrue(baseline.wasSuccessful())
        with patch.object(owner,'_GROUPED_SPLIT_MECHANICS',tuple(m for m in owner._GROUPED_SPLIT_MECHANICS if m!='myriad')):
            mutant=unittest.TestResult();case.run(mutant)
        self.assertFalse(mutant.wasSuccessful());self.assertFalse(mutant.errors)

    def test_collapsing_myriad_registration_identities_is_killed(self):
        from unittest.mock import patch
        from quorune.compiler import generated_program_identity as owner
        original=owner.generated_ability_id
        def mutate(**values):
            if values['kind']=='triggered_ability' and ':myriad:' in values.get('node_id',''):
                return f"trigger:{values['face_id']}:n{values['line']}"
            return original(**values)
        case=type(self)('test_each_myriad_instance_has_its_own_node_span_and_sibling_keywords')
        baseline=unittest.TestResult();case.run(baseline);self.assertTrue(baseline.wasSuccessful())
        with patch.object(owner,'generated_ability_id',mutate):
            mutant=unittest.TestResult();case.run(mutant)
        self.assertFalse(mutant.wasSuccessful());self.assertFalse(mutant.errors)


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class MyriadKeywordCompositionNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'myriad-composition.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/myriad-composition-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Myriad composition',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def test_actual_scurry_has_two_independent_optional_triggers_and_both_copy_groups_exile(self):
        s=self.session(702116002)
        source=self.add(s.engine,'Scurry of Squirrels');fog=self.add(s.engine,'Fog',zone='hand')
        forest=self.add(s.engine,'Forest');private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        self.assert_closed(s,source);self.assert_closed(s,fog);self.prepare(s);self.checkpoint(s)
        self.attack(s,[(source,'B')])
        items=[item for item in s.state.stack if item.source_object_id==source.object_id]
        self.assertEqual(2,len(items));self.assertEqual(2,len({item.ref for item in items}))
        for index,opponent in enumerate(('C','D')):
            decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
            command={'action_id':'choose','attacking':{f'myriad:{seat}':'create' if seat==opponent else 'decline' for seat in 'CD'}}
            before=authoritative_state_hash(s.state)
            self.assertFalse(s.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
            self.assertFalse(s.act('pilot:A',{'action_id':'choose','attacking':{'myriad:C':'create'}}).ok)
            self.assertEqual(before,authoritative_state_hash(s.state))
            result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
            self.assertEqual('semantic.choice',s.state.pending_decision.kind)
            if index==0:s=self.replay(s,load=True)
            result=s.act('pilot:A',{'action_id':'choose','attacking':{f'myriad:{opponent}:0':opponent}})
            self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        copies=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield' and c.oracle_id==source.oracle_id]
        self.assertEqual(2,len(copies));self.assertTrue(all(c.tapped for c in copies))
        self.assertEqual({'C','D'},{c.attacking for c in copies})
        self.assertEqual(2,len(s.state.delayed_triggers))
        self.assertTrue(all(str(s.engine._effective_card_data(c)['power'])=='2' for c in copies))
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.priority(s,'A');actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(r for r in actions if r.get('mana_ability') and forest.ref in r['id'])
        result=s.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.cast(s,'A',fog,[]);self.resolve_top(s)
        self.until(s,lambda game:game.state.phase=='postcombat_main')
        self.assertEqual([40,40,40,40],[s.state.players[seat].life for seat in 'ABCD'])
        self.assertTrue(all(s.state.cards[c.object_id].zone=='outside' for c in copies))
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertFalse(any(trigger.active for trigger in s.state.delayed_triggers))
        self.replay(s,load=True)


if __name__=='__main__':unittest.main()

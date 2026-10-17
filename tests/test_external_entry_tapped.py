from __future__ import annotations

"""CR 614.4/614.12: an existing source modifies prospective entry."""

import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.capabilities import CapabilityRegistry
from quorune.oracle_ir import compile_oracle_card
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs import bind_card_program_runtime
from quorune.semantics import SemanticRegistry
from quorune.external_entry_state_model import ExternalEntryStateSpec,EXTERNAL_ENTRY_STATE_CAPABILITY
from quorune.compiler.external_entry_state_templates import external_entry_state_handler
from quorune.semantic_runtime.external_entry_state import ExternalEntryStateHandler
from quorune.replacement.model import ReplaceableEvent
from quorune.replacement.applicability import condition_matches
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions
from test_granted_skulk import record


FIXTURE=ROOT/'tests/fixtures/external-entry-tapped-cards.json'
CLOSED={'Orb of Dreams','Root Maze','Frozen Aether','Imposing Sovereign','Authority of the Consuls',
    'Urabrask the Hidden','Blind Obedience','Kismet','Loxodon Gatekeeper','Manglehorn',"Kinjalli's Sunwing",
    'Spider-Woman, Stunning Savior','False Floor'}


class ExternalEntryTappedCompilerTests(unittest.TestCase):
    def test_external_entry_grammar_source_spans_and_dependency_gates(self):
        registry=load_default_capability_registry()
        for text,relation,types in (
            ('Permanents enter tapped.','all',()),
            ('Artifacts and lands enter tapped.','all',('artifact','land')),
            ('Creatures your opponents control enter tapped.','opponents',('creature',)),
            ('Artifacts, creatures, and lands you control enter tapped.','controller',('artifact','creature','land')),
            ('Venom Blast — Artifacts and creatures your opponents control enter tapped.','opponents',('artifact','creature')),
        ):
            with self.subTest(text=text):
                ir=compile_oracle_card(record(text,'Enchantment'),capability_registry=registry)
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node,=ir.faces[0].nodes
                self.assertEqual(text,record(text,'Enchantment').oracle_text[node.span.start:node.span.end])
                self.assertEqual('battlefield',node.active_zone)
                self.assertEqual(relation,node.handlers[0]['source_relation'])
                self.assertEqual(types,tuple(node.handlers[0]['types_any']))
                self.assertIn('current_ability_fragment_required',node.runtime_coverage)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        for dependency in (EXTERNAL_ENTRY_STATE_CAPABILITY,'zone.entry.tapped_state'):
            changed=deepcopy(raw)
            next(r for r in changed['capabilities'] if r['id']==dependency).update(status='blocked',blockers=['entry owner omitted'])
            self.assertNotEqual('exact',compile_oracle_card(record('Permanents enter tapped.','Artifact'),capability_registry=CapabilityRegistry(changed)).status)
        for text in ('Nonbasic lands enter tapped.','Non-Phyrexian creatures enter tapped.',
            'Creatures and nonbasic lands your opponents control enter tapped.',
            'Creatures played by your opponents enter tapped.','Creatures enter tapped if it is your turn.',
            'Artifacts, lands enter tapped.','Artifacts and artifacts enter tapped.',
            'As long as you control a creature — Creatures enter tapped.',
            'This creature is blue — Creatures enter tapped.',
            'If You Control A Forest — Creatures enter tapped.',
            'Artifacts and lands enter tapped. Draw a card.'):
            with self.subTest(text=text):self.assertIsNone(external_entry_state_handler(text))

    def test_external_entry_model_and_event_scope_are_closed(self):
        for relation in ('all','controller','opponents'):
            for types in ((),('creature',),('artifact','land')):
                spec=ExternalEntryStateSpec(relation,types)
                self.assertEqual(spec,ExternalEntryStateSpec.from_dict(spec.to_dict()))
                descriptor={'handler_id':'replacement.zone.external-entry-tapped.v1','schema_version':1,
                    'event':'zone.change',**spec.to_dict()}
                for source_controller in 'ABCD':
                    effect=ExternalEntryStateHandler().source_replacement_effect(descriptor,source_ref='existing-source',source_controller=source_controller,component_id='component')
                    for controller in 'ABCD':
                        for kind in ('artifact','creature','land','enchantment'):
                            for destination in ('battlefield','exile'):
                                event=ReplaceableEvent('entry','zone.change',controller,{'destination':destination,
                                    'destination_controller':controller,'object_types':[kind]})
                                expected=(destination=='battlefield' and (not types or kind in types)
                                    and (relation=='all' or ((controller==source_controller)==(relation=='controller'))))
                                self.assertEqual(expected,condition_matches(effect.conditions,event))
        for raw in ({'source_relation':[],'types_any':[]},{'source_relation':'target','types_any':[]},
            {'source_relation':'all','types_any':['land','artifact']},{'source_relation':'all','types_any':['creature','creature']},
            {'source_relation':'all','types_any':['instant']},{'source_relation':'all','types_any':[],'extra':True}):
            with self.subTest(raw=raw),self.assertRaises(ValueError):ExternalEntryStateSpec.from_dict(raw)


class ExternalEntryTappedRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=actions.BoundEffectProgramRuntimeTests.session
    add=actions.BoundEffectProgramRuntimeTests.add
    checkpoint=actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve=actions.BoundEffectProgramRuntimeTests.resolve
    replay=actions.BoundEffectProgramRuntimeTests.replay
    prepare=zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast=zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed=zone_actions.SelfPublicZoneRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        path=Path(cls.temp.name)/'external-entry.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/qualified-landwalk-cards.json',
            ROOT/'tests/fixtures/escaped-entry-counter-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('External entry',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_kismet_cast_affects_every_opponent_but_not_its_controller(self):
        session=self.session(61400401);engine=session.engine
        source=self.add(engine,'Kismet',zone='hand')
        private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='entry-private')
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':3,'W':1});self.checkpoint(session)
        self.cast(session,'A',source);self.resolve(session)
        self.assertEqual('battlefield',source.zone)
        self.assertFalse(source.tapped)
        self.replay(session,load=True)
        for actor in 'ABCD':
            with self.subTest(actor=actor):
                card=self.add(session.engine,'Generic Bound Body',seat=actor,zone='hand',ref='kismet-entrant-'+actor)
                self.prepare(session,actor=actor,active=actor,mana={'W':1});self.checkpoint(session)
                action=next(row for row in session.packet('pilot:'+actor,full=True)['decision']['ctx']['legal']['actions'] if row.get('card')==card.ref)
                command={'action_id':action['id'],'pay':'auto'}
                before=authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:'+('B' if actor=='A' else 'A'),command).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
                accepted=session.act('pilot:'+actor,command);self.assertTrue(accepted.ok,accepted.summary)
                session=self.replay(session,load=True);self.resolve(session)
                current=session.state.cards[card.object_id]
                self.assertEqual('battlefield',current.zone)
                self.assertEqual(actor!='A',current.tapped)
                for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
                self.replay(session,load=True)

    def test_originals_admit_only_the_complete_external_entry_family(self):
        for row in json.loads(FIXTURE.read_text(encoding='utf-8'))['cards']:
            if row['oracle_id'].startswith('fixture:') or row['name'] in {'Sol Ring','Memnite'}:continue
            with self.subTest(name=row['name']):
                program=compile_best_available_card_program(self.db,self.db.lookup(row['name']),semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry,capability_profile='commander_review')
                binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertEqual(row['name'] in CLOSED,binding['strict_capability_ready'],binding['blockers'])

    def test_actual_orb_does_not_tap_itself_but_taps_later_creature_and_token(self):
        session=self.session(61400402);engine=session.engine
        source=self.add(engine,'Orb of Dreams',zone='hand')
        creature=self.add(engine,'Woe Strider',zone='graveyard')
        fodder=[self.add(engine,'Generic Bound Plains',zone='graveyard',ref=f'orb-escape-fodder-{i}') for i in range(4)]
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':3});self.checkpoint(session)
        self.cast(session,'A',source);self.resolve(session)
        self.assertEqual('battlefield',source.zone);self.assertFalse(source.tapped)
        self.replay(session,load=True)
        self.prepare(session,mana={'C':3,'B':2});self.checkpoint(session)
        action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row.get('card')==creature.ref)
        result=session.act('pilot:A',{'action_id':action['id'],'cost_option':'escape','exile_cards':[card.ref for card in fodder],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        session=self.replay(session,load=True);self.resolve(session)
        if session.state.pending_decision is not None and session.state.pending_decision.kind=='replacement.order':
            session=self.replay(session,load=True)
        while session.state.pending_decision is not None and session.state.pending_decision.kind=='replacement.order':
            for viewer in 'BCD':self.assertIsNone(session.packet('pilot:'+viewer,full=True)['decision'])
            choice=session.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
            result=session.act('pilot:A',{'action_id':'choose','replacement':choice})
            self.assertTrue(result.ok,result.summary)
            self.resolve(session)
        creature=session.state.cards[creature.object_id]
        self.assertEqual('battlefield',creature.zone);self.assertTrue(creature.tapped)
        self.assertEqual({'+1/+1':2},creature.counters)
        goats=[card for card in session.state.cards.values() if card.is_token and card.printed_name=='Goat' and card.zone=='battlefield']
        self.assertEqual(1,len(goats));self.assertTrue(goats[0].tapped)
        self.replay(session,load=True)

    def test_actual_external_and_untapped_land_replacements_choose_for_affected_controller(self):
        for first_source,expected in (('orb-order',False),('untapped-order',True)):
            with self.subTest(first_source=first_source):
                session=self.session(61400403+expected);engine=session.engine
                orb=self.add(engine,'Orb of Dreams',ref='orb-order')
                untap=self.add(engine,'Generic Controlled Untapped Entry',seat='B',ref='untapped-order')
                land=self.add(engine,'Generic Bound Plains',seat='B',zone='hand',ref='order-land')
                self.assert_closed(session,orb);self.assert_closed(session,untap)
                self.prepare(session,actor='B',active='B');self.checkpoint(session)
                action=next(row for row in session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if row['id']=='play-land:'+land.ref)
                accepted=session.act('pilot:B',{'action_id':action['id']});self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual('replacement.order',session.state.pending_decision.kind)
                self.assertEqual('hand',land.zone)
                session=self.replay(session,load=True)
                for seat in 'ACD':self.assertIsNone(session.packet('pilot:'+seat,full=True)['decision'])
                options=session.packet('pilot:B',full=True)['decision']['ctx']['options']
                chosen=next(row for row in options if first_source in row['id'])
                before=authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:A',{'action_id':'choose','replacement':chosen['id']}).ok)
                self.assertEqual(before,authoritative_state_hash(session.state))
                accepted=session.act('pilot:B',{'action_id':'choose','replacement':chosen['id']});self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual('battlefield',session.state.cards[land.object_id].zone)
                self.assertEqual(expected,session.state.cards[land.object_id].tapped)
                self.replay(session,load=True)

    def test_simultaneous_entry_owner_freezes_only_preexisting_ambient_sources(self):
        session=self.session(61400405);engine=session.engine
        source=self.add(engine,'Orb of Dreams',zone='hand')
        ring=self.add(engine,'Sol Ring',zone='hand')
        self.assert_closed(session,source);self.assert_closed(session,ring)
        # Canonical owner test: the pinned mass-return spells remain residual.
        engine._move_cards_simultaneously([(source.object_id,'battlefield'),(ring.object_id,'battlefield')],reason='entry batch owner witness',log=False)
        self.assertFalse(source.tapped);self.assertFalse(ring.tapped)
        later=self.add(engine,'Sol Ring',zone='hand',ref='later-ring')
        engine.move_card(later.object_id,'battlefield',log=False)
        self.assertTrue(later.tapped)

    def test_actual_root_maze_uses_copied_entry_types_and_ignores_plain_creatures(self):
        session=self.session(61400406);engine=session.engine
        root=self.add(engine,'Root Maze')
        target=self.add(engine,'Memnite')
        clone=self.add(engine,'Clone',seat='B',zone='hand')
        plain=self.add(engine,'Generic Bound Body',seat='B',zone='hand',ref='root-plain')
        self.assert_closed(session,root);self.assert_closed(session,target)
        self.prepare(session,actor='B',active='B',mana={'C':3,'U':1});self.checkpoint(session)
        self.cast(session,'B',clone);decision=self.resolve(session)
        self.assertEqual('replacement.order',decision.kind)
        session=self.replay(session,load=True)
        option=next(row for row in session.packet('pilot:B',full=True)['decision']['ctx']['options'] if target.ref in row['id'] and not row['id'].startswith('decline:'))
        result=session.act('pilot:B',{'action_id':'choose','replacement':option['id']});self.assertTrue(result.ok,result.summary)
        self.resolve(session)
        self.assertEqual('battlefield',session.state.cards[clone.object_id].zone)
        self.assertTrue(session.state.cards[clone.object_id].tapped)
        self.replay(session,load=True)
        self.prepare(session,actor='B',active='B',mana={'W':1});self.checkpoint(session)
        self.cast(session,'B',plain);self.resolve(session)
        self.assertFalse(session.state.cards[plain.object_id].tapped)
        self.replay(session,load=True)

    def test_actual_copied_source_survives_original_and_loses_rule_to_humble(self):
        session=self.session(61400407);engine=session.engine
        source=self.add(engine,'Loxodon Gatekeeper')
        clone=self.add(engine,'Clone',zone='hand')
        bounce=self.add(engine,'Unsummon',zone='hand')
        humble=self.add(engine,'Humble',zone='hand')
        self.assert_closed(session,source)
        self.prepare(session,mana={'C':3,'U':1});self.checkpoint(session)
        self.cast(session,'A',clone);decision=self.resolve(session)
        self.assertEqual('replacement.order',decision.kind)
        session=self.replay(session,load=True)
        option=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['options'] if source.ref in row['id'] and not row['id'].startswith('decline:'))
        result=session.act('pilot:A',{'action_id':'choose','replacement':option['id']});self.assertTrue(result.ok,result.summary);self.resolve(session)
        self.replay(session,load=True)
        self.prepare(session,mana={'U':1});self.checkpoint(session)
        self.cast(session,'A',bounce,[source.ref]);self.resolve(session)
        self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.replay(session,load=True)
        first=self.add(session.engine,'Generic Bound Body',seat='B',zone='hand',ref='copy-source-first')
        self.prepare(session,actor='B',active='B',mana={'W':1});self.checkpoint(session)
        self.cast(session,'B',first);self.resolve(session)
        self.assertTrue(session.state.cards[first.object_id].tapped)
        self.replay(session,load=True)
        self.prepare(session,mana={'C':1,'W':1});self.checkpoint(session)
        self.cast(session,'A',humble,[clone.ref]);self.resolve(session)
        self.replay(session,load=True)
        second=self.add(session.engine,'Generic Bound Body',seat='B',zone='hand',ref='copy-source-second')
        self.prepare(session,actor='B',active='B',mana={'W':1});self.checkpoint(session)
        self.cast(session,'B',second);self.resolve(session)
        self.assertFalse(session.state.cards[second.object_id].tapped)
        self.replay(session,load=True)

    def test_external_source_omission_is_killed_by_actual_command_witness(self):
        import quorune.semantic_runtime.zone_replacements as owner
        original=owner.collect_zone_change_replacement_effects
        def omitted(host,**kwargs):
            return tuple(effect for effect in original(host,**kwargs) if not effect.effect_id.startswith('replacement.zone.external-entry-tapped.v1:'))
        with mock.patch.object(owner,'collect_zone_change_replacement_effects',side_effect=omitted):
            with self.assertRaises(AssertionError):self.test_actual_orb_does_not_tap_itself_but_taps_later_creature_and_token()


if __name__=='__main__':unittest.main()

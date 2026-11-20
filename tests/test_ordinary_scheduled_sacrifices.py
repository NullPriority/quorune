"""CR603/608/701.21: ordinary upkeep players choose their own sacrifice."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import TurnEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients

FIXTURE=ROOT/'tests/fixtures/ordinary-scheduled-sacrifice-cards.json'
NAMES=['Ruthless Winnower','Sheoldred, Whispering One','Molder Slug']

class OrdinaryScheduledSacrificeCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'ordinary-sacrifice.sqlite3'
        build_fixture_database([FIXTURE],path);cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_all_three_originals_close_normally_and_removing_annotations_does_not_remove_ownership(self):
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
        semantics=SemanticRegistry();cards=[self.db.lookup(n,fuzzy=False) for n in NAMES]
        register_generated_programs(self.db,semantics,cards,trust_level='provisional',capability_registry=self.registry,
            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        for card in cards:
            with self.subTest(card=card.name):
                ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,capability_registry=self.registry,capability_profile='commander_review')
                bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertTrue(bound['strict_capability_ready'],(card.name,bound['blockers']))
                trigger=next(p for p in semantics.programs_for_oracle(card.oracle_id) if any(e.get('op')=='choose_cards_apnap' and e.get('then')=='sacrifice' for e in p.effects))
                for cap in ('trigger.event.scheduled_player_result','choice.affected_player.fixed_sacrifice'):
                    deps=tuple(c for c in trigger.capability_dependencies if c!=cap)
                    altered=replace(trigger,handlers=(),coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                    result=bind_semantic_program_runtime(altered,capability_registry=self.registry,profile='commander_review')
                    self.assertFalse(result['strict']);self.assertTrue(any(cap in b for b in result['blockers']),result['blockers'])

    def test_matching_one_upkeep_clause_does_not_admit_unresolved_material_siblings(self):
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime
        from quorune.carddb import CardRecord
        card=CardRecord(oracle_id='constructed-upkeep-material-boundary',name='Upkeep Boundary',mana_cost='{2}{B}',mana_value=3,type_line='Enchantment',
            oracle_text="At the beginning of each player's upkeep, that player sacrifices a creature of their choice.\nWhen you control exactly thirteen creatures, you win the game.",
            power=None,toughness=None,loyalty=None,defense=None,colors=('B',),color_identity=('B',),keywords=(),produced_mana=(),layout='normal',released_at='',legalities={'commander':'legal'},faces=(),raw={})
        ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)
        self.assertTrue(any(n.event=='step.begin' for n in ir.faces[0].nodes))
        p=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        self.assertFalse(bind_card_program_runtime(p,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])

class OrdinaryScheduledSacrificeNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'ordinary-sacrifice-native.sqlite3'
        build_fixture_database([FIXTURE],path);cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Ordinary upkeep sacrifices',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def choose(self,s,seat,card,*,excluded=()):
        d=self.resolve(s);self.assertEqual('choice.apnap',d.kind);self.assertEqual([seat],d.actors)
        options=s.packet('pilot:'+seat,full=True)['decision']['ctx']['options'];self.assertIn(card.ref,options)
        for bad in excluded:
            self.assertNotIn(bad.ref,options);self.reject(s,seat,{'action_id':'choose','cards':[bad.ref]})
        wrong='A' if seat!='A' else 'D';command={'action_id':'choose','cards':[card.ref]};self.reject(s,wrong,command)
        s=self.replay(s,load=True);result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));return s

    def test_actual_molder_slug_survives_source_departure_and_borrowed_artifact_goes_to_owner(self):
        s=self.session(7132100);source=self.add(s.engine,'Molder Slug');selected=self.add(s.engine,'Memnite')
        gift=self.add(s.engine,'Donate',zone='hand');bad=self.add(s.engine,'Grizzly Bears',seat='B')
        bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand');self.add(s.engine,'Island',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='ordinary-molder-private')
        self.assert_closed(s,source);self.prepare(s,mana={'C':2,'U':1});self.checkpoint(s)
        self.cast_for(s,'A',gift,['B',selected.ref]);self.assertIsNone(self.resolve(s));self.assertEqual('B',s.state.cards[selected.object_id].controller)
        self.until(s,lambda ss:ss.state.active_player=='B' and ss.state.phase=='beginning' and ss.state.step=='upkeep')
        self.cast_for(s,'B',bounce,[source.ref]);self.resolve_top(s);self.assertEqual('hand',s.state.cards[source.object_id].zone)
        s=self.choose(s,'B',selected,excluded=(bad,));self.assertEqual('graveyard',s.state.cards[selected.object_id].zone)
        self.assertIn(selected.object_id,s.state.players['A'].zones['graveyard']);self.assertNotIn(selected.object_id,s.state.players['B'].zones['graveyard'])
        self.assertEqual('battlefield',s.state.cards[bad.object_id].zone);self.finish(s,private)

    def test_actual_ruthless_winnower_excludes_elf_and_changeling_and_selects_nonelf(self):
        s=self.session(7132110);source=self.add(s.engine,'Ruthless Winnower')
        elf=self.add(s.engine,'Llanowar Elves',seat='B');changeling=self.add(s.engine,'Woodland Changeling',seat='B')
        selected=self.add(s.engine,'Grizzly Bears',seat='B');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='ordinary-winnower-private')
        self.assert_closed(s,source);self.prepare(s);self.checkpoint(s)
        self.until(s,lambda ss:ss.state.active_player=='B' and ss.state.phase=='beginning' and ss.state.step=='upkeep')
        s=self.choose(s,'B',selected,excluded=(elf,changeling));self.assertEqual('graveyard',s.state.cards[selected.object_id].zone)
        self.assertEqual(['battlefield','battlefield'],[s.state.cards[c.object_id].zone for c in (elf,changeling)]);self.finish(s,private)

    def test_actual_sheoldred_reanimates_on_own_upkeep_and_only_opponent_sacrifices_on_theirs(self):
        s=self.session(7132120);source=self.add(s.engine,'Sheoldred, Whispering One')
        returned=self.add(s.engine,'Grizzly Bears',zone='graveyard',ref='ordinary-reanimation')
        selected=self.add(s.engine,'Grizzly Bears',seat='B',ref='ordinary-opponent-choice')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='ordinary-sheoldred-private')
        s.state.current_turn=TurnEntry('ordinary-initial-D','D');s.state.last_normal_turn_player='D'
        self.assert_closed(s,source);self.prepare(s,actor='D',active='D');self.checkpoint(s)
        self.target(s,'A',[returned.ref]);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[returned.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.until(s,lambda ss:ss.state.active_player=='A' and ss.state.phase=='precombat_main')
        land=next(a for a in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('play-land:'))
        self.assertTrue(s.act('pilot:A',{'action_id':land['id']}).ok)
        self.until(s,lambda ss:ss.state.active_player=='B' and ss.state.phase=='beginning' and ss.state.step=='upkeep')
        s=self.choose(s,'B',selected);self.assertEqual('graveyard',s.state.cards[selected.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[returned.object_id].zone);self.finish(s,private)

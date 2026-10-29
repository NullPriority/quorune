"""Per-caster matching spell ordinals survive later casts and observer changes."""

from copy import deepcopy
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ROOT
from test_qualified_zone_event_queries import query_record
import test_qualified_public_history as native
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.model import TurnHistory,TurnHistoryEvent
from quorune.public_history_query_model import PublicHistoryQuerySpec
from quorune.spell_history_ordinal_model import QualifiedSpellOrdinalSpec,qualified_spell_ordinal_dependencies,QUALIFIED_SPELL_ORDINAL_CAPABILITY as CAPABILITY
from quorune.rules.spell_history_ordinals import qualified_spell_cast_number
from quorune.compiler.spell_cast_predicates import fixed_spell_cast_binding_spec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class QualifiedSpellOrdinalCompilerTests(unittest.TestCase):
    def scene(self):
        history=TurnHistory(turn_sequence=3,spell_origin_history_version=1,events=[
            TurnHistoryEvent('spell_cast',actor='A',object_incarnation='creature@1',types=('creature',),origin='hand'),
            TurnHistoryEvent('spell_cast',actor='B',object_incarnation='foreign@1',types=('enchantment',),origin='hand'),
            TurnHistoryEvent('spell_cast',actor='A',object_incarnation='first@1',types=('artifact','enchantment'),origin='hand'),
            TurnHistoryEvent('spell_cast',actor='A',object_incarnation='later@1',types=('enchantment',),origin='hand')])
        state=SimpleNamespace(turn_history=history,turn_sequence=3,players={s:SimpleNamespace(in_game=True) for s in 'ABCD'},cards={})
        context={'controller':'A','logical_object_id':'first@1','types':['artifact','enchantment']}
        spec=QualifiedSpellOrdinalSpec(PublicHistoryQuerySpec('spell_cast',types_any=('artifact','enchantment')),1)
        return state,context,spec

    def test_closed_ordinal_codec_and_existing_unqualified_spell_predicates(self):
        state,context,spec=self.scene();self.assertEqual(spec,QualifiedSpellOrdinalSpec.from_condition(spec.to_condition()))
        for change in ({'value':True},{'value':0},{'value':11},{'schema_version':True},{'op':'gte'},{'extra':1}):
            with self.assertRaises(ValueError):QualifiedSpellOrdinalSpec.from_condition({**spec.to_condition(),**change})
        for text,number in (('first',1),('second',2),('third',3),('fourth',4)):
            parsed=fixed_spell_cast_binding_spec('Whenever you cast your '+text+' spell each turn, draw a card.')
            self.assertEqual(number,parsed.subject.caster_spell_number);self.assertIsNone(parsed.subject.qualified_spell_number)
            self.assertNotIn(CAPABILITY,qualified_spell_ordinal_dependencies(parsed.subject.event_condition))
        for text in ('Whenever you cast your first blue spell each turn, draw a card.',
                'Whenever you cast your first spell with {X} in its mana cost each turn, draw a card.',
                'Whenever an opponent casts their first instant spell from your graveyard each turn, draw a card.'):
            self.assertIsNone(fixed_spell_cast_binding_spec(text))

    def test_ordinal_is_matching_prefix_per_caster_not_whole_turn_or_current_objects(self):
        state,context,spec=self.scene();before=state.turn_history.to_dict()
        self.assertEqual(1,qualified_spell_cast_number(state,spec.to_condition(),context))
        self.assertEqual(2,qualified_spell_cast_number(state,spec.to_condition(),{**context,'logical_object_id':'later@1','types':['enchantment']}))
        self.assertEqual(1,qualified_spell_cast_number(state,spec.to_condition(),{**context,'controller':'B','logical_object_id':'foreign@1','types':['enchantment']}))
        self.assertEqual(0,qualified_spell_cast_number(state,spec.to_condition(),{**context,'logical_object_id':'creature@1','types':['creature']}))
        self.assertEqual(before,state.turn_history.to_dict())

    def test_missing_stale_ambiguous_and_unsealed_history_is_unavailable(self):
        state,context,spec=self.scene()
        for history in (None,TurnHistory(turn_sequence=2)):
            with self.assertRaises(ValueError):qualified_spell_cast_number(replace_state(state,history),spec.to_condition(),context)
        for changed in ({'logical_object_id':'missing@1'},{'types':['creature']},{'controller':'unknown'}):
            with self.assertRaises(ValueError):qualified_spell_cast_number(state,spec.to_condition(),{**context,**changed})
        state.turn_history.events.append(state.turn_history.events[2])
        with self.assertRaises(ValueError):qualified_spell_cast_number(state,spec.to_condition(),context)

    def test_origin_filter_uses_recorded_cast_zones_and_prefix_before_later_casts(self):
        state,context,_=self.scene();query=PublicHistoryQuerySpec('spell_cast',types_all=('enchantment',),schema_version=2,origin='graveyard')
        spec=QualifiedSpellOrdinalSpec(query,1)
        state.turn_history.events[2]=replace(state.turn_history.events[2],origin='graveyard')
        self.assertEqual(1,qualified_spell_cast_number(state,spec.to_condition(),context))
        state.turn_history.spell_origin_history_version=None
        with self.assertRaises(ValueError):qualified_spell_cast_number(state,spec.to_condition(),context)

    def test_whole_turn_count_and_unfiltered_caster_number_mutants_are_killed(self):
        import quorune.rules.spell_history_ordinals as owner
        state,context,spec=self.scene()
        def witness():self.assertEqual(1,owner.qualified_spell_cast_number(state,spec.to_condition(),context))
        witness()
        for wrong in (2,3):
            with patch.object(owner,'qualified_spell_cast_number',return_value=wrong),self.assertRaises(AssertionError):witness()

    def test_qualified_grammar_composes_with_results_and_verifies_withdrawal(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        registry=load_default_capability_registry()
        for subject in ('you cast your first enchantment spell','an opponent casts their first noncreature spell',
                'a player casts their second artifact spell','you cast your first instant or sorcery spell from your graveyard'):
            text='Whenever '+subject+' each turn, draw a card.'
            ir=compile_oracle_card(query_record(text),capability_registry=registry)
            self.assertEqual('exact',ir.status,ir.material_residuals);self.assertIn(CAPABILITY,ir.faces[0].nodes[0].capability_dependencies)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['matching ordinal owner withdrawn'])
        changed=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        changed.mark_evidence_verified(validate_capability_evidence_index(index,registry=changed))
        self.assertNotEqual('exact',compile_oracle_card(query_record('Whenever you cast your first enchantment spell each turn, draw a card.'),capability_registry=changed).status)
        self.assertEqual('exact',compile_oracle_card(query_record('Whenever you cast your first spell each turn, draw a card.'),capability_registry=changed).status)


def replace_state(state,history):return SimpleNamespace(**{**vars(state),'turn_history':history})


class QualifiedSpellOrdinalNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=native.QualifiedPublicHistoryNativeTests.session
    add=native.QualifiedPublicHistoryNativeTests.add
    prepare=native.QualifiedPublicHistoryNativeTests.prepare
    cast=native.QualifiedPublicHistoryNativeTests.cast
    cast_for=native.QualifiedPublicHistoryNativeTests.cast_for
    action=native.QualifiedPublicHistoryNativeTests.action
    priority=native.QualifiedPublicHistoryNativeTests.priority
    checkpoint=native.QualifiedPublicHistoryNativeTests.checkpoint
    resolve=native.QualifiedPublicHistoryNativeTests.resolve
    replay=native.QualifiedPublicHistoryNativeTests.replay
    assert_closed=native.QualifiedPublicHistoryNativeTests.assert_closed
    until=native.QualifiedPublicHistoryNativeTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'qualified-spell-ordinals.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/qualified-spell-ordinal-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Spell ordinals',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_runtime_qualified_ordinal_dependency_is_inferred_without_declaration(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session=self.session(603634001);source=self.add(session.engine,'Tuvasa the Sunlit');checked=0
        for program in session.engine.semantics.programs_for_oracle(source.oracle_id):
            if CAPABILITY not in program.capability_dependencies:continue
            checked+=1;deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,result['blockers'])
        self.assertGreater(checked,0)

    def test_actual_tuvasa_counts_matching_cast_not_all_spells_and_queued_draw_survives_responses(self):
        session=self.session(603634010);source=self.add(session.engine,'Tuvasa the Sunlit',zone='hand')
        nonmatch=self.add(session.engine,'Memnite',zone='hand');first=self.add(session.engine,'Fervor',zone='hand');later=self.add(session.engine,'Levitation',zone='hand')
        self.add(session.engine,'Vedalken Orrery');bounce=self.add(session.engine,'Unsummon',seat='B',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':4,'G':1,'W':1,'U':3,'R':1});session.state.players['B'].mana_pool['U']=1;self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':4,'U':2,'R':1});self.assertIsNone(self.resolve(session))
        self.cast_for(session,'A',nonmatch,reserve={'C':4,'U':2,'R':1});self.assertIsNone(self.resolve(session))
        self.priority(session,'A');offered=self.action(session,'A',first);before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':offered['id'],'pay':'auto'}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast_for(session,'A',first,reserve={'C':2,'U':2})
        self.assertEqual(1,sum(i.source_object_id==source.object_id for i in session.state.stack))
        self.cast(session,'A',later);self.assertEqual(1,sum(i.source_object_id==source.object_id for i in session.state.stack))
        self.cast(session,'B',bounce,[source.ref]);session=self.replay(session,load=True)
        self.until(session,lambda s:s.state.cards[source.object_id].zone=='hand')
        hand=len(session.state.players['A'].zones['hand']);expected=session.state.cards[session.state.players['A'].zones['library'][-1]].ref
        self.assertIsNone(self.resolve(session));self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']))
        for seat in 'BCD':self.assertNotIn(expected,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_psemilla_earlier_countered_enchantment_before_source_entry_consumes_first(self):
        session=self.session(603634020);first=self.add(session.engine,'Fervor',zone='hand');source=self.add(session.engine,'Psemilla, Meletian Poet',zone='hand')
        later=self.add(session.engine,'Levitation',zone='hand');counter=self.add(session.engine,'Counterspell',seat='B',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':6,'R':1,'W':1,'U':2});session.state.players['B'].mana_pool['U']=2;self.checkpoint(session)
        self.cast_for(session,'A',first,reserve={'C':4,'W':1,'U':2});self.cast(session,'B',counter,[session.state.stack[-1].ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[first.object_id].zone)
        self.cast_for(session,'A',source,reserve={'C':2,'U':2});self.assertIsNone(self.resolve(session))
        self.cast(session,'A',later);self.assertFalse(any(i.source_object_id==source.object_id for i in session.state.stack))
        self.assertIsNone(self.resolve(session));self.assertFalse(any(c.zone=='battlefield' and c.is_token for c in session.state.cards.values()));self.replay(session,load=True)

    def test_actual_valeria_noncreature_ordinal_excludes_artifact_creature_and_counts_free_spell(self):
        session=self.session(603634030);source=self.add(session.engine,'Valeria Richards, Precocious',zone='hand')
        body=self.add(session.engine,'Memnite',zone='hand');first=self.add(session.engine,'Short Sword',zone='hand');later=self.add(session.engine,'Fervor',zone='hand')
        self.assert_closed(session,source);self.prepare(session,mana={'C':4,'U':1,'R':1});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'C':1,'R':1});self.assertIsNone(self.resolve(session))
        self.cast_for(session,'A',body,reserve={'C':1,'R':1});self.assertIsNone(self.resolve(session))
        mana=dict(session.state.players['A'].mana_pool);self.cast_for(session,'A',first,reserve={'C':1,'R':1})
        self.assertEqual(mana,session.state.players['A'].mana_pool)
        hand=len(session.state.players['A'].zones['hand']);self.assertIsNone(self.resolve(session));self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']))
        self.cast(session,'A',later);hand=len(session.state.players['A'].zones['hand']);self.assertIsNone(self.resolve(session));self.assertEqual(hand,len(session.state.players['A'].zones['hand']));self.replay(session,load=True)

    def test_actual_shadow_first_noncreature_is_separate_for_each_foreign_caster(self):
        session=self.session(603634040);source=self.add(session.engine,'Shadow in the Warp',zone='hand')
        creature=self.add(session.engine,'Memnite',seat='B',zone='hand');first=self.add(session.engine,'Short Sword',seat='B',zone='hand',ref='B-sword')
        foreign=self.add(session.engine,'Short Sword',seat='C',zone='hand',ref='C-sword');later=self.add(session.engine,'Sol Ring',seat='B',zone='hand')
        for seat in 'BC':self.add(session.engine,'Vedalken Orrery',seat=seat,ref=seat+'-orrery')
        self.assert_closed(session,source);self.prepare(session,mana={'C':1,'R':1,'G':1});session.state.players['B'].mana_pool['C']=2;session.state.players['C'].mana_pool['C']=1;self.checkpoint(session)
        self.cast(session,'A',source);self.assertIsNone(self.resolve(session));life={s:session.state.players[s].life for s in 'ABCD'}
        self.cast(session,'B',creature);self.assertIsNone(self.resolve(session));self.assertEqual(life['B'],session.state.players['B'].life)
        self.cast_for(session,'B',first,reserve={'C':1});self.assertIsNone(self.resolve(session));self.assertEqual(life['B']-2,session.state.players['B'].life)
        self.cast(session,'C',foreign);session=self.replay(session,load=True);self.assertIsNone(self.resolve(session));self.assertEqual(life['C']-2,session.state.players['C'].life)
        self.cast(session,'B',later);self.assertIsNone(self.resolve(session));self.assertEqual(life['B']-2,session.state.players['B'].life)
        for seat in 'AD':self.assertEqual(life[seat],session.state.players[seat].life)
        self.replay(session,load=True)

    def test_actual_station_monitor_declares_token_restriction_and_blocks_after_creator_leaves(self):
        session=self.session(603634050);source=self.add(session.engine,'Station Monitor',zone='hand')
        second=self.add(session.engine,'Memnite',zone='hand');bounce=self.add(session.engine,'Unsummon',zone='hand');self.add(session.engine,'Vedalken Orrery')
        flying=self.add(session.engine,'Wind Drake',seat='B');ground=self.add(session.engine,'Grizzly Bears',seat='B')
        for card in (flying,ground):card.acquired_control_turn_count=-1
        self.assert_closed(session,source);self.prepare(session,active='B',mana={'W':1,'U':2});self.checkpoint(session)
        self.cast_for(session,'A',source,reserve={'U':1});self.assertIsNone(self.resolve(session))
        self.cast_for(session,'A',second,reserve={'U':1});self.assertIsNone(self.resolve(session))
        token=next(c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token)
        self.cast(session,'A',bounce,[source.ref]);self.assertIsNone(self.resolve(session));self.assertEqual('hand',session.state.cards[source.object_id].zone)
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.attackers')
        result=session.act('pilot:B',{'action_id':'attack','attackers':{flying.ref:'A',ground.ref:'A'}});self.assertTrue(result.ok,result.summary)
        self.until(session,lambda s:s.state.pending_decision is not None and s.state.pending_decision.kind=='combat.blockers')
        self.assertFalse(session.engine._can_block(session.state.cards[ground.object_id],session.state.cards[token.object_id])[0])
        self.assertTrue(session.engine._can_block(session.state.cards[flying.object_id],session.state.cards[token.object_id])[0])
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'block','blocks':{token.ref:ground.ref}}).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'block','blocks':{token.ref:flying.ref}});self.assertTrue(result.ok,result.summary)
        life=session.state.players['A'].life;session=self.replay(session,load=True)
        self.until(session,lambda s:s.state.phase=='postcombat_main' and s.state.step=='main')
        self.assertEqual(life-2,session.state.players['A'].life)
        current=session.state.cards.get(token.object_id);self.assertTrue(current is None or current.zone!='battlefield');self.replay(session,load=True)

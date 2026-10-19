from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import json,tempfile,unittest
from common import ROOT
from quorune.preparation_model import PrepareSpellSpec,PreparedDesignation,PREPARED_FIELD,PREPARE_COPY_FIELD
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.carddb import CardRecord
import test_fixed_reveal_additional_costs as reveal


def preparation_record(text='This creature enters prepared.'):
    front={'name':'Generic Preparation Creature','mana_cost':'{2}{B}','type_line':'Creature — Human',
        'oracle_text':text,'power':'2','toughness':'3','colors':['B']}
    inset={'name':'Generic Preparation Spell','mana_cost':'{B}','type_line':'Sorcery','oracle_text':'Draw a card.','colors':['B']}
    return CardRecord(oracle_id='fixture:preparation',name=front['name']+' // '+inset['name'],mana_cost='{2}{B}',mana_value=3,
        type_line=front['type_line'],oracle_text=text,power='2',toughness='3',loyalty=None,defense=None,colors=('B',),color_identity=('B',),
        keywords=(),produced_mana=(),layout='prepare',released_at='2026-01-01',legalities={'commander':'legal'},faces=(front,inset),raw={})


class PreparationModelCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.registry=load_default_capability_registry()

    def test_prepared_entry_pins_the_inset_spell_and_complete_source_contract(self):
        card=preparation_record();ir=compile_oracle_card(card,capability_registry=self.registry)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node,=ir.faces[0].nodes;self.assertEqual(card.faces[0]['oracle_text'],node.text)
        descriptor,=node.handlers;self.assertIs(True,descriptor['requires_complete_card_program'])
        spec=PrepareSpellSpec.from_dict(descriptor['spell']);self.assertEqual(('Generic Preparation Spell','{B}',1,'Sorcery'),
            (spec.face_id,spec.mana_cost,spec.mana_value,spec.type_line))
        self.assertEqual(spec,PrepareSpellSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
        value=PreparedDesignation('id','REF','id@1',spec,'copy')
        self.assertEqual(value,PreparedDesignation.from_dict(json.loads(json.dumps(value.to_dict()))))

    def test_missing_alternative_unknown_preparation_and_malformed_links_remain_closed(self):
        card=preparation_record()
        for row in (replace(card,layout='normal',faces=()),preparation_record('This creature enters prepared twice.')):
            self.assertNotEqual('exact',compile_oracle_card(row,capability_registry=self.registry).status)
        from quorune.semantic_runtime.preparation import PreparationEntryHandler
        spec=PrepareSpellSpec('id','Inset','{U}',1,'Instant',('U',))
        raw={'handler_id':'replacement.zone.prepared-entry.v1','schema_version':1,'event':'zone.change',
            'spell':spec.to_dict(),'requires_complete_card_program':True}
        for change in ({'schema_version':True},{'requires_complete_card_program':False},{'extra':1}):
            with self.assertRaises(ValueError):PreparationEntryHandler().validate({**raw,**change})
        with self.assertRaises(ValueError):PrepareSpellSpec.from_dict({**spec.to_dict(),'mana_value':True})

    def test_prepare_copy_sba_exception_is_only_for_the_captured_exile_kind(self):
        from quorune.state_based_actions import ObjectSnapshot,evaluate_state_based_actions
        batch=evaluate_state_based_actions(permanents=(),objects=(
            ObjectSnapshot('ordinary-exile','exile',is_card_copy=True),
            ObjectSnapshot('retained-prepare','exile',is_card_copy=True,retained_prepare_spell=True),
            ObjectSnapshot('not-exile','graveyard',is_card_copy=True,retained_prepare_spell=True),
            ObjectSnapshot('not-cardcopy','exile',is_spell_copy=True,retained_prepare_spell=True)))
        self.assertEqual({'ordinary-exile','not-exile','not-cardcopy'},set(batch.cease))

    def test_unsupported_source_or_inset_sibling_rejects_the_complete_preparation_program(self):
        from types import SimpleNamespace
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        card=preparation_record()
        for index in (0,1):
            faces=[dict(face) for face in card.faces];faces[index]['oracle_text']+='\nTurn every moon into a pancake.'
            program=compile_best_available_card_program(SimpleNamespace(rulings=lambda _:()),replace(card,faces=tuple(faces)),
                semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
            self.assertFalse(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])

    def test_physical_prepare_front_face_preserves_case_insensitive_selection(self):
        from types import SimpleNamespace
        from quorune.preparation import preparation_cast_face
        from quorune.errors import GameRuleError
        card=preparation_record();physical=SimpleNamespace(annotations={})
        self.assertEqual('generic preparation creature',preparation_cast_face(physical,card,'generic preparation creature'))
        with self.assertRaises(GameRuleError):preparation_cast_face(physical,card,'Generic Preparation Spell')


class PreparationRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=reveal.FixedRevealCostRuntimeTests.session
    add=reveal.FixedRevealCostRuntimeTests.add
    fund=reveal.FixedRevealCostRuntimeTests.fund
    checkpoint=reveal.FixedRevealCostRuntimeTests.checkpoint
    resolve=reveal.FixedRevealCostRuntimeTests.resolve
    replay=reveal.FixedRevealCostRuntimeTests.replay
    reject=reveal.FixedRevealCostRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from scripts.build_test_database import build_fixture_database
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'preparation.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/preparation-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Preparation witness',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def cast_prepared_creature(self,session,name,*,mana,ref='prepared-creature'):
        source=self.add(session.engine,name,zone='hand',ref=ref);self.fund(session,mana)
        result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        return session.state.cards[source.object_id]

    def prepare_copy(self,session,source):
        self.assertIn(PREPARED_FIELD,session.state.cards[source.object_id].annotations)
        designation=PreparedDesignation.from_dict(session.state.cards[source.object_id].annotations[PREPARED_FIELD])
        return session.state.cards[designation.copy_id]

    def test_actual_entry_creates_public_persistent_inset_copy_and_native_cast_unprepares_once(self):
        session=self.session(7223);source=self.cast_prepared_creature(session,'Adventurous Eater',mana={'B':1,'C':2})
        session=self.replay(session,load=True)
        copy=self.prepare_copy(session,source);self.assertEqual('Have a Bite',session.engine.display_name(copy.object_id))
        self.assertTrue(copy.is_card_copy);self.assertEqual('exile',copy.zone)
        target=self.add(session.engine,'Generic Bound Body',ref='prepare-result-target')
        private=self.add(session.engine,'Generic Bound Growth',zone='hand',ref='prepare-private-hand')
        self.fund(session,{'B':1});self.checkpoint(session)
        for viewer in 'BCD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        for viewer in 'ABCD':self.assertIn('"prepared": true',json.dumps(session.packet('pilot:'+viewer,full=True)))
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==copy.ref)
        session=self.replay(session,load=True);life=session.state.players['A'].life
        self.reject(session,'B',action_id=action['id'],targets=[target.ref],pay='auto')
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertNotIn(PREPARED_FIELD,session.state.cards[source.object_id].annotations)
        self.assertEqual('stack',session.state.cards[copy.object_id].zone);self.assertEqual(1,len(session.state.stack))
        session=self.replay(session,load=True);self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.cards[target.object_id].counters.get('+1/+1',0));self.assertEqual(life+1,session.state.players['A'].life)
        self.assertEqual('outside',session.state.cards[copy.object_id].zone);self.replay(session,load=True)

    def test_physical_card_cannot_cast_inset_and_failed_copy_cast_keeps_preparation(self):
        session=self.session(7224);source=self.add(session.engine,'Adventurous Eater',zone='hand',ref='physical-prepare')
        self.fund(session,{'B':1,'C':2})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertEqual(['cast:'+source.ref],[row['id'] for row in actions if row.get('card')==source.ref])
        self.reject(session,action_id='cast:'+source.ref,face='Have a Bite',pay='auto')
        result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));copy=self.prepare_copy(session,source)
        self.fund(session,{})
        self.reject(session,action_id='cast:'+copy.ref+':face:Have a Bite',targets=[source.ref],pay='auto')
        self.assertIn(PREPARED_FIELD,session.state.cards[source.object_id].annotations)
        self.assertEqual('exile',session.state.cards[copy.object_id].zone);self.replay(session,load=True)

    def test_current_controller_owns_the_copy_permission_after_control_and_ability_loss(self):
        session=self.session(7225);source=self.cast_prepared_creature(session,'Blazing Firesinger',mana={'R':1,'C':2})
        copy=self.prepare_copy(session,source);self.add(session.engine,'Humility',ref='prepare-ability-loss')
        session.engine.change_control(source.object_id,'B',reason='Prepared permanent current controller boundary')
        self.fund(session,{'R':1,'C':2},actor='B');self.assertEqual('A',session.state.cards[copy.object_id].owner)
        actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        offered=[row for row in actions if row.get('card')==copy.ref];self.assertTrue(offered)
        action=offered[0]
        self.reject(session,'A',action_id=action['id'],pay='auto')
        session=self.replay(session,load=True)
        result=session.act('pilot:B',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertNotIn(PREPARED_FIELD,session.state.cards[source.object_id].annotations)
        self.assertIsNone(self.resolve(session));self.assertEqual(5,session.state.players['B'].mana_pool['R'])
        self.assertEqual(0,session.state.players['A'].mana_pool['R']);self.replay(session,load=True)

    def test_countering_accepted_prepare_spell_keeps_the_source_unprepared(self):
        session=self.session(7226);source=self.cast_prepared_creature(session,'Adventurous Eater',mana={'B':1,'C':2})
        copy=self.prepare_copy(session,source);counter=self.add(session.engine,'Counterspell',seat='B',zone='hand',ref='prepare-counterspell')
        session.state.players['B'].mana_pool.update({'U':2});self.fund(session,{'B':1})
        action=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if row.get('card')==copy.ref)
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        spell=session.state.stack[-1];self.assertNotIn(PREPARED_FIELD,session.state.cards[source.object_id].annotations)
        session=self.replay(session,load=True);self.assertTrue(session.act('pilot:A',{'action_id':'pass'}).ok)
        result=session.act('pilot:B',{'action_id':'cast:'+counter.ref,'targets':[spell.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertFalse(session.state.cards[source.object_id].counters)
        self.assertNotIn(PREPARED_FIELD,session.state.cards[source.object_id].annotations)
        self.assertEqual('outside',session.state.cards[copy.object_id].zone);self.replay(session,load=True)

    def test_source_departure_and_phase_boundary_end_old_copy_without_copying_prepared_status(self):
        from quorune.preparation import become_prepared,preparation_cast_permission
        session=self.session(7227);source=self.cast_prepared_creature(session,'Adventurous Eater',mana={'B':1,'C':2})
        copy=self.prepare_copy(session,source)
        self.assertFalse(become_prepared(session.engine,source))
        vanilla=self.add(session.engine,'Generic Bound Body',ref='cannot-prepare-vanilla')
        self.assertFalse(become_prepared(session.engine,vanilla))
        source.phased_out=True;session.engine._stabilize()
        self.assertIn(PREPARED_FIELD,session.state.cards[source.object_id].annotations)
        self.assertEqual('outside',session.state.cards[copy.object_id].zone)
        self.assertFalse(preparation_cast_permission(session.engine,'A',copy))
        source.phased_out=False;session.engine._stabilize();second=self.prepare_copy(session,source)
        self.assertNotEqual(copy.object_id,second.object_id)
        session.engine.move_card(source.object_id,'exile',log=False);session.engine._stabilize()
        self.assertNotIn(PREPARED_FIELD,session.state.cards[source.object_id].annotations)
        self.assertEqual('outside',session.state.cards[second.object_id].zone)
        session.engine.move_card(source.object_id,'battlefield',controller='A',log=False);session.engine._stabilize()
        third=self.prepare_copy(session,source);self.assertNotEqual(second.object_id,third.object_id)
        self.assertFalse(preparation_cast_permission(session.engine,'A',second))
        self.fund(session,{'B':1});self.replay(session,load=True)

    def test_all_fourteen_originals_have_complete_bindings_for_both_characteristic_sets(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        rows=json.loads((ROOT/'tests/fixtures/preparation-cards.json').read_text(encoding='utf-8'))['cards']
        for row in (row for row in rows if row['layout']=='prepare'):
            with self.subTest(name=row['name']):
                card=self.db.by_oracle_id(row['oracle_id']);program=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry,capability_profile='commander_review')
                binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
                self.assertTrue(binding['strict_capability_ready'],binding['blockers'])

    def test_prepare_spell_uses_its_own_mana_draw_and_token_results(self):
        cases=(('Blazing Firesinger','Seething Song','mana'),('Landscape Painter','Vibrant Idea','draw'),
            ('Goblin Glasswright','Craft with Pride','treasure'),('Campus Composer','Aqueous Aria','elemental'),
            ('Strife Scholar','Awaken the Ages','spirits'))
        for index,(name,inset,result_kind) in enumerate(cases):
            with self.subTest(name=name):
                session=self.session(7230+index);source=self.cast_prepared_creature(session,name,mana={'W':8,'U':8,'B':8,'R':8,'G':8,'C':12})
                copy=self.prepare_copy(session,source);self.assertEqual(inset,session.engine.display_name(copy.object_id))
                self.fund(session,{'W':4,'U':6,'B':4,'R':4,'G':4,'C':8});hand=len(session.state.players['A'].zones['hand'])
                before_mana=session.state.players['A'].mana_pool['R']
                action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==copy.ref)
                result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(session));self.assertNotIn(PREPARED_FIELD,session.state.cards[source.object_id].annotations)
                if result_kind=='draw':self.assertEqual(hand+2,len(session.state.players['A'].zones['hand']))
                elif result_kind=='mana':self.assertEqual(before_mana-1+5,session.state.players['A'].mana_pool['R'])
                else:
                    tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield']
                    self.assertEqual(2 if result_kind=='spirits' else 1,len(tokens))
                    for token in tokens:
                        data=session.engine._effective_card_data(token)
                        if result_kind=='treasure':self.assertIn('Treasure',data['type_line'])
                        else:
                            self.assertEqual((2,2) if result_kind=='spirits' else (3,3),tuple(session.engine._numeric_stat(token.object_id,k) for k in ('power','toughness')))
                            if result_kind=='elemental':self.assertIn('flying',session.engine._combat_keywords(token))
                self.replay(session,load=True)

    def test_prepare_spell_targeted_growth_and_zone_results_use_normal_owners(self):
        for index,(name,result_kind) in enumerate((('Honorbound Page','flying'),('Quill-Blade Laureate','double_strike'),
                ('Maelstrom Artisan','destroy'),('Cheerful Osteomancer','return'),('Skycoach Conductor','blink'))):
            with self.subTest(name=name):
                session=self.session(7240+index);source=self.cast_prepared_creature(session,name,mana={'W':8,'U':8,'B':8,'R':8,'G':8,'C':12})
                target=self.add(session.engine,'Command Tower' if result_kind=='destroy' else 'Generic Bound Body',
                    zone='graveyard' if result_kind=='return' else 'battlefield',ref='prepare-target')
                old=target.logical_object_id;copy=self.prepare_copy(session,source)
                self.fund(session,{'W':4,'U':4,'B':4,'R':4,'G':4,'C':8})
                action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==copy.ref)
                result=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(session));card=session.state.cards[target.object_id]
                if result_kind in {'flying','double_strike'}:
                    self.assertEqual((3,6),tuple(session.engine._numeric_stat(card.object_id,k) for k in ('power','toughness')))
                    self.assertIn('flying' if result_kind=='flying' else 'double strike',session.engine._combat_keywords(card))
                else:
                    self.assertEqual('graveyard' if result_kind=='destroy' else 'hand' if result_kind=='return' else 'battlefield',card.zone)
                    if result_kind=='blink':self.assertNotEqual(old,card.logical_object_id)
                self.replay(session,load=True)

    def test_inset_search_and_private_discard_choices_resume_without_preparing_again(self):
        for index,name in enumerate(('Studious First-Year','Spellbook Seeker')):
            with self.subTest(name=name):
                session=self.session(7250+index);source=self.cast_prepared_creature(session,name,mana={'U':4,'G':2,'C':5})
                if index==0:land=self.add(session.engine,'Generic Bound Plains',zone='library',ref='prepare-search-land')
                else:
                    discards=[self.add(session.engine,'Generic Bound Growth',zone='hand',ref='prepare-private-discard-'+str(i)) for i in range(2)]
                copy=self.prepare_copy(session,source);self.fund(session,{'U':1,'G':1,'C':1});hand=len(session.state.players['A'].zones['hand'])
                action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==copy.ref)
                result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
                self.assertIsNotNone(self.resolve(session));self.assertNotIn(PREPARED_FIELD,session.state.cards[source.object_id].annotations)
                session=self.replay(session,load=True)
                if index==0:
                    self.reject(session,'B',action_id='choose',search_cards=[land.ref])
                    result=session.act('pilot:A',{'action_id':'choose','search_cards':[land.ref]})
                else:
                    for viewer in 'BCD':self.assertTrue(all(c.ref not in json.dumps(session.packet('pilot:'+viewer,full=True)) for c in discards))
                    result=session.act('pilot:A',{'action_id':'choose','cards':[c.ref for c in discards]})
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
                if index==0:self.assertEqual(('battlefield',True),(session.state.cards[land.object_id].zone,session.state.cards[land.object_id].tapped))
                else:self.assertEqual(hand,len(session.state.players['A'].zones['hand']))
                self.replay(session,load=True)

    def test_rejoinder_tap_untap_and_decline_choices_all_continue_to_one_draw(self):
        for index,choice in enumerate(('tap','untap','decline')):
            with self.subTest(choice=choice):
                session=self.session(7252+index);source=self.cast_prepared_creature(session,'Elite Interceptor',mana={'W':1})
                target=self.add(session.engine,'Generic Bound Body',ref='rejoinder-target');target.tapped=choice=='untap'
                copy=self.prepare_copy(session,source);self.fund(session,{'W':1,'C':1});hand=len(session.state.players['A'].zones['hand'])
                action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==copy.ref)
                result=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
                self.assertIsNotNone(self.resolve(session))
                result=session.act('pilot:A',{'action_id':'choose','choice':choice});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(session));self.assertEqual(choice=='tap',session.state.cards[target.object_id].tapped)
                self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']));self.replay(session,load=True)

    def test_copied_creature_entry_and_token_exceptions_retain_only_alternative_spell_values(self):
        for index,spell_name in enumerate(('Clone','Croaking Counterpart')):
            with self.subTest(spell=spell_name):
                session=self.session(7255+index);original=self.cast_prepared_creature(session,'Goblin Glasswright',mana={'R':1,'C':1})
                old_copy=self.prepare_copy(session,original)
                spell=self.add(session.engine,spell_name,zone='hand',ref='copy-preparation-creature')
                self.fund(session,{'U':1,'G':1,'C':3})
                response={'action_id':'cast:'+spell.ref,'pay':'auto'}
                if index:response['targets']=[original.ref]
                result=session.act('pilot:A',response);self.assertTrue(result.ok,result.summary)
                if not index:
                    self.assertEqual('replacement.order',self.resolve(session).kind)
                    ctx=session.packet('pilot:A',full=True)['decision']['ctx']
                    option=next(row['id'] for row in ctx['options'] if original.ref in row['id'] and not row['decline'])
                    result=session.act('pilot:A',{'action_id':'choose','replacement':option});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(session))
                copied=next(c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield') if index else session.state.cards[spell.object_id]
                new_copy=self.prepare_copy(session,copied);self.assertNotEqual(old_copy.object_id,new_copy.object_id)
                values=session.engine._effective_card_data(new_copy);self.assertEqual(('Craft with Pride','Sorcery',['R']),
                    (values['name'],values['type_line'],values['colors']))
                self.assertIsNone(values['power']);self.assertIsNone(values['toughness'])
                if index:
                    frog=session.engine._effective_card_data(copied);self.assertEqual(['G'],frog['colors']);self.assertIn('Frog',frog['type_line'])
                    self.assertEqual((1,1),tuple(session.engine._numeric_stat(copied.object_id,k) for k in ('power','toughness')))
                self.replay(session,load=True)

    def test_existing_humility_suppresses_entry_but_face_down_or_inset_copies_have_no_prepare_alternative(self):
        from quorune.preparation import compiled_prepare_spell,become_prepared
        session=self.session(7257);self.add(session.engine,'Humility',ref='before-entry-humility')
        source=self.add(session.engine,'Adventurous Eater',zone='hand',ref='unprepared-humble-source')
        self.fund(session,{'B':1,'C':2});result=session.act('pilot:A',{'action_id':'cast:'+source.ref,'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertNotIn(PREPARED_FIELD,session.state.cards[source.object_id].annotations)
        self.assertFalse(any(PREPARE_COPY_FIELD in c.annotations for c in session.state.cards.values()))
        # The current face-down characteristic boundary removes the alternative,
        # while a previously prepared permanent would retain its captured link.
        source=session.state.cards[source.object_id];source.face_down=True
        self.assertIsNone(compiled_prepare_spell(session.engine,source));self.assertFalse(become_prepared(session.engine,source))
        session=self.session(7258);source=self.cast_prepared_creature(session,'Adventurous Eater',mana={'B':1,'C':2})
        self.assertIsNone(compiled_prepare_spell(session.engine,self.prepare_copy(session,source)))
        self.replay(session,load=True)

    def test_inset_names_and_normal_card_names_remain_individually_nameable(self):
        session=self.session(7259)
        self.assertEqual({'have a bite':'Have a Bite'},session.engine._semantic_choice_canonical_names({'card_name':'Have a Bite'}))
        self.assertEqual({'adventurous eater':'Adventurous Eater'},session.engine._semantic_choice_canonical_names({'card_name':'Adventurous Eater'}))
        self.assertEqual({'counterspell':'Counterspell'},session.engine._semantic_choice_canonical_names({'card_name':'Counterspell'}))
        self.assertEqual({},session.engine._semantic_choice_canonical_names({'card_name':'Adventurous Eater // Have a Bite'}))

    def test_linked_copy_rejects_malformed_or_changed_source_identity(self):
        from quorune.preparation import preparation_cast_permission
        from quorune.errors import GameRuleError
        session=self.session(7260);source=self.cast_prepared_creature(session,'Adventurous Eater',mana={'B':1,'C':2})
        copy=self.prepare_copy(session,source);raw=dict(copy.annotations[PREPARE_COPY_FIELD])
        for change in ({'source_id':[]},{'source_identity':True},{'copy_id':[]},{'extra':1}):
            copy.annotations[PREPARE_COPY_FIELD]={**raw,**change}
            with self.assertRaises(GameRuleError):preparation_cast_permission(session.engine,'A',copy)
        copy.annotations[PREPARE_COPY_FIELD]={**raw,'source_identity':'stale@1'}
        self.assertFalse(preparation_cast_permission(session.engine,'A',copy))
        copy.annotations[PREPARE_COPY_FIELD]=raw
        self.assertTrue(preparation_cast_permission(session.engine,'A',copy))

    def test_pending_inset_mana_replacement_keeps_prepared_until_cast_succeeds(self):
        from quorune.damage_prevention_creation import PreventionShieldCreationRequest,PreventionSubjectAllocation,plan_prevention_shield_creation,commit_prevention_shield_creation
        from quorune.damage_modifier_state import PreventionMode,DamageModifierDuration
        session=self.session(7261);source=self.cast_prepared_creature(session,'Landscape Painter',mana={'U':1,'C':1})
        copy=self.prepare_copy(session,source);reef=self.add(session.engine,'Shivan Reef',ref='prepare-payment-reef')
        for label in ('one','two'):
            request=PreventionShieldCreationRequest(source_id='fixture:prepare-prevention:'+label,controller='A',mode=PreventionMode.AMOUNT,
                duration=DamageModifierDuration.UNTIL_END_OF_TURN,subjects=(PreventionSubjectAllocation('A',1),),label='Prevent '+label)
            commit_prevention_shield_creation(session.engine,plan_prevention_shield_creation(session.engine,request))
        self.fund(session,{'C':4});hand=len(session.state.players['A'].zones['hand'])
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==copy.ref)
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertIn(PREPARED_FIELD,session.state.cards[source.object_id].annotations);self.assertEqual('exile',session.state.cards[copy.object_id].zone)
        self.assertFalse(session.state.cards[reef.object_id].tapped);self.assertEqual(4,session.state.players['A'].mana_pool['C'])
        session=self.replay(session,load=True);ctx=session.packet('pilot:A',full=True)['decision']['ctx']
        result=session.act('pilot:A',{'action_id':'choose','replacement':ctx['options'][0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertNotIn(PREPARED_FIELD,session.state.cards[source.object_id].annotations)
        self.assertIsNone(self.resolve(session));self.assertEqual(hand+2,len(session.state.players['A'].zones['hand']));self.replay(session,load=True)

    def test_stack_copy_keeps_prepare_spell_status_and_source_is_consumed_only_once(self):
        session=self.session(7262);source=self.cast_prepared_creature(session,'Goblin Glasswright',mana={'R':1,'C':1})
        copy=self.prepare_copy(session,source);twincast=self.add(session.engine,'Twincast',seat='B',zone='hand',ref='copy-prepare-spell')
        session.state.players['B'].mana_pool.update({'U':2});self.fund(session,{'R':1})
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==copy.ref)
        result=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        original=session.state.stack[-1];self.assertTrue(session.act('pilot:A',{'action_id':'pass'}).ok)
        result=session.act('pilot:B',{'action_id':'cast:'+twincast.ref,'targets':[original.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        for _ in range(32):
            copied=next((item for item in session.state.stack if item.kind=='spell_copy'),None)
            if copied is not None:break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        else:self.fail('The prepare spell copy was not created')
        values=session.engine._copyable_characteristics(session.state.cards[copied.card_object_id])
        self.assertIs(True,values['is_prepare_spell']);self.assertNotIn(PREPARED_FIELD,session.state.cards[source.object_id].annotations)
        self.assertIsNone(self.resolve(session));self.assertEqual(2,len([card for card in session.state.cards.values() if card.is_token and card.zone=='battlefield']))
        self.replay(session,load=True)

    def test_preparation_producer_dependency_copy_lifetime_and_consumption_mutants_are_killed(self):
        from unittest.mock import patch
        from quorune.rules.capabilities import CapabilityRegistry
        card=preparation_record()
        with patch('quorune.compiler.preparation_nodes.preparation_entry_node',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=self.registry).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for dependency in ('card.preparation.fixed_entry_spell','zone.entry.fixed_copy','casting.cost.modifier.fixed_query','target.revalidate_resolution'):
            changed=json.loads(json.dumps(raw));next(row for row in changed['capabilities'] if row['id']==dependency).update(status='blocked',blockers=['Preparation dependency omitted'])
            self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=CapabilityRegistry(changed)).status)
        method='test_actual_entry_creates_public_persistent_inset_copy_and_native_cast_unprepares_once'
        for target,value in (('quorune.preparation._create_prepare_spell_copy',None),('quorune.preparation.preparation_copy_retained',False),
                ('quorune.preparation.consume_prepared_cast',None)):
            with patch(target,return_value=value):
                with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_source_departure_and_phase_boundary_end_old_copy_without_copying_prepared_status'
        with patch('quorune.preparation.preparation_copy_retained',return_value=True):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_current_controller_owns_the_copy_permission_after_control_and_ability_loss'
        with patch('quorune.preparation.preparation_cast_permission',side_effect=lambda host,seat,copy:copy.zone=='exile' and copy.owner==seat):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_existing_preparation_survives_new_noncreature_copiable_values_but_cannot_be_regained(self):
        from quorune.preparation import compiled_prepare_spell,preparation_cast_permission,become_prepared
        from quorune.effect_runtime.objects_stack_and_tokens import _apply_copy_until_end_of_turn
        session=self.session(7263);source=self.cast_prepared_creature(session,'Adventurous Eater',mana={'B':1,'C':2})
        copy=self.prepare_copy(session,source);land=self.add(session.engine,'Generic Bound Plains',ref='prepared-copy-becomes-land')
        _apply_copy_until_end_of_turn(session.engine,{'op':'copy_until_end_of_turn','source':source.ref,'target':land.ref},
            actor='A',operation='copy_until_end_of_turn',reason='Prepared designation survives a copiable-value change')
        self.assertIsNone(compiled_prepare_spell(session.engine,source));self.assertTrue(preparation_cast_permission(session.engine,'A',copy))
        self.assertIn(PREPARED_FIELD,source.annotations);self.assertFalse(become_prepared(session.engine,source))
        target=self.add(session.engine,'Generic Bound Body',ref='land-prepared-target');self.fund(session,{'B':1})
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==copy.ref)
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(session));self.assertNotIn(PREPARED_FIELD,session.state.cards[source.object_id].annotations)
        self.assertFalse(become_prepared(session.engine,session.state.cards[source.object_id]));self.replay(session,load=True)

    def test_rules_authorized_free_base_can_apply_to_an_inset_copy_without_consuming_it_during_offers(self):
        from quorune.rules.casting.costs import build_cast_cost_options
        from quorune.rules.casting.proposal import _spell_semantic_key
        session=self.session(7264);source=self.cast_prepared_creature(session,'Landscape Painter',mana={'U':1,'C':1})
        copy=self.prepare_copy(session,source);self.fund(session,{})
        program=session.engine.semantics.get(_spell_semantic_key(self.db.lookup('Landscape Painter'),self.db.lookup('Landscape Painter').faces[1]))
        options=build_cast_cost_options(session.engine,'A',copy,program,hint=True,force_without_mana_cost=True)
        self.assertEqual(1,len(options));self.assertFalse(sum(options[0].to_dict()['requirements'].values()))
        self.assertIn(PREPARED_FIELD,session.state.cards[source.object_id].annotations);self.assertEqual('exile',session.state.cards[copy.object_id].zone)

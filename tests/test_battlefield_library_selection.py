"""Pinned library-to-battlefield partition and entry transaction contracts."""

from copy import deepcopy
from dataclasses import replace
import unittest

from quorune.rules.library_selection import LibrarySelectionArrangement,LibrarySelectionObjectIdentity


class BattlefieldLibraryArrangementTests(unittest.TestCase):
    def arrangement(self,**changes):
        values=dict(looked=(LibrarySelectionObjectIdentity('one','one@0','P1'),
            LibrarySelectionObjectIdentity('two','two@0','P2')),
            selected_refs=('P1',),remainder_refs=('P2',),remainder_destination='library_bottom',
            remainder_order='chosen',selected_destination='battlefield',entry_tapped=True)
        return LibrarySelectionArrangement(**{**values,**changes})

    def test_battlefield_arrangement_pins_entry_status_and_complete_partition(self):
        arrangement=self.arrangement()
        self.assertEqual(('battlefield',True,('P1',),('P2',)),
            (arrangement.selected_destination,arrangement.entry_tapped,arrangement.selected_refs,arrangement.remainder_refs))
        with self.assertRaises(ValueError):self.arrangement(remainder_refs=())
        with self.assertRaises(ValueError):self.arrangement(remainder_refs=('P1','P2'))
        with self.assertRaises(ValueError):self.arrangement(entry_tapped=1)

    def test_old_hand_and_top_forms_do_not_acquire_entry_status(self):
        hand=self.arrangement(selected_destination='hand',entry_tapped=False)
        top=self.arrangement(selected_destination='library_top',entry_tapped=False,remainder_destination='exile')
        self.assertFalse(hand.entry_tapped);self.assertFalse(top.entry_tapped)
        for old in (hand,top):
            with self.assertRaises(ValueError):replace(old,entry_tapped=True)

    def test_replacement_identity_adds_entry_fields_only_to_the_new_destination(self):
        from quorune.semantic_runtime.intents import LibrarySelectionIntent
        from quorune.semantic_choices.intent_replacement import semantic_intent_identity,validate_semantic_intent_identity
        for destination in ('hand','battlefield','library_top'):
            arrangement=self.arrangement(selected_destination=destination,entry_tapped=destination=='battlefield',
                remainder_destination='exile' if destination=='library_top' else 'library_bottom')
            intent=LibrarySelectionIntent('A','A',arrangement,'Library selection','STACK',False,False)
            kind,identity=semantic_intent_identity(intent)
            self.assertEqual(identity,validate_semantic_intent_identity(kind,identity))
            raw=identity['arrangement']
            if destination=='battlefield':self.assertIs(raw['entry_tapped'],True)
            else:self.assertNotIn('entry_tapped',raw)
            if destination=='hand':self.assertNotIn('selected_destination',raw)

    def test_entry_is_one_controller_bound_batch_before_bottom_order_changes(self):
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        from quorune.rules.library_battlefield_entry import commit_library_battlefield_selection
        looked=tuple(LibrarySelectionObjectIdentity(word,word+'@0','P'+str(index))
            for index,word in enumerate(('one','two','three'),1))
        arrangement=LibrarySelectionArrangement(looked,('P1','P2'),('P3',),'library_bottom','chosen','battlefield',True)
        library=['unseen','three','two','one'];original=list(library)
        cards={identity.ref:SimpleNamespace(object_id=identity.object_id,ref=identity.ref,zone='library') for identity in looked}
        host=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(seed=1),event_sequence=3,
            players={'A':SimpleNamespace(zones={'library':library})}),_log=Mock())
        def move(changes,**options):
            self.assertEqual(original,library)
            self.assertEqual((('one','battlefield'),('two','battlefield')),changes)
            self.assertEqual({'one':'A','two':'A'},options['destination_controllers'])
            self.assertIs(options['tapped'],True)
            result=[]
            for object_id,_ in changes:
                library.remove(object_id)
                card=next(card for card in cards.values() if card.object_id==object_id)
                card.zone='battlefield';result.append(card)
            return result
        with patch('quorune.zone_transitions.ZoneTransitionOwner') as owner:
            owner.return_value.move_cards_simultaneously.side_effect=move
            result=commit_library_battlefield_selection(host,actor='A',player='A',arrangement=arrangement,
                cards=cards,reason='Library entry',source_stack_ref='STACK',looked_are_public=False,
                selected_are_public=False,replacement_selections=())
            owner.return_value.move_cards_simultaneously.assert_called_once()
        self.assertEqual(('P1','P2','P3'),result);self.assertEqual(['three','unseen'],library)
        self.assertEqual(['P1','P2'],host._log.call_args.args[3]['objects'])

    def test_pending_entry_preparation_does_not_order_the_hidden_remainder(self):
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        from quorune.rules.library_battlefield_entry import commit_library_battlefield_selection
        arrangement=self.arrangement();library=['unseen','two','one'];before=list(library)
        cards={identity.ref:SimpleNamespace(object_id=identity.object_id,ref=identity.ref,zone='library')
            for identity in arrangement.looked}
        host=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(seed=1),event_sequence=3,
            players={'A':SimpleNamespace(zones={'library':library})}),_log=Mock())
        with patch('quorune.zone_transitions.ZoneTransitionOwner') as owner:
            owner.return_value.move_cards_simultaneously.side_effect=RuntimeError('pending-entry-witness')
            with self.assertRaisesRegex(RuntimeError,'pending-entry-witness'):
                commit_library_battlefield_selection(host,actor='A',player='A',arrangement=arrangement,
                    cards=cards,reason='Library entry',source_stack_ref='STACK',looked_are_public=False,
                    selected_are_public=False,replacement_selections=())
        self.assertEqual(before,library);host._log.assert_not_called()


class BattlefieldLibraryChoiceTests(unittest.TestCase):
    def effect(self):
        from quorune.object_predicate import ObjectQuerySpec
        return {'op':'fixed_library_selection','schema_version':3,'player':'A','look_count':3,
            'public_reveal':False,'selected_reveal':False,'selection_policy':'up_to_matching',
            'minimum':0,'maximum':2,'predicate_groups':[[ObjectQuerySpec(zones=('library',),types_any=('creature',)).to_dict()]],
            'remainder_destination':'library_bottom','remainder_order':'chosen',
            'selected_destination':'battlefield','entry_tapped':True,
            'mana_value_limit':{'schema_version':1,'maximum':3}}

    def context(self):
        from types import SimpleNamespace
        from quorune.object_query import ObjectQueryResult
        rows={ref:ObjectQueryResult(object_id=ref,logical_object_id=ref+'@0',ref=ref,printed_name=ref,
            owner='A',controller='A',zone='library',types=(kind,),mana_value=mana)
            for ref,kind,mana in (('P1','creature',3),('P2','creature',4),('P3','land',0))}
        return SimpleNamespace(actor='A',stack_controller='A',stack_label='Battlefield selection',stack_ref='STACK',
            query=SimpleNamespace(library_refs=lambda *args,**kwargs:tuple(rows),object=lambda ref,**kwargs:rows[ref]))

    def test_choice_keeps_complete_looked_set_but_only_three_mana_creature_is_eligible(self):
        from quorune.semantic_choices.fixed_library_selection import FixedLibrarySelectionChoiceHandler
        handler=FixedLibrarySelectionChoiceHandler();preparation=handler.prepare(self.effect(),self.context())
        effect=preparation.continuation_effect
        self.assertEqual([['P1']],[list(group) for group in effect['_eligible_groups']])
        self.assertEqual(3,len(effect['_looked_objects']))
        self.assertIn('battlefield',preparation.request.prompt)
        self.assertIn('battlefield',preparation.request.choice.partitions)
        self.assertNotIn('hand',preparation.request.choice.partitions)

    def test_completion_keeps_entry_metadata_and_rejects_over_limit_card(self):
        from types import SimpleNamespace
        from quorune.semantic_choices.fixed_library_selection import FixedLibrarySelectionChoiceHandler
        handler=FixedLibrarySelectionChoiceHandler();preparation=handler.prepare(self.effect(),self.context())
        continuation=SimpleNamespace(effect=preparation.continuation_effect,stack_ref='STACK')
        with self.assertRaises(ValueError):handler.complete(continuation,
            {'cards':{'battlefield':['P2'],'bottom':['P1','P3']}},self.context().query)
        completed=handler.complete(continuation,{'cards':{'battlefield':['P1'],'bottom':['P2','P3']}},self.context().query)
        arrangement=completed.intents[0].arrangement
        self.assertEqual('battlefield',arrangement.selected_destination);self.assertIs(arrangement.entry_tapped,True)

    def test_old_shapes_cannot_smuggle_entry_fields_and_aura_capable_predicates_remain_unowned(self):
        from quorune.semantic_choices.fixed_library_selection import FixedLibrarySelectionChoiceHandler
        from quorune.object_predicate import ObjectQuerySpec
        handler=FixedLibrarySelectionChoiceHandler()
        with self.assertRaises(ValueError):handler.prepare({**self.effect(),'schema_version':1},self.context())
        aura={**self.effect(),'predicate_groups':[[ObjectQuerySpec(zones=('library',),types_any=('enchantment',)).to_dict()]]}
        with self.assertRaises(ValueError):handler.prepare(aura,self.context())
        with self.assertRaises(ValueError):handler.prepare({**self.effect(),'entry_tapped':1},self.context())

    def test_compiler_and_dependency_withdrawal_preserve_new_shape_boundaries(self):
        import json
        from common import ROOT
        from dataclasses import replace
        from unittest.mock import patch
        from test_qualified_zone_event_queries import query_record
        from quorune.compiler.library_battlefield_templates import library_battlefield_selection_effect_template as compile
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        text=('Look at the top six cards of your library. Put up to two creature cards with mana value 3 or less '
            'from among them onto the battlefield. Put the rest on the bottom of your library in any order.')
        result=compile(text);self.assertIsNotNone(result)
        self.assertEqual((3,'battlefield',False),(result[1][0]['schema_version'],
            result[1][0]['selected_destination'],result[1][0]['entry_tapped']))
        self.assertEqual({'schema_version':1,'maximum':3},result[1][0]['mana_value_limit'])
        for changed in (text.replace('onto the battlefield','onto the battlefield tapped and attacking'),
                        text.replace('creature cards with mana value 3 or less','enchantment cards'),
                        text.replace('on the bottom of your library in any order','into your graveyard')):
            self.assertIsNone(compile(changed))
        source=replace(query_record(text),type_line='Instant',power=None,toughness=None)
        registry=load_default_capability_registry()
        ir=compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,[(row.kind,row.reason,row.blockers) for row in ir.material_residuals])
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf8'))
        for dependency in ('library.select.battlefield_controller','library.select.fixed_controller'):
            changed=deepcopy(raw);cap=next(row for row in changed['capabilities'] if row['id']==dependency)
            cap['status']='blocked';cap['blockers']=['Withdrawn for focused mutation']
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=CapabilityRegistry(changed),
                capability_profile='commander_review').status)
        with patch('quorune.compiler.library_battlefield_templates.library_battlefield_selection_effect_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review').status)


class BattlefieldLibraryNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'library-entry.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/battlefield-library-selection-cards.json',ROOT/'tests/fixtures/entry-copy-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Library entry',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def closed(self,card):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),semantic_registry=SemanticRegistry(),
            capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])

    def test_original_rejuvenator_puts_only_the_selected_land_in_tapped_and_keeps_remainder_private(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=H.session(self,939001);engine=session.engine
        cards=tuple(H.add(self,engine,name,zone='library',ref='LOOK'+str(index)) for index,name in enumerate(
            ('Generic Bound Body','Generic Bound Growth','Generic Bound Plains','Mirror Image','Collected Company')))
        source=H.add(self,engine,'Elvish Rejuvenator',zone='hand');self.closed(source)
        action=H.ready(self,session,source,{'G':1,'C':2});H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
        self.assertNotIn('Mirror Image',str(session.packet('pilot:B',full=True)))
        session=H.replay(self,session,load=True);engine=session.engine
        before=authoritative_state_hash(engine.state)
        response={'action_id':'choose','cards':[cards[2].ref]}
        rejected=session.act('pilot:B',response)
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        accepted=session.act('pilot:A',response)
        self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
        land=engine.state.cards[cards[2].object_id]
        self.assertEqual(('battlefield','A',True),(land.zone,land.controller,land.tapped))
        self.assertTrue(all(engine.state.cards[card.object_id].zone=='library' for card in cards if card.object_id!=land.object_id))
        self.assertNotIn('Mirror Image',str(session.packet('pilot:B',full=True)))
        H.replay(self,session,load=True)

    def test_original_company_short_library_and_stale_top_are_identity_bound(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        for index,stale in enumerate((False,True)):
            with self.subTest(stale=stale):
                session=H.session(self,939010+index);engine=session.engine
                for oid in tuple(engine.state.players['A'].zones['library']):
                    engine.move_card(oid,'outside',log=False)
                top=H.add(self,engine,'Generic Bound Body',zone='library',ref='SHORT_TOP')
                source=H.add(self,engine,'Collected Company',zone='hand');self.closed(source)
                action=H.ready(self,session,source,{'G':1,'C':3});H.checkpoint(self,session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
                session=H.replay(self,session,load=True);engine=session.engine
                if stale:
                    # Deliberate checkpoint corruption tests the pinned top;
                    # this is not part of the legal command replay.
                    engine.move_card(top.object_id,'graveyard',log=False)
                    before=authoritative_state_hash(engine.state)
                accepted=session.act('pilot:A',{'action_id':'choose',
                    'cards':{'battlefield':[top.ref],'bottom':[]}})
                if stale:
                    self.assertFalse(accepted.ok)
                    self.assertEqual(before,authoritative_state_hash(engine.state))
                    self.assertEqual('graveyard',engine.state.cards[top.object_id].zone)
                else:
                    self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
                    self.assertEqual('battlefield',engine.state.cards[top.object_id].zone)
                    H.replay(self,session,load=True)

    def test_original_company_retains_simultaneous_entry_and_pending_copy_replacement_before_bottom_order(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=H.session(self,939002);engine=session.engine
        existing=H.add(self,engine,'Generic Bound Body',ref='EXISTING')
        names=('Mirror Image','Generic Bound Body','Silverback Elder','Generic Bound Plains','Generic Bound Growth','Elvish Rejuvenator')
        cards=tuple(H.add(self,engine,name,zone='library',ref='LOOK'+str(index)) for index,name in enumerate(names))
        self.assertGreater(self.db.by_oracle_id(cards[2].oracle_id).mana_value,3)
        source=H.add(self,engine,'Collected Company',zone='hand');self.closed(source)
        action=H.ready(self,session,source,{'G':1,'C':3});H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
        before_library=list(engine.state.players['A'].zones['library'])
        before=authoritative_state_hash(engine.state)
        rejected=session.act('pilot:A',{'action_id':'choose','cards':{'battlefield':[cards[2].ref],
            'bottom':[card.ref for card in cards if card.object_id!=cards[2].object_id]}})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        selected=(cards[0],cards[1]);remainder=cards[2:]
        accepted=session.act('pilot:A',{'action_id':'choose','cards':{'battlefield':[card.ref for card in selected],
            'bottom':[card.ref for card in remainder]}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertEqual(before_library,engine.state.players['A'].zones['library'])
        self.assertTrue(all(engine.state.cards[card.object_id].zone=='library' for card in selected))
        session=H.replay(self,session,load=True);engine=session.engine
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        choice=next(row['id'] for row in options if existing.ref in row['id'] and not row['id'].startswith('decline:'))
        accepted=session.act('pilot:A',{'action_id':'choose','replacement':choice})
        self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
        entered=[engine.state.cards[card.object_id] for card in selected]
        self.assertTrue(all(card.zone=='battlefield' and card.controller=='A' for card in entered))
        self.assertEqual(entered[0].zone_timestamp,entered[1].zone_timestamp)
        self.assertEqual('Generic Bound Body',engine.display_name(entered[0].object_id))
        self.assertTrue(all(engine.state.cards[card.object_id].zone=='library' for card in remainder))
        H.replay(self,session,load=True)

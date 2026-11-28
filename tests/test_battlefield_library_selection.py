"""Pinned library-to-battlefield partition and entry transaction contracts."""

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
import unittest

from inherited_composition_fixture import InheritedNativeFixture
from quorune.record import authoritative_state_hash
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

    def test_diverted_random_entrant_is_excluded_from_the_remainder(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        from quorune.rules.library_battlefield_entry import commit_library_battlefield_selection

        looked = tuple(LibrarySelectionObjectIdentity(f"card-{index}", f"card-{index}@0", f"P{index}")
                       for index in range(1, 4))
        arrangement = LibrarySelectionArrangement(looked, ("P2",), ("P1", "P3"),
                                                   "library_bottom", "random", "battlefield")
        library = ["unseen", "card-3", "card-2", "card-1"]
        cards = {identity.ref: SimpleNamespace(ref=identity.ref, object_id=identity.object_id, zone="library")
                 for identity in looked}
        host = SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(seed=4), event_sequence=10,
                               players={"A": SimpleNamespace(zones={"library": library})}), _log=Mock())

        def exile_instead(changes, **options):
            self.assertEqual((("card-2", "battlefield"),), changes)
            library.remove("card-2")
            cards["P2"].zone = "exile"
            return (cards["P2"],)

        with patch("quorune.zone_transitions.ZoneTransitionOwner") as owner:
            move = owner.return_value.move_cards_simultaneously
            move.side_effect = exile_instead
            commit_library_battlefield_selection(host, actor="A", player="A", arrangement=arrangement,
                cards=cards, reason="Public random creature entry", source_stack_ref="STACK", looked_are_public=True,
                selected_are_public=False, replacement_selections=())
            move.assert_called_once()
        self.assertEqual("exile", cards["P2"].zone)
        self.assertEqual({"card-1", "card-3"}, set(library[:-1]))
        self.assertEqual("unseen", library[-1])


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


    def test_random_revealed_entry_is_closed_and_refuses_player_selection(self):
        from types import SimpleNamespace
        from quorune.compiler.library_battlefield_templates import library_battlefield_selection_effect_template
        from quorune.library_battlefield_model import random_revealed_creature_entry_effect
        from quorune.rules.library_selection_capability_shapes import fixed_library_selection_node_capabilities
        from quorune.semantic_choices.fixed_library_selection import FixedLibrarySelectionChoiceHandler

        text = ("Reveal the top thirteen cards of your library. Put a random creature card from among them "
                "onto the battlefield. Put the rest on the bottom of your library in a random order.")
        _, (effect,), schema, mechanics = library_battlefield_selection_effect_template(text)
        self.assertEqual(random_revealed_creature_entry_effect(13), effect)
        self.assertEqual({"library.select.fixed_controller", "library.select.battlefield_controller"},
            set(fixed_library_selection_node_capabilities(effects=(effect,), target_schema=schema, mechanic_ids=mechanics)))
        for changed in ({"public_reveal": False}, {"remainder_order": "chosen"}, {"look_count": True},
                        {"entry_tapped": True}, {"schema_version": 3}, {"minimum": False}, {"player": "B"}):
            with self.subTest(changed=changed):
                self.assertEqual((), fixed_library_selection_node_capabilities(
                    effects=({**effect, **changed},), target_schema=schema, mechanic_ids=mechanics))
        for variant in ("random land card", "up to one random creature card", "random enchantment card"):
            self.assertIsNone(library_battlefield_selection_effect_template(text.replace("a random creature card", variant)))
        handler = FixedLibrarySelectionChoiceHandler()
        with self.assertRaisesRegex(ValueError, "accepts no player selection"):
            handler.complete(SimpleNamespace(effect=effect), {"cards": ["P1"]}, self.context().query)
        context = self.context()
        context.query.choice_candidate_refs = lambda: ()
        with self.assertRaisesRegex(ValueError, "lost its selected eligible card"):
            handler.prepare(random_revealed_creature_entry_effect(3, player="A"), context)


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


class GetawayBarrelNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = TemporaryDirectory()
        database = os.environ.get("MTG_CARD_DB")
        if database is None:
            folder = Path(cls.temporary.name)
            fixtures = ROOT / "tests" / "fixtures"
            selections = {
                "battlefield-library-selection-cards.json": {"Getaway Barrel", "Silverback Elder"},
                "entry-copy-cards.json": {"Grizzly Bears", "Mirror Image", "Sol Ring"},
                "source-attack-alone-cards.json": {"Disenchant", "Isamaru, Hound of Konda", "Plains"},
                "attached-destruction-set-cards.json": {"Rest in Peace"},
                "creature-subtype-set-results-cards.json": {"Ornithopter"},
            }
            fixture_paths = []
            for filename, names in selections.items():
                payload = json.loads((fixtures / filename).read_bytes())
                payload["cards"] = [row for row in payload["cards"] if row["name"] in names]
                assert {row["name"] for row in payload["cards"]} == names
                ids = {row["oracle_id"] for row in payload["cards"]}
                payload["rulings"] = [row for row in payload.get("rulings", ()) if row["oracle_id"] in ids]
                path = folder / filename
                path.write_text(json.dumps(payload), encoding="utf-8")
                fixture_paths.append(path)
            database = folder / "getaway-barrel.sqlite3"
            build_fixture_database(fixture_paths, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Getaway Barrel", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def scene(self, seed, names, *, below=(), exile_instead=False, mirror=False):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        for object_id in tuple(game.state.players["A"].zones["library"]):
            game.engine.move_card(object_id, "outside", log=False)
        unseen = tuple(self.add(game.engine, name, zone="library", ref=f"BARREL-UNREVEALED-{i}")
                       for i, name in enumerate(below))
        for index, card in enumerate(unseen):
            card.known_to = ["A"] if index == len(unseen) - 1 else []
            card.revealed_to = []
        prefix = [self.add(game.engine, names[index], zone="library", ref=f"BARREL-PREFIX-{index}")
                  for index in reversed(range(len(names)))]
        source = self.add(game.engine, "Getaway Barrel")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        extra = []
        if mirror:
            extra.append(self.add(game.engine, "Grizzly Bears", ref="BARREL-EXISTING-BEARS"))
        if exile_instead:
            extra.append(self.add(game.engine, "Rest in Peace", seat="B"))
        for card in (source, removal, *extra):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast(game, "B", removal, [source.ref])
        self.resolve_top(game)
        if exile_instead:
            self.assertFalse(game.state.stack)
        else:
            trigger, = game.state.stack
            self.assertEqual(("A", "triggered_ability", source.object_id, []),
                             (trigger.controller, trigger.kind, trigger.source_object_id, trigger.targets))
        return game, source, tuple(reversed(prefix)), unseen, extra

    @staticmethod
    def events(game, code):
        return [event for event in game.state.events if event.code == code]

    def test_actual_top_thirteen_are_public_and_random_selection_and_bottom_order_replay(self):
        names = ["Plains"] * 13
        for index, name in ((1, "Grizzly Bears"), (4, "Ornithopter"), (9, "Grizzly Bears")):
            names[index] = name
        seed = 939271
        game, source, prefix, below, _ = self.scene(seed, names, below=("Silverback Elder", "Sol Ring"))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual([([], []), (["A"], [])],
            [(game.state.cards[card.object_id].known_to, game.state.cards[card.object_id].revealed_to)
             for card in below])
        trigger, = game.state.stack
        stack_ref = trigger.ref
        eligible = sorted([prefix[i] for i in (1, 4, 9)], key=lambda card: card.object_id)
        chosen, = Random(f"{seed}|{stack_ref}|random-revealed-library-creature|A").sample(eligible, 1)
        self.assertEqual("Ornithopter", chosen.printed_name)
        original_identity = chosen.logical_object_id
        self.assertIsNone(self.resolve(game))
        reveal, = self.events(game, "library.reveal")
        partition, = self.events(game, "library.selection")
        self.assertLess(reveal.event_id, partition.event_id)
        self.assertEqual([{"ref": card.ref, "name": card.printed_name} for card in prefix], reveal.details["cards"])
        self.assertEqual((13, 1, 12), tuple(partition.details[key]
            for key in ("looked_count", "selected_count", "remainder_count")))
        entered = game.state.cards[chosen.object_id]
        self.assertEqual(("battlefield", "A", False), (entered.zone, entered.controller, entered.tapped))
        self.assertNotEqual(original_identity, entered.logical_object_id)
        bottom = [card.object_id for card in prefix if card.object_id != chosen.object_id]
        rng = Random(f"{seed}|A|library-battlefield-selection|{partition.event_id - 1}|{stack_ref}")
        rng.shuffle(bottom)
        self.assertEqual(bottom + [card.object_id for card in below], game.state.players["A"].zones["library"])
        for oid in bottom:
            card = game.state.cards[oid]
            self.assertFalse(card.known_to)
            self.assertFalse(card.revealed_to)
        for seat in "ABCD":
            packet = json.dumps(game.packet(f"pilot:{seat}", full=True))
            for card in below if seat != "A" else below[:1]:
                self.assertNotIn(card.ref, packet)
                self.assertNotIn(card.printed_name, packet)
            self.assertIn("Ornithopter", packet)
        self.assertEqual([below[1].ref],
            [row["id"] for row in game.packet("pilot:A", full=True)["state"]["players"]["A"]["known_top"]])
        self.assertFalse(self.events(game, "library.shuffle"))
        self.replay(game, load=True)

    def test_actual_replacement_pending_keeps_selected_identity_and_reveals_once_after_resume(self):
        game, _, prefix, _, extra = self.scene(939272, ("Plains", "Mirror Image", "Plains"), mirror=True)
        image = prefix[1]
        bears, = extra
        before = list(game.state.players["A"].zones["library"])
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(before, game.state.players["A"].zones["library"])
        self.assertEqual(1, len(self.events(game, "library.reveal")))
        self.assertFalse(self.events(game, "library.selection"))
        game = self.replay(game, load=True)
        rejected_hash = authoritative_state_hash(game.state)
        rejected = game.act("pilot:B", {"action_id": "choose", "replacement": "forged"})
        self.assertFalse(rejected.ok)
        self.assertEqual(rejected_hash, authoritative_state_hash(game.state))
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        choice = next(row["id"] for row in options if bears.ref in row["id"] and not row["id"].startswith("decline:"))
        accepted = game.act("pilot:A", {"action_id": "choose", "replacement": choice})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(self.events(game, "library.reveal")))
        self.assertEqual("battlefield", game.state.cards[image.object_id].zone)
        self.assertEqual("Grizzly Bears", game.engine.display_name(image.object_id))
        self.assertEqual({prefix[0].object_id, prefix[2].object_id}, set(game.state.players["A"].zones["library"]))
        self.replay(game, load=True)

    def test_actual_short_or_empty_prefix_and_exile_replacement(self):
        for seed, (names, exile_instead) in enumerate((
            (("Plains", "Sol Ring", "Plains"), False),
            ((), False),
            (("Ornithopter",), True),
            (("Ornithopter", "Plains"), False),
        ), 939273):
            with self.subTest(names=names, exile_instead=exile_instead):
                game, source, prefix, _, _ = self.scene(seed, names, exile_instead=exile_instead)
                before = tuple(game.state.players["A"].zones["library"])
                self.assertIsNone(self.resolve(game))
                if exile_instead or not names:
                    self.assertFalse(self.events(game, "library.reveal"))
                    self.assertFalse(self.events(game, "library.selection"))
                    self.assertEqual(before, tuple(game.state.players["A"].zones["library"]))
                else:
                    reveal, = self.events(game, "library.reveal")
                    partition, = self.events(game, "library.selection")
                    self.assertEqual([card.ref for card in prefix], [card["ref"] for card in reveal.details["cards"]])
                    entrant = next((card for card in prefix if card.printed_name == "Ornithopter"), None)
                    self.assertEqual(int(entrant is not None), partition.details["selected_count"])
                    if entrant:
                        self.assertEqual("battlefield", game.state.cards[entrant.object_id].zone)
                    self.assertEqual(set(before) - ({entrant.object_id} if entrant else set()),
                                     set(game.state.players["A"].zones["library"]))
                self.assertEqual("exile" if exile_instead else "graveyard", game.state.cards[source.object_id].zone)
                self.replay(game, load=True)


class PrivateLookLandHandNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "private-look-land-hand.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json", "battlefield-library-selection-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Land or hand private look", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def _genesis(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, "Planar Genesis", zone="hand")
        self.assert_closed(game, source)
        return game, source

    def _cast_genesis(self, game, source):
        self.prepare(game, mana={"G": 1, "U": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        return self.resolve(game)

    def _check_look(self, game, cards, *, unseen):
        self.assertEqual(["pilot:A"], game.pending_principals())
        packet = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual([card.ref for card in reversed(cards)], [row["id"] for row in packet["ctx"]["objects"]])
        self.assertNotIn(unseen.ref, json.dumps(game.packet("pilot:A", full=True)))
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            foreign = game.packet(principal, full=True)
            self.assertIsNone(foreign["decision"])
            self.assertTrue(all(card.ref not in json.dumps(foreign) for card in cards))

    def _select(self, game, cards, *, actor="A", ok=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, {"action_id": "choose", "cards": cards})
        self.assertEqual(ok, result.ok, result.summary)
        if not ok:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def _normal_library(self, game):
        unseen = self.add(game.engine, "Generic Bound Body", zone="library", ref="genesis-unseen-middle")
        cards = [self.add(game.engine, name, zone="library", ref=f"genesis-looked-{index}")
                 for index, name in enumerate(("Generic Bound Body", "Forest", "Generic Bound Body", "Generic Bound Plains"))]
        for card in (unseen, *cards):
            card.known_to.clear()
            card.revealed_to.clear()
        return unseen, cards

    def test_planar_genesis_puts_one_selected_land_onto_the_battlefield_tapped(self):
        from unittest.mock import patch

        from quorune.rules import library_battlefield_entry

        game, source = self._genesis(77634100)
        unseen, cards = self._normal_library(game)
        library = list(game.state.players["A"].zones["library"])
        self.assertEqual("semantic.choice", self._cast_genesis(game, source).kind)
        self._check_look(game, cards, unseen=unseen)
        for refs, actor in (([cards[2].ref], "A"), ([unseen.ref], "A"), ([cards[1].ref], "B")):
            self._select(game, refs, actor=actor, ok=False)
        game = self.replay(game, load=True)
        land = cards[1]
        with patch.object(library_battlefield_entry, "_finish_library_remainder",
                          wraps=library_battlefield_entry._finish_library_remainder) as finish:
            self._select(game, [land.ref])
            finish.assert_called_once()
            self.assertEqual("random", finish.call_args.kwargs["arrangement"].remainder_order)
        self.assertIsNone(self.resolve(game))
        actual = game.state.cards[land.object_id]
        self.assertEqual(("battlefield", True, "A"), (actual.zone, actual.tapped, actual.controller))
        final_library = game.state.players["A"].zones["library"]
        self.assertEqual({card.object_id for card in cards if card != land}, set(final_library[:3]))
        self.assertEqual(library[:-4], final_library[3:])
        opponent = json.dumps(game.packet("pilot:B", full=True))
        self.assertIn(land.ref, opponent)
        self.assertTrue(all(card.ref not in opponent for card in cards if card != land))
        self.replay(game, load=True)

    def test_planar_genesis_decline_reuses_the_same_look_and_can_put_a_land_in_hand(self):
        from unittest.mock import patch

        from quorune.rules import library_selection

        game, source = self._genesis(77634101)
        unseen, cards = self._normal_library(game)
        library = list(game.state.players["A"].zones["library"])
        self.assertEqual("semantic.choice", self._cast_genesis(game, source).kind)
        self._check_look(game, cards, unseen=unseen)
        self._select(game, [])
        self.assertEqual("semantic.choice", game.state.pending_decision.kind)
        self.assertEqual(library, game.state.players["A"].zones["library"])
        self._check_look(game, cards, unseen=unseen)
        self._select(game, [], ok=False)
        game = self.replay(game, load=True)
        choice = cards[-1]
        with patch.object(library_selection, "_shuffle_bottom_selection",
                          wraps=library_selection._shuffle_bottom_selection) as shuffle:
            self._select(game, [choice.ref])
            shuffle.assert_called_once()
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[choice.object_id].zone)
        self.assertNotIn(choice.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.assertEqual(1, sum(event.code == "library.look" for event in game.state.events))
        final_library = game.state.players["A"].zones["library"]
        self.assertEqual({card.object_id for card in cards[:-1]}, set(final_library[:3]))
        self.assertEqual(library[:-4], final_library[3:])
        self.replay(game, load=True)

    def test_planar_genesis_handles_short_nonland_and_empty_libraries(self):
        for count in (0, 1, 2):
            with self.subTest(count=count):
                game, source = self._genesis(77634104 + count)
                for oid in tuple(game.state.players["A"].zones["library"]):
                    game.engine.move_card(oid, "exile", log=False, semantic_events=False)
                cards = [self.add(game.engine, "Generic Bound Body", zone="library",
                                  ref=f"genesis-short-{i}") for i in range(count)]
                pending = self._cast_genesis(game, source)
                if not count:
                    self.assertIsNone(pending)
                    self.assertEqual([], game.state.players["A"].zones["library"])
                else:
                    self.assertEqual("semantic.choice", pending.kind)
                    self._select(game, [], ok=False)
                    game = self.replay(game, load=True)
                    self._select(game, [cards[0].ref])
                    self.assertEqual("hand", game.state.cards[cards[0].object_id].zone)
                    self.assertEqual([card.object_id for card in cards[1:]], game.state.players["A"].zones["library"])
                    self.assertIsNone(self.resolve(game))
                self.replay(game, load=True)

    def test_planar_genesis_requires_the_complete_private_fallback_recipe(self):
        from dataclasses import replace
        from unittest.mock import patch

        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.private_look_land_or_hand import PRIVATE_LOOK_LAND_OR_HAND_VERSION

        game, source = self._genesis(77634109)
        card = self.db.lookup(source.printed_name, fuzzy=False)
        def witness():
            ir = compile_oracle_card(card, capability_registry=self.registry)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            self.assertEqual({"op":"fixed_library_selection","schema_version":PRIVATE_LOOK_LAND_OR_HAND_VERSION,
                              "player":"$controller","look_count":4}, ir.faces[0].nodes[0].effects[0])
        witness()
        with patch("quorune.compiler.private_look_land_or_hand_templates.private_look_land_or_hand_template",
                   return_value=None):
            with self.assertRaises(AssertionError):
                witness()
        changed = replace(card, oracle_text=card.oracle_text.replace("random order", "any order"))
        self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry).status)

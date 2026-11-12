"""Pinned Discover eligibility and resumable procedure contracts."""

import unittest
from inherited_composition_fixture import InheritedNativeFixture


class DiscoverValueTests(unittest.TestCase):
    def test_qualified_exile_scan_compiler_keeps_supplier_and_mode_closed(self):
        import json
        import re
        from unittest.mock import patch
        from common import ROOT
        from test_bound_effect_programs import record
        from quorune.compiler import discover_templates
        from quorune.discover_model import ExileCastProgress, QUALIFIED_SCAN_CAPABILITY
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry

        text = ("Target opponent exiles cards from the top of their library until they exile an instant or sorcery card. "
                "You may cast that card without paying its mana cost. Then put the exiled cards that weren't cast "
                "this way on the bottom of that library in a random order.")
        registry = load_default_capability_registry()
        ir = compile_oracle_card(record(text), capability_registry=registry)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        self.assertEqual('$target.0', ir.faces[0].nodes[0].effects[0]['supplier'])
        self.assertIn(QUALIFIED_SCAN_CAPABILITY, ir.faces[0].nodes[0].capability_dependencies)
        for changed in (text.replace('instant or sorcery', 'instant, sorcery, or creature'),
                        text.replace('without paying its mana cost.', 'this turn.'),
                        text.replace('Target opponent', 'Each opponent')):
            with self.subTest(text=changed):
                self.assertNotEqual('exact', compile_oracle_card(record(changed), capability_registry=registry).status)
        with patch.object(discover_templates, '_TARGETED_QUALIFIED_SCAN', re.compile(r'(?!)')):
            self.assertNotEqual('exact', compile_oracle_card(record(text), capability_registry=registry).status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row = next(row for row in raw['capabilities'] if row['id'] == QUALIFIED_SCAN_CAPABILITY)
        row.update(status='blocked', blockers=['Withheld for supplier-boundary test'])
        self.assertNotEqual('exact', compile_oracle_card(record(text), capability_registry=CapabilityRegistry(raw)).status)
        progress = ExileCastProgress(None, (), None, 'B')
        self.assertEqual(progress, ExileCastProgress.from_dict(progress.to_dict()))
        for malformed in ({**progress.to_dict(), 'scan': 'first_nonland'},
                          {**progress.to_dict(), 'supplier': ''},
                          {**progress.to_dict(), 'amount': 3}):
            with self.assertRaises(ValueError):
                ExileCastProgress.from_dict(malformed)

    def test_nonland_scan_wire_and_compiler_exclude_other_cast_permissions(self):
        import json
        import re
        from dataclasses import replace
        from unittest.mock import patch
        from common import ROOT
        from test_bound_effect_programs import record
        from quorune.compiler import discover_templates
        from quorune.discover_model import ExileCastProgress, NONLAND_SCAN_CAPABILITY
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry

        empty = ExileCastProgress(None, (), None)
        self.assertEqual(empty, ExileCastProgress.from_dict(empty.to_dict()))
        for forged in ({**empty.to_dict(), 'scan': 'discover'},
                       {'schema_version': 1, 'amount': None, 'exiled': [], 'candidate_ref': None}):
            with self.assertRaises(ValueError):
                ExileCastProgress.from_dict(forged)

        text = ('Exile cards from the top of your library until you exile a nonland card. '
                'You may cast that card without paying its mana cost.')
        source = record(text)
        registry = load_default_capability_registry()
        ir = compile_oracle_card(source, capability_registry=registry)
        self.assertEqual('exact', ir.status, ir.material_residuals)
        self.assertIn(NONLAND_SCAN_CAPABILITY, ir.faces[0].nodes[0].capability_dependencies)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        owner = next(row for row in raw['capabilities'] if row['id'] == NONLAND_SCAN_CAPABILITY)
        owner.update(status='blocked', blockers=['Withdrawn for focused mutation'])
        disabled = compile_oracle_card(source, capability_registry=CapabilityRegistry(raw))
        self.assertNotEqual('exact', disabled.status)
        for changed in (text.replace('cast that card', 'play that card'), text[:-1] + ' this turn.',
                        text.replace('nonland card', 'creature card')):
            with self.subTest(text=changed):
                self.assertNotEqual('exact', compile_oracle_card(replace(source, oracle_text=changed),
                                                                capability_registry=registry).status)
        with patch.object(discover_templates, '_FIRST_NONLAND_CAST', re.compile(r'(?!)')):
            self.assertNotEqual('exact', compile_oracle_card(source, capability_registry=registry).status)

    def test_resolving_cast_identity_rejects_internal_payment_fields_and_preserves_choices(self):
        from quorune.discover_model import DiscoverProgress,DiscoverCastIntent
        from quorune.rules.library_selection import LibrarySelectionObjectIdentity as Identity
        from quorune.replacement.immutable import FrozenMap
        from quorune.semantic_choices.intent_replacement import (
            semantic_intent_identity,validate_semantic_intent_identity,with_replacement_selections)
        progress=DiscoverProgress(4,(Identity('one','one@1','P1'),),'P1')
        intent=DiscoverCastIntent('A','STACK',progress,'CURRENT_OPTIONS',FrozenMap({'targets':['TARGET']}))
        kind,identity=semantic_intent_identity(intent)
        self.assertEqual('discover_cast',kind)
        self.assertEqual(identity,validate_semantic_intent_identity(kind,identity))
        self.assertEqual(('RULE',),with_replacement_selections(intent,('RULE',)).replacement_selections)
        for key in ('_mana_payment_id','_mana_replacement_selections','_random_discard_cost_refs'):
            with self.assertRaises(ValueError):
                DiscoverCastIntent('A','STACK',progress,'CURRENT_OPTIONS',FrozenMap({key:'FORGED'}))
        with self.assertRaises(ValueError):validate_semantic_intent_identity(kind,{**identity,'extra':True})

    def test_activated_discover_uses_closed_capability_gating_without_trusting_the_mechanic_globally(self):
        from dataclasses import replace
        from unittest.mock import patch
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.compiler.activated_mana_nodes import _CAPABILITY_SHAPED_EFFECT_OPERATIONS
        source=replace(query_record('{2}, {T}, Sacrifice this creature: Discover 3. Activate only as a sorcery.'),
            power='2',toughness='2')
        registry=load_default_capability_registry()
        ir=compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,[(row.reason,row.blockers) for row in ir.material_residuals])
        with patch('quorune.compiler.activated_mana_nodes._CAPABILITY_SHAPED_EFFECT_OPERATIONS',
                _CAPABILITY_SHAPED_EFFECT_OPERATIONS-{'discover'}):
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=registry,
                capability_profile='commander_review').status)

    def test_cleanup_identity_is_closed_and_replacement_selection_preserves_the_progress(self):
        from quorune.discover_model import DiscoverProgress,DiscoverCleanupIntent
        from quorune.rules.library_selection import LibrarySelectionObjectIdentity as Identity
        from quorune.semantic_choices.intent_replacement import (
            semantic_intent_identity,validate_semantic_intent_identity,with_replacement_selections)
        progress=DiscoverProgress(0,(Identity('one','one@1','P1'),),None)
        intent=DiscoverCleanupIntent('A','STACK',progress)
        kind,identity=semantic_intent_identity(intent)
        self.assertEqual('discover_cleanup',kind)
        self.assertEqual(identity,validate_semantic_intent_identity(kind,identity))
        selected=with_replacement_selections(intent,('RULE',))
        self.assertEqual(('RULE',),selected.replacement_selections)
        self.assertEqual(progress,selected.progress)
        with self.assertRaises(ValueError):validate_semantic_intent_identity(kind,{**identity,'extra':True})

    def test_inclusive_card_and_resulting_spell_limits_are_independent(self):
        from quorune.discover_model import discover_card_qualifies,discover_spell_qualifies
        self.assertTrue(discover_card_qualifies(4,types=('creature',),mana_value=4))
        self.assertFalse(discover_card_qualifies(4,types=('creature',),mana_value=5))
        self.assertFalse(discover_card_qualifies(4,types=('land',),mana_value=0))
        self.assertTrue(discover_spell_qualifies(4,mana_value=4))
        self.assertFalse(discover_spell_qualifies(4,mana_value=7))
        self.assertTrue(discover_card_qualifies(0,types=('instant',),mana_value=0))
        for value in (True,-1,'4',float('nan')):
            with self.assertRaises(ValueError):discover_spell_qualifies(4,mana_value=value)

    def test_progress_pins_distinct_exile_incarnations_and_candidate_membership(self):
        from quorune.discover_model import DiscoverProgress
        from quorune.rules.library_selection import LibrarySelectionObjectIdentity as Identity
        first=Identity('one','one@1','P1');second=Identity('two','two@1','P2')
        progress=DiscoverProgress(4,(first,second),'P2')
        self.assertEqual((first,),progress.cleanup_cards)
        self.assertEqual(progress,DiscoverProgress.from_dict(progress.to_dict()))
        for exiled,candidate in (((first,first),None),((first,), 'P2')):
            with self.assertRaises(ValueError):DiscoverProgress(4,exiled,candidate)
        for amount in (True,-1,4.0,'4'):
            with self.assertRaises(ValueError):DiscoverProgress(amount,(),None)

    def test_progress_wire_shape_is_closed_and_does_not_alias_mutable_input(self):
        from quorune.discover_model import DiscoverProgress
        value={'schema_version':1,'amount':0,'exiled':[
            {'object_id':'one','logical_object_id':'one@1','ref':'P1'}],'candidate_ref':'P1'}
        progress=DiscoverProgress.from_dict(value)
        value['exiled'][0]['ref']='CHANGED'
        self.assertEqual('P1',progress.candidate_ref)
        self.assertEqual('P1',progress.exiled[0].ref)
        for changed in ({**progress.to_dict(),'extra':True},
                        {**progress.to_dict(),'schema_version':True}):
            with self.assertRaises(ValueError):DiscoverProgress.from_dict(changed)

    def test_compiler_and_capability_withdrawal_preserve_material_discover_boundaries(self):
        from copy import deepcopy
        from dataclasses import replace
        import json
        from unittest.mock import patch
        from common import ROOT
        from test_qualified_zone_event_queries import query_record
        from quorune.compiler.discover_templates import discover_effect_template
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        source=replace(query_record('Discover 3.'),type_line='Sorcery',power=None,toughness=None)
        registry=load_default_capability_registry()
        self.assertEqual('exact',compile_oracle_card(source,capability_registry=registry,
            capability_profile='commander_review').status)
        for text in ('Discover X.','Discover 3 twice.','Discover 3, then exile the discovered card.'):
            self.assertIsNone(discover_effect_template(text))
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf8'))
        for dependency in ('library.discover.controller','casting.permission.one_shot_exile_without_mana'):
            changed=deepcopy(raw);cap=next(row for row in changed['capabilities'] if row['id']==dependency)
            cap['status']='blocked';cap['blockers']=['Withdrawn for focused mutation']
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=CapabilityRegistry(changed),
                capability_profile='commander_review').status)
        with patch('quorune.compiler.discover_templates.discover_effect_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=registry,
                capability_profile='commander_review').status)
        conditional=replace(source,oracle_text='When this creature enters, if you cast it from exile, discover 3.',
            type_line='Creature — Human',power='3',toughness='2')
        self.assertNotEqual('exact',compile_oracle_card(conditional,capability_registry=registry,
            capability_profile='commander_review').status)


class VariableDiscoverValueTests(unittest.TestCase):
    def test_amount_capabilities_and_optional_source_binding_withdrawal_fail_closed(self):
        from copy import deepcopy
        from dataclasses import replace
        import json
        from unittest.mock import patch
        from common import ROOT
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from quorune.query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY
        from quorune.scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY
        source=replace(query_record('When this creature dies, you may discover X, where X is its toughness.'),power='0',toughness='3')
        registry=load_default_capability_registry()
        self.assertEqual('exact',compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review').status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf8'))
        for dependency in (DECLARED_AMOUNT_CAPABILITY,SCALAR_AMOUNT_CAPABILITY):
            changed=deepcopy(raw);cap=next(row for row in changed['capabilities'] if row['id']==dependency)
            cap['status']='blocked';cap['blockers']=['Withdrawn for variable Discover mutation']
            ir=compile_oracle_card(source,capability_registry=CapabilityRegistry(changed),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)
            self.assertTrue(any(dependency in blocker for row in ir.material_residuals for blocker in row.blockers))
        with patch('quorune.compiler.scalar_effect_amounts.source_scalar_effect_amount_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=registry,
                capability_profile='commander_review').status)

    def test_declared_and_source_last_known_amounts_require_owned_producers(self):
        from dataclasses import replace
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        registry=load_default_capability_registry()
        cast=replace(query_record('Discover X.'),type_line='Sorcery',mana_cost='{X}{R}',power=None,toughness=None,
            colors=('R',),color_identity=('R',))
        death=replace(query_record('When this creature dies, you may discover X, where X is its toughness.'),power='0',toughness='3')
        for source in (cast,death):
            ir=compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,[(row.reason,row.blockers) for row in ir.material_residuals])
        for source in (replace(cast,mana_cost='{R}'),replace(death,oracle_text=
                'When this creature dies, discover X, where X is an arbitrary private characteristic.')):
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=registry,
                capability_profile='commander_review').status)

    def test_unresolved_discover_amount_has_a_closed_type_and_cannot_reach_the_scan(self):
        from quorune.discover_model import validate_discover_effect
        from quorune.scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin
        amount=ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE,characteristic='toughness').to_dict()
        effect={'op':'discover','schema_version':1,'player':'$controller','amount':amount}
        validate_discover_effect(effect)
        with self.assertRaises(ValueError):validate_discover_effect({**effect,'player':'A'},resolved=True)
        for value in (True,-1,2.0,'3',{'kind':'unowned_amount'},{**amount,'coefficient':-1},{**amount,'extra':True}):
            with self.assertRaises(ValueError):validate_discover_effect({**effect,'amount':value})


class DiscoverStepTests(unittest.TestCase):
    def test_qualified_scan_skips_creatures_and_retains_supplier(self):
        from dataclasses import replace
        from quorune.discover_model import QUALIFIED_SCAN_OPERATION, QUALIFIED_SCAN_CAST_OPERATION
        from quorune.semantic_choices.discover import DiscoverScanHandler

        context, rows = self.context()
        for ref, row in tuple(rows.items()):
            rows[ref] = replace(row, owner='B', controller='B')
        effect = {'op': QUALIFIED_SCAN_OPERATION, 'schema_version': 1, 'player': 'A', 'supplier': 'B'}
        for ref in ('LAND', 'HIGH', 'EQUAL', 'UNSEEN'):
            preparation = DiscoverScanHandler(effect['op']).prepare(effect, context)
            intent, = preparation.preparation_intents
            self.assertEqual(('B', ref, 'exile'), (intent.actor, intent.object_ref, intent.destination))
            self.assertEqual(ref, context.query.library.pop(0))
            rows[ref] = replace(rows[ref], zone='exile', logical_object_id=ref+'@1')
            effect = dict(preparation.auto_continue.prepend_effects[0])
        preparation = DiscoverScanHandler(effect['op']).prepare(effect, context)
        choice, = preparation.auto_continue.prepend_effects
        self.assertEqual(QUALIFIED_SCAN_CAST_OPERATION, choice['op'])
        self.assertEqual(('B', 'UNSEEN'), (choice['progress']['supplier'], choice['progress']['candidate_ref']))
        self.assertEqual([], context.query.library)

    def test_supplied_scan_snapshot_contains_only_bound_library_top(self):
        from types import SimpleNamespace
        from quorune.discover_casting import qualified_scan_library_refs
        from quorune.discover_model import QUALIFIED_SCAN_OPERATION
        from quorune.errors import StateInvariantError
        from quorune.semantic_choices.discover import DiscoverScanHandler

        state = SimpleNamespace(
            players={'B': SimpleNamespace(zones={'library': ['HIDDEN', 'TOP']}), 'D': SimpleNamespace()},
            cards={'TOP': SimpleNamespace(ref='SUPPLIED_TOP')},
        )
        host = SimpleNamespace(state=state, active_seats=('A', 'B', 'C'))
        effect = {'op': QUALIFIED_SCAN_OPERATION, 'schema_version': 1, 'player': 'A', 'supplier': 'B'}
        self.assertEqual({'B': ['SUPPLIED_TOP']}, qualified_scan_library_refs(host, actor='A', effect=effect))
        self.assertEqual({}, qualified_scan_library_refs(host, actor='A', effect={'op': 'draw', 'player': 'A'}))
        self.assertEqual({}, qualified_scan_library_refs(host, actor='A', effect={**effect, 'supplier': 'D'}))
        with self.assertRaises(StateInvariantError):
            qualified_scan_library_refs(host, actor='C', effect=effect)
        with self.assertRaises(StateInvariantError):
            qualified_scan_library_refs(host, actor='A', effect={**effect, 'supplier': 'UNKNOWN'})
        context, _ = self.context()
        context.query.active_seats = ('A',)
        prepared = DiscoverScanHandler(QUALIFIED_SCAN_OPERATION).prepare(effect, context)
        self.assertIsNone(prepared.request)
        self.assertEqual((), prepared.preparation_intents)
        self.assertEqual((), prepared.auto_continue.prepend_effects)

    def context(self):
        from types import SimpleNamespace
        from quorune.object_query import ObjectQueryResult
        rows={}
        for ref,kind,mana in (('LAND','land',0),('HIGH','creature',4),('EQUAL','creature',3),('UNSEEN','instant',1)):
            rows[ref]=ObjectQueryResult(ref,ref,ref,'A','A','library',types=(kind,),mana_value=mana,
                logical_object_id=ref+'@0')
        class Query:
            library=['LAND','HIGH','EQUAL','UNSEEN']
            active_seats=('A','B')
            def object(self,ref,*,zones=None):
                row=rows.get(ref)
                return row if row is not None and (zones is None or row.zone in zones) else None
            def library_refs(self,actor,*,top_first):return tuple(self.library)
        return SimpleNamespace(actor='A',stack_controller='A',stack_ref='STACK',stack_label='Discover',query=Query()),rows

    def test_scan_exiles_one_card_per_existing_zone_intent_and_stops_at_equality(self):
        from dataclasses import replace
        from quorune.semantic_choices.discover import DiscoverScanHandler
        from quorune.semantic_runtime.zone_move_intent import ZoneMoveIntent
        context,rows=self.context()
        effect={'op':'discover','schema_version':1,'player':'A','amount':3}
        for ref in ('LAND','HIGH','EQUAL'):
            preparation=DiscoverScanHandler(effect['op']).prepare(effect,context)
            self.assertIsNone(preparation.request);self.assertEqual(1,len(preparation.preparation_intents))
            intent=preparation.preparation_intents[0]
            self.assertIsInstance(intent,ZoneMoveIntent)
            self.assertEqual((ref,'exile',('library',),0),
                (intent.object_ref,intent.destination,intent.expected_zones,intent.expected_zone_change_counter))
            self.assertEqual(ref,context.query.library.pop(0))
            rows[ref]=replace(rows[ref],zone='exile',logical_object_id=ref+'@1')
            effect=dict(preparation.auto_continue.prepend_effects[0])
        preparation=DiscoverScanHandler(effect['op']).prepare(effect,context)
        self.assertFalse(preparation.preparation_intents)
        choice=dict(preparation.auto_continue.prepend_effects[0])
        self.assertEqual('discover_cast_choice',choice['op'])
        self.assertEqual('EQUAL',choice['progress']['candidate_ref'])
        self.assertEqual(['LAND','HIGH','EQUAL'],[card['ref'] for card in choice['progress']['exiled']])
        self.assertEqual(['UNSEEN'],context.query.library)

    def test_changed_exile_incarnation_and_unknown_progress_fields_reject_before_next_move(self):
        from dataclasses import replace
        from quorune.semantic_choices.discover import DiscoverScanHandler
        context,rows=self.context()
        initial={'op':'discover','schema_version':1,'player':'A','amount':3}
        preparation=DiscoverScanHandler('discover').prepare(initial,context)
        effect=dict(preparation.auto_continue.prepend_effects[0])
        context.query.library.pop(0)
        rows['LAND']=replace(rows['LAND'],zone='exile',logical_object_id='LAND@3')
        with self.assertRaises(ValueError):DiscoverScanHandler('discover_step').prepare(effect,context)
        with self.assertRaises(ValueError):DiscoverScanHandler('discover').prepare({**initial,'extra':True},context)

    def test_nonland_scan_exhaustion_continues_and_rejects_discover_progress(self):
        from quorune.discover_model import ExileCastProgress, NONLAND_SCAN_OPERATION, NONLAND_SCAN_STEP_OPERATION
        from quorune.semantic_choices.discover import DiscoverScanHandler

        context, _ = self.context()
        context.query.library = []
        initial = {'op': NONLAND_SCAN_OPERATION, 'schema_version': 1, 'player': 'A'}
        preparation = DiscoverScanHandler(NONLAND_SCAN_OPERATION).prepare(initial, context)
        self.assertIsNone(preparation.request)
        self.assertEqual((), preparation.preparation_intents)
        self.assertEqual((), preparation.auto_continue.prepend_effects)
        step = {'op': NONLAND_SCAN_STEP_OPERATION, 'schema_version': 1, 'player': 'A',
                'progress': ExileCastProgress(3, (), None).to_dict(),
                'pending': {'object_id': 'LAND', 'logical_object_id': 'LAND@0', 'ref': 'LAND'}}
        with self.assertRaisesRegex(ValueError, 'printed search rule'):
            DiscoverScanHandler(NONLAND_SCAN_STEP_OPERATION).prepare(step, context)


class DiscoverNativeTests(unittest.TestCase):
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
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'discover.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/discover-cards.json',ROOT/'tests/fixtures/cascade-cards.json',
            ROOT/'tests/fixtures/state-based-destination-cards.json',
            ROOT/'tests/fixtures/conditional-exile-cards.json',
            ROOT/'tests/fixtures/qualified-spell-ordinal-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Discover',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def closed(self,card):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),
            semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])

    def test_chaos_wand_casts_from_other_library_and_returns_unused_cards(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash

        for index, choice in enumerate(('cast', 'decline', 'exhausted', 'unpayable')):
            with self.subTest(choice=choice):
                session = H.session(self, 766001 + index)
                engine = session.engine
                session.state.config.semantic_policy = 'trusted_only'
                source = H.add(self, engine, 'Chaos Wand', ref='SUPPLIER_WAND')
                names = ('Forest', 'Generic Bound Plains') if choice == 'exhausted' else (
                    'Forest', 'Tormenting Voice' if choice == 'unpayable' else 'Lightning Bolt', 'Generic Bound Growth')
                cards = [H.add(self, engine, name, seat='B', zone='library', ref=f'SUPPLIER_TOP{position}')
                         for position, name in enumerate(names)]
                library = engine.state.players['B'].zones['library']
                if choice == 'exhausted':
                    for oid in list(library):
                        if oid not in {card.object_id for card in cards}:
                            engine.move_card(oid, 'exile', log=False)
                for card in cards:
                    library.remove(card.object_id)
                library.extend(card.object_id for card in reversed(cards))
                original_library = list(library)
                own_library = list(engine.state.players['A'].zones['library'])
                self.closed(source)
                if choice == 'unpayable':
                    self.closed(cards[1])
                    for oid in list(engine.state.players['A'].zones['hand']):
                        engine.move_card(oid, 'exile', log=False)
                action = H.ready(self, session, source, {'C': 4})
                H.checkpoint(self, session)
                result = session.act('pilot:A', {'action_id': action['id'], 'targets': ['B'], 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary)
                pending = H.resolve(self, session)
                if choice in {'exhausted', 'unpayable'}:
                    self.assertIsNone(pending)
                    if choice == 'exhausted':
                        self.assertCountEqual(original_library, engine.state.players['B'].zones['library'])
                    else:
                        self.assertCountEqual([c.object_id for c in cards[:2]],
                                              engine.state.players['B'].zones['library'][:2])
                else:
                    self.assertEqual('semantic.choice', pending.kind)
                    self.assertEqual(['pilot:A'], session.pending_principals())
                    self.assertEqual(['exile', 'exile', 'library'], [engine.state.cards[c.object_id].zone for c in cards])
                    self.assertNotIn(cards[2].ref, str(session.packet('pilot:C', full=True)))
                    session = H.replay(self, session, load=True)
                    engine = session.engine
                    library = engine.state.players['B'].zones['library']
                    option, = session.packet('pilot:A', full=True)['decision']['ctx']['cast_options']
                    command = {'action_id': 'choose', 'choice': choice}
                    if choice == 'cast':
                        command['cast'] = {'cost_option': option['id'], 'targets': ['C']}
                    before = authoritative_state_hash(session.state)
                    self.assertFalse(session.act('pilot:B', command).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                    if choice == 'cast':
                        wrong = {**command, 'cast': {**command['cast'], 'targets': [source.ref]}}
                        self.assertFalse(session.act('pilot:A', wrong).ok)
                        self.assertEqual(before, authoritative_state_hash(session.state))
                    result = session.act('pilot:A', command)
                    self.assertTrue(result.ok, result.summary)
                    if choice == 'cast':
                        spell, = session.state.stack
                        self.assertEqual('A', spell.controller)
                        self.assertEqual('B', engine.state.cards[cards[1].object_id].owner)
                    self.assertIsNone(H.resolve(self, session))
                    self.assertEqual(37 if choice == 'cast' else 40, session.state.players['C'].life)
                    returned = cards[:1] if choice == 'cast' else cards[:2]
                    self.assertCountEqual([c.object_id for c in returned],
                                          engine.state.players['B'].zones['library'][:len(returned)])
                    self.assertEqual('graveyard' if choice == 'cast' else 'library', engine.state.cards[cards[1].object_id].zone)
                self.assertEqual(own_library, engine.state.players['A'].zones['library'])
                H.replay(self, session, load=True)

    def test_combat_sources_use_damaged_library_and_return_declined_card(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.model import CombatState

        for index, (name, damage) in enumerate((('Dazzling Sphinx', 4), ("Gríma, Saruman's Footman", 1))):
            with self.subTest(source=name):
                session = H.session(self, 766201 + index)
                engine = session.engine
                session.state.config.semantic_policy = 'trusted_only'
                source = H.add(self, engine, name, ref='SUPPLIER_ATTACKER')
                source.acquired_control_turn_count = -1
                cards = [H.add(self, engine, card_name, seat='B', zone='library', ref=f'COMBAT_TOP{position}')
                         for position, card_name in enumerate(('Forest', 'Lightning Bolt', 'Generic Bound Growth'))]
                library = engine.state.players['B'].zones['library']
                for card in cards:
                    library.remove(card.object_id)
                library.extend(card.object_id for card in reversed(cards))
                self.closed(source)
                engine.permissions.invalidate_current()
                engine.state.pending_decision = None
                engine.state.active_player = 'A'
                engine.state.started = True
                engine.state.phase = 'combat'
                engine.state.step = 'declare_attackers'
                engine.state.phase_index = 5
                engine.state.combat = CombatState()
                engine._issue_attackers()
                H.checkpoint(self, session)
                for _ in range(48):
                    kind = session.state.pending_decision.kind
                    if kind == 'semantic.choice':
                        break
                    if kind == 'combat.attackers':
                        command = {'action_id': 'attack', 'attackers': {source.ref: 'B'}}
                    elif kind == 'combat.blockers':
                        command = {'action_id': 'block', 'blocks': {}}
                    else:
                        self.assertEqual('priority', kind)
                        command = {'action_id': 'pass'}
                    accepted = session.act(session.pending_principals()[0], command)
                    self.assertTrue(accepted.ok, accepted.summary)
                else:
                    self.fail('The actual combat damage did not reach its supplied-card choice')
                self.assertEqual(40 - damage, session.state.players['B'].life)
                self.assertEqual(['pilot:A'], session.pending_principals())
                self.assertNotIn(cards[2].ref, str(session.packet('pilot:D', full=True)))
                accepted = session.act('pilot:A', {'action_id': 'choose', 'choice': 'decline'})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertIsNone(H.resolve(self, session))
                self.assertCountEqual([c.object_id for c in cards[:2]],
                                      engine.state.players['B'].zones['library'][:2])
                self.assertFalse(any(event.code == 'library.discover' for event in session.state.events))
                H.replay(self, session, load=True)

    def test_supplied_cast_has_no_offer_when_target_concedes_before_resolution(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H

        session = H.session(self, 766501)
        engine = session.engine
        session.state.config.semantic_policy = 'trusted_only'
        source = H.add(self, engine, 'Chaos Wand', ref='DEPARTING_SUPPLIER_WAND')
        card = H.add(self, engine, 'Lightning Bolt', seat='B', zone='library', ref='DEPARTING_SUPPLIED_CARD')
        self.closed(source)
        action = H.ready(self, session, source, {'C': 4})
        surviving_libraries = {seat: list(engine.state.players[seat].zones['library']) for seat in 'ACD'}
        H.checkpoint(self, session)
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': ['B'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        result = session.act('pilot:A', {'action_id': 'pass'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(['pilot:B'], session.pending_principals())
        result = session.act('pilot:B', {'action_id': 'concede', 'choices': {'confirm_concede': True}})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(session.state.players['B'].in_game)
        self.assertEqual('outside', session.state.cards[card.object_id].zone)
        self.assertIsNone(H.resolve(self, session))
        self.assertFalse(session.state.stack)
        self.assertEqual(surviving_libraries, {seat: session.state.players[seat].zones['library'] for seat in 'ACD'})
        H.replay(self, session, load=True)

    def test_actual_nico_casts_first_nonland_or_leaves_every_unused_card_exiled(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash

        for index, choice in enumerate(('cast', 'decline')):
            with self.subTest(choice=choice):
                session = H.session(self, 6082701 + index)
                engine = session.engine
                session.state.config.semantic_policy = 'trusted_only'
                source = H.add(self, engine, 'Nico Minoru, Runaway', ref='NICO')
                source.acquired_control_turn_count = -1
                discard = H.add(self, engine, 'Generic Bound Plains', zone='hand', ref='NICO_DISCARD')
                names = ('Forest', 'Lightning Bolt', 'Generic Bound Growth')
                cards = [H.add(self, engine, name, zone='library', ref=f'NICO_TOP{position}')
                         for position, name in enumerate(names)]
                library = engine.state.players['A'].zones['library']
                for card in cards:
                    library.remove(card.object_id)
                library.extend(card.object_id for card in reversed(cards))
                self.closed(source)
                self.closed(cards[1])
                action = H.ready(self, session, source, {'C': 2, 'R': 1})
                H.checkpoint(self, session)

                accepted = session.act('pilot:A', {'action_id': action['id'], 'cost_cards': [discard.ref], 'pay': 'auto'})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertEqual('graveyard', session.state.cards[discard.object_id].zone)
                self.assertTrue(session.state.cards[source.object_id].tapped)
                self.assertFalse(any(session.state.players['A'].mana_pool.values()))
                pending = H.resolve(self, session)
                self.assertIsNotNone(pending)
                self.assertEqual('semantic.choice', pending.kind)
                self.assertEqual(['exile', 'exile', 'library'], [session.state.cards[c.object_id].zone for c in cards])
                self.assertNotIn(cards[2].ref, str(session.packet('pilot:B', full=True)))
                session = H.replay(self, session, load=True)
                public = session.packet('pilot:A', full=True)['decision']['ctx']
                option, = public['cast_options']
                command = {'action_id': 'choose', 'choice': choice}
                if choice == 'cast':
                    command['cast'] = {'cost_option': option['id'], 'targets': ['B']}
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:B', command).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                if choice == 'cast':
                    malformed = {**command, 'cast': {**command['cast'], 'targets': [discard.ref]}}
                    self.assertFalse(session.act('pilot:A', malformed).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                accepted = session.act('pilot:A', command)
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertIsNone(H.resolve(self, session))
                self.assertEqual('exile', session.state.cards[cards[0].object_id].zone)
                self.assertEqual('library', session.state.cards[cards[2].object_id].zone)
                if choice == 'cast':
                    self.assertEqual('graveyard', session.state.cards[cards[1].object_id].zone)
                    self.assertEqual([35, 38, 38], [session.state.players[seat].life for seat in 'BCD'])
                else:
                    self.assertEqual('exile', session.state.cards[cards[1].object_id].zone)
                    self.assertEqual([40, 40, 40], [session.state.players[seat].life for seat in 'BCD'])
                self.assertFalse(any(event.code == 'library.discover' for event in session.state.events))
                H.replay(self, session, load=True)

    def test_actual_wandas_vision_casts_from_exile_only_on_the_second_spell(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H

        session = H.session(self, 6082703)
        engine = session.engine
        session.state.config.semantic_policy = 'trusted_only'
        source = H.add(self, engine, "Wanda's Vision", ref='WANDA')
        first = H.add(self, engine, 'Lightning Bolt', zone='hand', ref='FIRST_BOLT')
        second = H.add(self, engine, 'Lightning Bolt', zone='hand', ref='SECOND_BOLT')
        land = H.add(self, engine, 'Forest', zone='library', ref='WANDA_LAND')
        third = H.add(self, engine, 'Lightning Bolt', zone='library', ref='WANDA_BOLT')
        library = engine.state.players['A'].zones['library']
        for card in (land, third):
            library.remove(card.object_id)
        library.extend((third.object_id, land.object_id))
        self.closed(source)
        action = H.ready(self, session, first, {'R': 2})
        H.checkpoint(self, session)

        result = session.act('pilot:A', {'action_id': action['id'], 'targets': ['B'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(H.resolve(self, session))
        self.assertEqual(37, session.state.players['B'].life)
        self.assertEqual('library', session.state.cards[land.object_id].zone)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        action = next(row for row in actions if row.get('card') == second.ref)
        result = session.act('pilot:A', {'action_id': action['id'], 'targets': ['B'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        pending = H.resolve(self, session)
        self.assertEqual('semantic.choice', pending.kind)
        self.assertEqual(37, session.state.players['B'].life)
        self.assertEqual('exile', session.state.cards[land.object_id].zone)
        self.assertEqual('exile', session.state.cards[third.object_id].zone)
        option, = session.packet('pilot:A', full=True)['decision']['ctx']['cast_options']
        result = session.act('pilot:A', {'action_id': 'choose', 'choice': 'cast',
                                         'cast': {'cost_option': option['id'], 'targets': ['B']}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(H.resolve(self, session))
        self.assertEqual(31, session.state.players['B'].life)
        self.assertEqual('graveyard', session.state.cards[third.object_id].zone)
        self.assertEqual('exile', session.state.cards[land.object_id].zone)
        H.replay(self, session, load=True)

    def test_original_dinosaur_egg_uses_last_known_toughness_zero_and_optional_decline(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        for index,mode in enumerate(('modified','negative','decline')):
            with self.subTest(mode=mode):
                session=H.session(self,949100+index);engine=session.engine
                egg=H.add(self,engine,'Dinosaur Egg',ref='ORIGINAL_EGG');self.closed(egg)
                if mode!='negative':egg.counters['+1/+1']=2
                names=(('Hill Giant','Endless One','Generic Bound Growth') if mode=='negative' else
                    ('Trumpeting Carnosaur','Hill Giant','Generic Bound Growth'))
                cards=tuple(H.add(self,engine,name,zone='library',ref='EGG_TOP'+str(i)) for i,name in enumerate(names))
                library=engine.state.players['A'].zones['library']
                for card in cards:library.remove(card.object_id)
                library.extend(card.object_id for card in reversed(cards))
                original_library=list(library)
                killer=H.add(self,engine,'Grasp of Darkness' if mode=='negative' else 'Murder',zone='hand')
                self.closed(killer)
                action=H.ready(self,session,killer,{'B':2} if mode=='negative' else {'B':2,'C':1})
                H.checkpoint(self,session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[egg.ref],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
                self.assertEqual('graveyard',engine.state.cards[egg.object_id].zone)
                self.assertEqual({},engine.state.cards[egg.object_id].counters)
                accepted=session.act('pilot:A',{'action_id':'choose','choice':'decline' if mode=='decline' else 'apply'})
                self.assertTrue(accepted.ok,accepted.summary);decision=H.resolve(self,session)
                if mode=='decline':
                    self.assertIsNone(decision);self.assertEqual(original_library,engine.state.players['A'].zones['library'])
                    self.assertFalse(any(event.code=='library.discover' for event in engine.state.events))
                else:
                    self.assertIsNotNone(decision)
                    self.assertEqual(0 if mode=='negative' else 5,decision.continuation['effect']['progress']['amount'])
                    self.assertEqual(('exile','exile','library'),tuple(engine.state.cards[card.object_id].zone for card in cards))
                    self.assertNotIn('Generic Bound Growth',str(session.packet('pilot:B',full=True)))
                    session=H.replay(self,session,load=True);engine=session.engine
                    before=authoritative_state_hash(engine.state)
                    rejected=session.act('pilot:B',{'action_id':'choose','choice':'decline'})
                    self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
                    accepted=session.act('pilot:A',{'action_id':'choose','choice':'decline'})
                    self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
                    self.assertEqual('hand',engine.state.cards[cards[1].object_id].zone)
                    self.assertEqual('library',engine.state.cards[cards[0].object_id].zone)
                    discoveries=[event for event in engine.state.events if event.code=='library.discover']
                    self.assertEqual(1,len(discoveries));self.assertEqual(2,discoveries[0].details['exiled_count'])
                H.replay(self,session,load=True)

    def test_cast_x_and_public_amounts_keep_their_distinct_values_through_casting_and_costs(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        for index,mode in enumerate(('cast_x','public')):
            with self.subTest(mode=mode):
                session=H.session(self,949110+index);engine=session.engine
                artifacts=tuple(H.add(self,engine,'Generic Bound Mentor',ref='AMOUNT_ARTIFACT'+str(i)) for i in range(2))
                target=H.add(self,engine,'Generic Bound Body',seat='B',ref='AMOUNT_TARGET')
                names=(('Hill Giant','Generic Bound Body') if mode=='cast_x' else
                       ('Centaur Courser','Generic Discover Double Cost'))
                cards=tuple(H.add(self,engine,name,zone='library',ref='AMOUNT_TOP'+str(i)) for i,name in enumerate(names))
                library=engine.state.players['A'].zones['library']
                for card in cards:library.remove(card.object_id)
                library.extend(card.object_id for card in reversed(cards))
                source=H.add(self,engine,'Generic Discover Cast X' if mode=='cast_x' else
                    'Generic Discover Public Quantity',zone='hand');self.closed(source)
                action=H.ready(self,session,source,{'R':1,'C':3} if mode=='cast_x' else {'R':1});H.checkpoint(self,session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto',**({'x':3} if mode=='cast_x' else {})})
                self.assertTrue(accepted.ok,accepted.summary);decision=H.resolve(self,session)
                self.assertIsNotNone(decision)
                self.assertEqual(3 if mode=='cast_x' else 2,decision.continuation['effect']['progress']['amount'])
                self.assertTrue(all(engine.state.cards[card.object_id].zone=='exile' for card in cards))
                session=H.replay(self,session,load=True);engine=session.engine
                if mode=='cast_x':
                    accepted=session.act('pilot:A',{'action_id':'choose','choice':'decline'})
                    self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
                    self.assertEqual('hand',engine.state.cards[cards[1].object_id].zone)
                else:
                    options=session.packet('pilot:A',full=True)['decision']['ctx']['cast_options']
                    accepted=session.act('pilot:A',{'action_id':'choose','choice':'cast','cast':{
                        'cost_option':options[0]['id'],'targets':[target.ref],
                        'sacrifice_cards':[artifact.ref for artifact in artifacts]}})
                    self.assertTrue(accepted.ok,accepted.summary)
                    self.assertTrue(all(engine.state.cards[artifact.object_id].zone=='graveyard' for artifact in artifacts))
                    event=next(event for event in engine.state.events if event.code=='library.discover')
                    self.assertEqual(2,event.details['amount'])
                    H.resolve(self,session);self.assertEqual('graveyard',engine.state.cards[target.object_id].zone)
                H.replay(self,session,load=True)

    def test_original_hidden_nursery_pays_sacrifices_and_discovers_after_the_source_leaves(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.session(self,949060);engine=session.engine
        candidate=H.add(self,engine,'Hill Giant',zone='library',ref='LAND_DISCOVERY')
        source=H.add(self,engine,'Hidden Nursery');self.closed(source)
        H.ready(self,session,source,{'G':1,'C':4})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in actions if row['id'].startswith('activate:'+source.ref+':')
            and 'Discover' in row.get('label',''))
        H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',engine.state.cards[source.object_id].zone)
        self.assertEqual(0,sum(engine.state.players['A'].mana_pool.values()))
        self.assertIsNotNone(H.resolve(self,session))
        self.assertEqual('exile',engine.state.cards[candidate.object_id].zone)
        session=H.replay(self,session,load=True);engine=session.engine
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'decline'})
        self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
        self.assertEqual('hand',engine.state.cards[candidate.object_id].zone)
        self.assertEqual('graveyard',engine.state.cards[source.object_id].zone)
        H.replay(self,session,load=True)

    def test_discovered_spell_additional_cost_replacement_preserves_parent_permission_and_pays_once(self):
        self.assert_discovered_cost_replacement('Cascade Trial',1,949070)

    def test_two_discovered_spell_cost_objects_wait_and_resume_their_distinct_replacements(self):
        self.assert_discovered_cost_replacement('Generic Discover Double Cost',2,949071)

    def test_paid_conditional_source_return_waits_for_owner_aura_choice_before_parent_followup(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.conditional_exile_model import ConditionalExileBinding,ExiledObjectIdentity
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.record import authoritative_state_hash
        session=H.session(self,949080);engine=session.engine
        cost=H.add(self,engine,'Generic Bound Body',ref='RETURN_COST')
        held=H.add(self,engine,'Generic Conditional Aura',seat='C',zone='exile',ref='RETURN_AURA')
        attachment=H.add(self,engine,'Generic Bound Body',seat='B',ref='RETURN_SUBJECT')
        binding=ConditionalExileBinding('existing-discover-cost-hold',
            ContinuousObjectIdentity(cost.object_id,cost.logical_object_id),
            (ExiledObjectIdentity(held.object_id,held.ref,held.logical_object_id),))
        ZoneTransitionOwner(engine).remember_conditional_exile(binding)
        candidate=H.add(self,engine,'Generic Conditional Cast Sacrifice',zone='library',ref='RETURN_SPELL');self.closed(candidate)
        source=H.add(self,engine,'Generic Discover Followup',zone='hand');self.closed(source)
        action=H.ready(self,session,source,{'R':1});H.checkpoint(self,session)
        before_hand=len(session.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
        options=session.packet('pilot:A',full=True)['decision']['ctx']['cast_options']
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'cast',
            'cast':{'cost_option':options[0]['id'],'sacrifice_cards':[cost.ref]}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('aura.entry',session.state.pending_decision.kind)
        self.assertEqual(['pilot:C'],list(session.pending_principals()))
        self.assertEqual(('graveyard','exile','stack','stack'),tuple(engine.state.cards[card.object_id].zone
            for card in (cost,held,candidate,source)))
        self.assertEqual(before_hand-1,len(engine.state.players['A'].zones['hand']))
        session=H.replay(self,session,load=True);engine=session.engine
        before=authoritative_state_hash(engine.state)
        rejected=session.act('pilot:A',{'action_id':'choose','aura_target':attachment.ref})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        rejected=session.act('pilot:C',{'action_id':'choose','aura_target':cost.ref})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        accepted=session.act('pilot:C',{'action_id':'choose','aura_target':attachment.ref})
        self.assertTrue(accepted.ok,accepted.summary)
        returned=engine.state.cards[held.object_id]
        self.assertEqual(('battlefield','C',attachment.object_id),(returned.zone,returned.controller,returned.attached_to))
        self.assertEqual('graveyard',engine.state.cards[source.object_id].zone)
        self.assertEqual('stack',engine.state.cards[candidate.object_id].zone)
        self.assertEqual(before_hand,len(engine.state.players['A'].zones['hand']))
        H.resolve(self,session)
        self.assertEqual(before_hand+1,len(engine.state.players['A'].zones['hand']))
        self.assertEqual(1,sum(event.code=='cost.sacrifice' and event.details.get('object')==cost.ref for event in engine.state.events))
        H.replay(self,session,load=True)

    def assert_discovered_cost_replacement(self,candidate_name,cost_count,seed):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=H.session(self,seed);engine=session.engine
        candidate=H.add(self,engine,candidate_name,zone='library',ref='COST_CANDIDATE');self.closed(candidate)
        artifacts=tuple(H.add(self,engine,'Generic Bound Mentor',ref='COST_ARTIFACT'+str(index)) for index in range(cost_count))
        target=H.add(self,engine,'Generic Bound Body',seat='B',ref='COST_TARGET')
        # Same reviewed replacement controls as the current SBA/casting tests;
        # Dauthi whole-card strict admission is not the claim under test.
        H.add(self,engine,'Dauthi Voidwalker',seat='B',ref='COST_REPLACE_ONE')
        H.add(self,engine,'Dauthi Voidwalker',seat='B',ref='COST_REPLACE_TWO')
        source=H.add(self,engine,'Generic Discover Followup',zone='hand');self.closed(source)
        action=H.ready(self,session,source,{'R':1});H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
        options=session.packet('pilot:A',full=True)['decision']['ctx']['cast_options']
        self.assertTrue(options)
        self.assertTrue(all(artifact.ref in options[0]['choice_schema']['sacrifice_cards']['legal_refs'] for artifact in artifacts))
        before_hand=list(engine.state.players['A'].zones['hand'])
        before_payment=authoritative_state_hash(engine.state)
        rejected=session.act('pilot:A',{'action_id':'choose','choice':'cast',
            'cast':{'_mana_payment_id':'FORGED','cost_option':options[0]['id'],
                'targets':[target.ref],'sacrifice_cards':[artifact.ref for artifact in artifacts]}})
        self.assertFalse(rejected.ok);self.assertEqual(before_payment,authoritative_state_hash(engine.state))
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'cast',
            'cast':{'cost_option':options[0]['id'],'targets':[target.ref],'sacrifice_cards':[artifact.ref for artifact in artifacts]}})
        if not accepted.ok:self.assertEqual(before_payment,authoritative_state_hash(engine.state))
        self.assertTrue(accepted.ok,accepted.summary)
        for index in range(cost_count):
            self.assertEqual('replacement.order',session.state.pending_decision.kind)
            self.assertEqual('exile',engine.state.cards[candidate.object_id].zone)
            self.assertTrue(all(engine.state.cards[artifact.object_id].zone=='battlefield' for artifact in artifacts))
            self.assertFalse(any(item.card_object_id==candidate.object_id for item in session.state.stack))
            self.assertEqual(before_hand,engine.state.players['A'].zones['hand'])
            session=H.replay(self,session,load=True);engine=session.engine
            before=authoritative_state_hash(engine.state)
            selected=session.packet('pilot:A',full=True)['decision']['ctx']['options'][index%2]['id']
            self.assertEqual(before,authoritative_state_hash(engine.state),'Replacement projection changed authoritative state')
            rejected=session.act('pilot:B',{'action_id':'choose','replacement':selected})
            self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
            accepted=session.act('pilot:A',{'action_id':'choose','replacement':selected})
            self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(all(engine.state.cards[artifact.object_id].zone=='exile' for artifact in artifacts))
        self.assertEqual('stack',engine.state.cards[candidate.object_id].zone)
        self.assertEqual(len(before_hand)+1,len(engine.state.players['A'].zones['hand']))
        for artifact in artifacts:
            self.assertEqual(1,sum(event.code=='cost.sacrifice' and event.details.get('object')==artifact.ref for event in engine.state.events))
        # The same opponent replacements also apply to each A-owned spell's
        # eventual graveyard destination, independently of its paid cost.
        self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertEqual('stack',engine.state.cards[source.object_id].zone)
        selected=session.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
        accepted=session.act('pilot:A',{'action_id':'choose','replacement':selected})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('exile',engine.state.cards[source.object_id].zone)
        self.assertIsNotNone(H.resolve(self,session))
        self.assertEqual('graveyard',engine.state.cards[target.object_id].zone)
        selected=session.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
        accepted=session.act('pilot:A',{'action_id':'choose','replacement':selected})
        self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
        self.assertEqual('exile',engine.state.cards[candidate.object_id].zone)
        H.replay(self,session,load=True)

    def test_original_daring_discovery_exiles_until_inclusive_limit_then_casts_or_puts_in_hand(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        for index,choice in enumerate(('cast','decline')):
            with self.subTest(choice=choice):
                session=H.session(self,949001+index);engine=session.engine
                names=('Forest','Trumpeting Carnosaur','Hill Giant','Generic Bound Growth')
                cards=tuple(H.add(self,engine,name,zone='library',ref='DISCOVER'+str(i)) for i,name in enumerate(names))
                library=engine.state.players['A'].zones['library']
                for card in cards:library.remove(card.object_id)
                library.extend(card.object_id for card in reversed(cards))
                target=H.add(self,engine,'Generic Bound Body',seat='B',ref='BLOCKER')
                source=H.add(self,engine,'Daring Discovery',zone='hand');self.closed(source)
                action=H.ready(self,session,source,{'R':2,'C':4});H.checkpoint(self,session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
                self.assertEqual(['exile','exile','exile','library'],
                    [engine.state.cards[card.object_id].zone for card in cards])
                self.assertIn('Hill Giant',str(session.packet('pilot:B',full=True)))
                self.assertNotIn('Generic Bound Growth',str(session.packet('pilot:B',full=True)))
                session=H.replay(self,session,load=True);engine=session.engine
                response={'action_id':'choose','choice':choice}
                before=authoritative_state_hash(engine.state)
                rejected=session.act('pilot:B',response)
                self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
                accepted=session.act('pilot:A',response)
                self.assertTrue(accepted.ok,accepted.summary)
                if choice=='cast':
                    self.assertTrue(any(item.card_object_id==cards[2].object_id and item.kind=='spell' for item in session.state.stack))
                H.resolve(self,session)
                self.assertEqual('battlefield' if choice=='cast' else 'hand',engine.state.cards[cards[2].object_id].zone)
                self.assertTrue(all(engine.state.cards[card.object_id].zone=='library' for card in (cards[0],cards[1],cards[3])))
                self.assertEqual(1,sum(event.code=='library.discover' for event in engine.state.events))
                self.assertNotIn('Generic Bound Growth',str(session.packet('pilot:B',full=True)))
                H.replay(self,session,load=True)

    def test_original_daring_discovery_empty_library_completes_without_a_choice(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.session(self,949010);engine=session.engine
        for oid in tuple(engine.state.players['A'].zones['library']):engine.move_card(oid,'outside',log=False)
        target=H.add(self,engine,'Generic Bound Body',seat='B',ref='BLOCKER')
        source=H.add(self,engine,'Daring Discovery',zone='hand')
        self.closed(source)
        action=H.ready(self,session,source,{'R':2,'C':4});H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(H.resolve(self,session))
        self.assertEqual(1,sum(event.code=='library.discover' for event in engine.state.events))
        H.replay(self,session,load=True)

    def test_nested_cast_uses_front_card_types_and_finishes_followup_before_the_new_spell_resolves(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.session(self,949020);engine=session.engine
        names=('Forest','Cascade Creature // Cascade Refuge','Generic Bound Growth')
        cards=tuple(H.add(self,engine,name,zone='library',ref='NESTED'+str(i)) for i,name in enumerate(names))
        library=engine.state.players['A'].zones['library']
        for card in cards:library.remove(card.object_id)
        library.extend(card.object_id for card in reversed(cards))
        source=H.add(self,engine,'Generic Discover Followup',zone='hand');self.closed(source)
        action=H.ready(self,session,source,{'R':1});H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
        self.assertEqual(('exile','exile','library'),tuple(engine.state.cards[card.object_id].zone for card in cards))
        session=H.replay(self,session,load=True);engine=session.engine
        options=session.packet('pilot:A',full=True)['decision']['ctx']['cast_options']
        self.assertEqual(['Cascade Creature'],[option.get('face') for option in options])
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'cast',
            'cast':{'face':'Cascade Creature','cost_option':options[0]['id']}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',engine.state.cards[source.object_id].zone)
        self.assertEqual('hand',engine.state.cards[cards[2].object_id].zone)
        self.assertTrue(any(item.kind=='spell' and item.card_object_id==cards[1].object_id for item in session.state.stack))
        H.resolve(self,session)
        self.assertEqual('battlefield',engine.state.cards[cards[1].object_id].zone)
        self.assertEqual('Cascade Creature',engine.state.cards[cards[1].object_id].active_face)
        H.replay(self,session,load=True)

    def test_original_discovery_uses_combined_split_value_then_offers_distinct_legal_faces(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=H.session(self,949030);engine=session.engine
        cards=tuple(H.add(self,engine,name,zone='library',ref='SPLIT'+str(i))
            for i,name in enumerate(('Boom // Bust','Dead // Gone','Generic Bound Growth')))
        library=engine.state.players['A'].zones['library']
        for card in cards:library.remove(card.object_id)
        library.extend(card.object_id for card in reversed(cards))
        target=H.add(self,engine,'Generic Bound Body',seat='B',ref='BLOCKER')
        source=H.add(self,engine,'Daring Discovery',zone='hand');self.closed(source)
        action=H.ready(self,session,source,{'R':2,'C':4});H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
        self.assertEqual(['exile','exile','library'],[engine.state.cards[card.object_id].zone for card in cards])
        session=H.replay(self,session,load=True);engine=session.engine
        options=session.packet('pilot:A',full=True)['decision']['ctx']['cast_options']
        self.assertEqual({'Dead','Gone'},{option.get('face') for option in options})
        self.assertEqual(len(options),len({option['id'] for option in options}))
        gone=next(option for option in options if option['face']=='Gone')
        before=authoritative_state_hash(engine.state)
        rejected=session.act('pilot:A',{'action_id':'choose','choice':'cast',
            'cast':{'face':'Dead // Gone','cost_option':gone['id'],'targets':[target.ref]}})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'cast',
            'cast':{'face':'Gone','cost_option':gone['id'],'targets':[target.ref]}})
        self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
        self.assertEqual('hand',engine.state.cards[target.object_id].zone)
        self.assertEqual('graveyard',engine.state.cards[cards[1].object_id].zone)
        self.assertEqual('library',engine.state.cards[cards[0].object_id].zone)
        H.replay(self,session,load=True)

    def test_original_discovery_moves_an_uncastable_card_to_hand_and_free_x_cast_announces_zero(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        for index,name in enumerate(('Generic Bound Growth','Endless One')):
            with self.subTest(candidate=name):
                session=H.session(self,949040+index);engine=session.engine
                card=H.add(self,engine,name,zone='library',ref='CANDIDATE')
                target=H.add(self,engine,'Generic Bound Body',seat='B',ref='BLOCKER')
                source=H.add(self,engine,'Daring Discovery',zone='hand');self.closed(source)
                action=H.ready(self,session,source,{'R':2,'C':4});H.checkpoint(self,session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);decision=H.resolve(self,session)
                if name=='Generic Bound Growth':
                    self.assertIsNone(decision)
                    self.assertEqual('hand',engine.state.cards[card.object_id].zone)
                else:
                    self.assertIsNotNone(decision);self.closed(card)
                    session=H.replay(self,session,load=True);engine=session.engine
                    before=authoritative_state_hash(engine.state)
                    rejected=session.act('pilot:A',{'action_id':'choose','choice':'cast','cast':{'x':1}})
                    self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
                    accepted=session.act('pilot:A',{'action_id':'choose','choice':'cast','cast':{'x':0}})
                    self.assertTrue(accepted.ok,accepted.summary)
                    spell=next(item for item in session.state.stack if item.card_object_id==card.object_id)
                    self.assertEqual(0,spell.x_value);H.resolve(self,session)
                    self.assertEqual('graveyard',engine.state.cards[card.object_id].zone)
                H.replay(self,session,load=True)

    def test_cleanup_commander_replacement_suspends_the_whole_bottom_batch_then_resumes_the_tail(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=H.session(self,949050);engine=session.engine
        commander=next(card for card in engine.state.cards.values() if card.owner=='A' and card.is_commander)
        engine.state.players['A'].zones[commander.zone].remove(commander.object_id)
        commander.zone='library';commander.zone_change_counter+=1
        commander.known_to=['A'];commander.revealed_to=['A']
        engine.state.players['A'].zones['library'].append(commander.object_id)
        other=H.add(self,engine,'Generic Bound Body',zone='library',ref='CLEANUP_OTHER')
        candidate=H.add(self,engine,'Endless One',zone='library',ref='CLEANUP_ZERO')
        tail=H.add(self,engine,'Generic Bound Growth',zone='library',ref='CLEANUP_TAIL')
        cards=(commander,other,candidate,tail);library=engine.state.players['A'].zones['library']
        for card in cards:library.remove(card.object_id)
        library.extend(card.object_id for card in reversed(cards))
        source=H.add(self,engine,'Generic Discover Zero',zone='hand');self.closed(source)
        action=H.ready(self,session,source,{'R':1});H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'decline'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertEqual(('exile','exile','library'),tuple(engine.state.cards[card.object_id].zone
            for card in (commander,other,tail)))
        session=H.replay(self,session,load=True);engine=session.engine
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        selected=next(option['id'] for option in options if not option['id'].startswith('decline:'))
        before=authoritative_state_hash(engine.state)
        rejected=session.act('pilot:B',{'action_id':'choose','replacement':selected})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        accepted=session.act('pilot:A',{'action_id':'choose','replacement':selected})
        self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
        self.assertEqual(('command','library','hand','hand'),tuple(engine.state.cards[card.object_id].zone for card in cards))
        self.assertEqual(1,sum(event.code=='library.discover' for event in engine.state.events))
        H.replay(self,session,load=True)


class FixedTopExileTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "fixed-top-exile.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures" / name for name in (
            "bound-effect-program-cards.json", "discover-cards.json", "global-alternative-cost-cards.json",
            "morph-rules-cards.json", "prototype-cards.json", "chosen-impulse-access-cards.json",
            "declared-impulse-access-cards.json", "end-step-impulse-access-cards.json",
            "resolving-top-play-cards.json", "distinct-sacrifice-cards.json", "captured-draw-cast-cards.json",
            "draw-rules-cards.json", "fixed-top-order-cards.json", "paid-sacrifice-creature-stat-cards.json",
            "bargain-cards.json", "external-entry-tapped-cards.json", "revealed-entry-followup-cards.json",
            "counter-doubling-cards.json", "counter-placement-event-cards.json", "fixed-extra-turn-cards.json")], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Fixed top exile witness", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    def options(self, session):
        self.assertEqual("semantic.choice", session.state.pending_decision.kind)
        return session.packet("pilot:A", full=True)["decision"]["ctx"]["cast_options"]

    def cast_result(self, session, card, targets=()):
        option, = [row for row in self.options(session) if row["card"] == card.ref]
        cast = {"cost_option": option["id"]}
        if targets:
            cast["targets"] = list(targets)
        result = session.act("pilot:A", {"action_id": "choose", "choice": "cast", "cast": cast})
        self.assertTrue(result.ok, result.summary)

    def chosen_impulse_options(self, session):
        self.assertEqual("semantic.choice", session.state.pending_decision.kind)
        return {row["id"] for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["objects"]}

    def test_chosen_impulse_scope_and_independent_binding_fail_closed(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.compiler.discover_templates import _chosen_impulse_template
        from quorune.fixed_top_exile import CHOSEN_IMPULSE_CAPABILITY, CHOSEN_IMPULSE_SCOPE, validate_fixed_top_exile
        from quorune.oracle_ir import compile_oracle_card
        from test_public_query_effect_amounts import query_amount_record

        text = ("Exile the top two cards of your library. Choose one of them. "
                "Until the end of your next turn, you may play that card.")
        _, (effect,), _, _ = _chosen_impulse_template(text)
        self.assertEqual(CHOSEN_IMPULSE_SCOPE, validate_fixed_top_exile(effect))
        for change in ({"count": True}, {"scope": "each_library"}, {"player": "B"}, {"duration": "until_used"}):
            with self.assertRaises(ValueError):
                validate_fixed_top_exile({**effect, **change})
        self.assertIsNone(_chosen_impulse_template(text.replace("Choose one of them. ", "").replace("that card", "one of those cards")))
        self.assertIsNone(_chosen_impulse_template(text.replace("your library", "an opponent's library")))
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        self.assertEqual("exact", compile_oracle_card(query_amount_record(text, suffix=608280020), **options).status)
        with patch("quorune.compiler.discover_templates._chosen_impulse_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(query_amount_record(text, suffix=608280021), **options).status)

        session = self.session(60828020)
        source = self.add(session.engine, "Riverwheel Sweep", zone="hand")
        self.assert_closed(session, source)
        program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                    if CHOSEN_IMPULSE_CAPABILITY in p.capability_dependencies]
        deps = tuple(cap for cap in program.capability_dependencies if cap != CHOSEN_IMPULSE_CAPABILITY)
        forged = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(forged, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + CHOSEN_IMPULSE_CAPABILITY, binding["blockers"])

    def cast_riverwheel(self, session, sweep, creature):
        self.priority(session, "A")
        offer = self.action(session, "A", sweep)
        option = next(row for row in offer["cost_options"]
                      if row["requirements"]["U"] == 1 and row["requirements"]["GENERIC"] == 4)
        result = session.act("pilot:A", {"action_id": offer["id"], "cost_option": option["id"],
                                         "targets": [creature.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def test_riverwheel_chooses_only_actual_exile_results_and_plays_a_land(self):
        from quorune.record import authoritative_state_hash

        session = self.session(60828021)
        engine = session.engine
        sweep = self.add(engine, "Riverwheel Sweep", zone="hand")
        hand_land = self.add(engine, "Mountain", zone="hand", ref="ordinary-land")
        creature = self.add(engine, "Grizzly Bears", seat="B")
        hidden = self.add(engine, "Generic Bound Plains", zone="library", ref="third-hidden-card")
        bolt = self.add(engine, "Lightning Bolt", zone="library", ref="unchosen-bolt")
        land = self.add(engine, "Mountain", zone="library", ref="chosen-land")
        unrelated = self.add(engine, "Mountain", zone="exile", ref="unrelated-exile")
        self.assert_closed(session, sweep)
        self.prepare(session, mana={"U": 3, "C": 8, "R": 1})
        self.checkpoint(session)
        self.cast_riverwheel(session, sweep, creature)
        self.assertIsNotNone(self.resolve(session))
        self.assertEqual({bolt.ref, land.ref}, self.chosen_impulse_options(session))
        self.assertTrue(engine.state.cards[creature.object_id].tapped)
        self.assertEqual(3, engine.state.cards[creature.object_id].counters["stun"])
        self.assertNotIn(hidden.ref, str(session.packet("pilot:B", full=True)))
        self.assertIsNone(session.packet("pilot:B", full=True)["decision"])
        before = authoritative_state_hash(session.state)
        for principal, selected in (("pilot:B", [land.ref]), ("pilot:A", []),
                                    ("pilot:A", [unrelated.ref]), ("pilot:A", [bolt.ref, land.ref])):
            self.assertFalse(session.act(principal, {"action_id": "choose", "objects": selected}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": "choose", "objects": [land.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.priority(session, "A")
        ids = {row["id"] for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]}
        self.assertIn(f"play-land:{land.ref}", ids)
        self.assertNotIn(f"cast:{bolt.ref}", ids)
        self.assertNotIn(f"play-land:{unrelated.ref}", ids)
        result = session.act("pilot:A", {"action_id": f"play-land:{land.ref}"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", engine.state.cards[land.object_id].zone)
        self.assertEqual("exile", engine.state.cards[bolt.object_id].zone)
        self.priority(session, "A")
        ids = {row["id"] for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]}
        self.assertNotIn(f"play-land:{hand_land.ref}", ids)
        self.replay(session, load=True)

    def test_research_desk_replays_its_pending_choice_after_sacrifice_and_pays_normal_cost(self):
        session = self.session(60828022)
        engine = session.engine
        desk = self.add(engine, "Mishra's Research Desk")
        mountain = self.add(engine, "Mountain", zone="hand")
        bear = self.add(engine, "Grizzly Bears", zone="library", ref="desk-unchosen-bear")
        bolt = self.add(engine, "Lightning Bolt", zone="library", ref="desk-chosen-bolt")
        self.assert_closed(session, desk)
        self.prepare(session, mana={"C": 1})
        self.checkpoint(session)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = [row for row in actions if row.get("source") == desk.ref and row["action"] == "activate"]
        result = session.act("pilot:A", {"action_id": activation["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", engine.state.cards[desk.object_id].zone)
        self.assertIsNotNone(self.resolve(session))
        session = self.replay(session, load=True)
        self.assertEqual({bear.ref, bolt.ref}, self.chosen_impulse_options(session))
        result = session.act("pilot:A", {"action_id": "choose", "objects": [bolt.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.priority(session, "A")
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(row["id"] == f"cast:{bolt.ref}" for row in actions))
        result = session.act("pilot:A", {"action_id": f"play-land:{mountain.ref}"})
        self.assertTrue(result.ok, result.summary)
        self.cast_for(session, "A", bolt, ["D"])
        self.assertIsNone(self.resolve(session))
        self.assertTrue(session.state.cards[mountain.object_id].tapped)
        self.assertEqual(37, session.state.players["D"].life)
        self.assertEqual("exile", session.state.cards[bear.object_id].zone)
        self.replay(session, load=True)

    def test_riverwheel_illegal_target_exiles_nothing(self):
        session = self.session(60828023)
        engine = session.engine
        sweep = self.add(engine, "Riverwheel Sweep", zone="hand")
        creature = self.add(engine, "Grizzly Bears", seat="B")
        unsummon = self.add(engine, "Unsummon", seat="B", zone="hand")
        top = self.add(engine, "Mountain", zone="library", ref="untouched-top")
        session.state.players["B"].mana_pool["U"] = 1
        self.prepare(session, mana={"U": 3, "C": 8})
        self.checkpoint(session)
        self.cast_riverwheel(session, sweep, creature)
        self.cast_for(session, "B", unsummon, [creature.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[creature.object_id].zone)
        self.assertEqual("library", session.state.cards[top.object_id].zone)
        self.assertEqual([], session.state.players["A"].zones["exile"])
        self.replay(session)

    def test_fireglass_choice_follows_second_main_phase_and_opponent_life_loss(self):
        for seed, life_lost in ((60828024, False), (60828025, True)):
            with self.subTest(life_lost=life_lost):
                session = self.session(seed)
                engine = session.engine
                source = self.add(engine, "Fireglass Mentor")
                bolt = self.add(engine, "Lightning Bolt", zone="hand")
                bear = self.add(engine, "Grizzly Bears", zone="library", ref="mentor-bear")
                land = self.add(engine, "Mountain", zone="library", ref="mentor-land")
                self.assert_closed(session, source)
                self.prepare(session, mana={"R": 1})
                self.checkpoint(session)
                if life_lost:
                    self.cast_for(session, "A", bolt, ["C"])
                    self.assertIsNone(self.resolve(session))
                self.until(session, lambda game: game.state.phase == "postcombat_main"
                           and game.state.pending_decision is not None)
                if not life_lost:
                    self.assertEqual("library", engine.state.cards[land.object_id].zone)
                    self.assertFalse(session.state.stack)
                    continue
                self.assertIsNotNone(self.resolve(session))
                self.assertEqual({bear.ref, land.ref}, self.chosen_impulse_options(session))
                result = session.act("pilot:A", {"action_id": "choose", "objects": [land.ref]})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("until_end_of_turn",
                    engine.state.cards[land.object_id].annotations["temporary_play_permission"]["duration"])
                self.replay(session, load=True)

    def test_heroes_hangout_chooses_exile_mode_without_patrol_targets(self):
        from quorune.record import authoritative_state_hash

        session = self.session(60828026)
        engine = session.engine
        spell = self.add(engine, "Heroes' Hangout", zone="hand")
        bear = self.add(engine, "Grizzly Bears", zone="library", ref="hangout-bear")
        land = self.add(engine, "Mountain", zone="library", ref="hangout-land")
        self.assert_closed(session, spell)
        self.prepare(session, mana={"R": 1})
        self.checkpoint(session)
        offered = self.action(session, "A", spell)
        self.assertEqual(["mode_1"], offered["target_schema"]["legal_modes"])
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": offered["id"], "modes": ["mode_2"]})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", {"action_id": offered["id"], "modes": ["mode_1"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNotNone(self.resolve(session))
        self.assertEqual({bear.ref, land.ref}, self.chosen_impulse_options(session))
        chosen = session.act("pilot:A", {"action_id": "choose", "objects": [land.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.replay(session)

    def test_strongbox_raid_uses_the_actual_turn_attack(self):
        session = self.session(60828027)
        engine = session.engine
        raider = self.add(engine, "Strongbox Raider", zone="hand")
        attacker = self.add(engine, "Grizzly Bears", ref="raid-attacker")
        attacker.acquired_control_turn_count = -1
        for index in range(4):
            self.add(engine, "Mountain", ref=f"raid-mountain-{index}")
        bolt = self.add(engine, "Lightning Bolt", zone="library", ref="raid-bolt")
        land = self.add(engine, "Mountain", zone="library", ref="raid-land")
        self.assert_closed(session, raider)
        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, [(attacker, "D")])
        self.until(session, lambda game: game.state.phase == "postcombat_main" and game.state.pending_decision is not None)
        self.cast_for(session, "A", raider)
        self.assertIsNotNone(self.resolve(session))
        self.assertEqual({bolt.ref, land.ref}, self.chosen_impulse_options(session))
        chosen = session.act("pilot:A", {"action_id": "choose", "objects": [land.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.replay(session)

    def test_declared_impulse_count_requires_typed_x_and_its_runtime_capability(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.compiler.declared_effect_amounts import _RESULT_FIELDS
        from quorune.impulse_access_model import IMPULSE_ACCESS_DECLARED_COUNT_CAPABILITY, impulse_access_count_is_legal
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.impulse_access_capability_shapes import declared_impulse_access_dependencies

        session = self.session(60828030)
        source = self.add(session.engine, "Commune with Lava", zone="hand")
        self.assert_closed(session, source)
        program, = [row for row in session.engine.semantics.programs_for_oracle(source.oracle_id)
                    if IMPULSE_ACCESS_DECLARED_COUNT_CAPABILITY in row.capability_dependencies]
        effect, = program.effects
        self.assertIn(IMPULSE_ACCESS_DECLARED_COUNT_CAPABILITY, declared_impulse_access_dependencies(effect))
        for change in ({"schema_version": True}, {"count": 2}, {"count": {**effect["count"], "coefficient": -1}}):
            with self.assertRaises(ValueError):
                declared_impulse_access_dependencies({**effect, **change})
        with self.assertRaises(ValueError):
            declared_impulse_access_dependencies({key: value for key, value in effect.items() if key != "schema_version"})
        for count in (0, 12):
            self.assertTrue(impulse_access_count_is_legal(count, 2))
            self.assertFalse(impulse_access_count_is_legal(count, 1))
        self.assertFalse(impulse_access_count_is_legal(-1, 2))
        deps = tuple(cap for cap in program.capability_dependencies if cap != IMPULSE_ACCESS_DECLARED_COUNT_CAPABILITY)
        altered = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + IMPULSE_ACCESS_DECLARED_COUNT_CAPABILITY, binding["blockers"])
        record = replace(self.db.lookup("Commune with Lava"), oracle_id="declared-impulse-mutation-probe")
        with patch.dict(_RESULT_FIELDS, {"fixed_impulse_access": ()}):
            ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
            self.assertNotEqual("exact", ir.status)

    def cast_impulse_x(self, session, card, amount, *, reserve=None):
        self.priority(session, "A")
        offer = self.action(session, "A", card)
        accepted = session.act("pilot:A", {"action_id": offer["id"], "x": amount,
                                           "pay": "auto", "reserve": reserve or {}})
        self.assertTrue(accepted.ok, accepted.summary)

    def test_commune_uses_announced_x_and_keeps_ordinary_play_costs_and_timing(self):
        session = self.session(60828031)
        engine = session.engine
        zero = self.add(engine, "Commune with Lava", zone="hand", ref="zero-commune")
        positive = self.add(engine, "Commune with Lava", zone="hand", ref="positive-commune")
        hidden = self.add(engine, "Generic Bound Plains", zone="library", ref="commune-unexiled")
        land = self.add(engine, "Mountain", zone="library", ref="commune-exiled-land")
        bolt = self.add(engine, "Lightning Bolt", zone="library", ref="commune-exiled-bolt")
        self.assert_closed(session, zero)
        self.prepare(session, active="B", mana={"R": 5, "C": 6})
        self.checkpoint(session)
        initial = tuple(session.state.players["A"].zones["library"])
        self.cast_impulse_x(session, zero, 0, reserve={"R": 3})
        self.assertIsNone(self.resolve(session))
        self.assertEqual(initial, tuple(session.state.players["A"].zones["library"]))
        self.assertEqual([], session.state.players["A"].zones["exile"])
        self.cast_impulse_x(session, positive, 2, reserve={"R": 1})
        self.assertIsNone(self.resolve(session))
        self.assertEqual(initial[:-2], tuple(session.state.players["A"].zones["library"]))
        self.assertEqual({land.object_id, bolt.object_id}, set(session.state.players["A"].zones["exile"]))
        self.assertNotIn(hidden.ref, str(session.packet("pilot:B", full=True)))
        self.priority(session, "A")
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(row["id"] == f"play-land:{land.ref}" for row in actions))
        red_before = session.state.players["A"].mana_pool["R"]
        self.cast_for(session, "A", bolt, ["D"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(red_before - 1, session.state.players["A"].mana_pool["R"])
        self.assertEqual(37, session.state.players["D"].life)
        self.replay(session, load=True)

    def test_hugs_uses_twelve_sealed_entry_cards_after_the_source_leaves(self):
        session = self.session(60828032)
        engine = session.engine
        hugs = self.add(engine, "Hugs, Grisly Guardian", zone="hand")
        bounce = self.add(engine, "Unsummon", seat="B", zone="hand")
        top = self.add(engine, "Mountain", zone="library", ref="hugs-exiled-land")
        self.assert_closed(session, hugs)
        session.state.players["B"].mana_pool["U"] = 1
        self.prepare(session, mana={"R": 2, "G": 2, "C": 14})
        self.checkpoint(session)
        initial = tuple(session.state.players["A"].zones["library"])
        self.assertGreater(len(initial), 12)
        self.cast_impulse_x(session, hugs, 12)
        self.until(session, lambda game: game.state.cards[hugs.object_id].zone == "battlefield"
                   and any(item.kind == "triggered_ability" for item in game.state.stack))
        self.cast_for(session, "B", bounce, [hugs.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", engine.state.cards[hugs.object_id].zone)
        self.assertEqual(initial[:-12], tuple(session.state.players["A"].zones["library"]))
        self.assertEqual(set(initial[-12:]), set(session.state.players["A"].zones["exile"]))
        for object_id in initial[-12:]:
            permission = engine.state.cards[object_id].annotations["temporary_play_permission"]
            self.assertEqual(("A", "until_end_of_next_turn"), (permission["player"], permission["duration"]))
        self.priority(session, "A")
        self.assertTrue(session.act("pilot:A", {"action_id": f"play-land:{top.ref}"}).ok)
        self.replay(session, load=True)

    def test_next_end_step_impulse_shape_and_independent_binding(self):
        from dataclasses import replace
        import re
        from unittest.mock import patch
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.impulse_access_model import IMPULSE_ACCESS_NEXT_END_STEP_CAPABILITY
        from quorune.oracle_ir import compile_oracle_card

        session = self.session(60828035)
        yasmin = self.add(session.engine, "Yasmin Khan")
        self.assert_closed(session, yasmin)
        program, = [row for row in session.engine.semantics.programs_for_oracle(yasmin.oracle_id)
                    if IMPULSE_ACCESS_NEXT_END_STEP_CAPABILITY in row.capability_dependencies]
        deps = tuple(cap for cap in program.capability_dependencies if cap != IMPULSE_ACCESS_NEXT_END_STEP_CAPABILITY)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + IMPULSE_ACCESS_NEXT_END_STEP_CAPABILITY, binding["blockers"])
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        self.assertNotEqual("exact", compile_oracle_card(self.db.lookup("Unlucky Witness"), **options).status)
        record = replace(self.db.lookup("Yasmin Khan"), oracle_id="end-step-impulse-mutation-probe")
        with patch("quorune.compiler.impulse_access_templates._FIXED_IMPULSE_ACCESS", re.compile(r"^$")):
            self.assertNotEqual("exact", compile_oracle_card(record, **options).status)

    def activate_yasmin_impulse(self, session, yasmin):
        self.priority(session, "A")
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [row for row in actions if row.get("source") == yasmin.ref and row["action"] == "activate"]
        result = session.act("pilot:A", {"action_id": offered["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))

    def has_exiled_instant_offer(self, session, card):
        self.priority(session, "A")
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return any(row["id"] == f"cast:{card.ref}" for row in actions)

    def test_yasmin_impulse_expires_before_priority_in_the_next_end_step(self):
        session = self.session(60828036)
        engine = session.engine
        yasmin = self.add(engine, "Yasmin Khan")
        yasmin.acquired_control_turn_count = -1
        mountain = self.add(engine, "Mountain")
        bolt = self.add(engine, "Lightning Bolt", zone="library", ref="yasmin-expiring-bolt")
        self.assert_closed(session, yasmin)
        self.prepare(session)
        self.checkpoint(session)
        self.activate_yasmin_impulse(session, yasmin)
        self.assertTrue(self.has_exiled_instant_offer(session, bolt))
        self.until(session, lambda game: game.state.step == "end_step" and game.state.pending_decision is not None)
        self.assertFalse(engine.state.cards[mountain.object_id].tapped)
        self.assertNotIn("temporary_play_permission", engine.state.cards[bolt.object_id].annotations)
        self.assertFalse(self.has_exiled_instant_offer(session, bolt))
        self.replay(session, load=True)

    def test_yasmin_grant_during_end_step_reaches_the_extra_turn_end_step(self):
        session = self.session(60828037)
        engine = session.engine
        yasmin = self.add(engine, "Yasmin Khan")
        yasmin.acquired_control_turn_count = -1
        self.add(engine, "Mountain")
        warp = self.add(engine, "Time Warp", zone="hand")
        bolt = self.add(engine, "Lightning Bolt", zone="library", ref="yasmin-extra-turn-bolt")
        self.prepare(session, mana={"U": 2, "C": 3})
        self.checkpoint(session)
        self.cast_for(session, "A", warp, ["A"])
        self.assertIsNone(self.resolve(session))
        self.until(session, lambda game: game.state.step == "end_step" and game.state.pending_decision is not None)
        self.activate_yasmin_impulse(session, yasmin)
        turn = session.state.turn_sequence
        self.assertTrue(self.has_exiled_instant_offer(session, bolt))
        self.until(session, lambda game: game.state.turn_sequence > turn and game.state.phase == "precombat_main"
                   and game.state.pending_decision is not None)
        self.assertEqual("A", session.state.active_player)
        self.assertTrue(self.has_exiled_instant_offer(session, bolt))
        self.until(session, lambda game: game.state.step == "end_step" and game.state.pending_decision is not None)
        self.assertEqual("A", session.state.active_player)
        self.assertFalse(self.has_exiled_instant_offer(session, bolt))
        self.assertNotIn("temporary_play_permission", engine.state.cards[bolt.object_id].annotations)
        self.replay(session, load=True)

    def test_wiccan_cast_trigger_keeps_normal_cast_and_end_step_timing(self):
        session = self.session(60828038)
        engine = session.engine
        wiccan = self.add(engine, "Wiccan, Young Avenger")
        self.add(engine, "Mountain")
        first = self.add(engine, "Lightning Bolt", zone="hand", ref="wiccan-triggering-bolt")
        exiled = self.add(engine, "Lightning Bolt", zone="library", ref="wiccan-exiled-bolt")
        self.assert_closed(session, wiccan)
        self.prepare(session, mana={"R": 1})
        self.checkpoint(session)
        self.cast_for(session, "A", first, ["D"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("exile", engine.state.cards[exiled.object_id].zone)
        self.assertTrue(self.has_exiled_instant_offer(session, exiled))
        self.until(session, lambda game: game.state.step == "end_step" and game.state.pending_decision is not None)
        self.assertFalse(self.has_exiled_instant_offer(session, exiled))
        self.replay(session)

    def test_runestone_room_body_requires_independent_resolving_play_authority(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.fixed_top_exile import RESOLVING_TOP_PLAY_CAPABILITY, RESOLVING_TOP_PLAY_SCOPE, validate_fixed_top_exile
        from quorune.oracle_ir import compile_oracle_card

        dungeon = self.db.lookup("Dungeon of the Mad Mage")
        source = self.db.lookup("Generic Resolving Exile Play Fixture")
        room = next(line for line in dungeon.oracle_text.splitlines() if line.startswith("Runestone Caverns — "))
        self.assertEqual(room.split(" — ", 1)[1].split(" (Leads to:", 1)[0], source.oracle_text)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(source, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        effect, = ir.faces[0].nodes[0].effects
        self.assertEqual(RESOLVING_TOP_PLAY_SCOPE, validate_fixed_top_exile(effect))
        for mutation in ({"count": True}, {"count": 1}, {"player": "B"}, {"schema_version": 2},
                         {"duration": "until_end_of_turn"}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_fixed_top_exile({**effect, **mutation})
        later = replace(source, oracle_id="generic-deferred-top-play-witness",
                        oracle_text=source.oracle_text.rstrip(".") + " this turn.")
        deferred = compile_oracle_card(later, **options)
        self.assertEqual("exact", deferred.status, deferred.material_residuals)
        self.assertNotIn(RESOLVING_TOP_PLAY_CAPABILITY, deferred.faces[0].nodes[0].capability_dependencies)
        probe = replace(source, oracle_id="runestone-play-producer-mutation")
        with patch("quorune.compiler.discover_templates._fixed_top_exile_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(probe, **options).status)
        session = self.session(60828042)
        card = self.add(session.engine, source.name, zone="hand")
        self.assert_closed(session, card)
        program, = [p for p in session.engine.semantics.programs_for_oracle(card.oracle_id)
                    if RESOLVING_TOP_PLAY_CAPABILITY in p.capability_dependencies]
        deps = tuple(cap for cap in program.capability_dependencies if cap != RESOLVING_TOP_PLAY_CAPABILITY)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(result["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + RESOLVING_TOP_PLAY_CAPABILITY, result["blockers"])

    def resolve_runestone_fixture(self, session, source):
        self.assert_closed(session, source)
        self.checkpoint(session)
        self.cast_for(session, "A", source)
        pending = self.resolve(session)
        self.assertIsNotNone(pending)
        self.assertEqual("semantic.choice", pending.kind)
        packet = session.packet("pilot:A", full=True)["decision"]
        action, = [action for action in packet["legal_actions"] if action["id"] == "choose"]
        self.assertEqual(["play", "decline"], action["choice_schema"]["legal_values"])
        self.assertEqual({"field": "choice", "value": "play"}, action["choice_schema"]["cast_options_when"])

    @staticmethod
    def resolving_play_command(option, **fields):
        return {"action_id": "choose", "choice": "play", "cast": {"cost_option": option["id"], **fields}}

    def test_runestone_land_enables_paid_cast_and_holds_landfall_until_resolution_ends(self):
        from quorune.record import authoritative_state_hash

        session = self.session(60828043)
        engine = session.engine
        source = self.add(engine, "Generic Resolving Exile Play Fixture", zone="hand")
        lagac = self.add(engine, "Spitfire Lagac")
        hidden = self.add(engine, "Grizzly Bears", zone="library", ref="runestone-private-third")
        spell = self.add(engine, "Lightning Bolt", zone="library", ref="runestone-bolt")
        land = self.add(engine, "Mountain", zone="library", ref="runestone-mountain")
        self.assert_closed(session, lagac)
        self.prepare(session)
        self.resolve_runestone_fixture(session, source)
        land_option, = self.options(session)
        self.assertEqual(("land", land.ref), (land_option["kind"], land_option["card"]))
        self.assertNotIn(hidden.ref, str(session.packet("pilot:B", full=True)))
        before = authoritative_state_hash(session.state)
        for principal, command in (("pilot:B", self.resolving_play_command(land_option)),
                                   ("pilot:A", self.resolving_play_command(land_option, targets=["B"]))):
            self.assertFalse(session.act(principal, command).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "choice": "play", "cost_option": land_option["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", session.state.cards[land.object_id].zone)
        self.assertEqual(0, session.state.players["A"].land_plays_remaining)
        self.assertEqual([source.object_id], [item.card_object_id for item in session.state.stack])
        self.assertEqual(40, session.state.players["D"].life)
        cast_option, = self.options(session)
        self.assertEqual(spell.ref, cast_option["card"])
        before = authoritative_state_hash(session.state)
        for stale in (self.resolving_play_command(land_option), self.resolving_play_command(cast_option, targets=["invalid"])):
            self.assertFalse(session.act("pilot:A", stale).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", self.resolving_play_command(cast_option, targets=["D"]))
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual("stack", session.state.cards[spell.object_id].zone)
        self.assertTrue(session.state.cards[land.object_id].tapped)
        self.assertEqual(40, session.state.players["D"].life)
        self.assertIsNone(self.resolve(session))
        self.assertEqual((39, 39, 36), tuple(session.state.players[seat].life for seat in "BCD"))
        self.replay(session, load=True)

    def test_runestone_off_turn_allows_paid_sorcery_and_excludes_land(self):
        session = self.session(60828044)
        engine = session.engine
        source = self.add(engine, "Generic Resolving Exile Play Fixture", zone="hand")
        spell = self.add(engine, "Divination", zone="library", ref="runestone-off-turn-sorcery")
        land = self.add(engine, "Mountain", zone="library", ref="runestone-off-turn-land")
        self.prepare(session, active="B", mana={"U": 1, "C": 2})
        self.resolve_runestone_fixture(session, source)
        option, = self.options(session)
        self.assertEqual(spell.ref, option["card"])
        self.assertEqual(1, option["requirements"]["U"])
        result = session.act("pilot:A", self.resolving_play_command(option))
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", engine.state.cards[spell.object_id].zone)
        self.assertEqual("exile", engine.state.cards[land.object_id].zone)
        self.assertNotIn("temporary_play_permission", engine.state.cards[land.object_id].annotations)
        self.assertIsNone(self.resolve(session))
        self.replay(session)

    def test_runestone_uses_current_land_quota_and_the_selected_life_payment(self):
        for extra in (False, True):
            with self.subTest(extra=extra):
                session = self.session(60828045 + int(extra))
                engine = session.engine
                source = self.add(engine, "Generic Resolving Exile Play Fixture", zone="hand")
                if extra:
                    exploration = self.add(engine, "Exploration")
                    self.assert_closed(session, exploration)
                land = self.add(engine, "Blood Crypt", zone="library", ref="runestone-shockland")
                later = self.add(engine, "Mountain", zone="library", ref="runestone-second-land")
                self.prepare(session)
                self.resolve_runestone_fixture(session, source)
                choices = [option for option in self.options(session) if option["card"] == land.ref]
                self.assertEqual({False, True}, {option["pay_life"] for option in choices})
                paid, = [option for option in choices if option["pay_life"]]
                result = session.act("pilot:A", self.resolving_play_command(paid))
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("battlefield", engine.state.cards[land.object_id].zone)
                self.assertFalse(engine.state.cards[land.object_id].tapped)
                self.assertEqual(38, session.state.players["A"].life)
                if extra:
                    final_option, = self.options(session)
                    self.assertEqual(later.ref, final_option["card"])
                    result = session.act("pilot:A", self.resolving_play_command(final_option))
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual("battlefield", engine.state.cards[later.object_id].zone)
                else:
                    self.assertEqual("exile", engine.state.cards[later.object_id].zone)
                    self.assertNotIn("temporary_play_permission", engine.state.cards[later.object_id].annotations)
                self.assertEqual("graveyard", engine.state.cards[source.object_id].zone)
                self.replay(session)

    def test_runestone_offers_both_modal_faces_and_keeps_declined_cards_exiled(self):
        session = self.session(60828047)
        engine = session.engine
        source = self.add(engine, "Generic Resolving Exile Play Fixture", zone="hand")
        self.add(engine, "Exploration")
        self.add(engine, "Grizzly Bears", zone="graveyard")
        modal = self.add(engine, "Bala Ged Recovery // Bala Ged Sanctuary", zone="library")
        later = self.add(engine, "Mountain", zone="library", ref="runestone-declined-land")
        self.assert_closed(session, modal)
        self.prepare(session, mana={"G": 1, "C": 2})
        self.resolve_runestone_fixture(session, source)
        options = [option for option in self.options(session) if option["card"] == modal.ref]
        self.assertEqual({"Bala Ged Recovery", "Bala Ged Sanctuary"}, {option["face"] for option in options})
        land, = [option for option in options if option["kind"] == "land"]
        result = session.act("pilot:A", {"action_id": "choose", "choice": "play", "cost_option": land["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("Bala Ged Sanctuary", engine.state.cards[modal.object_id].active_face)
        remaining, = self.options(session)
        self.assertEqual(later.ref, remaining["card"])
        declined = session.act("pilot:A", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(declined.ok, declined.summary)
        self.assertEqual("exile", engine.state.cards[later.object_id].zone)
        self.assertNotIn("temporary_play_permission", engine.state.cards[later.object_id].annotations)
        self.replay(session, load=True)

    def test_runestone_land_entry_replacement_replays_and_resumes_the_parent(self):
        session = self.session(60828056)
        engine = session.engine
        source = self.add(engine, "Generic Resolving Exile Play Fixture", zone="hand")
        orb = self.add(engine, "Orb of Dreams", ref="runestone-orb-entry-order")
        untap = self.add(engine, "Generic Controlled Untapped Entry", ref="runestone-untap-entry-order")
        land = self.add(engine, "Mountain", zone="library", ref="runestone-replaced-land-entry")
        for card in (orb, untap):
            self.assert_closed(session, card)
        self.prepare(session)
        self.resolve_runestone_fixture(session, source)
        option, = [row for row in self.options(session) if row["card"] == land.ref]
        result = session.act("pilot:A", self.resolving_play_command(option))
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        self.assertEqual("exile", session.state.cards[land.object_id].zone)
        session = self.replay(session, load=True)
        choices = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        selected = next(row["id"] for row in choices if orb.ref in row["id"])
        result = session.act("pilot:A", {"action_id": "choose", "replacement": selected})
        self.assertTrue(result.ok, result.summary)
        current = session.state.cards[land.object_id]
        self.assertEqual(("battlefield", False), (current.zone, current.tapped))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual(0, session.state.players["A"].land_plays_remaining)
        self.replay(session, load=True)

    def test_throne_replaces_entry_counters_and_expires_hexproof_on_the_actual_next_turn(self):
        from dataclasses import replace
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.library_battlefield_model import LIBRARY_REVEALED_ENTRY_FOLLOWUP_CAPABILITY
        from quorune.record import authoritative_state_hash

        session = self.session(60828057)
        engine = session.engine
        source = self.add(engine, "Generic Resolving Revealed Entry Fixture", zone="hand")
        scales = self.add(engine, "Hardened Scales", ref="throne-entry-scales")
        doubler = self.add(engine, "Branching Evolution", ref="throne-entry-doubler")
        warp = self.add(engine, "Time Warp", zone="hand", ref="throne-extra-turn")
        deeper = self.add(engine, "Grizzly Bears", zone="library", ref="throne-unrevealed-creature")
        for index in range(8):
            self.add(engine, "Generic Bound Plains", zone="library", ref=f"throne-revealed-land-{index}")
        spare = self.add(engine, "Wall of Omens", zone="library", ref="throne-other-creature")
        selected = self.add(engine, "Grizzly Bears", zone="library", ref="throne-chosen-creature")
        for card in (source, scales, doubler, warp):
            self.assert_closed(session, card)
        program, = session.engine.semantics.programs_for_oracle(source.oracle_id)
        deps = tuple(cap for cap in program.capability_dependencies if cap != LIBRARY_REVEALED_ENTRY_FOLLOWUP_CAPABILITY)
        forged = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(forged, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + LIBRARY_REVEALED_ENTRY_FOLLOWUP_CAPABILITY,
                      binding["blockers"])
        self.prepare(session, mana={"C": 3, "U": 2})
        self.checkpoint(session)
        initial_shuffles = session.state.players["A"].stats.get("shuffle_count", 0)
        start_turn = session.state.turn_sequence
        self.cast_for(session, "A", source)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        menu = session.packet("pilot:A", full=True)["decision"]
        action, = [action for action in menu["legal_actions"] if action["id"] == "choose"]
        schema = action["choice_schema"]
        self.assertEqual({selected.ref, spare.ref}, set(schema["legal_refs"]))
        self.assertNotIn(deeper.ref, str(session.packet("spectator", full=True)))
        original = session.state.cards[selected.object_id].logical_object_id
        before = authoritative_state_hash(session.state)
        for seat, cards in (("B", [selected.ref]), ("A", []), ("A", [deeper.ref]), ("A", [selected.ref, spare.ref])):
            result = session.act("pilot:" + seat, {"action_id": "choose", "cards": cards})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": "choose", "cards": [selected.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        self.assertEqual("library", session.state.cards[selected.object_id].zone)
        self.assertEqual(initial_shuffles, session.state.players["A"].stats.get("shuffle_count", 0))
        session = self.replay(session, load=True)
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        choice = next(option["id"] for option in options if scales.ref in option["id"])
        result = session.act("pilot:A", {"action_id": "choose", "replacement": choice})
        self.assertTrue(result.ok, result.summary)
        current = session.state.cards[selected.object_id]
        self.assertEqual(("battlefield", {"+1/+1": 8}), (current.zone, current.counters))
        self.assertNotEqual(original, current.logical_object_id)
        effect, = session.state.continuous_effects
        self.assertEqual("until_next_turn", effect.duration.value)
        self.assertEqual("A", effect.expires_on_next_turn_of)
        self.assertEqual(current.logical_object_id, effect.locked_objects[0].logical_object_id)
        self.assertEqual("Hexproof", effect.operations[0].value)
        self.assertEqual(initial_shuffles + 1, session.state.players["A"].stats["shuffle_count"])
        self.assertEqual("library", session.state.cards[spare.object_id].zone)
        self.assertNotIn(spare.ref, str(session.packet("pilot:B", full=True)["state"]["players"]["A"]))
        self.cast_for(session, "A", warp, ["A"])
        self.assertIsNone(self.resolve(session))
        self.assertIn(effect, session.state.continuous_effects)
        self.until(session, lambda game: game.state.turn_sequence > start_turn)
        self.assertEqual("A", session.state.active_player)
        self.assertTrue(session.state.current_turn.extra)
        self.assertEqual([], session.state.continuous_effects)
        self.replay(session, load=True)

    def test_throne_shuffles_when_the_public_prefix_has_no_creature(self):
        session = self.session(60828058)
        engine = session.engine
        source = self.add(engine, "Generic Resolving Revealed Entry Fixture", zone="hand")
        deeper = self.add(engine, "Grizzly Bears", zone="library", ref="throne-creature-after-the-prefix")
        for index in range(10):
            self.add(engine, "Generic Bound Plains", zone="library", ref=f"throne-public-plains-{index}")
        self.assert_closed(session, source)
        self.prepare(session)
        self.checkpoint(session)
        initial_shuffles = session.state.players["A"].stats.get("shuffle_count", 0)
        self.cast_for(session, "A", source)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(initial_shuffles + 1, session.state.players["A"].stats["shuffle_count"])
        self.assertEqual("library", session.state.cards[deeper.object_id].zone)
        self.assertEqual([], session.state.continuous_effects)
        self.assertNotIn(deeper.ref, str(session.packet("spectator", full=True)))
        self.replay(session)

    def test_mad_wizard_offer_requires_independent_runtime_authority(self):
        from dataclasses import replace
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.drawing.captured_hand import CAPTURED_DRAW_CAST_CAPABILITY

        session = self.session(60828051)
        card = self.add(session.engine, "Generic Resolving Draw Cast Fixture", zone="hand")
        self.assert_closed(session, card)
        program, = session.engine.semantics.programs_for_oracle(card.oracle_id)
        deps = tuple(cap for cap in program.capability_dependencies if cap != CAPTURED_DRAW_CAST_CAPABILITY)
        self.assertNotEqual(program.capability_dependencies, deps)
        forged = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(forged, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPTURED_DRAW_CAST_CAPABILITY, binding["blockers"])

    def test_mad_wizard_casts_an_original_draw_after_a_private_dredge_choice(self):
        from quorune.record import authoritative_state_hash

        session = self.session(60828052)
        engine = session.engine
        source = self.add(engine, "Generic Resolving Draw Cast Fixture", zone="hand")
        old_card = self.add(engine, "Lightning Bolt", zone="hand", ref="lair-old-hand")
        dredger = self.add(engine, "Dakmor Salvage", zone="graveyard", ref="lair-dredge")
        sacrifice = self.add(engine, "Grizzly Bears", ref="lair-available-price")
        foreign = self.add(engine, "Grizzly Bears", seat="C", ref="lair-foreign-price")
        deeper = self.add(engine, "Lightning Bolt", zone="library", ref="lair-deeper-library")
        land = self.add(engine, "Mountain", zone="library", ref="lair-last-original-draw")
        for index in range(2):
            self.add(engine, "Generic Bound Plains", zone="library", ref=f"lair-dredge-mill-{index}")
        fling = self.add(engine, "Fling", zone="library", ref="lair-first-original-draw")
        for card in (source, dredger, fling):
            self.assert_closed(session, card)
        self.prepare(session, active="B")
        self.checkpoint(session)
        self.cast_for(session, "A", source)
        self.assertEqual("draw.replacement", self.resolve(session).kind)
        ordinary = session.act("pilot:A", {"action_id": "choose", "choice": "draw"})
        self.assertTrue(ordinary.ok, ordinary.summary)
        self.assertEqual("draw.replacement", session.state.pending_decision.kind)
        self.assertNotIn(fling.ref, str(session.packet("pilot:C", full=True)))
        session = self.replay(session, load=True)
        replaced = session.act("pilot:A", {"action_id": "choose", "choice": dredger.ref})
        self.assertTrue(replaced.ok, replaced.summary)

        option, = self.options(session)
        self.assertEqual(fling.ref, option["card"])
        self.assertFalse(any(option.get("requirements", {}).values()))
        self.assertEqual("hand", session.state.cards[dredger.object_id].zone)
        reveal, = [event for event in session.state.events if event.code == "hand.reveal.captured_draws"]
        self.assertEqual({fling.ref, land.ref}, set(reveal.details["objects"]))
        for principal in ("pilot:C", "spectator"):
            packet = session.packet(principal, full=True)
            hand = packet["state"]["players"]["A"]["known_hand"]
            self.assertIn(fling.ref, str(hand))
            self.assertIn(land.ref, str(hand))
            self.assertNotIn(old_card.ref, str(packet))
            self.assertNotIn(deeper.ref, str(packet))
        before = authoritative_state_hash(session.state)
        command = {"action_id": "choose", "choice": "cast", "cost_option": option["id"], "targets": ["D"]}
        for principal, invalid in (
            ("pilot:B", {**command, "sacrifice_cards": [sacrifice.ref]}),
            ("pilot:A", {**command, "sacrifice_cards": [foreign.ref]}),
            ("pilot:A", {**command, "card": old_card.ref, "sacrifice_cards": [sacrifice.ref]}),
            ("pilot:A", command),
        ):
            self.assertFalse(session.act(principal, invalid).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        accepted = session.act("pilot:A", {**command, "sacrifice_cards": [sacrifice.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[sacrifice.object_id].zone)
        self.assertEqual("stack", session.state.cards[fling.object_id].zone)
        self.assertEqual(40, session.state.players["D"].life)
        self.assertNotIn("known_hand", session.packet("spectator", full=True)["state"]["players"]["A"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(38, session.state.players["D"].life)
        self.replay(session, load=True)

    def test_mad_wizard_keeps_generated_replacement_draws_private(self):
        session = self.session(60828053)
        engine = session.engine
        source = self.add(engine, "Generic Resolving Draw Cast Fixture", zone="hand")
        reflection = self.add(engine, "Thought Reflection")
        candidate = self.add(engine, "Lightning Bolt", zone="library", ref="lair-replacement-result")
        self.assert_closed(session, source)
        self.assert_closed(session, reflection)
        self.prepare(session)
        self.checkpoint(session)
        before = len(session.state.events)
        self.cast_for(session, "A", source)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[candidate.object_id].zone)
        self.assertEqual(6, sum(event.code == "card.draw" and event.actor == "A" for event in session.state.events[before:]))
        self.assertFalse(any(event.code == "hand.reveal.captured_draws" for event in session.state.events))
        self.assertNotIn(candidate.ref, str(session.packet("pilot:C", full=True)))
        self.replay(session)

    def test_mad_wizard_cast_price_replacement_resumes_the_original_resolution(self):
        from quorune.drawing.captured_hand import CAPTURED_HAND_CONTEXT

        session = self.session(60828054)
        engine = session.engine
        source = self.add(engine, "Generic Resolving Draw Cast Fixture", zone="hand")
        price = self.add(engine, "Grizzly Bears", ref="lair-replaced-price")
        fling = self.add(engine, "Fling", zone="library", ref="lair-drawn-fling")
        for index in range(2):
            self.add(engine, "Dauthi Voidwalker", seat="B", ref=f"lair-replacement-{index}")
        self.prepare(session)
        self.checkpoint(session)
        self.cast_for(session, "A", source)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        option, = self.options(session)
        result = session.act("pilot:A", {"action_id": "choose", "choice": "cast",
            "cast": {"cost_option": option["id"], "targets": ["D"], "sacrifice_cards": [price.ref]}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        self.assertEqual("hand", session.state.cards[fling.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[price.object_id].zone)
        session = self.replay(session, load=True)
        payment = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"][0]["id"]
        result = session.act("pilot:A", {"action_id": "choose", "replacement": payment})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("exile", session.state.cards[price.object_id].zone)
        self.assertEqual("stack", session.state.cards[fling.object_id].zone)
        parent, = [item for item in session.state.stack if item.card_object_id == source.object_id]
        self.assertNotIn(CAPTURED_HAND_CONTEXT, parent.context)
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        entry = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"][0]["id"]
        result = session.act("pilot:A", {"action_id": "choose", "replacement": entry})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("exile", session.state.cards[source.object_id].zone)
        self.assertEqual(1, sum(event.code == "cost.sacrifice" and event.details.get("object") == price.ref
                                for event in session.state.events))
        self.replay(session, load=True)

    def test_mad_wizard_may_decline_and_finishes_the_reveal(self):
        session = self.session(60828055)
        engine = session.engine
        source = self.add(engine, "Generic Resolving Draw Cast Fixture", zone="hand")
        spell = self.add(engine, "Divination", zone="library", ref="lair-declined-draw")
        self.prepare(session, active="B")
        self.checkpoint(session)
        self.cast_for(session, "A", source)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        option, = self.options(session)
        self.assertEqual(spell.ref, option["card"])
        result = session.act("pilot:A", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", session.state.cards[spell.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertNotIn("known_hand", session.packet("spectator", full=True)["state"]["players"]["A"])
        self.assertIn(spell.ref, str(session.packet("pilot:B", full=True)["state"]["players"]["A"]["known_hand"]))
        self.replay(session)

    def test_oubliette_body_and_matching_require_distinct_roles(self):
        from dataclasses import replace
        from itertools import product
        from unittest.mock import patch
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.distinct_sacrifices import DISTINCT_SACRIFICE_CAPABILITY, distinct_sacrifice_options

        room = next(line for line in self.db.lookup("Tomb of Annihilation").oracle_text.splitlines()
                    if line.startswith("Oubliette — "))
        record = self.db.lookup("Generic Distinct Sacrifice Fixture")
        self.assertEqual(room.split(" — ", 1)[1].split(" (Leads to:", 1)[0], record.oracle_text)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        self.assertEqual("exact", compile_oracle_card(record, **options).status)
        with patch("quorune.compiler.resolution_effect_templates._distinct_sacrifice_template", return_value=None):
            mutant = replace(record, oracle_id="distinct-sacrifice-producer-mutation")
            self.assertNotEqual("exact", compile_oracle_card(mutant, **options).status)

        session = self.session(60828050)
        source = self.add(session.engine, record.name, zone="hand")
        self.assert_closed(session, source)
        program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
                    if DISTINCT_SACRIFICE_CAPABILITY in p.capability_dependencies]
        deps = tuple(cap for cap in program.capability_dependencies if cap != DISTINCT_SACRIFICE_CAPABILITY)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(result["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + DISTINCT_SACRIFICE_CAPABILITY, result["blockers"])

        assignments = tuple(product((None, 0, 1, 2), repeat=3))
        for masks in product(range(1, 8), repeat=3):
            legal = []
            for assignment in assignments:
                used = [card for card in assignment if card is not None]
                if len(used) == len(set(used)) and all(
                    card is None or masks[card] & (1 << role) for role, card in enumerate(assignment)
                ):
                    legal.append((len(used), assignment[0]))
            maximum = max(count for count, _ in legal)
            first = {card for count, card in legal if count == maximum}
            refs, may_skip, actual = distinct_sacrifice_options({str(i): mask for i, mask in enumerate(masks)}, 3)
            self.assertEqual((maximum, None in first, {str(i) for i in first if i is not None}),
                             (actual, may_skip, set(refs)), masks)

    def sacrifice_role_offer(self, session):
        self.assertEqual("semantic.choice", session.state.pending_decision.kind)
        return next(action["choice_schema"] for action in session.packet("pilot:A", full=True)["decision"]["legal_actions"]
                    if action["id"] == "choose")

    def test_oubliette_discards_privately_then_sacrifices_a_complete_feasible_set(self):
        from quorune.record import authoritative_state_hash

        session = self.session(60828051)
        engine = session.engine
        source = self.add(engine, "Generic Distinct Sacrifice Fixture", zone="hand")
        discard = self.add(engine, "Lightning Bolt", zone="hand", ref="oubliette-private-discard")
        artifact = self.add(engine, "Ornithopter", seat="B", ref="oubliette-only-artifact")
        engine.change_control(artifact.object_id, "A", reason="Fixture control change")
        creature = self.add(engine, "Grizzly Bears", ref="oubliette-creature")
        land = self.add(engine, "Mountain", ref="oubliette-land")
        self.prepare(session)
        self.checkpoint(session)
        self.cast_for(session, "A", source)
        self.assertEqual("choice.apnap", self.resolve(session).kind)
        self.assertNotIn(discard.ref, str(session.packet("pilot:B", full=True)))
        accepted = session.act("pilot:A", {"action_id": "choose", "cards": [discard.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", engine.state.cards[discard.object_id].zone)
        self.assertEqual([creature.ref], self.sacrifice_role_offer(session)["legal_refs"])
        before = authoritative_state_hash(session.state)
        for principal, ref in (("pilot:A", artifact.ref), ("pilot:B", creature.ref)):
            self.assertFalse(session.act(principal, {"action_id": "choose", "objects": [ref]}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", {"action_id": "choose", "objects": [creature.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual([artifact.ref], self.sacrifice_role_offer(session)["legal_refs"])
        session = self.replay(session, load=True)
        accepted = session.act("pilot:A", {"action_id": "choose", "objects": [artifact.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual([land.ref], self.sacrifice_role_offer(session)["legal_refs"])
        cards = (artifact, creature, land)
        self.assertTrue(all(session.state.cards[card.object_id].zone == "battlefield" for card in cards))
        accepted = session.act("pilot:A", {"action_id": "choose", "objects": [land.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(all(session.state.cards[card.object_id].zone == "graveyard" for card in cards))
        self.assertIn(artifact.object_id, session.state.players["B"].zones["graveyard"])
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_oubliette_empty_hand_can_leave_one_role_vacant_to_sacrifice_two(self):
        session = self.session(60828052)
        engine = session.engine
        for object_id in tuple(session.state.players["A"].zones["hand"]):
            engine.move_card(object_id, "library", reason="Fixture empty hand", log=False, semantic_events=False)
        source = self.add(engine, "Generic Distinct Sacrifice Fixture", zone="hand")
        ornithopter = self.add(engine, "Ornithopter")
        citadel = self.add(engine, "Darksteel Citadel")
        self.prepare(session)
        self.checkpoint(session)
        self.cast_for(session, "A", source)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual([ornithopter.ref], self.sacrifice_role_offer(session)["legal_refs"])
        chosen = session.act("pilot:A", {"action_id": "choose", "objects": [ornithopter.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        offer = self.sacrifice_role_offer(session)
        self.assertEqual([citadel.ref], offer["legal_refs"])
        self.assertEqual(0, offer["minimum"])
        skipped = session.act("pilot:A", {"action_id": "choose", "objects": []})
        self.assertTrue(skipped.ok, skipped.summary)
        offer = self.sacrifice_role_offer(session)
        self.assertEqual([citadel.ref], offer["legal_refs"])
        self.assertEqual(1, offer.get("minimum", 1))
        self.assertFalse(offer.get("optional", False))
        last = session.act("pilot:A", {"action_id": "choose", "objects": [citadel.ref]})
        self.assertTrue(last.ok, last.summary)
        self.assertEqual({ornithopter.object_id, citadel.object_id, source.object_id},
                         set(session.state.players["A"].zones["graveyard"]))
        self.replay(session)

    def test_fixed_top_exile_requires_its_scope_and_runtime_capability(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune.card_programs import bind_semantic_program_runtime
        from quorune.fixed_top_exile import FIXED_TOP_EXILE_CAPABILITY, validate_fixed_top_exile
        from quorune.oracle_ir import compile_oracle_card

        original = self.db.lookup("Mind's Dilation", fuzzy=False)
        ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        effect, = ir.faces[0].nodes[0].effects
        self.assertEqual("cast_event_library", validate_fixed_top_exile(effect))
        for changed in ({"supplier": "$controller"}, {"scope": "each_library"}, {"schema_version": True},
                        {"decline": {"op": "draw"}}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_fixed_top_exile({**effect, **changed})
        with patch("quorune.compiler.discover_templates.cast_event_fixed_top_exile", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(original, capability_registry=self.registry).status)

        session = self.session(6082801)
        source = self.add(session.engine, "Mind's Dilation")
        program, = [p for p in session.engine.semantics.programs_for_oracle(source.oracle_id) if p.effects]
        dependencies = tuple(cap for cap in program.capability_dependencies if cap != FIXED_TOP_EXILE_CAPABILITY)
        altered = replace(program, capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + FIXED_TOP_EXILE_CAPABILITY, binding["blockers"])

    def test_etali_casts_any_order_with_fresh_targets_and_leaves_unused_cards_exiled(self):
        from quorune.record import authoritative_state_hash

        session = self.session(6082802)
        engine = session.engine
        etali = self.add(engine, "Etali, Primal Storm")
        etali.acquired_control_turn_count = -1
        bolt = self.add(engine, "Lightning Bolt", zone="library", ref="top-exile-bolt")
        twincast = self.add(engine, "Twincast", seat="B", zone="library", ref="top-exile-twincast")
        hidden = self.add(engine, "Generic Bound Growth", seat="C", zone="library", ref="top-exile-private-next-card")
        bear = self.add(engine, "Grizzly Bears", seat="C", zone="library", ref="top-exile-unused-spell")
        land = self.add(engine, "Generic Bound Plains", seat="D", zone="library", ref="top-exile-land")
        self.assert_closed(session, etali)
        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, [(etali, "D")])
        self.assertIsNotNone(self.resolve(session))
        self.assertEqual(["exile"] * 4, [session.state.cards[c.object_id].zone for c in (bolt, twincast, bear, land)])
        self.assertEqual({bolt.ref, bear.ref}, {row["card"] for row in self.options(session)})
        self.assertNotIn(hidden.ref, str(session.packet("pilot:D", full=True)))
        self.assertIsNone(session.packet("pilot:B", full=True)["decision"])
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {"action_id": "choose", "choice": "cast",
            "cast": {"cost_option": "unoffered"}}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)

        self.cast_result(session, bolt, ["D"])
        bolt_item = session.state.stack[-1]
        self.assertEqual(bolt.object_id, bolt_item.card_object_id)
        self.assertEqual({twincast.ref, bear.ref}, {row["card"] for row in self.options(session)})
        self.cast_result(session, twincast, [bolt_item.ref])
        self.assertEqual([bear.ref], [row["card"] for row in self.options(session)])
        self.assertEqual(["triggered_ability", "spell", "spell"], [item.kind for item in session.state.stack])
        declined = session.act("pilot:A", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(declined.ok, declined.summary)
        self.assertEqual(["spell", "spell"], [item.kind for item in session.state.stack])
        self.assertEqual("exile", session.state.cards[bear.object_id].zone)
        self.assertEqual("exile", session.state.cards[land.object_id].zone)
        self.assertEqual("library", session.state.cards[hidden.object_id].zone)
        self.replay(session, load=True)

    def test_minds_dilation_uses_each_opponents_first_cast_and_only_their_top(self):
        session = self.session(6082803)
        engine = session.engine
        dilation = self.add(engine, "Mind's Dilation")
        first = self.add(engine, "Lightning Bolt", seat="B", zone="hand", ref="dilation-first-bolt")
        second = self.add(engine, "Lightning Bolt", seat="B", zone="hand", ref="dilation-second-bolt")
        third = self.add(engine, "Lightning Bolt", seat="C", zone="hand", ref="dilation-other-bolt")
        for seat, ref in (("B", "dilation-b-mana-one"), ("B", "dilation-b-mana-two"), ("C", "dilation-c-mana")):
            self.add(engine, "Mountain", seat=seat, ref=ref)
        lower = self.add(engine, "Generic Bound Growth", seat="B", zone="library", ref="dilation-untouched")
        supplied = self.add(engine, "Grizzly Bears", seat="B", zone="library", ref="dilation-supplied")
        land = self.add(engine, "Generic Bound Plains", seat="C", zone="library", ref="dilation-nonspell")
        self.assert_closed(session, dilation)
        self.prepare(session)
        self.checkpoint(session)
        self.cast(session, "B", first, ["D"])
        self.assertIsNotNone(self.resolve(session))
        self.assertEqual([supplied.ref], [row["card"] for row in self.options(session)])
        self.cast_result(session, supplied)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(("B", "A", "battlefield"), (supplied.owner, supplied.controller, supplied.zone))
        self.cast(session, "B", second, ["D"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("library", session.state.cards[lower.object_id].zone)
        self.cast(session, "C", third, ["D"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("exile", session.state.cards[land.object_id].zone)
        self.replay(session, load=True)

    def test_chandra_pays_printed_cast_cost_and_damage_runs_only_on_decline(self):
        session = self.session(6082804)
        engine = session.engine
        chandra = self.add(engine, "Chandra, Torch of Defiance")
        chandra.counters["loyalty"] = 4
        bear = self.add(engine, "Grizzly Bears", zone="library", ref="chandra-paid-top")
        self.assert_closed(session, chandra)
        self.prepare(session, mana={"C": 1, "G": 1})
        self.activate_chandra_exile(session, chandra)
        self.assertIsNotNone(self.resolve(session))
        option, = self.options(session)
        self.assertEqual((1, 1), (option["requirements"]["GENERIC"], option["requirements"]["G"]))
        declined = self.replay(session, load=True)
        self.cast_result(session, bear)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[bear.object_id].zone)
        self.assertFalse(any(session.state.players["A"].mana_pool.values()))
        self.assertEqual([40, 40, 40], [session.state.players[seat].life for seat in "BCD"])
        self.replay(session, load=True)
        result = declined.act("pilot:A", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(declined))
        self.assertEqual([38, 38, 38], [declined.state.players[seat].life for seat in "BCD"])
        self.assertEqual("exile", declined.state.cards[bear.object_id].zone)
        self.replay(declined, load=True)

    def activate_chandra_exile(self, session, chandra):
        self.checkpoint(session)
        actions = session.packet("pilot:A", full=True)["decision"]["legal_actions"]
        action, = [row for row in actions if row["id"] == f"activate:{chandra.ref}:ab1"]
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def test_chandra_offers_face_down_cast_and_excludes_absent_printed_price(self):
        for seed, name in ((6082805, "Zoetic Cavern"), (6082806, "Ancestral Vision")):
            with self.subTest(name=name):
                session = self.session(seed)
                chandra = self.add(session.engine, "Chandra, Torch of Defiance")
                chandra.counters["loyalty"] = 4
                card = self.add(session.engine, name, zone="library", ref="chandra-price-witness")
                self.assert_closed(session, card)
                self.prepare(session, mana={"C": 3})
                self.activate_chandra_exile(session, chandra)
                decision = self.resolve(session)
                if name == "Ancestral Vision":
                    self.assertIsNone(decision)
                    self.assertEqual("exile", session.state.cards[card.object_id].zone)
                    self.assertEqual([38, 38, 38], [session.state.players[seat].life for seat in "BCD"])
                else:
                    self.assertIsNotNone(decision)
                    option, = self.options(session)
                    self.assertEqual(("morph", 3), (option["cast_method"], option["requirements"]["GENERIC"]))
                    self.cast_result(session, card)
                    self.assertIsNone(self.resolve(session))
                    current = session.state.cards[card.object_id]
                    self.assertEqual(("battlefield", True), (current.zone, current.face_down))
                    self.assertFalse(any(session.state.players["A"].mana_pool.values()))
                    self.assertEqual([40, 40, 40], [session.state.players[seat].life for seat in "BCD"])
                self.replay(session, load=True)

    def test_etali_offers_free_prototype_and_rejects_alternate_face_down_cost(self):
        from quorune.record import authoritative_state_hash

        session = self.session(6082807)
        etali = self.add(session.engine, "Etali, Primal Storm")
        etali.acquired_control_turn_count = -1
        prototype = self.add(session.engine, "Blitz Automaton", zone="library", ref="etali-prototype")
        morph = self.add(session.engine, "Zoetic Cavern", seat="B", zone="library", ref="etali-morph")
        for seat in "CD":
            self.add(session.engine, "Generic Bound Plains", seat=seat, zone="library", ref=f"etali-method-{seat}")
        self.assert_closed(session, prototype)
        self.prepare(session, mana={"C": 3})
        self.checkpoint(session)
        self.attack(session, [(etali, "D")])
        self.assertIsNotNone(self.resolve(session))
        options = self.options(session)
        self.assertEqual({(prototype.ref, None), (prototype.ref, "prototype")},
                         {(row["card"], row["cast_method"]) for row in options})
        chosen, = [row for row in options if row["cast_method"] == "prototype"]
        self.assertFalse(any(chosen["requirements"].values()))
        before = authoritative_state_hash(session.state)
        wrong = session.act("pilot:A", {"action_id": "choose", "choice": "cast",
            "cast": {"cost_option": chosen["id"], "cast_method": "morph"}})
        self.assertFalse(wrong.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        cast = session.act("pilot:A", {"action_id": "choose", "choice": "cast", "cast": {"cost_option": chosen["id"]}})
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual("prototype", session.state.stack[-1].context["cast_method"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("battlefield", session.state.cards[prototype.object_id].zone)
        self.assertEqual("exile", session.state.cards[morph.object_id].zone)
        self.replay(session, load=True)

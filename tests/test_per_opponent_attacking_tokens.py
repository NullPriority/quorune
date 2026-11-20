"""CR508.4: a specified opponent restricts each token's destination."""
import unittest
from dataclasses import FrozenInstanceError

from quorune.attacking_opponent_model import AttackingOpponentScope, OpponentAttackDestination


class AttackingOpponentScopeTests(unittest.TestCase):
    def test_player_only_and_same_opponent_planeswalker_scopes_stay_distinct_and_exclude_battles(self):
        player = AttackingOpponentScope(OpponentAttackDestination.PLAYER)
        walkers = AttackingOpponentScope(OpponentAttackDestination.PLAYER_OR_PLANESWALKER)
        self.assertEqual(('player',), player.permitted_destination_kinds)
        self.assertEqual(('player', 'planeswalker'), walkers.permitted_destination_kinds)
        self.assertNotIn('battle', walkers.permitted_destination_kinds)
        self.assertNotEqual(player.to_dict(), walkers.to_dict())

    def test_serialized_scope_is_closed_and_does_not_mutate_or_alias_input(self):
        value = {'schema_version': 1, 'destination': 'opponent_player_or_planeswalker'}
        scope = AttackingOpponentScope.from_dict(value)
        value['destination'] = 'opponent_player'
        self.assertEqual('opponent_player_or_planeswalker', scope.to_dict()['destination'])
        self.assertEqual(scope, AttackingOpponentScope.from_dict(scope.to_dict()))
        with self.assertRaises(FrozenInstanceError):
            scope.destination = OpponentAttackDestination.PLAYER

    def test_unknown_roles_versions_and_open_fields_never_expand_old_destination_authority(self):
        for value in ({}, {'schema_version': 2, 'destination': 'opponent_player'},
                {'schema_version': True, 'destination': 'opponent_player'},
                {'schema_version': 1, 'destination': 'any_defender'},
                {'schema_version': 1, 'destination': 'opponent_battle'},
                {'schema_version': 1, 'destination': None},
                {'schema_version': 1, 'destination': 'opponent_player', 'allow_battle': True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                AttackingOpponentScope.from_dict(value)


class PerOpponentTokenCompilerTests(unittest.TestCase):
    @staticmethod
    def compile(text):
        from quorune.compiler.attacking_token_results import fixed_attacking_token_template
        from quorune.compiler.per_opponent_attacking_tokens import per_opponent_attacking_token_template
        from quorune.compiler.token_templates import _single_fixed_token_creation_effect_template
        return per_opponent_attacking_token_template(text,compile_attacking=lambda line:
            fixed_attacking_token_template(line,compile_fixed=_single_fixed_token_creation_effect_template))

    def test_three_printed_result_bodies_keep_token_definition_and_explicit_same_opponent_scope(self):
        cases=(
            ("For each opponent, create a 1/1 white Human creature token that's tapped and attacking that player or a planeswalker they control.",'Human','opponent_player_or_planeswalker',1),
            ("For each opponent, create a 1/1 black Ninja creature token that's tapped and attacking that player.",'Ninja','opponent_player',1),
            ("For each opponent, you create a 2/2 green Boar creature token that's tapped and attacking that opponent.",'Boar','opponent_player',2),
        )
        for text,subtype,destination,power in cases:
            with self.subTest(subtype=subtype):
                result=self.compile(text);self.assertIsNotNone(result)
                effect=result.effect
                self.assertEqual(1,effect['quantity']);self.assertTrue(effect['tapped'])
                self.assertEqual(3,effect['token_entry_version'])
                self.assertEqual(destination,effect['opponent_scope']['destination'])
                self.assertIn(subtype,effect['characteristics']['type_line'])
                self.assertEqual(str(power),str(effect['characteristics']['power']))

    def test_unknown_recipient_copy_or_followup_is_never_consumed_as_fixed_opponent_creation(self):
        base="For each opponent, create a 1/1 black Ninja creature token that's tapped and attacking that player."
        for text in (base.replace('that player.','a battle they protect.'),
                base.replace('that player.','any opponent.'),
                base+' Draw a card.',
                base.replace('a 1/1 black Ninja creature token',"a token that's a copy of this creature"),
                base.replace('For each opponent','For each player')):
            with self.subTest(text=text):
                self.assertIsNone(self.compile(text))

    def test_new_result_requires_closed_scope_and_old_versions_still_reject_its_new_field(self):
        from quorune.rules.per_opponent_attacking_tokens import per_opponent_attacking_token_closure
        from quorune.rules.attacking_token_results import attacking_token_result_closure
        result=self.compile("For each opponent, create a 1/1 black Ninja creature token that's tapped and attacking that player.")
        effect=result.effect;required,_=per_opponent_attacking_token_closure(effect)
        self.assertIn('token.creation.fixed_per_opponent_attacking_definition',required)
        for changes in ({'opponent_scope':{}},{'quantity':True},{'quantity':{'kind':'unowned'}},
                {'player':'$event.player'},{'extra':False},{'token_entry_version':True}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                per_opponent_attacking_token_closure({**effect,**changes})
        for version in (1,2):
            with self.subTest(version=version),self.assertRaises(ValueError):
                attacking_token_result_closure({**effect,'token_entry_version':version})


class PerOpponentTokenChoiceTests(unittest.TestCase):
    def effect(self, *, planeswalkers=True):
        text=("For each opponent, create a 1/1 white Human creature token that's tapped and attacking "
            + ("that player or a planeswalker they control." if planeswalkers else "that player."))
        return {**PerOpponentTokenCompilerTests.compile(text).effect,'player':'A'}

    def query(self, effect, *, actor='A', multiplier=1, attacking=True, active=('A','B','C','D')):
        from types import SimpleNamespace
        from quorune.replacement.immutable import FrozenMap
        from quorune.rules.per_opponent_attacking_tokens import per_opponent_token_inputs
        class Query:
            seats=('A','B','C','D')
            active_seats=active
            attacking_token_entry_allowed=attacking
            rows=[
                SimpleNamespace(ref='B-walker',controller='B',types=frozenset({'planeswalker'}),phased_out=False),
                SimpleNamespace(ref='C-walker',controller='C',types=frozenset({'planeswalker'}),phased_out=False),
                SimpleNamespace(ref='B-battle',controller='C',battle_protector='B',types=frozenset({'battle'}),phased_out=False),
                SimpleNamespace(ref='own-walker',controller='A',types=frozenset({'planeswalker'}),phased_out=False),
            ]
            def objects(self,**kwargs):return tuple(self.rows)
            def token_creation_preview(self):
                opponents,groups,common=per_opponent_token_inputs(self.actor,self.effect,
                    seats=self.seats,active_seats=self.active_seats,attacking=self.attacking_token_entry_allowed)
                if not self.attacking_token_entry_allowed:
                    return (FrozenMap({'quantity':len(groups)*self.multiplier}),)
                return tuple(FrozenMap({'_attacking_group':opponent,
                    'quantity':self.effect['quantity']*self.multiplier}) for opponent in opponents)
        value=Query();value.actor=actor;value.effect=effect;value.multiplier=multiplier
        return value

    def prepare(self,effect,query):
        from types import SimpleNamespace
        from quorune.semantic_choices.per_opponent_attacking_tokens import prepare_per_opponent_tokens
        query.effect=effect
        return prepare_per_opponent_tokens(effect,SimpleNamespace(actor=query.actor,
            stack_controller=query.actor,stack_ref='S1',stack_label='Opponent token trigger',query=query))

    def complete(self,preparation,response,query):
        from types import SimpleNamespace
        from quorune.semantic_choices.per_opponent_attacking_tokens import complete_per_opponent_tokens
        query.effect=preparation.continuation_effect
        return complete_per_opponent_tokens(SimpleNamespace(effect=preparation.continuation_effect,
            stack_ref='S1'),response,query)

    def test_fixed_player_recipients_are_automatic_and_create_one_batch_for_all_current_opponents(self):
        effect=self.effect(planeswalkers=False);query=self.query(effect)
        prepared=self.prepare(effect,query)
        self.assertIsNone(prepared.request);self.assertIsNotNone(prepared.auto_continue)
        intent,=prepared.preparation_intents
        self.assertEqual(3,intent.quantity);self.assertTrue(intent.tapped)
        self.assertEqual(('B','C','D'),intent.attacking_groups)
        self.assertEqual(('B','C','D'),intent.attacking_assignments)

    def test_replacement_expansion_keeps_each_group_and_only_advertises_that_opponents_player_or_planeswalker(self):
        effect=self.effect();query=self.query(effect,multiplier=2)
        prepared=self.prepare(effect,query);schema=prepared.request.choice.choice_schema()
        self.assertEqual(['B','B-walker'],schema['legal_values'])
        self.assertEqual(['opponent-token:B:0','opponent-token:B:1'],schema['legal_refs'])
        next_effect=self.complete(prepared,{'attacking':dict(zip(schema['legal_refs'],('B-walker','B')))},query).repeat_effect
        prepared=self.prepare(next_effect,query);schema=prepared.request.choice.choice_schema()
        self.assertEqual(['C','C-walker'],schema['legal_values'])
        next_effect=self.complete(prepared,{'attacking':dict(zip(schema['legal_refs'],('C','C-walker')))},query).repeat_effect
        final=self.prepare(next_effect,query);intent,=final.preparation_intents
        self.assertIsNone(final.request);self.assertEqual(3,intent.quantity)
        self.assertEqual(('B','C','D'),intent.attacking_groups)
        self.assertEqual(('B-walker','B','C','C-walker','D','D'),intent.attacking_assignments)

    def test_cross_opponent_battle_own_walker_missing_token_and_unknown_response_reject_without_creation(self):
        from quorune.semantic_choices.model import SemanticChoiceError
        effect=self.effect();query=self.query(effect);prepared=self.prepare(effect,query)
        token=prepared.request.choice.legal_refs[0];before=prepared.continuation_effect
        for response in ({'attacking':{token:'C-walker'}},{'attacking':{token:'B-battle'}},
                {'attacking':{token:'own-walker'}},{'attacking':{}},
                {'attacking':{token:'B'},'quantity':99}):
            with self.subTest(response=response),self.assertRaises(SemanticChoiceError):
                self.complete(prepared,response,query)
        self.assertEqual(before,prepared.continuation_effect)

    def test_nonattacking_copied_controller_creates_own_opponent_tokens_tapped_without_combat_assignments(self):
        effect={**self.effect(),'player':'B'};query=self.query(effect,actor='B',attacking=False)
        prepared=self.prepare(effect,query);intent,=prepared.preparation_intents
        self.assertIsNone(prepared.request);self.assertEqual(('B','B',3),(intent.actor,intent.controller,intent.quantity))
        self.assertTrue(intent.tapped);self.assertEqual((),intent.attacking_groups)
        self.assertEqual((),intent.attacking_assignments)

    def test_changed_replacement_preview_rebuilds_the_uncommitted_choice_before_any_creation(self):
        effect=self.effect();query=self.query(effect)
        prepared=self.prepare(effect,query);query.multiplier=2
        token=prepared.request.choice.legal_refs[0]
        completed=self.complete(prepared,{'attacking':{token:'B'}},query)
        self.assertFalse(completed.intents);self.assertIsNotNone(completed.repeat_effect)
        fresh=self.prepare(completed.repeat_effect,query)
        self.assertEqual(2,len(fresh.request.choice.legal_refs))

    def test_departed_original_opponent_remains_bound_to_its_known_player_without_a_new_recipient(self):
        effect=self.effect();query=self.query(effect);prepared=self.prepare(effect,query)
        query.active_seats=('A','C','D')
        token=prepared.request.choice.legal_refs[0]
        completed=self.complete(prepared,{'attacking':{token:'B'}},query)
        fresh=self.prepare(completed.repeat_effect,query)
        self.assertEqual('C',fresh.continuation_effect['_current_opponent'])
        self.assertEqual('B',fresh.continuation_effect['_assignments'][token])

    def test_initial_opponent_list_uses_current_active_players_and_no_opponents_means_no_creation(self):
        effect=self.effect(planeswalkers=False)
        query=self.query(effect,active=('A','C'))
        intent,=self.prepare(effect,query).preparation_intents
        self.assertEqual((1,('C',)),(intent.quantity,intent.attacking_groups))
        empty=self.prepare(effect,self.query(effect,active=('A',)))
        self.assertFalse(empty.preparation_intents);self.assertIsNone(empty.request)


class PerOpponentTokenLiveBridgeTests(unittest.TestCase):
    effect=PerOpponentTokenChoiceTests.effect
    query=PerOpponentTokenChoiceTests.query

    def test_ordinary_effect_compiler_routes_all_three_printed_riders_through_version_three(self):
        from quorune.oracle_ir import _reviewed_effect_template
        for text in (
            "For each opponent, create a 1/1 white Human creature token that's tapped and attacking that player or a planeswalker they control.",
            "For each opponent, create a 1/1 black Ninja creature token that's tapped and attacking that player.",
            "For each opponent, you create a 2/2 green Boar creature token that's tapped and attacking that opponent.",
        ):
            with self.subTest(text=text):
                template,effects,target,mechanics=_reviewed_effect_template(text,card_name='Generic Opponent Token Producer')
                self.assertEqual('fixed-per-opponent-attacking-token-v1',template)
                self.assertIsNone(target);self.assertEqual(1,len(effects))
                self.assertEqual(3,effects[0]['token_entry_version'])
                self.assertIn('fixed-per-opponent-attacking-token-result',mechanics)

    def test_registered_fixed_handler_dispatches_new_scope_and_keeps_old_private_field_rejections(self):
        from types import SimpleNamespace
        from quorune.replacement.immutable import FrozenMap
        from quorune.semantic_choices.fixed_attacking_tokens import FixedAttackingTokenDestinationHandler
        from quorune.semantic_choices.model import SemanticChoiceError
        effect=self.effect();query=self.query(effect)
        handler=FixedAttackingTokenDestinationHandler()
        result=handler.prepare(effect,SimpleNamespace(actor='A',stack_controller='A',stack_ref='S1',stack_label='Scoped trigger',query=query))
        self.assertEqual(('B','B-walker'),result.request.choice.legal_values)
        old={k:v for k,v in effect.items() if k!='opponent_scope'}
        old.update(token_entry_version=1,_choice_actor='A',_outside_combat=True,_opponents=('B',))
        with self.assertRaisesRegex(SemanticChoiceError,'Version-one/two'):
            handler.complete(SimpleNamespace(effect=FrozenMap(old),stack_ref='S1'),{},self.query(old,attacking=False))

    def test_engine_preview_uses_current_or_frozen_opponent_groups_in_the_canonical_creation_port(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import quorune.semantic_choices.token_creation_queries as owner
        effect=self.effect(planeswalkers=False)
        host=SimpleNamespace(seats=('A','B','C','D'),active_seats=('A','B','C','D'),
            state=SimpleNamespace(phase='combat',active_player='A'))
        rows=({'_attacking_group':'B','quantity':2},{'_attacking_group':'C','quantity':2},{'_attacking_group':'D','quantity':2})
        with patch.object(owner,'preview_token_creation',return_value=SimpleNamespace(specs=rows)) as preview:
            result=owner.semantic_choice_token_creation_preview(host,'A',effect)
        self.assertEqual(3,len(result));self.assertEqual(('B','C','D'),preview.call_args.kwargs['attacking_groups'])
        self.assertEqual(3,preview.call_args.kwargs['quantity'])
        self.assertNotIn('copy_of',preview.call_args.kwargs)
        host.active_seats=('A','C','D')
        with patch.object(owner,'preview_token_creation',return_value=SimpleNamespace(specs=rows)) as preview:
            owner.semantic_choice_token_creation_preview(host,'A',{**effect,'_opponents':('B','C','D')})
        self.assertEqual(('B','C','D'),preview.call_args.kwargs['attacking_groups'])

    def test_current_ability_controller_is_required_before_any_token_preview_or_creation(self):
        from types import SimpleNamespace
        from quorune.semantic_choices.model import SemanticChoiceError
        from quorune.semantic_choices.per_opponent_attacking_tokens import prepare_per_opponent_tokens
        effect=self.effect();query=self.query(effect)
        with self.assertRaisesRegex(SemanticChoiceError,'current ability controller'):
            prepare_per_opponent_tokens(effect,SimpleNamespace(actor='A',stack_controller='B',stack_ref='S1',stack_label='Wrong actor',query=query))


from inherited_composition_fixture import InheritedNativeFixture


class PerOpponentTokenNativeFixture(InheritedNativeFixture):
    @classmethod
    def setUpClass(cls):
        import json,tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();root=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[row for row in base['cards'] if row['name']=='Generic Bound Plains'];base['rulings']=[]
        path=root/'base.json';path.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([path,ROOT/'tests/fixtures/per-opponent-attacking-token-cards.json'],root/'opponent-tokens.sqlite3')
        cls.db=CardDatabase(root/'opponent-tokens.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Per-opponent tokens',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    @staticmethod
    def created_tokens(session,subtype):
        return [card for card in session.state.cards.values()
            if card.zone=='battlefield' and card.controller=='A' and card.object_kind=='token'
            and subtype.casefold() in {str(value).casefold() for value in session.engine._type_parts(
                str(session.engine._effective_card_data(card).get('type_line') or ''))[1]}]


class PerOpponentTokenNativeTests(PerOpponentTokenNativeFixture,unittest.TestCase):
    def test_complete_three_originals_bind_normally_with_all_printed_material_siblings(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.semantics import SemanticRegistry
        for name in ('Adeline, Resplendent Cathar','Endless Foot Assault','Stampede Surfer'):
            card=self.db.lookup(name)
            ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            program=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
            result=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
            self.assertTrue(result['strict_capability_ready'],(name,result['blockers']))

    def test_actual_paid_adeline_triggers_once_per_declaration_and_selects_only_each_opponents_player_or_planeswalker_privately_and_replays(self):
        import json
        from collections import Counter
        from quorune.record import authoritative_state_hash
        for source_attacks in (False,True):
            with self.subTest(source_attacks=source_attacks):
                s=self.session(508420100+source_attacks)
                source=self.add(s.engine,'Adeline, Resplendent Cathar',zone='hand')
                bear=self.add(s.engine,'Grizzly Bears');self.add(s.engine,'Fervor')
                bpw=self.add(s.engine,'Jace Beleren',seat='B',ref='opponent-b-jace');bpw.counters['loyalty']=3
                cpw=self.add(s.engine,'Jace Beleren',seat='C',ref='opponent-c-jace');cpw.counters['loyalty']=3
                private=self.add(s.engine,'Counterspell',seat='D',zone='hand',ref='opponent-token-private')
                for card in (source,bpw,cpw):self.assert_closed(s,card)
                self.prepare(s,mana={'C':1,'W':2});self.checkpoint(s)
                self.cast_for(s,'A',source);self.assertIsNone(self.resolve(s))
                self.assertFalse(any(s.state.players['A'].mana_pool.values()))
                attackers=[(bear,'C')]+([(source,'B')] if source_attacks else [])
                self.attack(s,attackers);self.assertEqual(1,self.trigger_count(s,source))
                self.assertFalse(s.state.cards[source.object_id].tapped)
                self.assertEqual('semantic.choice',self.resolve(s).kind)
                decision=s.packet('pilot:A',full=True)['decision'];schema=decision['legal_actions'][0]['choice_schema']
                self.assertEqual({'B',bpw.ref},set(schema['legal_values']))
                before=authoritative_state_hash(s.state)
                wrong=s.act('pilot:A',{'action_id':'choose','attacking':{key:cpw.ref for key in schema['legal_refs']}})
                self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(s.state))
                wrong=s.act('pilot:C',{'action_id':'choose','attacking':{key:'B' for key in schema['legal_refs']}})
                self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(s.state))
                s=self.replay(s,load=True)
                result=s.act('pilot:A',{'action_id':'choose','attacking':{key:bpw.ref for key in schema['legal_refs']}})
                self.assertTrue(result.ok,result.summary)
                decision=s.packet('pilot:A',full=True)['decision'];schema=decision['legal_actions'][0]['choice_schema']
                self.assertEqual({'C',cpw.ref},set(schema['legal_values']))
                result=s.act('pilot:A',{'action_id':'choose','attacking':{key:'C' for key in schema['legal_refs']}})
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                tokens=self.created_tokens(s,'Human');self.assertEqual(3,len(tokens));self.assertTrue(all(card.tapped for card in tokens))
                self.assertEqual(Counter((bpw.ref,'C','D')),Counter(s.state.combat.attackers[card.object_id] for card in tokens))
                self.assertEqual(5,s.engine._numeric_stat(source.object_id,'power'))
                self.assertEqual(0,self.trigger_count(s,source))
                for viewer in 'ABC':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
                self.replay(s,load=True)

    def test_actual_paid_ninja_enchantment_and_hybrid_boar_source_keep_one_trigger_and_replacement_expanded_opponent_groups(self):
        from collections import Counter
        cases=(('Endless Foot Assault',{'C':2,'W':1},'Ninja',False),
            ('Stampede Surfer',{'C':3,'R':2},'Boar',True))
        for index,(name,mana,token_name,self_attack) in enumerate(cases):
            with self.subTest(name=name):
                s=self.session(508420110+index);source=self.add(s.engine,name,zone='hand')
                bears=[self.add(s.engine,'Grizzly Bears',ref='opponent-bear-'+str(i)) for i in range(2)]
                self.add(s.engine,'Fervor');doubler=self.add(s.engine,'Anointed Procession')
                self.assert_closed(s,source);self.assert_closed(s,doubler)
                self.prepare(s,mana=mana);self.checkpoint(s);self.cast_for(s,'A',source)
                self.assertIsNone(self.resolve(s));self.assertFalse(any(s.state.players['A'].mana_pool.values()))
                if self_attack:
                    self.assertIn('haste',s.engine._combat_keywords(s.state.cards[source.object_id]))
                assignments=[(bears[0],'B'),(bears[1],'C')]+([(source,'C')] if self_attack else [])
                self.attack(s,assignments);self.assertEqual(1,self.trigger_count(s,source))
                self.assertIsNone(self.resolve(s))
                tokens=self.created_tokens(s,token_name)
                self.assertEqual(6,len(tokens));self.assertTrue(all(card.tapped for card in tokens))
                self.assertEqual(Counter({'B':2,'C':2,'D':2}),Counter(s.state.combat.attackers[card.object_id] for card in tokens))
                self.assertFalse(s.state.stack);self.replay(s,load=True)

    def test_existing_fixed_token_copy_for_nonattacking_controller_creates_tapped_nonattacking_cats_and_replays(self):
        s=self.session(508420120);source=self.add(s.engine,'Leonin Warleader',zone='hand')
        self.add(s.engine,'Fervor');self.assert_closed(s,source)
        self.prepare(s,mana={'C':2,'W':2});self.checkpoint(s)
        self.cast_for(s,'A',source);self.assertIsNone(self.resolve(s))
        self.attack(s,[(source,'C')])
        original=next(item for item in s.state.stack if item.kind=='triggered_ability' and item.source_object_id==source.object_id)
        # Canonical copy boundary; no unsupported opponent-copy utility is admitted.
        s.engine._copy_stack_item(controller='B',target=original,targets=[],target_groups={},
            reason='Existing fixed token nonattacking-controller interaction')
        self.checkpoint(s)
        decision=self.resolve(s)
        cats=[card for card in s.state.cards.values() if card.zone=='battlefield'
            and card.controller=='B' and card.object_kind=='token' and 'cat' in {str(value).casefold() for value in s.engine._type_parts(str(s.engine._effective_card_data(card).get('type_line') or ''))[1]}]
        self.assertEqual(2,len(cats))
        self.assertTrue(all(card.tapped and card.object_id not in s.state.combat.attackers for card in cats))
        self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        packet=s.packet('pilot:A',full=True)['decision'];schema=packet['legal_actions'][0]['choice_schema']
        result=s.act('pilot:A',{'action_id':'choose','attacking':{key:'C' for key in schema['legal_refs']}})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,len(self.created_tokens(s,'Cat')))
        self.replay(s,load=True)



    def test_actual_paid_squad_copy_keeps_its_own_attack_trigger_and_original_token_clause(self):
        from collections import Counter
        s=self.session(508420130);source=self.add(s.engine,'Endless Foot Assault',zone='hand')
        bear=self.add(s.engine,'Grizzly Bears');self.add(s.engine,'Fervor')
        self.assert_closed(s,source);self.prepare(s,mana={'C':3,'W':2});self.checkpoint(s)
        self.priority(s,'A')
        action=next(row for row in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row.get('card')==source.ref and row['id'].startswith('cast:'))
        option=next(row for row in action['cost_options'] if '+squad:' in row['id'])
        result=s.act('pilot:A',{'action_id':action['id'],'cost_option':option['id'],'squad_count':1,'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertFalse(any(s.state.players['A'].mana_pool.values()))
        self.assertIsNone(self.resolve(s))
        copies=[card for card in s.state.cards.values() if card.zone=='battlefield'
            and card.controller=='A' and card.object_kind=='token'
            and card.printed_name=='Endless Foot Assault']
        self.assertEqual(1,len(copies))
        self.attack(s,[(bear,'B')])
        self.assertEqual(1,self.trigger_count(s,source));self.assertEqual(1,self.trigger_count(s,copies[0]))
        self.assertIsNone(self.resolve(s))
        tokens=self.created_tokens(s,'Ninja')
        self.assertEqual(6,len(tokens))
        self.assertEqual(Counter({'B':2,'C':2,'D':2}),Counter(s.state.combat.attackers[card.object_id] for card in tokens))
        self.replay(s,load=True)

    def test_unsupported_definition_replacement_control_is_not_forced_or_counted(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.semantics import SemanticRegistry
        card=self.db.lookup('Divine Visitation')
        program=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),
            capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(binding['strict_capability_ready'])
        self.assertIn('trust_basis:unresolved',binding['blockers'])

    def test_actual_token_creation_fault_restores_the_entire_batch_and_pending_choice_then_retries(self):
        from unittest.mock import patch
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        import quorune.semantic_choices.intent_host as owner
        s=self.session(508420140);source=self.add(s.engine,'Adeline, Resplendent Cathar',zone='hand')
        bear=self.add(s.engine,'Grizzly Bears');self.add(s.engine,'Fervor')
        for seat in ('B','C'):
            pw=self.add(s.engine,'Jace Beleren',seat=seat,ref='rollback-jace-'+seat)
            pw.counters['loyalty']=3
        self.prepare(s,mana={'C':1,'W':2});self.checkpoint(s)
        self.cast_for(s,'A',source);self.assertIsNone(self.resolve(s))
        self.attack(s,[(bear,'B')]);self.assertEqual('semantic.choice',self.resolve(s).kind)
        schema=s.packet('pilot:A',full=True)['decision']['legal_actions'][0]['choice_schema']
        result=s.act('pilot:A',{'action_id':'choose','attacking':{key:'B' for key in schema['legal_refs']}})
        self.assertTrue(result.ok,result.summary)
        schema=s.packet('pilot:A',full=True)['decision']['legal_actions'][0]['choice_schema']
        command={'action_id':'choose','attacking':{key:'C' for key in schema['legal_refs']}}
        before=authoritative_state_hash(s.state);original=owner.execute_token_creation_intent;observed=[]
        def fault(host,intent):
            result=original(host,intent);observed.append(len(result))
            raise GameRuleError('Injected complete opponent token batch fault')
        with patch.object(owner,'execute_token_creation_intent',side_effect=fault):
            result=s.act('pilot:A',command)
        self.assertFalse(result.ok,result.summary);self.assertIn('Injected complete opponent token batch fault',result.summary)
        self.assertEqual([3],observed);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertFalse(self.created_tokens(s,'Human'))
        s=self.replay(s,load=True)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(3,len(self.created_tokens(s,'Human')))
        self.replay(s,load=True)



    def test_actual_countered_or_bounced_enchantment_keeps_correct_queued_opponent_token_behavior_and_replays(self):
        from collections import Counter
        for countered in (False,True):
            with self.subTest(countered=countered):
                s=self.session(508420150+countered)
                source=self.add(s.engine,'Endless Foot Assault',zone='hand')
                bear=self.add(s.engine,'Grizzly Bears');self.add(s.engine,'Fervor')
                response=self.add(s.engine,'Stifle' if countered else 'Boomerang',seat='B',zone='hand')
                self.assert_closed(s,response)
                for i in range(1 if countered else 2):
                    self.add(s.engine,'Island',seat='B',ref='opponent-response-island-'+str(i))
                self.prepare(s,mana={'C':2,'W':1});self.checkpoint(s)
                self.cast_for(s,'A',source);self.assertIsNone(self.resolve(s));self.attack(s,[(bear,'C')])
                ability=next(item for item in s.state.stack if item.kind=='triggered_ability' and item.source_object_id==source.object_id)
                self.cast_for(s,'B',response,[ability.ref if countered else source.ref])
                self.resolve_top(s);self.assertIsNone(self.resolve(s))
                tokens=self.created_tokens(s,'Ninja')
                self.assertEqual(0 if countered else 3,len(tokens))
                self.assertEqual('battlefield' if countered else 'hand',s.state.cards[source.object_id].zone)
                if not countered:
                    self.assertEqual(Counter(('B','C','D')),Counter(s.state.combat.attackers[card.object_id] for card in tokens))
                self.replay(s,load=True)

    def test_actual_conceded_opponent_before_resolution_is_not_replaced_by_another_token_recipient(self):
        from collections import Counter
        s=self.session(508420160);source=self.add(s.engine,'Endless Foot Assault',zone='hand')
        bear=self.add(s.engine,'Grizzly Bears');self.add(s.engine,'Fervor')
        self.prepare(s,mana={'C':2,'W':1});self.checkpoint(s);self.cast_for(s,'A',source)
        self.assertIsNone(self.resolve(s));self.attack(s,[(bear,'C')]);self.priority(s,'B')
        result=s.act('pilot:B',{'action_id':'concede','choices':{'confirm_concede':True}})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        tokens=self.created_tokens(s,'Ninja');self.assertEqual(2,len(tokens))
        self.assertEqual(Counter(('C','D')),Counter(s.state.combat.attackers[card.object_id] for card in tokens))
        self.replay(s,load=True)

    def test_fresh_native_cross_opponent_destination_mutant_is_killed_after_real_paid_adeline(self):
        from unittest.mock import patch
        import quorune.semantic_choices.per_opponent_attacking_tokens as owner
        name='test_actual_paid_adeline_triggers_once_per_declaration_and_selects_only_each_opponents_player_or_planeswalker_privately_and_replays'
        positive=unittest.TestResult();type(self)(name).run(positive)
        self.assertTrue(positive.wasSuccessful(),(positive.failures,positive.errors))
        def wrong(query,scope,opponent):
            return (opponent,*(row.ref for row in query.objects(zones=('battlefield',)) if 'planeswalker' in row.types))
        mutant=unittest.TestResult()
        with patch.object(owner,'_destinations',side_effect=wrong):
            type(self)(name).run(mutant)
        self.assertEqual([],mutant.errors);self.assertGreaterEqual(len(mutant.failures),1)



    def test_canonical_opponent_copy_of_new_token_trigger_creates_its_own_tapped_nonattacking_batch_and_replays(self):
        s=self.session(508420180);source=self.add(s.engine,'Endless Foot Assault',zone='hand')
        bear=self.add(s.engine,'Grizzly Bears');self.add(s.engine,'Fervor')
        self.prepare(s,mana={'C':2,'W':1});self.checkpoint(s)
        self.cast_for(s,'A',source);self.assertIsNone(self.resolve(s));self.attack(s,[(bear,'C')])
        original=next(item for item in s.state.stack if item.kind=='triggered_ability' and item.source_object_id==source.object_id)
        s.engine._copy_stack_item(controller='B',target=original,targets=[],target_groups={},
            reason='Version-three per-opponent token controller copy boundary')
        self.checkpoint(s);self.assertIsNone(self.resolve(s))
        tokens=[card for card in s.state.cards.values() if card.zone=='battlefield'
            and card.controller=='B' and card.object_kind=='token' and 'ninja' in {str(value).casefold()
                for value in s.engine._type_parts(str(s.engine._effective_card_data(card).get('type_line') or ''))[1]}]
        self.assertEqual(3,len(tokens))
        self.assertTrue(all(card.tapped and card.object_id not in s.state.combat.attackers for card in tokens))
        self.assertEqual(3,len(self.created_tokens(s,'Ninja')))
        self.replay(s,load=True)


class PerOpponentTokenAssuranceTests(unittest.TestCase):
    effect=PerOpponentTokenChoiceTests.effect
    query=PerOpponentTokenChoiceTests.query
    prepare=PerOpponentTokenChoiceTests.prepare
    complete=PerOpponentTokenChoiceTests.complete

    def test_raw_payload_minimum_owns_the_new_scope_without_mechanic_or_coverage_annotations(self):
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        effect={**self.effect(),'player':'$controller'}
        required=typed_result_amount_runtime_dependencies(effect)
        self.assertIn('token.creation.fixed_per_opponent_attacking_definition',required)
        self.assertIn('token.creation.fixed_attacking_definition',required)
        for changes in ({'opponent_scope':{}},{'token_entry_version':1},{'_opponents':['B']}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                typed_result_amount_runtime_dependencies({**effect,**changes})

    def test_fresh_other_opponent_planeswalker_mutant_is_killed_after_positive_bound_choice(self):
        from unittest.mock import patch
        import quorune.semantic_choices.per_opponent_attacking_tokens as owner
        name='test_replacement_expansion_keeps_each_group_and_only_advertises_that_opponents_player_or_planeswalker'
        positive=unittest.TestResult();PerOpponentTokenChoiceTests(name).run(positive)
        self.assertTrue(positive.wasSuccessful(),(positive.failures,positive.errors))
        def wrong(query,scope,opponent):
            return (opponent,*(row.ref for row in query.objects(zones=('battlefield',)) if 'planeswalker' in row.types))
        mutant=unittest.TestResult()
        with patch.object(owner,'_destinations',side_effect=wrong):
            PerOpponentTokenChoiceTests(name).run(mutant)
        self.assertEqual([],mutant.errors);self.assertEqual(1,len(mutant.failures))

    def test_typed_continuation_round_trip_keeps_original_groups_and_partial_assignments(self):
        import json
        from quorune.semantic_choices.model import SemanticChoiceFrame,SemanticChoiceContinuation
        from quorune.semantic_choices.defaults import default_semantic_choice_registry
        from quorune.semantic_choices.intent_replacement import semantic_intent_identity,validate_semantic_intent_identity
        effect=self.effect();query=self.query(effect);prepared=self.prepare(effect,query)
        continuation=SemanticChoiceContinuation(handler_id='choice.token.fixed-attacking-destinations.v1',
            handler_version=1,stack_ref='S1',effect=prepared.continuation_effect,remaining=(),
            destination=None,note='',semantic_frame=SemanticChoiceFrame('fixture:opponent-token',1,'S1',0,'A'))
        encoded=json.loads(json.dumps(continuation.to_dict()))
        handler,restored=default_semantic_choice_registry().decode_continuation(encoded)
        self.assertEqual(encoded,restored.to_dict());query.effect=restored.effect
        token=prepared.request.choice.legal_refs[0]
        completed=handler.complete(restored,{'attacking':{token:'B-walker'}},query)
        second=self.prepare(completed.repeat_effect,query)
        self.assertEqual('B-walker',second.continuation_effect['_assignments'][token])
        keys=second.request.choice.legal_refs
        completed=self.complete(second,{'attacking':{key:'C' for key in keys}},query)
        final=self.prepare(completed.repeat_effect,query);intent,=final.preparation_intents
        kind,identity=semantic_intent_identity(intent)
        self.assertEqual(identity,validate_semantic_intent_identity(kind,identity))
        self.assertEqual(('B-walker','C','D'),intent.attacking_assignments)

    def test_generic_resolution_json_thaw_preserves_finished_opponent_assignments(self):
        from quorune.replacement.immutable import FrozenMap,thaw_value
        effect=self.effect();query=self.query(effect)
        base=query.token_creation_preview
        query.token_creation_preview=lambda:tuple(FrozenMap({**row,
            'characteristics':{'colors':['W']},'temporary_keywords':[]}) for row in base())
        first=self.prepare(effect,query);token=first.request.choice.legal_refs[0]
        completed=self.complete(first,{'attacking':{token:'B-walker'}},query)
        thawed=thaw_value(completed.repeat_effect)
        self.assertIsInstance(thawed['_preview_specs'][0]['characteristics']['colors'],list)
        next_choice=self.prepare(thawed,query)
        self.assertEqual(('C','C-walker'),next_choice.request.choice.legal_values)
        self.assertEqual('B-walker',next_choice.continuation_effect['_assignments'][token])

    def test_canonical_replacement_definition_preview_retains_each_original_group_without_utility_card_admission(self):
        from quorune.replacement.immutable import FrozenMap
        from quorune.semantic_choices.token_creation_intents import execute_token_creation_intent
        from types import SimpleNamespace
        effect=self.effect();query=self.query(effect)
        original=query.token_creation_preview
        query.token_creation_preview=lambda:tuple(FrozenMap({**row,'name':'Angel Token',
            'characteristics':{'type_line':'Token Creature — Angel','power':'4','toughness':'4','keywords':['Flying','Vigilance']}})
            for row in original())
        first=self.prepare(effect,query);keys=first.request.choice.legal_refs
        completed=self.complete(first,{'attacking':{key:'B-walker' for key in keys}},query)
        second=self.prepare(completed.repeat_effect,query);keys=second.request.choice.legal_refs
        completed=self.complete(second,{'attacking':{key:'C' for key in keys}},query)
        final=self.prepare(completed.repeat_effect,query);intent,=final.preparation_intents
        captured={}
        def create(controller,**kwargs):
            captured.update(controller=controller,**kwargs)
            return ('new-token-1','new-token-2','new-token-3')
        created=execute_token_creation_intent(SimpleNamespace(create_token=create),intent)
        self.assertEqual(3,len(created));self.assertEqual(('B','C','D'),captured['attacking_groups'])
        self.assertEqual(('B-walker','C','D'),captured['attacking'])
        self.assertEqual(effect['name'],captured['name'])
        self.assertNotIn('Angel',captured['characteristics']['type_line'])
        # The mutation owner reapplies the issued replacements to the original
        # definition once; the preview must not become a pre-replaced input.


class PerOpponentTokenRegistrationTests(PerOpponentTokenNativeFixture,unittest.TestCase):
    def test_runtime_minimum_rejects_stripped_fine_owner_even_without_new_coverage(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(508420170);source=self.add(s.engine,'Endless Foot Assault',zone='hand')
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
            if any(e.get('token_entry_version')==3 for e in p.effects))
        cap='token.creation.fixed_per_opponent_attacking_definition'
        self.assertIn(cap,program.capability_dependencies)
        dependencies=tuple(c for c in program.capability_dependencies if c!=cap)
        changed=replace(program,capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict(),
            coverage=[c for c in program.coverage if c!='fixed-per-opponent-attacking-token-result'])
        binding=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(binding['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:'+cap,binding['blockers'])

    def test_genuinely_verified_fine_and_base_withdrawals_block_full_originals_and_preserve_independent_controls(self):
        import json
        from copy import deepcopy
        from common import ROOT
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        for cap in ('token.creation.fixed_per_opponent_attacking_definition','token.creation.fixed_attacking_definition'):
            data=deepcopy(raw)
            next(row for row in data['capabilities'] if row['id']==cap).update(status='blocked',blockers=['Withdrawn opponent token authority'])
            registry=CapabilityRegistry(data)
            index=build_index(registry_value=data,declaration_source=declarations,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in ('Adeline, Resplendent Cathar','Endless Foot Assault','Stampede Surfer'):
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status,(cap,name))
                blockers=[b for residual in ir.material_residuals for b in residual.blockers]
                self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers)
                self.assertFalse(any('evidence_index:unverified' in b for b in blockers),blockers)
            ordinary=compile_oracle_card(self.db.lookup('Grizzly Bears'),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ordinary.status,ordinary.material_residuals)
            old=compile_oracle_card(self.db.lookup('Leonin Warleader'),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual(cap=='token.creation.fixed_per_opponent_attacking_definition',old.status=='exact')

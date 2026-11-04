"""Existing resolution-locked recipients compose with independently owned amounts."""
import unittest
from quorune.oracle_ir import _reviewed_effect_template


class PublicSetQuantityCompilerTests(unittest.TestCase):
    def test_public_counts_keep_one_set_result_and_independent_keyword(self):
        for text,coefficient,keyword in (
                ('All creatures get -1/-1 until end of turn for each Swamp you control.',-1,None),
                ('Until end of turn, creatures you control gain trample and get +1/+1 for each basic land type among lands you control.',1,'Trample'),
                ('Other creatures you control get +2/+2 until end of turn for each creature card in your graveyard.',2,None)):
            with self.subTest(text=text):
                template,effects,target,mechanics=_reviewed_effect_template(text,card_name='Quantity Set Fixture')
                self.assertIsNotNone(template);self.assertIsNone(target);self.assertEqual(1,len(effects))
                effect=effects[0];self.assertEqual('modify_all_matching_permanents_until_end_of_turn',effect['op'])
                for field in ('power','toughness'):
                    self.assertEqual('public_query_effect_amount',effect[field]['kind'])
                    self.assertEqual(coefficient,effect[field]['coefficient'])
                self.assertEqual([] if keyword is None else [keyword],effect.get('keywords',[]))

    def test_source_counter_results_keep_scalar_origin_and_each_field_coefficient(self):
        for text,power,toughness in (
                ('Until end of turn, other Elves you control gain vigilance and get +1/+1 for each +1/+1 counter on Quantity Set Fixture.',1,1),
                ('Target creature gets +2/-1 until end of turn for each lore counter on this artifact.',2,-1)):
            with self.subTest(text=text):
                template,effects,target,mechanics=_reviewed_effect_template(text,card_name='Quantity Set Fixture')
                self.assertIsNotNone(template)
                effect=next(row for row in effects if isinstance(row.get('power'),dict))
                for field,coefficient in (('power',power),('toughness',toughness)):
                    self.assertEqual('scalar_effect_amount',effect[field]['kind'])
                    self.assertEqual('source_characteristic',effect[field]['origin'])
                    self.assertEqual(coefficient,effect[field]['coefficient'])
                self.assertIn('scalar-effect-amount',mechanics)

    def test_unrepresented_counter_references_and_public_sets_stay_closed(self):
        for text in (
                'All creatures get -1/-1 until end of turn for each secret you remember.',
                'All creatures get -1/-1 until end of turn for each Swamp you control if you won the game.',
                'Until end of turn, creatures you control gain rewrite history and get +1/+1 for each land you control.',
                'Other creatures you control get +1/+1 until end of turn for each lore counter on that creature.',
                'All creatures get +1/+1 until your next turn for each land you control.'):
            with self.subTest(text=text):self.assertIsNone(_reviewed_effect_template(text,card_name='Quantity Set Fixture')[0])

    def test_source_counter_binding_distinguishes_coefficients_and_retains_zero_field(self):
        def result(stats):
            text=f'Target creature gets {stats} until end of turn for each lore counter on this artifact.'
            return _reviewed_effect_template(text,card_name='Quantity Set Fixture')[1][0]
        single=result('+1/+0');double=result('+2/+0')
        self.assertEqual(0,single['toughness']);self.assertEqual(0,double['toughness'])
        self.assertEqual(1,single['power']['coefficient']);self.assertEqual(2,double['power']['coefficient'])
        self.assertNotEqual(single['power']['binding_id'],double['power']['binding_id'])

    def test_existing_declared_source_counter_definition_keeps_its_owner_and_terminates(self):
        template,effects,target,mechanics=_reviewed_effect_template(
            'Target creature gets +X/+X until end of turn, where X is the number of lore counters on Fixture for each.',
            card_name='Fixture for each')
        self.assertIsNotNone(template);self.assertEqual('lore',effects[0]['power']['counter_name'])
        self.assertEqual('scalar_effect_amount',effects[0]['power']['kind'])

    def test_existing_declared_global_keeps_one_instruction_scoped_amount(self):
        for subject in ('Creatures you control','Each creature you control'):
            text=f'{subject} get +X/+X until end of turn, where X is the number of creatures you control.'
            template,effects,target,mechanics=_reviewed_effect_template(text,card_name='Quantity Set Fixture')
            self.assertIsNotNone(template);self.assertEqual(1,len(effects))
            effect=effects[0];self.assertEqual('modify_all_matching_permanents_until_end_of_turn',effect['op'])
            self.assertEqual(2,effect['power']['schema_version']);self.assertTrue(effect['power']['binding_id'])
            self.assertEqual(effect['power']['binding_id'],effect['toughness']['binding_id'])

    def test_amount_and_public_recipient_owners_are_independently_required(self):
        import json,copy
        from common import ROOT
        from test_public_query_effect_amounts import query_amount_record,_NoRulingsDatabase
        from quorune.card_programs import compile_card_program,bind_card_program_runtime
        from quorune.rules.capabilities import load_default_capability_registry,CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        texts=(('All creatures get -1/-1 until end of turn for each Swamp you control.',
            'quantity_expression.public_query_effect_amount'),
            ('All creatures get -1/-1 until end of turn for each Swamp you control.',
            'continuous.resolution.fixed_characteristics_until_end_of_turn'),
            ('Other Elves you control get +1/+1 until end of turn for each lore counter on this artifact.',
            'quantity_expression.scalar_effect_amount'))
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values());registry=load_default_capability_registry()
        for index,(text,cap) in enumerate(texts):
            with self.subTest(capability=cap):
                record=query_amount_record(text,suffix=365211200+index)
                program=compile_card_program(_NoRulingsDatabase(),record,capability_registry=registry,
                    capability_profile='commander_review',trust_level='trusted')
                self.assertTrue(bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')['strict_capability_ready'])
                changed=copy.deepcopy(raw);next(row for row in changed['capabilities'] if row['id']==cap).update(status='blocked',blockers=['owner withdrawn'])
                blocked=CapabilityRegistry(changed)
                evidence=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=known)
                blocked.mark_evidence_verified(validate_capability_evidence_index(evidence,registry=blocked))
                provisional=compile_card_program(_NoRulingsDatabase(),record,capability_registry=blocked,
                    capability_profile='commander_review',trust_level='provisional')
                self.assertFalse(bind_card_program_runtime(provisional,capability_registry=blocked,profile='commander_review')['strict_capability_ready'])

    def test_dropping_set_projection_source_counter_adapter_or_coefficient_is_killed(self):
        from unittest.mock import patch
        from quorune.compiler import public_query_effect_amounts as public
        from quorune.compiler import source_counter_modifiers as scalar
        def killed(method,changes):
            case=type(self)(method);baseline=unittest.TestResult();case.run(baseline)
            self.assertTrue(baseline.wasSuccessful())
            with changes:
                result=unittest.TestResult();case.run(result)
            self.assertFalse(result.wasSuccessful());self.assertFalse(result.errors)
        killed('test_public_counts_keep_one_set_result_and_independent_keyword',patch.object(public,'_CHARACTERISTIC_RESULT_OPERATIONS',
            public._CHARACTERISTIC_RESULT_OPERATIONS-{'modify_all_matching_permanents_until_end_of_turn'}))
        killed('test_source_counter_results_keep_scalar_origin_and_each_field_coefficient',patch.object(scalar,'source_counter_modifier_template',return_value=None))
        original=scalar.replace
        killed('test_source_counter_results_keep_scalar_origin_and_each_field_coefficient',patch.object(scalar,'replace',
            side_effect=lambda spec,**changes:original(spec,**{**changes,'coefficient':spec.coefficient})))


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class PublicSetQuantityNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'public-set-quantity.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/public-set-quantity-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Public set quantities',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def stats(self,s,card):
        return tuple(s.engine._numeric_stat(card.object_id,field) for field in ('power','toughness'))

    def keywords(self,s,card):return {word.casefold() for word in s.engine._combat_keywords(s.state.cards[card.object_id])}

    def load_pending(self,s):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'quantity-pending';s.save(path);loaded=type(s).load(self.db,path)
            self.assertEqual(authoritative_state_hash(s.state),authoritative_state_hash(loaded.state));return loaded

    def trim(self,s):
        for oid in list(s.state.players['A'].zones['hand']):s.engine.move_card(oid,'library',log=False)

    def test_actual_tromp_locks_resolved_quantity_recipients_and_keywords_through_combat_and_cleanup(self):
        s=self.session(111400401);self.trim(s)
        bear=self.add(s.engine,'Grizzly Bears');elf=self.add(s.engine,'Llanowar Elves')
        blocker=self.add(s.engine,'Grizzly Bears',seat='B',ref='tromp-blocker')
        self.add(s.engine,'Forest');island=self.add(s.engine,'Island')
        tromp=self.add(s.engine,'Tromp the Domains',zone='hand');later=self.add(s.engine,'Raise the Alarm',zone='hand')
        bounce=self.add(s.engine,'Boomerang',seat='B',zone='hand');private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        for card in (tromp,later,bounce):self.assert_closed(s,card)
        s.state.players['B'].mana_pool.update({'U':2});self.prepare(s,mana={'C':6,'G':1,'W':1});self.checkpoint(s)
        self.cast(s,'A',tromp);self.cast(s,'B',bounce,[island.ref]);self.resolve_top(s)
        self.assertEqual('hand',s.state.cards[island.object_id].zone);s=self.load_pending(s)
        self.assertIsNone(self.resolve(s));self.assertEqual((3,3),self.stats(s,bear));self.assertEqual((2,2),self.stats(s,elf))
        self.assertIn('trample',self.keywords(s,bear));self.assertIn('trample',self.keywords(s,elf))
        self.cast(s,'A',later);self.assertIsNone(self.resolve(s))
        tokens=[card for card in s.state.cards.values() if card.zone=='battlefield' and card.is_token and card.controller=='A']
        self.assertEqual(2,len(tokens))
        for token in tokens:self.assertEqual((1,1),self.stats(s,token));self.assertNotIn('trample',self.keywords(s,token))
        self.attack(s,[(s.state.cards[bear.object_id],'B'),(s.state.cards[elf.object_id],'C')])
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.blockers')
        accepted=s.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:bear.ref}});self.assertTrue(accepted.ok,accepted.summary)
        self.until(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='combat.damage')
        command={'action_id':'assign_damage','assignments':[{'source':bear.ref,'target':blocker.ref,'amount':2},
            {'source':bear.ref,'target':'B','amount':1},{'source':elf.ref,'target':'C','amount':2}]}
        before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        accepted=s.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
        self.until(s,lambda game:game.state.phase=='postcombat_main')
        self.assertEqual([40,39,38,40],[s.state.players[seat].life for seat in 'ABCD'])
        self.assertEqual('graveyard',s.state.cards[blocker.object_id].zone)
        self.until(s,lambda game:game.state.active_player=='B' and game.state.phase=='precombat_main')
        self.assertEqual((2,2),self.stats(s,bear));self.assertEqual((1,1),self.stats(s,elf));self.assertNotIn('trample',self.keywords(s,bear))
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s)

    def test_actual_zero_domain_keeps_keyword_grant_independent_from_numeric_zero(self):
        s=self.session(111400402);bear=self.add(s.engine,'Grizzly Bears');self.add(s.engine,'Wastes')
        tromp=self.add(s.engine,'Tromp the Domains',zone='hand');self.assert_closed(s,tromp)
        self.prepare(s,mana={'C':5,'G':1});self.checkpoint(s);self.cast(s,'A',tromp);self.assertIsNone(self.resolve(s))
        self.assertEqual((2,2),self.stats(s,bear));self.assertIn('trample',self.keywords(s,bear));self.replay(s)

    def test_actual_mutilate_simultaneous_deaths_preserve_triggers_and_later_spirit_is_not_affected(self):
        s=self.session(111400403);self.trim(s)
        bear=self.add(s.engine,'Grizzly Bears');other=self.add(s.engine,'Grizzly Bears',seat='C',ref='mutilate-other')
        traveler=self.add(s.engine,'Doomed Traveler',seat='B');celebrant=self.add(s.engine,'Cruel Celebrant',seat='B')
        survivor=self.add(s.engine,'Loxodon Smiter',seat='D')
        self.add(s.engine,'Swamp',ref='mutilate-swamp-one');self.add(s.engine,'Swamp',ref='mutilate-swamp-two')
        spell=self.add(s.engine,'Mutilate',zone='hand')
        for card in (spell,traveler,celebrant):self.assert_closed(s,card)
        self.prepare(s,mana={'C':2,'B':2});self.checkpoint(s);self.cast(s,'A',spell)
        self.until(s,lambda game:not game.state.stack and game.state.pending_decision is not None and game.state.pending_decision.kind=='priority')
        for card in (bear,other,traveler,celebrant):self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
        self.assertEqual((2,2),self.stats(s,survivor));self.assertEqual([38,42,38,38],[s.state.players[seat].life for seat in 'ABCD'])
        tokens=[card for card in s.state.cards.values() if card.is_token and card.zone=='battlefield']
        self.assertEqual(1,len(tokens));self.assertEqual('B',tokens[0].controller);self.assertEqual((1,1),self.stats(s,tokens[0]))
        self.assertIn('flying',self.keywords(s,tokens[0]));self.replay(s)

    def test_actual_haldir_and_scroll_read_source_counters_after_responses_and_keep_resolved_results(self):
        s=self.session(111400404);self.trim(s)
        haldir=self.add(s.engine,'Haldir, Lórien Lieutenant');haldir.counters['+1/+1']=1
        scroll=self.add(s.engine,'Scroll of the Masters');elf=self.add(s.engine,'Elvish Visionary');bear=self.add(s.engine,'Grizzly Bears')
        for index in range(4):self.add(s.engine,'Forest',ref=f'source-counter-forest-{index}')
        first=self.add(s.engine,'Snakeskin Veil',zone='hand',ref='source-counter-first-veil')
        last=self.add(s.engine,'Snakeskin Veil',zone='hand',ref='source-counter-last-veil')
        growth=self.add(s.engine,'Giant Growth',zone='hand');private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        for card in (haldir,scroll,first,growth):self.assert_closed(s,card)
        self.prepare(s,mana={'C':8,'G':4});self.checkpoint(s)
        def activate(card,targets=()):
            self.priority(s,'A');rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            row=next(row for row in rows if row['id'].startswith('activate:') and card.ref in row['id'])
            command={'action_id':row['id'],'targets':list(targets),'pay':'auto'};before=authoritative_state_hash(s.state)
            self.assertFalse(s.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
            accepted=s.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
        activate(haldir);self.cast(s,'A',first,[haldir.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual((2,2),self.stats(s,haldir));self.assertEqual((3,3),self.stats(s,elf));self.assertEqual((2,2),self.stats(s,bear))
        self.assertIn('vigilance',self.keywords(s,elf));self.assertEqual(1,s.state.cards[scroll.object_id].counters.get('lore'))
        activate(scroll,[bear.ref]);self.cast(s,'A',growth,[elf.ref]);self.resolve_top(s);s=self.load_pending(s)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(2,s.state.cards[scroll.object_id].counters.get('lore'));self.assertEqual((4,4),self.stats(s,bear))
        self.cast(s,'A',last,[haldir.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(3,s.state.cards[scroll.object_id].counters.get('lore'));self.assertEqual((3,3),self.stats(s,haldir))
        self.assertEqual((6,6),self.stats(s,elf));self.assertEqual((4,4),self.stats(s,bear))
        self.attack(s,[(s.state.cards[elf.object_id],'C'),(s.state.cards[bear.object_id],'B')]);self.assertFalse(s.state.cards[elf.object_id].tapped)
        self.until(s,lambda game:game.state.phase=='postcombat_main');self.assertEqual([40,36,34,40],[s.state.players[seat].life for seat in 'ABCD'])
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s)

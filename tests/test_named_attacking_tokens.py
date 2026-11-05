"""Named token data survives whole closed productions and entering-combat riders."""
import unittest
from quorune.oracle_ir import _reviewed_effect_template


class NamedAttackingTokenCompilerTests(unittest.TestCase):
    def test_named_token_grammar_case_does_not_change_captured_name_or_legacy_payload(self):
        for text in ("Create a 1/1 green Snake creature token named Koma's Coil.",
                'Create Ragavan, a legendary 2/1 red Monkey creature token.'):
            before=_reviewed_effect_template(text,card_name='Named token fixture')
            self.assertIsNotNone(before[0])
            after=_reviewed_effect_template('create'+text[len('Create'):],card_name='Named token fixture')
            self.assertEqual(before,after)
        named=_reviewed_effect_template('create a 1/1 green Snake creature token named PeArL.',card_name='Named token fixture')
        self.assertEqual('PeArL',named[1][0]['name'])

    def test_name_production_does_not_swallow_cleanup_conditions_or_unrepresented_abilities(self):
        for text in ('create a 1/1 red Goblin creature token named Boulder. Rewrite game history.',
                'create a 1/1 red Goblin creature token named Boulder with "Do an unrepresented thing."',
                "create a 2/1 colorless Construct artifact creature token with flying named Boulder that's attacking that player."):
            with self.subTest(text=text):
                template,_,_,_=_reviewed_effect_template(text,card_name='Named token fixture')
                self.assertIsNone(template)

    def test_case_sensitive_named_verb_is_killed(self):
        import re
        from unittest.mock import patch
        from quorune.compiler import fixed_token_production_templates as owner
        original=owner._FIXED_NAMED_TOKEN
        case=type(self)('test_trigger_case_and_named_attacking_definition_keep_name_and_keywords')
        baseline=unittest.TestResult();case.run(baseline);self.assertTrue(baseline.wasSuccessful())
        with patch.object(owner,'_FIXED_NAMED_TOKEN',re.compile(original.pattern)):
            mutant=unittest.TestResult();case.run(mutant)
        self.assertFalse(mutant.wasSuccessful());self.assertFalse(mutant.errors)

    def test_trigger_case_and_named_attacking_definition_keep_name_and_keywords(self):
        for verb in ('Create','create'):
            for attacking in (False,True):
                text=verb+" a 2/1 colorless Construct artifact creature token with flying named Ballistic Boulder"+(
                    " that's tapped and attacking." if attacking else '.')
                with self.subTest(text=text):
                    template,effects,target,mechanics=_reviewed_effect_template(text,card_name='Named token fixture')
                    self.assertIsNotNone(template)
                    self.assertEqual(1,len(effects));self.assertIsNone(target)
                    self.assertEqual('Ballistic Boulder',effects[0]['name'])
                    self.assertEqual('create_token' if not attacking else 'choose_fixed_attacking_token_destinations',effects[0]['op'])
                    self.assertEqual(['Flying'],effects[0]['characteristics']['keywords'])


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class NamedAttackingTokenNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'named-attacking-tokens.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/named-attacking-token-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Named attacking tokens',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def test_actual_fire_navy_preserves_name_recipient_combat_and_next_end_step_cleanup(self):
        s=self.session(111400201)
        for oid in list(s.state.players['A'].zones['hand']):s.engine.move_card(oid,'library',log=False)
        navy=self.add(s.engine,'Fire Navy Trebuchet');attacker=self.add(s.engine,'Goblin Piker')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand')
        self.assert_closed(s,navy);self.assert_closed(s,attacker)
        self.prepare(s);self.checkpoint(s);self.attack(s,[(attacker,'B')])
        decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
        ctx=decision.payload_by_actor['A'];refs=ctx['legal_actions'][0]['choice_schema']['legal_refs']
        self.assertEqual(1,len(refs))
        source=next(item.source_object_id for item in s.state.stack if item.ref==ctx['stack'])
        self.assertEqual(navy.object_id,source)
        command={'action_id':'choose','attacking':{refs[0]:'C'}};before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertFalse(s.act('pilot:A',{'action_id':'choose','attacking':{refs[0]:'A'}}).ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.settle(s)
        token=next(card for card in s.state.cards.values() if card.zone=='battlefield' and card.is_token and card.printed_name=='Ballistic Boulder')
        self.assertTrue(token.tapped);self.assertEqual('C',token.attacking)
        data=s.engine._effective_card_data(token)
        self.assertEqual(('2','1'),(str(data['power']),str(data['toughness'])))
        self.assertIn('flying',{keyword.casefold() for keyword in s.engine._combat_keywords(token)})
        cleanup=next(item for item in s.state.delayed_triggers if item.active)
        self.assertEqual(navy.object_id,cleanup.source_object_id)
        self.until(s,lambda game:game.state.phase=='postcombat_main')
        self.assertEqual([40,38,38,40],[s.state.players[seat].life for seat in 'ABCD'])
        self.assertEqual('battlefield',s.state.cards[token.object_id].zone)
        self.until(s,lambda game:game.state.phase=='ending' and game.state.step=='end_step')
        with tempfile.TemporaryDirectory() as directory:
            record=Path(directory)/'named-token-pending';s.save(record);resumed=type(s).load(self.db,record)
            self.assertEqual(authoritative_state_hash(s.state),authoritative_state_hash(resumed.state));s=resumed
        self.settle(s)
        self.assertEqual('outside',s.state.cards[token.object_id].zone)
        self.assertFalse(any(item.active for item in s.state.delayed_triggers))
        for seat in 'ABD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.replay(s)


if __name__=='__main__':unittest.main()

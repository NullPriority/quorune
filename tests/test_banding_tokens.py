"""CR 702.22: a created token's banding must drive the actual combat graph."""
from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients


class BandingTokenTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish
    tokens=recipients.TargetRecipientNativeTests.tokens

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'banding-token.sqlite3'
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[row for row in base['cards'] if row['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=Path(cls.temporary.name)/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/banding-token-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Banding tokens',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def test_complete_original_requires_token_and_banding_dependencies_and_rejects_unknown_variants(self):
        s=self.session(1002100);source=self.add(s.engine,'Errand of Duty',zone='hand')
        self.assert_closed(s,source)
        program=next(program for program in s.engine.semantics.programs_for_oracle(source.oracle_id)
            if 'token.creation.fixed_definition' in program.capability_dependencies)
        for cap in ('token.creation.fixed_definition','combat.banding'):
            deps=tuple(value for value in program.capability_dependencies if value!=cap)
            for coverage in (program.coverage,{}):
                changed=replace(program,coverage=coverage,capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])
        card=self.db.lookup('Errand of Duty',fuzzy=False)
        for word in ('bands with other Knights','banding and an unrepresented ability'):
            changed=replace(card,oracle_text=card.oracle_text.replace('with banding.','with '+word+'.'))
            self.assertNotEqual('exact',compile_oracle_card(changed,capability_registry=self.registry).status)

    def test_original_created_knight_forms_a_real_band_rejects_illegal_groups_and_chooses_blocker_damage(self):
        s=self.session(1002110);source=self.add(s.engine,'Errand of Duty',zone='hand')
        first=self.add(s.engine,'Grizzly Bears',ref='banding-token-partner')
        extra=self.add(s.engine,'Grizzly Bears',ref='banding-token-extra')
        blocker=self.add(s.engine,'Grizzly Bears',seat='B',ref='banding-token-blocker')
        self.add(s.engine,'Fervor');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='banding-token-private-card')
        s.state.config.semantic_policy='trusted_only';self.assert_closed(s,source)
        self.prepare(s,mana={'W':1,'C':1});self.checkpoint(s);self.cast(s,'A',source)
        self.assertIsNone(self.resolve(s));token,=self.tokens(s,'A')
        data=s.engine._effective_card_data(token);self.assertIn('Knight',data['type_line'])
        self.assertEqual(['W'],list(data['colors']));self.assertEqual((1,1),tuple(s.engine._numeric_stat(token.object_id,stat) for stat in ('power','toughness')))
        self.assertIn('banding',s.engine._combat_keywords(token));self.assertEqual('A',token.owner)
        self.until(s,lambda current:current.state.pending_decision is not None and current.state.pending_decision.kind=='combat.attackers')
        ctx=s.packet('pilot:A',full=True)['decision']['ctx'];self.assertIn(token.ref,ctx['banding']['banding_attackers'])
        invalid={'action_id':'attack','attacks':{card.ref:'B' for card in (token,first,extra)},'bands':[[token.ref,first.ref,extra.ref]]}
        self.reject(s,'A',invalid)
        self.reject(s,'A',{'action_id':'attack','attacks':{token.ref:'B',first.ref:'C'},'bands':[[token.ref,first.ref]]})
        command={'action_id':'attack','attacks':{token.ref:'B',first.ref:'B'},'bands':[[token.ref,first.ref]]}
        self.reject(s,'D',command);accepted=s.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(1,len(s.state.combat.attack_bands))
        self.until(s,lambda current:current.state.pending_decision is not None and current.state.pending_decision.kind=='combat.blockers')
        accepted=s.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:token.ref}});self.assertTrue(accepted.ok,accepted.summary)
        for card in (token,first):self.assertEqual([blocker.object_id],s.state.combat.blockers[card.object_id])
        self.until(s,lambda current:current.state.pending_decision is not None and current.state.pending_decision.kind=='combat.damage')
        self.assertEqual(['pilot:A'],s.pending_principals())
        command={'action_id':'assign_damage','assignments':[
            {'source':token.ref,'target':blocker.ref,'amount':1},
            {'source':first.ref,'target':blocker.ref,'amount':2},
            {'source':blocker.ref,'target':first.ref,'amount':2}]}
        self.reject(s,'B',command);accepted=s.act('pilot:A',command);self.assertTrue(accepted.ok,accepted.summary)
        self.until(s,lambda current:current.state.phase=='postcombat_main')
        self.assertEqual('battlefield',s.state.cards[token.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[first.object_id].zone)
        self.assertEqual('graveyard',s.state.cards[blocker.object_id].zone)
        self.finish(s,private)

    def test_producer_and_consumer_band_keyword_omission_mutants_keep_the_original_unready(self):
        from quorune.compiler import token_templates
        from quorune.rules import token_creation_capability_shapes as shapes
        card=self.db.lookup('Errand of Duty',fuzzy=False)
        self.assertEqual('exact',compile_oracle_card(card,capability_registry=self.registry).status)
        with patch.object(token_templates,'_CREATURE_TOKEN_KEYWORDS',token_templates._CREATURE_TOKEN_KEYWORDS-{'banding'}):
            self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=self.registry).status)
        with patch.object(shapes,'_CREATURE_KEYWORDS',shapes._CREATURE_KEYWORDS-{'Banding'}):
            self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=self.registry).status)

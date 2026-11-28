from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.turn_cast_grant_model import TURN_CAST_GRANT_CAPABILITY as CAP,TURN_CAST_GRANT_HANDLER as HANDLER,TURN_CAST_GRANT_FIELD as FIELD,TurnCastGrantSpec
from scripts.build_test_database import build_fixture_database
import test_optional_life_cost_reductions as controls
from quorune.rules.evidence_storage import read_evidence_document

NAMES=('Zaffai and the Tempests',"Danitha, New Benalia's Light",'Vision, Spectral Synthezoid',
    'Gisa and Geralf','Karador, Ghost Chieftain','One with the Multiverse')


class OwnedGraveyardTurnCompilerTests(unittest.TestCase):
    def test_coupled_graveyard_turn_permission_preserves_mandatory_exile_and_rejects_mutants(self):
        from test_granted_skulk import record
        from quorune.compiler.owned_graveyard_turn_play import owned_graveyard_turn_play_template
        from quorune.rules.owned_graveyard_turn_play import CAPABILITY, graveyard_turn_play_runtime_dependencies
        from quorune.semantics import SemanticProgram

        printed = ("Until end of turn, you may play lands and cast spells from your graveyard.\n"
                   "If a card would be put into your graveyard from anywhere this turn, exile that card instead.")
        registry = load_default_capability_registry()

        def compile_text(text):
            return compile_oracle_card(record(text, "Sorcery"), capability_registry=registry,
                                       capability_profile="commander_review")

        ir = compile_text(printed)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIn(CAPABILITY, node.capability_dependencies)
        for mutated in (printed.replace("lands and cast", "cards and cast"),
                        printed.replace("a card", "a permanent"),
                        printed.replace("this turn", "this game"),
                        printed.replace("your graveyard", "their graveyard")):
            with self.subTest(mutated=mutated):
                self.assertNotEqual("exact", compile_text(mutated).status)
        _, instructions, _, mechanics = owned_graveyard_turn_play_template(printed)
        program = SemanticProgram(key="graveyard-turn", label="Play", effects=list(instructions),
                                  coverage=list(mechanics), destination="graveyard")
        self.assertTrue(graveyard_turn_play_runtime_dependencies(program))
        for mutated in (
            replace(program, destination="exile"),
            replace(program, event="activate", active_zone="battlefield"),
            replace(program, effects=[{**instructions[0], "player": "$target.0"}]),
        ):
            with self.subTest(mutated=mutated), self.assertRaises(ValueError):
                graveyard_turn_play_runtime_dependencies(mutated)


class TurnCastGrantTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.OptionalLifeCostTests.session
    add=controls.OptionalLifeCostTests.add
    prepare=controls.OptionalLifeCostTests.prepare
    checkpoint=controls.OptionalLifeCostTests.checkpoint
    resolve=controls.OptionalLifeCostTests.resolve
    replay=controls.OptionalLifeCostTests.replay
    binding=controls.OptionalLifeCostTests.binding
    actions=controls.OptionalLifeCostTests.actions
    cast=controls.OptionalLifeCostTests.cast
    reject=controls.OptionalLifeCostTests.reject
    private_replay=controls.OptionalLifeCostTests.private_replay
    priority=controls.OptionalLifeCostTests.priority
    advance=controls.grant_controls.GlobalAlternativeCostTests.advance
    top=controls.OptionalLifeCostTests.top

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'turn-grants.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/turn-cast-grant-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Source turn casting grants',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def options(self,s,card,seat='A'):
        self.priority(s,seat)
        actions=s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
        row=next((a for a in actions if a['id']=='cast:'+card.ref),None)
        return row.get('cost_options',[]) if row else []

    def grants(self,s,card,seat='A'):
        return [o for o in self.options(s,card,seat) if o.get('permission_source')]

    def use(self,s,card,option,payment=None,targets=(),seat='A',**choices):
        self.assertNotIn(FIELD,option)
        command={'action_id':'cast:'+card.ref,'cost_option':option['id'],'pay':'manual','payment':payment or {},'targets':list(targets),**choices}
        self.reject(s,command,'pilot:D' if seat!='D' else 'pilot:C')
        accepted=s.act('pilot:'+seat,command);self.assertTrue(accepted.ok,accepted.summary)

    def test_actual_will_allows_ordinary_graveyard_plays_and_redirects_only_owners_cards(self):
        from quorune.flashback import FLASHBACK_CAST_ANNOTATION
        from quorune.record import authoritative_state_hash
        from quorune.rules.owned_graveyard_turn_play import current_graveyard_turn_play_grants

        game = self.session(801191)
        spell = self.add(game.engine, "Yawgmoth's Will", zone="hand")
        land = self.add(game.engine, "Generic Bound Plains", zone="graveyard", ref="will-owned-land")
        second_land = self.add(game.engine, "Generic Bound Plains", zone="graveyard", ref="will-other-land")
        own = self.add(game.engine, "Lightning Bolt", zone="graveyard", ref="will-own-bolt")
        other = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="will-other-bolt")
        future = self.add(game.engine, "Generic Bound Body", zone="graveyard", ref="will-permanent")
        self.assertTrue(self.binding(game, spell)["strict_capability_ready"])
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"B": 1, "C": 4, "R": 1})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.assertFalse(self.options(game, own))
        self.assertFalse(current_graveyard_turn_play_grants(game.engine, "A"))
        cast = game.act("pilot:A", {"action_id": "cast:" + spell.ref, "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[spell.object_id].zone)
        self.assertTrue(current_graveyard_turn_play_grants(game.engine, "A"))
        self.assertFalse(current_graveyard_turn_play_grants(game.engine, "B"))
        game = self.replay(game, load=True)

        play = {"action_id": "play-land:" + land.ref}
        played = game.act("pilot:A", play)
        self.assertTrue(played.ok, played.summary)
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id":"play-land:" + second_land.ref}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        option, = self.options(game, own)
        self.assertEqual("normal", option["id"])
        self.assertEqual(1, option["requirements"]["R"])
        cast = game.act("pilot:A", {"action_id":"cast:" + own.ref,"cost_option":"normal","targets":["D"],"pay":"auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertNotIn(FLASHBACK_CAST_ANNOTATION, game.state.cards[own.object_id].annotations)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[own.object_id].zone)

        self.priority(game,"B")
        cast = game.act("pilot:B", {"action_id":"cast:" + other.ref,"targets":["D"],"pay":"auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
        self.priority(game,"A")
        cast = game.act("pilot:A", {"action_id":"cast:" + future.ref,"pay":"auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[future.object_id].zone)
        self.replay(game, load=True)

    def test_six_originals_have_normal_complete_admission_and_independent_runtime_minimum(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(801000)
        for i,name in enumerate(NAMES):
            source=self.add(s.engine,name,ref='turn-source-'+str(i));bound=self.binding(s,source)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            program=next((p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if any(d.get('handler_id')==HANDLER for d in p.handlers)),None)
            self.assertIsNotNone(program)
            deps=tuple(d for d in program.capability_dependencies if d!=CAP) or ('activation.usage.once_per_turn',)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,result['blockers'])

    def test_closed_source_grammar_preserves_unknown_followups_and_descriptor_boundaries(self):
        from quorune.compiler.turn_cast_grants import turn_cast_grant_handler
        from quorune.semantic_runtime.turn_cast_grants import TurnCastGrantHandler
        text='Once during each of your turns, you may cast a creature spell from your graveyard.'
        template=turn_cast_grant_handler(text);self.assertIsNotNone(template)
        self.assertEqual(('graveyard',),TurnCastGrantHandler().validate(template[1]).origins)
        for bad in (text.replace('Once','Twice'),text.replace('your turns','each turn'),text+' It gains haste.',
                text.replace('creature','creature with an odd mana value'),text.replace('your graveyard',"an opponent's graveyard"),
                text.replace('a creature','a legendary creature'),text.replace('a creature','a white'),
                text.replace('a creature','an artifact or enchantment or creature')):
            self.assertIsNone(turn_cast_grant_handler(bad),bad)
        for key,value in (('schema_version',True),('origins',['exile']),('without_mana_cost',1)):
            raw=template[1]['grant'].copy();raw[key]=value
            with self.assertRaises(ValueError):TurnCastGrantSpec.from_dict(raw)
        for name in ('Serra Paragon',):
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)
        kess=compile_oracle_card(self.db.lookup('Kess, Dissident Mage'),capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',kess.status,kess.material_residuals)

    def test_actual_zaffai_normal_cast_preserves_quota_then_free_cast_consumes_it(self):
        s=self.session(801010);source=self.add(s.engine,'Zaffai and the Tempests')
        spells=[self.add(s.engine,'Lightning Bolt',zone='hand',ref='zaffai-bolt-'+str(i)) for i in range(3)]
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'R':2});self.checkpoint(s)
        normal=next((o for o in self.options(s,spells[0]) if o['id']=='normal'),None);self.assertIsNotNone(normal)
        self.use(s,spells[0],normal,{'R':1},['B']);self.assertIsNone(self.resolve(s))
        offered=self.grants(s,spells[1]);self.assertEqual(1,len(offered));self.assertEqual(source.ref,offered[0]['permission_source'])
        self.assertEqual(0,sum(offered[0]['requirements'].values()))
        self.reject(s,{'action_id':'cast:'+spells[1].ref,'cost_option':offered[0]['id'],'targets':['invented'],'pay':'auto'})
        self.assertEqual(1,len(self.grants(s,spells[1])))
        self.use(s,spells[1],offered[0],targets=['B']);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(34,s.state.players['B'].life);self.assertEqual([],self.grants(s,spells[2]))
        self.assertTrue(self.options(s,spells[2]));self.private_replay(s,private)

    def test_actual_gisa_entry_mills_then_one_zombie_cast_pays_printed_cost(self):
        s=self.session(801020);source=self.add(s.engine,'Gisa and Geralf',zone='hand')
        corpse=self.top(s,'Walking Corpse','gisa-milled-corpse')
        for i in range(3):self.top(s,'Generic Bound Plains','gisa-top-plains-'+str(i))
        other=self.add(s.engine,'Walking Corpse',zone='graveyard',ref='gisa-other-corpse')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'U':1,'B':2});self.checkpoint(s)
        self.assertEqual([],self.options(s,other))
        self.cast(s,'A',source);self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[corpse.object_id].zone)
        options=self.grants(s,corpse);self.assertEqual(1,len(options));self.assertEqual(1,options[0]['requirements']['GENERIC'])
        self.assertEqual(1,options[0]['requirements']['B']);self.use(s,corpse,options[0],{'C':1,'B':1})
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[corpse.object_id].zone)
        self.assertEqual([],self.options(s,other));self.private_replay(s,private)

    def test_actual_danitha_shares_one_graveyard_use_across_aura_and_equipment(self):
        for name in ('Rancor','Bonesplitter'):
            s=self.session(801030+(name=='Bonesplitter'));source=self.add(s.engine,"Danitha, New Benalia's Light")
            body=self.add(s.engine,'Generic Bound Body');spell=self.add(s.engine,name,zone='graveyard')
            other=self.add(s.engine,'Bonesplitter' if name=='Rancor' else 'Rancor',zone='graveyard',ref='danitha-other')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'C':1,'G':1});self.checkpoint(s);options=self.grants(s,spell);self.assertEqual(1,len(options))
            self.use(s,spell,options[0],{'G':1} if name=='Rancor' else {'C':1},[body.ref] if name=='Rancor' else [])
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[spell.object_id].zone)
            self.assertEqual([],self.options(s,other))
            self.assertTrue({'vigilance','trample','lifelink'}<=s.engine._combat_keywords(s.state.cards[source.object_id]))
            self.assertEqual(4 if name=='Rancor' else 2,int(s.engine._effective_card_data(s.state.cards[body.object_id])['power']))
            self.private_replay(s,private)

    def test_actual_karador_keeps_self_reduction_then_casts_one_creature_from_graveyard(self):
        s=self.session(801040);source=self.add(s.engine,'Karador, Ghost Chieftain',zone='hand')
        spell=self.add(s.engine,'Llanowar Elves',zone='graveyard')
        for i in range(2):self.add(s.engine,'Generic Bound Body',zone='graveyard',ref='karador-corpse-'+str(i))
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':2,'W':1,'B':1,'G':2});self.checkpoint(s);self.assertEqual([],self.options(s,spell))
        source_options=self.options(s,source);self.assertTrue(source_options);self.assertEqual(2,source_options[0]['requirements']['GENERIC'])
        self.use(s,source,source_options[0],{'C':2,'W':1,'B':1,'G':1})
        self.assertIsNone(self.resolve(s));options=self.grants(s,spell);self.assertEqual(1,len(options))
        self.use(s,spell,options[0],{'G':1});s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield',s.state.cards[spell.object_id].zone);self.private_replay(s,private)

    def test_actual_vision_free_robot_and_noncreature_excludes_other_creatures(self):
        for name in ('Monoist Sentry','Bonesplitter'):
            s=self.session(801050+(name=='Bonesplitter'));source=self.add(s.engine,'Vision, Spectral Synthezoid')
            spell=self.add(s.engine,name,zone='hand');excluded=self.add(s.engine,'Generic Bound Body',zone='hand')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'W':1});self.checkpoint(s)
            self.assertTrue(self.options(s,excluded));self.assertEqual([],self.grants(s,excluded))
            offered=self.grants(s,spell);self.assertEqual(1,len(offered));self.assertEqual(0,sum(offered[0]['requirements'].values()))
            self.use(s,spell,offered[0]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual('battlefield',s.state.cards[spell.object_id].zone)
            self.assertIn('flying',s.engine._combat_keywords(s.state.cards[source.object_id]));self.private_replay(s,private)

    def test_actual_multiverse_shares_hand_top_quota_but_retains_ordinary_top_permission(self):
        for top in (False,True):
            s=self.session(801060+top);self.add(s.engine,'One with the Multiverse')
            second=self.top(s,'Bonesplitter','multiverse-next')
            first=self.top(s,'Lightning Bolt','multiverse-first') if top else self.add(s.engine,'Lightning Bolt',zone='hand')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s)
            self.assertNotIn('Lightning Bolt',json.dumps(s.packet('pilot:C',full=True)))
            offered=self.grants(s,first);self.assertEqual(1,len(offered));self.use(s,first,offered[0],targets=['B'])
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual([],self.grants(s,second))
            normal=next((o for o in self.options(s,second) if o['id']=='normal'),None);self.assertIsNotNone(normal)
            self.use(s,second,normal,{'C':1});self.assertIsNone(self.resolve(s));self.assertEqual(37,s.state.players['B'].life)
            self.private_replay(s,private)

    def test_free_price_retains_kicker_tax_and_only_zeroes_mana_x(self):
        for index,(name,kicker) in enumerate((('Lightning Bolt',False),('Burst Lightning',True),('Blaze',False))):
            s=self.session(801070+index);self.add(s.engine,'Zaffai and the Tempests');self.add(s.engine,'Sphere of Resistance')
            spell=self.add(s.engine,name,zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'C':5,'R':1});self.checkpoint(s)
            options=[o for o in self.grants(s,spell) if (o['kind']=='kicker')==kicker]
            self.assertEqual(1,len(options),self.options(s,spell));option=options[0]
            self.assertEqual(5 if kicker else 1,option['requirements']['GENERIC']);self.assertEqual(0,option['requirements']['R'])
            if name=='Blaze':
                self.reject(s,{'action_id':'cast:'+spell.ref,'cost_option':option['id'],'pay':'auto','targets':['B'],'x':4})
            self.use(s,spell,option,{'C':5 if kicker else 1},['B'],**({'x':0} if name=='Blaze' else {}))
            s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual(36 if kicker else 40 if name=='Blaze' else 37,s.state.players['B'].life);self.private_replay(s,private)

    def test_free_price_keeps_declared_additional_x_and_private_discards(self):
        s=self.session(801080);self.add(s.engine,'Zaffai and the Tempests')
        spell=self.add(s.engine,'Sickening Dreams',zone='hand')
        cards=[self.add(s.engine,name,zone='hand',ref='grant-discard-'+str(i)) for i,name in enumerate(('Lightning Bolt','Rancor'))]
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s);self.checkpoint(s);options=self.grants(s,spell);self.assertEqual(1,len(options))
        self.reject(s,{'action_id':'cast:'+spell.ref,'cost_option':options[0]['id'],'pay':'auto','x':3,'discard_cards':[c.ref for c in cards]})
        self.use(s,spell,options[0],x=2,discard_cards=[c.ref for c in cards])
        for card in cards:self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        for seat in 'ABCD':self.assertEqual(38,s.state.players[seat].life)
        self.private_replay(s,private)

    def test_countered_grant_spell_does_not_refund_source_quota(self):
        s=self.session(801090);self.add(s.engine,'Zaffai and the Tempests')
        spell=self.add(s.engine,'Lightning Bolt',zone='hand',ref='countered-grant-bolt')
        other=self.add(s.engine,'Lightning Bolt',zone='hand',ref='next-grant-bolt')
        counter=self.add(s.engine,'Counterspell',seat='B',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool['U']=2;self.prepare(s,mana={'R':1});self.checkpoint(s)
        options=self.grants(s,spell);self.assertEqual(1,len(options));self.use(s,spell,options[0],targets=['B'])
        self.cast(s,'B',counter,[s.state.stack[0].ref]);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone);self.assertEqual(40,s.state.players['B'].life)
        self.assertEqual([],self.grants(s,other));self.assertTrue(self.options(s,other));self.private_replay(s,private)

    def test_real_blink_renews_incarnation_and_rejects_old_grant_price(self):
        s=self.session(801100);source=self.add(s.engine,'Zaffai and the Tempests')
        first=self.add(s.engine,'Lightning Bolt',zone='hand',ref='blink-first')
        second=self.add(s.engine,'Lightning Bolt',zone='hand',ref='blink-second');blink=self.add(s.engine,'Ephemerate',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'W':1,'R':1});self.checkpoint(s);old=self.grants(s,second)[0];logical=source.logical_object_id
        self.use(s,first,self.grants(s,first)[0],targets=['B']);self.assertIsNone(self.resolve(s));self.assertEqual([],self.grants(s,second))
        self.cast(s,'A',blink,[source.ref]);self.assertIsNone(self.resolve(s));self.assertNotEqual(logical,s.state.cards[source.object_id].logical_object_id)
        current=self.grants(s,second);self.assertEqual(1,len(current));self.assertNotEqual(old['id'],current[0]['id'])
        self.reject(s,{'action_id':'cast:'+second.ref,'cost_option':old['id'],'pay':'auto','targets':['B']})
        self.use(s,second,current[0],targets=['B']);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(34,s.state.players['B'].life);self.private_replay(s,private)

    def test_real_copied_source_has_its_own_allowance(self):
        s=self.session(801110);source=self.add(s.engine,'One with the Multiverse')
        first=self.add(s.engine,'Lightning Bolt',zone='hand',ref='copy-first');second=self.add(s.engine,'Lightning Bolt',zone='hand',ref='copy-second')
        copy=self.add(s.engine,'Mirrormade',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':1,'U':2,'R':1});self.checkpoint(s)
        self.use(s,first,self.grants(s,first)[0],targets=['B']);self.assertIsNone(self.resolve(s))
        self.cast(s,'A',copy);pending=self.resolve(s);self.assertIsNotNone(pending)
        self.assertEqual('replacement.order',pending.kind)
        choices=s.packet('pilot:A',full=True)['decision']['ctx']['options']
        matching=[row for row in choices if source.ref in row['id'] and not row['id'].startswith('decline:')]
        self.assertEqual(1,len(matching),choices)
        result=s.act('pilot:A',{'action_id':'choose','replacement':matching[0]['id']});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));options=self.grants(s,second);self.assertEqual(1,len(options))
        self.assertEqual(s.state.cards[copy.object_id].ref,options[0]['permission_source'])
        self.use(s,second,options[0],targets=['B']);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(34,s.state.players['B'].life);self.private_replay(s,private)

    def test_own_turn_only_and_new_extra_turn_renews_the_quota(self):
        s=self.session(801120);self.add(s.engine,'Zaffai and the Tempests')
        bolt=self.add(s.engine,'Lightning Bolt',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,active='B',mana={'R':1});self.checkpoint(s);self.assertEqual([],self.grants(s,bolt))
        normal=self.options(s,bolt);self.assertEqual(1,len(normal));self.use(s,bolt,normal[0],{'R':1},['B'])
        self.assertIsNone(self.resolve(s));self.private_replay(s,private)
        s=self.session(801121);self.add(s.engine,'Zaffai and the Tempests');warp=self.add(s.engine,'Time Warp',zone='hand')
        bolt=self.add(s.engine,'Lightning Bolt',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s);self.checkpoint(s);turn=s.state.turn_sequence
        options=self.grants(s,warp);self.assertEqual(1,len(options));self.use(s,warp,options[0],targets=['A']);self.assertIsNone(self.resolve(s))
        self.assertEqual([],self.grants(s,bolt));self.advance(s,lambda q:q.state.turn_sequence>turn and q.state.active_player=='A' and q.state.phase=='precombat_main')
        options=self.grants(s,bolt);self.assertEqual(1,len(options));self.use(s,bolt,options[0],targets=['B'])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual(37,s.state.players['B'].life);self.private_replay(s,private)

    def test_graveyard_grant_composes_with_global_price_kicker_and_life_reduction(self):
        for life in (False,True):
            s=self.session(801130+life);source=self.add(s.engine,'Karador, Ghost Chieftain');self.add(s.engine,'Fist of Suns')
            if life:self.add(s.engine,'Defiler of Vigor')
            spell=self.add(s.engine,'Gnarlid Colony',zone='graveyard');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            payment={'C':2,'W':1,'U':1,'B':1,'R':1,'G':1 if life else 2}
            self.prepare(s,mana=payment);self.checkpoint(s)
            options=[o for o in self.grants(s,spell) if o['kind']=='kicker' and o['requirements']['W']==1 and o.get('additional_life',0)==(2 if life else 0)]
            self.assertEqual(1,len(options),self.options(s,spell));self.assertEqual(source.ref,options[0]['permission_source'])
            self.use(s,spell,options[0],payment);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual(2,s.state.cards[spell.object_id].counters.get('+1/+1',0));self.assertEqual(38 if life else 40,s.state.players['A'].life)
            self.private_replay(s,private)

    def test_zombie_permission_rejects_a_face_down_non_zombie_spell(self):
        s=self.session(801140);self.add(s.engine,'Gisa and Geralf')
        spell=self.add(s.engine,'Skinthinner',zone='graveyard');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'B':1});self.checkpoint(s);self.assertTrue(self.grants(s,spell))
        self.assertFalse(any(a['id']=='cast-morph:'+spell.ref for a in self.actions(s)))
        self.reject(s,{'action_id':'cast-morph:'+spell.ref,'pay':'auto'})
        options=self.grants(s,spell);self.assertEqual(1,len(options));self.use(s,spell,options[0],{'C':1,'B':1})
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertFalse(s.state.cards[spell.object_id].face_down)
        self.private_replay(s,private)

    def test_creature_grant_accepts_actual_face_down_creature_cast(self):
        s=self.session(801150);self.add(s.engine,'Karador, Ghost Chieftain')
        spell=self.add(s.engine,'Skinthinner',zone='graveyard');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3});self.checkpoint(s)
        action=next((a for a in self.actions(s) if a['id']=='cast-morph:'+spell.ref),None)
        self.assertIsNotNone(action)
        options=[o for o in action['cost_options'] if o.get('permission_source')];self.assertEqual(1,len(options))
        result=s.act('pilot:A',{'action_id':action['id'],'cost_option':options[0]['id'],'pay':'manual','payment':{'C':3}})
        self.assertTrue(result.ok,result.summary);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        current=s.state.cards[spell.object_id];self.assertTrue(current.face_down)
        self.assertEqual('Creature',s.engine._effective_card_data(current)['type_line']);self.private_replay(s,private)

    def test_disturb_self_permission_remains_separate_from_source_grant_use(self):
        for available_quota in (False,True):
            s=self.session(801160+available_quota);self.add(s.engine,'Karador, Ghost Chieftain')
            spell=self.add(s.engine,'Baithook Angler // Hook-Haunt Drifter',zone='graveyard')
            other=self.add(s.engine,'Generic Bound Body',zone='graveyard',ref='disturb-other')
            spent=self.add(s.engine,'Generic Bound Body',zone='graveyard',ref='disturb-spent')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana={'C':1,'U':1,'W':2});self.checkpoint(s)
            if not available_quota:
                self.use(s,spent,self.grants(s,spent)[0],{'W':1});self.assertIsNone(self.resolve(s))
            action=next((a for a in self.actions(s) if a['id']=='cast:'+spell.ref+':face:Hook-Haunt Drifter'),None)
            self.assertIsNotNone(action)
            self.assertTrue(all(not o.get('permission_source') for o in action['cost_options']))
            options=action['cost_options'];self.assertEqual(1,len(options),options)
            result=s.act('pilot:A',{'action_id':action['id'],'face':'Hook-Haunt Drifter','cost_option':options[0]['id'],
                'pay':'manual','payment':{'C':1,'U':1}})
            self.assertTrue(result.ok,result.summary);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
            self.assertEqual('Hook-Haunt Drifter',s.engine.display_name(spell.object_id))
            self.assertEqual(available_quota,bool(self.grants(s,other)));self.private_replay(s,private)

    def test_verified_grant_withdrawal_and_fresh_compiler_and_quota_omissions_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']==CAP).update(status='blocked',blockers=['withdrawn turn source grant'],dependency_fail_closed_status='not_run')
        registry=CapabilityRegistry(raw);decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        ir=compile_oracle_card(self.db.lookup('Zaffai and the Tempests'),capability_registry=registry,capability_profile='commander_review')
        blockers=[b for r in ir.material_residuals for b in r.blockers]
        self.assertTrue(any('status:'+CAP+':blocked' in b for b in blockers),blockers)
        self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune.compiler import turn_cast_grants as compiler
        method='test_six_originals_have_normal_complete_admission_and_independent_runtime_minimum'
        with patch.object(compiler,'turn_cast_grant_handler',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        from quorune.rules.casting import commit as owner
        method='test_actual_zaffai_normal_cast_preserves_quota_then_free_cast_consumes_it'
        with patch.object(owner,'commit_turn_cast_grant_use',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def _for_permanent_type(self, options, kind):
        return [option for option in options if f" for {kind}:" in option.get("label", "")]

    def test_actual_muldrotha_uses_separate_permanent_types_and_new_source_incarnation(self):
        game = self.session(801150)
        source = self.add(game.engine, "Muldrotha, the Gravetide")
        first = self.add(game.engine, "Ornithopter", zone="graveyard", ref="muldrotha-first")
        second = self.add(game.engine, "Memnite", zone="graveyard", ref="muldrotha-second")
        third = self.add(game.engine, "Ornithopter", zone="graveyard", ref="muldrotha-third")
        instant = self.add(game.engine, "Lightning Bolt", zone="graveyard")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        self.assertTrue(self.binding(game, source)["strict_capability_ready"])
        self.prepare(game, mana={"W": 1})
        self.checkpoint(game)

        first_options = self.grants(game, first)
        self.assertEqual(2, len(first_options))
        artifact, = self._for_permanent_type(first_options, "artifact")
        self.assertEqual(1, len(self._for_permanent_type(first_options, "creature")))
        self.assertEqual([], self.options(game, instant))
        self.use(game, first, artifact)
        self.assertIsNone(self.resolve(game))

        creature, = self.grants(game, second)
        self.assertIn(" for creature:", creature["label"])
        self.reject(game, {"action_id": "cast:" + second.ref, "cost_option": artifact["id"], "pay": "auto"})
        self.use(game, second, creature)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], self.options(game, third))

        retired = game.state.cards[source.object_id].logical_object_id
        self.cast(game, "A", blink, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertNotEqual(retired, game.state.cards[source.object_id].logical_object_id)
        renewed = self.grants(game, third)
        self.assertEqual(2, len(renewed))
        self.assertNotIn(artifact["id"], [option["id"] for option in renewed])
        self.use(game, third, self._for_permanent_type(renewed, "artifact")[0])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[third.object_id].zone)
        self.replay(game, load=True)

    def test_actual_muldrotha_land_quota_needs_own_turn_and_an_available_land_play(self):
        from quorune.rules.casting.turn_cast_grants import current_turn_cast_grants

        def land_offers(game, card):
            self.priority(game, "A")
            packet = game.packet("pilot:A", full=True)
            return [action for action in packet["decision"]["ctx"]["legal"]["actions"]
                    if action["id"].startswith("play-land:" + card.ref)]

        game = self.session(801151)
        source = self.add(game.engine, "Muldrotha, the Gravetide")
        forest = self.add(game.engine, "Forest", zone="graveyard")
        island = self.add(game.engine, "Island", zone="graveyard")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        explore = self.add(game.engine, "Explore", zone="hand")
        self.assertTrue(self.binding(game, source)["strict_capability_ready"])
        self.prepare(game, mana={"W": 1, "C": 1, "G": 1})
        self.checkpoint(game)
        offer, = land_offers(game, forest)
        self.assertEqual(source.ref, offer["permission_source"])
        command = {"action_id": offer["id"], "land_play_permission": offer["land_play_permission"]}
        self.reject(game, command, "pilot:B")
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", game.state.cards[forest.object_id].zone)
        self.assertEqual((), current_turn_cast_grants(game.engine, "A", island, for_land=True))
        self.assertEqual([], land_offers(game, island))
        self.cast(game, "A", blink, [source.ref])
        self.assertIsNone(self.resolve(game))
        unused, = current_turn_cast_grants(game.engine, "A", island, for_land=True)
        self.assertEqual("land", unused["permanent_type"])
        self.assertEqual([], land_offers(game, island))
        self.cast(game, "A", explore)
        self.assertIsNone(self.resolve(game))
        offer, = land_offers(game, island)
        result = game.act("pilot:A", {"action_id": offer["id"], "land_play_permission": offer["land_play_permission"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", game.state.cards[island.object_id].zone)
        self.replay(game, load=True)

        off_turn = self.session(801152)
        self.add(off_turn.engine, "Muldrotha, the Gravetide")
        land = self.add(off_turn.engine, "Forest", zone="graveyard")
        spell = self.add(off_turn.engine, "Ornithopter", zone="graveyard")
        self.prepare(off_turn, active="B")
        self.checkpoint(off_turn)
        self.assertEqual([], land_offers(off_turn, land))
        self.assertEqual([], self.options(off_turn, spell))
        self.replay(off_turn, load=True)

    def test_actual_muldrotha_bestow_charges_the_spell_permanent_type(self):
        game = self.session(801153)
        self.add(game.engine, "Muldrotha, the Gravetide")
        target = self.add(game.engine, "Generic Bound Body")
        creature = self.add(game.engine, "Ornithopter", zone="graveyard")
        dryad = self.add(game.engine, "Leafcrown Dryad", zone="graveyard")
        self.prepare(game, mana={"C": 3, "G": 1})
        self.checkpoint(game)
        all_dryad_options = self.grants(game, dryad)
        self.assertEqual(3, len(all_dryad_options))
        bestow_quote, = [option for option in all_dryad_options if "Bestow" in option["label"]]
        self.assertIn(" for enchantment:", bestow_quote["label"])
        creature_use, = self._for_permanent_type(self.grants(game, creature), "creature")
        self.use(game, creature, creature_use)
        self.assertIsNone(self.resolve(game))
        options = self.grants(game, dryad)
        bestow, = [option for option in options if "Bestow" in option["label"]]
        self.assertIn(" for enchantment:", bestow["label"])
        self.assertFalse(self._for_permanent_type(options, "creature"))
        self.use(game, dryad, bestow, {"C": 3, "G": 1}, targets=[target.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(target.object_id, game.state.cards[dryad.object_id].attached_to)
        self.replay(game, load=True)

    def test_per_type_graveyard_permission_is_versioned_and_source_spanned(self):
        from quorune.compiler.turn_cast_grants import turn_cast_grant_handler
        from quorune.rules.turn_cast_grant_model import (
            TURN_CAST_PERMANENT_TYPE_CAPABILITY, permanent_type_graveyard_grant,
        )
        from test_qualified_zone_event_queries import query_record

        text = ("During each of your turns, you may play a land and cast a permanent spell of each "
                "permanent type from your graveyard.")
        descriptor = turn_cast_grant_handler(text)
        self.assertEqual(permanent_type_graveyard_grant(), TurnCastGrantSpec.from_dict(descriptor[1]["grant"]))
        self.assertEqual(5, descriptor[1]["grant"]["schema_version"])
        self.assertIn(TURN_CAST_PERMANENT_TYPE_CAPABILITY, descriptor[2])
        for alternate in (
            text.replace("your turns", "turns"),
            text.replace("your graveyard", "any graveyard"),
            text.replace("each permanent type", "each nonland card type"),
            text + " It gains haste.",
        ):
            self.assertIsNone(turn_cast_grant_handler(alternate))
        for patch_fields in ({"allow_land_play": False}, {"without_mana_cost": True},
                             {"schema_version": 4}, {"origins": ["exile"]}):
            with self.assertRaises(ValueError):
                TurnCastGrantSpec.from_dict({**descriptor[1]["grant"], **patch_fields})
        witness = replace(query_record(text), type_line="Creature — Elemental")
        self.assertEqual("exact", compile_oracle_card(witness, capability_registry=self.registry).status)
        with patch("quorune.compiler.turn_cast_grants.turn_cast_grant_handler", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(witness, capability_registry=self.registry).status)

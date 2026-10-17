from __future__ import annotations

from itertools import combinations
from pathlib import Path
import tempfile
import unittest

from common import keep_all, load_assets, make_session
from quorune.combat_evasion import (
    CombatantEvasionCharacteristics,
    combat_evasion_verdict,
)
from quorune.landwalk import (
    BASIC_LANDWALK_TYPES,
    BasicLandwalkBlockVerdict,
    LandwalkRuleError,
    basic_landwalk_block_verdict,
)
from quorune.model import CombatState
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)


class BasicLandwalkTests(unittest.TestCase):
    @staticmethod
    def combatant(*keywords: str) -> CombatantEvasionCharacteristics:
        return CombatantEvasionCharacteristics(
            keywords=frozenset(keywords),
            colors=frozenset(),
            card_types=frozenset({"creature"}),
            power=2,
        )

    def test_each_basic_variant_uses_current_matching_land_subtype(self):
        for keyword, land_type in BASIC_LANDWALK_TYPES:
            with self.subTest(keyword=keyword):
                verdict = basic_landwalk_block_verdict(
                    frozenset({"landwalk", keyword}),
                    frozenset({land_type}),
                )
                self.assertEqual(
                    BasicLandwalkBlockVerdict(False, (land_type,)),
                    verdict,
                )
                self.assertTrue(
                    basic_landwalk_block_verdict(
                        frozenset({"landwalk", keyword}),
                        frozenset(),
                    ).allowed
                )

    def test_multiple_variants_are_cumulative_and_repeated_instances_redundant(self):
        verdict = basic_landwalk_block_verdict(
            frozenset(
                {
                    "landwalk",
                    "islandwalk",
                    "swampwalk",
                    "forestwalk",
                }
            ),
            frozenset({"forest", "swamp", "mountain"}),
        )

        self.assertEqual(("swamp", "forest"), verdict.matching_land_types)
        self.assertEqual("attacker_has_swampwalk", verdict.reason)

    def test_basic_landwalk_holds_across_the_bounded_keyword_land_grid(self):
        pairs = tuple(BASIC_LANDWALK_TYPES)
        for keyword_count in range(len(pairs) + 1):
            for selected_keywords in combinations(pairs, keyword_count):
                keywords = frozenset(keyword for keyword, _ in selected_keywords)
                selected_types = {
                    land_type for _, land_type in selected_keywords
                }
                for land_count in range(len(pairs) + 1):
                    for selected_lands in combinations(pairs, land_count):
                        land_types = frozenset(
                            land_type for _, land_type in selected_lands
                        )
                        with self.subTest(
                            keywords=keywords,
                            land_types=land_types,
                        ):
                            self.assertEqual(
                                not bool(selected_types.intersection(land_types)),
                                basic_landwalk_block_verdict(
                                    keywords,
                                    land_types,
                                ).allowed,
                            )

    def test_malformed_and_unsupported_variants_fail_closed(self):
        with self.assertRaisesRegex(LandwalkRuleError, "attacker"):
            basic_landwalk_block_verdict({"swampwalk"}, frozenset())
        with self.assertRaisesRegex(LandwalkRuleError, "land type"):
            basic_landwalk_block_verdict(
                frozenset({"swampwalk"}), frozenset({"desert"})
            )
        for unsupported in (
            "landwalk",
            "desertwalk",
            "legendary landwalk",
            "nonbasic landwalk",
        ):
            with self.subTest(unsupported=unsupported):
                with self.assertRaisesRegex(
                    LandwalkRuleError, "Unsupported landwalk"
                ):
                    basic_landwalk_block_verdict(
                        frozenset({unsupported}), frozenset()
                    )
        with self.assertRaisesRegex(LandwalkRuleError, "requires"):
            BasicLandwalkBlockVerdict(False)

    def test_landwalk_remains_cumulative_with_shadow_and_flying(self):
        self.assertEqual(
            "attacker_has_swampwalk",
            combat_evasion_verdict(
                self.combatant("flying", "swampwalk", "landwalk"),
                self.combatant("reach"),
                frozenset({"swamp"}),
            ).reason,
        )
        self.assertEqual(
            "attacker_has_islandwalk",
            combat_evasion_verdict(
                self.combatant("shadow", "islandwalk", "landwalk"),
                self.combatant("shadow"),
                frozenset({"island"}),
            ).reason,
        )


class BasicLandwalkIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.phase = "combat"
        engine.state.step = "declare_blockers"
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def permanent(engine, controller: str, name: str, type_line: str, *, keywords=()):
        ref = engine.create_token(
            controller,
            name=name,
            characteristics={
                "type_line": type_line,
                "power": "2" if "Creature" in type_line else None,
                "toughness": "2" if "Creature" in type_line else None,
                "keywords": list(keywords),
            },
        )[0]
        return engine._resolve_object(
            controller,
            ref,
            zones={"battlefield"},
        )

    def prepare_block(self, session, *, with_matching_land: bool):
        engine = session.engine
        attacker = self.permanent(
            engine,
            "A",
            "Current Swampwalker",
            "Token Creature — Test",
            keywords=("Landwalk", "Swampwalk", "SWAMPWALK"),
        )
        blocker = self.permanent(
            engine,
            "C",
            "C blocker",
            "Token Creature — Test",
        )
        if with_matching_land:
            self.permanent(
                engine,
                "C",
                "Nonbasic Swamp",
                "Token Land — Swamp",
            )
            ordinary = self.permanent(
                engine,
                "A",
                "Ordinary attacker",
                "Token Creature — Test",
            )
            ordinary.attacking = "C"
        attacker.attacking = "C"
        attackers = {attacker.object_id: "C"}
        if with_matching_land:
            attackers[ordinary.object_id] = "C"
        engine.state.combat = CombatState(
            attackers_declared=True,
            had_attacking_creature=True,
            attackers=attackers,
            defending_players=["C"],
        )
        engine._begin_blocker_decisions()
        return attacker, blocker

    def test_offer_and_command_share_current_landwalk_legality_and_rollback(self):
        session = self.make_session(702_014_001)
        attacker, blocker = self.prepare_block(
            session,
            with_matching_land=True,
        )
        decision = session.packet("pilot:C", full=True)["decision"]
        self.assertNotIn(
            attacker.ref,
            decision["ctx"]["legal_blocks"][blocker.ref],
        )
        for hidden_seat in ("A", "B", "D"):
            self.assertIsNone(
                session.packet(f"pilot:{hidden_seat}", full=True)["decision"]
            )

        before = authoritative_state_hash(session.state)
        rejected = session.act(
            "pilot:C",
            {"a": "block", "blk": {blocker.ref: attacker.ref}},
        )
        self.assertFalse(rejected.ok)
        self.assertIn("swampwalk", rejected.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_legal_nonmatching_block_is_seat_scoped_and_replays(self):
        session = self.make_session(702_014_002)
        attacker, blocker = self.prepare_block(
            session,
            with_matching_land=False,
        )
        decision = session.packet("pilot:C", full=True)["decision"]
        self.assertEqual(
            [attacker.ref],
            decision["ctx"]["legal_blocks"][blocker.ref],
        )
        session.initial_checkpoint = checkpoint_envelope(session.state)

        accepted = session.act(
            "pilot:C",
            {"a": "block", "blk": {blocker.ref: attacker.ref}},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "basic-landwalk"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(1, replay["commands"])


class QualifiedLandwalkRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/'qualified-landwalk.sqlite3'
        from quorune.deck import DeckDefinition, DeckEntry
        build_fixture_database([ROOT/'tests/fixtures/qualified-landwalk-cards.json',ROOT/'tests/fixtures/bound-effect-program-cards.json'],path)
        cls.db=CardDatabase(path)
        cls.capabilities=load_default_capability_registry()
        cls.registry=cls.capabilities
        cls.deck=DeckDefinition('Qualified Landwalk',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    register_fixture_cards_on_add=True
    import test_bound_effect_programs as _bound
    session=_bound.BoundEffectProgramRuntimeTests.session
    add=_bound.BoundEffectProgramRuntimeTests.add
    checkpoint=_bound.BoundEffectProgramRuntimeTests.checkpoint
    replay=_bound.BoundEffectProgramRuntimeTests.replay
    ready=_bound.BoundEffectProgramRuntimeTests.ready
    resolve=_bound.BoundEffectProgramRuntimeTests.resolve

    def test_actual_snow_forestwalker_is_completely_admitted(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        card=self.db.lookup('Rime Dryad')
        program=compile_best_available_card_program(self.db,card,
            semantic_registry=SemanticRegistry(),capability_registry=self.capabilities,
            capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.capabilities,
            profile='commander_review')['strict_capability_ready'],'Actual Rime Dryad must be completely admitted')


    def test_nine_qualified_landwalk_originals_bind_completely(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        for name in ('Rime Dryad','Ayumi, the Last Visitor','Desert Nomads','Vectis Gloves',
                'Livonya Silone',"Trailblazer's Boots",'Legions of Lim-Dûl','Dryad Sophisticate','Zombie Musher'):
            with self.subTest(name=name):
                p=compile_best_available_card_program(self.db,self.db.lookup(name),semantic_registry=SemanticRegistry(),
                    capability_registry=self.capabilities,capability_profile='commander_review')
                self.assertTrue(bind_card_program_runtime(p,capability_registry=self.capabilities,
                    profile='commander_review')['strict_capability_ready'],name)

    def test_actual_snow_forestwalk_requires_one_current_defender_land(self):
        from quorune.rules.capabilities import load_default_capability_registry
        self.test_actual_snow_forestwalker_is_completely_admitted()
        for index,(land_names,matching,seat) in enumerate((
                (('Snow-Covered Forest',),True,'B'),
                (('Snow-Covered Island','Forest'),False,'B'),
                (('Snow-Covered Forest',),False,'C'))):
            with self.subTest(lands=land_names,seat=seat):
                session=self.session(529010+index);engine=session.engine
                attacker=self.add(engine,'Rime Dryad',ref='QUALIFIED-ATTACKER')
                blocker=self.add(engine,'Grizzly Bears',seat='B',ref='QUALIFIED-BLOCKER')
                ordinary=self.add(engine,'Grizzly Bears',ref='QUALIFIED-ORDINARY')
                private=self.add(engine,'Generic Bound Body',seat='D',zone='hand',ref='qualified-private-hand')
                for number,name in enumerate(land_names):self.add(engine,name,seat=seat,ref='qualified-land-'+str(number))
                engine.state.started=True;engine.state.active_player='A'
                from quorune.engine import TURN_STEPS
                engine.state.phase='combat';engine.state.step='declare_blockers'
                engine.state.phase_index=TURN_STEPS.index(('combat','declare_blockers'))
                attacker.attacking='B';ordinary.attacking='B'
                engine.state.combat=CombatState(attackers_declared=True,had_attacking_creature=True,
                    attackers={attacker.object_id:'B',ordinary.object_id:'B'},defending_players=['B'])
                engine._begin_blocker_decisions();self.checkpoint(session)
                packet=session.packet('pilot:B',full=True)
                self.assertEqual(not matching,attacker.ref in packet['decision']['ctx']['legal_blocks'][blocker.ref])
                self.assertNotIn(private.ref,str(session.packet('pilot:C',full=True)))
                self.assertIn(private.ref,str(session.packet('pilot:D',full=True)))
                session=self.replay(session,load=True);engine=session.engine
                before=authoritative_state_hash(engine.state)
                self.assertFalse(session.act('pilot:C',{'a':'block','blk':{blocker.ref:attacker.ref}}).ok)
                self.assertEqual(before,authoritative_state_hash(engine.state))
                if matching:
                    rejected=session.act('pilot:B',{'a':'block','blk':{blocker.ref:attacker.ref}})
                    self.assertFalse(rejected.ok)
                    self.assertEqual(before,authoritative_state_hash(engine.state))
                accepted=session.act('pilot:B',{'a':'block','blk':{blocker.ref:ordinary.ref if matching else attacker.ref}})
                self.assertTrue(accepted.ok,accepted.summary)
                for _ in range(40):
                    if engine.state.phase=='postcombat_main':break
                    result=session.act(session.pending_principals()[0],{'action_id':'pass'})
                    self.assertTrue(result.ok,result.summary)
                self.assertEqual('postcombat_main',engine.state.phase)
                self.assertEqual(39 if matching else 38,engine.state.players['B'].life)
                self.assertEqual('battlefield' if matching else 'graveyard',engine.state.cards[attacker.object_id].zone)
                self.replay(session,load=True)

    def test_snow_land_conjunction_omission_is_killed_after_original_admission(self):
        from unittest.mock import patch
        import quorune.landwalk as owner
        original=owner._matching_keywords
        def combine(keywords,lands):
            if lands:
                lands=(owner.LandwalkLandCharacteristics(
                    frozenset().union(*(row.card_types for row in lands)),
                    frozenset().union(*(row.subtypes for row in lands)),
                    frozenset().union(*(row.supertypes for row in lands))),)
            return original(keywords,lands)
        from contextlib import nullcontext
        with patch.object(owner,'_matching_keywords',side_effect=combine), patch.object(self,'subTest',side_effect=lambda **_:nullcontext()):
            with self.assertRaises(AssertionError):
                self.test_actual_snow_forestwalk_requires_one_current_defender_land()


    def cast_qualified_control(self,session,card,targets=(),payment=None):
        while session.pending_principals()[0]!='pilot:A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offer=next(row for row in rows if row['id']=='cast:'+card.ref)
        response={'action_id':offer['id'],'targets':list(targets),'pay':'manual','payment':payment or {}}
        accepted=session.act('pilot:A',response)
        self.assertTrue(accepted.ok,accepted.summary)
        return self.resolve(session)

    def test_actual_qualified_equipment_retargets_its_live_landwalk_and_stats(self):
        from quorune.engine import TURN_STEPS
        from quorune.combat_evasion_engine_adapter import engine_combat_evasion_verdict
        for index,(name,keyword,land_name,power) in enumerate((
                ("Trailblazer's Boots",'nonbasic landwalk','Overgrown Tomb',2),
                ('Vectis Gloves','artifact landwalk','Darksteel Citadel',4))):
            with self.subTest(equipment=name):
                session=self.session(529030+index);engine=session.engine
                source=self.add(engine,name,ref='qualified-equipment')
                first=self.add(engine,'Grizzly Bears',ref='qualified-recipient-one')
                second=self.add(engine,'Grizzly Bears',ref='qualified-recipient-two')
                blocker=self.add(engine,'Grizzly Bears',seat='B',ref='qualified-equipment-blocker')
                self.add(engine,land_name,seat='B')
                from quorune.card_programs import bind_card_program_runtime
                from quorune.card_programs.adapters import compile_best_available_card_program
                from quorune.semantics import SemanticRegistry
                program=compile_best_available_card_program(self.db,self.db.by_oracle_id(source.oracle_id),
                    semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
                self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,
                    profile='commander_review')['strict_capability_ready'])
                engine.permissions.invalidate_current();engine.state.pending_decision=None
                engine.state.priority_player=None;engine.state.priority_passes=[]
                engine.state.started=True;engine.state.active_player='A'
                engine.state.phase='precombat_main';engine.state.step='main'
                engine.state.phase_index=TURN_STEPS.index(('precombat_main','main'))
                engine.state.players['A'].mana_pool['C']=4
                engine._grant_priority('A');engine.pump()
                self.checkpoint(session)
                for recipient in (first,second):
                    while session.pending_principals()[0]!='pilot:A':
                        self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                    rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                    equip=next(row for row in rows if row['id'].startswith('activate:'+source.ref+':'))
                    result=session.act('pilot:A',{'action_id':equip['id'],'targets':[recipient.ref],
                        'pay':'manual','payment':{'C':2}})
                    self.assertTrue(result.ok,result.summary);self.resolve(session)
                    current=engine.state.cards[recipient.object_id]
                    self.assertIn(keyword,engine._combat_keywords(current))
                    self.assertEqual(power,engine._numeric_stat(current.object_id,'power'))
                    self.assertFalse(engine_combat_evasion_verdict(engine,current,blocker,'B').allowed)
                    self.replay(session,load=True)
                self.assertNotIn(keyword,engine._combat_keywords(engine.state.cards[first.object_id]))
                self.assertEqual(2,engine._numeric_stat(first.object_id,'power'))
                self.assertTrue(engine_combat_evasion_verdict(engine,engine.state.cards[first.object_id],blocker,'B').allowed)

    def test_actual_humbled_landwalker_copies_printed_keyword_and_keeps_it_after_bounce(self):
        from quorune.engine import TURN_STEPS
        from quorune.combat_evasion_engine_adapter import engine_combat_evasion_verdict
        session=self.session(529040);engine=session.engine
        original=self.add(engine,'Rime Dryad',ref='qualified-copy-original')
        copier=self.add(engine,'Clone',zone='hand',ref='qualified-clone')
        humble=self.add(engine,'Humble',zone='hand',ref='qualified-humble')
        bounce=self.add(engine,'Unsummon',zone='hand',ref='qualified-bounce')
        blocker=self.add(engine,'Grizzly Bears',seat='B',ref='qualified-copy-blocker')
        self.add(engine,'Snow-Covered Forest',seat='B')
        self.ready(session,humble,{'C':6,'U':4,'W':2})
        engine.state.phase_index=TURN_STEPS.index(('precombat_main','main'));self.checkpoint(session)
        self.cast_qualified_control(session,humble,(original.ref,),{'C':1,'W':1})
        self.assertNotIn('snow forestwalk',engine._combat_keywords(engine.state.cards[original.object_id]))
        self.assertEqual((0,1),(engine._numeric_stat(original.object_id,'power'),engine._numeric_stat(original.object_id,'toughness')))
        decision=self.cast_qualified_control(session,copier,payment={'C':3,'U':1})
        self.assertEqual('replacement.order',decision.kind)
        session=self.replay(session,load=True);engine=session.engine
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        choice=next(row for row in options if original.ref in row['id'] and not row['id'].startswith('decline:'))
        result=session.act('pilot:A',{'action_id':'choose','replacement':choice['id']})
        self.assertTrue(result.ok,result.summary);self.resolve(session)
        copied=engine.state.cards[copier.object_id]
        self.assertIn('snow forestwalk',engine._combat_keywords(copied))
        self.assertEqual((1,2),(engine._numeric_stat(copied.object_id,'power'),engine._numeric_stat(copied.object_id,'toughness')))
        self.assertFalse(engine_combat_evasion_verdict(engine,copied,engine.state.cards[blocker.object_id],'B').allowed)
        self.cast_qualified_control(session,bounce,(original.ref,),{'U':1})
        self.assertEqual('hand',engine.state.cards[original.object_id].zone)
        self.assertIn('snow forestwalk',engine._combat_keywords(engine.state.cards[copier.object_id]))
        self.replay(session,load=True)

    def test_actual_yavimaya_changes_one_snow_lands_current_subtypes(self):
        from quorune.engine import TURN_STEPS
        from quorune.combat_evasion_engine_adapter import engine_combat_evasion_verdict,defending_land_characteristics
        session=self.session(529050);engine=session.engine
        source=self.add(engine,'Yavimaya, Cradle of Growth',zone='hand',ref='qualified-yavimaya')
        attacker=self.add(engine,'Rime Dryad',ref='qualified-yavimaya-attacker')
        blocker=self.add(engine,'Grizzly Bears',seat='B',ref='qualified-yavimaya-blocker')
        snow=self.add(engine,'Snow-Covered Island',seat='B')
        self.add(engine,'Forest',seat='B')
        self.ready(session,source,{})
        engine.state.phase_index=TURN_STEPS.index(('precombat_main','main'));self.checkpoint(session)
        self.assertTrue(engine_combat_evasion_verdict(engine,attacker,blocker,'B').allowed)
        rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offer=next(row for row in rows if row.get('card')==source.ref and row['action']=='play_land')
        result=session.act('pilot:A',{'action_id':offer['id']});self.assertTrue(result.ok,result.summary)
        self.assertFalse(engine_combat_evasion_verdict(engine,engine.state.cards[attacker.object_id],engine.state.cards[blocker.object_id],'B').allowed)
        self.assertTrue(any({'forest','island'}<=land.subtypes and 'snow' in land.supertypes for land in defending_land_characteristics(engine,'B')))
        self.assertIn('Snow',engine._effective_card_data(engine.state.cards[snow.object_id])['type_line'])
        self.replay(session,load=True)


class QualifiedLandwalkValueTests(unittest.TestCase):
    def test_missing_keyword_index_uses_only_current_printed_declarations(self):
        from types import SimpleNamespace
        from quorune.rules.current_face_keywords import current_printed_face_keywords
        def projected(*,trusted=True,template='printed-keyword-list-v1',face='front',coverage=('snow forestwalk',),copy=False,token=False,base=('Flying',)):
            card=SimpleNamespace(annotations={'copy_overrides':{}} if copy else {},is_token=token,object_kind='card',zone='battlefield',active_face=None)
            record=SimpleNamespace(oracle_id='qualified-diagnostic',faces=())
            program=SimpleNamespace(provenance={'template_id':template,'face_id':face},coverage=coverage)
            host=SimpleNamespace(semantics=SimpleNamespace(programs_for_oracle=lambda _:[program]),semantic_program_is_current_trusted=lambda _:trusted)
            return current_printed_face_keywords(host,card,record,{'keywords':list(base)})
        self.assertEqual(['Flying','Snow Forestwalk'],projected())
        for change in ({'trusted':False},{'template':'conditional-keyword-grant'},
                {'face':'back'},{'coverage':('snow nonbasic landwalk',)},{'copy':True},{'token':True}):
            with self.subTest(change=change):self.assertEqual(['Flying'],projected(**change))
        self.assertEqual(['Flying','snow forestwalk'],projected(base=('Flying','snow forestwalk')))

    def test_qualified_landwalk_snapshots_and_legacy_boundaries(self):
        from quorune.landwalk import LandwalkLandCharacteristics as Row,landwalk_block_verdict
        def row(subtypes=(),supertypes=(),types=('land',)):
            return Row(frozenset(types),frozenset(subtypes),frozenset(supertypes))
        def verdict(keyword,*lands):return landwalk_block_verdict(frozenset({keyword,'landwalk'}),tuple(lands))
        snow_forest=row(('forest',),('snow','basic'))
        self.assertFalse(verdict('snow forestwalk',snow_forest).allowed)
        self.assertTrue(verdict('snow forestwalk',row(('island',),('snow','basic')),row(('forest',),('basic',))).allowed)
        cases=(('desertwalk',row(('desert',))),('artifact landwalk',row(types=('artifact','land'))),
            ('legendary landwalk',row(supertypes=('legendary',))),('nonbasic landwalk',row(('forest',))),
            ('snow landwalk',snow_forest),('snow plainswalk',row(('plains',),('snow',))),
            ('snow islandwalk',row(('island',),('snow',))),('snow swampwalk',row(('swamp',),('snow',))),
            ('snow mountainwalk',row(('mountain',),('snow',))))
        for keyword,land in cases:
            with self.subTest(keyword=keyword):
                self.assertFalse(verdict(keyword,land).allowed)
                self.assertTrue(verdict(keyword,row(('island',),('basic',))).allowed)
        self.assertTrue(verdict('nonbasic landwalk',snow_forest).allowed)
        with self.assertRaises(LandwalkRuleError):row(types=('artifact',))
        for keyword in ('dreamwalk','landwalk','snow nonbasic landwalk'):
            with self.subTest(unknown=keyword),self.assertRaises(LandwalkRuleError):
                landwalk_block_verdict(frozenset({keyword}),())
        with self.assertRaises(LandwalkRuleError):basic_landwalk_block_verdict(frozenset({'nonbasic landwalk'}),frozenset())
        mixed=(row(('island',),('snow',)),row(('forest',),('basic',)))
        self.assertEqual('attacker_has_flying',combat_evasion_verdict(
            CombatantEvasionCharacteristics(frozenset({'flying','snow forestwalk'}),frozenset(),frozenset({'creature'}),1),
            CombatantEvasionCharacteristics(frozenset(),frozenset(),frozenset({'creature'}),2),mixed).reason)


class LandwalkAsThoughRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/'landwalk-as-though.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/landwalk-as-though-cards.json',
            ROOT/'tests/fixtures/qualified-landwalk-cards.json',ROOT/'tests/fixtures/bound-effect-program-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Landwalk permissions',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    register_fixture_cards_on_add=True
    import test_bound_effect_programs as _bound
    session=_bound.BoundEffectProgramRuntimeTests.session
    add=_bound.BoundEffectProgramRuntimeTests.add
    ready=_bound.BoundEffectProgramRuntimeTests.ready
    resolve=_bound.BoundEffectProgramRuntimeTests.resolve
    checkpoint=_bound.BoundEffectProgramRuntimeTests.checkpoint
    replay=_bound.BoundEffectProgramRuntimeTests.replay

    def test_actual_staff_is_fully_admitted_for_native_landwalk_permissions(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        p=compile_best_available_card_program(self.db,self.db.lookup('Staff of the Ages'),semantic_registry=SemanticRegistry(),
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(p,capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'],'Original Staff must be fully admitted')

    def test_ten_as_though_originals_bind_as_complete_programs(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        for name in ('Crevasse','Deadfall','Gosta Dirk','Great Wall','Lord Magnus','Quagmire',
                'Staff of the Ages','Street Savvy','Undertow','Ur-Drago'):
            with self.subTest(name=name):
                p=compile_best_available_card_program(self.db,self.db.lookup(name),semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry,capability_profile='commander_review')
                self.assertTrue(bind_card_program_runtime(p,capability_registry=self.registry,
                    profile='commander_review')['strict_capability_ready'],name)

    def test_actual_staff_changes_only_landwalk_block_legality_before_damage(self):
        from quorune.engine import TURN_STEPS
        self.test_actual_staff_is_fully_admitted_for_native_landwalk_permissions()
        for index,present in enumerate((False,True)):
            with self.subTest(staff=present):
                session=self.session(530010+index);engine=session.engine
                attacker=self.add(engine,'Rime Dryad',ref='as-though-walker')
                ordinary=self.add(engine,'Grizzly Bears',ref='as-though-ordinary')
                blocker=self.add(engine,'Grizzly Bears',seat='B',ref='as-though-blocker')
                self.add(engine,'Snow-Covered Forest',seat='B')
                private=self.add(engine,'Generic Bound Body',seat='D',zone='hand',ref='as-though-private')
                if present:self.add(engine,'Staff of the Ages',seat='C')
                engine.state.started=True;engine.state.active_player='A'
                engine.state.phase='combat';engine.state.step='declare_blockers'
                engine.state.phase_index=TURN_STEPS.index(('combat','declare_blockers'))
                attacker.attacking='B';ordinary.attacking='B'
                engine.state.combat=CombatState(attackers_declared=True,had_attacking_creature=True,
                    attackers={attacker.object_id:'B',ordinary.object_id:'B'},defending_players=['B'])
                engine._begin_blocker_decisions();self.checkpoint(session)
                self.assertIn('snow forestwalk',engine._combat_keywords(attacker))
                legal=session.packet('pilot:B',full=True)['decision']['ctx']['legal_blocks'][blocker.ref]
                self.assertEqual(present,attacker.ref in legal)
                self.assertNotIn(private.ref,str(session.packet('pilot:C',full=True)))
                self.assertIn(private.ref,str(session.packet('pilot:D',full=True)))
                session=self.replay(session,load=True);engine=session.engine
                before=authoritative_state_hash(engine.state)
                self.assertFalse(session.act('pilot:C',{'a':'block','blk':{blocker.ref:attacker.ref}}).ok)
                self.assertEqual(before,authoritative_state_hash(engine.state))
                if not present:
                    result=session.act('pilot:B',{'a':'block','blk':{blocker.ref:attacker.ref}})
                    self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
                chosen=attacker.ref if present else ordinary.ref
                result=session.act('pilot:B',{'a':'block','blk':{blocker.ref:chosen}})
                self.assertTrue(result.ok,result.summary)
                for _ in range(40):
                    if engine.state.phase=='postcombat_main':break
                    result=session.act(session.pending_principals()[0],{'action_id':'pass'})
                    self.assertTrue(result.ok,result.summary)
                self.assertEqual('postcombat_main',engine.state.phase)
                self.assertEqual(38 if present else 39,engine.state.players['B'].life)
                self.assertEqual('graveyard' if present else 'battlefield',engine.state.cards[attacker.object_id].zone)
                self.replay(session,load=True)

    def test_ignored_permission_omission_is_killed_after_original_admission(self):
        from unittest.mock import patch
        from contextlib import nullcontext
        with patch('quorune.combat_evasion_engine_adapter.current_landwalk_block_exceptions',return_value=frozenset()),patch.object(self,'subTest',side_effect=lambda **_:nullcontext()):
            with self.assertRaises(AssertionError):self.test_actual_staff_changes_only_landwalk_block_legality_before_damage()


    def cast_permission_control(self,session,card,targets=(),payment=None):
        while session.pending_principals()[0]!='pilot:A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offer=next(r for r in rows if r['id']=='cast:'+card.ref)
        result=session.act('pilot:A',{'action_id':offer['id'],'targets':list(targets),
            'pay':'manual','payment':payment or {}})
        self.assertTrue(result.ok,result.summary);return self.resolve(session)

    def test_actual_quagmire_names_one_variant_and_keeps_flying_independent(self):
        from quorune.engine import TURN_STEPS
        from quorune.combat_evasion_engine_adapter import engine_combat_evasion_verdict
        session=self.session(530030);engine=session.engine
        source=self.add(engine,'Quagmire',zone='hand',ref='as-though-quagmire')
        jump=self.add(engine,'Jump',zone='hand',ref='as-though-jump')
        ordinary=self.add(engine,'Bog Wraith',ref='as-though-swampwalker')
        snow=self.add(engine,'Legions of Lim-Dûl',ref='as-though-snow-swampwalker')
        forest=self.add(engine,'Shanodin Dryads',ref='as-though-forestwalker')
        blocker=self.add(engine,'Grizzly Bears',seat='B',ref='as-though-ground-blocker')
        self.add(engine,'Snow-Covered Swamp',seat='B');self.add(engine,'Forest',seat='B')
        self.ready(session,source,{'C':2,'B':1,'U':1})
        engine.state.phase_index=TURN_STEPS.index(('precombat_main','main'));self.checkpoint(session)
        for attacker in (ordinary,snow,forest):self.assertFalse(engine_combat_evasion_verdict(engine,attacker,blocker,'B').allowed)
        self.cast_permission_control(session,source,payment={'C':2,'B':1})
        self.assertTrue(engine_combat_evasion_verdict(engine,ordinary,blocker,'B').allowed)
        self.assertIn('swampwalk',engine._combat_keywords(ordinary))
        for attacker in (snow,forest):self.assertFalse(engine_combat_evasion_verdict(engine,attacker,blocker,'B').allowed)
        self.cast_permission_control(session,jump,(ordinary.ref,),{'U':1})
        self.assertIn('swampwalk',engine._combat_keywords(ordinary));self.assertIn('flying',engine._combat_keywords(ordinary))
        self.assertEqual('attacker_has_flying',engine_combat_evasion_verdict(engine,ordinary,blocker,'B').reason)
        self.replay(session,load=True)

    def test_actual_global_creature_permission_ends_when_source_loses_ability(self):
        from quorune.engine import TURN_STEPS
        from quorune.combat_evasion_engine_adapter import engine_combat_evasion_verdict
        session=self.session(530040);engine=session.engine
        source=self.add(engine,'Ur-Drago',seat='C',ref='as-though-global-creature')
        attacker=self.add(engine,'Bog Wraith',ref='as-though-other-walker')
        blocker=self.add(engine,'Grizzly Bears',seat='B',ref='as-though-other-blocker')
        self.add(engine,'Swamp',seat='B')
        humble=self.add(engine,'Humble',zone='hand',ref='as-though-source-humble')
        self.ready(session,humble,{'C':1,'W':1})
        engine.state.phase_index=TURN_STEPS.index(('precombat_main','main'));self.checkpoint(session)
        self.assertTrue(engine_combat_evasion_verdict(engine,attacker,blocker,'B').allowed)
        self.cast_permission_control(session,humble,(source.ref,),{'C':1,'W':1})
        self.assertEqual('battlefield',engine.state.cards[source.object_id].zone)
        self.assertFalse(engine_combat_evasion_verdict(engine,engine.state.cards[attacker.object_id],engine.state.cards[blocker.object_id],'B').allowed)
        self.assertIn('swampwalk',engine._combat_keywords(engine.state.cards[attacker.object_id]))
        self.replay(session,load=True)

    def test_actual_street_savvy_is_a_source_rule_and_ends_with_its_aura(self):
        from quorune.engine import TURN_STEPS
        from quorune.combat_evasion_engine_adapter import engine_combat_evasion_verdict
        from quorune.landwalk_permission_model import LandwalkBlockPermission
        session=self.session(530050);engine=session.engine
        aura=self.add(engine,'Street Savvy',zone='hand',ref='as-though-street-savvy')
        humble=self.add(engine,'Humble',zone='hand',ref='as-though-recipient-humble')
        removal=self.add(engine,'Disenchant',zone='hand',ref='as-though-aura-removal')
        attacker=self.add(engine,'Rime Dryad',ref='as-though-savvy-attacker')
        blocker=self.add(engine,'Grizzly Bears',seat='B',ref='as-though-savvy-blocker')
        other=self.add(engine,'Grizzly Bears',seat='B',ref='as-though-unsavvy-blocker')
        self.add(engine,'Snow-Covered Forest',seat='B')
        self.ready(session,aura,{'G':1,'C':2,'W':2})
        engine.state.phase_index=TURN_STEPS.index(('precombat_main','main'));self.checkpoint(session)
        self.assertFalse(engine_combat_evasion_verdict(engine,attacker,blocker,'B').allowed)
        self.cast_permission_control(session,aura,(blocker.ref,),{'G':1})
        self.assertEqual(4,engine._numeric_stat(blocker.object_id,'toughness'))
        self.assertTrue(engine_combat_evasion_verdict(engine,attacker,blocker,'B').allowed)
        self.assertFalse(engine_combat_evasion_verdict(engine,attacker,other,'B').allowed)
        self.assertFalse(any(isinstance(f,LandwalkBlockPermission) for f in engine._effective_ability_fragments(blocker)))
        self.cast_permission_control(session,humble,(blocker.ref,),{'C':1,'W':1})
        self.assertEqual(3,engine._numeric_stat(blocker.object_id,'toughness'))
        self.assertTrue(engine_combat_evasion_verdict(engine,attacker,blocker,'B').allowed)
        self.cast_permission_control(session,removal,(aura.ref,),{'C':1,'W':1})
        self.assertEqual('graveyard',engine.state.cards[aura.object_id].zone)
        self.assertEqual(1,engine._numeric_stat(blocker.object_id,'toughness'))
        self.assertFalse(engine_combat_evasion_verdict(engine,attacker,blocker,'B').allowed)
        self.assertIn('snow forestwalk',engine._combat_keywords(attacker))
        self.replay(session,load=True)


    def test_actual_street_permission_does_not_follow_blinked_blocker_incarnation(self):
        from quorune.engine import TURN_STEPS
        from quorune.combat_evasion_engine_adapter import engine_combat_evasion_verdict
        session=self.session(530060);engine=session.engine
        aura=self.add(engine,'Street Savvy',zone='hand',ref='as-though-blink-aura')
        blink=self.add(engine,'Flicker of Fate',zone='hand',ref='as-though-blink')
        attacker=self.add(engine,'Rime Dryad',ref='as-though-blink-attacker')
        blocker=self.add(engine,'Grizzly Bears',seat='B',ref='as-though-returning-blocker')
        self.add(engine,'Snow-Covered Forest',seat='B')
        self.ready(session,aura,{'G':1,'C':1,'W':1})
        engine.state.phase_index=TURN_STEPS.index(('precombat_main','main'));self.checkpoint(session)
        self.cast_permission_control(session,aura,(blocker.ref,),{'G':1})
        self.assertTrue(engine_combat_evasion_verdict(engine,attacker,blocker,'B').allowed)
        session=self.replay(session,load=True);engine=session.engine
        previous=engine.state.cards[blocker.object_id].logical_object_id
        self.cast_permission_control(session,blink,(blocker.ref,),{'C':1,'W':1})
        current=engine.state.cards[blocker.object_id]
        self.assertEqual('battlefield',current.zone)
        self.assertNotEqual(previous,current.logical_object_id)
        self.assertEqual('graveyard',engine.state.cards[aura.object_id].zone)
        self.assertFalse(engine_combat_evasion_verdict(engine,engine.state.cards[attacker.object_id],current,'B').allowed)
        self.assertEqual(2,engine._numeric_stat(current.object_id,'toughness'))
        self.replay(session,load=True)


    def test_actual_copied_source_permission_survives_original_and_obeys_copy_ability_loss(self):
        from quorune.engine import TURN_STEPS
        from quorune.combat_evasion_engine_adapter import engine_combat_evasion_verdict
        session=self.session(530070);engine=session.engine
        original=self.add(engine,'Ur-Drago',seat='C',ref='as-though-copy-original')
        copier=self.add(engine,'Clone',zone='hand',ref='as-though-copy')
        bounce=self.add(engine,'Unsummon',zone='hand',ref='as-though-copy-bounce')
        humble=self.add(engine,'Humble',zone='hand',ref='as-though-copy-humble')
        attacker=self.add(engine,'Bog Wraith',ref='as-though-copy-walker')
        blocker=self.add(engine,'Grizzly Bears',seat='B',ref='as-though-copy-blocker')
        self.add(engine,'Swamp',seat='B')
        self.ready(session,copier,{'C':4,'U':2,'W':1})
        engine.state.phase_index=TURN_STEPS.index(('precombat_main','main'));self.checkpoint(session)
        decision=self.cast_permission_control(session,copier,payment={'C':3,'U':1})
        self.assertEqual('replacement.order',decision.kind)
        session=self.replay(session,load=True);engine=session.engine
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        chosen=next(r for r in options if original.ref in r['id'] and not r['id'].startswith('decline:'))
        result=session.act('pilot:A',{'action_id':'choose','replacement':chosen['id']})
        self.assertTrue(result.ok,result.summary);self.resolve(session)
        self.assertEqual('battlefield',engine.state.cards[copier.object_id].zone)
        self.cast_permission_control(session,bounce,(original.ref,),{'U':1})
        self.assertEqual('hand',engine.state.cards[original.object_id].zone)
        self.assertTrue(engine_combat_evasion_verdict(engine,engine.state.cards[attacker.object_id],engine.state.cards[blocker.object_id],'B').allowed)
        self.cast_permission_control(session,humble,(copier.ref,),{'C':1,'W':1})
        self.assertEqual('battlefield',engine.state.cards[copier.object_id].zone)
        self.assertFalse(engine_combat_evasion_verdict(engine,engine.state.cards[attacker.object_id],engine.state.cards[blocker.object_id],'B').allowed)
        self.assertIn('swampwalk',engine._combat_keywords(engine.state.cards[attacker.object_id]))
        self.replay(session,load=True)


class LandwalkAsThoughValueTests(unittest.TestCase):
    def test_source_permission_schema_and_exact_keyword_matching_are_closed(self):
        from dataclasses import replace
        from quorune.landwalk_permission_model import LandwalkBlockPermission
        from quorune.ability_fragments import ability_fragment_to_dict,ability_fragment_from_dict
        from quorune.landwalk import LandwalkLandCharacteristics,landwalk_block_verdict,LANDWALK_KEYWORDS
        for spec in (LandwalkBlockPermission(),LandwalkBlockPermission(keyword='swampwalk'),LandwalkBlockPermission(scope='attached_blocker')):
            self.assertEqual(spec,ability_fragment_from_dict(ability_fragment_to_dict(spec)))
        spec=LandwalkBlockPermission()
        for change in ({'schema_version':True},{'scope':'attached_attacker'},{'keyword':'Swampwalk'},{'keyword':'dreamwalk'}):
            with self.subTest(change=change),self.assertRaises(ValueError):replace(spec,**change)
        snow=LandwalkLandCharacteristics(frozenset({'land'}),frozenset({'swamp','forest'}),frozenset({'snow'}))
        keywords=frozenset({'landwalk','snow swampwalk','swampwalk','forestwalk'})
        self.assertEqual(('forestwalk','snow swampwalk'),landwalk_block_verdict(keywords,(snow,),ignored_keywords=frozenset({'swampwalk'})).matching_keywords)
        self.assertTrue(landwalk_block_verdict(keywords,(snow,),ignored_keywords=frozenset(LANDWALK_KEYWORDS)).allowed)
        self.assertIn('snow swampwalk',keywords)
        with self.assertRaises(LandwalkRuleError):landwalk_block_verdict(frozenset({'dreamwalk'}),(snow,),ignored_keywords=frozenset(LANDWALK_KEYWORDS))
        flying=CombatantEvasionCharacteristics(frozenset({'flying','forestwalk'}),frozenset(),frozenset({'creature'}),2)
        ground=CombatantEvasionCharacteristics(frozenset(),frozenset(),frozenset({'creature'}),2)
        self.assertEqual('attacker_has_flying',combat_evasion_verdict(flying,ground,(snow,),ignored_landwalk_keywords=frozenset(LANDWALK_KEYWORDS)).reason)
        self.assertTrue(basic_landwalk_block_verdict(frozenset({'forestwalk'}),frozenset({'forest'}),ignored_keywords=frozenset({'forestwalk'})).allowed)


if __name__ == "__main__":
    unittest.main()

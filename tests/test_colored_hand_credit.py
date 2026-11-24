"""Native price, privacy, rollback, and replay for colored hand-exile credit."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from common import ROOT
import test_qualified_hexproof as native
from test_controller_creature_keyword_absence import register_card
from quorune.carddb import CardDatabase
from quorune.compiler.colored_hand_credit_template import colored_hand_credit_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.colored_hand_credit import COLORED_HAND_CREDIT_DEPENDENCIES, ColoredHandCredit
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


SPELL = "Coalglass Petition"
RESOURCE = "Soot Ledger"
TAX = "Cinder Customs"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class ColoredHandCreditTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.QualifiedHexproofTests.session
    add = native.QualifiedHexproofTests.add
    prepare = native.QualifiedHexproofTests.prepare
    checkpoint = native.QualifiedHexproofTests.checkpoint
    resolve = native.QualifiedHexproofTests.resolve
    replay = native.QualifiedHexproofTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        path = Path(cls.temp.name) / "colored-hand-credit.sqlite3"
        build_fixture_database([FIXTURES / "bound-effect-program-cards.json",
                                FIXTURES / "colored-hand-credit-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private colored credit", [
            DeckEntry("Generic Bound Commander",1,"commander"), DeckEntry("Generic Bound Plains",30)
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def ready(self, seed, count, *, tax=False):
        session = self.session(seed)
        spell = self.add(session.engine, SPELL, zone="hand")
        cards = [self.add(session.engine, RESOURCE, zone="hand", ref=f"veil-credit-{i}") for i in range(count)]
        foreign = self.add(session.engine, RESOURCE, seat="D", zone="hand", ref="credit-foreign-hand")
        white = self.add(session.engine, "Generic Bound Growth", zone="hand", ref="credit-wrong-color")
        target = self.add(session.engine,"Generic Bound Body",seat="B",ref="colored-credit-target")
        if tax:
            self.add(session.engine,TAX,ref="colored-credit-tax")
        self.prepare(session, mana={"B":1})
        self.checkpoint(session)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"]=="cast:"+spell.ref)
        return session, spell, cards, foreign, white, target, action

    def cast_response(self, action, target, *, x, objects):
        return {"action_id":action["id"],"targets":[target.ref],"x":x,"exile_cards":[card.ref for card in objects],
                "pay":"manual","payment":{"B":1}}

    def test_credit_reserves_private_refs_pays_tax_and_allows_excess_without_losing_x(self):
        session, spell, cards, foreign, white, body, action = self.ready(601_281,12,tax=True)
        quote = action["cost_options"][0]
        choose = quote["choice_schema"]["exile_cards"]
        self.assertEqual((0,12), (choose["minimum"],choose["maximum"]))
        self.assertEqual({card.ref for card in cards}, set(choose["legal_refs"]))
        self.assertEqual(23,quote["choice_schema"]["x"]["maximum"])
        self.assertEqual(1, quote["requirements"]["B"])
        for seat in "BCD":
            seen = json.dumps(session.packet("pilot:"+seat,full=True))
            self.assertTrue(all(card.ref not in seen for card in cards))
        response = self.cast_response(action,body,x=1,objects=cards)
        sent = session.act("pilot:A",response)
        self.assertTrue(sent.ok,sent.summary)
        self.assertEqual(session.state.stack[-1].x_value,1)
        self.assertEqual(session.state.cards[spell.object_id].zone,"stack")
        self.assertTrue(all(session.state.cards[obj.object_id].zone=="exile" for obj in cards))
        self.assertEqual(session.state.cards[foreign.object_id].zone,"hand")
        self.assertEqual(session.state.cards[white.object_id].zone,"hand")
        self.assertEqual(session.state.players["A"].mana_pool["B"],0)
        session = self.replay(session,load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(session.state.cards[body.object_id].marked_damage, 1)
        self.assertEqual(session.state.players["A"].life, 41)
        self.replay(session,load=True)

    def test_invalid_credit_refs_and_unpaid_color_roll_back_then_zero_selection_casts(self):
        session, spell, cards, foreign, white, body, action = self.ready(601_282,1)
        before = authoritative_state_hash(session.state)
        for x, refs in ((1,[foreign]),(1,[white]),(1,[spell]),(1,[cards[0],cards[0]]),(1,[])):
            result = session.act("pilot:A",self.cast_response(action,body,x=x,objects=refs))
            self.assertFalse(result.ok,result.summary)
            self.assertEqual(authoritative_state_hash(session.state),before)
        nonblack = dict(self.cast_response(action,body,x=1,objects=cards),payment={"C":1})
        self.assertFalse(session.act("pilot:A",nonblack).ok)
        self.assertEqual(authoritative_state_hash(session.state),before)
        self.assertTrue(session.act("pilot:A",self.cast_response(action,body,x=0,objects=[])).ok)
        self.assertEqual(session.state.cards[cards[0].object_id].zone,"hand")
        self.assertEqual(session.state.stack[-1].x_value,0)
        self.replay(session,load=True)

    def test_exact_hand_credit_shape_and_dependency_withdrawal_keep_the_source_fail_closed(self):
        source = self.db.lookup(SPELL)
        clause = source.oracle_text.splitlines()[0]
        template = colored_hand_credit_template(clause)
        self.assertIsNotNone(template)
        self.assertEqual(template.spec,ColoredHandCredit.from_dict(template.spec.to_dict()))
        for invalid in (clause.replace("{2}","{3}"),clause.replace("you may exile","exile"),
                        clause.replace("from your hand","from your graveyard"),clause+" Draw a card."):
            self.assertIsNone(colored_hand_credit_template(invalid))
        binding, _ = register_card(self.db,self.registry,source.name)
        self.assertTrue(binding["strict_capability_ready"],binding["blockers"])
        raw = json.loads((ROOT/"quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT/"platform/capability-evidence-declarations.json")
        ids = set(discover_tests(ROOT).values())
        for required in COLORED_HAND_CREDIT_DEPENDENCIES:
            data = json.loads(json.dumps(raw))
            next(row for row in data["capabilities"] if row["id"]==required).update(
                status="blocked",blockers=["Exact hand credit scope withdrawn."])
            registry = CapabilityRegistry(data)
            index = build_index(registry_value=data,declaration_source=declarations,discovered_test_ids=ids)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual(compile_oracle_card(source,capability_registry=registry,
                                                    capability_profile=PROFILE).status,"exact")
        with patch("quorune.compiler.spell_additional_cost_nodes.colored_hand_credit_template",return_value=None):
            self.assertNotEqual(compile_oracle_card(source,capability_registry=self.registry,
                                                    capability_profile=PROFILE).status,"exact")

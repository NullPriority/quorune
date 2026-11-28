"""Native target, payment and lifetime evidence for a temporary Escape grant."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_underworld_breach_native as breach
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.casting.escape_grants import compiled_granted_escape_permission
from quorune.temporary_graveyard_escape import TEMPORARY_ESCAPE_CAPABILITY, TEMPORARY_ESCAPE_STATS_FIELD
from scripts.build_test_database import build_fixture_database


CARD = "Confession Dial"
ORACLE_ID = "6316158c-2f66-4dc4-b78c-c6ff7c39bb77"


class TargetGraveyardEscapeTests(InheritedNativeFixture, unittest.TestCase):
    accept = breach.UnderworldBreachNativeTests.accept
    reject = breach.UnderworldBreachNativeTests.reject
    escape_offers = breach.UnderworldBreachNativeTests.escape_offers
    escape_command = breach.UnderworldBreachNativeTests.escape_command

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "target-escape.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/inherited-rule-composition-cards.json",
            ROOT / "tests/fixtures/target-graveyard-escape-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Temporary Escape", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 20),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_surveil_and_rejects_a_changed_escape_scope(self):
        original = self.db.by_oracle_id(ORACLE_ID)

        def compile_card(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")

        value = compile_card(original)
        self.assertEqual("exact", value.status, value.material_residuals)
        nodes = value.faces[0].nodes
        self.assertTrue(any(effect.get("op") == "surveil" and effect.get("count", effect.get("amount")) == 3
                            for node in nodes for effect in node.effects))
        grant, = (node for node in nodes if TEMPORARY_ESCAPE_CAPABILITY in node.capability_dependencies)
        self.assertEqual("activate", grant.event)
        self.assertEqual(("legendary",), tuple(grant.target_schema["supertypes_any"]))
        for old, new in (("legendary creature", "creature"), ("exile three", "exile four"),
                         ("until end of turn", "until your next turn")):
            self.assertNotEqual("exact", compile_card(replace(original, oracle_text=original.oracle_text.replace(old,new))).status)
        with patch("quorune.temporary_graveyard_escape.targeted_escape_template", return_value=None):
            self.assertNotEqual("exact", compile_card(original).status)

        import json
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == "casting.lifecycle.source_granted_escape").update(
            status="blocked", blockers=["Withdraw the ordinary Escape payment authority"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        disabled = compile_oracle_card(original, capability_registry=registry, capability_profile="commander_review")
        self.assertNotEqual("exact", disabled.status)

    def setup_graveyard(self, seed):
        game = self.session(seed)
        host = game.engine
        dial = self.add(host, CARD, ref="dial-source")
        legal = self.add(host, "Isamaru, Hound of Konda", zone="graveyard", ref="dial-legendary-target")
        nonlegend = self.add(host, "Grizzly Bears", zone="graveyard", ref="dial-nonlegendary")
        other = self.add(host, "Isamaru, Hound of Konda", seat="B", zone="graveyard", ref="dial-opposing")
        fodder = [self.add(host, "Generic Bound Plains", zone="graveyard", ref=f"dial-price-{index}") for index in range(4)]
        self.add(host, "Generic Bound Plains", ref="dial-white-payment")
        self.add(host, "Generic Bound Plains", seat="C", ref="dial-opponent-white")
        self.add(host, "Wastes", seat="C", ref="dial-opponent-generic")
        remove = self.add(host, "Disenchant", seat="C", zone="hand", ref="dial-source-removal")
        self.assert_closed(game, dial)
        self.assert_closed(game, legal)
        self.prepare(game)
        self.checkpoint(game)
        return game, dial, legal, nonlegend, other, fodder, remove

    def grant(self, game, dial, legal, *, illegal=()):
        self.priority(game, "A")
        action, = (row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith(f"activate:{dial.ref}:"))
        for target in illegal:
            self.reject(game, "A", {"action_id":action["id"], "targets":[target.ref], "pay":"auto"})
            self.assertFalse(game.state.cards[dial.object_id].tapped)
        self.accept(game, "A", {"action_id":action["id"], "targets":[legal.ref], "pay":"auto"})
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[dial.object_id].tapped)

    def test_actual_target_grant_survives_source_departure_and_pays_three_other_cards(self):
        game, dial, legal, ordinary, opposing, fodder, removal = self.setup_graveyard(57661581)
        self.grant(game, dial, legal, illegal=(ordinary,opposing))
        self.assertFalse(compiled_granted_escape_permission(game.engine,"A",ordinary))
        self.cast(game,"C",removal,[dial.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[dial.object_id].zone)
        game=self.replay(game,load=True)
        offer,=self.escape_offers(game,legal)
        self.reject(game,"A",self.escape_command(offer,[legal,*fodder[:2]]))
        self.accept(game,"A",self.escape_command(offer,fodder[:3]))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[legal.object_id].zone)
        self.assertEqual(["exile","exile","exile","graveyard"],
                         [game.state.cards[card.object_id].zone for card in fodder])
        self.replay(game,load=True)

    def test_actual_cleanup_expires_a_still_present_original_target(self):
        game, dial, legal, _, _, _, _ = self.setup_graveyard(57661582)
        self.grant(game,dial,legal)
        self.assertTrue(self.escape_offers(game,legal))
        current_turn=game.state.turn_sequence
        self.until(game,lambda seen:seen.state.turn_sequence > current_turn)
        self.assertEqual("graveyard",game.state.cards[legal.object_id].zone)
        self.assertNotIn(TEMPORARY_ESCAPE_STATS_FIELD,game.state.players["A"].stats)
        self.assertFalse(compiled_granted_escape_permission(game.engine,"A",game.state.cards[legal.object_id]))
        self.replay(game,load=True)

"""Common native fixture for fixed resolution mana compositions."""
import tempfile
from pathlib import Path
from common import ROOT
import inherited_composition_fixture as actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

class ManaCompositionFixture(actions.InheritedNativeFixture):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'mana-compositions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/mana-component-composition-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry();cls.deck=DeckDefinition('Mana components',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])

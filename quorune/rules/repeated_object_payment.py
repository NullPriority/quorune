"""Version-twelve repeated object prices preserve historical codecs."""
from ..object_predicate import ObjectQuerySpec

REPEATED_OBJECT_PAYMENT_VERSION=12
REPEATED_OBJECT_PAYMENT_CAPABILITY='cost.object.repeated_resolution'


def validate_repeated_object_payment(spec):
    if type(spec.schema_version)is not int or spec.schema_version!=REPEATED_OBJECT_PAYMENT_VERSION or type(spec.amount)is not int or spec.amount<=0:
        raise ValueError('Repeated object price requires version twelve and a positive amount')
    if spec.requirements is not None or spec.counter_name is not None or spec.source_ref is not None or spec.hybrid_symbols!=() or not isinstance(spec.predicate,ObjectQuerySpec):
        raise ValueError('Repeated object price carries another cost or lacks its predicate')
    p=spec.predicate
    if spec.kind=='discard' and type(p.owner)is str and p.owner:
        expected=ObjectQuerySpec(zones=('hand',),owner=p.owner)
    elif spec.kind=='sacrifice' and type(p.controller)is str and p.controller and len(p.types_all)==1 and p.types_all[0] in {'artifact','battle','creature','enchantment','land','planeswalker'}:
        expected=ObjectQuerySpec(zones=('battlefield',),controller=p.controller,types_all=p.types_all)
    elif spec.kind == 'sacrifice' and spec.amount == 2 and type(p.controller) is str and p.controller and not p.types_all:
        expected = ObjectQuerySpec(zones=('battlefield',), controller=p.controller)
    elif spec.kind == 'sacrifice' and spec.amount == 3 and type(p.controller) is str and p.controller:
        expected = ObjectQuerySpec(zones=('battlefield',), controller=p.controller, excluded_types=('land',))
    else:raise ValueError('Repeated object cost is outside its closed hand/permanent family')
    if p!=expected:raise ValueError('Repeated object price changed its selection domain')

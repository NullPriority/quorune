"""Lower closed public characteristic sets to ordinary event comparisons."""


def public_event_query_conditions(query, *, prefix):
    conditions = []
    for field, values, operation, excluded in (
        ('types', query.types_all, 'contains_all', False),
        ('types', query.types_any, 'contains_any', False),
        ('types', query.excluded_types, 'contains_any', True),
        ('subtypes', query.subtypes_all, 'contains_all', False),
        ('subtypes', query.subtypes_any, 'contains_any', False),
        ('subtypes', query.excluded_subtypes, 'contains_any', True),
        ('colors', query.colors_all, 'contains_all', False),
        ('colors', query.colors_any, 'contains_any', False),
        ('supertypes', query.supertypes_all, 'contains_all', False),
        ('keywords', query.keywords_all, 'contains_all', False),
        ('keywords', query.keywords_none, 'contains_any', True),
    ):
        if not values: continue
        if operation == 'contains_all':
            conditions.extend({'field': prefix + field, 'op': 'contains_any', 'value': [value]} for value in values)
        else:
            condition = {'field': prefix + field, 'op': operation, 'value': list(values)}
            conditions.append({'not': condition} if excluded else condition)
    if query.colorless is not None:
        condition = {'field': prefix + 'colors', 'op': 'eq', 'value': []}
        conditions.append(condition if query.colorless else {'not': condition})
    return conditions

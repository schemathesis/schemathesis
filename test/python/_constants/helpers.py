def pool_values(pool, type_):
    return tuple(entry.value for entry in pool.entries_for(type_))

def add_driver(conn, name):
    cur = conn.execute("INSERT INTO drivers (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def list_drivers(conn, only_active=False):
    sql = "SELECT * FROM drivers"
    if only_active:
        sql += " WHERE active=1"
    sql += " ORDER BY id"
    return [dict(r) for r in conn.execute(sql)]


def set_driver_active(conn, driver_id, active):
    conn.execute("UPDATE drivers SET active=? WHERE id=?",
                 (1 if active else 0, driver_id))
    conn.commit()


def get_driver(conn, driver_id):
    row = conn.execute("SELECT * FROM drivers WHERE id=?",
                       (driver_id,)).fetchone()
    return dict(row) if row else None

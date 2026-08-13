def add_driver(conn, name, business_id):
    cur = conn.execute(
        "INSERT INTO drivers (name, business_id) VALUES (?, ?)",
        (name, business_id))
    conn.commit()
    return cur.lastrowid


def list_drivers(conn, business_id, only_active=False):
    sql = "SELECT * FROM drivers WHERE business_id=?"
    if only_active:
        sql += " AND active=1"
    sql += " ORDER BY id"
    return [dict(r) for r in conn.execute(sql, (business_id,))]


def set_driver_active(conn, driver_id, active, business_id):
    conn.execute("UPDATE drivers SET active=? WHERE id=? AND business_id=?",
                 (1 if active else 0, driver_id, business_id))
    conn.commit()


def get_driver(conn, driver_id, business_id):
    row = conn.execute(
        "SELECT * FROM drivers WHERE id=? AND business_id=?",
        (driver_id, business_id)).fetchone()
    return dict(row) if row else None


def rename_driver(conn, driver_id, name, business_id):
    cur = conn.execute(
        "UPDATE drivers SET name=? WHERE id=? AND business_id=?",
        (name, driver_id, business_id))
    conn.commit()
    return cur.rowcount > 0

ALLOWED_PAY_MODELS = ("per_package", "per_day")


def add_driver(conn, name, business_id, pay_model="per_package",
               pay_rate=0.0):
    if pay_model not in ALLOWED_PAY_MODELS:
        raise ValueError("unknown pay model: %s" % pay_model)
    cur = conn.execute(
        "INSERT INTO drivers (name, business_id, pay_model, pay_rate) "
        "VALUES (?, ?, ?, ?)", (name, business_id, pay_model, pay_rate))
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


def update_driver_pay(conn, driver_id, business_id, pay_model, pay_rate):
    """Change what a driver is paid going forward.

    Deliberately does not touch any logged day. Each entry froze the
    driver's rate when it was saved, so a raise today cannot reprice
    work done last month.
    """
    if pay_model not in ALLOWED_PAY_MODELS:
        raise ValueError("unknown pay model: %s" % pay_model)
    cur = conn.execute(
        "UPDATE drivers SET pay_model=?, pay_rate=? "
        "WHERE id=? AND business_id=?",
        (pay_model, pay_rate, driver_id, business_id))
    conn.commit()
    return cur.rowcount > 0

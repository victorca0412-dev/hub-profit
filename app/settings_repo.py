ALLOWED_SETTINGS = {
    "gas_price_per_gal", "vehicle_year", "vehicle_make", "vehicle_model",
    "vehicle_mpg", "track_hours",
}


def get_settings(conn):
    """The global settings row: vehicle, fuel, and display preferences.

    Pay rate and driver mode live on the business, not here.
    """
    return dict(conn.execute("SELECT * FROM settings WHERE id=1").fetchone())


def update_settings(conn, values):
    fields = {k: v for k, v in values.items() if k in ALLOWED_SETTINGS}
    if not fields:
        return
    assignments = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE settings SET {assignments} WHERE id=1",
                 list(fields.values()))
    conn.commit()


def get_active_business_id(conn):
    return conn.execute(
        "SELECT active_business_id FROM settings WHERE id=1").fetchone()[0]


def set_active_business(conn, business_id):
    conn.execute("UPDATE settings SET active_business_id=? WHERE id=1",
                 (business_id,))
    conn.commit()


def get_expense_config(conn, business_id):
    out = {}
    for r in conn.execute(
            "SELECT key, enabled, mode, amount FROM expense_config "
            "WHERE business_id=?", (business_id,)):
        out[r["key"]] = {
            "enabled": bool(r["enabled"]),
            "mode": r["mode"],
            "amount": r["amount"],
        }
    return out


def update_expense_config(conn, business_id, key, enabled=None, amount=None):
    if enabled is None and amount is None:
        return
    if enabled is not None:
        conn.execute("UPDATE expense_config SET enabled=? "
                     "WHERE business_id=? AND key=?",
                     (1 if enabled else 0, business_id, key))
    if amount is not None:
        conn.execute("UPDATE expense_config SET amount=? "
                     "WHERE business_id=? AND key=?",
                     (amount, business_id, key))
    conn.commit()

ALLOWED_SETTINGS = {
    "business_name", "pay_per_package", "gas_price_per_gal",
    "vehicle_year", "vehicle_make", "vehicle_model", "vehicle_mpg",
    "track_hours", "drivers_enabled",
}


def get_settings(conn):
    row = conn.execute("SELECT * FROM settings WHERE id=1").fetchone()
    return dict(row)


def update_settings(conn, values):
    fields = {k: v for k, v in values.items() if k in ALLOWED_SETTINGS}
    if not fields:
        return
    assignments = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE settings SET {assignments} WHERE id=1",
                 list(fields.values()))
    conn.commit()


def get_expense_config(conn):
    out = {}
    for r in conn.execute("SELECT key, enabled, mode, amount FROM expense_config"):
        out[r["key"]] = {
            "enabled": bool(r["enabled"]),
            "mode": r["mode"],
            "amount": r["amount"],
        }
    return out


def update_expense_config(conn, key, enabled=None, amount=None):
    if enabled is None and amount is None:
        return
    if enabled is not None:
        conn.execute("UPDATE expense_config SET enabled=? WHERE key=?",
                     (1 if enabled else 0, key))
    if amount is not None:
        conn.execute("UPDATE expense_config SET amount=? WHERE key=?",
                     (amount, key))
    conn.commit()

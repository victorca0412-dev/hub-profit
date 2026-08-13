import json

from app.settings_repo import get_settings, get_expense_config


def _row_to_entry(row):
    if row is None:
        return None
    e = dict(row)
    e["snap_expense_config"] = json.loads(e["snap_expense_config"])
    return e


def create_entry(conn, data, business_id):
    s = get_settings(conn)
    cfg = get_expense_config(conn, business_id)
    rate = conn.execute(
        "SELECT pay_per_package FROM businesses WHERE id=?",
        (business_id,)).fetchone()[0]
    cur = conn.execute(
        """INSERT INTO daily_entries
           (business_id, date, driver_id, packages, miles, hours,
            extra_expense, note, snap_pay_per_package, snap_gas_price,
            snap_mpg, snap_expense_config)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (business_id, data["date"], data.get("driver_id"), data["packages"],
         data.get("miles", 0.0), data.get("hours"), data.get("extra_expense"),
         data.get("note"), rate, s["gas_price_per_gal"], s["vehicle_mpg"],
         json.dumps(cfg)),
    )
    conn.commit()
    return cur.lastrowid


def get_entry(conn, entry_id, business_id):
    row = conn.execute(
        "SELECT * FROM daily_entries WHERE id=? AND business_id=?",
        (entry_id, business_id)).fetchone()
    return _row_to_entry(row)


def list_entries(conn, start_date, end_date, business_id):
    rows = conn.execute(
        "SELECT * FROM daily_entries WHERE business_id=? "
        "AND date >= ? AND date <= ? ORDER BY date DESC, id DESC",
        (business_id, start_date, end_date)).fetchall()
    return [_row_to_entry(r) for r in rows]


def update_entry(conn, entry_id, data, business_id):
    """Update the user-entered fields of an entry.

    Deliberately does not touch any snap_* column. The frozen rates are what
    keep past days correct when settings change later, so correcting a typo
    must never reprice the day. Returns True if a row matched.
    """
    cur = conn.execute(
        """UPDATE daily_entries
           SET date=?, driver_id=?, packages=?, miles=?, hours=?,
               extra_expense=?, note=?
           WHERE id=? AND business_id=?""",
        (data["date"], data.get("driver_id"), data["packages"],
         data.get("miles", 0.0), data.get("hours"), data.get("extra_expense"),
         data.get("note"), entry_id, business_id),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_entry(conn, entry_id, business_id):
    conn.execute("DELETE FROM daily_entries WHERE id=? AND business_id=?",
                 (entry_id, business_id))
    conn.commit()


def distinct_workdays_in_month(conn, year_month, business_id):
    """Count distinct dates worked in a calendar month, e.g. '2026-06'.

    Scoped per business: a day worked under Hub A is a workday for Hub A's
    book, and a day worked under both is a workday in each.
    """
    row = conn.execute(
        "SELECT COUNT(DISTINCT date) FROM daily_entries "
        "WHERE business_id=? AND substr(date, 1, 7) = ?",
        (business_id, year_month)).fetchone()
    return row[0]

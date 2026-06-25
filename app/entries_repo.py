import json
from app.settings_repo import get_settings, get_expense_config


def _row_to_entry(row):
    if row is None:
        return None
    e = dict(row)
    e["snap_expense_config"] = json.loads(e["snap_expense_config"])
    return e


def create_entry(conn, data):
    s = get_settings(conn)
    cfg = get_expense_config(conn)
    cur = conn.execute(
        """INSERT INTO daily_entries
           (date, driver_id, packages, miles, hours, extra_expense, note,
            snap_pay_per_package, snap_gas_price, snap_mpg, snap_expense_config)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (data["date"], data.get("driver_id"), data["packages"],
         data.get("miles", 0.0), data.get("hours"), data.get("extra_expense"),
         data.get("note"), s["pay_per_package"], s["gas_price_per_gal"],
         s["vehicle_mpg"], json.dumps(cfg)),
    )
    conn.commit()
    return cur.lastrowid


def get_entry(conn, entry_id):
    row = conn.execute("SELECT * FROM daily_entries WHERE id=?",
                       (entry_id,)).fetchone()
    return _row_to_entry(row)


def list_entries(conn, start_date, end_date):
    rows = conn.execute(
        "SELECT * FROM daily_entries WHERE date >= ? AND date <= ? "
        "ORDER BY date DESC, id DESC", (start_date, end_date)).fetchall()
    return [_row_to_entry(r) for r in rows]


def delete_entry(conn, entry_id):
    conn.execute("DELETE FROM daily_entries WHERE id=?", (entry_id,))
    conn.commit()

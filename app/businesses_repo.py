from app.db import seed_business_expenses

ALLOWED_FIELDS = {"name", "rate_model", "pay_per_package", "drivers_enabled"}


class ActiveBusinessError(Exception):
    """Raised when an operation would leave no active business."""


def list_businesses(conn, include_archived=False):
    sql = "SELECT * FROM businesses"
    if not include_archived:
        sql += " WHERE archived = 0"
    sql += " ORDER BY id"
    return [dict(r) for r in conn.execute(sql)]


def get_business(conn, business_id):
    row = conn.execute("SELECT * FROM businesses WHERE id=?",
                       (business_id,)).fetchone()
    return dict(row) if row else None


def create_business(conn, name):
    cur = conn.execute("INSERT INTO businesses (name) VALUES (?)", (name,))
    business_id = cur.lastrowid
    # Seed inside the same transaction: a business without expense rows
    # would silently report zero costs on every day logged under it.
    seed_business_expenses(conn, business_id)
    conn.commit()
    return business_id


def rename_business(conn, business_id, name):
    cur = conn.execute("UPDATE businesses SET name=? WHERE id=?",
                       (name, business_id))
    conn.commit()
    return cur.rowcount > 0


def update_business(conn, business_id, values):
    fields = {k: v for k, v in values.items() if k in ALLOWED_FIELDS}
    if not fields:
        return
    assignments = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE businesses SET {assignments} WHERE id=?",
                 list(fields.values()) + [business_id])
    conn.commit()


def get_tiers(conn, business_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM rate_tiers WHERE business_id=? ORDER BY min_packages",
        (business_id,))]


def replace_tiers(conn, business_id, tiers):
    """Replace a business's whole tier table.

    `tiers` is an ordered list of (max_packages | None, rate). Lower
    bounds are derived here rather than stored from the form: the first
    tier always starts at 1 and each next starts one above the previous
    ceiling, which makes a gap or an overlap unrepresentable.
    """
    conn.execute("DELETE FROM rate_tiers WHERE business_id=?", (business_id,))
    low = 1
    for max_packages, rate in tiers:
        conn.execute(
            "INSERT INTO rate_tiers (business_id, min_packages, "
            "max_packages, rate) VALUES (?,?,?,?)",
            (business_id, low, max_packages, rate))
        if max_packages is None:
            break  # an unbounded tier must be the last one
        low = max_packages + 1
    conn.commit()


def set_archived(conn, business_id, archived):
    if archived:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM businesses WHERE archived=0 AND id<>?",
            (business_id,)).fetchone()[0]
        if remaining == 0:
            raise ActiveBusinessError(
                "You cannot archive your only business.")
    cur = conn.execute("UPDATE businesses SET archived=? WHERE id=?",
                       (1 if archived else 0, business_id))
    conn.commit()
    return cur.rowcount > 0

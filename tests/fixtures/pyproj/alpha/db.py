"""Database helpers."""
QUERY = "SELECT id, name FROM customers WHERE active = 1"


def fetch_customers(conn):
    return conn.execute(QUERY)

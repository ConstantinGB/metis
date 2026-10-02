"""Database access."""
QUERY = "SELECT id, name FROM customers WHERE id = %s"
INSERT = """
    INSERT INTO audit_log (customer_id, action)
    VALUES (%s, %s)
"""


def fetch_customer(customer_id):
    return QUERY, customer_id

-- Customers who can log in.
CREATE TABLE customers (
  id INT PRIMARY KEY,
  name VARCHAR(100)
);

-- Every change made through the API.
CREATE TABLE audit_log (
  id INT PRIMARY KEY,
  customer_id INT REFERENCES customers(id),
  action VARCHAR(50)
);

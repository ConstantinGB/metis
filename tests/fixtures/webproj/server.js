/**
 * Tiny Express server that fronts the API.
 */
const express = require("express");

const app = express();
const PORT = process.env.PORT || 3000;

/**
 * Health check used by the load balancer.
 */
function health(req, res) {
  res.send("ok");
}

app.get("/health", health);
app.post("/echo", (req, res) => res.json(req.body));

app.listen(PORT);

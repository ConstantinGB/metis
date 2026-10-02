import express from "express";
import { helper, VERSION } from "./util.js";
const other = require("./lib/other");

export function start() {
  const app = express();
  helper(app);
  other.run();
  return app;
}

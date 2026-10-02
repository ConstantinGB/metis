import { helper } from "./util";
import { run } from "@lib/other";
import type { Thing } from "./types";

export class App {
  boot(): Thing {
    helper(this);
    run();
    return { id: 1 };
  }
}

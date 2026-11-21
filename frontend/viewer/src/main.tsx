import { startApp } from "@rdb/core";

startApp("Viewer", () => import("./App"));

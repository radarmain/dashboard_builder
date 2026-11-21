import { startApp } from "@rdb/core";
import "./index.css";

startApp("Builder", () => import("./App"));

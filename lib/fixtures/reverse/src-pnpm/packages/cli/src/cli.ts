#!/usr/bin/env node
import { greet } from "@demo/core";

console.log(greet(process.argv[2] ?? "world"));

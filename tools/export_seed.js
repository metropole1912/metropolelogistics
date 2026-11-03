// Exports the snapshot arrays from web/data.js into supabase/seed_data.json.
// Run:  node tools/export_seed.js      then   python tools/build_seed.py
const fs = require("fs"), path = require("path"), vm = require("vm");
const root = path.resolve(__dirname, "..");
const src = fs.readFileSync(path.join(root, "web", "data.js"), "utf8");
const names = ["DATASET_DATE", "METALS", "ENERGY_CODES", "COUNTRIES", "MINES", "PORTS", "CORRIDORS", "CHOKES", "ZONES", "EVENTS",
  "FORECASTS", "BRIEFINGS", "SOURCES", "DEMAND", "DEMAND_YEARS", "DEMAND_SOURCE"];
const data = vm.runInNewContext(src + ";({" + names.join(",") + "})", {});
fs.writeFileSync(path.join(root, "supabase", "seed_data.json"), JSON.stringify(data));
console.log("seed_data.json:", Object.fromEntries(names.filter(n => Array.isArray(data[n])).map(n => [n, data[n].length])));

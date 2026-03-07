"""Patch tauri.conf.json to ignore __pycache__ in file watcher."""
import json, sys, os

conf_path = r"C:\dev\strategy-optimizer\src-tauri\tauri.conf.json"
with open(conf_path, "r") as f:
    conf = json.load(f)

# Add watchIgnore to the build section
if "build" not in conf:
    conf["build"] = {}

conf["build"]["watchIgnore"] = ["**/python/__pycache__/**", "**/*.pyc", "**/strategies/__pycache__/**"]

with open(conf_path, "w") as f:
    json.dump(conf, f, indent=2)

print("Patched tauri.conf.json with watchIgnore")
print(json.dumps(conf["build"], indent=2))

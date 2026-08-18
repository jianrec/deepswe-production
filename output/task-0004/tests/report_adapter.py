#!/usr/bin/env python3
import argparse, json, pathlib, re, xml.etree.ElementTree as ET

parser = argparse.ArgumentParser()
parser.add_argument("--bucket", choices=("f2p", "p2p"), required=True)
parser.add_argument("--rc", type=int, required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--log", required=True)
parser.add_argument("--config", default="/tests/config.json")
args = parser.parse_args()
config = json.loads(pathlib.Path(args.config).read_text())
ids = [str(x).strip() for x in config.get("f2p_node_ids" if args.bucket == "f2p" else "p2p_node_ids", []) if str(x).strip()]
output = pathlib.Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)

# Read a native JUnit file if the runner produced one.  Its framework-specific
# names are mapped back to the source-derived whitelist IDs below.
native = []
if output.is_file() and output.stat().st_size:
    try:
        candidate = ET.parse(output).getroot()
        for case in candidate.iter("testcase"):
            name = str(case.attrib.get("name") or "").strip()
            classname = str(case.attrib.get("classname") or "").strip()
            state = "passed"
            message = ""
            for child in case:
                tag = child.tag.rsplit("}", 1)[-1]
                if tag in {"failure", "error"}:
                    state = "failed"
                    message = str(child.get("message") or child.text or "")
                    break
                if tag == "skipped":
                    state = "skipped"
            native.append((classname, name, state, message))
    except Exception:
        native = []

statuses = {}
log_path = pathlib.Path(args.log)
if log_path.is_file():
    for line in log_path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        name = str(event.get("Test") or "").strip()
        action = str(event.get("Action") or "").lower()
        if not name or action not in {"pass", "fail", "skip"}:
            continue
        state = "passed" if action == "pass" else ("skipped" if action == "skip" else "failed")
        for node_id in ids:
            if node_id == name or node_id.endswith("." + name) or node_id.endswith("/" + name):
                statuses[node_id] = state

    # Rust's stable human-readable harness output contains one line per test.
    for match in re.finditer(r"^test\s+(.+?)\s+\.\.\.\s+(ok|FAILED|ignored)\s*$", log_path.read_text(errors="replace"), re.M):
        name, action = match.groups()
        state = "passed" if action == "ok" else ("skipped" if action == "ignored" else "failed")
        for node_id in ids:
            leaf = node_id.rsplit(".", 1)[-1]
            if name == leaf or name.endswith("::" + leaf):
                statuses[node_id] = state

root = ET.Element("testsuite", name=args.bucket, tests=str(len(ids)))
for node_id in ids:
    classname, name = node_id.rsplit(".", 1) if "." in node_id else (args.bucket, node_id)
    case = ET.SubElement(root, "testcase", classname=classname, name=name)
    state = statuses.get(node_id)
    message = ""
    if state is None:
        matches = [
            item for item in native
            if item[1] == name
            or item[1].startswith(name + "[")
            or item[1].endswith(" " + name)
            or item[1].endswith("::" + name)
        ]
        if matches:
            state = "failed" if any(item[2] == "failed" for item in matches) else ("skipped" if any(item[2] == "skipped" for item in matches) else "passed")
            message = next((item[3] for item in matches if item[3]), "")
    if state == "failed" or (state is None and args.rc):
        failure = ET.SubElement(case, "failure", message="test case failed")
        failure.text = message or "The language-native test command failed; see the native log."
    elif state == "skipped" or state is None:
        skipped = ET.SubElement(case, "skipped", message="test result missing from native runner output")
        skipped.text = "The configured test ID was not observed in the native report."
ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)

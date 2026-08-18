#!/bin/bash
set -uo pipefail
trap 'if [ ! -f /logs/verifier/reward.json ]; then mkdir -p /logs/verifier; echo -1 > /logs/verifier/reward.txt; fi' EXIT
cd /app || exit 6
python3 /tests/grader.py prepare || exit $?
[ -f /logs/verifier/reward.json ] && exit 0
mkdir -p /logs/verifier
set +e
(
  cd /app || exit 6
  cargo test --locked --test hidden_sort_name_path --test hidden_sort_size --test hidden_sort_times --test hidden_sort_extension --test hidden_sort_cli --test hidden_sort_exec --test hidden_sort_output --test hidden_sort_edges
) > /logs/verifier/feature-native.log 2>&1
feature_rc=$?
(
  cd /app || exit 6
  cargo test --locked --bin fd --test tests
) > /logs/verifier/regression-native.log 2>&1
regression_rc=$?
set -e
python3 /tests/report_adapter.py --bucket f2p --rc "$feature_rc" --log /logs/verifier/feature-native.log --output /logs/verifier/feature-junit.xml
python3 /tests/report_adapter.py --bucket p2p --rc "$regression_rc" --log /logs/verifier/regression-native.log --output /logs/verifier/regression-junit.xml
printf 'feature_rc=%s regression_rc=%s
' "$feature_rc" "$regression_rc"
python3 /tests/grader.py grade

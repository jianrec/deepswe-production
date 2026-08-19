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
  python -m pytest tests/test_hidden_bubble_sort_key.py tests/test_hidden_insertion_sort_key.py tests/test_hidden_merge_sort_key.py tests/test_hidden_selection_sort_key.py tests/test_hidden_search_ordering.py tests/test_hidden_top_k_frequent_words.py tests/test_hidden_ordering_key_contract.py -q --junitxml=/logs/verifier/feature-junit.xml
) > /logs/verifier/feature-native.log 2>&1
feature_rc=$?
(
  cd /app || exit 6
  python -m pytest searches sorts strings -q --junitxml=/logs/verifier/regression-junit.xml
) > /logs/verifier/regression-native.log 2>&1
regression_rc=$?
set -e
python3 /tests/report_adapter.py --bucket f2p --rc "$feature_rc" --log /logs/verifier/feature-native.log --output /logs/verifier/feature-junit.xml
python3 /tests/report_adapter.py --bucket p2p --rc "$regression_rc" --log /logs/verifier/regression-native.log --output /logs/verifier/regression-junit.xml
printf 'feature_rc=%s regression_rc=%s
' "$feature_rc" "$regression_rc"
python3 /tests/grader.py grade

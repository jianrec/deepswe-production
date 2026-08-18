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
  pnpm vitest run tests/hidden --reporter=junit --outputFile=/logs/verifier/feature-junit.xml
) > /logs/verifier/feature-native.log 2>&1
feature_rc=$?
(
  cd /app || exit 6
  pnpm vitest run tests/basic.test.tsx tests/devtools.test.tsx tests/immer.test.tsx tests/middlewareTypes.test.tsx tests/persistAsync.test.tsx tests/persistSync.test.tsx tests/shallow.test.tsx tests/ssr.test.tsx tests/subscribe.test.tsx tests/types.test.tsx tests/vanilla/basic.test.ts tests/vanilla/shallow.test.tsx tests/vanilla/subscribe.test.tsx --reporter=junit --outputFile=/logs/verifier/regression-junit.xml
) > /logs/verifier/regression-native.log 2>&1
regression_rc=$?
set -e
python3 /tests/report_adapter.py --bucket f2p --rc "$feature_rc" --log /logs/verifier/feature-native.log --output /logs/verifier/feature-junit.xml
python3 /tests/report_adapter.py --bucket p2p --rc "$regression_rc" --log /logs/verifier/regression-native.log --output /logs/verifier/regression-junit.xml
printf 'feature_rc=%s regression_rc=%s
' "$feature_rc" "$regression_rc"
python3 /tests/grader.py grade

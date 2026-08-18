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
  HARBOR_JUNIT_PATH=/logs/verifier/feature-junit.xml npm test -- --runInBand --runTestsByPath hidden-tests/modularArithmetic.feature.test.js hidden-tests/alphabetCodec.feature.test.js hidden-tests/matrixClassicalCipher.feature.test.js hidden-tests/hillCipherRoundTrip.feature.test.js hidden-tests/caesarNormalizedShift.feature.test.js hidden-tests/railFenceValidation.feature.test.js hidden-tests/polynomialHashResidues.feature.test.js hidden-tests/leastCommonMultipleSigned.feature.test.js --reporters=default --reporters=./hidden-tests/support/HarborJunitReporter.js
) > /logs/verifier/feature-native.log 2>&1
feature_rc=$?
(
  cd /app || exit 6
  HARBOR_JUNIT_PATH=/logs/verifier/regression-junit.xml npm test -- --runInBand --testPathIgnorePatterns=/hidden-tests/ --reporters=default --reporters=./hidden-tests/support/HarborJunitReporter.js
) > /logs/verifier/regression-native.log 2>&1
regression_rc=$?
set -e
python3 /tests/report_adapter.py --bucket f2p --rc "$feature_rc" --log /logs/verifier/feature-native.log --output /logs/verifier/feature-junit.xml
python3 /tests/report_adapter.py --bucket p2p --rc "$regression_rc" --log /logs/verifier/regression-native.log --output /logs/verifier/regression-junit.xml
printf 'feature_rc=%s regression_rc=%s
' "$feature_rc" "$regression_rc"
python3 /tests/grader.py grade

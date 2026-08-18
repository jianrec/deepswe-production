#!/bin/bash
set -uo pipefail
trap 'if [ ! -f /logs/verifier/reward.json ]; then mkdir -p /logs/verifier; echo -1 > /logs/verifier/reward.txt; fi' EXIT
cd /app || exit 6

python3 /tests/grader.py prepare || exit $?
[ -f /logs/verifier/reward.json ] && exit 0

mkdir -p /logs/verifier
feature_tests=(
  src/utils/streak.test.ts
  src/utils/streak.boundary.test.ts
  src/utils/streak.property.test.ts
  src/utils/streak.regression.test.ts
  src/routes/protected/challenge.streak.spec.ts
  src/routes/protected/user.streak.spec.ts
  src/daily-coding-challenge/routes/daily-coding-challenge.streak.spec.ts
)
regression_tests=(
  src/utils/sentry.test.ts
  src/utils/http-metrics.test.ts
  src/utils/drip-campaign.test.ts
  src/utils/normalize.test.ts
  src/utils/progress.test.ts
  src/utils/validation.test.ts
  src/utils/index.test.ts
  src/utils/exam.test.ts
  src/utils/validate-donation.test.ts
  src/plugins/redirect-with-message.test.ts
  src/plugins/runtime-metrics.test.ts
)

set +e
(
  cd api || exit 6
  ./node_modules/.bin/vitest run "${feature_tests[@]}" \
    --reporter=junit --outputFile=/logs/verifier/feature-junit.xml
)
feature_rc=$?
(
  cd api || exit 6
  ./node_modules/.bin/vitest run "${regression_tests[@]}" \
    --reporter=junit --outputFile=/logs/verifier/regression-junit.xml
)
regression_rc=$?
set -e

printf 'feature_rc=%s regression_rc=%s\n' "$feature_rc" "$regression_rc"
python3 /tests/grader.py grade

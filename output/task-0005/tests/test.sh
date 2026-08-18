#!/bin/bash
set -uo pipefail
export CARGO_BUILD_JOBS=1
export CARGO_INCREMENTAL=0
export CARGO_PROFILE_DEV_DEBUG=0
export CARGO_PROFILE_TEST_DEBUG=0
trap 'if [ ! -f /logs/verifier/reward.json ]; then mkdir -p /logs/verifier; echo -1 > /logs/verifier/reward.txt; fi' EXIT
cd /app || exit 6
if [ "${DEEPSWE_SOURCE_PREPARED:-0}" != 1 ]; then
  python3 /tests/grader.py prepare || exit $?
fi
[ -f /logs/verifier/reward.json ] && exit 0
mkdir -p /logs/verifier
set +e
(
  cd /app || exit 6
  cargo test --manifest-path codex-rs/Cargo.toml -p codex-config --test exec_limits_parsing --test exec_limits_toml && cargo test --manifest-path codex-rs/Cargo.toml -p codex-cli --test limits_accounting --test limits_conversion --test limit_exit_code --test exec_limits_commands --test exec_limits_wall_time --test exec_limits_config --test exec_limits_output_and_validation
) > /logs/verifier/feature-native.log 2>&1
feature_rc=$?
if [ "${DEEPSWE_MUTANT_FAST_FAIL:-0}" = 1 ] && [ "$feature_rc" -ne 0 ]; then
  echo "Mutant already failed the feature suite; P2P execution skipped." > /logs/verifier/regression-native.log
  regression_rc=1
else
  (
    cd /app || exit 6
    cargo test --manifest-path codex-rs/Cargo.toml -p codex-exec
  ) > /logs/verifier/regression-native.log 2>&1
  regression_rc=$?
fi
set -e
python3 /tests/report_adapter.py --bucket f2p --rc "$feature_rc" --log /logs/verifier/feature-native.log --output /logs/verifier/feature-junit.xml
python3 /tests/report_adapter.py --bucket p2p --rc "$regression_rc" --log /logs/verifier/regression-native.log --output /logs/verifier/regression-junit.xml
printf 'feature_rc=%s regression_rc=%s
' "$feature_rc" "$regression_rc"
python3 /tests/grader.py grade

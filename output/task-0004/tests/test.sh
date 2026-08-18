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
  rm -f /tmp/feature-results.json /tmp/feature-output.log /logs/verifier/feature-junit.xml; yarn test --runTestsByPath packages/react/src/__tests__/ReactChildrenReduce-hidden-test.js packages/react/src/__tests__/ReactChildrenFilter-hidden-test.js packages/react/src/__tests__/ReactChildrenFind-hidden-test.js packages/react/src/__tests__/ReactChildrenToKeyedArray-hidden-test.js packages/react/src/__tests__/ReactChildrenFunctionalTraversal-hidden-test.js packages/react/src/__tests__/ReactChildrenExports-hidden-test.js packages/react/src/__tests__/ReactChildKeyPath-hidden-test.js packages/react/src/__tests__/ReactChildrenInvalidInputs-hidden-test.js --ci --runInBand --json --outputFile=/tmp/feature-results.json >/tmp/feature-output.log 2>&1; status=$?; node -e "const fs=require('fs');const path=require('path');const input=process.argv[1];const output=process.argv[2];const esc=value=>String(value==null?'':value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;').replace(/'/g,'&apos;');const suites=[];if(fs.existsSync(input)){const result=JSON.parse(fs.readFileSync(input,'utf8'));for(const file of result.testResults||[]){const assertions=file.assertionResults||[];let failures=0;const cases=assertions.map(test=>{const failed=test.status==='failed'||(test.failureMessages||[]).length>0;if(failed)failures++;const message=(test.failureMessages||[]).join('\n');return '<testcase classname=\"'+esc(file.name)+'\" name=\"'+esc(test.fullName||test.title)+'\" time=\"'+((test.duration||0)/1000)+'\">'+(failed?'<failure message=\"failed\">'+esc(message)+'</failure>':'')+'</testcase>';}).join('');suites.push('<testsuite name=\"'+esc(file.name)+'\" tests=\"'+assertions.length+'\" failures=\"'+failures+'\">'+cases+'</testsuite>');}}path.dirname(output)&&fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,'<?xml version=\"1.0\" encoding=\"UTF-8\"?><testsuites>'+suites.join('')+'</testsuites>');" /tmp/feature-results.json /logs/verifier/feature-junit.xml; cat /tmp/feature-output.log; exit $status
) > /logs/verifier/feature-native.log 2>&1
feature_rc=$?
(
  cd /app || exit 6
  rm -f /tmp/regression-results.json /tmp/regression-output.log /logs/verifier/regression-junit.xml; files=$(git ls-tree -r --name-only ec61f187fe39b0aa8ec6b508f2553b2047dc30cc packages/react/src/__tests__ | awk '/-test[.]js$/ {printf "%s ",$0}'); test -n "$files" || exit 6; yarn test --runTestsByPath $files --ci --runInBand --json --outputFile=/tmp/regression-results.json >/tmp/regression-output.log 2>&1; status=$?; node -e "const fs=require('fs');const path=require('path');const input=process.argv[1];const output=process.argv[2];const esc=value=>String(value==null?'':value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;').replace(/'/g,'&apos;');const suites=[];if(fs.existsSync(input)){const result=JSON.parse(fs.readFileSync(input,'utf8'));for(const file of result.testResults||[]){const assertions=file.assertionResults||[];let failures=0;const cases=assertions.map(test=>{const failed=test.status==='failed'||(test.failureMessages||[]).length>0;if(failed)failures++;const message=(test.failureMessages||[]).join('\n');return '<testcase classname=\"'+esc(file.name)+'\" name=\"'+esc(test.fullName||test.title)+'\" time=\"'+((test.duration||0)/1000)+'\">'+(failed?'<failure message=\"failed\">'+esc(message)+'</failure>':'')+'</testcase>';}).join('');suites.push('<testsuite name=\"'+esc(file.name)+'\" tests=\"'+assertions.length+'\" failures=\"'+failures+'\">'+cases+'</testsuite>');}}path.dirname(output)&&fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,'<?xml version=\"1.0\" encoding=\"UTF-8\"?><testsuites>'+suites.join('')+'</testsuites>');" /tmp/regression-results.json /logs/verifier/regression-junit.xml; cat /tmp/regression-output.log; exit $status
) > /logs/verifier/regression-native.log 2>&1
regression_rc=$?
set -e
python3 /tests/report_adapter.py --bucket f2p --rc "$feature_rc" --log /logs/verifier/feature-native.log --output /logs/verifier/feature-junit.xml
python3 /tests/report_adapter.py --bucket p2p --rc "$regression_rc" --log /logs/verifier/regression-native.log --output /logs/verifier/regression-junit.xml
printf 'feature_rc=%s regression_rc=%s
' "$feature_rc" "$regression_rc"
python3 /tests/grader.py grade

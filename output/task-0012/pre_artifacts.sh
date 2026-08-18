#!/bin/sh
set -eu
cd /app
git diff --binary dcaa4296d111981ffb31ac3eba90bb63e1eb5ab9 HEAD > /logs/artifacts/model.patch

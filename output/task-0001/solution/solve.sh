#!/bin/sh
set -eu
cd /app
git apply --whitespace=nowarn /solution/solution.patch

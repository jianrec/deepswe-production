#!/bin/sh
set -eu
cd /app
git diff --binary f5988cc09713315817df6a7e327e258013a94440 HEAD > /logs/artifacts/model.patch

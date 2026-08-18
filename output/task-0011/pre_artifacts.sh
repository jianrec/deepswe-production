#!/bin/sh
set -eu
cd /app
git diff --binary beca84e600e4e250f6b244d22878e72948f331c7 HEAD > /logs/artifacts/model.patch

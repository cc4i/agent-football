#!/bin/bash
# deploy.sh - Builds the three images in Cloud Build and replaces the service.

# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -euo pipefail

: "${PROJECT:?set PROJECT}"
: "${REGION:?set REGION}"
# The VPC the database lives in and the subnet the service gets its interface
# in. The subnet has to be in $REGION; the network has to be the one the
# private connection was made on, or the address below is not routable from
# here no matter what it says.
NETWORK="${NETWORK:-default}"
SUBNET="${SUBNET:-default}"
TAG="${TAG:-$(git rev-parse --short HEAD)}"
REPO="${REGION}-docker.pkg.dev/${PROJECT}/futsal"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -n "$(git status --porcelain)" ]; then
    echo "WARNING: the tree is dirty, so ${TAG} will not describe what is deployed."
fi

# Every deploy replaces the one instance, and the one instance is holding every
# live match. There is no rolling handover to be had at max-instances: 1.
echo "This drops every match currently being played. Deploy between matches."
read -r -p "Continue? [y/N] " answer
[ "$answer" = "y" ] || exit 1

# Built in Cloud Build rather than here: it is amd64 natively, which this Mac is
# not, and it pushes with the credentials gcloud already has. What gets uploaded
# is governed by .gcloudignore, which keeps the dugout out of the tarball.
echo "--> Building the three images..."
gcloud builds submit --config deploy/cloudbuild.yaml \
    --substitutions="_REPO=${REPO},_TAG=${TAG}" \
    --project="${PROJECT}" .

# arena-pg is created with --no-assign-ip, so it has exactly one address and
# that address is private. Read it rather than record it: it is assigned when
# the instance is made, and an instance rebuilt between workshops comes back
# with a different one. The type is checked because the failure it catches is
# silent - a public address rendered here is not carried by private-ranges-only
# egress, and the arena would spend its startup timing out against a route
# nothing in the log mentions.
echo "--> Reading the database's private address..."
DB_ADDRESS="$(gcloud sql instances describe arena-pg --project="${PROJECT}" \
    --format='value(ipAddresses[0].type,ipAddresses[0].ipAddress)')"
DB_IP_TYPE="${DB_ADDRESS%%$'\t'*}"
DB_HOST="${DB_ADDRESS##*$'\t'}"
if [ "$DB_IP_TYPE" != "PRIVATE" ] || [ -z "$DB_HOST" ]; then
    echo "arena-pg's first address is '${DB_IP_TYPE:-none}', not PRIVATE." >&2
    echo "The arena reaches the database over the VPC and cannot use a public one." >&2
    exit 1
fi
echo "    ${DB_HOST}"

echo "--> Replacing the service..."
sed -e "s|__PROJECT__|${PROJECT}|g" \
    -e "s|__REGION__|${REGION}|g" \
    -e "s|__NETWORK__|${NETWORK}|g" \
    -e "s|__SUBNET__|${SUBNET}|g" \
    -e "s|__DB_HOST__|${DB_HOST}|g" \
    -e "s|__TAG__|${TAG}|g" \
    deploy/service.yaml > /tmp/arena-service.yaml
gcloud run services replace /tmp/arena-service.yaml --region="${REGION}" --project="${PROJECT}"

gcloud run services describe arena --region="${REGION}" --project="${PROJECT}" \
    --format='value(status.url)'

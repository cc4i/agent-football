#!/bin/bash
# deploy.sh - Builds the four images in Cloud Build and replaces both services.

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
# What this deployment is called and which database it keeps its venue in.
# The defaults are what a single deployment has always used, so an ordinary run
# of this script is exactly what it was before these three lines existed.
#
# Set them and you get a second venue beside the first: its own two services,
# its own database on the same instance, the same service account and the same
# secrets. Two arenas must never share a database - each one sweeps for matches
# whose screen has gone and would abandon the other's - so the name moves with
# the services rather than being something you can forget.
#
#   ARENA_NAME=arena-v2 GROUNDS_NAME=grounds-v2 DB_NAME=arena_v2 deploy/deploy.sh
ARENA_NAME="${ARENA_NAME:-arena}"
GROUNDS_NAME="${GROUNDS_NAME:-grounds}"
DB_NAME="${DB_NAME:-arena}"
REPO="${REGION}-docker.pkg.dev/${PROJECT}/futsal"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -n "$(git status --porcelain)" ]; then
    echo "WARNING: the tree is dirty, so ${TAG} will not describe what is deployed."
fi

# Every deploy replaces what is running, and what is running is holding every
# live match. There is no rolling handover to be had at max-instances: 1. What
# the replace does not do is stop the old arena, which is what the last step of
# this script is for. See deploy/README.md.
echo "This drops every match currently being played. Deploy between matches."
read -r -p "Continue? [y/N] " answer
[ "$answer" = "y" ] || exit 1

# Built in Cloud Build rather than here: it is amd64 natively, which this Mac is
# not, and it pushes with the credentials gcloud already has. What gets uploaded
# is governed by .gcloudignore, which keeps the dugout out of the tarball.
echo "--> Building the four images..."
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

# The arena creates its own database when it can, which is what makes a laptop
# `brew services start postgresql@18` and nothing else. It cannot here: the
# `arena` role is not a superuser and has no CREATEDB, so on Cloud SQL the
# database is made from outside. README.md does it by hand for the first
# deployment; doing it here as well is what lets DB_NAME name one that does not
# exist yet without a second page of instructions.
#
# Idempotent on purpose: an ordinary deploy re-runs this against a database that
# has been there since the first one, and `already exists` is the expected
# answer rather than a failure.
echo "--> Making sure the ${DB_NAME} database exists..."
if gcloud sql databases describe "${DB_NAME}" --instance=arena-pg \
        --project="${PROJECT}" >/dev/null 2>&1; then
    echo "    ${DB_NAME} is already there"
else
    gcloud sql databases create "${DB_NAME}" --instance=arena-pg --project="${PROJECT}"
    echo "    made ${DB_NAME}"
fi

echo "--> Replacing the service..."
sed -e "s|__PROJECT__|${PROJECT}|g" \
    -e "s|__REGION__|${REGION}|g" \
    -e "s|__NETWORK__|${NETWORK}|g" \
    -e "s|__SUBNET__|${SUBNET}|g" \
    -e "s|__DB_HOST__|${DB_HOST}|g" \
    -e "s|__ARENA_NAME__|${ARENA_NAME}|g" \
    -e "s|__DB_NAME__|${DB_NAME}|g" \
    -e "s|__TAG__|${TAG}|g" \
    deploy/service.yaml > /tmp/arena-service.yaml
gcloud run services replace /tmp/arena-service.yaml --region="${REGION}" --project="${PROJECT}"

# The grounds play for an arena, and this is the one they play for. Read rather
# than recorded, for the same reason the database's address is: it is assigned
# by Cloud Run when the service is first made. Read after the replace above and
# not before, so that a first deploy has a URL to read at all.
#
# A grounds pointed at the wrong arena is the quietest failure available here.
# It connects, offers its pitches, and plays nothing - while the arena the venue
# is actually using has no grounds and answers every kick-off with a 503.
echo "--> Reading the arena's URL for the grounds to play for..."
ARENA_URL="$(gcloud run services describe "${ARENA_NAME}" --region="${REGION}" \
    --project="${PROJECT}" --format='value(status.url)')"
if [ -z "$ARENA_URL" ]; then
    echo "the arena has no URL, so there is nothing for the grounds to play for." >&2
    exit 1
fi
echo "    ${ARENA_URL}"

echo "--> Replacing the grounds..."
sed -e "s|__PROJECT__|${PROJECT}|g" \
    -e "s|__REGION__|${REGION}|g" \
    -e "s|__ARENA_URL__|${ARENA_URL}|g" \
    -e "s|__GROUNDS_NAME__|${GROUNDS_NAME}|g" \
    -e "s|__TAG__|${TAG}|g" \
    deploy/grounds.yaml > /tmp/grounds-service.yaml
gcloud run services replace /tmp/grounds-service.yaml --region="${REGION}" --project="${PROJECT}"

# The containers every deploy before this one pinned. maxScale bounds a revision
# and minScale pins an instance per revision, so the replaces above did not stop
# what they superseded: that container keeps running, takes no traffic, and goes
# on doing its job with the code it was built from. For the arena that is
# sweeping the one database on its own watchdog - three were up at once here.
# For the grounds it is a browser whose control socket reconnects to whatever
# the public URL now points at, which is the new arena: a pitch running last
# deploy's bundle, offering itself for real matches. Cloud Run reclaims them on
# a schedule of its own, and across one afternoon of deploys that ran from a
# minute to an hour; a revision is immutable, so there is nothing to scale down
# to hurry it.
#
# Deleting is the only lever there is, and it is not a stop button either. The
# revision leaves the API at once and the container was still answering its
# health probe twenty minutes later with no revision left to belong to. So this
# keeps the pile from growing rather than ending the overlap -- which the arena
# is built to survive in any case, or the deploy itself could not be survived.
#
# All but one of them. The newest of what is replaced is the way back if this
# deploy turns out to be the bad one, and pointing traffic at a revision that
# still exists takes seconds where rebuilding an old tag takes minutes. That
# rollback is in deploy/README.md; this is the line that keeps it possible.
stand_down() {
    local service="$1"
    local serving replaced keep revision
    echo "--> Standing down the ${service} revisions this replaces..."
    serving="$(gcloud run services describe "${service}" --region="${REGION}" \
        --project="${PROJECT}" --format='value(status.traffic[].revisionName)' | tr ';' '\n')"
    if [ -z "$serving" ]; then
        # Nothing named as taking traffic is not an answer to act on: the list
        # below is every revision there is, so an empty exclusion would delete
        # the live one.
        echo "WARNING: nothing is named as serving traffic, so none were stood down." >&2
        return
    fi
    replaced="$(gcloud run revisions list --service="${service}" --region="${REGION}" \
        --project="${PROJECT}" --format='value(metadata.name)')"
    # Whole line and fixed string. arena-00007-cvr contains arena-00007-cv, and
    # a match loose enough to confuse the two takes the venue down.
    for revision in $serving; do
        replaced="$(printf '%s\n' "${replaced}" | grep -vxF "${revision}" || true)"
    done
    # Newest first, which is the order `revisions list` answers in.
    keep="$(printf '%s\n' "${replaced}" | head -n 1)"
    if [ -n "$keep" ]; then
        echo "    ${keep} (kept, this is what rolls back to)"
    fi
    for revision in $(printf '%s\n' "${replaced}" | tail -n +2); do
        echo "    ${revision}"
        # Not fatal. The deploy is live by this point, and a revision that
        # refuses to go is the state this script has run in all along.
        gcloud run revisions delete "${revision}" --region="${REGION}" \
            --project="${PROJECT}" --quiet || echo "WARNING: ${revision} is still up." >&2
    done
}

stand_down "${ARENA_NAME}"
stand_down "${GROUNDS_NAME}"

echo "${ARENA_URL}"

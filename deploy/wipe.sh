#!/bin/bash
# wipe.sh - Empty the deployed venue. Every manager, every match, every result.

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
#
# The script it runs is deploy/wipe_the_venue.py, carried into a Cloud Run job
# base64'd, the same way deploy/README.md carries tidy_rehearsals.py. The job is
# how anything reaches the database at all: the Cloud SQL instance has a private
# address, so it takes something inside the VPC on the arena's own image.
#
#   deploy/wipe.sh              # count it, change nothing
#   deploy/wipe.sh --apply      # and mean it
#   deploy/wipe.sh --apply --live   # even with matches being played
#
# On a laptop none of this is needed. The script only reads ARENA_DB:
#
#   cd arena && ARENA_DB=postgresql:///arena uv run python ../deploy/wipe_the_venue.py

set -euo pipefail

# Newest last. `tac` is GNU and absent from the Mac this is run from; `tail -r`
# is BSD and absent from the container images. awk is on both.
reversed() { awk '{ held[NR] = $0 } END { for (i = NR; i > 0; i--) print held[i] }'; }

: "${PROJECT:?set PROJECT}"
: "${REGION:?set REGION}"
NETWORK="${NETWORK:-default}"
SUBNET="${SUBNET:-default}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

APPLY=0
LIVE=0
for argument in "$@"; do
    case "$argument" in
        --apply) APPLY=1 ;;
        --live)  LIVE=1 ;;
        *) echo "unknown argument: ${argument}" >&2; exit 2 ;;
    esac
done

# The image the arena is running, rather than a tag guessed from git. What the
# job needs off it is psycopg and the arena's own modules, and the surest way to
# have the same ones is to use the same image.
IMAGE="$(gcloud run services describe arena --region="${REGION}" --project="${PROJECT}" \
    --format='value(spec.template.spec.containers[0].image)')"
if [ -z "$IMAGE" ]; then
    echo "no arena service in ${REGION}, so there is no venue to empty." >&2
    exit 1
fi

# Read rather than recorded, for the reason deploy.sh gives: the address is
# assigned when the instance is made.
DB_HOST="$(gcloud sql instances describe arena-pg --project="${PROJECT}" \
    --format='value(ipAddresses[0].ipAddress)')"
# Which venue is being emptied. Defaulted to the one there has always been, so
# an unqualified `wipe.sh --apply` does exactly what it always did - and named
# here at all because a second deployment shares this instance. Wiping is the
# one operation where reaching the wrong database is unrecoverable, and a
# hardcoded name gives somebody running it against a variant no way to say so.
DB_NAME="${DB_NAME:-arena}"
# And which service is holding that database, for the bounce this prints at the
# end. Wrong here is merely unhelpful rather than dangerous, but a wipe that
# finishes by naming somebody else's arena is a poor last thing to say.
ARENA_NAME="${ARENA_NAME:-arena}"
ARENA_DB="postgresql://arena@${DB_HOST}:5432/${DB_NAME}"

if [ "$APPLY" = "1" ]; then
    echo "This empties the venue behind ${IMAGE}."
    echo "Database: ${DB_NAME} on arena-pg."
    echo "Every manager, every match, every result. The standings go with them."
    read -r -p "Type the word empty to go ahead: " answer
    [ "$answer" = "empty" ] || { echo "left alone."; exit 1; }
fi

# Created or updated every run, so the job always carries this copy of the
# script rather than whichever one was current the first time somebody ran it.
VERB=create
gcloud run jobs describe wipe-venue --region="${REGION}" --project="${PROJECT}" \
    >/dev/null 2>&1 && VERB=update

gcloud run jobs "${VERB}" wipe-venue --region="${REGION}" --project="${PROJECT}" \
    --image="${IMAGE}" \
    --network="${NETWORK}" --subnet="${SUBNET}" --vpc-egress=private-ranges-only \
    `# A pipe as the separator, not the comma the tidy job uses and not the @` \
    `# gcloud's own docs reach for: the DSN is postgresql://arena@host, so @` \
    `# splits it in the middle of the credentials, and base64's alphabet is` \
    `# A-Za-z0-9+/= so a pipe cannot turn up inside the script either.` \
    --set-env-vars="^|^ARENA_DB=${ARENA_DB}|WIPE_APPLY=${APPLY}|WIPE_LIVE=${LIVE}|WIPE_B64=$(base64 < "${ROOT}/deploy/wipe_the_venue.py" | tr -d '\n')" \
    --set-secrets="PGPASSWORD=arena-db-password:latest" \
    --max-retries=0 --task-timeout=5m \
    --command=/app/.venv/bin/python \
    --args='^@^-c@import base64,os;exec(base64.b64decode(os.environ["WIPE_B64"]))' \
    >/dev/null

# The name is captured because the log below is filtered on it. Read back by
# freshness instead, the counts printed are whichever run happened to be inside
# the window - and on a tool that empties a venue, showing the previous run's
# numbers as though they were this one's is the worst thing it could do.
EXECUTION="$(gcloud run jobs execute wipe-venue --region="${REGION}" \
    --project="${PROJECT}" --wait --format='value(metadata.name)')"

# The job's stdout goes to Cloud Logging rather than to the execute above, so
# the counts it printed are read back here rather than left for somebody to go
# looking for.
echo "--> What ${EXECUTION} did:"
gcloud logging read \
    "resource.type=\"cloud_run_job\"
     AND labels.\"run.googleapis.com/execution_name\"=\"${EXECUTION}\"" \
    --project="${PROJECT}" --limit=40 --format='value(textPayload)' --freshness=1h \
    | reversed

if [ "$APPLY" = "1" ]; then
    echo
    echo "Bounce the arena to clear its memory too - the bus, the chain's seats and"
    echo "the rooms its sockets are holding all outlive the database:"
    echo "  gcloud run services update ${ARENA_NAME} --region=${REGION} --project=${PROJECT} \\"
    echo "      --update-env-vars=ARENA_BOUNCE=\$(date +%s)"
fi

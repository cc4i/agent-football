# Deploying the arena

Two Cloud Run services. `arena` has three containers in it: the arena on the
port, and the coach and the captain beside it on the loopback interface the
three share. `grounds` has one, a browser, and takes no traffic at all - the
arena hands it matches over a socket the grounds opened, and the matches are
played there rather than in whatever tab happens to be showing them. One Cloud
SQL instance behind the pair. The dugout is not here and never will be - it
embeds the Antigravity CLI and runs shell commands unrestricted, so it stays on
the presenter's laptop.

`service.yaml` and `grounds.yaml` are the whole topology. Everything below
either creates something they refer to or renders and applies them.

## Read this first

**Every deploy drops every live match.** `maxScale: "1"` is a correctness
constraint - the match bus, host liveness and the chain's semaphore are all in
one process - so there is no second instance to hand over to and no rolling
update to be had. The new revision starts, the old one stops, and every phone
in the venue loses its socket mid-match. A crash does exactly the same thing.

The database survives both. Players, rooms, the event log and the leaderboard
are all in Cloud SQL, so a manager who reloads gets their history back. What
they do not get back is the match they were playing. Deploy between matches.

**A revision this one replaces keeps its instance.** `maxScale` bounds a
revision and `minScale` pins an instance per revision; neither is a statement
about the service. So a deploy does not stop the arena it supersedes. That
container stays up taking no traffic but its own health probe, and it runs the
watchdog like any other arena, so it decides some of the abandonments in the
venue and publishes each to a bus with nobody on it. Three were up at once here,
one of them half an hour past the deploy that replaced it:

```bash
gcloud logging read 'resource.type="cloud_run_revision"
  AND resource.labels.service_name="arena"' --limit=500 \
  --format='value(resource.labels.revision_name)' | sort | uniq -c
```

More than one name in that count is more than one arena. `deploy.sh` deletes
what it supersedes for this reason, all but the newest, which it keeps for the
rollback below. Deleting is the only lever there is, because a revision cannot
be scaled down once it exists.

It is not a stop button. The revision leaves the API immediately; the container
it pinned does not go with it, and was still answering its health probe twenty
minutes later with no revision left to belong to. Cloud Run reclaims these on a
schedule of its own that ran anywhere from a minute to an hour across one
afternoon of deploys. So the stand-down keeps the pile from growing and does not
end the overlap - which the arena is built to survive in any case, or a deploy
could not be survived either: liveness is a column, and the sweep re-tells its
own sockets what the database says. Anything you add that assumes one process is
assuming something Cloud Run has not agreed to.

## Rolling back

The revision the last deploy replaced is still there, which makes going back to
it seconds rather than a rebuild:

```bash
gcloud run services update-traffic arena --region="$REGION" \
  --to-revisions=arena-00006-ffx=100
```

`deploy.sh` prints the name to use as it stands the others down. That pins
traffic, so the next `deploy.sh` puts `latestRevision: true` back and takes over
again - there is nothing to undo afterwards. Going back further than one
revision means rebuilding the tag, which is `TAG=<sha> ./deploy/deploy.sh`; the
image is still in Artifact Registry under its commit either way.

Both of these drop every live match, exactly as a deploy does.

## What it costs to leave up

`minScale` with `cpu-throttling: false` bills for the whole lifetime of the
service rather than per request: 4 vCPU and 8 GiB of the arena's container,
plus the coach's and the captain's 2 and 4 each, and then the grounds' 4 and 4
**three times over**, because `grounds.yaml` runs three instances. All of it
charged continuously whether anybody is playing or not. The Cloud SQL instance
never stops either. Neither service scales to zero and neither can be made to
without giving up what rests on it.

The grounds is the part with a dial on it. Three instances is sixty pitches,
which is the spec's fifty and ten spare; one instance is twenty, which is what
a workshop of twenty matches needs and a third of the cost. `GROUNDS_CAPACITY`
is a promise about a single thread and should not be raised to compensate -
that is measured in `grounds.yaml` and the number it is measured at is twenty.
Turning it down between workshops is `minScale` and `maxScale` together, which
must stay equal: the service takes no requests but its own probes, so a range
would let Cloud Run decide the venue's capacity on something unrelated to how
much football is being played.

Left alone it multiplies. Every superseded revision holding its pinned instance
bills at the same rate as the live one, so an afternoon of deploys costs an
afternoon of arenas; the stand-down step is worth as much here as it is above.

Delete both services and stop the SQL instance between workshops:

```bash
gcloud run services delete arena --region="$REGION"
gcloud run services delete grounds --region="$REGION"
gcloud sql instances patch arena-pg --activation-policy=NEVER
```

## Once per project

```bash
export PROJECT=your-project-id
export REGION=europe-west1        # Run, Cloud SQL and Artifact Registry
gcloud config set project "$PROJECT"
```

Vertex is reached at `global` rather than in `$REGION`; that is set in
`service.yaml` and is deliberate, because the models the chain uses are not in
every region.

### The APIs

```bash
gcloud services enable \
    run.googleapis.com \
    sqladmin.googleapis.com \
    secretmanager.googleapis.com \
    artifactregistry.googleapis.com \
    aiplatform.googleapis.com \
    cloudbuild.googleapis.com \
    compute.googleapis.com \
    servicenetworking.googleapis.com
```

`cloudbuild` is in the list because the images are built there rather than on
the laptop. See "Building somewhere else" below for why. `compute` and
`servicenetworking` are there because the database has no public IP: the first
allocates the range it lives in and the second peers that range to your
network.

### The image repository

```bash
gcloud artifacts repositories create futsal \
    --repository-format=docker --location="$REGION"
```

`futsal` is the name `service.yaml` and `cloudbuild.yaml` both spell out.

### The private connection

The VPC and its subnet are assumed to exist. What does not exist yet is the
block the managed instance lives in:

```bash
export NETWORK=default    # the VPC the database and the service share
export SUBNET=default     # in $REGION, where the service gets its interface

gcloud compute addresses create "google-managed-services-${NETWORK}" \
    --global --purpose=VPC_PEERING --prefix-length=16 \
    --network="projects/${PROJECT}/global/networks/${NETWORK}"

gcloud services vpc-peerings connect \
    --service=servicenetworking.googleapis.com \
    --ranges="google-managed-services-${NETWORK}" \
    --network="$NETWORK" --project="$PROJECT"
```

A Cloud SQL instance with a private address does not sit in your network. It
sits in Google's, and its address comes out of a range you allocate in yours
and then hand to service networking, which peers the two. That is what private
services access is, and it is the reason the range is created before the
instance rather than after.

There is one such connection per network and every managed service shares it,
so check before adding a second range:

```bash
gcloud services vpc-peerings list --network="$NETWORK" --project="$PROJECT"
```

If that already answers, both commands above are already done. Deleting the
connection later cuts private connectivity to everything using it, not only to
this instance.

The subnet has to be in `$REGION` and no smaller than a `/26`: Direct VPC
egress puts the service's own interface in it, and Cloud Run will not admit a
revision naming a subnet in another region.

### The database

```bash
gcloud sql instances create arena-pg \
    --database-version=POSTGRES_18 \
    --region="$REGION" \
    --tier=db-g1-small \
    --edition=enterprise \
    --network="projects/${PROJECT}/global/networks/${NETWORK}" \
    --no-assign-ip

gcloud sql databases create arena --instance=arena-pg

DB_PASSWORD="$(openssl rand -base64 24)"
gcloud sql users create arena --instance=arena-pg --password="$DB_PASSWORD"
```

`db-g1-small` holds a workshop's leaderboard without noticing. A venue of fifty
phones is still one connection from one instance, so the tier is about how long
you want to keep the history rather than about the load.

`--edition=enterprise` is load-bearing rather than decoration. Anything from
Postgres 16 up defaults to the Enterprise Plus edition, which only sells
dedicated-core machines, and the create fails with `Invalid Tier (db-g1-small)
for (ENTERPRISE_PLUS) Edition`. Shared cores live in the Enterprise edition
only; Postgres 18 runs happily there.

`--no-assign-ip` is the whole point of the section above it, and `--network` is
what makes it survivable: drop the second and the instance has no address at
all.

There is no `--ssl-mode` here and no `sslmode` in `ARENA_DB`, which is a
decision rather than an oversight: a private address is not an encrypted one,
and this connection is not encrypted. What is on the wire is the query traffic
- display names, masked emails, results - between two ends of one VPC. The
password is not among it, because Postgres 18 authenticates with SCRAM rather
than by sending it. Encrypting it is `--ssl-mode=ENCRYPTED_ONLY` on the
instance and `?sslmode=require` in `ARENA_DB`, together and not separately;
pinning the instance's CA and asking for `verify-ca` is the further step, for
the day the wire has to be trusted rather than merely private.

The arena reaches the instance by TCP on that private address, which is why
`service.yaml` carries `network-interfaces` and no `cloudsql-instances`. The
socket that annotation mounts is the Cloud SQL Auth Proxy and the proxy dials
the public address this instance does not have. `deploy.sh` reads the private
one off the instance on every deploy and renders it into `ARENA_DB`, so it is
written down nowhere and an instance rebuilt between workshops needs no edit.

What you give up is reaching the database from the laptop. A private address is
routable from inside the VPC and nowhere else, so `gcloud sql connect` and a
local `psql` both have nothing to dial. Run SQL from Cloud SQL Studio in the
console instead - it goes through the Admin API rather than the network, works
against a private-only instance, and needs `roles/cloudsql.studioUser`.

### The four secrets

```bash
for name in arena-db-password arena-secret arena-email-salt arena-service-token; do
    gcloud secrets create "$name" --replication-policy=automatic
done

printf '%s' "$DB_PASSWORD"            | gcloud secrets versions add arena-db-password    --data-file=-
printf '%s' "$(openssl rand -hex 32)" | gcloud secrets versions add arena-secret         --data-file=-
printf '%s' "$(openssl rand -hex 16)" | gcloud secrets versions add arena-email-salt     --data-file=-
printf '%s' "$(openssl rand -hex 24)" | gcloud secrets versions add arena-service-token  --data-file=-
```

`printf` rather than `echo`, and no `--data-file` pointing at a file somebody
typed into. A trailing newline is part of the secret: an `ARENA_SERVICE_TOKEN`
with one on the end is a token the specialists send and the arena never
matches, and the symptom is a shout that reports a huddle over a squad that
never moved.

`arena-email-salt` cannot be rotated afterwards without turning every returning
player into a stranger. Set it once.

### The two service accounts

```bash
gcloud iam service-accounts create futsal-arena --display-name="The futsal arena"

SA="futsal-arena@${PROJECT}.iam.gserviceaccount.com"
for role in roles/secretmanager.secretAccessor \
            roles/aiplatform.user \
            roles/logging.logWriter; do
    gcloud projects add-iam-policy-binding "$PROJECT" \
        --member="serviceAccount:${SA}" --role="$role" --condition=None
done

gcloud iam service-accounts create futsal-grounds --display-name="The futsal grounds"

GROUNDS_SA="futsal-grounds@${PROJECT}.iam.gserviceaccount.com"
for role in roles/secretmanager.secretAccessor \
            roles/logging.logWriter; do
    gcloud projects add-iam-policy-binding "$PROJECT" \
        --member="serviceAccount:${GROUNDS_SA}" --role="$role" --condition=None
done
```

`futsal-arena` and not `arena`, which is the name everything else here uses: an
account ID has to be at least six characters and `arena` is five, so the create
answers `The account ID "arena" does not have a length between 6 and 30`.
`service.yaml`'s `serviceAccountName` spells the longer name.

`--condition=None` is the difference between a loop that runs and a loop that
stops three times to ask a question. A project whose policy already holds one
conditional binding - Cloud Build's connection setup leaves one behind - makes
the flag mandatory: without it gcloud will not guess that you meant an
unconditional binding, and it prompts for the condition instead. Under
`deploy.sh` or any other script that is a hang rather than a prompt.

Three roles for the arena, one per thing the instance touches: the secrets it
starts with, Vertex for the chain, and its own logs. The database is not one of
them. `roles/cloudsql.client` is the Auth Proxy's permission - it authorises the
connector to open the tunnel - and a TCP connection to a private address with a
password in `PGPASSWORD` never asks the Cloud SQL API anything. Add it back the
day the socket comes back, and not before.

Two for the grounds, and the missing one is the point. `roles/aiplatform.user`
is a Vertex token that anything inside the instance can mint from the metadata
server, and what runs inside that instance is a web browser pointed at a page.
The arena's account would have worked and would have carried that with it; this
one holds the service token it needs to connect and nothing else.

The interface into the VPC is the Cloud Run service agent's business rather
than this account's, and in a single project it already holds the role for it.
In a Shared VPC it needs `roles/compute.networkUser` in the host project.

## Deploying

```bash
export PROJECT=your-project-id
export REGION=europe-west1
export NETWORK=default        # both default to `default` if unset
export SUBNET=default
deploy/deploy.sh
```

It warns if the tree is dirty, asks before dropping the live matches, builds
the four images in Cloud Build, reads the database's private address, renders
`service.yaml` with your project, region, network, subnet, that address and the
short commit as the tag, and replaces the arena. Then it reads the arena's own
URL back off Cloud Run, renders `grounds.yaml` with it, replaces the grounds,
stands down what both replaced, and prints the URL.

The order is the point. A grounds needs the address of the arena it plays for,
that address is assigned when the service is first made, and reading it after
the replace is what keeps it out of the yaml. Pointed at the wrong arena a
grounds connects, offers its pitches and plays nothing, while the arena people
are actually using has no grounds and answers every kick-off with a 503.

It stops before the deploy if the address it read is a public one, because a
public address is the one thing that renders cleanly here and then cannot be
reached: `private-ranges-only` egress does not carry it, and the arena spends
its startup timing out against a route nothing in the log names.

### Letting the phones in

`gcloud run services replace` never touches the invoker policy, so this is a
separate command and a one-off:

```bash
gcloud run services add-iam-policy-binding arena \
    --region="$REGION" --member=allUsers --role=roles/run.invoker
```

Without it a scanned QR code lands on a 403 and nothing on the phone explains
why.

The grounds gets no such binding and must not. Nothing dials it: it holds one
outbound socket to the arena and serves one health check, which Cloud Run's own
probes reach without going through IAM at all. To read that health check
yourself, send a token rather than opening the service:

```bash
GROUNDS="$(gcloud run services describe grounds --region="$REGION" \
    --format='value(status.url)')"
curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
    "${GROUNDS}/healthz"          # {"ok":true,"running":3,"capacity":12}
```

### The last two steps of a first deploy

**Set the public URL.** The first deploy needs no `ARENA_PUBLIC_URL`: the arena
works the address out per request from the forwarded scheme and the Host
header, which is what lets a service that has no name yet still print a QR code
that works. It says so on the way up, once per start:

```
ARENA_PUBLIC_URL unset; the public URL is being worked out per request and
should be set explicitly now that the service has a name
```

That warning in the log is how you know it is unset. Now that `deploy.sh` has
printed the `*.run.app` URL, add it to the arena container's `env:` in
`service.yaml` and deploy again:

```yaml
            - name: ARENA_PUBLIC_URL
              value: https://arena-xxxxxxxxxx.europe-west1.run.app
```

**Shout once.** The coach's startup probe is a `tcpSocket` on 8000 and that is
all it can honestly be: `adk web` binds its port and serves with no credentials
at all, and the ADK resolves them lazily at the first model call. A missing
`aiplatform` binding, a wrong project or a model that is not available in
`global` all pass the probe and surface as a shout that fails partway down the
chain. So open the deployed arena, take a seat, kick off and shout once. It is
the only check that covers the half of the service the probes cannot see.

## Rehearsing the load before the room does

`arena/tests/test_load_rehearsal.py` drives fifty rooms at ten frames a second
for a full three-minute match. It runs against a laptop by default and the
numbers it prints there are worth having, but three of the things it is meant
to check exist only up here:

- **TLS termination.** Fifty rooms is a hundred and fifty-one websockets
  through the front end rather than straight onto a socket, and the frames are
  `wss` rather than `ws`.
- **The 3600-second timeout, actually holding.** A three-minute match is the
  first thing that has run long enough on one socket to tell you the
  `timeoutSeconds` in `service.yaml` is the number in force. A default of 300
  looks identical until a match passes five minutes.
- **CPU that is not throttled between requests.** `cpu-throttling: false` is
  what lets the sweep and the bus run in the gaps. Throttled, the frames
  arrive in bursts behind each request and the latency is somebody else's
  scheduler.

```bash
cd arena
ARENA_LOAD=1 \
ARENA_LOAD_URL="$(gcloud run services describe arena --region="$REGION" \
    --format='value(status.url)')" \
    uv run pytest tests/test_load_rehearsal.py -s
```

`-s` is not optional: the report is printed and pytest swallows the stdout of a
test that passed. Do it after "Letting the phones in", because every socket in
it is an anonymous one and a 403 looks like a venue that will not open. The
laptop still needs its own Postgres running: this is a pytest run and the
suite's fixtures make their throwaway database whether or not this particular
test looks at it.

Two things it cannot measure from out here, and says so in its own output: the
event-loop lag and the bus's drop counters are read from inside the process,
and the row counts come from a database that is not this laptop's to query. The
frame rate, the latency and the delivery counts are measured from this end and
are the same numbers either way.

It plays real matches. A hundred players named `Blue 0` through `Red 49`, fifty
finished rooms and a hundred results land in Cloud SQL and on the leaderboard.
Rehearse before the workshop rather than during it. The board is computed from
`result`, so clearing that clears the board:

```sql
DELETE FROM result WHERE player_id IN (
    SELECT id FROM player WHERE email_masked LIKE '%@rehearsal.example.com');
```

From Cloud SQL Studio in the console, not from here: the instance has no public
address, so the laptop has no route to it. That leaves the fifty rooms and their
event logs behind, which nothing renders and nothing reaps. They are a few
thousand rows and the tier will not notice, but they are there.

### Clearing both rehearsals, rooms and all

The grounds capacity ramp leaves the same kind of mess under its own domain -
managers named `Rehearsal 0` upward, at `@grounds.example.com` - and after
three runs against prod it was 96 managers and 181 rooms, which is a standings
board with nothing human on the first page.

A Cloud Run job clears both, and unlike the SQL above it takes the rooms and
their event logs too. It runs on the arena's own image inside the VPC, which is
what gives it a route to the private instance:

```bash
gcloud run jobs create tidy-rehearsals --region="$REGION" \
    --image="$IMAGE" \
    --network=default --subnet=default --vpc-egress=private-ranges-only \
    --set-env-vars="ARENA_DB=$ARENA_DB,TIDY_B64=$(base64 < tidy_rehearsals.py | tr -d '\n')" \
    --set-secrets="PGPASSWORD=arena-db-password:latest" \
    --max-retries=0 --task-timeout=5m \
    --command=/app/.venv/bin/python \
    --args='^@^-c@import base64,os;exec(base64.b64decode(os.environ["TIDY_B64"]))'

gcloud run jobs execute tidy-rehearsals --region="$REGION" --wait
```

The script is `deploy/tidy_rehearsals.py`. It matches on the masked email's
domain rather than on the display name, because a human who types "Rehearsal
12" into the lobby is a manager whose row this has no business touching, and it
deletes a room only when every seat in it belongs to a rehearsal player. It
rolls back and prints its counts unless `TIDY_APPLY=1` is set, so the first run
is always the dry run.

Read what it did with `gcloud logging read`, not from the execute command -
the job's stdout goes to Cloud Logging:

```bash
gcloud logging read \
    'resource.type="cloud_run_job" AND resource.labels.job_name="tidy-rehearsals"' \
    --limit=40 --format='value(textPayload)' --freshness=20m
```

## If the first revision never goes ready

The coach and the captain bound `127.0.0.1` once, on the argument that the
containers of one Cloud Run instance share a network namespace - the same
argument `ARENA_COACH_URL=http://127.0.0.1:8000` rests on, and one that had been
reproduced under a `podman pod`. What that reproduction could not tell us is
where Cloud Run runs a `tcpSocket` startup probe from.

Revision `arena-00001` answered it: not from in there. The captain logged

```
INFO:     Uvicorn running on http://127.0.0.1:8001
ERROR:    STARTUP TCP probe failed 40 times consecutively for container
          "captain" on port 8001. Connection failed with status DEADLINE_EXCEEDED.
```

- the server up and listening, the probe against that same port timing out
every time, over five instance starts. The coach never ran to fail the same way
because it sits behind the captain in `container-dependencies`, so both were
widened together rather than one failed deploy apart.

So both sidecars bind `0.0.0.0` now, and a bind narrowed back to the loopback is
a revision that never goes ready. `arena/tests/test_images.py` holds that.

Widening published nothing. Neither server authenticates anything, and what
isolates them is that neither declares a port: Cloud Run routes external
requests to the ingress container alone, and Direct VPC egress is egress, so
nothing in the VPC can dial an instance either. The bind was never the lock.

The captain's bind is a second variable rather than a wider `CAPTAIN_HOST`.
`to_a2a` writes `CAPTAIN_HOST` into the agent card's `rpc_url`, and the coach
dials the url out of the card rather than the address it fetched the card from,
so that one stays `127.0.0.1` - an address to connect to - while `CAPTAIN_BIND`
is the wildcard to listen on. Collapsing them back into one breaks whichever end
loses.

If the one that fails is `arena`, the database is the first thing to rule out.
Its `/health` opens a connection, so an unreachable database reads as a startup
probe that never passes, and the log has psycopg waiting on a private address
rather than anything about networking. Three things make that address
unreachable and all three are in this file: the peering
(`gcloud services vpc-peerings list --network="$NETWORK"`), the subnet being in
`$REGION` and in the same network as the peering, and the instance still having
no public address for `deploy.sh` to have picked up instead.

The arena's `0.0.0.0` was never in question and stays: Cloud Run's container
contract requires the ingress container to listen on `0.0.0.0:$PORT`.

## Building the images here instead

The deploy builds in Cloud Build. It is amd64 natively, which this Mac is not,
and it already holds the credentials for the push. A local build is a debugging
aid rather than a deploy path:

```bash
podman machine start
podman build --platform linux/amd64 -f arena/Dockerfile          -t arena:local   .
podman build --platform linux/amd64 -f game/Dockerfile.coach     -t coach:local   .
podman build --platform linux/amd64 -f game/Dockerfile.captain   -t captain:local .
podman build --platform linux/amd64 -f grounds/Dockerfile        -t grounds:local .
```

`--platform linux/amd64` because Cloud Run will not run the arm64 image an
Apple Silicon Mac builds by default. It costs an emulated `npm ci`, four
emulated `uv sync`s and an emulated browser download, which is the other reason
the real build is not here. Drop the flag to build the grounds for this Mac,
which is the only way the run below is quick enough to be worth doing.

Running the arena image by hand, against this machine's Postgres:

```bash
podman run --rm -p 8080:8080 \
  -e ARENA_DB="postgresql://$(whoami)@host.containers.internal:5432/arena" \
  -e ARENA_SECRET=dummy-session-secret \
  -e ARENA_EMAIL_SALT=dummy-salt \
  -e ARENA_SERVICE_TOKEN=dummy-token \
  arena:local

curl -s localhost:8080/health          # {"ok":true,"service":"arena","swept_ago":2.1}
```

`curl -s` and not `curl -I`: `/health` is a FastAPI route and GET-only, so a
HEAD against a perfectly healthy arena answers 405.

`swept_ago` is the seconds since the watchdog last got all the way round, and
it is the whole of what this route answers for. Over `HEALTH_STALE_SECONDS`
the same call answers 503 with `"ok":false`, which is the liveness probe's cue
to have the instance replaced. A number that climbs rather than sitting under
the sweep interval means the loop has stopped turning or the database has
stopped answering; the log says which, once, when it crosses.

The image bakes in `ARENA_ENV=production`, so it refuses to start without those
three. That refusal is the image working.

Running the grounds image by hand, against an arena on this machine:

```bash
podman run --rm -p 8004:8004 \
  -e ARENA_URL=http://host.containers.internal:8003 \
  -e ARENA_SERVICE_TOKEN=dev-token \
  grounds:local

curl -s localhost:8004/healthz         # {"ok":true,"running":0,"capacity":12}
```

`ok` goes true when Chromium has launched and the arena has served it
`/pitch/host.html`, which is a second or two. Until then the same route answers
503 with the same body, because a startup probe reads the status and never the
body - and so the log is the thing to watch if it stays false: it says which of
the two it is still waiting for, every few seconds, by name.

## Watching it

```bash
gcloud run services describe arena --region="$REGION"
gcloud run services logs read arena --region="$REGION"
gcloud beta run services logs tail arena --region="$REGION"
gcloud run revisions list --service=arena --region="$REGION"
```

`tail` is `beta` and only `beta`: the GA group has `read` alone, and
`gcloud run services logs tail` answers `Invalid choice: 'tail'`.

Logs from all three containers arrive interleaved and each line carries the
container name, which is the quickest way to tell an arena that refused a patch
from a specialist that never sent one.

The grounds answers the same four commands with `grounds` in place of `arena`,
and one line in its log is worth knowing:

```
connected to the arena at https://arena-... , offering 20 pitches
```

Without it, nothing is being played anywhere and every kick-off in the venue is
a 503. The arena's side of the same moment is `grounds joined, capacity 20; 1
connected`, and the pair is the whole handshake.

## When Cloud Run recycles the arena

It does, on its own schedule, and `maxScale: 1` means there is no second
instance to carry the venue while it happens. Seen in prod during the capacity
rehearsal, and worth recognising in a log before a workshop rather than during
one:

```
grounds: the arena socket dropped (received 1012 (service restart)); back in 0.5s
grounds: the arena socket dropped (timed out during opening handshake); back in 1.0s
grounds: connected to the arena at https://arena-..., offering 20 pitches
```

Twenty-three seconds end to end, and the grounds' backoff handled it without
help - that part works. What did not survive is the matches. Every room's
socket comes back at once against an arena that is still booting, and the page
logs `WebSocket is closed before the connection is established` and, for the
ones that got far enough, `Unexpected response code: 429`. A match whose socket
does not come back is dropped, and the supervisor counts it finished:

```
grounds: page: WebSocket connection to '.../ws/rooms/GQV2' failed: ...
supervisor: 3 finished: 5CXX, GQV2, J36K (18 of 64)
```

That is five matches lost to one recycle. The spec accepts the same failure for
a grounds restart - "one process holds the venue" - and this is the arena's
half of it, which the spec does not call out. Nothing here reconnects a match
that was mid-play: kick-off is the only thing that starts one. It is a real
limit on a long workshop and it is not a tuning knob; closing it means the
grounds resuming a room it already holds rather than dropping it, which is a
design change and not a deploy setting.

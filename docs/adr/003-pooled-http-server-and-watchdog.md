# ADR-003: PooledHTTPServer architecture and in-process watchdog

**Status:** Accepted
**Date:** 2026-05-08

## Context

The original CBNC server used Python's stock `HTTPServer`, which is
single-threaded: one stalled client blocked every other request. The
production server occasionally hung indefinitely with no visible cause,
and several latent vulnerabilities were known:

- **Slowloris**: `self.rfile.read()` had no timeout. A client that sent
  partial headers and stalled would hold the only worker forever.
- **TOCTOU**: approval / project-limit checks were performed *outside*
  `file_lock`, then `increment_build_number` re-acquired the lock to
  write. Under any future multi-threading, two requests could both see
  "limit not yet reached" and both pass it.
- **No restart hook**: a wedged process could not signal its container
  orchestrator to restart it.

This ADR records the design choices made when fixing all three at once.

## Decision

### Concurrency model: bounded queue + fixed daemon worker pool

The accept loop runs only in the main thread and `put_nowait`s each
accepted socket into a `queue.Queue(maxsize=max_workers)`. N daemon
threads (`max_workers`, default 64) loop on `Queue.get(timeout=0.5)`
and call `finish_request`.

Hard concurrency cap: `max_workers` in flight + `max_workers` queued.
Excess connections are closed immediately on the accept thread (no
HTTP body, no I/O).

### Why not `concurrent.futures.ThreadPoolExecutor`

1. Its worker threads are non-daemon. `_python_exit`'s atexit handler
   joins them, which would block process exit if a worker is wedged —
   exactly the case the watchdog was added to handle.
2. Its work queue is unbounded by default. Bounding it (via a custom
   queue) is possible but undocumented and version-fragile.
3. `executor.submit()` does not expose a clean nonblocking-or-reject
   semantic; `cancel_futures=True` cancels pending futures but does
   not close the underlying sockets, so server_close needs an extra
   sweep regardless.

A 50-line manual pool gives daemon threads, bounded backpressure, and
deterministic shutdown without fighting the Executor abstraction.

### Why not `ThreadingHTTPServer` / `ThreadingMixIn`

Both spawn a fresh thread per accepted socket. A Slowloris attack —
1 connection per second held open — creates 1 thread per second with
no upper bound. The bounded queue is the whole point.

### Queue size welded to `max_workers`

`maxsize=max_workers` means the burst tolerance equals the steady-state
concurrency. This is intentionally simple — operators have one knob,
not two — and the trade-off (no separate burst buffer) is acceptable
given typical CBNC workloads (bursts of CMake configure calls, all
sub-100ms). A future `--queue-size` flag is non-breaking if the need
arises.

### 503 vs 429 on overload

429 is rate-limit semantics ("you, specifically, sent too many"). 503
is server-state semantics ("nobody can be served right now"). Pool
saturation is the second case: any caller would hit it, and the right
client behavior is "back off and retry" not "I'm being throttled". 503
also lets the rate-limit and pool-overload paths emit different
metrics if needed.

### No 503 body, just close

Earlier iterations wrote a 503 JSON body from the accept thread under
a 100 ms send timeout. Under sustained overload — exactly the case
this code path exists for — every refused client steals up to 100 ms
from the next `accept()`. The bounded-queue design is meant to make
refusal cheap; synchronous network I/O on the accept thread defeats
that.

The current implementation just `shutdown_request`s. Clients see RST
or FIN. Existing tests already tolerate this case (see ADR-001 on
TCP-RST early errors); the `_expect_rejection` helper covers it.

### Daemon workers, no join on shutdown

`server_close` sets a shutdown event and drains the pending queue, but
does **not** join the worker threads. Workers exit on the next 0.5 s
poll tick. We do not block shutdown waiting for a stuck handler:
that is the failure mode the watchdog exists to handle.

### Wall-clock deadline against header-Slowloris

`BuildNumberHandler.timeout` is a per-recv timeout (1 s). It alone is
bypassable: an attacker who sends one header byte every 0.9 s never
trips it but holds the worker for `timeout × header_count` seconds.
Adding `max_request_seconds = 5` (a daemon `threading.Timer` armed in
`setup`, cancelled in `finish`) caps total per-request worker hold
regardless of recv pacing.

### Watchdog: separate thread, `os._exit(1)`

The watchdog is a dedicated daemon thread polling `GET /healthz` over
loopback. After `--watchdog-failures` (default 3) consecutive failures,
it calls `os._exit(1)`.

Why `os._exit`, not `sys.exit`: in the failure mode this guards
against (workers stuck in I/O, deadlocked finalizers, exhausted thread
pool), normal interpreter teardown could itself hang. `os._exit` jumps
straight to process termination so the container orchestrator
(Docker `restart_policy`, Railway, k8s) can restart us.

Why the watchdog probes through the normal HTTP path (not, say, an
internal health flag): a saturated worker pool is precisely the
signal we want — `/healthz` will queue, time out, and tick the
failure counter. An internal flag would not catch that.

Why default OFF: tests and local development should not surprise-kill
themselves on a transient network blip. Containers explicitly opt in
via the Dockerfile `CMD`.

### Rate-limit cleanup: lazy, not periodic

The rate-limit state (`rate_tracker`, `temp_bans`) is cleaned
opportunistically inside `check_rate_limit` rather than from a
background thread:

- The IP being checked has its own bucket purged (old timestamps
  dropped, expired temp ban removed) on every request — this was
  already happening pre-hardening for correctness.
- Every `_RATE_SWEEP_EVERY` (256) allowed requests, a global
  `cleanup_rate_data()` runs under `rate_lock` to evict dormant IPs.

This removes the previous `_start_cleanup_timer` / `threading.Timer`
recursion entirely. One fewer background thread, one fewer pattern
in the codebase, and the behavior under load is unchanged: an active
IP gets a fresh bucket each request, and idle IPs get evicted within
the next 256 incoming requests. Under no traffic at all there are no
dormant entries to begin with.

### Watchdog uses `Event.wait`, not `time.sleep`

The watchdog loop waits on a `threading.Event` (`stop_event.wait(interval)`)
rather than `time.sleep(interval)`. Functionally equivalent on the
happy path; the difference matters for shutdown and testing — setting
the event wakes the loop immediately, so tests can stop the watchdog
deterministically without monkeypatching `time.sleep`, and a future
graceful-shutdown path can stop the thread without waiting up to
`interval` seconds.

## Consequences

- A wedged process restarts in `interval × failures` seconds (default
  30 s) instead of hanging forever.
- Workers can deadlock or block on filesystem I/O without taking down
  the accept loop (modulo the bounded queue eventually filling).
- One configuration knob (`--max-threads`) controls both concurrency
  and burst tolerance. Operators who want them decoupled need a code
  change for now.
- 503 responses are bodyless. Curious clients see only the TCP close;
  observability tools that scrape HTTP status codes can still see 503
  via Docker's HEALTHCHECK exit code or via the watchdog's stderr log.
- `os._exit(1)` skips `atexit` handlers. Combined with the new
  `flush() + os.fsync()` in `save_json_file`, in-flight counter writes
  are durable across an emergency exit.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| `ThreadPoolExecutor` | Non-daemon workers, atexit-join blocks shutdown |
| `ThreadingHTTPServer` | No bound on threads; Slowloris-amplifying |
| Async / `asyncio` | Whole-server rewrite; not justified for this load profile |
| In-handler 503 body | Synchronous I/O on accept thread under attack |
| `sys.exit` from watchdog | Can hang on the same I/O the watchdog is detecting |
| ADR-001's QuietHTTPServer pattern | Replaced by PooledHTTPServer; ADR-001 updated |

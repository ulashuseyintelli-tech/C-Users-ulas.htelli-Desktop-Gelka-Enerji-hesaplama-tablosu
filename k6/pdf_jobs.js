/**
 * k6 Load Test — PDF Jobs (Senaryo S1, S2, S3)
 *
 * Kullanım:
 *   k6 run --env BASE_URL=https://staging.example.com \
 *           --env ADMIN_KEY=<key> \
 *           --env TEMPLATE=invoice \
 *           --env SCENARIO=steady \
 *           k6/pdf_jobs.js
 *
 * Senaryolar:
 *   steady  — S1: 2→5→10 job/dk ramp, 20 dk
 *   burst   — S2: 200 job / 60 sn, sonra 10 dk drain
 *   retry   — S3: 5 job/dk, %20 ağır template mix, 15 dk
 */

import http from "k6/http";
import { check, sleep, group } from "k6";
import { Counter, Trend, Gauge } from "k6/metrics";

// ── Config ──────────────────────────────────────────────────────

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const ADMIN_KEY = __ENV.ADMIN_KEY || "";
const TEMPLATE = __ENV.TEMPLATE || "invoice";
const HEAVY_TEMPLATE = __ENV.HEAVY_TEMPLATE || "__heavy_timeout_test";
const SCENARIO = (__ENV.SCENARIO || "steady").toLowerCase();

const HEADERS = {
  "Content-Type": "application/json",
};
if (ADMIN_KEY) {
  HEADERS["X-Admin-Key"] = ADMIN_KEY;
}

// ── Custom Metrics ──────────────────────────────────────────────

const jobsCreated = new Counter("pdf_jobs_created");
const jobsSucceeded = new Counter("pdf_jobs_succeeded");
const jobsFailed = new Counter("pdf_jobs_failed");
const jobsStuck = new Counter("pdf_jobs_stuck");
const jobDuration = new Trend("pdf_job_duration_ms");
const queueDepthMax = new Gauge("pdf_queue_depth_max");
const drainTime = new Trend("pdf_drain_time_ms");

// ── Scenario Options ────────────────────────────────────────────

const SCENARIOS = {
  steady: {
    executor: "ramping-arrival-rate",
    startRate: 2,
    timeUnit: "1m",
    preAllocatedVUs: 20,
    maxVUs: 50,
    stages: [
      { duration: "5m", target: 2 },
      { duration: "5m", target: 5 },
      { duration: "5m", target: 10 },
      { duration: "5m", target: 10 },
    ],
  },
  burst: {
    executor: "constant-arrival-rate",
    rate: 200,
    timeUnit: "1m",
    duration: "1m",
    preAllocatedVUs: 50,
    maxVUs: 100,
  },
  retry: {
    executor: "constant-arrival-rate",
    rate: 5,
    timeUnit: "1m",
    duration: "15m",
    preAllocatedVUs: 10,
    maxVUs: 30,
  },
};

export const options = {
  scenarios: {
    pdf: SCENARIOS[SCENARIO] || SCENARIOS.steady,
  },
  thresholds: {
    pdf_jobs_stuck: ["count<1"],
  },
};

// ── Helpers ──────────────────────────────────────────────────────

function createJob(templateName, payload) {
  const res = http.post(
    `${BASE_URL}/pdf/jobs`,
    JSON.stringify({ template_name: templateName, payload: payload || {} }),
    { headers: HEADERS, tags: { name: "POST /pdf/jobs" } }
  );
  return res;
}

function pollUntilTerminal(jobId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  const pollInterval = 2; // seconds

  while (Date.now() < deadline) {
    const res = http.get(`${BASE_URL}/pdf/jobs/${jobId}`, {
      headers: HEADERS,
      tags: { name: "GET /pdf/jobs/{id}" },
    });

    if (res.status === 200) {
      const body = res.json();
      if (body.status === "succeeded" || body.status === "failed" || body.status === "expired") {
        return body;
      }
    }
    sleep(pollInterval);
  }
  return null; // stuck
}

function downloadPdf(jobId) {
  return http.get(`${BASE_URL}/pdf/jobs/${jobId}/download`, {
    headers: HEADERS,
    tags: { name: "GET /pdf/jobs/{id}/download" },
    responseType: "binary",
  });
}

// ── Main VU Function ────────────────────────────────────────────

export default function () {
  // S3 retry scenario: %20 heavy template mix
  let templateName = TEMPLATE;
  if (SCENARIO === "retry" && Math.random() < 0.2) {
    templateName = HEAVY_TEMPLATE;
  }

  const payload = {
    title: `k6-test-${Date.now()}`,
    data: { iteration: __ITER, vu: __VU },
  };

  group("create_job", function () {
    const createRes = createJob(templateName, payload);
    const created = check(createRes, {
      "create: status 202": (r) => r.status === 202,
      "create: has job_id": (r) => r.json("job_id") !== undefined,
    });

    if (!created || createRes.status !== 202) {
      // 403 (template not allowed) veya 503 (queue unavailable) — beklenen hata
      jobsFailed.add(1);
      return;
    }

    jobsCreated.add(1);
    const jobId = createRes.json("job_id");
    const startTime = Date.now();

    // Poll for completion (5 dk timeout — stuck detection)
    const result = pollUntilTerminal(jobId, 5 * 60 * 1000);

    if (result === null) {
      jobsStuck.add(1);
      return;
    }

    const elapsed = Date.now() - startTime;
    jobDuration.add(elapsed);

    if (result.status === "succeeded") {
      jobsSucceeded.add(1);

      // Download doğrulaması (sadece steady ve retry'da)
      if (SCENARIO !== "burst") {
        group("download", function () {
          const dlRes = downloadPdf(jobId);
          check(dlRes, {
            "download: status 200": (r) => r.status === 200,
            "download: content-type pdf": (r) =>
              (r.headers["Content-Type"] || "").includes("application/pdf"),
          });
        });
      }
    } else {
      jobsFailed.add(1);
    }
  });
}

// ── Burst Drain Phase ───────────────────────────────────────────
// burst senaryosunda: 1 dk burst sonrası 10 dk drain bekleme
// k6 teardown'da queue depth kontrolü

export function teardown(_data) {
  if (SCENARIO !== "burst") return;

  console.log("=== DRAIN PHASE: 10 dk bekleniyor ===");
  const drainStart = Date.now();
  const drainDeadline = drainStart + 10 * 60 * 1000;
  let lastDepth = -1;

  while (Date.now() < drainDeadline) {
    // /metrics endpoint'inden queue depth oku (varsa)
    const metricsRes = http.get(`${BASE_URL}/metrics`, {
      tags: { name: "GET /metrics (drain)" },
    });

    if (metricsRes.status === 200) {
      const match = metricsRes.body.match(
        /ptf_admin_pdf_queue_depth\s+(\d+(?:\.\d+)?)/
      );
      if (match) {
        const depth = parseFloat(match[1]);
        if (depth > lastDepth) queueDepthMax.add(depth);
        lastDepth = depth;

        if (depth === 0) {
          const elapsed = Date.now() - drainStart;
          drainTime.add(elapsed);
          console.log(`Queue drained in ${elapsed}ms`);
          return;
        }
      }
    }
    sleep(10);
  }

  console.log("WARN: Queue did not fully drain within 10 minutes");
  drainTime.add(10 * 60 * 1000);
}

/**
 * k6 Load Test — API Endpoint Mix + Guard Decision Layer (Senaryo S4)
 *
 * Kullanım:
 *   k6 run --env BASE_URL=https://staging.example.com \
 *           --env ADMIN_KEY=<key> \
 *           --env TENANT_ID=tenant-a \
 *           --env SUB_SCENARIO=4a \
 *           k6/api_mix.js
 *
 * Alt senaryolar:
 *   4a — Guard OFF baseline (10 dk, 20 RPS)
 *   4b — Guard SHADOW (10 dk, 20 RPS, aynı mix)
 *   4c — Guard ENFORCE (10 dk, 20 RPS, high-risk'te enforce)
 */

import http from "k6/http";
import { check, sleep, group } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";
import { SharedArray } from "k6/data";

// ── Config ──────────────────────────────────────────────────────

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const ADMIN_KEY = __ENV.ADMIN_KEY || "";
const TENANT_ID = __ENV.TENANT_ID || "";
const SUB_SCENARIO = (__ENV.SUB_SCENARIO || "4a").toLowerCase();

function buildHeaders(extra) {
  const h = { "Content-Type": "application/json" };
  if (ADMIN_KEY) h["X-Admin-Key"] = ADMIN_KEY;
  if (TENANT_ID) h["X-Tenant-Id"] = TENANT_ID;
  return Object.assign(h, extra || {});
}

const HEADERS = buildHeaders();

// ── Custom Metrics ──────────────────────────────────────────────

const reqByEndpoint = new Counter("api_mix_requests");
const errByEndpoint = new Counter("api_mix_errors");
const guardBlocks = new Counter("api_mix_guard_blocks");       // 503 from guard
const latencyByEndpoint = new Trend("api_mix_latency_ms");
const errorRate = new Rate("api_mix_error_rate");
const guardBlockRate = new Rate("api_mix_guard_block_rate");

// ── Scenario Options ────────────────────────────────────────────

export const options = {
  scenarios: {
    api_mix: {
      executor: "constant-arrival-rate",
      rate: 20,
      timeUnit: "1s",
      duration: "10m",
      preAllocatedVUs: 30,
      maxVUs: 60,
    },
  },
  thresholds: {
    // 4a/4b: error rate < %2, 4c: guard block'lar beklenen
    api_mix_error_rate: SUB_SCENARIO === "4c"
      ? [{ threshold: "rate<0.15", abortOnFail: false }]
      : [{ threshold: "rate<0.02", abortOnFail: false }],
  },
};

// ── Endpoint Mix Table ──────────────────────────────────────────
// Yük testi planı Senaryo 4 endpoint mix tablosuna birebir uyumlu.
// Kümülatif ağırlık ile weighted random selection.

const ENDPOINTS = [
  // { weight, method, path, riskClass, buildBody }
  {
    weight: 40,
    method: "GET",
    path: "/admin/market-prices",
    risk: "low",
    tag: "GET /admin/market-prices",
    exec: function () {
      return http.get(`${BASE_URL}/admin/market-prices?page=1&page_size=10`, {
        headers: HEADERS,
        tags: { name: this.tag, risk_class: this.risk },
      });
    },
  },
  {
    weight: 20,
    method: "GET",
    path: "/admin/market-prices/history",
    risk: "low",
    tag: "GET /admin/market-prices/{id}/history",
    exec: function () {
      // Rastgele bir dönem seç
      const periods = ["2024-11", "2024-12", "2025-01", "2025-02"];
      const period = periods[Math.floor(Math.random() * periods.length)];
      return http.get(`${BASE_URL}/admin/market-prices/history?period=${period}`, {
        headers: HEADERS,
        tags: { name: this.tag, risk_class: this.risk },
      });
    },
  },
  {
    weight: 15,
    method: "POST",
    path: "/admin/market-prices",
    risk: "medium",
    tag: "POST /admin/market-prices",
    exec: function () {
      const period = `2099-${String(Math.floor(Math.random() * 12) + 1).padStart(2, "0")}`;
      return http.post(
        `${BASE_URL}/admin/market-prices`,
        JSON.stringify({
          period: period,
          ptf_tl_per_mwh: 2800 + Math.random() * 500,
          yekdem_tl_per_mwh: 340 + Math.random() * 50,
          source_note: `k6-load-test-${SUB_SCENARIO}`,
        }),
        { headers: HEADERS, tags: { name: this.tag, risk_class: this.risk } }
      );
    },
  },
  {
    weight: 5,
    method: "POST",
    path: "/admin/market-prices/import",
    risk: "high",
    tag: "POST /admin/market-prices/import",
    exec: function () {
      // Import preview — daha güvenli (apply değil)
      return http.post(
        `${BASE_URL}/admin/market-prices/import/preview`,
        JSON.stringify({
          records: [
            { period: "2099-06", ptf_tl_per_mwh: 3000, yekdem_tl_per_mwh: 360 },
          ],
        }),
        { headers: HEADERS, tags: { name: this.tag, risk_class: this.risk } }
      );
    },
  },
  {
    weight: 10,
    method: "POST",
    path: "/pdf/jobs",
    risk: "medium",
    tag: "POST /pdf/jobs",
    exec: function () {
      return http.post(
        `${BASE_URL}/pdf/jobs`,
        JSON.stringify({
          template_name: __ENV.TEMPLATE || "invoice",
          payload: { title: `k6-mix-${Date.now()}` },
        }),
        { headers: HEADERS, tags: { name: this.tag, risk_class: this.risk } }
      );
    },
  },
  {
    weight: 5,
    method: "GET",
    path: "/pdf/jobs/{id}",
    risk: "low",
    tag: "GET /pdf/jobs/{id}",
    exec: function () {
      // Var olmayan job_id ile sorgula — 404 beklenen, guard davranışını test eder
      return http.get(`${BASE_URL}/pdf/jobs/nonexistent-${__VU}-${__ITER}`, {
        headers: HEADERS,
        tags: { name: this.tag, risk_class: this.risk },
      });
    },
  },
  {
    weight: 5,
    method: "POST",
    path: "/admin/telemetry/events",
    risk: "skip",
    tag: "POST /admin/telemetry/events",
    exec: function () {
      // Telemetry endpoint — guard skip path, auth yok
      const telemetryHeaders = { "Content-Type": "application/json" };
      if (TENANT_ID) telemetryHeaders["X-Tenant-Id"] = TENANT_ID;
      return http.post(
        `${BASE_URL}/admin/telemetry/events`,
        JSON.stringify({
          events: [
            {
              event: "ptf_admin.k6_load_test",
              properties: { scenario: SUB_SCENARIO, vu: String(__VU) },
              timestamp: new Date().toISOString(),
            },
          ],
        }),
        { headers: telemetryHeaders, tags: { name: this.tag, risk_class: this.risk } }
      );
    },
  },
];

// Kümülatif ağırlık dizisi oluştur
const TOTAL_WEIGHT = ENDPOINTS.reduce((s, e) => s + e.weight, 0);
const CUMULATIVE = [];
let cumSum = 0;
for (const ep of ENDPOINTS) {
  cumSum += ep.weight;
  CUMULATIVE.push(cumSum);
}

function pickEndpoint() {
  const r = Math.random() * TOTAL_WEIGHT;
  for (let i = 0; i < CUMULATIVE.length; i++) {
    if (r < CUMULATIVE[i]) return ENDPOINTS[i];
  }
  return ENDPOINTS[ENDPOINTS.length - 1];
}

// ── Main VU Function ────────────────────────────────────────────

export default function () {
  const ep = pickEndpoint();

  const res = ep.exec();

  // Metrik toplama
  reqByEndpoint.add(1, { endpoint: ep.tag, risk_class: ep.risk });
  latencyByEndpoint.add(res.timings.duration, { endpoint: ep.tag });

  const isError = res.status >= 400 && res.status !== 404; // 404 beklenen (pdf job query)
  const isGuardBlock = res.status === 503 && isGuardResponse(res);

  if (isGuardBlock) {
    guardBlocks.add(1, { endpoint: ep.tag, risk_class: ep.risk });
    guardBlockRate.add(true);
  } else {
    guardBlockRate.add(false);
  }

  if (isError && !isGuardBlock) {
    errByEndpoint.add(1, { endpoint: ep.tag, risk_class: ep.risk });
    errorRate.add(true);
  } else if (!isGuardBlock) {
    errorRate.add(false);
  }

  // Senaryo-spesifik check'ler
  if (SUB_SCENARIO === "4a" || SUB_SCENARIO === "4b") {
    check(res, {
      "no guard 503": (r) => !isGuardBlock,
    });
  }

  if (SUB_SCENARIO === "4c" && isGuardBlock) {
    // Enforce modda: sadece high-risk endpoint'lerde 503 beklenir
    check(res, {
      "guard block only on high-risk": () => ep.risk === "high",
    });
  }
}

function isGuardResponse(res) {
  try {
    const body = res.json();
    return (
      body.errorCode === "OPS_GUARD_STALE" ||
      body.errorCode === "OPS_GUARD_INSUFFICIENT"
    );
  } catch (_) {
    return false;
  }
}

// ── Setup / Teardown ────────────────────────────────────────────

export function setup() {
  // Sağlık kontrolü
  const healthRes = http.get(`${BASE_URL}/health`);
  check(healthRes, {
    "setup: health OK": (r) => r.status === 200,
  });

  // Guard decision counter baseline (varsa)
  let baselineDecisions = 0;
  const metricsRes = http.get(`${BASE_URL}/metrics`);
  if (metricsRes.status === 200) {
    const match = metricsRes.body.match(
      /ptf_admin_guard_decision_requests_total\s+(\d+(?:\.\d+)?)/
    );
    if (match) baselineDecisions = parseFloat(match[1]);
  }

  console.log(`=== API Mix S4 — sub-scenario: ${SUB_SCENARIO} ===`);
  console.log(`BASE_URL: ${BASE_URL}`);
  console.log(`TENANT_ID: ${TENANT_ID || "(none)"}`);
  console.log(`Baseline guard decisions: ${baselineDecisions}`);

  return { baselineDecisions };
}

export function teardown(data) {
  // Guard decision counter delta
  const metricsRes = http.get(`${BASE_URL}/metrics`);
  if (metricsRes.status === 200) {
    const match = metricsRes.body.match(
      /ptf_admin_guard_decision_requests_total\s+(\d+(?:\.\d+)?)/
    );
    if (match) {
      const final = parseFloat(match[1]);
      const delta = final - (data.baselineDecisions || 0);
      console.log(`Guard decision delta: ${delta}`);

      if (SUB_SCENARIO === "4a") {
        // Guard OFF — decision counter artmamalı
        if (delta > 0) {
          console.log("WARN: Guard decisions incremented with guard OFF");
        }
      } else {
        // 4b/4c — decision counter artmalı
        if (delta === 0) {
          console.log("WARN: Guard decisions did NOT increment with guard ON");
        }
      }
    }

    // Memory baseline/final karşılaştırması
    const memMatch = metricsRes.body.match(
      /process_resident_memory_bytes\s+(\d+(?:\.\d+)?)/
    );
    if (memMatch) {
      const memBytes = parseFloat(memMatch[1]);
      const memMB = (memBytes / 1024 / 1024).toFixed(1);
      console.log(`Final process memory: ${memMB} MB`);
    }
  }

  console.log("=== API Mix S4 teardown complete ===");
}

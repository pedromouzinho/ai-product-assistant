function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export function getFilenameExtension(filename) {
  const safe = String(filename || "").trim().toLowerCase();
  const idx = safe.lastIndexOf(".");
  return idx >= 0 ? safe.slice(idx) : "";
}

export function getMaxBytesForFile(file, maxUploadFileBytes, maxUploadFileBytesByExtension = {}) {
  const extension = getFilenameExtension(file && file.name);
  const extensionLimit = Number((maxUploadFileBytesByExtension || {})[extension]);
  if (Number.isFinite(extensionLimit) && extensionLimit > 0) {
    return extensionLimit;
  }
  return maxUploadFileBytes;
}

export function isTabularFile(file) {
  const extension = getFilenameExtension(file && file.name);
  return [".csv", ".tsv", ".xlsx", ".xlsb", ".xls"].includes(extension);
}

export async function uploadSingleFileSync(authFetchFn, apiUrl, file, conversationId) {
  const formData = new FormData();
  formData.append("file", file);
  if (conversationId) formData.append("conversation_id", conversationId);
  const res = await authFetchFn(apiUrl + "/upload", { method: "POST", body: formData });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Erro upload");
  }
  const data = await res.json();
  if (data && data.status === "completed" && data.result) {
    return data.result;
  }
  if (data && data.status === "queued" && data.job_id) {
    return await waitUploadJob(authFetchFn, apiUrl, data.job_id);
  }
  return data;
}

export async function queueUploadJob(authFetchFn, apiUrl, file, conversationId) {
  const formData = new FormData();
  formData.append("file", file);
  if (conversationId) formData.append("conversation_id", conversationId);
  const res = await authFetchFn(apiUrl + "/upload/async", { method: "POST", body: formData });
  if (!res.ok) {
    if (res.status === 404 || res.status === 405) {
      return null;
    }
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Erro ao criar job de upload");
  }
  return await res.json();
}

export async function queueUploadJobStream(authFetchFn, apiUrl, file, conversationId) {
  const params = new URLSearchParams();
  if (conversationId) params.set("conversation_id", conversationId);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const res = await authFetchFn(apiUrl + "/upload/stream/async" + suffix, {
    method: "POST",
    headers: {
      "Content-Type": file.type || "application/octet-stream",
      "X-Upload-Filename": encodeURIComponent(file.name || "upload.bin"),
    },
    body: file,
  });
  if (!res.ok) {
    if (res.status === 404 || res.status === 405) {
      return null;
    }
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Erro ao criar job de upload em streaming");
  }
  return await res.json();
}

export async function queueUploadJobsBatch(authFetchFn, apiUrl, files, conversationId) {
  const formData = new FormData();
  files.forEach(f => formData.append("files", f));
  if (conversationId) formData.append("conversation_id", conversationId);
  const res = await authFetchFn(apiUrl + "/upload/batch/async", { method: "POST", body: formData });
  if (!res.ok) {
    if (res.status === 404 || res.status === 405) {
      return null;
    }
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Erro ao criar jobs de upload");
  }
  return await res.json();
}

export async function waitUploadJob(authFetchFn, apiUrl, jobId, uploadJobTimeoutMs = 600000, uploadPollIntervalMs = 1200) {
  const deadline = Date.now() + uploadJobTimeoutMs;
  while (Date.now() < deadline) {
    const res = await authFetchFn(apiUrl + "/api/upload/status/" + encodeURIComponent(jobId), { method: "GET" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Erro ao consultar estado do upload");
    }
    const job = await res.json();
    if (job.status === "completed") {
      if (!job.result) throw new Error("Upload concluído sem resultado");
      return job.result;
    }
    if (job.status === "failed") {
      throw new Error(job.error || "Falha no processamento do ficheiro");
    }
    await sleep(uploadPollIntervalMs);
  }
  throw new Error("Timeout no processamento do ficheiro. Tenta novamente.");
}

export async function fetchUploadStatusBatch(authFetchFn, apiUrl, authHeaders, jobIds) {
  const res = await authFetchFn(apiUrl + "/api/upload/status/batch", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ job_ids: jobIds }),
  });
  if (!res.ok) {
    if (res.status === 404 || res.status === 405) {
      return null;
    }
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Erro ao consultar estado batch dos uploads");
  }
  return await res.json();
}

export async function resolveQueuedUploadsLegacy(authFetchFn, apiUrl, queuedJobs, concurrency, onProgress, uploadJobTimeoutMs, uploadPollIntervalMs) {
  if (!Array.isArray(queuedJobs) || queuedJobs.length === 0) {
    return { results: [], errors: [] };
  }
  const results = new Array(queuedJobs.length);
  const errors = [];
  let cursor = 0;
  let processed = 0;

  async function worker() {
    while (true) {
      const idx = cursor++;
      if (idx >= queuedJobs.length) return;
      const job = queuedJobs[idx];
      try {
        const data = await waitUploadJob(authFetchFn, apiUrl, job.job_id, uploadJobTimeoutMs, uploadPollIntervalMs);
        results[idx] = { ok: true, data, filename: job.filename };
        processed += 1;
        if (onProgress) onProgress(processed, queuedJobs.length, job.filename, true);
      } catch (err) {
        const message = (err && err.message) ? err.message : "Falha no processamento do ficheiro";
        results[idx] = { ok: false, error: message, filename: job.filename };
        errors.push({ filename: job.filename, error: message });
        processed += 1;
        if (onProgress) onProgress(processed, queuedJobs.length, job.filename, false);
      }
    }
  }

  const workerCount = Math.max(1, Math.min(concurrency, queuedJobs.length));
  await Promise.all(Array.from({ length: workerCount }, () => worker()));
  return { results, errors };
}

export async function resolveQueuedUploads(authFetchFn, apiUrl, authHeaders, queuedJobs, concurrency, onProgress, uploadJobTimeoutMs, uploadPollIntervalMs) {
  if (!Array.isArray(queuedJobs) || queuedJobs.length === 0) {
    return { results: [], errors: [] };
  }
  const byId = new Map();
  queuedJobs.forEach((job, idx) => {
    byId.set(job.job_id, { idx, filename: job.filename || "ficheiro" });
  });
  const results = new Array(queuedJobs.length);
  const errors = [];
  const pending = new Set(queuedJobs.map(j => j.job_id));
  const deadline = Date.now() + uploadJobTimeoutMs;
  let processed = 0;

  while (pending.size > 0 && Date.now() < deadline) {
    const ids = Array.from(pending);
    const batch = await fetchUploadStatusBatch(authFetchFn, apiUrl, authHeaders, ids);
    if (!batch || !Array.isArray(batch.items)) {
      return await resolveQueuedUploadsLegacy(authFetchFn, apiUrl, queuedJobs, concurrency, onProgress, uploadJobTimeoutMs, uploadPollIntervalMs);
    }

    for (const item of batch.items) {
      const jobId = String(item.job_id || "");
      if (!pending.has(jobId)) continue;
      const meta = byId.get(jobId);
      if (!meta) {
        pending.delete(jobId);
        continue;
      }

      const status = String(item.status || "").toLowerCase();
      if (status === "completed") {
        if (item.result) {
          results[meta.idx] = { ok: true, data: item.result, filename: meta.filename };
          processed += 1;
          if (onProgress) onProgress(processed, queuedJobs.length, meta.filename, true);
        } else {
          const msg = "Upload concluído sem resultado";
          results[meta.idx] = { ok: false, error: msg, filename: meta.filename };
          errors.push({ filename: meta.filename, error: msg });
          processed += 1;
          if (onProgress) onProgress(processed, queuedJobs.length, meta.filename, false);
        }
        pending.delete(jobId);
      } else if (status === "failed" || status === "not_found" || status === "forbidden") {
        const msg = item.error || "Falha no processamento do ficheiro";
        results[meta.idx] = { ok: false, error: msg, filename: meta.filename };
        errors.push({ filename: meta.filename, error: msg });
        processed += 1;
        if (onProgress) onProgress(processed, queuedJobs.length, meta.filename, false);
        pending.delete(jobId);
      }
    }

    if (pending.size > 0) {
      await sleep(uploadPollIntervalMs);
    }
  }

  for (const jobId of pending) {
    const meta = byId.get(jobId);
    if (!meta) continue;
    const msg = "Timeout no processamento do ficheiro";
    results[meta.idx] = { ok: false, error: msg, filename: meta.filename };
    errors.push({ filename: meta.filename, error: msg });
    if (onProgress) onProgress(Math.min(queuedJobs.length, ++processed), queuedJobs.length, meta.filename, false);
  }

  return { results, errors };
}

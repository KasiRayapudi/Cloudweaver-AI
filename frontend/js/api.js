/**
 * Backend client.
 *
 * The only module that knows the API exists. Endpoints and payload shapes are
 * unchanged from the existing backend; this wraps them with timeouts, typed
 * errors and a friendlier message than a raw status code.
 */

const TIMEOUT_MS = 30000;

/** An API failure the interface can present to a person. */
export class ApiError extends Error {
  constructor(message, { status = 0, detail = "", cause } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.cause = cause;
  }

  /** Whether retrying the identical request could plausibly succeed. */
  get retryable() {
    return this.status === 0 || this.status >= 500;
  }
}

async function request(path, { method = "GET", body, signal } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

  // Caller cancellation and the timeout both have to reach the same fetch.
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", () => controller.abort(), { once: true });
  }

  try {
    const response = await fetch(path, {
      method,
      headers: body ? { "content-type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });

    const isJson = (response.headers.get("content-type") || "").includes("json");
    const payload = isJson ? await response.json().catch(() => null) : null;

    if (!response.ok) {
      const detail = payload?.detail || response.statusText || "Request failed";
      throw new ApiError(detail, { status: response.status, detail });
    }
    return payload;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error.name === "AbortError") {
      throw new ApiError(
        signal?.aborted ? "Generation cancelled." : "The request timed out.",
        { status: 0, cause: error },
      );
    }
    throw new ApiError(
      "Could not reach the generator. Check that the backend is running.",
      { status: 0, cause: error },
    );
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  health: () => request("/api/health"),
  examples: () => request("/api/examples"),

  generate: (prompt, { extractor = "rule", signal } = {}) =>
    request("/api/generate", { method: "POST", body: { prompt, extractor }, signal }),

  /**
   * Ask a question about a design.
   *
   * The prompt is sent with the question rather than a session id: generation
   * is deterministic, so the backend rebuilds the identical design and the
   * answer is guaranteed to be about the architecture on screen.
   */
  ask: (prompt, question, { extractor = "rule" } = {}) =>
    request("/api/ask", {
      method: "POST",
      body: { prompt, question, extractor },
    }),

  /** Returns a Blob; the zip endpoint does not speak JSON. */
  async download(prompt, { extractor = "rule" } = {}) {
    const response = await fetch("/api/generate/download", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ prompt, extractor }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      throw new ApiError(payload?.detail || "Could not package the project.", {
        status: response.status,
      });
    }
    return response.blob();
  },
};

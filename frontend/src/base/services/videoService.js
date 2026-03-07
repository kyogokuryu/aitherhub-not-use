import BaseApiService from '../api/BaseApiService';
import { URL_CONSTANTS } from '../api/endpoints/constant';
import TokenManager from '../utils/tokenManager';

class VideoService extends BaseApiService {
  constructor() {
    super(import.meta.env.VITE_API_BASE_URL);
  }

  async getVideosByUser(userId) {
    try {
      // Try the new endpoint with clip counts first
      const response = await this.get(`/api/v1/videos/user/${userId}/with-clips`);
      if (Array.isArray(response)) {
        return response;
      } else if (response?.data && Array.isArray(response.data)) {
        return response.data;
      } else if (response?.videos && Array.isArray(response.videos)) {
        return response.videos;
      }
      return [];
    } catch (error) {
      // Fallback to original endpoint without clip counts
      try {
        const fallback = await this.get(`/api/v1/videos/user/${userId}`);
        if (Array.isArray(fallback)) return fallback;
        if (fallback?.data && Array.isArray(fallback.data)) return fallback.data;
        if (fallback?.videos && Array.isArray(fallback.videos)) return fallback.videos;
        return [];
      } catch (fallbackError) {
        if (fallbackError.response?.status === 404 || fallbackError.response?.status === 501) {
          return [
            {
              "id": "1",
              "original_filename": "富士山.mp4",
              "status": "completed",
              "created_at": "2026-01-08T00:00:00.000Z"
            },          
            {
              "id": "2",
              "original_filename": "video 2",
              "status": "processing",
              "created_at": "2026-01-08T00:00:00.000Z"
            },
          ];
        }
        return [];
      }
    }
  }

  async getVideoById(videoId, config = {}) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}`, config);
      if (response?.data) {
        return response.data;
      }
      return response;
    } catch (error) {
      if (error.response?.status === 404 || error.response?.status === 501) {
        const mockVideoDetails = {
          "1": {
            "id": "1",
            "original_filename": "富士山.mp4",
            "status": "completed",
            "created_at": "2026-01-08T00:00:00.000Z",
            "description": {
              "title1": "富士山",
              "content1": "富士山の壮大な景色を収めたこの動画では、四季折々の自然美と静寂な空気感を臨場感たっぷりに感じることができます。朝日や夕焼け、雲海、雪景色が心を癒やし旅情を深めてくれます感動が広がる映像ですとても美しい 富士山の壮大な景色を収めたこの動画では、四季折々の自然美と静寂な空気感を臨場感たっぷりに感じることができます。朝日や夕焼け、雲海、雪景色が心を癒やし旅情を深めてくれます感動が広がる映像ですとても美しい富士山の壮大な景色を収めたこの動画では、四季折々の自然美と静寂な空気感を臨場感たっぷりに感じることができます。朝日や夕焼け、雲海、雪景色が心を癒やし旅情を深めてくれます感動が広がる映像ですとても美しい 富士山の壮大な景色を収めたこの動画では、四季折々の自然美と静寂な空気感を臨場感たっぷりに感じることができます。朝日や夕焼け、雲海、雪景色が心を癒やし旅情を深めてくれます感動が広がる映像ですとても美しい",
              "title2": "tiêu điểm 2",
              "content2": "người dùng đang mỉm cười với người xem",
              "title3": "tiêu điểm 3",
              "content3": "người dùng cúi đầu chào kết thúc video"
            }
          },
          "2": {
            "id": "2",
            "original_filename": "video 2",
            "status": "processing",
            "created_at": "2026-01-08T00:00:00.000Z",
            "description": {
              "title": "tiêu điểm 2",
              "content": "người dùng đang show sản phẩm"
            }
          }
        };
        
        if (mockVideoDetails[videoId]) {
          return mockVideoDetails[videoId];
        }
      }
      throw error;
    }
  }

  /**
   * Stream chat responses from backend SSE endpoint.
   * params: { videoId, messages, token, onMessage, onDone, onError }
   * Returns: { cancel: () => void }
   */
  streamChat({ videoId, messages = [], onMessage = () => {}, onDone = () => {}, onError = () => {} }) {
    const base = (this.client && this.client.defaults && this.client.defaults.baseURL) || import.meta.env.VITE_API_BASE_URL || "";
    const url = `${base.replace(/\/$/, "")}/api/v1/chat/stream?video_id=${encodeURIComponent(videoId)}`;

    const controller = new AbortController();
    const signal = controller.signal;

    (async () => {
      try {
        const headers = {
          Accept: "text/event-stream",
          "Content-Type": "application/json",
        };

        const token = TokenManager.getToken();
        if (token) headers["Authorization"] = `Bearer ${token}`;

        const resp = await fetch(url, {
          method: "POST",
          headers,
          body: JSON.stringify({ messages }),
          credentials: "same-origin",
          signal,
        });

        if (!resp.ok) {
          const txt = await resp.text();
          throw new Error(`Stream request failed: ${resp.status} ${txt}`);
        }

        if (!resp.body) {
          throw new Error("Stream response has no body");
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          let idx;
          while ((idx = buffer.indexOf("\n\n")) !== -1) {
            const raw = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            const lines = raw.split(/\r?\n/);
            for (const line of lines) {
              if (line === "") continue;
              if (line.startsWith("data:")) {
                let payload = line.slice(5);
                if (payload.charAt(0) === " ") payload = payload.slice(1);
                if ((payload.startsWith('"') && payload.endsWith('"')) || (payload.startsWith("'") && payload.endsWith("'"))) {
                  payload = payload.slice(1, -1).trim();
                }
                const isDone = payload === "[DONE]" || payload === "DONE";
                if (isDone) {
                  try { onDone(); } catch (e) {}
                } else if (payload.startsWith("[ERROR]")) {
                  try { onError(new Error(payload)); } catch (e) {}
                } else {
                  try { onMessage(payload); } catch (e) {}
                }
              }
            }
          }
        }

        if (buffer) {
          const lines = buffer.split(/\r?\n/);
          for (const line of lines) {
            if (line === "") continue;
            if (line.startsWith("data:")) {
              let payload = line.slice(5);
              if (payload.charAt(0) === " ") payload = payload.slice(1);
              if ((payload.startsWith('"') && payload.endsWith('"')) || (payload.startsWith("'") && payload.endsWith("'"))) {
                payload = payload.slice(1, -1);
              }
              const isDone = payload === "[DONE]" || payload === "DONE";
              if (isDone) onDone();
              else if (payload.startsWith("[ERROR]")) onError(new Error(payload));
              else onMessage(payload);
            }
          }
        }

        try { onDone(); } catch (e) {}
      } catch (err) {
        if (err.name === 'AbortError') {
          return;
        }
        try { onError(err); } catch (e) {}
      }
    })();

    return { cancel: () => controller.abort() };
  }

  async deleteVideo(videoId) {
    try {
      const response = await this.delete(`/api/v1/videos/${videoId}`);
      return response;
    } catch (error) {
      console.error('Failed to delete video:', error);
      throw error;
    }
  }

  async renameVideo(videoId, newName) {
    try {
      const response = await this.patch(`/api/v1/videos/${videoId}/rename`, { name: newName });
      return response;
    } catch (error) {
      console.error('Failed to rename video:', error);
      throw error;
    }
  }

  async getChatHistory(videoId) {
    try {
      const response = await this.get(`/api/v1/chat/history?video_id=${encodeURIComponent(videoId)}`);
      if (response?.data) return response.data;
      return response;
    } catch (err) {
      throw err;
    }
  }


  /**
   * Generate a download URL for a video from Azure Blob Storage
   * @param {string} videoId - The video ID
   * @param {Object} options
   * @param {number} [options.expiresInMinutes=60] - URL expiration time in minutes
   * @param {string} [options.email] - User email (required by backend). Will fallback to localStorage if not provided.
   * @returns {Promise<string>} - Download URL with SAS token
   */
  async getDownloadUrl(videoId, { expiresInMinutes = 60, email } = {}) {
    try {
      const payload = {
        video_id: videoId,
        expires_in_minutes: expiresInMinutes,
      };

      // Backend expects email; try to supply if available
      if (email) {
        payload.email = email;
      } else {
        try {
          const storedUser = localStorage.getItem("user");
          if (storedUser) {
            const parsed = JSON.parse(storedUser);
            if (parsed && parsed.email) {
              payload.email = parsed.email;
            }
          }
        } catch {
          // ignore storage access / parse errors
        }
      }

      const response = await this.post(`/api/v1/videos/generate-download-url`, payload);
      return response?.download_url || response?.data?.download_url || response;
    } catch (err) {
      console.error("Failed to get download URL:", err);
      throw err;
    }
  }
  /**
   * Stream video processing status updates via Server-Sent Events (SSE).
   *
   * @param {Object} params - Stream parameters
   * @param {string} params.videoId - Video ID to monitor
   * @param {Function} params.onStatusUpdate - Callback when status updates: (data) => void
   *   data shape: { video_id, status, progress, message, updated_at }
   * @param {Function} params.onDone - Callback when processing completes
   * @param {Function} params.onError - Callback on error: (error) => void
   * @returns {Object} - Control object with close() method to stop streaming
   *
   * @example
   * const stream = VideoService.streamVideoStatus({
   *   videoId: 'video-123',
   *   onStatusUpdate: (data) => {
   *     console.log(`Status: ${data.status}, Progress: ${data.progress}%`);
   *   },
   *   onDone: () => console.log('Processing complete'),
   *   onError: (err) => console.error('Stream error:', err),
   * });
   *
   * // Later: stream.close();
   */
  streamVideoStatus({ videoId, onStatusUpdate = () => {}, onDone = () => {}, onError = () => {} }) {
    const base = (this.client && this.client.defaults && this.client.defaults.baseURL) || import.meta.env.VITE_API_BASE_URL || "";
    const url = `${base.replace(/\/$/, "")}/api/v1/videos/${encodeURIComponent(videoId)}/status/stream`;

    const controller = new AbortController();
    const signal = controller.signal;

    // Retry configuration
    const MAX_RETRIES = 3;
    const RETRY_DELAY = 5000; // 5 seconds
    const HEARTBEAT_TIMEOUT = 120000; // 2 minutes without heartbeat = connection lost
    let retryCount = 0;
    let lastHeartbeatTime = Date.now();
    let heartbeatTimeoutId = null;

    const connectWithRetry = async () => {
      try {
        console.log(`SSE: Connecting to video ${videoId} status stream${retryCount > 0 ? ` (retry ${retryCount}/${MAX_RETRIES})` : ''}`);

        const headers = {
          Accept: "text/event-stream",
        };

        const token = TokenManager.getToken();
        if (token) headers["Authorization"] = `Bearer ${token}`;

        const resp = await fetch(url, {
          method: "GET",
          headers,
          credentials: "same-origin",
          signal,
        });

        if (!resp.ok) {
          const txt = await resp.text();
          throw new Error(`SSE request failed: ${resp.status} ${txt}`);
        }

        if (!resp.body) {
          throw new Error("SSE response has no body");
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";

        // Set up heartbeat timeout check
        const checkHeartbeat = () => {
          const timeSinceLastHeartbeat = Date.now() - lastHeartbeatTime;
          if (timeSinceLastHeartbeat > HEARTBEAT_TIMEOUT) {
            console.warn(`SSE: No heartbeat received for ${Math.round(timeSinceLastHeartbeat/1000)}s, connection may be stale`);
          }
          heartbeatTimeoutId = setTimeout(checkHeartbeat, 30000); // Check every 30 seconds
        };
        checkHeartbeat();

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          let idx;
          while ((idx = buffer.indexOf("\n\n")) !== -1) {
            const raw = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);

            const lines = raw.split(/\r?\n/);
            for (const line of lines) {
              if (line === "") continue;
              if (line.startsWith("data:")) {
                let payload = line.slice(5).trim();

                // Handle [DONE] marker
                if (payload === "[DONE]" || payload === "DONE") {
                  console.log('SSE: Stream completed successfully');
                  clearTimeout(heartbeatTimeoutId);
                  onDone();
                  return;
                }

                // Parse JSON payload
                try {
                  const data = JSON.parse(payload);

                  // Update heartbeat timestamp for any message
                  lastHeartbeatTime = Date.now();

                  // Handle heartbeat messages
                  if (data.heartbeat) {
                    console.debug(`SSE: Heartbeat received (poll ${data.poll_count})`);
                    continue; // Don't pass heartbeat to onStatusUpdate
                  }

                  // Handle error from server
                  if (data.error) {
                    console.error('SSE: Server error:', data.error);
                    clearTimeout(heartbeatTimeoutId);
                    onError(new Error(data.error));
                    return;
                  }

                  // Send status update
                  onStatusUpdate(data);

                  // Auto-close on completion
                  if (data.status === 'DONE' || data.status === 'ERROR') {
                    console.log(`SSE: Processing ${data.status}, closing stream`);
                    clearTimeout(heartbeatTimeoutId);
                    onDone();
                    return;
                  }
                } catch (parseErr) {
                  console.error('SSE JSON parse error:', parseErr, 'payload:', payload);
                }
              }
            }
          }
        }

        // Handle remaining buffer
        if (buffer) {
          const lines = buffer.split(/\r?\n/);
          for (const line of lines) {
            if (line === "") continue;
            if (line.startsWith("data:")) {
              const payload = line.slice(5).trim();
              if (payload === "[DONE]" || payload === "DONE") {
                console.log('SSE: Stream completed (from buffer)');
                clearTimeout(heartbeatTimeoutId);
                onDone();
              }
            }
          }
        }

        clearTimeout(heartbeatTimeoutId);
        onDone();
      } catch (err) {
        clearTimeout(heartbeatTimeoutId);

        if (err.name === 'AbortError') {
          console.log('SSE: Stream aborted by user');
          return;
        }

        console.error(`SSE: Connection failed: ${err.message}`);

        // Retry logic
        if (retryCount < MAX_RETRIES) {
          retryCount++;
          console.log(`SSE: Retrying connection in ${RETRY_DELAY}ms... (${retryCount}/${MAX_RETRIES})`);
          await new Promise(resolve => setTimeout(resolve, RETRY_DELAY));
          return connectWithRetry();
        } else {
          console.error(`SSE: Max retries (${MAX_RETRIES}) exceeded, giving up`);
          onError(err);
        }
      }
    };

    // Start the connection
    connectWithRetry();

    return {
      close: () => controller.abort(),
      cancel: () => controller.abort(), // Alias for compatibility
    };
  }

  async getProductData(videoId) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/product-data`);
      return response;
    } catch (error) {
      console.warn('Failed to fetch product data:', error);
      return { products: [], trends: [], has_product_data: false, has_trend_data: false };
    }
  }

  /**
   * Request clip generation for a specific phase.
   * @param {string} videoId
   * @param {number} phaseIndex
   * @param {number} timeStart - Start time in seconds
   * @param {number} timeEnd - End time in seconds
   * @param {number} [speedFactor=1.2] - Playback speed (1.0-1.5x)
   * @returns {Promise<{clip_id, status, message}>}
   */
  async requestClipGeneration(videoId, phaseIndex, timeStart, timeEnd, speedFactor = 1.2) {
    try {
      const response = await this.post(`/api/v1/videos/${videoId}/clips`, {
        phase_index: phaseIndex,
        time_start: timeStart,
        time_end: timeEnd,
        speed_factor: speedFactor,
      });
      return response;
    } catch (error) {
      console.error('Failed to request clip generation:', error);
      throw error;
    }
  }

  /**
   * Get clip generation status for a specific phase.
   * @param {string} videoId
   * @param {number} phaseIndex
   * @returns {Promise<{clip_id, status, clip_url?}>}
   */
  async getClipStatus(videoId, phaseIndex) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/clips/${phaseIndex}`);
      return response;
    } catch (error) {
      console.warn('Failed to get clip status:', error);
      return { status: 'not_found' };
    }
  }

  /**
   * List all clips for a video.
   * @param {string} videoId
   * @returns {Promise<{clips: Array}>}
   */
  async listClips(videoId) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/clips`);
      return response;
    } catch (error) {
      console.warn('Failed to list clips:', error);
      return { clips: [] };
    }
  }

  /**
   * Rate a specific phase of a video (1-5 stars + optional comment).
   * @param {string} videoId
   * @param {number} phaseIndex
   * @param {number} rating - 1 to 5
   * @param {string} [comment] - optional feedback comment
   * @returns {Promise<Object>}
   */
  async ratePhase(videoId, phaseIndex, rating, comment = '') {
    try {
      const response = await this.put(`/api/v1/videos/${videoId}/phases/${phaseIndex}/rating`, {
        rating,
        comment,
      });
      return response;
    } catch (error) {
      console.warn('Failed to rate phase:', error);
      throw error;
    }
  }

  // =========================================================
  // Product Exposure Timeline API
  // =========================================================

  async getProductExposures(videoId) {
    try {
      const response = await this.get(`/api/v1/videos/${videoId}/product-exposures`);
      return response;
    } catch (error) {
      console.warn('Failed to fetch product exposures:', error);
      return { exposures: [], count: 0 };
    }
  }

  async updateProductExposure(videoId, exposureId, data) {
    try {
      const response = await this.put(
        `/api/v1/videos/${videoId}/product-exposures/${exposureId}`,
        data,
      );
      return response;
    } catch (error) {
      console.warn('Failed to update product exposure:', error);
      throw error;
    }
  }

  async createProductExposure(videoId, data) {
    try {
      const response = await this.post(
        `/api/v1/videos/${videoId}/product-exposures`,
        data,
      );
      return response;
    } catch (error) {
      console.warn('Failed to create product exposure:', error);
      throw error;
    }
  }

  async deleteProductExposure(videoId, exposureId) {
    try {
      const response = await this.delete(
        `/api/v1/videos/${videoId}/product-exposures/${exposureId}`,
      );
      return response;
    } catch (error) {
      console.warn('Failed to delete product exposure:', error);
      throw error;
    }
  }

  // =========================================================
  // TikTok Live Capture API
  // =========================================================

  /**
   * Check if a TikTok user is currently live.
   * @param {string} liveUrl - TikTok live URL
   * @returns {Promise<{is_live, username, room_id, title, message}>}
   */
  async checkLiveStatus(liveUrl) {
    try {
      const response = await this.post(URL_CONSTANTS.LIVE_CHECK, {
        live_url: liveUrl,
      });
      return response;
    } catch (error) {
      console.error('Failed to check live status:', error);
      throw error;
    }
  }

  /**
   * Start capturing a TikTok live stream.
   * @param {string} liveUrl - TikTok live URL
   * @param {number} [duration=0] - Max recording duration in seconds (0 = until stream ends)
   * @returns {Promise<{video_id, status, stream_title, username, message}>}
   */
  async startLiveCapture(liveUrl, duration = 0) {
    try {
      const response = await this.post(URL_CONSTANTS.LIVE_CAPTURE, {
        live_url: liveUrl,
        duration,
      });
      return response;
    } catch (error) {
      console.error('Failed to start live capture:', error);
      throw error;
    }
  }

  // =========================================================
  // Real-time Live Monitoring API
  // =========================================================

  /**
   * Start real-time monitoring for a live capture.
   * @param {string} videoId - Video ID
   * @param {string} liveUrl - TikTok live URL
   * @returns {Promise}
   */
  async startLiveMonitor(videoId, liveUrl) {
    try {
      const response = await this.post(`${URL_CONSTANTS.LIVE_START_MONITOR}/${videoId}/start-monitor`, {
        live_url: liveUrl,
        video_id: videoId,
      });
      return response;
    } catch (error) {
      console.error('Failed to start live monitor:', error);
      throw error;
    }
  }

  /**
   * Get current live monitoring status.
   * @param {string} videoId - Video ID
   * @returns {Promise}
   */
  async getLiveStatus(videoId) {
    try {
      const response = await this.get(`${URL_CONSTANTS.LIVE_STATUS}/${videoId}/status`);
      return response;
    } catch (error) {
      console.error('Failed to get live status:', error);
      throw error;
    }
  }

  /**
   * Get all active live monitoring sessions.
   * @returns {Promise}
   */
  async getActiveLiveSessions() {
    try {
      const response = await this.get(URL_CONSTANTS.LIVE_ACTIVE);
      return response;
    } catch (error) {
      console.error('Failed to get active sessions:', error);
      throw error;
    }
  }

  /**
   * Stream real-time live events via SSE.
   * @param {Object} params
   * @param {string} params.videoId - Video ID to monitor
   * @param {Function} params.onMetrics - Callback for metrics updates
   * @param {Function} params.onAdvice - Callback for AI advice
   * @param {Function} params.onStreamUrl - Callback for stream URL
   * @param {Function} params.onStreamEnded - Callback when stream ends
   * @param {Function} params.onError - Callback on error
   * @returns {Object} - Control object with close() method
   */
  streamLiveEvents({ videoId, onMetrics = () => {}, onAdvice = () => {}, onStreamUrl = () => {}, onStreamEnded = () => {}, onError = () => {} }) {
    const base = (this.client && this.client.defaults && this.client.defaults.baseURL) || import.meta.env.VITE_API_BASE_URL || "";
    const url = `${base.replace(/\/$/, "")}/api/v1/live/${encodeURIComponent(videoId)}/stream`;

    const controller = new AbortController();
    const signal = controller.signal;

    const MAX_RETRIES = 5;
    const RETRY_DELAY = 3000;
    let retryCount = 0;

    const connect = async () => {
      try {
        console.log(`LiveSSE: Connecting to live stream ${videoId}`);

        const headers = { Accept: "text/event-stream" };
        const token = TokenManager.getToken();
        if (token) headers["Authorization"] = `Bearer ${token}`;

        const resp = await fetch(url, {
          method: "GET",
          headers,
          credentials: "same-origin",
          signal,
        });

        if (!resp.ok) {
          throw new Error(`LiveSSE request failed: ${resp.status}`);
        }

        if (!resp.body) {
          throw new Error("LiveSSE response has no body");
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";
        retryCount = 0; // Reset on successful connection

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          let idx;
          while ((idx = buffer.indexOf("\n\n")) !== -1) {
            const raw = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);

            const lines = raw.split(/\r?\n/);
            for (const line of lines) {
              if (!line.startsWith("data:")) continue;
              const payload = line.slice(5).trim();

              try {
                const data = JSON.parse(payload);
                const { event_type, payload: eventPayload } = data;

                switch (event_type) {
                  case 'metrics':
                    onMetrics(eventPayload);
                    break;
                  case 'advice':
                    onAdvice(eventPayload);
                    break;
                  case 'stream_url':
                    onStreamUrl(eventPayload);
                    break;
                  case 'stream_ended':
                    onStreamEnded(eventPayload);
                    return;
                  case 'heartbeat':
                    break; // Ignore heartbeats
                  default:
                    console.log('LiveSSE: Unknown event type:', event_type);
                }
              } catch (parseErr) {
                console.error('LiveSSE parse error:', parseErr);
              }
            }
          }
        }
      } catch (err) {
        if (signal.aborted) return;
        console.error('LiveSSE error:', err);

        if (retryCount < MAX_RETRIES) {
          retryCount++;
          console.log(`LiveSSE: Retrying in ${RETRY_DELAY}ms (${retryCount}/${MAX_RETRIES})`);
          setTimeout(connect, RETRY_DELAY);
        } else {
          onError(err);
        }
      }
    };

    connect();

    return {
      close: () => {
        controller.abort();
      },
    };
  }

  // ── Live Report v1 ─────────────────────────────────────

  /**
   * Generate Live Report v1 for a video.
   * @param {string} videoId - Video ID
   * @returns {Promise} Report data
   */
  async generateLiveReport(videoId) {
    try {
      const response = await this.post(`${URL_CONSTANTS.REPORT_GENERATE}/${videoId}/generate`);
      return response;
    } catch (error) {
      console.error('Failed to generate live report:', error);
      throw error;
    }
  }

  /**
   * Get the latest Live Report for a video.
   * @param {string} videoId - Video ID
   * @returns {Promise} Report data
   */
  async getLiveReport(videoId) {
    try {
      const response = await this.get(`${URL_CONSTANTS.REPORT_GET}/${videoId}`);
      return response;
    } catch (error) {
      console.error('Failed to get live report:', error);
      throw error;
    }
  }
}

export default new VideoService();


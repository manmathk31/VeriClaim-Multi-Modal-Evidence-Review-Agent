// ApiClient — legacy stub. Real API calls now go through:
// - WebSocket /ws/voice_agent (voice agent)
// - fetch /api/v1/upload (evidence upload in uploader.js)
// - fetch /api/v1/* endpoints (dashboard.html inline scripts)
class ApiClient {
    constructor() { this.baseUrl = ''; }
}
window.api = new ApiClient();

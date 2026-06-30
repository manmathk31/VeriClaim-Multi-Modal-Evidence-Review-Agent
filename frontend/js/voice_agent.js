// Advanced Voice Engine - Bug-Fixed & Hardened

class VoiceAgent {
    constructor() {
        this.statusText = document.getElementById('agentStatus');
        this.visLabel = document.getElementById('visualizerLabel');
        this.toggleMicBtn = document.getElementById('toggleMicBtn');
        this.transcriptArea = document.getElementById('transcriptArea');
        this.processingIndicator = document.getElementById('processingIndicator');

        this.isListening = false;
        this.isSpeaking = false;
        this.isProcessing = false;
        this.hasGreeted = false;
        this.isHoldingSpace = false;
        this.isVADProcessing = false;
        this.isMutedByUser = false;

        // WebSocket reconnect state
        this.ws = null;
        this.wsReconnectAttempts = 0;
        this.wsMaxReconnects = 5;
        this.user = null;

        this.conversationState = "GREETING";

        // Premium UI Chime
        this.startupSound = new Audio('assets/chime.mp3');
        this.startupSound.volume = 0.5;
        this.startupSound.preload = 'auto';

        this.setupSpeechRecognition();
        this.setupSpeechSynthesis();
        this.bindEvents();

        // BUG FIX #1: init() must run first to populate this.user before
        // setupWebSocket() tries to read this.user.token. We run this synchronously
        // instead of with a timeout to prevent race conditions.
        this.init();
    }

    init() {
        // Authenticate User
        const userStr = localStorage.getItem('vericlaim_user');
        if (!userStr) {
            window.location.href = 'home.html';
            return;
        }
        this.user = JSON.parse(userStr);
        this.userName = this.user.name || this.user.email.split('@')[0];
        // Capitalize first letter
        this.userName = this.userName.charAt(0).toUpperCase() + this.userName.slice(1);

        // Update header user display
        const userDisplay = document.getElementById('userDisplay');
        const userNameEl = document.getElementById('userName');
        const userAvatarEl = document.getElementById('userAvatar');
        if (userDisplay && userNameEl) {
            userNameEl.innerText = this.userName;
            if (userAvatarEl) {
                userAvatarEl.innerText = this.userName.charAt(0).toUpperCase();
            }
            userDisplay.classList.remove('hidden');
            userDisplay.classList.add('flex');
        }

        if (this.toggleMicBtn) this.toggleMicBtn.classList.add('muted');
        this.updateVisLabel("Ready");
        this.updateStatus("Standby");
    }

    setupSpeechRecognition() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            this.updateStatus("Voice not supported.");
            return;
        }

        this.recognition = new SpeechRecognition();
        const isAndroid = /Android/i.test(navigator.userAgent);
        this.recognition.continuous = !isAndroid; // Disable continuous on Android to prevent duplication bugs
        this.recognition.interimResults = true;
        this.recognition.lang = 'en-US';
        this.currentTranscript = '';

        this.recognition.onstart = () => {
            this.isListening = true;
            this.updateStatus("Live Connection");
            if (this.toggleMicBtn) this.toggleMicBtn.classList.remove('muted');
            
            // Android UI State sync
            if (isAndroid) {
                this.updateVisLabel("Listening...");
                if (window.WaveEngine) window.WaveEngine.setListening(true);
            }
        };

        this.recognition.onresult = (event) => {
            // Rebuild the transcript from scratch on every event.
            // This completely bypasses Android Chrome's stuttering bugs where resultIndex 
            // behaves unpredictably or fires duplicate final segments.
            let interim = '';
            let finalSegment = '';
            for (let i = 0; i < event.results.length; ++i) {
                const transcript = event.results[i][0].transcript;
                if (event.results[i].isFinal) {
                    finalSegment += transcript;
                } else {
                    interim += transcript;
                }
            }
            // Overwrite instead of appending to prevent double-counting
            this.currentTranscript = finalSegment;
            this.interimTranscript = interim;
        };

        this.recognition.onerror = (event) => {
            console.error("Speech Recognition Error:", event.error);
            // BUG FIX #3 (partial): Show user-visible feedback on mic errors
            // so they know to retry rather than silently failing.
            if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
                this.updateVisLabel("Mic permission denied");
                this.addSystemMsg("Microphone access was denied. Please allow mic access and reload.");
            }
            // Release hold state so UI doesn't get stuck
            this.isHoldingSpace = false;
            if (this.toggleMicBtn) this.toggleMicBtn.classList.add('muted');
        };

        this.recognition.onend = () => {
            this.isListening = false;
            if (window.WaveEngine) window.WaveEngine.setListening(false);
            if (this.toggleMicBtn) this.toggleMicBtn.classList.add('muted');

            // BUG FIX #4: Only show "Hold Space to Speak" if the agent is NOT
            // currently speaking. Previously this label stomped the "Transmitting"
            // label mid-speech because onend fires right after recognition.stop().
            if (!this.isSpeaking && !this.isProcessing && !this.isVADProcessing) {
                if (this.isMutedByUser) {
                    this.updateVisLabel("Muted");
                    if (this.toggleMicBtn) this.toggleMicBtn.classList.add('muted');
                    if (window.WaveEngine) window.WaveEngine.setListening(false);
                    return;
                }

                this.updateVisLabel("Ready");
                
                // Android-specific: Native VAD fallback
                const isAndroid = /Android/i.test(navigator.userAgent);
                if (isAndroid) {
                    let combined = this.currentTranscript;
                    if (this.interimTranscript && !this.currentTranscript.toLowerCase().includes(this.interimTranscript.toLowerCase().trim())) {
                        combined += ' ' + this.interimTranscript;
                    }
                    combined = combined.trim();
                    
                    if (combined !== '') {
                        this.addUserMsg(combined);
                        this.processUserMessage(combined);
                        this.currentTranscript = '';
                        this.interimTranscript = '';
                    } else {
                        // Keep listening
                        this.startRecognitionSession();
                    }
                } else {
                    try { this.recognition.start(); } catch (err) { }
                }
            }
        };
    }

    startRecognitionSession() {
        this.currentTranscript = '';
        this.interimTranscript = '';
        if (this.recognition) {
            try { this.recognition.start(); } catch (err) { }
        }
    }

    setupSpeechSynthesis() {
        this.synth = window.speechSynthesis;
    }

    bindEvents() {
        const startBtn = document.getElementById('startConversationBtn');
        if (startBtn) {
            startBtn.addEventListener('click', () => {
                // Mobile TTS Unlock: Must speak a silent utterance synchronously during user click
                if (this.synth) {
                    const unlockUtterance = new SpeechSynthesisUtterance('');
                    unlockUtterance.volume = 0;
                    this.synth.speak(unlockUtterance);
                }

                // Hide overlay, show action bar
                document.getElementById('startOverlay').style.opacity = '0';
                document.getElementById('startOverlay').style.pointerEvents = 'none';

                const actionBar = document.getElementById('actionBar');
                actionBar.classList.remove('opacity-0', 'pointer-events-none', 'translate-y-4');

                // Connect WebSocket only when user starts the conversation
                if (!this.ws) {
                    this.setupWebSocket();
                }

                // Initialize state
                this.updateStatus("Live Connection");

                if (!this.hasGreeted) {
                    this.hasGreeted = true;

                    // Manually turn on the visualizer glow immediately
                    if (window.WaveEngine) window.WaveEngine.setSpeaking(true);

                    // When chime finishes playing naturally, trigger AI and stop manual glow
                    this.startupSound.onended = () => {
                        if (window.WaveEngine) window.WaveEngine.setSpeaking(false);
                        this.triggerSystemResponse(`Hello, ${this.userName}. I am your VeriClaim assistant. Please describe the incident and any damage you are reporting today.`);
                        setTimeout(() => { this.setupVAD(); }, 5000);
                    };

                    // Play startup chime. If blocked, fallback instantly
                    const playPromise = this.startupSound.play();
                    if (playPromise !== undefined) {
                        playPromise.catch(e => {
                            console.log('Audio play prevented:', e);
                            if (window.WaveEngine) window.WaveEngine.setSpeaking(false);
                            this.triggerSystemResponse(`Hello, ${this.userName}. I am your VeriClaim assistant. Please describe the incident and any damage you are reporting today.`);
                            setTimeout(() => { this.setupVAD(); }, 5000);
                        });
                    }
                }
            });
        }

        // VAD (Voice Activity Detection) completely replaces the spacebar
        // The VAD is initialized in setupVAD() when the user starts the conversation.


        const endBtn = document.getElementById('endSessionBtn');
        if (endBtn) {
            endBtn.addEventListener('click', () => this.stopEverything());
        }

        if (this.toggleMicBtn) {
            this.toggleMicBtn.addEventListener('click', () => {
                const isAndroid = /Android/i.test(navigator.userAgent);
                if (isAndroid) {
                    if (this.isMutedByUser) {
                        // Unmute
                        this.isMutedByUser = false;
                        this.toggleMicBtn.classList.remove('muted');
                        if (!this.isSpeaking && !this.isProcessing) {
                            this.updateVisLabel("Listening...");
                            try { this.recognition.start(); } catch(e){}
                        } else {
                            this.updateVisLabel(this.isSpeaking ? "Speaking" : "Processing");
                        }
                    } else {
                        // Mute
                        this.isMutedByUser = true;
                        this.toggleMicBtn.classList.add('muted');
                        this.updateVisLabel("Muted");
                        try { this.recognition.stop(); } catch(e){}
                        if (window.WaveEngine) window.WaveEngine.setListening(false);
                    }
                    return;
                }
                
                if (!this.vad) return;
                if (this.toggleMicBtn.classList.contains('muted')) {
                    this.vad.start();
                    this.toggleMicBtn.classList.remove('muted');
                    this.updateVisLabel("Listening...");
                } else {
                    this.vad.pause();
                    this.toggleMicBtn.classList.add('muted');
                    this.updateVisLabel("Muted");
                }
            });
        }
    }

    async setupVAD() {
        const isAndroid = /Android/i.test(navigator.userAgent);
        if (isAndroid) {
            console.log("Android detected. Skipping VAD to prevent microphone lock conflict.");
            if (!this.isSpeaking && !this.isProcessing && !this.isMutedByUser) {
                this.startRecognitionSession();
            }
            return;
        }

        if (typeof vad === 'undefined' || !vad.MicVAD) {
            console.warn("VAD library not loaded yet.");
            this.addSystemMsg("Voice detection unavailable. Please refresh the page.");
            return;
        }
        try {
            // Explicitly request microphone access to force the browser prompt
            await navigator.mediaDevices.getUserMedia({ audio: true });
            // Tell ONNX Runtime Web to fetch WASM files from CDN instead of root
            if (window.ort) {
                window.ort.env.wasm.wasmPaths = "https://cdn.jsdelivr.net/npm/onnxruntime-web/dist/";
            }

            this.vad = await vad.MicVAD.new({
                workletURL: "https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.19/dist/vad.worklet.bundle.min.js",
                modelURL: "https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.19/dist/silero_vad.onnx",
                onSpeechStart: () => {
                    if (this.isSpeaking || this.isProcessing) return;
                    this.isListening = true;
                    if (window.WaveEngine) window.WaveEngine.setListening(true);
                    this.updateVisLabel("Listening...");
                    if (this.toggleMicBtn) this.toggleMicBtn.classList.remove('muted');
                    try { this.recognition.start(); } catch (err) { }
                },
                onSpeechEnd: (audio) => {
                    if (this.isSpeaking || this.isProcessing) return;
                    this.isListening = false;
                    if (window.WaveEngine) window.WaveEngine.setListening(false);
                    if (this.toggleMicBtn) this.toggleMicBtn.classList.add('muted');

                    this.isVADProcessing = true;
                    try { this.recognition.stop(); } catch (err) { }

                    // Wait a moment for final transcription results
                    setTimeout(() => {
                        let combined = this.currentTranscript;
                        if (this.interimTranscript && !this.currentTranscript.toLowerCase().includes(this.interimTranscript.toLowerCase().trim())) {
                            combined += ' ' + this.interimTranscript;
                        }
                        combined = combined.trim();
                        
                        if (combined !== '') {
                            this.addUserMsg(combined);
                            this.processUserMessage(combined);
                            this.currentTranscript = '';
                            this.interimTranscript = '';
                        } else {
                            this.updateVisLabel("Ready");
                            this.startRecognitionSession();
                        }
                        this.isVADProcessing = false;
                    }, 500);
                }
            });
            this.vad.start();
            this.startRecognitionSession();
        } catch (err) {
            console.error("VAD initialization failed:", err);
            this.updateVisLabel("Mic Denied");
            this.addSystemMsg("Microphone access was denied. Please allow microphone access in your browser settings and refresh the page.");
        }
    }

    stopEverything() {
        if (this.toggleMicBtn) this.toggleMicBtn.classList.add('muted');
        if (this.synth) this.synth.cancel();
        // BUG FIX #8: Guard recognition.stop() the same way as in keyup
        if (this.recognition) {
            try { this.recognition.stop(); } catch (err) { /* already stopped */ }
        }
        if (this.vad) {
            try { this.vad.destroy(); } catch (e) { }
            this.vad = null;
        }
        this.isSpeaking = false;
        this.isProcessing = false;
        this.isHoldingSpace = false;
        if (window.WaveEngine) {
            window.WaveEngine.setListening(false);
            window.WaveEngine.setSpeaking(false);
            window.WaveEngine.setProcessing(false);
        }
        this.updateVisLabel("Offline");
        this.updateStatus("Session Ended");
        // Close the WebSocket cleanly
        if (this.ws) {
            this.wsReconnectAttempts = this.wsMaxReconnects; // Prevent auto-reconnect
            this.ws.close();
        }
        // Redirect to dashboard page after a brief delay
        setTimeout(() => {
            window.location.href = 'dashboard.html';
        }, 1500);
    }

    setupWebSocket() {
        // BUG FIX #1 (continuation): this.user is now guaranteed to be set
        // before this method is called (called from init(), not constructor).
        const token = this.user ? this.user.token : '';
        const wsProto = window.location.protocol === 'https:' ? 'wss' : 'ws';
        this.ws = new WebSocket(`${wsProto}://${window.location.host}/ws/voice_agent?token=${token}`);

        this.ws.onopen = () => {
            this.wsReconnectAttempts = 0;
            console.log("WebSocket connected.");
        };

        this.ws.onmessage = (event) => {
            if (this.processingIndicator) {
                this.processingIndicator.classList.add('hidden');
                this.processingIndicator.classList.remove('flex');
            }

            try {
                const data = JSON.parse(event.data);
                if (data.text) {
                    this.triggerSystemResponse(data.text);
                }
            } catch (err) {
                console.error("Failed to parse WebSocket message:", err);
            }
        };

        this.ws.onerror = (err) => {
            console.error("WebSocket error:", err);
            this.addSystemMsg("Connection to CrewAI backend failed.");
        };

        this.ws.onclose = (event) => {
            // Stop reconnecting if it's an authentication error
            if (event.code === 1008) {
                this.addSystemMsg("Session expired. Please log in again.");
                this.wsReconnectAttempts = this.wsMaxReconnects;
                setTimeout(() => { window.location.href = 'home.html'; }, 2000);
                return;
            }

            // Don't reconnect if the session was intentionally ended
            if (this.wsReconnectAttempts >= this.wsMaxReconnects) return;

            const delay = Math.min(1000 * 2 ** this.wsReconnectAttempts, 16000);
            this.wsReconnectAttempts++;
            console.warn(`WebSocket closed (code ${event.code}). Reconnecting in ${delay}ms… (attempt ${this.wsReconnectAttempts}/${this.wsMaxReconnects})`);

            // Clean Reconnect UI instead of spamming chat
            this.updateStatus(`Reconnecting... (${this.wsReconnectAttempts}/${this.wsMaxReconnects})`);
            if (this.toggleMicBtn) this.toggleMicBtn.classList.add('muted');

            setTimeout(() => {
                if (this.user) this.setupWebSocket();
            }, delay);
        };
    }

    processUserMessage(text) {
        this.isProcessing = true;
        if (window.WaveEngine) window.WaveEngine.setProcessing(true);
        if (this.processingIndicator) {
            this.processingIndicator.classList.remove('hidden');
            this.processingIndicator.classList.add('flex');
        }
        this.updateVisLabel("Processing");

        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ text: text }));
        } else {
            // Reset processing state immediately since we're not waiting for a response
            this.isProcessing = false;
            if (window.WaveEngine) window.WaveEngine.setProcessing(false);
            if (this.processingIndicator) {
                this.processingIndicator.classList.add('hidden');
                this.processingIndicator.classList.remove('flex');
            }
            this.triggerSystemResponse("My connection to the backend is down. Please wait while I reconnect.");
        }
    }

    processContextUpload(count) {
        // Send a real message to the backend so the crew knows images are ready
        const msg = `I have uploaded ${count || 'some'} evidence image(s) for my claim.`;
        this.addUserMsg(msg);
        this.processUserMessage(msg);
    }

    triggerSystemResponse(text) {
        this.isProcessing = false;
        if (window.WaveEngine) window.WaveEngine.setProcessing(false);
        if (!this.synth) return;

        this.synth.cancel();

        // BUG FIX #10: Guard recognition.stop() — calling stop() when recognition
        // is already stopped or was never started throws InvalidStateError.
        // Previously this crash was silently swallowed but it broke the TTS flow.
        if (this.recognition && this.isListening) {
            try { this.recognition.stop(); } catch (err) { /* already stopped */ }
        }

        this.isSpeaking = true; // Lock state early to prevent onend from resetting label prematurely

        const utterance = new SpeechSynthesisUtterance(text);

        // Voices may not be loaded yet — use onvoiceschanged fallback
        const applyVoice = () => {
            const voices = this.synth.getVoices();
            const englishVoices = voices.filter(v => v.lang.startsWith('en'));
            let preferredVoice = englishVoices.find(v => v.name.includes('Google') || v.name.includes('Samantha') || v.name.includes('Natural'));
            if (!preferredVoice && englishVoices.length > 0) preferredVoice = englishVoices[0];
            if (preferredVoice) utterance.voice = preferredVoice;
        };

        if (this.synth.getVoices().length > 0) {
            applyVoice();
        } else {
            this.synth.onvoiceschanged = () => { applyVoice(); this.synth.onvoiceschanged = null; };
        }

        utterance.lang = 'en-US';
        utterance.rate = 0.95;

        utterance.onstart = () => {
            this.addAiMsg(text);
            if (window.WaveEngine) window.WaveEngine.setSpeaking(true);
            this.updateVisLabel("Speaking");
        };

        utterance.onend = () => {
            this.isSpeaking = false;
            if (window.WaveEngine) window.WaveEngine.setSpeaking(false);
            // Only update label if not already processing a follow-up
            if (!this.isProcessing) {
                if (this.isMutedByUser) {
                    this.updateVisLabel("Muted");
                    if (this.toggleMicBtn) this.toggleMicBtn.classList.add('muted');
                    if (window.WaveEngine) window.WaveEngine.setListening(false);
                } else {
                    this.updateVisLabel("Ready");
                    this.startRecognitionSession();
                }
            }
        };

        // BUG FIX #11: utterance.onerror — speech synthesis can fail silently
        // (e.g. if synth was cancelled before onstart fires). Reset state cleanly.
        utterance.onerror = (err) => {
            console.error("Speech synthesis error:", err);
            this.isSpeaking = false;
            if (window.WaveEngine) window.WaveEngine.setSpeaking(false);
            if (!this.isProcessing) {
                if (this.isMutedByUser) {
                    this.updateVisLabel("Muted");
                    if (this.toggleMicBtn) this.toggleMicBtn.classList.add('muted');
                    if (window.WaveEngine) window.WaveEngine.setListening(false);
                } else {
                    this.updateVisLabel("Ready");
                    this.startRecognitionSession();
                }
            }
        };

        this.synth.speak(utterance);
    }

    updateStatus(text) {
        if (this.statusText) this.statusText.innerText = text;
    }

    updateVisLabel(text) {
        if (this.visLabel) {
            this.visLabel.style.opacity = '0';
            setTimeout(() => {
                this.visLabel.innerText = text;
                this.visLabel.style.opacity = '0.5';
            }, 300);
        }
    }

    // UPDATED TEXT SIZES & THEME SUPPORT
    addAiMsg(text) {
        if (!this.transcriptArea) return;
        const html = `
            <div class="fade-in-text flex flex-col gap-2 max-w-[800px] w-full mx-auto">
                <span class="text-[10px] tracking-wider text-cyan-600 dark:text-cyan-400/80 uppercase flex items-center gap-2 font-medium">
                    <span class="w-1.5 h-1.5 rounded-full bg-cyan-500 dark:bg-cyan-400 shadow-[0_0_6px_rgba(34,211,238,0.5)]"></span>
                    VeriClaim AI
                </span>
                <p class="text-sm text-slate-800 dark:text-gray-200 font-light leading-relaxed tracking-tight">
                    ${text}
                </p>
            </div>
        `;
        this.transcriptArea.insertAdjacentHTML('beforeend', html);
        this.transcriptArea.scrollTop = this.transcriptArea.scrollHeight;
    }

    addUserMsg(text) {
        if (!this.transcriptArea) return;
        const html = `
            <div class="fade-in-text flex flex-col gap-2 max-w-[800px] w-full mx-auto items-end text-right">
                <span class="text-[10px] tracking-wider text-slate-500 dark:text-gray-500 uppercase font-medium">User</span>
                <p class="text-sm text-slate-600 dark:text-gray-400 font-light leading-relaxed tracking-tight">
                    ${text}
                </p>
            </div>
        `;
        this.transcriptArea.insertAdjacentHTML('beforeend', html);
        this.transcriptArea.scrollTop = this.transcriptArea.scrollHeight;
    }

    addSystemMsg(text) {
        if (!this.transcriptArea) return;
        const html = `<div class="text-[10px] text-slate-500 dark:text-gray-600 uppercase tracking-widest text-center my-4">${text}</div>`;
        this.transcriptArea.insertAdjacentHTML('beforeend', html);
        this.transcriptArea.scrollTop = this.transcriptArea.scrollHeight;
    }
}

setTimeout(() => {
    window.VoiceAgent = new VoiceAgent();
}, 500);

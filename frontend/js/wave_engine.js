// Complex WebGL Canvas Math for the Edge Glow (Siri-style)

class WaveEngine {
    constructor() {
        this.canvas = document.getElementById('waveCanvas');
        if (!this.canvas) return;
        
        this.gl = this.canvas.getContext('webgl') || this.canvas.getContext('experimental-webgl');
        this.isListening = false;
        this.isSpeaking = false;
        this.isProcessing = false;
        
        this.initShader();
        this.syncSize();
        
        if (typeof ResizeObserver !== 'undefined') {
            new ResizeObserver(() => this.syncSize()).observe(this.canvas);
        }
        
        this.startTime = performance.now();
        this.render();
    }

    setListening(state) {
        this.isListening = state;
    }

    setSpeaking(state) {
        this.isSpeaking = state;
    }

    setProcessing(state) {
        this.isProcessing = state;
    }

    syncSize() {
        const w = this.canvas.clientWidth || 800;
        const h = this.canvas.clientHeight || 350;
        if (this.canvas.width !== w || this.canvas.height !== h) {
            this.canvas.width = w;
            this.canvas.height = h;
        }
    }

    initShader() {
        const vs = `
            attribute vec2 a_position;
            varying vec2 v_texCoord;
            void main() {
                v_texCoord = a_position * 0.5 + 0.5;
                gl_Position = vec4(a_position, 0.0, 1.0);
            }
        `;
        
        const fs = `
            precision highp float;
            varying vec2 v_texCoord;
            uniform float u_time;
            uniform vec2 u_resolution;
            uniform float u_listening;
            uniform float u_speaking;
            uniform float u_processing;
            uniform float u_is_dark;

            float hash(vec2 p) {
                p = fract(p * vec2(123.34, 456.21));
                p += dot(p, p + 45.32);
                return fract(p.x * p.y);
            }

            void main() {
                vec2 uv = v_texCoord;
                uv.x = (uv.x - 0.5) * 2.0; 
                
                // Elliptical arc at the bottom
                float arc = uv.y - (0.05 + 0.15 * uv.x * uv.x); 
                
                float thickness = 0.015;
                float dist = abs(arc);
                float edge = smoothstep(thickness, 0.0, dist);
                
                // Color stops
                vec3 d_base = vec3(0.05, 0.0, 0.2);
                vec3 d_cyan = vec3(0.0, 0.8, 1.0);
                vec3 d_violet = vec3(0.6, 0.2, 0.9);
                vec3 d_white = vec3(0.9, 0.95, 1.0);
                
                vec3 l_base = vec3(0.85, 0.9, 0.95);
                vec3 l_cyan = vec3(0.0, 0.6, 0.9);
                vec3 l_violet = vec3(0.7, 0.3, 0.9);
                vec3 l_white = vec3(1.0, 1.0, 1.0);
                
                vec3 deepIndigo = mix(l_base, d_base, u_is_dark);
                vec3 electricCyan = mix(l_cyan, d_cyan, u_is_dark);
                vec3 softViolet = mix(l_violet, d_violet, u_is_dark);
                vec3 whiteGlow = mix(l_white, d_white, u_is_dark);
                
                // Idle Colors (Darker, slower)
                vec3 baseColor = deepIndigo;
                float shimmer = hash(vec2(uv.x * 2.0 + u_time * 0.2, uv.y));
                
                // If Listening: Cyan glowing sweep
                if (u_listening > 0.5) {
                    baseColor = mix(deepIndigo, electricCyan, 0.3 + 0.4 * sin(uv.x * 2.0 + u_time * 1.5));
                }
                
                // If Processing: Slow, deep pulsing cyan
                if (u_processing > 0.5) {
                    float pulse = sin(u_time * 3.0) * 0.5 + 0.5;
                    baseColor = mix(deepIndigo, electricCyan, 0.2 + 0.4 * pulse);
                    edge *= 1.2;
                }
                
                // If Speaking: Intense Magenta/Violet vibrations
                if (u_speaking > 0.5) {
                    float wave = sin(uv.x * 5.0 - u_time * 8.0) * 0.5 + 0.5;
                    baseColor = mix(electricCyan, softViolet, wave);
                    baseColor = mix(baseColor, whiteGlow, pow(wave, 4.0) * 0.6);
                    edge *= 1.5; // Thicker line
                }
                
                // Upward Glow bleed
                float glow = exp(-arc * 8.0) * 0.6;
                if (u_speaking > 0.5) glow *= 1.5;
                if (u_processing > 0.5) glow *= (0.8 + 0.4 * sin(u_time * 3.0));
                if (u_listening < 0.5 && u_speaking < 0.5 && u_processing < 0.5) glow *= 0.3; // Dim idle
                
                glow *= (1.0 - smoothstep(0.3, 0.8, uv.y)); 
                
                float alpha = edge + glow;
                vec3 color = baseColor * (edge + glow * 0.8);

                gl_FragColor = vec4(color, alpha);
            }
        `;

        const compile = (type, src) => {
            const s = this.gl.createShader(type);
            this.gl.shaderSource(s, src);
            this.gl.compileShader(s);
            return s;
        };

        this.prog = this.gl.createProgram();
        this.gl.attachShader(this.prog, compile(this.gl.VERTEX_SHADER, vs));
        this.gl.attachShader(this.prog, compile(this.gl.FRAGMENT_SHADER, fs));
        this.gl.linkProgram(this.prog);
        this.gl.useProgram(this.prog);

        const buf = this.gl.createBuffer();
        this.gl.bindBuffer(this.gl.ARRAY_BUFFER, buf);
        this.gl.bufferData(this.gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, 1,1]), this.gl.STATIC_DRAW);

        const pos = this.gl.getAttribLocation(this.prog, 'a_position');
        this.gl.enableVertexAttribArray(pos);
        this.gl.vertexAttribPointer(pos, 2, this.gl.FLOAT, false, 0, 0);

        this.uTime = this.gl.getUniformLocation(this.prog, 'u_time');
        this.uRes = this.gl.getUniformLocation(this.prog, 'u_resolution');
        this.uListening = this.gl.getUniformLocation(this.prog, 'u_listening');
        this.uSpeaking = this.gl.getUniformLocation(this.prog, 'u_speaking');
        this.uProcessing = this.gl.getUniformLocation(this.prog, 'u_processing');
        this.uIsDark = this.gl.getUniformLocation(this.prog, 'u_is_dark');
    }

    render() {
        if (!this.gl) return;
        
        const t = (performance.now() - this.startTime) * 0.001;
        this.gl.viewport(0, 0, this.canvas.width, this.canvas.height);
        
        if (this.uTime) this.gl.uniform1f(this.uTime, t);
        if (this.uRes) this.gl.uniform2f(this.uRes, this.canvas.width, this.canvas.height);
        if (this.uListening) this.gl.uniform1f(this.uListening, this.isListening ? 1.0 : 0.0);
        if (this.uSpeaking) this.gl.uniform1f(this.uSpeaking, this.isSpeaking ? 1.0 : 0.0);
        if (this.uProcessing) this.gl.uniform1f(this.uProcessing, this.isProcessing ? 1.0 : 0.0);
        
        const isDark = document.documentElement.classList.contains('dark') ? 1.0 : 0.0;
        if (this.uIsDark) this.gl.uniform1f(this.uIsDark, isDark);
        
        this.gl.drawArrays(this.gl.TRIANGLE_STRIP, 0, 4);
        requestAnimationFrame(() => this.render());
    }
}

window.WaveEngine = new WaveEngine();

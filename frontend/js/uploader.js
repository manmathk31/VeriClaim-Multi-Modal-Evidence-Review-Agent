// UI Logic for the right pane (Upload toggle, Drag & Drop, Mouse Glow)
// Now with REAL server upload — files are POSTed to /api/v1/upload

document.addEventListener('DOMContentLoaded', () => {
    const fileInput = document.getElementById('fileInput');
    const gallery = document.getElementById('imageGallery');
    const dropZone = document.getElementById('dropZone');
    const toggleUploadBtn = document.getElementById('toggleUploadBtn');
    const closeUploadBtn = document.getElementById('closeUploadBtn');
    
    const transcriptArea = document.getElementById('transcriptArea');
    const uploadArea = document.getElementById('uploadArea');
    const rightPane = document.getElementById('rightPane');
    const mouseGlow = document.getElementById('mouseGlow');

    // Mouse Glow Effect
    if (rightPane && mouseGlow) {
        rightPane.addEventListener('mousemove', (e) => {
            const rect = rightPane.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            
            mouseGlow.style.left = `${x}px`;
            mouseGlow.style.top = `${y}px`;
        });
        
        rightPane.addEventListener('mouseenter', () => mouseGlow.style.opacity = '1');
        rightPane.addEventListener('mouseleave', () => mouseGlow.style.opacity = '0');
    }

    // Toggle Views
    function showUpload() {
        transcriptArea.classList.remove('view-active');
        transcriptArea.classList.add('view-hidden');
        uploadArea.classList.remove('view-hidden');
        uploadArea.classList.add('view-active');
    }

    function hideUpload() {
        uploadArea.classList.remove('view-active');
        uploadArea.classList.add('view-hidden');
        transcriptArea.classList.remove('view-hidden');
        transcriptArea.classList.add('view-active');
    }

    if (toggleUploadBtn) toggleUploadBtn.addEventListener('click', showUpload);
    if (closeUploadBtn) closeUploadBtn.addEventListener('click', hideUpload);
    if (dropZone) dropZone.addEventListener('click', () => fileInput.click());

    // Drag and drop handlers
    if (dropZone) {
        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.classList.add('border-cyan-500', 'bg-white/[0.05]');
        });

        dropZone.addEventListener('dragleave', () => {
            dropZone.classList.remove('border-cyan-500', 'bg-white/[0.05]');
        });

        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('border-cyan-500', 'bg-white/[0.05]');
            handleFiles(e.dataTransfer.files);
        });
    }

    if (fileInput) {
        fileInput.addEventListener('change', (e) => handleFiles(e.target.files));
    }

    /**
     * Upload files to the server AND show thumbnails in the gallery.
     * Previously, files were only read as DataURLs — they never reached the backend.
     * Now each file is POSTed to /api/v1/upload with the user's auth token.
     */
    async function handleFiles(files) {
        if (!files || files.length === 0) return;

        // Get user token for authenticated uploads
        const userStr = localStorage.getItem('vericlaim_user');
        const user = userStr ? JSON.parse(userStr) : null;
        const token = user?.token || '';

        let uploadedCount = 0;
        const uploadPromises = [];

        Array.from(files).forEach(file => {
            if (!file.type.match('image.*')) return;
            
            // Show thumbnail in gallery (existing behavior)
            const reader = new FileReader();
            reader.onload = (e) => {
                const img = document.createElement('img');
                img.src = e.target.result;
                img.className = 'mini-thumb';
                gallery.appendChild(img);
            };
            reader.readAsDataURL(file);

            // POST the actual file to the server (NEW)
            const formData = new FormData();
            formData.append('file', file);

            const uploadPromise = fetch(`/api/v1/upload?token=${encodeURIComponent(token)}`, {
                method: 'POST',
                body: formData
            })
            .then(response => {
                if (response.ok) {
                    uploadedCount++;
                    return response.json();
                }
                throw new Error(`Upload failed: ${response.status}`);
            })
            .then(data => {
                console.log(`[UPLOAD] Server confirmed: ${data.filename}`);
            })
            .catch(err => {
                console.error(`[UPLOAD] Failed to upload ${file.name}:`, err);
            });

            uploadPromises.push(uploadPromise);
        });

        // Wait for all uploads to complete
        await Promise.all(uploadPromises);

        if (uploadedCount > 0 && window.VoiceAgent) {
            window.VoiceAgent.addSystemMsg(`${uploadedCount} evidence file(s) uploaded to server.`);
            hideUpload(); // Auto-return to transcript view
            
            // Notify the voice agent to trigger backend processing
            setTimeout(() => {
                window.VoiceAgent.processContextUpload(uploadedCount);
            }, 500);
        } else if (uploadedCount === 0 && window.VoiceAgent) {
            window.VoiceAgent.addSystemMsg("Upload failed. Please try again.");
        }
    }
});

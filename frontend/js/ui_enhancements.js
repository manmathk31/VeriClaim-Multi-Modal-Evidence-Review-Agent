// ── UI Aesthetics & Micro-interactions ──

document.addEventListener('DOMContentLoaded', () => {
    initScrollReveals();
    init3DTiltCards();
});

// 1. Scroll-triggered Reveals (AOS style)
function initScrollReveals() {
    const revealElements = document.querySelectorAll('.reveal');
    if (!revealElements.length) return;

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('active');
                // Optional: stop observing once revealed
                // observer.unobserve(entry.target);
            }
        });
    }, {
        threshold: 0.1,
        rootMargin: "0px 0px -50px 0px"
    });

    revealElements.forEach(el => observer.observe(el));
}

// 2. Interactive 3D Card Tilt
function init3DTiltCards() {
    const tiltCards = document.querySelectorAll('.tilt-card');
    
    tiltCards.forEach(card => {
        card.addEventListener('mousemove', (e) => {
            const rect = card.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            
            // Calculate rotation (max 10 degrees)
            const centerX = rect.width / 2;
            const centerY = rect.height / 2;
            
            const rotateX = ((y - centerY) / centerY) * -10;
            const rotateY = ((x - centerX) / centerX) * 10;
            
            card.style.transform = `perspective(1000px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) scale3d(1.02, 1.02, 1.02)`;
        });
        
        card.addEventListener('mouseleave', () => {
            card.style.transform = `perspective(1000px) rotateX(0deg) rotateY(0deg) scale3d(1, 1, 1)`;
            card.style.transition = 'transform 0.5s cubic-bezier(0.25, 1, 0.5, 1)';
        });
        
        card.addEventListener('mouseenter', () => {
            card.style.transition = 'none';
        });
    });
}

// 3. Enhanced Toast Override
// This overrides the simple toast in dashboard.html/home.html if needed
window.showEnhancedToast = function(message, type = 'success') {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toast = document.createElement('div');
    const isError = type === 'error';
    const bgClass = isError ? 'bg-red-50 dark:bg-red-900/30' : 'bg-white dark:bg-[#111]';
    const borderClass = isError ? 'border-red-200 dark:border-red-800/50' : 'border-slate-200 dark:border-white/10';
    const iconClass = isError ? 'text-red-500' : 'text-cyan-500 dark:text-cyan-400';
    const iconName = isError ? 'fa-circle-exclamation' : 'fa-circle-check';
    const progressClass = isError ? 'text-red-500' : 'text-cyan-500';

    toast.className = `glass-card ${bgClass} border ${borderClass} rounded-xl p-4 shadow-xl flex flex-col gap-3 pointer-events-auto toast-slide relative overflow-hidden`;
    toast.innerHTML = `
        <div class="flex items-center gap-3 relative z-10">
            <i class="fa-solid ${iconName} ${iconClass} text-lg"></i>
            <span class="text-sm font-medium text-slate-800 dark:text-gray-200">${message}</span>
            <button class="ml-auto text-slate-400 hover:text-slate-600 dark:hover:text-white transition-colors">
                <i class="fa-solid fa-xmark"></i>
            </button>
        </div>
        <div class="toast-progress ${progressClass}" style="animation-duration: 4s;"></div>
    `;

    // Close button
    toast.querySelector('button').onclick = () => {
        toast.classList.remove('toast-slide');
        toast.classList.add('toast-slide-out');
        setTimeout(() => toast.remove(), 400);
    };

    container.appendChild(toast);

    // Auto dismiss
    setTimeout(() => {
        if(toast.parentNode) {
            toast.classList.remove('toast-slide');
            toast.classList.add('toast-slide-out');
            setTimeout(() => toast.remove(), 400);
        }
    }, 4000);
}

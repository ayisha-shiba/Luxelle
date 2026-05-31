/**
 * Luxelle | Premium Luxury JS
 * Handles interactive elements, form animations, and subtle aesthetic touches.
 */

document.addEventListener('DOMContentLoaded', () => {
    // 1. Initial Load Animation
    // The main fade-in is handled by CSS classes, but we can stagger elements inside the form
    const formElements = document.querySelectorAll('.login-card form > div, .login-card form > button, .login-card form > p');
    
    formElements.forEach((el, index) => {
        el.style.opacity = '0';
        el.style.transform = 'translateY(10px)';
        el.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
        
        setTimeout(() => {
            el.style.opacity = '1';
            el.style.transform = 'translateY(0)';
        }, 300 + (index * 100)); // Stagger delay
    });

    // 2. Input Focus Effects
    // Add an extra class to the parent when an input is focused to allow complex styling
    const inputs = document.querySelectorAll('.custom-input');
    
    inputs.forEach(input => {
        input.addEventListener('focus', function() {
            this.parentElement.classList.add('is-focused');
        });

        input.addEventListener('blur', function() {
            if (!this.value) {
                this.parentElement.classList.remove('is-focused');
            }
        });

        // Initialize state if input has value (e.g. browser autofill)
        if (input.value) {
            input.parentElement.classList.add('is-focused');
        }
    });

    // 3. Form Submit Interaction (Button animation simulation)
    const loginForm = document.getElementById('loginForm');
    if (loginForm) {
        const submitBtn = loginForm.querySelector('button[type="submit"]');
        
        loginForm.addEventListener('submit', (e) => {
            // Note: If you want actual submission to work, remove e.preventDefault() 
            // e.preventDefault(); 
            
            // Change button state to show loading
            const originalText = submitBtn.innerText;
            submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Signing in...';
            submitBtn.disabled = true;
            
            // Re-enable after delay if not navigating away
            setTimeout(() => {
                submitBtn.innerHTML = originalText;
                submitBtn.disabled = false;
            }, 1500);
        });
    }

    // 4. Subtle Parallax Effect on Banner (Mouse move)
    const bannerSection = document.querySelector('.banner-section');
    const bannerImage = document.querySelector('.banner-image');

    if (bannerSection && bannerImage) {
        bannerSection.addEventListener('mousemove', (e) => {
            const xAxis = (window.innerWidth / 2 - e.pageX) / 50;
            const yAxis = (window.innerHeight / 2 - e.pageY) / 50;
            
            // Apply a very subtle translation based on mouse position
            // Combines with the CSS floating animation
            bannerImage.style.transform = `scale(1.05) translate(${xAxis}px, ${yAxis}px)`;
        });

        bannerSection.addEventListener('mouseleave', () => {
            // Reset to default CSS animation state
            bannerImage.style.transform = '';
        });
    }
});

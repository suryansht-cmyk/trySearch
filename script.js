document.addEventListener('DOMContentLoaded', () => {
  const toggleButton = document.querySelector('.mobile-menu-toggle');
  const mobileNav = document.querySelector('.mobile-nav');
  const contactForm = document.querySelector('#contact-form');
  const contactFormNote = document.querySelector('#contact-form-note');
  const masterSetupForm = document.querySelector('#master-setup-form');
  const masterSetupNote = document.querySelector('#master-setup-note');
  const masterReady = document.querySelector('#master-ready');

  function closeMobileNav() {
    if (!mobileNav || !toggleButton) return;
    mobileNav.setAttribute('aria-hidden', 'true');
    toggleButton.setAttribute('aria-expanded', 'false');
    toggleButton.classList.remove('open');
    document.body.style.overflow = '';
  }

  function openMobileNav() {
    if (!mobileNav || !toggleButton) return;
    mobileNav.setAttribute('aria-hidden', 'false');
    toggleButton.setAttribute('aria-expanded', 'true');
    toggleButton.classList.add('open');
    // prevent background scroll on small screens when menu open
    document.body.style.overflow = 'hidden';
  }

  if (toggleButton && mobileNav) {
    // ensure initial state
    if (!mobileNav.hasAttribute('aria-hidden')) mobileNav.setAttribute('aria-hidden', 'true');

    toggleButton.addEventListener('click', (e) => {
      e.stopPropagation();
      const isOpen = mobileNav.getAttribute('aria-hidden') === 'false';
      if (isOpen) closeMobileNav(); else openMobileNav();
    });

    // close when clicking any link inside the mobile nav
    mobileNav.addEventListener('click', (e) => {
      const target = e.target;
      if (target && target.tagName === 'A') {
        // allow navigation to proceed but close menu for single-page anchors
        closeMobileNav();
      }
    });

    // close on Escape
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeMobileNav();
    });

    // close when resizing to larger screens
    let resizeTimer = null;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (window.innerWidth > 760) {
          // restore normal state
          closeMobileNav();
        }
      }, 120);
    });

    // click outside to close (only when open)
    document.addEventListener('click', (e) => {
      if (!mobileNav || !toggleButton) return;
      const isOpen = mobileNav.getAttribute('aria-hidden') === 'false';
      if (!isOpen) return;
      const withinNav = mobileNav.contains(e.target) || toggleButton.contains(e.target);
      if (!withinNav) closeMobileNav();
    });
  }

  if (contactForm) {
    contactForm.addEventListener('submit', async (event) => {
      event.preventDefault();

      const formData = new FormData(contactForm);
      const body = {
        name: formData.get('name'),
        email: formData.get('email'),
        message: formData.get('message'),
      };

      try {
        const response = await fetch('/api/contacts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });

        const result = await response.json();
        if (response.ok) {
          contactForm.reset();
          contactFormNote.textContent = 'Thanks! Your request has been saved.';
          contactFormNote.className = 'form-note alert alert-success';
        } else {
          contactFormNote.textContent = result.error || 'Something went wrong, please try again.';
          contactFormNote.className = 'form-note alert alert-error';
        }
      } catch (error) {
        contactFormNote.textContent = 'Unable to connect to the backend. Make sure the server is running.';
        contactFormNote.className = 'form-note alert alert-error';
      }
    });
  }

  if (masterSetupForm && masterSetupNote && masterReady) {
    const masterSetupButton = document.querySelector('#master-setup-button');
    const masterReadyTitle = document.querySelector('#master-ready-title');
    const masterReadyLinks = document.querySelector('#master-ready-links');

    function showMasterWorkspace(data) {
      if (!data || !data.workspace) return;
      masterSetupForm.hidden = true;
      masterReady.hidden = false;
      masterReadyTitle.textContent = `${data.workspace.brand_name} is set up across trySearch.`;
      masterReadyLinks.innerHTML = '';
      (data.tools || []).forEach((tool) => {
        const link = document.createElement('a');
        link.href = tool.href;
        link.textContent = `Open ${tool.name}`;
        masterReadyLinks.appendChild(link);
      });
      masterSetupNote.textContent = '';
    }

    async function loadMasterWorkspace() {
      try {
        const response = await fetch('/api/master-workspace');
        if (!response.ok) return;
        const data = await response.json();
        if (data.workspace) showMasterWorkspace(data);
      } catch (error) {
        // The main page remains usable when the backend is unavailable.
      }
    }

    masterSetupForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const formData = new FormData(masterSetupForm);
      const body = Object.fromEntries(formData);
      masterSetupButton.disabled = true;
      masterSetupButton.textContent = 'Setting up your workspace…';
      masterSetupNote.textContent = '';

      try {
        const response = await fetch('/api/master-workspace', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await response.json().catch(() => ({}));
        if (response.status === 401) {
          masterSetupNote.textContent = 'Please log in first, then return here to activate your workspace.';
          masterSetupNote.className = 'form-note alert alert-error';
          return;
        }
        if (!response.ok) throw new Error(data.error || 'We could not set up your workspace. Please try again.');
        showMasterWorkspace(data);
      } catch (error) {
        masterSetupNote.textContent = error.message || 'We could not set up your workspace. Please try again.';
        masterSetupNote.className = 'form-note alert alert-error';
      } finally {
        masterSetupButton.disabled = false;
        masterSetupButton.textContent = 'Set up my AI workspace';
      }
    });

    loadMasterWorkspace();
  }

  const homePage = document.querySelector('.home-page');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  if (homePage && !reducedMotion.matches) {
    document.documentElement.classList.add('js-motion');

    const revealTargets = document.querySelectorAll([
      '.hero-content', '.hero-visual', '.feature-card', '.master-setup-copy',
      '.master-setup-panel', '.section-highlight > *', '.why-choose .section-heading',
      '.why-grid article', '.contact-section .section-heading', '.contact-panel'
    ].join(','));
    revealTargets.forEach((target, index) => {
      target.classList.add('reveal');
      target.style.setProperty('--reveal-delay', `${Math.min((index % 4) * 75, 225)}ms`);
    });

    if ('IntersectionObserver' in window) {
      const observer = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        });
      }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });
      revealTargets.forEach((target) => observer.observe(target));
    } else {
      revealTargets.forEach((target) => target.classList.add('is-visible'));
    }

    if (window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
      document.querySelectorAll('.visual-card, .feature-card, .master-setup-panel, .insights-panel, .why-grid article, .contact-panel').forEach((card) => {
        card.classList.add('tilt-card');
        card.addEventListener('pointermove', (event) => {
          const bounds = card.getBoundingClientRect();
          const x = (event.clientX - bounds.left) / bounds.width - 0.5;
          const y = (event.clientY - bounds.top) / bounds.height - 0.5;
          card.style.setProperty('--tilt-x', `${(-y * 5).toFixed(2)}deg`);
          card.style.setProperty('--tilt-y', `${(x * 5).toFixed(2)}deg`);
          card.style.setProperty('--tilt-lift', '-4px');
        });
        card.addEventListener('pointerleave', () => {
          card.style.setProperty('--tilt-x', '0deg');
          card.style.setProperty('--tilt-y', '0deg');
          card.style.setProperty('--tilt-lift', '0px');
        });
      });
    }

    const scene = document.querySelector('.ambient-scene');
    const heroVisual = document.querySelector('.hero-visual');
    let pendingFrame = false;
    const updateParallax = () => {
      const y = Math.min(window.scrollY, 1200);
      if (scene) {
        scene.style.setProperty('--ambient-one-y', `${y * -0.045}px`);
        scene.style.setProperty('--ambient-two-y', `${y * 0.035}px`);
        scene.style.setProperty('--ambient-three-y', `${y * -0.025}px`);
      }
      if (heroVisual) heroVisual.style.setProperty('--hero-parallax-y', `${y * 0.035}px`);
      pendingFrame = false;
    };
    const requestParallax = () => {
      if (pendingFrame) return;
      pendingFrame = true;
      window.requestAnimationFrame(updateParallax);
    };
    updateParallax();
    window.addEventListener('scroll', requestParallax, { passive: true });
  }
});

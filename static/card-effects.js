/* MTG card 3D tilt + holographic effects
   Adapted from https://github.com/simeydotme/pokemon-cards-css */

(function () {
    const SPRING = 0.12;   // interpolation speed (0–1, higher = snappier)
    const MAX_ROTATE = 15; // max degrees of tilt

    // Per-card state
    const state = new WeakMap();

    function getState(card) {
        if (!state.has(card)) {
            state.set(card, {
                rotX: 0, rotY: 0,
                bgX: 50, bgY: 50,
                fromLeft: 0.5, fromTop: 0.5,
                fromCenter: 0,
                raf: null,
            });
        }
        return state.get(card);
    }

    function lerp(a, b, t) {
        return a + (b - a) * t;
    }

    function setVars(card, s) {
        card.style.setProperty('--rotate-x',          `${s.rotX.toFixed(2)}deg`);
        card.style.setProperty('--rotate-y',          `${s.rotY.toFixed(2)}deg`);
        card.style.setProperty('--pointer-x',         `${(s.fromLeft * 100).toFixed(1)}%`);
        card.style.setProperty('--pointer-y',         `${(s.fromTop  * 100).toFixed(1)}%`);
        card.style.setProperty('--pointer-from-center', s.fromCenter.toFixed(3));
        card.style.setProperty('--pointer-from-top',  s.fromTop.toFixed(3));
        card.style.setProperty('--pointer-from-left', s.fromLeft.toFixed(3));
        card.style.setProperty('--background-x',      `${(s.bgX).toFixed(1)}%`);
        card.style.setProperty('--background-y',      `${(s.bgY).toFixed(1)}%`);
        card.style.setProperty('--card-scale',        '1.06');
        card.style.setProperty('--card-opacity',      '1');
    }

    function resetVars(card) {
        const s = getState(card);
        s.targetRotX = 0;
        s.targetRotY = 0;
        s.targetBgX  = 50;
        s.targetBgY  = 50;
        s.targetFromLeft   = 0.5;
        s.targetFromTop    = 0.5;
        s.targetFromCenter = 0;

        function animateOut() {
            s.rotX       = lerp(s.rotX,       0,   SPRING);
            s.rotY       = lerp(s.rotY,       0,   SPRING);
            s.bgX        = lerp(s.bgX,        50,  SPRING);
            s.bgY        = lerp(s.bgY,        50,  SPRING);
            s.fromLeft   = lerp(s.fromLeft,   0.5, SPRING);
            s.fromTop    = lerp(s.fromTop,    0.5, SPRING);
            s.fromCenter = lerp(s.fromCenter, 0,   SPRING);

            setVars(card, s);

            const stillMoving =
                Math.abs(s.rotX) > 0.05 ||
                Math.abs(s.rotY) > 0.05 ||
                Math.abs(s.fromCenter) > 0.01;

            if (stillMoving) {
                s.raf = requestAnimationFrame(animateOut);
            } else {
                card.style.setProperty('--card-scale',   '1');
                card.style.setProperty('--card-opacity', '0');
                card.classList.remove('interacting');
            }
        }

        cancelAnimationFrame(s.raf);
        s.raf = requestAnimationFrame(animateOut);
    }

    function onMove(card, e) {
        const rect = card.getBoundingClientRect();
        const clientX = e.clientX ?? e.touches?.[0]?.clientX;
        const clientY = e.clientY ?? e.touches?.[0]?.clientY;
        if (clientX == null) return;

        const x = clientX - rect.left;
        const y = clientY - rect.top;
        const fromLeft   = x / rect.width;
        const fromTop    = y / rect.height;
        const fromCenterX = (fromLeft - 0.5) * 2;  // -1 to 1
        const fromCenterY = (fromTop  - 0.5) * 2;
        const fromCenter  = Math.min(Math.sqrt(fromCenterX ** 2 + fromCenterY ** 2), 1);

        const targetRotY =  fromCenterX * MAX_ROTATE;
        const targetRotX = -fromCenterY * MAX_ROTATE;
        const targetBgX  =  fromLeft * 100;
        const targetBgY  =  fromTop  * 100;

        const s = getState(card);

        function animate() {
            s.rotX       = lerp(s.rotX,       targetRotX,  SPRING);
            s.rotY       = lerp(s.rotY,       targetRotY,  SPRING);
            s.bgX        = lerp(s.bgX,        targetBgX,   SPRING);
            s.bgY        = lerp(s.bgY,        targetBgY,   SPRING);
            s.fromLeft   = lerp(s.fromLeft,   fromLeft,    SPRING);
            s.fromTop    = lerp(s.fromTop,    fromTop,     SPRING);
            s.fromCenter = lerp(s.fromCenter, fromCenter,  SPRING);

            setVars(card, s);
            s.raf = requestAnimationFrame(animate);
        }

        cancelAnimationFrame(s.raf);
        card.classList.add('interacting');
        s.raf = requestAnimationFrame(animate);
    }

    function attach(card) {
        card.addEventListener('mousemove',  e => onMove(card, e));
        card.addEventListener('touchmove',  e => { e.preventDefault(); onMove(card, e); }, { passive: false });
        card.addEventListener('mouseleave', () => { card.classList.remove('interacting'); resetVars(card); });
        card.addEventListener('touchend',   () => resetVars(card));
    }

    // Attach to all current cards and any dynamically added ones
    function attachAll() {
        document.querySelectorAll('.card-thumb').forEach(attach);
    }

    // Watch for new cards added by infinite scroll
    const observer = new MutationObserver(mutations => {
        mutations.forEach(m => {
            m.addedNodes.forEach(node => {
                if (node.nodeType !== 1) return;
                if (node.classList?.contains('card-thumb')) attach(node);
                node.querySelectorAll?.('.card-thumb').forEach(attach);
            });
        });
    });

    document.addEventListener('DOMContentLoaded', () => {
        attachAll();
        const grid = document.getElementById('cardsGrid');
        if (grid) observer.observe(grid, { childList: true, subtree: true });
    });
})();

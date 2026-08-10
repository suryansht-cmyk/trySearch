/*
 * Decorative homepage depth field. This is intentionally isolated from app
 * state and interactions: if WebGL or the CDN is unavailable, the existing
 * CSS atmosphere remains and the site functions exactly as before.
 */
const canvas = document.querySelector('#three-home-canvas');
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

if (canvas && !reducedMotion) {
  import('https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js')
    .then((THREE) => {
      const renderer = new THREE.WebGLRenderer({
        canvas,
        alpha: true,
        antialias: false,
        powerPreference: 'low-power'
      });
      renderer.setClearColor(0x000000, 0);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.35));

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(48, 1, 0.1, 100);
      camera.position.set(0, 0, 12);

      const field = new THREE.Group();
      scene.add(field);

      const pointCount = window.matchMedia('(max-width: 720px)').matches ? 105 : 260;
      const positions = new Float32Array(pointCount * 3);
      const colors = new Float32Array(pointCount * 3);
      const colorWarm = new THREE.Color(0xff7f11);
      const colorLight = new THREE.Color(0xe3d9c8);

      for (let index = 0; index < pointCount; index += 1) {
        const offset = index * 3;
        const spread = Math.random() < 0.55 ? 8.5 : 13;
        positions[offset] = (Math.random() - 0.5) * spread * 1.25;
        positions[offset + 1] = (Math.random() - 0.5) * 21;
        positions[offset + 2] = (Math.random() - 0.55) * 8 - 1;
        const color = Math.random() < 0.36 ? colorWarm : colorLight;
        colors[offset] = color.r;
        colors[offset + 1] = color.g;
        colors[offset + 2] = color.b;
      }

      const particlesGeometry = new THREE.BufferGeometry();
      particlesGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      particlesGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
      const particles = new THREE.Points(particlesGeometry, new THREE.PointsMaterial({
        size: 0.065,
        vertexColors: true,
        transparent: true,
        opacity: 0.72,
        depthWrite: false,
        sizeAttenuation: true
      }));
      field.add(particles);

      const lineVertices = [];
      const lineColors = [];
      const lineCount = window.matchMedia('(max-width: 720px)').matches ? 22 : 66;
      for (let index = 0; index < lineCount; index += 1) {
        const x = (Math.random() - 0.5) * 17;
        const y = (Math.random() - 0.5) * 22;
        const z = -3 - Math.random() * 2.5;
        const length = 0.6 + Math.random() * 1.7;
        lineVertices.push(x, y, z, x + (Math.random() - 0.5) * length, y + (Math.random() - 0.5) * length, z + (Math.random() - 0.5));
        const color = Math.random() < 0.6 ? colorWarm : colorLight;
        lineColors.push(color.r, color.g, color.b, color.r, color.g, color.b);
      }
      const linesGeometry = new THREE.BufferGeometry();
      linesGeometry.setAttribute('position', new THREE.Float32BufferAttribute(lineVertices, 3));
      linesGeometry.setAttribute('color', new THREE.Float32BufferAttribute(lineColors, 3));
      const lines = new THREE.LineSegments(linesGeometry, new THREE.LineBasicMaterial({
        vertexColors: true,
        transparent: true,
        opacity: 0.22,
        depthWrite: false
      }));
      field.add(lines);

      const halo = new THREE.Mesh(
        new THREE.TorusGeometry(3.2, 0.018, 8, 90),
        new THREE.MeshBasicMaterial({ color: 0xff7f11, transparent: true, opacity: 0.18 })
      );
      halo.rotation.set(0.86, -0.46, 0.2);
      halo.position.set(-4.1, 2.6, -3.5);
      field.add(halo);

      const pointer = { x: 0, y: 0 };
      const target = { x: 0, y: 0 };
      let scrollY = window.scrollY;
      let animationFrame;

      const resize = () => {
        const width = window.innerWidth;
        const height = window.innerHeight;
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
        renderer.setSize(width, height, false);
      };

      const onPointerMove = (event) => {
        target.x = (event.clientX / window.innerWidth - 0.5) * 2;
        target.y = (event.clientY / window.innerHeight - 0.5) * 2;
      };

      const onScroll = () => { scrollY = window.scrollY; };

      const render = (time) => {
        pointer.x += (target.x - pointer.x) * 0.025;
        pointer.y += (target.y - pointer.y) * 0.025;
        const elapsed = time * 0.001;
        field.rotation.y = elapsed * 0.026 + pointer.x * 0.16;
        field.rotation.x = pointer.y * 0.075;
        field.rotation.z = scrollY * 0.00007;
        field.position.y = Math.sin(elapsed * 0.34) * 0.28 - (scrollY % 800) * 0.00022;
        particles.rotation.z = elapsed * 0.018;
        halo.rotation.z = elapsed * 0.19;
        renderer.render(scene, camera);
        animationFrame = requestAnimationFrame(render);
      };

      const onVisibilityChange = () => {
        if (document.hidden) {
          cancelAnimationFrame(animationFrame);
        } else {
          animationFrame = requestAnimationFrame(render);
        }
      };

      resize();
      window.addEventListener('resize', resize, { passive: true });
      window.addEventListener('pointermove', onPointerMove, { passive: true });
      window.addEventListener('scroll', onScroll, { passive: true });
      document.addEventListener('visibilitychange', onVisibilityChange);
      animationFrame = requestAnimationFrame(render);
    })
    .catch(() => canvas.remove());
}

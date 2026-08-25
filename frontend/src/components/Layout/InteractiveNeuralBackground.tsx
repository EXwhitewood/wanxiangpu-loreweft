import { useEffect, useRef } from "react";
import type { ResolvedTheme, ThemeTransitionDetail } from "@/hooks/useTheme";

interface InteractiveNeuralBackgroundProps {
  mouseInteractionEnabled?: boolean;
  ambientMotionEnabled?: boolean;
  theme: ResolvedTheme;
}

type Rgb = [number, number, number];

interface ParticlePalette {
  primary: Rgb;
  secondary: Rgb;
  highlight: Rgb;
  core: Rgb;
}

interface PaletteTransition {
  from: ParticlePalette;
  to: ParticlePalette;
  fromThemeMix: number;
  toThemeMix: number;
  startedAt: number;
  duration: number;
  targetTheme: ResolvedTheme;
}

interface FlowPulse {
  originX: number;
  originY: number;
  radius: number;
  startedAt: number;
  duration: number;
}

interface SceneController {
  syncTheme: (theme: ResolvedTheme) => void;
  setAmbientMotion: (enabled: boolean) => void;
}

const LIGHT_FALLBACK: ParticlePalette = {
  primary: [84, 150, 136],
  secondary: [126, 176, 165],
  highlight: [208, 164, 75],
  core: [247, 252, 249],
};

const NIGHT_FALLBACK: ParticlePalette = {
  primary: [113, 195, 170],
  secondary: [166, 168, 216],
  highlight: [210, 176, 106],
  core: [242, 255, 249],
};

function readRgbToken(name: string, fallback: Rgb): Rgb {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const values = raw.split(/\s+/).map(Number);
  return values.length === 3 && values.every(Number.isFinite) ? values as Rgb : fallback;
}

function readPalette(theme: ResolvedTheme): ParticlePalette {
  const fallback = theme === "night" ? NIGHT_FALLBACK : LIGHT_FALLBACK;
  return {
    primary: readRgbToken("--particle-primary-rgb", fallback.primary),
    secondary: readRgbToken("--particle-secondary-rgb", fallback.secondary),
    highlight: readRgbToken("--particle-highlight-rgb", fallback.highlight),
    core: readRgbToken("--particle-core-rgb", fallback.core),
  };
}

function mix(from: number, to: number, progress: number) {
  return from + (to - from) * progress;
}

function mixRgb(from: Rgb, to: Rgb, progress: number): Rgb {
  return [
    mix(from[0], to[0], progress),
    mix(from[1], to[1], progress),
    mix(from[2], to[2], progress),
  ];
}

function mixPalette(from: ParticlePalette, to: ParticlePalette, progress: number): ParticlePalette {
  return {
    primary: mixRgb(from.primary, to.primary, progress),
    secondary: mixRgb(from.secondary, to.secondary, progress),
    highlight: mixRgb(from.highlight, to.highlight, progress),
    core: mixRgb(from.core, to.core, progress),
  };
}

function rgba(rgb: Rgb, alpha: number) {
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
}

function easeInOutCubic(progress: number) {
  return progress < 0.5
    ? 4 * progress * progress * progress
    : 1 - Math.pow(-2 * progress + 2, 3) / 2;
}

function easeOutCubic(progress: number) {
  return 1 - Math.pow(1 - progress, 3);
}

export default function InteractiveNeuralBackground({
  mouseInteractionEnabled = true,
  ambientMotionEnabled = true,
  theme,
}: InteractiveNeuralBackgroundProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const controllerRef = useRef<SceneController | null>(null);
  const mouseInteractionRef = useRef(mouseInteractionEnabled);
  const ambientMotionRef = useRef(ambientMotionEnabled);

  useEffect(() => {
    mouseInteractionRef.current = mouseInteractionEnabled;
  }, [mouseInteractionEnabled]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    const ctx: CanvasRenderingContext2D = context;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const initialTheme = document.documentElement.dataset.theme === "night" ? "night" : "light";
    const connectionDistance = 150;
    const mouseConnectionDistance = 200;
    let currentPalette = readPalette(initialTheme);
    let currentThemeMix = initialTheme === "night" ? 1 : 0;
    let paletteTransition: PaletteTransition | null = null;
    let flowPulse: FlowPulse | null = null;
    let particles: Particle[] = [];
    let animationFrameId = 0;
    let viewportWidth = 0;
    let viewportHeight = 0;
    let previousTime = performance.now();
    let lastRenderTime = 0;
    let pageVisible = !document.hidden;
    let mouse = { x: -1000, y: -1000 };

    class Particle {
      x: number;
      y: number;
      vx: number;
      vy: number;
      size: number;
      baseAngle: number;
      baseSpeed: number;
      colorSlot: 0 | 1 | 2;
      curlDirection: 1 | -1;
      baseAlpha: number;
      twinklePhase: number;
      twinkleSpeed: number;
      isGlint: boolean;

      constructor(width: number, height: number) {
        this.x = Math.random() * width;
        this.y = Math.random() * height;
        this.baseAngle = Math.random() * Math.PI * 2;
        // Daylight keeps the denser, quicker constellation used by the
        // original lobby; the night palette still eases this speed down.
        this.baseSpeed = 0.05 + Math.random() * 0.23;
        this.vx = Math.cos(this.baseAngle) * this.baseSpeed;
        this.vy = Math.sin(this.baseAngle) * this.baseSpeed;
        this.size = Math.random() * 1.05 + 0.48;
        const colorRoll = Math.random();
        this.colorSlot = colorRoll < 0.65 ? 0 : colorRoll < 0.9 ? 1 : 2;
        this.curlDirection = Math.random() > 0.5 ? 1 : -1;
        this.baseAlpha = 0.36 + Math.random() * 0.22;
        this.twinklePhase = Math.random() * Math.PI * 2;
        this.twinkleSpeed = 0.00035 + Math.random() * 0.00045;
        this.isGlint = Math.random() < 0.1;
      }

      update(deltaScale: number, pulseProgress: number | null) {
        const nightAngleOffset = this.curlDirection * 0.52 - 0.12;
        const targetAngle = this.baseAngle + nightAngleOffset * currentThemeMix;
        const targetSpeed = this.baseSpeed * mix(1, 0.72, currentThemeMix);
        const steering = 0.018 * deltaScale;
        this.vx += (Math.cos(targetAngle) * targetSpeed - this.vx) * steering;
        this.vy += (Math.sin(targetAngle) * targetSpeed - this.vy) * steering;

        if (flowPulse && pulseProgress !== null) {
          const dx = this.x - flowPulse.originX;
          const dy = this.y - flowPulse.originY;
          const distance = Math.max(1, Math.hypot(dx, dy));
          const waveFront = easeOutCubic(pulseProgress) * flowPulse.radius;
          const bandWidth = Math.max(84, flowPulse.radius * 0.075);
          const waveDistance = (distance - waveFront) / bandWidth;
          const waveStrength = Math.exp(-(waveDistance * waveDistance)) * (1 - pulseProgress * 0.42);
          const radialX = dx / distance;
          const radialY = dy / distance;
          const tangentX = -radialY * this.curlDirection;
          const tangentY = radialX * this.curlDirection;
          const force = 0.042 * waveStrength * deltaScale;
          this.vx += radialX * force + tangentX * force * 0.58;
          this.vy += radialY * force + tangentY * force * 0.58;
        }

        const speed = Math.hypot(this.vx, this.vy);
        if (speed > 1.15) {
          this.vx = (this.vx / speed) * 1.15;
          this.vy = (this.vy / speed) * 1.15;
        }
        this.x += this.vx * deltaScale;
        this.y += this.vy * deltaScale;
        if (this.x < 0 || this.x > viewportWidth) this.vx *= -1;
        if (this.y < 0 || this.y > viewportHeight) this.vy *= -1;
        this.x = Math.max(0, Math.min(viewportWidth, this.x));
        this.y = Math.max(0, Math.min(viewportHeight, this.y));
      }

      draw(time: number) {
        const slotColor = this.colorSlot === 0
          ? currentPalette.primary
          : this.colorSlot === 1
            ? currentPalette.secondary
            : currentPalette.highlight;
        const color = mixRgb(currentPalette.primary, slotColor, currentThemeMix);
        const wave = Math.max(0, Math.sin(time * this.twinkleSpeed + this.twinklePhase));
        const flash = this.isGlint ? Math.pow(wave, 10) * currentThemeMix : 0;
        const alpha = Math.min(0.96, mix(0.60, this.baseAlpha, currentThemeMix) + flash * 0.48);
        const radius = this.size * mix(1.16, 1, currentThemeMix) * (1 + flash * 0.38);

        if (this.isGlint && flash > 0.08) {
          const haloRadius = 5 + flash * 3;
          const halo = ctx.createRadialGradient(this.x, this.y, 0, this.x, this.y, haloRadius);
          halo.addColorStop(0, rgba(color, 0.22 * flash));
          halo.addColorStop(1, rgba(color, 0));
          ctx.fillStyle = halo;
          ctx.beginPath();
          ctx.arc(this.x, this.y, haloRadius, 0, Math.PI * 2);
          ctx.fill();

          ctx.strokeStyle = rgba(currentPalette.core, 0.32 * flash);
          ctx.lineWidth = 0.55;
          ctx.beginPath();
          ctx.moveTo(this.x - 4 * flash, this.y);
          ctx.lineTo(this.x + 4 * flash, this.y);
          ctx.moveTo(this.x, this.y - 4 * flash);
          ctx.lineTo(this.x, this.y + 4 * flash);
          ctx.stroke();
        }

        ctx.fillStyle = rgba(color, alpha);
        ctx.beginPath();
        ctx.arc(this.x, this.y, radius, 0, Math.PI * 2);
        ctx.fill();

        if (this.isGlint && currentThemeMix > 0.05) {
          ctx.fillStyle = rgba(currentPalette.core, 0.7 + flash * 0.28);
          ctx.beginPath();
          ctx.arc(this.x, this.y, Math.max(0.34, radius * 0.38), 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }

    const updateThemeState = (time: number) => {
      if (!paletteTransition) return;
      const rawProgress = Math.min(1, (time - paletteTransition.startedAt) / paletteTransition.duration);
      const progress = easeInOutCubic(rawProgress);
      currentPalette = mixPalette(paletteTransition.from, paletteTransition.to, progress);
      currentThemeMix = mix(paletteTransition.fromThemeMix, paletteTransition.toThemeMix, progress);
      if (rawProgress >= 1) paletteTransition = null;
    };

    const getPulseProgress = (time: number) => {
      if (!flowPulse) return null;
      const progress = Math.min(1, (time - flowPulse.startedAt) / flowPulse.duration);
      if (progress >= 1) {
        flowPulse = null;
        return null;
      }
      return progress;
    };

    const drawFrame = (time: number) => {
      const deltaScale = Math.min(2, Math.max(0.4, (time - previousTime) / 16.67));
      previousTime = time;
      updateThemeState(time);
      const pulseProgress = getPulseProgress(time);
      ctx.clearRect(0, 0, viewportWidth, viewportHeight);

      for (let i = 0; i < particles.length; i++) {
        const particle = particles[i];
        particle.update(deltaScale, pulseProgress);
        particle.draw(time);

        for (let j = i + 1; j < particles.length; j++) {
          const dx = particle.x - particles[j].x;
          const dy = particle.y - particles[j].y;
          const distanceSquared = dx * dx + dy * dy;
          if (distanceSquared >= connectionDistance * connectionDistance) continue;
          const distance = Math.sqrt(distanceSquared);
          const lineAlpha = mix(0.15, 0.11, currentThemeMix) * (1 - distance / connectionDistance);
          ctx.strokeStyle = rgba(currentPalette.primary, lineAlpha);
          ctx.lineWidth = mix(0.8, 0.65, currentThemeMix);
          ctx.beginPath();
          ctx.moveTo(particle.x, particle.y);
          ctx.lineTo(particles[j].x, particles[j].y);
          ctx.stroke();
        }

        if (!mouseInteractionRef.current) continue;
        const dxMouse = particle.x - mouse.x;
        const dyMouse = particle.y - mouse.y;
        const distanceMouseSquared = dxMouse * dxMouse + dyMouse * dyMouse;
        if (distanceMouseSquared >= mouseConnectionDistance * mouseConnectionDistance) continue;
        const distanceMouse = Math.sqrt(distanceMouseSquared);
        ctx.strokeStyle = rgba(
          currentPalette.primary,
          mix(0.3, 0.24, currentThemeMix) * (1 - distanceMouse / mouseConnectionDistance),
        );
        ctx.lineWidth = mix(1, 0.85, currentThemeMix);
        ctx.beginPath();
        ctx.moveTo(particle.x, particle.y);
        ctx.lineTo(mouse.x, mouse.y);
        ctx.stroke();
        particle.x -= dxMouse * mix(0.01, 0.005, currentThemeMix);
        particle.y -= dyMouse * mix(0.01, 0.005, currentThemeMix);
      }
    };

    const startAnimation = () => {
      if (reducedMotion || !pageVisible || animationFrameId !== 0) return;
      previousTime = performance.now();
      animationFrameId = requestAnimationFrame(animate);
    };

    const animate = (time: number) => {
      animationFrameId = 0;
      const shouldAnimate = ambientMotionRef.current || paletteTransition !== null || flowPulse !== null;
      if (!pageVisible || !shouldAnimate) return;

      const frameInterval = ambientMotionRef.current
        ? (currentThemeMix < 0.5 ? 1000 / 60 : 1000 / 30)
        : 1000 / 24;
      if (time - lastRenderTime >= frameInterval) {
        drawFrame(time);
        lastRenderTime = time;
      }
      animationFrameId = requestAnimationFrame(animate);
    };

    const syncParticleCount = (targetTheme: ResolvedTheme = currentThemeMix < 0.5 ? "light" : "night") => {
      const targetCount = targetTheme === "light"
        ? 80
        : Math.max(40, Math.min(56, Math.round((viewportWidth * viewportHeight) / 30_000)));
      if (particles.length > targetCount) particles.length = targetCount;
      while (particles.length < targetCount) particles.push(new Particle(viewportWidth, viewportHeight));
    };

    const resize = () => {
      const oldWidth = viewportWidth || window.innerWidth;
      const oldHeight = viewportHeight || window.innerHeight;
      viewportWidth = window.innerWidth;
      viewportHeight = window.innerHeight;
      const scaleX = viewportWidth / oldWidth;
      const scaleY = viewportHeight / oldHeight;
      particles.forEach((particle) => {
        particle.x *= scaleX;
        particle.y *= scaleY;
      });
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      canvas.width = Math.round(viewportWidth * dpr);
      canvas.height = Math.round(viewportHeight * dpr);
      canvas.style.width = `${viewportWidth}px`;
      canvas.style.height = `${viewportHeight}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      syncParticleCount();
      if (reducedMotion || !ambientMotionRef.current) drawFrame(performance.now());
    };

    const handleMouseMove = (event: MouseEvent) => {
      mouse = { x: event.clientX, y: event.clientY };
    };
    const handleMouseLeave = () => {
      mouse = { x: -1000, y: -1000 };
    };
    const handleThemeTransition = (event: Event) => {
      const detail = (event as CustomEvent<ThemeTransitionDetail>).detail;
      const now = performance.now();
      paletteTransition = {
        from: currentPalette,
        to: readPalette(detail.theme),
        fromThemeMix: currentThemeMix,
        toThemeMix: detail.theme === "night" ? 1 : 0,
        startedAt: now,
        duration: detail.duration,
        targetTheme: detail.theme,
      };
      syncParticleCount(detail.theme);
      if (!reducedMotion) {
        flowPulse = {
          originX: detail.origin.x,
          originY: detail.origin.y,
          radius: detail.radius,
          startedAt: now,
          duration: detail.duration,
        };
        startAnimation();
      }
    };
    const handleVisibilityChange = () => {
      pageVisible = !document.hidden;
      if (!pageVisible) {
        cancelAnimationFrame(animationFrameId);
        animationFrameId = 0;
        return;
      }
      startAnimation();
    };

    controllerRef.current = {
      syncTheme(nextTheme) {
        if (paletteTransition?.targetTheme === nextTheme) return;
        currentPalette = readPalette(nextTheme);
        currentThemeMix = nextTheme === "night" ? 1 : 0;
        paletteTransition = null;
        syncParticleCount(nextTheme);
        if (reducedMotion || !ambientMotionRef.current) drawFrame(performance.now());
      },
      setAmbientMotion(enabled) {
        ambientMotionRef.current = enabled;
        if (enabled) startAnimation();
      },
    };

    window.addEventListener("resize", resize);
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("loreweft-theme-transition-start", handleThemeTransition);
    document.addEventListener("mouseleave", handleMouseLeave);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    resize();
    if (ambientMotionRef.current) startAnimation();

    return () => {
      controllerRef.current = null;
      window.removeEventListener("resize", resize);
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("loreweft-theme-transition-start", handleThemeTransition);
      document.removeEventListener("mouseleave", handleMouseLeave);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      cancelAnimationFrame(animationFrameId);
    };
  }, []);

  useEffect(() => {
    controllerRef.current?.syncTheme(theme);
  }, [theme]);

  useEffect(() => {
    controllerRef.current?.setAmbientMotion(ambientMotionEnabled);
  }, [ambientMotionEnabled]);

  return (
    <canvas
      ref={canvasRef}
      className="theme-neural-canvas pointer-events-none absolute inset-0 h-full w-full"
      aria-hidden="true"
    />
  );
}

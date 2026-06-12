import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import './brand.css';

const vertexShader = `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = vec4(position, 1.0);
  }
`;

const fragmentShader = `
  uniform float uTime;
  varying vec2 vUv;

  float wave(vec2 p, float t) {
    return sin(p.x * 2.4 + t * 0.35) * sin(p.y * 1.8 - t * 0.28) * 0.5 + 0.5;
  }

  void main() {
    vec2 uv = vUv;
    float t = uTime;

    vec3 black = vec3(0.02, 0.02, 0.02);
    vec3 orange = vec3(0.98, 0.45, 0.09);
    vec3 amber = vec3(0.76, 0.28, 0.05);

    float glowA = exp(-length(uv - vec2(0.12, 0.92)) * 2.8) * 0.42;
    float glowB = exp(-length(uv - vec2(0.88, 0.08)) * 3.4) * 0.08;
    float shimmer = wave(uv, t) * 0.06;

    float intensity = glowA + glowB + shimmer;
    vec3 color = mix(black, mix(amber, orange, glowA / max(intensity, 0.001)), intensity);
    gl_FragColor = vec4(color, 1.0);
  }
`;

function ShaderPlane() {
  const materialRef = useRef<THREE.ShaderMaterial>(null);
  const uniforms = useMemo(
    () => ({ uTime: { value: 0 } }),
    [],
  );

  useFrame(({ clock }) => {
    if (materialRef.current) {
      materialRef.current.uniforms.uTime.value = clock.getElapsedTime();
    }
  });

  return (
    <mesh>
      <planeGeometry args={[2, 2]} />
      <shaderMaterial
        ref={materialRef}
        vertexShader={vertexShader}
        fragmentShader={fragmentShader}
        uniforms={uniforms}
        depthWrite={false}
        depthTest={false}
      />
    </mesh>
  );
}

function WebGLScene() {
  return (
    <Canvas
      orthographic
      camera={{ position: [0, 0, 1], zoom: 1 }}
      gl={{ antialias: false, alpha: false, powerPreference: 'low-power' }}
      dpr={[1, 1.5]}
    >
      <ShaderPlane />
    </Canvas>
  );
}

export function WebGLBackground() {
  const [enabled, setEnabled] = useState(true);

  useEffect(() => {
    try {
      const canvas = document.createElement('canvas');
      const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
      setEnabled(Boolean(gl));
    } catch {
      setEnabled(false);
    }
  }, []);

  return (
    <div className="am-webgl-bg" aria-hidden="true">
      {enabled ? (
        <Suspense fallback={<div className="am-webgl-bg__fallback" />}>
          <WebGLScene />
        </Suspense>
      ) : (
        <div className="am-webgl-bg__fallback" />
      )}
      <div className="am-ambient__vignette" />
    </div>
  );
}
